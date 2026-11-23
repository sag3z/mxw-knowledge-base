"""Build the Obsidian vault from data/kb.sqlite with DeepSeek / Kimi / GLM (via reformboss).

Usage:
  python build_vault.py sources                    # source notes per useful conversation (no LLM)
  python build_vault.py media_notes                # media notes only, with collision suffixes (no LLM)
  python build_vault.py plan <category|all>        # Kimi: split a category into articles
  python build_vault.py write <category|all> [--only "<article title>"] [--workers 6]   # DeepSeek: write articles
  python build_vault.py schemes                    # DeepSeek: scheme articles; big ones (>SCHEME_SPLIT convs) → plan + subarticle series + hub
  python build_vault.py entities [--min 4]         # GLM: notes on projects/tools by tag
  python build_vault.py index                      # home page + category indexes (no LLM)
"""
import argparse
import json
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import llm

ROOT = Path(__file__).parent
DATA = ROOT / "data"
VAULT = ROOT / "vault"
PLANS = DATA / "plans"
_local = threading.local()


class _DB:
    """One SQLite connection per thread: a shared connection breaks under concurrent workers."""
    def __getattr__(self, name):
        if not hasattr(_local, "conn"):
            _local.conn = sqlite3.connect(DATA / "kb.sqlite")
            _local.conn.row_factory = sqlite3.Row
        return getattr(_local.conn, name)


db = _DB()

SHORT = {"nft_движуха": "nft", "ai_вайбкод_билдинг_хакатоны": "ai", "жизнь_флуд_кайф": "life",
         "крипта_работа_проекты": "crypto", "перпы_фандинг_dex_ы": "perps", "предикшены_и_ставки": "predict",
         "софты": "soft", "щитки_мемы_деген_плеи": "shitki", "важное_саммари_и_коллы": "important"}
CATEGORIES = {
    "nft": "NFT: коллекции, минты, вайтлисты, флипы",
    "ai_dev": "AI, вайбкодинг и разработка",
    "tools": "Инструменты, софты и сервисы",
    "security": "Безопасность и скамы",
    "trading": "Трейдинг, перпы, арбитраж, мемкоины",
    "drops_testnets": "Дропы, тестнеты и фарм активностей",
    "market_news": "Новости рынка",
    "predictions": "Предикшены и ставки",
    "jobs_income": "Работа и заработок",
    "community": "Комьюнити MXW",
    "other": "Разное полезное",
}
SCHEMES = {
    "multiaccount": "Мультиаккаунты и сибил", "antisybil": "Антисибил: как проверяют и что обходится",
    "farms": "Фермы (телефоны, аккаунты, игры)", "accounts": "Покупка и регистрация аккаунтов",
    "bots_soft": "Боты и софт (формы, минт, регистрация)", "kyc_verification": "KYC и верификации",
    "proxy_antidetect": "Прокси и антидетект", "bans_risks": "Баны и риски",
}
SCHEME_SPLIT = 90        # convs; a bigger scheme is written as a planned subarticle series + hub
MAX_MATERIAL = 120_000   # chars per writer call; larger articles are written in two stages


def src_name(topic, conv):
    return f"{SHORT[topic]}-{conv}"


def safe(name):
    """Obsidian/filesystem-safe, idempotent name.

    Order matters: the 90-char cap comes BEFORE whitespace collapsing and tail
    cleanup, so a name already cut by the previous version of this function
    (which didn't collapse spaces) maps to exactly the same result."""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", name).strip()[:90]
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"[\s,.;\-–—]+$", "", s)


def useful(where="1=1", args=()):
    return db.execute(f"SELECT * FROM convs WHERE verdict!='drop' AND {where} ORDER BY start_ts", args).fetchall()


def msg_lines(topic, ids, limit_media=1200):
    if not ids:
        return []
    q = f"SELECT * FROM messages WHERE topic=? AND id IN ({','.join('?' * len(ids))}) ORDER BY id"
    out = []
    for m in db.execute(q, (topic, *ids)):
        line = f"#{m['id']} {m['ts'][:16]} {m['author']}"
        if m["fwd_from"]:
            line += f" (переслано от {m['fwd_from']})"
        line += ": " + (m["text"] or "").replace("\n", " / ")
        for md in json.loads(m["media_text"] or "[]"):
            t = re.sub(r"\s+", " ", md["text"])[:limit_media]
            line += f" ⟪{md['kind']}: {t}⟫"
        out.append(line)
    return out


def conv_material(c, full=True):
    head = f"[[{src_name(c['topic'], c['conv'])}]] {c['start_ts'][:10] if c['start_ts'] else ''} | ветка {SHORT[c['topic']]} | теги: {', '.join(json.loads(c['tags']))}"
    parts = [head, "Выжимка: " + (c["summary"] or "")]
    if c["has_scheme"] and c["scheme_summary"]:
        parts.append("Схема: " + c["scheme_summary"])
    if full:
        parts += msg_lines(c["topic"], json.loads(c["key_msgs"]))
    return "\n".join(parts)


# ---------------------------------------------------------------- sources (no LLM)
def write_media_notes():
    """Media notes only (no LLM): one file per notes row into vault/Медиа-конспекты.
    Rows with the same safe(title) get a suffix " (<short>-<msg>)" so they don't
    overwrite each other. Deterministic via ORDER BY (re-runs give same names)."""
    out = VAULT / "Медиа-конспекты"
    out.mkdir(parents=True, exist_ok=True)
    taken = set()
    n = 0
    for r in db.execute("SELECT * FROM notes ORDER BY topic, msg"):
        name = safe(r["title"])
        if name in taken:   # duplicate title → suffix with branch and message id
            name = f"{name} ({SHORT[r['topic']]}-{r['msg']})"
            if name in taken:
                print(f"предупреждение: коллизия даже с суффиксом: {name!r} (перезапись)")
        taken.add(name)
        (out / f"{name}.md").write_text(r["body"], encoding="utf-8")
        n += 1
    print(f"медиа-конспектов: {n}")
    return n


DROP_NOTE_TEXT = ("Разговор не прошёл первичную сортировку, но на него ссылается "
                  "проверенная статья")


def write_source_note(topic, conv):
    """Одна заметка-источник vault/Источники/<short>/<short>-<conv>.md по строке разговора.

    Markdown разговора: мета (ветка/разговор/дата/категория/оценка/теги), заголовок,
    выжимка, схема, сообщения с выделением ключевых жирным. У drop-разговора нет выжимки
    и категории — вместо выжимки строка DROP_NOTE_TEXT (такие заметки создаёт
    factcheck_apply.py, когда проверенная статья ссылается на drop-разговор).
    Возвращает путь записанной заметки или None, если разговора нет в базе."""
    c = db.execute("SELECT * FROM convs WHERE topic=? AND conv=?", (topic, conv)).fetchone()
    if c is None:
        return None
    topic_title = db.execute("SELECT title FROM topics WHERE slug=?", (topic,)).fetchone()[0]
    ids = json.loads(c["msg_ids"])
    keys = set(json.loads(c["key_msgs"]))
    lines = msg_lines(topic, ids, limit_media=4000)
    body = [
        "---", f"ветка: \"{topic_title}\"", f"разговор: {c['conv']}", f"дата: {(c['start_ts'] or '')[:10]}",
        f"категория: {c['category'] or '—'}", f"оценка: {c['verdict']}",
        "теги: [" + ", ".join(json.dumps(t, ensure_ascii=False) for t in json.loads(c["tags"])) + "]", "---",
        f"# {topic_title} · {c['conv']} · {(c['start_ts'] or '')[:16]}", "",
        f"**Выжимка:** {c['summary'] or DROP_NOTE_TEXT}", "",
    ]
    if c["has_scheme"]:
        body += [f"**Схема** ({', '.join(json.loads(c['scheme_types']))}): {c['scheme_summary']}", ""]
    body += ["## Сообщения", ""]
    for l in lines:
        mid = int(re.match(r"#(\d+)", l).group(1))
        body.append(("**" + l + "**" if mid in keys else l) + "\n")
    f = VAULT / "Источники" / SHORT[topic] / f"{src_name(topic, conv)}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(body), encoding="utf-8")
    return f


def cmd_sources():
    n = 0
    for c in useful():
        if write_source_note(c["topic"], c["conv"]):
            n += 1
    write_media_notes()
    print(f"источников: {n}")


# ---------------------------------------------------------------- plan (Kimi)
PLAN_SYS = """Ты — редактор базы знаний приватного крипто-комьюнити MXW (Telegram). Тебе дан список полезных разговоров одной категории: id, дата, теги, короткая выжимка. Разбей их на статьи базы знаний.

Правила:
- Статья = связная тема, по которой участнику полезно прочитать всё в одном месте (проект, механика, инструмент, тип скама, стратегия, период событий). Не дроби мелко и не сваливай всё в одну кучу.
- Количество статей ~ число разговоров / 15 (минимум 3, максимум 40).
- Каждый разговор отнеси ровно к одной статье (поле convs), можно дополнительно упомянуть ещё в одной (also).
- Названия статей — по-русски, конкретно («Robinhood Chain: минты и механики лаунчпадов», а не «Разное про NFT»).
Верни только JSON: {"articles":[{"title":"...","scope":"1–2 предложения, что входит","convs":["nft-c0263-07",...],"also":[...]}]}"""


PLAN_CHUNK = 160   # bigger categories are planned per period, otherwise the JSON answer hits the output limit


def plan_chunk(label, convs, part=""):
    """One planning call: label = what the conversations have in common (a category or a
    scheme description), part = " — часть i/N, период …" for chunked plans."""
    alias = {f"n{i}": src_name(c["topic"], c["conv"]) for i, c in enumerate(convs, 1)}
    lines = [f"n{i} | {(c['start_ts'] or '')[:10]} | {', '.join(json.loads(c['tags']))} | {(c['summary'] or '')[:300]}"
             for i, c in enumerate(convs, 1)]
    sys_prompt = (PLAN_SYS.replace('"convs":["nft-c0263-07",...]', '"convs":["n1","n7",...]')
                  + "\nВ convs и also используй короткие номера строк (n1, n2, …) из списка.")
    plan = llm.chat_json(llm.PLANNER, sys_prompt, f"Категория: {label}{part}\nРазговоров: {len(lines)}\n\n" + "\n".join(lines),
                         max_tokens=32000, temperature=0.2, retries=3, fallback=llm.ENTITY)
    for a in plan["articles"]:
        a["convs"] = [alias[x] for x in a.get("convs", []) if x in alias]
        a["also"] = [alias[x] for x in a.get("also", []) if x in alias]
    return plan["articles"]


def plan_articles(label, convs):
    """LLM plan: cut the conversation list into PLAN_CHUNK pieces (by period) and plan each.
    Returns (articles, n_chunks). Shared by cmd_plan and scheme plans."""
    chunks = [convs[i:i + PLAN_CHUNK] for i in range(0, len(convs), PLAN_CHUNK)]
    articles = []
    for i, ch in enumerate(chunks, 1):
        part = "" if len(chunks) == 1 else f" — часть {i}/{len(chunks)}, период {(ch[0]['start_ts'] or '')[:10]} … {(ch[-1]['start_ts'] or '')[:10]} (укажи период в названиях, если тема привязана ко времени)"
        articles += plan_chunk(label, ch, part)
    return articles, len(chunks)


def finalize_plan(articles, convs, misc_title):
    """Each conversation goes to exactly one article; titles made unique; the
    uncovered ones go to a catch-all. Returns (articles, n_missing)."""
    valid = {src_name(c["topic"], c["conv"]) for c in convs}
    seen = set()
    for a in articles:
        a["convs"] = [x for x in a["convs"] if x in valid and x not in seen]
        seen |= set(a["convs"])
    titles = Counter(a["title"] for a in articles)
    for a in articles:   # same title from different periods → make unique
        if titles[a["title"]] > 1 and a["convs"]:
            d = db.execute("SELECT start_ts FROM convs WHERE topic=? AND conv=?", resolve(a["convs"][0])).fetchone()[0] or ""
            a["title"] = f"{a['title']} ({d[:7]})"
    dup_n = Counter()
    for a in articles:   # still the same title (same month too) → number the repeats
        dup_n[a["title"]] += 1
        if dup_n[a["title"]] > 1:
            a["title"] = f"{a['title']} ({dup_n[a['title']]})"
    articles = [a for a in articles if a["convs"]]
    missing = valid - seen
    if missing:
        articles.append({"title": misc_title, "scope": "Не вошедшее в тематические статьи",
                         "convs": sorted(missing), "also": []})
    return articles, len(missing)


def cmd_plan(cat):
    convs = list(useful("category=?", (cat,)))
    articles, n_chunks = plan_articles(CATEGORIES[cat], convs)
    articles, n_missing = finalize_plan(articles, convs, f"{CATEGORIES[cat]}: прочее")
    plan = {"articles": articles}
    PLANS.mkdir(parents=True, exist_ok=True)
    (PLANS / f"{cat}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{cat}: {len(convs)} разговоров → {len(articles)} статей (частей плана: {n_chunks}, без статьи осталось {n_missing})", flush=True)
    for a in articles:
        print(f"   {len(a['convs']):4}  {a['title']}", flush=True)


# ---------------------------------------------------------------- write (DeepSeek)
WRITE_SYS = """Ты пишешь статью для базы знаний (Obsidian) приватного крипто-комьюнити MXW по материалам их Telegram-чата. Тебе даны разговоры: ссылка-источник вида [[nft-c0263-07]], дата, выжимка, иногда схема, и ключевые сообщения (с расшифровками голосовых/видео и описаниями скринов в ⟪…⟫).

Как писать:
- По-русски, плотно и конкретно: названия, цифры, условия, даты, ссылки на сайты (без рефералок), кто что сказал. Без воды и морали.
- Структура: заголовок «# …», вводный абзац «Кратко» (3–5 предложений), затем тематические разделы «## …» (хронология, механика, инструменты, выводы — что подходит), в конце «## Выводы и правила» (практические пункты, если есть).
- После каждого факта ставь ссылку на источник: [[nft-c0263-07]] (можно несколько). Используй только ссылки из материала.
- Непроверенное помечай: «по словам X», «по пересланному посту». Противоречия между участниками показывай явно.
- Схемы (мультиаккаунты, фермы, KYC, софт, обходы) описывай так же конкретно, как всё остальное — это нужно владельцу базы.
- Не выдумывай ничего сверх материала. Не переписывай адреса кошельков, рефкоды, ключи, пароли, телефоны.
Верни только markdown статьи."""

MERGE_SYS = WRITE_SYS + "\n\nСейчас тебе даны не сырые разговоры, а черновые конспекты частей материала (со ссылками). Собери из них одну цельную статью, объединяя повторы и сохраняя все ссылки."


def merge_drafts(task, drafts):
    """Merge part drafts into the final article. With more than 4 drafts, merge
    level by level in groups of 4 (MERGE_SYS) first, so no single call's input
    grows giant. Every call falls back to llm.ENTITY on empty/provider errors."""
    while len(drafts) > 4:
        groups = [drafts[i:i + 4] for i in range(0, len(drafts), 4)]
        drafts = [llm.chat(llm.WRITER, MERGE_SYS,
                           task + f"(промежуточная сборка {j}/{len(groups)} из {len(g)} черновиков)\n\n"
                           + "\n\n---\n\n".join(g),
                           max_tokens=32000, fallback=llm.ENTITY)
                  for j, g in enumerate(groups, 1)]
    return llm.chat(llm.WRITER, MERGE_SYS, task + "\n\n---\n\n".join(drafts),
                    max_tokens=32000, fallback=llm.ENTITY)


def write_article(cat, a, out_dir):
    convs = [db.execute("SELECT * FROM convs WHERE topic=? AND conv=?", (t, c)).fetchone()
             for t, c in (resolve(x) for x in a["convs"] + a.get("also", []))]
    convs = [c for c in convs if c]
    blocks = [conv_material(c) for c in convs]
    task = f"Статья: {a['title']}\nО чём: {a['scope']}\nКатегория: {CATEGORIES.get(cat, cat)}\n\nМАТЕРИАЛ:\n\n"
    if sum(map(len, blocks)) <= MAX_MATERIAL:
        text = llm.chat(llm.WRITER, WRITE_SYS, task + "\n\n".join(blocks),
                        max_tokens=32000, fallback=llm.ENTITY)
    else:   # two stages: drafts per chunk, then merge
        chunks, cur, size = [], [], 0
        for b in blocks:
            if cur and size + len(b) > MAX_MATERIAL:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(b)
            size += len(b)
        chunks.append(cur)
        drafts = [llm.chat(llm.WRITER, WRITE_SYS, task + f"(часть {i}/{len(chunks)})\n\n" + "\n\n".join(ch),
                           max_tokens=24000, fallback=llm.ENTITY)
                  for i, ch in enumerate(chunks, 1)]
        text = merge_drafts(task, drafts)
    valid = {src_name(c["topic"], c["conv"]) for c in convs}
    bad = sorted({x for x in re.findall(r"\[\[([^\]|#]+)", text) if x not in valid})
    for b in bad:   # strip links the model invented
        text = text.replace(f"[[{b}]]", "")
    branches = sorted({SHORT[c["topic"]] for c in convs})
    dates = sorted(c["start_ts"][:10] for c in convs if c["start_ts"])
    front = ["---", f"категория: {cat}", f"ветки: [{', '.join(branches)}]",
             f"период: {dates[0]} — {dates[-1]}" if dates else "период: —", f"источников: {len(convs)}",
             f"модель: {llm.WRITER}", "---", ""]
    f = out_dir / f"{safe(a['title'])}.md"
    f.write_text("\n".join(front) + text.strip() + "\n", encoding="utf-8")
    return f, len(convs), len(bad)


def resolve(name):
    short, conv = name.split("-", 1)
    return {v: k for k, v in SHORT.items()}[short], conv


def cmd_write(cat, only=None, workers=6):
    cats = list(CATEGORIES) if cat == "all" else [cat]
    jobs = []
    for c in cats:
        if not (PLANS / f"{c}.json").exists():
            print(f"ERR {c}: нет плана, пропускаю", flush=True)
            continue
        plan = json.loads((PLANS / f"{c}.json").read_text(encoding="utf-8"))
        out_dir = VAULT / CATEGORIES[c].split(":")[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs += [(c, a, out_dir) for a in plan["articles"] if (not only or a["title"] == only)
                 and (only or not (out_dir / f"{safe(a['title'])}.md").exists())]   # resume: skip written
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(write_article, *j): j for j in jobs}
        for fu in as_completed(futs):
            c, a, _ = futs[fu]
            try:
                f, n, bad = fu.result()
                print(f"OK  {c}: {a['title']} ({n} источников{', убрано выдуманных ссылок: %d' % bad if bad else ''})", flush=True)
            except Exception as e:
                print(f"ERR {c}: {a['title']}: {e}", flush=True)
    print("usage:", llm.usage)


# ---------------------------------------------------------------- schemes (DeepSeek)
def scheme_plan(key, title, convs):
    """Subarticle plan for a big scheme — same mechanism as a category plan, but the
    prompt hints at the scheme itself so the subarticles cover its mechanics and tools.
    Cached in data/plans/scheme_<key>.json; a re-run does not re-plan."""
    pf = PLANS / f"scheme_{key}.json"
    if pf.exists():
        articles = json.loads(pf.read_text(encoding="utf-8"))["articles"]
        print(f"план {key}: {len(articles)} подстатей (из кэша)", flush=True)
        return articles
    label = (f"схема «{title}» — подстатьи про конкретные механики, софт, сервисы, цены, настройки и "
             f"результаты этой схемы (~15–25 разговоров на подстатью)")
    articles, n_chunks = plan_articles(label, convs)
    articles, _ = finalize_plan(articles, convs, f"{title}: прочее")
    PLANS.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps({"articles": articles}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"план {key}: {len(convs)} разговоров → {len(articles)} подстатей (частей плана: {n_chunks})", flush=True)
    return articles


def write_scheme_hub(out, title, n_convs, articles):
    """Hub for a big scheme, no LLM: frontmatter + a line per subarticle.
    Written only when every subarticle file is already in place."""
    sub_dir = out / safe(title)
    missing = [a for a in articles if not (sub_dir / f"{safe(a['title'])}.md").exists()]
    if missing:
        print(f"хаб пропущен: не хватает {len(missing)} подстатей — {title}", flush=True)
        return
    body = ["---", "категория: schemes", f"источников: {n_convs}", "---", "", f"# {title}", "",
            f"Схема на {n_convs} разговоров: {len(articles)} подстатей по конкретным механикам, софту, сервисам и результатам.", ""]
    body += [f"- [[{safe(a['title'])}]] — {re.sub(r'\s+', ' ', a['scope'] or '').strip()}" for a in articles]
    (out / f"{safe(title)}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"OK  хаб {title} ({len(articles)} подстатей)", flush=True)


def cmd_schemes(workers=4):
    out = VAULT / "Схемы"
    out.mkdir(parents=True, exist_ok=True)
    jobs, hubs = [], []
    for key, title in SCHEMES.items():
        convs = [c for c in useful("has_scheme=1") if key in json.loads(c["scheme_types"])]
        if (out / f"{safe(title)}.md").exists():   # already written (article or hub) — skip
            continue
        if len(convs) > SCHEME_SPLIT:   # big scheme: planned subarticle series + hub
            sub_dir = out / safe(title)
            sub_dir.mkdir(parents=True, exist_ok=True)
            articles = scheme_plan(key, title, convs)
            jobs += [(key, a, sub_dir) for a in articles
                     if not (sub_dir / f"{safe(a['title'])}.md").exists()]   # resume: skip written
            hubs.append((title, len(convs), articles))
        else:
            jobs.append((key, {"title": title, "scope": f"Всё, что в чате говорилось по теме «{title}»: конкретные сервисы, цены, количества, настройки, результаты, баны.",
                               "convs": [src_name(c["topic"], c["conv"]) for c in convs]}, out))
    with ThreadPoolExecutor(workers) as ex:   # all subarticles of all big schemes in one pool
        for fu in as_completed([ex.submit(write_article, *j) for j in jobs]):
            try:
                f, n, bad = fu.result()
                print(f"OK  {f.name} ({n} источников)", flush=True)
            except Exception as e:
                print("ERR", e, flush=True)
    for title, n_convs, articles in hubs:   # hubs after the subarticles, without LLM
        write_scheme_hub(out, title, n_convs, articles)
    print("usage:", llm.usage)


# ---------------------------------------------------------------- entities (GLM)
ENT_SYS = """Ты пишешь короткую справочную заметку для базы знаний крипто-комьюнити MXW о проекте/инструменте/сервисе по упоминаниям в их чате. Дан список выжимок разговоров со ссылками [[...]].
Формат: «# Название», строка «**Что это:** …», затем «## Что говорили в чате» — пункты с фактами, датами и ссылками [[...]] после каждого, затем «## Итог» (1–3 предложения: стоит ли, риски, статус). Только факты из материала, непроверенное — «по словам». Верни только markdown."""


def cmd_entities(min_n=4, workers=6):
    cnt, by = Counter(), defaultdict(list)
    for c in useful():
        for t in {t.strip().lower() for t in json.loads(c["tags"])}:
            if t:
                cnt[t] += 1
                by[t].append(c)
    out = VAULT / "Проекты и инструменты"
    out.mkdir(parents=True, exist_ok=True)
    tags = [t for t, n in cnt.most_common() if n >= min_n and not (out / f"{safe(t)}.md").exists()]
    print(f"сущностей с ≥{min_n} упоминаниями: {len(tags)}")

    def one(t):
        mat = "\n".join(conv_material(c, full=False) for c in by[t][:80])
        text = llm.chat(llm.ENTITY, ENT_SYS, f"Сущность: {t}\nУпоминаний: {cnt[t]}\n\n{mat}",
                        max_tokens=16000, fallback=llm.WRITER)
        valid = {src_name(c["topic"], c["conv"]) for c in by[t]}
        for b in {x for x in re.findall(r"\[\[([^\]|#]+)", text) if x not in valid}:
            text = text.replace(f"[[{b}]]", "")
        (out / f"{safe(t)}.md").write_text(f"---\nупоминаний: {cnt[t]}\nмодель: {llm.ENTITY}\n---\n{text.strip()}\n", encoding="utf-8")
        return t

    with ThreadPoolExecutor(workers) as ex:
        for fu in as_completed([ex.submit(one, t) for t in tags]):
            try:
                fu.result()
            except Exception as e:
                print("ERR", e, flush=True)
    print("usage:", llm.usage)


# ---------------------------------------------------------------- index (no LLM)
def cmd_index():
    lines = ["# База знаний MXW", "", "Собрано из выгрузки Telegram-чата MXW (9 веток, июнь–ноябрь 2026): переписка, "
             "расшифровки голосовых и видео, описания скринов.", ""]
    for d in sorted(p for p in VAULT.iterdir() if p.is_dir() and p.name != "Источники"):
        files = sorted(d.glob("*.md"))
        subs = {p.name: sorted(p.glob("*.md")) for p in d.iterdir() if p.is_dir()}   # Схемы/<схема>/: подстатьи
        lines += [f"## {d.name} ({len(files) + sum(map(len, subs.values()))})", ""]
        for f in files:
            lines.append(f"- [[{f.stem}]]")
            lines += [f"    - [[{sf.stem}]]" for sf in subs.pop(f.stem, [])]   # подстатьи с отступом под своим хабом
        for name, sfs in sorted(subs.items()):   # подпапка, у которой хаб ещё не написан
            lines.append(f"- {name} ({len(sfs)} подстатей, хаба нет)")
            lines += [f"    - [[{sf.stem}]]" for sf in sfs]
        lines.append("")
    (VAULT / "00 Главная.md").write_text("\n".join(lines), encoding="utf-8")
    print("главная обновлена")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--min", type=int, default=4)
    a = ap.parse_args()
    if a.cmd == "sources":
        cmd_sources()
    elif a.cmd == "media_notes":
        write_media_notes()
    elif a.cmd == "plan":
        for c in (CATEGORIES if a.arg == "all" else [a.arg]):
            cmd_plan(c)
        print("usage:", llm.usage)
    elif a.cmd == "write":
        cmd_write(a.arg, a.only, a.workers)
    elif a.cmd == "schemes":
        cmd_schemes(a.workers)
    elif a.cmd == "entities":
        cmd_entities(a.min, a.workers)
    elif a.cmd == "index":
        cmd_index()
