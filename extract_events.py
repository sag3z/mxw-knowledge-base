"""Доска «что активно»: события с датами (минты, клеймы, вайтлисты, тестнеты, снапшоты, TGE,
дедлайны), извлечённые из разговоров → таблица events в data/kb.sqlite + заметка Obsidian
vault/Календарь активностей.md.

Run:  python extract_events.py [--limit N]   (--limit N — обработать только N пачек, для пробы)
LLM:  DeepSeek (llm.WRITER, fallback llm.ENTITY), пачки по ~12 разговоров ≤25k символов,
      ≤3 параллельных запросов (лимит провайдера). Результат каждой пачки кэшируется в
      data/events_raw/<хеш списка conv>.json — повторный запуск не зовёт LLM для готовых пачек.
"""
import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import llm

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "kb.sqlite"
RAW = ROOT / "data" / "events_raw"
VAULT = ROOT / "vault"
NOTE = VAULT / "Календарь активностей.md"

SHORT = {"nft_движуха": "nft", "ai_вайбкод_билдинг_хакатоны": "ai", "жизнь_флуд_кайф": "life",
         "крипта_работа_проекты": "crypto", "перпы_фандинг_dex_ы": "perps", "предикшены_и_ставки": "predict",
         "софты": "soft", "щитки_мемы_деген_плеи": "shitki", "важное_саммари_и_коллы": "important"}
BATCH_CONV, BATCH_CHARS, MSG_CHARS = 12, 25_000, 500   # пачка: ≤12 разговоров, ≤25k символов; текст сообщения/расшифровки ≤500
DATE_MIN, DATE_MAX = date(2026, 1, 1), date(2027, 12, 31)
TYPES = {"mint", "claim", "whitelist", "testnet", "snapshot", "tge", "airdrop", "deadline", "launch", "other"}
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
             "сентябрь", "октябрь", "ноябрь", "декабрь"]

SYS = """Ты извлекаешь из Telegram-чата крипто-комьюнити MXW конкретные события с датами или сроками — для доски «что активно»: минты, клеймы, вайтлисты, тестнеты, снапшоты, TGE, аирдропы, дедлайны, запуски.

Правила:
- Бери только КОНКРЕТНЫЕ события с датой или явным сроком/окном («клейм открыт до 15.06», «минт сегодня в 18:00», «снапшот 20.08», «WL-форма закрывается в пятницу»).
- Относительные даты («завтра», «в пятницу», «через 2 дня») переводи в абсолютные, считая от даты сообщения (дата стоит в строке сообщения после #id).
- Если дата неизвестна — не выдумывай: ставь null. Событие без даты бери только при явном сроке/окне («минт сегодня» без числа — date=null, но событие есть).
- У длительного события date — начало, end_date — окончание; time — время, если указано (UTC).
- Не включай реферальные ссылки/коды, адреса кошельков, приватные ключи — в details их не писать.
- conv — идентификатор разговора из материала (e1, e2, …); msg_ids — номера (#id) ключевых сообщений этого разговора, к которым относится событие.
- Повтор одного события в разных разговорах — отдельные записи (объединим позже).

Верни только JSON без пояснений:
{"events":[{"conv":"e2","type":"mint","project":"Название проекта","title":"коротко что за событие, ≤90 симв","date":"YYYY-MM-DD или null","end_date":"YYYY-MM-DD или null","time":"HH:MM или null","chain":"сеть или null","details":"1–2 предложения: условия, цена, количество, как участвовать","msg_ids":[123,125]}]}
type — один из: mint, claim, whitelist, testnet, snapshot, tge, airdrop, deadline, launch, other.
Если событий в материале нет — верни {"events":[]}."""


def src_name(topic, conv):
    return f"{SHORT[topic]}-{conv}"


# ---------------------------------------------------------------- материал разговора (как conv_material в build_vault, компактнее)
def conv_material(c, alias):
    """Материал для LLM: алиас+дата, выжимка, схема, ключевые сообщения с датой-временем
    (текст ≤500 + расшифровки медиа ⟪kind: text⟫, каждая ≤500). Без ников авторов."""
    parts = [f"— {alias} · {src_name(c['topic'], c['conv'])} · {(c['start_ts'] or '')[:10]}"]
    if c["summary"]:
        parts.append("Выжимка: " + c["summary"])
    if c["has_scheme"] and c["scheme_summary"]:
        parts.append("Схема: " + c["scheme_summary"])
    ids = json.loads(c["key_msgs"])
    if ids:
        q = f"SELECT * FROM messages WHERE topic=? AND id IN ({','.join('?' * len(ids))}) ORDER BY id"
        for m in db.execute(q, (c["topic"], *ids)):
            line = f"#{m['id']} {m['ts'][:16]}: " + (m["text"] or "").replace("\n", " ")[:MSG_CHARS]
            for md in json.loads(m["media_text"] or "[]"):
                t = re.sub(r"\s+", " ", md.get("text") or "")[:MSG_CHARS]
                if t:
                    line += f" ⟪{md['kind']}: {t}⟫"
            parts.append(line)
    return "\n".join(parts)[:BATCH_CHARS]   # одиночный разговор не больше пачки


def make_batches():
    """Пачки по ~12 разговоров ≤25k символов; алиасы e1..eN — короткие id для модели."""
    convs = db.execute("SELECT * FROM convs WHERE verdict!='drop' ORDER BY topic, conv").fetchall()
    groups, cur, cur_chars = [], [], 0
    for c in convs:
        mat = conv_material(c, f"e{len(cur) + 1}")
        if cur and (len(cur) >= BATCH_CONV or cur_chars + len(mat) > BATCH_CHARS):
            groups.append(cur)
            cur, cur_chars = [], 0
            mat = conv_material(c, "e1")
        cur.append({"c": c, "alias": f"e{len(cur) + 1}", "mat": mat})
        cur_chars += len(mat)
    if cur:
        groups.append(cur)
    batches = []
    for items in groups:
        ids = [src_name(x["c"]["topic"], x["c"]["conv"]) for x in items]
        batches.append({"items": items, "ids": ids,
                        "key": hashlib.sha1("|".join(ids).encode()).hexdigest()[:12],
                        "material": "\n\n".join(x["mat"] for x in items)})
    return batches


def run_batch(b):
    """LLM по одной пачке; результат (сырые события с алиасами) → data/events_raw/<хеш>.json."""
    user = (f"Разговоры Telegram-чата (идентификаторы e1…e{len(b['ids'])}; у каждого — дата, выжимка, "
            f"схема и ключевые сообщения «#id YYYY-MM-DD HH:MM: текст», расшифровки медиа в ⟪…⟫). "
            f"Извлеки события.\n\n{b['material']}")
    r = llm.chat_json(llm.WRITER, SYS, user, max_tokens=8000, temperature=0.1, fallback=llm.ENTITY)
    evs = r.get("events") if isinstance(r, dict) else r
    if not isinstance(evs, list):
        raise RuntimeError(f"неожиданный ответ: {type(r)}")
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{b['key']}.json").write_text(json.dumps({"convs": b["ids"], "events": evs},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    return len(evs)


# ---------------------------------------------------------------- валидация и слияние
def valid_date(s):
    if not isinstance(s, str):
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s.strip())
    if not m:
        return None
    try:
        d = date(*map(int, m.groups()))
    except ValueError:
        return None
    return d.isoformat() if DATE_MIN <= d <= DATE_MAX else None


def valid_time(s):
    m = re.match(r"^(\d{1,2}):(\d{2})", str(s or ""))
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mnt:02d}" if h < 24 and mnt < 60 else None


def clean(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n] or None


def norm_project(p):
    return re.sub(r"[\W_]+", "", (p or "").lower())


def merge_events(batches):
    """Валидация (conv из пачки, msg_ids из key_msgs, даты 2026–2027) + дедупликация
    (нормализованный project + type + date) с несколькими источниками; details — самый длинный."""
    merged, n_raw, n_badconv, n_baddate = {}, 0, 0, 0
    for b in batches:
        f = RAW / f"{b['key']}.json"
        if not f.exists():   # LLM не смог обработать пачку — пропускаем, а не падаем
            print(f"предупреждение: нет кэша пачки {b['key']} ({len(b['ids'])} разговоров) — пропускаю", flush=True)
            continue
        cache = json.loads(f.read_text(encoding="utf-8"))
        by_alias = {x["alias"]: x["c"] for x in b["items"]}
        by_name = {src_name(c["topic"], c["conv"]): c for x in b["items"] for c in [x["c"]]}
        for ev in cache["events"]:
            if not isinstance(ev, dict):
                continue
            n_raw += 1
            c = by_alias.get(str(ev.get("conv"))) or by_name.get(str(ev.get("conv")))
            if not c:
                n_badconv += 1
                continue
            keys = set(json.loads(c["key_msgs"]))
            msg_ids = set()
            for x in (ev.get("msg_ids") or []):
                try:
                    x = int(x)
                except (TypeError, ValueError):
                    continue
                if x in keys:   # id не из ключевых сообщений — выкидываем
                    msg_ids.add(x)
            d, ed = valid_date(ev.get("date")), valid_date(ev.get("end_date"))
            n_baddate += sum(1 for x in (ev.get("date"), ev.get("end_date")) if x and not valid_date(x))
            if d and ed and ed < d:
                d, ed = ed, d
            sid = src_name(c["topic"], c["conv"])
            rec = {"type": ev.get("type") if ev.get("type") in TYPES else "other",
                   "project": clean(ev.get("project"), 80) or "—",
                   "title": clean(ev.get("title"), 90) or "",
                   "date": d, "end_date": ed, "time": valid_time(ev.get("time")),
                   "chain": clean(ev.get("chain"), 40),
                   "details": clean(ev.get("details"), 500),
                   "sources": {sid: sorted(msg_ids)},
                   "first_seen": (c["start_ts"] or "")[:10] or None}
            k = (norm_project(rec["project"]), rec["type"], rec["date"])
            if k not in merged:
                merged[k] = rec
                continue
            m = merged[k]
            for f in ("project", "title", "details"):   # длиннее — лучше
                if len(rec[f] or "") > len(m[f] or ""):
                    m[f] = rec[f]
            for f in ("end_date", "time", "chain", "first_seen"):
                if not m[f]:
                    m[f] = rec[f]
            if sid in m["sources"]:
                m["sources"][sid] = sorted(set(m["sources"][sid]) | msg_ids)
            else:
                m["sources"][sid] = sorted(msg_ids)
    evs = sorted(merged.values(), key=lambda e: (e["date"] or "9999-99-99", e["type"], e["project"].lower()))
    return evs, n_raw, n_badconv, n_baddate


def save_events(evs):
    db.execute("DROP TABLE IF EXISTS events")
    db.execute("""CREATE TABLE events(eid INTEGER PRIMARY KEY, type TEXT, project TEXT, title TEXT, date TEXT,
                 end_date TEXT, time TEXT, chain TEXT, details TEXT, sources TEXT, first_seen TEXT)""")
    db.executemany(
        "INSERT INTO events(type,project,title,date,end_date,time,chain,details,sources,first_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(e["type"], e["project"], e["title"], e["date"], e["end_date"], e["time"], e["chain"], e["details"],
          json.dumps([{"id": sid, "msg_ids": mids} for sid, mids in sorted(e["sources"].items())],
                     ensure_ascii=False), e["first_seen"]) for e in evs])
    db.commit()


def write_note(evs):
    """Заметка Obsidian: месяцы (новые сверху), строка на событие, ссылки только на существующие источники."""
    months, undated, n_missing = {}, [], 0
    for e in evs:
        links = []
        for sid in sorted(e["sources"]):
            short = sid.split("-", 1)[0]
            if (VAULT / "Источники" / short / f"{sid}.md").exists():
                links.append(f"[[{sid}]]")
            else:
                links.append(sid)   # файла нет — ссылку не ставим, только текст
                n_missing += 1
        d = e["date"] or e["end_date"]
        line = (f"- **{d}** · {e['type']} · **{e['project']}** — {e['title']} " + " ".join(links)).rstrip() \
            if d else f"- {e['type']} · **{e['project']}** — {e['title']} " + " ".join(links).rstrip()
        (months.setdefault(d[:7], []) if d else undated).append(line)
    body = ["---", f"источников-событий: {len(evs)}", f"модель: {llm.WRITER}", "---", "",
            "# Календарь активностей", "",
            "События с датами из чата (минты, клеймы, вайтлисты, тестнеты, снапшоты, TGE, дедлайны), извлечены "
            "автоматически; строки ссылаются на разговоры-источники. «Без даты» — события с явным сроком, но без точной даты.", ""]
    for ym in sorted(months, reverse=True):   # новые месяцы сверху
        y, m = ym.split("-")
        body += [f"## {y}-{m} · {MONTHS_RU[int(m) - 1]}", ""]
        body += sorted(months[ym])
        body.append("")
    if undated:
        body += ["## Без даты", ""] + undated + [""]
    NOTE.write_text("\n".join(body), encoding="utf-8")
    return n_missing


def main():
    global db
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="обработать только N пачек (для пробы)")
    args = ap.parse_args()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    batches = make_batches()
    if args.limit:
        batches = batches[:args.limit]
    todo = [b for b in batches if not (RAW / f"{b['key']}.json").exists()]
    print(f"пачек: {len(batches)} (из кэша: {len(batches) - len(todo)}, через LLM: {len(todo)})", flush=True)
    if todo:
        with ThreadPoolExecutor(3) as ex:   # у провайдера лимит 3 параллельных запроса — больше нельзя
            futs = {ex.submit(run_batch, b): b for b in todo}
            for i, fu in enumerate(as_completed(futs), 1):
                try:
                    n = fu.result()
                    print(f"  пачка {futs[fu]['key']}: {n} событий ({i}/{len(todo)})", flush=True)
                except Exception as e:
                    print(f"  ERR пачка {futs[fu]['key']}: {str(e)[:200]}", flush=True)

    evs, n_raw, n_badconv, n_baddate = merge_events(batches)
    save_events(evs)
    n_missing = write_note(evs)
    print(f"событий сырых: {n_raw} (отброшено: conv не из пачки {n_badconv}, неверных дат {n_baddate}) "
          f"→ после слияния: {len(evs)}")
    print("по типам:", dict(Counter(e["type"] for e in evs).most_common()))
    print(f"ссылок без файла-источника в заметке: {n_missing}")
    for e in evs[:10]:
        print(f"  {e['date'] or 'без даты'}{' ' + e['time'] if e['time'] else ''} · {e['type']} · "
              f"{e['project']} — {e['title']} ({', '.join(sorted(e['sources']))})")
    print("usage:", llm.usage)


if __name__ == "__main__":
    main()
