"""«Кто в чём разбирается»: экспертиза авторов чата по темам — кластеры (layout+clusters),
категории и типы схем — по ключевым сообщениям полезных разговоров. Профили топ-50 авторов
по n_key — LLM (DeepSeek через llm.WRITER, fallback llm.ENTITY). Таблицы experts и
expert_topics в data/kb.sqlite пересоздаются.

Run:  python experts.py
LLM:  ~50 вызовов, ≤3 параллельных (лимит провайдера). Кэш data/experts_profiles.json —
      по автору + хешу материала, повторный запуск не зовёт LLM для готовых профилей.
"""
import hashlib
import json
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import llm

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "kb.sqlite"
CACHE = ROOT / "data" / "experts_profiles.json"

TOP_AUTHORS = 50    # профили — топ-50 авторов по n_key
MIN_SCORE = 4       # пара (автор, тема) сохраняется при score = n_key + 2*n_convs ≥ 4
N_EXAMPLES = 5      # примеров-разговоров на пару (автор, тема)
N_MATERIAL = 40     # ключевых сообщений в материале профиля
MSG_CHARS = 400     # текст сообщения в материале
THREADS = 3         # параллельных LLM-запросов (лимит провайдера)

SHORT = {"nft_движуха": "nft", "ai_вайбкод_билдинг_хакатоны": "ai", "жизнь_флуд_кайф": "life",
         "крипта_работа_проекты": "crypto", "перпы_фандинг_dex_ы": "perps", "предикшены_и_ставки": "predict",
         "софты": "soft", "щитки_мемы_деген_плеи": "shitki", "важное_саммари_и_коллы": "important"}

SYS = """Ты составляешь карточку экспертизы участника крипто-чата по его ключевым сообщениям.

Правила:
- В профиле — только экспертиза: в чём разбирается и чем полезен сообществу, конкретно (инструменты, сети, механики, проекты, софт).
- НЕ пиши имя или ник автора в тексте профиля — пиши «Разбирается в…».
- Не упоминай никаких личных данных: город, возраст, работа вне крипты, реальные имена, контакты, кошельки — даже если они видны в материале.
- Не упоминай имена и ники других участников.
- Пиши только по материалу, не выдумывай; избегай общих слов без конкретики.

Верни только JSON без пояснений:
{"profile": "2–3 предложения: в чём разбирается и чем полезен, конкретно (инструменты, сети, механики)",
 "skills": ["5–8 коротких навыков/тем"]}"""


def _msg_text(m):
    """Текст ключевого сообщения: сам текст + расшифровки медиа, ≤400 символов."""
    parts = [m["text"] or ""]
    for mt in json.loads(m["media_text"] or "[]"):
        t = re.sub(r"\s+", " ", mt.get("text") or "")
        if t:
            parts.append(t)
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()[:MSG_CHARS]


def collect(db):
    """Авторы-не-боты (n_msgs/n_key/n_convs/даты) + пары (автор, тема) по ключевым
    сообщениям полезных разговоров. Темы: кластер (layout→clusters), категория,
    тип схемы (только у разговоров с has_scheme)."""
    authors = {}
    for r in db.execute("SELECT author, COUNT(*) n, MIN(ts) f, MAX(ts) l FROM messages "
                        "WHERE is_bot=0 AND author IS NOT NULL AND author!='' GROUP BY author"):
        authors[r["author"]] = {"n_msgs": r["n"], "n_key": 0, "n_convs": 0,
                                "first_ts": (r["f"] or "")[:10], "last_ts": (r["l"] or "")[:10]}
    cl_name = {r["k"]: r["name"] for r in db.execute("SELECT k, name FROM clusters")}
    cl_n = {r["name"]: r["n"] for r in db.execute("SELECT name, n FROM clusters")}
    lay = {(r["topic"], r["conv"]): r["k"] for r in db.execute("SELECT topic, conv, k FROM layout")}
    t_title = {r["slug"]: r["title"] for r in db.execute("SELECT slug, title FROM topics")}

    pairs = {}     # (author, kind, topic) → {"n_key": int, "convs": {conv_id: n}}
    keymsgs = {}   # author → [(дата, тема-кластер, текст)] — материал профиля
    for c in db.execute("SELECT * FROM convs WHERE verdict!='drop' ORDER BY topic, conv"):
        ids = json.loads(c["key_msgs"] or "[]")
        if not ids:
            continue
        t, cv = c["topic"], c["conv"]
        topics = []
        k = lay.get((t, cv))
        if k is not None and k in cl_name:
            topics.append(("cluster", cl_name[k]))
        if c["category"]:
            topics.append(("category", c["category"]))
        if c["has_scheme"]:
            for st in json.loads(c["scheme_types"] or "[]"):
                topics.append(("scheme", st))
        label = cl_name.get(k) or t_title.get(t, t)   # тема-кластер сообщения для материала
        cid = f"{SHORT[t]}-{cv}"
        q = f"SELECT id, author, ts, text, media_text FROM messages WHERE topic=? AND id IN ({','.join('?' * len(ids))})"
        per = {}   # author → число его ключевых сообщений в этом разговоре
        for m in db.execute(q, (t, *ids)):
            a = m["author"]
            if a not in authors:   # боты и пустые авторы не считаются
                continue
            per[a] = per.get(a, 0) + 1
            keymsgs.setdefault(a, []).append(((m["ts"] or "")[:10], label, _msg_text(m)))
        for a, n in per.items():
            authors[a]["n_key"] += n
            authors[a]["n_convs"] += 1
            for kind, topic in topics:
                p = pairs.setdefault((a, kind, topic), {"n_key": 0, "convs": {}})
                p["n_key"] += n
                p["convs"][cid] = p["convs"].get(cid, 0) + n
    return authors, pairs, keymsgs, cl_n


def pair_rows(pairs):
    """Пары со score = n_key + 2*n_convs ≥ 4; примеры — до 5 разговоров с наибольшим
    числом его ключевых сообщений."""
    rows = []
    for (a, kind, topic), p in pairs.items():
        score = p["n_key"] + 2 * len(p["convs"])
        if score < MIN_SCORE:
            continue
        ex = sorted(p["convs"].items(), key=lambda x: (-x[1], x[0]))[:N_EXAMPLES]
        rows.append({"kind": kind, "topic": topic, "author": a, "score": score,
                     "n_key": p["n_key"], "n_convs": len(p["convs"]),
                     "examples": [{"id": cid, "n": n} for cid, n in ex]})
    rows.sort(key=lambda r: (r["kind"], r["topic"], -r["score"], r["author"]))
    return rows


def profile_material(a, st, rows_a, keymsgs):
    """Материал профиля: статистика, топ-темы со счётчиками, до 40 ключевых
    сообщений (дата · тема-кластер · текст ≤400)."""
    lines = [f"Статистика участника: {st['n_msgs']} сообщений, из них ключевых {st['n_key']}, "
             f"в {st['n_convs']} разговорах; активен {st['first_ts']} — {st['last_ts']}."]
    tops = sorted(rows_a.get(a, []), key=lambda r: (-r["score"], r["kind"], r["topic"]))[:10]
    if tops:
        lines.append("Топ-темы (ключевые сообщения / разговоры):")
        for r in tops:
            lines.append(f"— {r['topic']}: {r['n_key']} ключевых в {r['n_convs']} разговорах")
    msgs = [x for x in keymsgs.get(a, []) if x[2]]
    msgs = sorted(msgs, key=lambda x: -len(x[2]))[:N_MATERIAL]   # самые содержательные…
    msgs.sort(key=lambda x: (x[0], x[1], x[2]))                  # …в хронологическом порядке
    if msgs:
        lines.append("Ключевые сообщения (дата · тема · текст):")
        for ts, label, text in msgs:
            lines.append(f"{ts} · {label} · {text}")
    return "\n".join(lines)


def _clean_profile(r):
    profile = re.sub(r"\s+", " ", str(r.get("profile") or "")).strip()
    skills = [re.sub(r"\s+", " ", str(s)).strip()[:40]
              for s in (r.get("skills") or []) if str(s).strip()][:8]
    return {"profile": profile, "skills": skills}


def build_profiles(top, materials):
    """Профили топ-50: кэш по автору + хешу материала, прочие — LLM параллельно (3 потока)."""
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    out, todo = {}, []
    for a in top:
        h = hashlib.sha1(materials[a].encode("utf-8")).hexdigest()[:12]
        c = cache.get(a)
        if isinstance(c, dict) and c.get("hash") == h and c.get("profile"):
            out[a] = {"profile": c["profile"], "skills": c.get("skills") or []}
        else:
            todo.append((a, h))
    print(f"профилей: {len(top)} (из кэша: {len(out)}, через LLM: {len(todo)})", flush=True)
    if not todo:
        return out
    lock = threading.Lock()

    def job(a, h):
        user = "Составь карточку экспертизы участника крипто-чата по его ключевым сообщениям и темам.\n\n" + materials[a]
        r = _clean_profile(llm.chat_json(llm.WRITER, SYS, user,
                                         max_tokens=3000, temperature=0.2, fallback=llm.ENTITY))
        r["hash"] = h
        with lock:
            cache[a] = r
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        return a, r

    with ThreadPoolExecutor(THREADS) as ex:
        futs = {ex.submit(job, a, h): a for a, h in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            a = futs[fu]
            try:
                aa, r = fu.result()
                out[aa] = {"profile": r["profile"], "skills": r["skills"]}
                print(f"  профиль {i}/{len(todo)}: {a} ({len(r['skills'])} навыков)", flush=True)
            except Exception as e:
                print(f"  ERR профиль {a}: {str(e)[:200]}", flush=True)
    return out


def save(db, authors, rows, profiles):
    db.execute("DROP TABLE IF EXISTS experts")
    db.execute("DROP TABLE IF EXISTS expert_topics")
    db.execute("""CREATE TABLE experts(author TEXT PRIMARY KEY, n_msgs INT, n_key INT, n_convs INT,
                 first_ts TEXT, last_ts TEXT, profile TEXT, skills TEXT)""")
    db.execute("""CREATE TABLE expert_topics(kind TEXT /*cluster|category|scheme*/, topic TEXT, author TEXT,
                 score INT, n_key INT, n_convs INT, examples TEXT)""")
    db.executemany("INSERT INTO experts(author,n_msgs,n_key,n_convs,first_ts,last_ts,profile,skills) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   [(a, s["n_msgs"], s["n_key"], s["n_convs"], s["first_ts"], s["last_ts"],
                     (profiles.get(a) or {}).get("profile"),
                     json.dumps((profiles.get(a) or {}).get("skills") or [], ensure_ascii=False))
                    for a, s in sorted(authors.items(), key=lambda x: (-x[1]["n_key"], x[0]))])
    db.executemany("INSERT INTO expert_topics(kind,topic,author,score,n_key,n_convs,examples) "
                   "VALUES(?,?,?,?,?,?,?)",
                   [(r["kind"], r["topic"], r["author"], r["score"], r["n_key"], r["n_convs"],
                     json.dumps(r["examples"], ensure_ascii=False)) for r in rows])
    db.commit()


def report(authors, rows, cl_n, profiles):
    top = sorted(authors.items(), key=lambda x: (-x[1]["n_key"], x[0]))[:15]
    print(f"\nавторов: {len(authors)} · пар (автор, тема) со score≥{MIN_SCORE}: {len(rows)} · "
          f"профилей: {len(profiles)}")
    print("\nтоп-15 авторов по n_key:")
    for a, s in top:
        print(f"  {a}: {s['n_key']} ключевых в {s['n_convs']} разговорах · {s['n_msgs']} сообщений · "
              f"{s['first_ts']} — {s['last_ts']}")
    print("\nтоп-3 эксперта в 10 крупнейших кластерах:")
    by_cluster = {}
    for r in rows:
        if r["kind"] == "cluster":
            by_cluster.setdefault(r["topic"], []).append(r)
    for name, n in sorted(cl_n.items(), key=lambda x: -x[1])[:10]:
        best = sorted(by_cluster.get(name, []), key=lambda r: (-r["score"], r["author"]))[:3]
        print(f"  {name} [{n}]: " + (" · ".join(f"{r['author']} ({r['score']})" for r in best) or "—"))
    # ники (длиной ≥4) не должны встречаться в текстах профилей
    nicks = [a for a in authors if len(a) >= 4]
    leaks = []
    for a, pr in profiles.items():
        low = (pr.get("profile") or "").lower()
        for nick in nicks:
            if nick.lower() in low:
                leaks.append((a, nick))
    print(f"\nники в профилях: {'не найдено' if not leaks else leaks}")
    print("usage:", llm.usage)


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    authors, pairs, keymsgs, cl_n = collect(db)
    print(f"авторов-не-ботов: {len(authors)} · полезных ключевых сообщений: "
          f"{sum(s['n_key'] for s in authors.values())}", flush=True)
    rows = pair_rows(pairs)
    print(f"пар (автор, тема) со score≥{MIN_SCORE}: {len(rows)}", flush=True)
    rows_a = {}
    for r in rows:
        rows_a.setdefault(r["author"], []).append(r)
    top = [a for a, _ in sorted(authors.items(), key=lambda x: (-x[1]["n_key"], x[0]))[:TOP_AUTHORS]]
    materials = {a: profile_material(a, authors[a], rows_a, keymsgs) for a in top}
    profiles = build_profiles(top, materials)
    save(db, authors, rows, profiles)
    report(authors, rows, cl_n, profiles)


if __name__ == "__main__":
    main()
