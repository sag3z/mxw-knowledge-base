"""Проверка фактов в статьях vault/ по разговорам-источникам (v2, задача 018).

Статьи писал DeepSeek по разговорам из data/kb.sqlite. Каждый раздел статьи (по заголовкам
«##», frontmatter выкидывается) проверяется конвейером:
  1) извлечение: судья делит раздел на атомарные утверждения (короткий вызов без материала),
     для каждого — src (маркер [[...]] из текста раздела); без src / без ссылок → no_source;
  2) проверка: пачки ≤ CLAIMS_PER_CALL утверждений с тем же материалом, компактный JSON
     по номерам (текст утверждения не повторяется в ответе — экономия out-токенов);
  3) допроверка: если судья в problem пометил расшифровку обрезанной («обрезано»/«обрыв…»),
     утверждение перепроверяется с полным материалом (расшифровки до MEDIA_FULL символов);
  4) второе мнение: unsupported/contradicted перепроверяет вторая модель;
  5) поиск источника: утверждения с итоговым unsupported/contradicted ищутся в соседних
     разговорах (±3 по номеру / ±12 ч) и по FTS (fts_msg); подтвердившееся → wrong_citation
     с correct_source, evidence и дословной quote.
Результаты: data/factcheck/<статья>.json + сводный data/factcheck/report.md. Готовые статьи
не перепроверяются (флаг --force).

Материал раздела: сообщения цитируемых разговоров — сначала ключевые (key_msgs), затем
сообщения/расшифровки со словами раздела (полностью, расшифровки до MEDIA_LIMIT), остальные —
укороченными строками с меткой «…[обрезано]» (лимит ~60k символов). Разделы длиннее
SECTION_CHUNK символов проверяются по кускам; материал куска — разговоры, на которые ссылается
сам кусок.

Скорость: судья — Qwen3.8-27B на HCNSEC-шлюзе (llm.QWEN; флаги --judge/--second),
второе мнение — qwen3.8-max на reformboss (другая модель-семейство), fallback судьи
и второго мнения — llm.QWEN_ALT (Qwen3.8-Flash-Next на HCNSEC). Официальный DeepSeek
в фактчекинге не используется — только чатом сайта (app/server.py).
Разделы всех статей идут в общий пул (WORKERS потоков); параллельность к провайдерам
ограничивают семафоры в llm.py: reformboss ≤ llm.LIMIT_REFORMBOSS, HCNSEC ≤
llm.LIMIT_HCNSEC. Перед стартом — проверка доступности HCNSEC-шлюза (llm.gateway_ok):
не отвечает → понятная ошибка «запусти OpenCode API Manager», а не зависание.
В json и отчёте — время и число вызовов.

Устойчивость к провайдеру: любая ошибка LLM-вызова (таймаут, 429/5xx, обрыв, пустой ответ,
невалидный JSON после ремонта) — повтор с паузой (до LLM_TRIES попыток, пауза вне семафора);
не помогло — раздел помечается error, проверка идёт дальше (статья не умирает целиком).
Каждая статья сохраняется сразу по готовности → resume: без --force готовые статьи пропускаются.

Запуск:
  python factcheck.py --pilot                 # пилот: 10 статей (random.seed(7))
  python factcheck.py <путь к .md> [...]      # конкретные статьи
  python factcheck.py --pilot --force         # перепроверить весь пилот
  python factcheck.py --pilot --judge glm-5.3 # другой судья
  python factcheck.py --all                   # ВСЕ статьи хранилища (готовые не трогать)
  python factcheck.py --all --force           # перепроверить всё хранилище
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime as _dt
from pathlib import Path

import llm

# при перенаправлении вывода (фоновый запуск) stdout на Windows = cp1251, и print текста
# от LLM с символами вне cp1251 («→» и т.п.) роняет процесс UnicodeEncodeError — форсим utf-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "vault"
OUT = ROOT / "data" / "factcheck"

JUDGE = llm.QWEN                 # судья по умолчанию: Qwen3.8-27B на HCNSEC-шлюзе (флаг --judge)
SECOND = "qwen3.8-max"           # второе мнение для unsupported/contradicted: другая модель-семейство,
                                 # reformboss (флаг --second)
FALLBACK = llm.QWEN_ALT          # fallback судьи: Qwen3.8-Flash-Next на HCNSEC
SECOND_FALLBACK = llm.QWEN_ALT   # fallback второго мнения: Qwen3.8-Flash-Next на HCNSEC

MATERIAL_LIMIT = 60_000         # символов материала на раздел
FULL_MATERIAL_LIMIT = 120_000   # материал при допроверке обрезанных расшифровок
SEARCH_MATERIAL_LIMIT = 30_000  # материал для поиска настоящего источника
SEARCH_CONV_LIMIT = 12_000      # на разговор-кандидата в поисковом материале
MEDIA_LIMIT = 4_000             # символов расшифровки медиа в обычном материале
MEDIA_FULL = 20_000             # символов расшифровки в полном материале (допроверка)
LINE_TEXT = 200                 # обрезка текста «остальных» сообщений
LINE_MEDIA = 120                # обрезка расшифровок «остальных» сообщений
SECTION_CHUNK = 1_500           # длинные разделы режем на куски такого размера
CLAIMS_PER_CALL = 12            # утверждений на один вызов судьи: Qwen3.8-27B с рассуждениями
                                # на пачках по 20 часто режет ответ (finish_reason=length) —
                                # пачки по 12 (компактный JSON по номерам) влезают в max_tokens
WORKERS = 12                    # потоков в общем пуле задач — как LIMIT_HCNSEC в llm.py
LLM_TRIES = 4                   # попыток на LLM-вызов (повтор с паузой), затем раздел error
RETRY_PAUSE = 20                # базовая пауза между попытками, сек (растёт: 20, 40, 60)
NEIGH_MAX = 8                   # соседних разговоров на один цитируемый
FTS_TOP = 5                     # разговоров из FTS на одно утверждение
SEARCH_MAX_CONVS = 10           # всего кандидатов на одно утверждение
VERDICTS = ("supported", "partial", "unsupported", "contradicted", "no_source", "wrong_citation")

SHORT = {"nft_движуха": "nft", "ai_вайбкод_билдинг_хакатоны": "ai", "жизнь_флуд_кайф": "life",
         "крипта_работа_проекты": "crypto", "перпы_фандинг_dex_ы": "perps", "предикшены_и_ставки": "predict",
         "софты": "soft", "щитки_мемы_деген_плеи": "shitki", "важное_саммари_и_коллы": "important"}
INV = {v: k for k, v in SHORT.items()}

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
CONV_RE = re.compile(r"^(" + "|".join(INV) + r")-(c\d+(?:-\d+)?)$")
# судья пожаловался, что расшифровка обрывается/обрезана → утверждение пойдёт на допроверку
TRUNC_RE = re.compile(r"обрезан|обрыв|усеч[её]н|truncat|cut\s*off|не\s*полностью|неполн", re.I)

# кандидаты для поиска источника: служебные слова выкидываем из FTS-запроса
STOPWORDS = set("""и в во не на что он она оно они мы вы я с со как а то же но бы ли или если когда
чтобы потому затем потом тогда также даже очень там тут здесь значит типа просто вообще короче
такой такое такие такая весь вся все всего после перед без более меньше самый самая самое
которые который которое которая был была было были быть есть нет да ну уже еще ещё там тут
the a an and or but if then else for with without from into about over under again once just
only very too also this that these those be been is are was were will would can could should
must have has had do does did not no you your they them their there here what which who when
where why how all any both each few more most other some such own same so than
""".split())

db = sqlite3.connect(ROOT / "data" / "kb.sqlite", check_same_thread=False)   # чтения из потоков пула
db.row_factory = sqlite3.Row

RUN_T0 = time.time()           # старт прогона — от него время статьи в json/отчёте

# кэши поиска источника (потоки пула; запись в dict атомарна под GIL)
_NEIGH_CACHE, _FTS_CACHE, _MAT_CACHE, _SEARCH_CACHE = {}, {}, {}, {}


def call_llm(model, system, user, **kw):
    """Единственное место обращения к провайдеру через llm.chat_json: параллельность
    ограничивают семафоры в llm.py — на каждого провайдера свой (reformboss ≤
    llm.LIMIT_REFORMBOSS, HCNSEC ≤ llm.LIMIT_HCNSEC), поэтому пул потоков
    не устраивает провайдеру 429. Любая ошибка (таймаут, 429/5xx, обрыв, пустой ответ,
    невалидный JSON) — повтор с паузой до LLM_TRIES раз. Все попытки не удались →
    исключение: вызвавшая фаза пометит раздел error и пойдёт дальше."""
    for attempt in range(1, LLM_TRIES + 1):
        try:
            return llm.chat_json(model, system, user, **kw)
        except Exception as e:
            if attempt >= LLM_TRIES:
                raise
            pause = RETRY_PAUSE * attempt
            print(f"[llm] {model}, попытка {attempt}/{LLM_TRIES}: {str(e)[:90]!r}; "
                  f"повтор через {pause} с", flush=True)
            time.sleep(pause)


# ---------------------------------------------------------------- промпты

EXTRACT_SYS = """Ты фактчекер базы знаний крипто-комьюнити MXW. Дан РАЗДЕЛ статьи (со ссылками-маркерами вида [[life-c0254]]). Разбей раздел на атомарные фактические утверждения: цифры, даты, названия, кто что сказал/сделал, условия, результаты; оценочные обобщения — тоже как утверждения. Перечисли все утверждения, ничего не пропускай и не добавляй своих.

Для каждого утверждения укажи src — маркер источника из текста раздела (например life-c0254), стоящий в предложении/пункте этого утверждения; если рядом маркера нет — "".

Отвечай сразу компактным JSON без markdown и пояснений: {"claims":[{"text":"утверждение кратко, без маркеров [[ ]]","src":"life-c0254"}]}"""


CHECK_SYS = """Ты фактчекер базы знаний крипто-комьюнити MXW. Даны УТВЕРЖДЕНИЯ статьи (по номерам) и МАТЕРИАЛ — сообщения Telegram-разговоров, на которые статья ссылается (#id — номер сообщения; ⟪…⟫ — расшифровки медиа; помеченное «…[обрезано]» укорочено; === — границы разговоров). Проверяй верность источникам (не истинность в мире).

Для каждого утверждения выбери вердикт:
- supported — прямо подтверждается материалом; обязательны evidence (id сообщений числами, например [57836]) и дословная quote из материала (≤200 символов);
- partial — суть верна, но есть неточность (цифра, дата, автор, преувеличение или обобщение из единичного случая); опиши её в problem;
- unsupported — в материале этого нет;
- contradicted — материал говорит об обратном; quote обязательна.

Поля: num (номер утверждения); verdict; evidence (id числами); quote; problem (для partial/unsupported/contradicted, иначе ""); hedge_ok — false, если в источнике это только чьё-то мнение/слух/прогноз, а в статье подано как факт без «по словам», иначе true; fix — для partial/unsupported/contradicted: как переписать предложение точно по материалу (или «удалить»), иначе "".

quote — точная копия фрагмента материала, не выдумывай. Если для проверки не хватило урезанной расшифровки — напиши в problem слово «обрезано».

Не рассуждай долго, не повторяй материал и текст утверждений (в ответе только num и поля вердикта): отвечай сразу компактным JSON без markdown: {"claims":[{"num":1,"verdict":"","evidence":[],"quote":"","problem":"","hedge_ok":true,"fix":""}]} — по пункту на каждое утверждение, без пропусков."""


SECOND_SYS = """Ты — второй фактчекер: перепроверяешь утверждения, которые первый пометил как unsupported или contradicted. Даны нумерованный список УТВЕРЖДЕНИЙ и МАТЕРИАЛ — сообщения Telegram-разговоров (#id — номер сообщения; ⟪…⟫ — расшифровки медиа).

Для каждого утверждения реши: подтверждается ли оно МАТЕРИАЛОМ? Ищи внимательно — подтверждение может быть сформулировано иначе или стоять в другом сообщении.
Вердикты: supported — прямо подтверждается; partial — суть верна, но неточность; unsupported — в материале нет; contradicted — материал говорит об обратном.

Отвечай сразу компактным JSON без пояснений: {"claims":[{"num":1,"verdict":"supported|partial|unsupported|contradicted","evidence":[],"quote":"<дословная цитата из материала, до 200 символов>"}]} — по пункту на каждое утверждение списка, без пропусков."""


SEARCH_SYS = """Ты фактчекер базы знаний крипто-комьюнити MXW. Утверждение из статьи первый фактчекер не нашёл в разговоре, на который статья ссылается. Даны УТВЕРЖДЕНИЕ и МАТЕРИАЛ — сообщения соседних разговоров тех же дней/тем (#id — номер сообщения; ⟪…⟫ — расшифровки медиа; === — границы разговоров, имена вида life-c0255).

Реши: подтверждается ли утверждение этим материалом (хотя бы одним разговором)? Если да — conv: имя разговора из строки «=== [[...]]» без скобок (например life-c0255), evidence: id сообщений числами, quote: дословная цитата ≤200 символов.

Отвечай сразу компактным JSON без markdown: {"found":true,"conv":"life-c0255","evidence":[16057],"quote":"..."} или {"found":false}."""

# ---------------------------------------------------------------- разбор статьи

def _norm(s):
    """Нормализация для проверки дословности цитат: пробелы + регистр."""
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def strip_frontmatter(text):
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.S)


def frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.S)
    out = {}
    if m:
        for ln in m.group(1).splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def section_convs(text):
    """Ссылки [[short-conv]] раздела, которые резолвятся в разговоры: [(topic, conv, имя)]."""
    out, seen = [], set()
    for name in LINK_RE.findall(text):
        m = CONV_RE.match(name.strip())
        if m:
            topic, conv = INV[m.group(1)], m.group(2)
            key = (topic, conv)
            if key not in seen and db.execute(
                    "SELECT 1 FROM convs WHERE topic=? AND conv=?", key).fetchone():
                seen.add(key)
                out.append((topic, conv, name.strip()))
    return out


def _media_part(md, media_limit):
    raw = re.sub(r"\s+", " ", str(md.get("text") or ""))
    t = raw[:media_limit]
    if len(raw) > len(t):
        t += "…[обрезано]"
    return f" ⟪{md.get('kind')}: {t}⟫"


def _compose_line(m, medias, media_limit):
    """Полная строка сообщения: текст + расшифровки до media_limit символов каждая."""
    line = f"#{m['id']} {m['ts'][:16]} {m['author']}"
    if m["fwd_from"]:
        line += f" (переслано от {m['fwd_from']})"
    line += ": " + (m["text"] or "").replace("\n", " / ")
    for md in medias:
        line += _media_part(md, media_limit)
    return line


def _short_line(m, medias):
    """Укороченная строка «остальных» сообщений: текст до LINE_TEXT, расшифровки до LINE_MEDIA."""
    line = f"#{m['id']} {m['ts'][:16]} {m['author']}"
    if m["fwd_from"]:
        line += f" (переслано от {m['fwd_from']})"
    txt = (m["text"] or "").replace("\n", " / ")
    if len(txt) > LINE_TEXT:
        txt = txt[:LINE_TEXT] + "…[обрезано]"
    line += ": " + txt
    for md in medias[:2]:
        raw = re.sub(r"\s+", " ", str(md.get("text") or ""))
        t = raw[:LINE_MEDIA]
        if len(raw) > len(t):
            t += "…[обрезано]"
        line += f" ⟪{md.get('kind')}: {t}⟫"
    if len(medias) > 2:
        line += f" ⟪+{len(medias) - 2} медиа обрезано⟫"
    return line


def build_material(convs, section_text, limit=MATERIAL_LIMIT, media_limit=MEDIA_LIMIT, shorten=True,
                   by_conv=False, conv_limit=None):
    """Материал: сообщения разговоров строками #<id> <дата-время> <автор>: <текст> ⟪<kind>: расшифровка⟫
    (is_bot и пустые пропускаются). Приоритет сообщения: 0 — ключевое (key_msgs), 1 — сообщение/
    расшифровка содержит слова раздела, 2 — остальные. Порядок: по умолчанию сначала все ключевые,
    затем все со словами, затем остальные; by_conv=True (поиск источника) — разговоры-кандидаты по
    порядку близости, внутри разговора ключевые/совпадающие/остальные, conv_limit — на разговор.
    Если всё целиком влезает в limit — включаем полностью; иначе prio 0/1 полностью (расшифровки
    до media_limit), остальные — укороченными строками.
    Возвращает (текст, валидные id, {id: имя разговора}, {id: обрезанных сообщений})."""
    words = {w for w in re.findall(r"[а-яёa-z0-9]{4,}", (section_text or "").lower())}
    blocks = []   # (имя, дата, [(приоритет, id, строка, «полная» строка, короткая строка)])
    for topic, conv, name in convs:
        row = db.execute("SELECT msg_ids, key_msgs, start_ts FROM convs WHERE topic=? AND conv=?",
                         (topic, conv)).fetchone()
        entries = []
        if row is not None:
            ids = json.loads(row["msg_ids"] or "[]")
            keys = set(json.loads(row["key_msgs"] or "[]"))
            if ids:
                q = f"SELECT id, ts, author, fwd_from, text, media_text, is_bot FROM messages " \
                    f"WHERE topic=? AND id IN ({','.join('?' * len(ids))}) ORDER BY id"
                for m in db.execute(q, (topic, *ids)).fetchall():
                    if m["is_bot"]:
                        continue
                    medias = [md for md in json.loads(m["media_text"] or "[]")
                              if str(md.get("text") or "").strip()]
                    if not (m["text"] or "").strip() and not medias:
                        continue
                    line = _compose_line(m, medias, media_limit)
                    line_max = _compose_line(m, medias, MEDIA_FULL)   # эталон для метки обрезки
                    short = _short_line(m, medias) if shorten else line
                    lw = line_max.lower()
                    prio = 0 if m["id"] in keys else (1 if any(w in lw for w in words) else 2)
                    entries.append((prio, m["id"], line, line_max, short))
        blocks.append((name, (row["start_ts"] or "")[:10] if row is not None else "", entries))
    full_size = sum(len(l) + 1 for _, _, entries in blocks for _, _, l, _, _ in entries)
    all_full = full_size <= limit
    order = []   # (имя, дата, приоритет, id, строка, полная, короткая)
    if by_conv:
        for name, date, entries in blocks:
            for prio in (0, 1, 2):
                for p, mid, line, line_max, short in entries:
                    if p == prio:
                        order.append((name, date, p, mid, line, line_max, short))
    else:
        for prio in (0, 1, 2):
            for name, date, entries in blocks:
                for p, mid, line, line_max, short in entries:
                    if p == prio:
                        order.append((name, date, p, mid, line, line_max, short))
    lines, valid, msg_conv, truncated = [], set(), {}, set()
    total, have, conv_total = 0, set(), Counter()
    for name, date, p, mid, line, line_max, short in order:
        if total >= limit:
            break
        if conv_limit is not None and conv_total[name] >= conv_limit:
            continue
        use = line if (all_full or p < 2 or not shorten) else short
        if name not in have:
            h = f"=== [[{name}]] {date} ==="
            if lines and total + len(h) + len(use) + 2 > limit:
                continue
            have.add(name)
            lines.append(h)
            total += len(h) + 1
        if total + len(use) + 1 > limit:
            if p == 2 and shorten and use is not short and total + len(short) + 1 <= limit:
                use = short
            else:
                continue
        lines.append(use)
        total += len(use) + 1
        conv_total[name] += len(use) + 1
        valid.add(mid)
        msg_conv[mid] = name
        if use != line_max:
            truncated.add(mid)
    return "\n".join(lines), valid, msg_conv, truncated


def split_chunks(text, limit=SECTION_CHUNK):
    """Длинный текст раздела → куски ≤ ~limit символов: по абзацам, длинный абзац — по строкам."""
    pieces = []
    for para in text.split("\n\n"):
        if len(para) <= limit:
            pieces.append(para)
            continue
        cur, size = [], 0
        for ln in para.split("\n"):
            if cur and size + len(ln) + 1 > limit:
                pieces.append("\n".join(cur))
                cur, size = [], 0
            cur.append(ln)
            size += len(ln) + 1
        if cur:
            pieces.append("\n".join(cur))
    chunks, cur, size = [], [], 0
    for piece in pieces:
        if cur and size + len(piece) + 2 > limit:
            chunks.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(piece)
        size += len(piece) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def prepare_article(path):
    """Разбор статьи: frontmatter прочь, разделы по «##»; для каждого куска — ссылки и материал."""
    text = path.read_text(encoding="utf-8")
    body = strip_frontmatter(text)
    m = re.match(r"(?m)^#\s+(.+)$", body)
    title = m.group(1).strip() if m else path.stem
    secs = []
    parts = re.split(r"(?m)^##\s+", body)
    if parts[0].strip():
        secs.append({"heading": title, "text": parts[0].strip()})
    for part in parts[1:]:
        head, _, rest = part.partition("\n")
        secs.append({"heading": head.strip(), "text": f"## {head.strip()}\n{rest.strip()}"})
    for sec in secs:
        convs = section_convs(sec["text"])
        sec["convs"] = [c[2] for c in convs]
        mat, valid, mc, trunc = build_material(convs, sec["text"])
        sec["chunks"] = []
        if len(sec["text"]) <= SECTION_CHUNK:
            sec["chunks"].append({"text": sec["text"], "convs": sec["convs"],
                                  "convs_resolved": convs, "material": mat, "valid_ids": valid,
                                  "msg_conv": mc, "truncated_ids": trunc})
        else:   # мега-раздел: материал куска — разговоры, на которые ссылается сам кусок
            for chunk in split_chunks(sec["text"]):
                cc = section_convs(chunk)
                mat, valid, mc, trunc = build_material(cc, chunk)
                sec["chunks"].append({"text": chunk, "convs": [c[2] for c in cc],
                                      "convs_resolved": cc, "material": mat, "valid_ids": valid,
                                      "msg_conv": mc, "truncated_ids": trunc})
    return {"path": path, "article": str(path.resolve().relative_to(ROOT)).replace("\\", "/"),
            "title": title, "writer": frontmatter(text).get("модель", ""),
            "sections": secs}

# ---------------------------------------------------------------- судья: фазы конвейера

def _as_ids(v):
    """id сообщений из evidence: модели пишут и [57836], и "57836", и "nft-c0681#57836"."""
    out = []
    items = v if isinstance(v, list) else [v] if v is not None else []
    for x in items:
        if isinstance(x, bool):
            continue
        if isinstance(x, int):
            out.append(x)
            continue
        for part in re.split(r"[;,\s]+", str(x).strip()):
            if "#" in part:                      # "nft-c0681#57836" → берём хвост после последнего #
                part = part.rsplit("#", 1)[-1]
            m = re.fullmatch(r"#?(\d+)", part.strip())
            if m:
                out.append(int(m.group(1)))
    return out


def _as_bool(v, default=True):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("false", "no", "нет", "0"):
        return False
    if s in ("true", "yes", "да", "1"):
        return True
    return default


_VERDICT_FIX = {"подтверждено": "supported", "подтверждается": "supported", "поддержано": "supported",
                "частично": "partial", "нет": "unsupported", "нет в материале": "unsupported",
                "не подтверждено": "unsupported", "опровергнуто": "contradicted",
                "опровергается": "contradicted", "без источника": "no_source"}


def _as_verdict(v):
    s = str(v or "").strip().lower()
    if s in VERDICTS:
        return s
    return _VERDICT_FIX.get(s, "")


def _as_src(v):
    """src от модели → нормальное имя вида life-c0254 (или "")."""
    s = str(v or "").strip().strip("[]").split("|")[0].strip().lower()
    return s if CONV_RE.match(s) else ""


def _by_num(raw, n):
    """Ответ судьи по номерам утверждений → {номер: объект}; запасной матч — по тексту."""
    if isinstance(raw, dict):
        raw = list(raw.values())
    by_num = {}
    for c in raw or []:
        if not isinstance(c, dict):
            continue
        num = c.get("num")
        if isinstance(num, str) and num.strip().isdigit():
            num = int(num)
        if isinstance(num, int) and 1 <= num <= n:
            by_num[num] = c
    return by_num


def extract_claims(heading, text):
    """Фаза 1: список атомарных утверждений с src — короткий вызов судьи без материала."""
    user = f"РАЗДЕЛ «{heading}»:\n\n{text.strip()}"
    try:
        # retries=2: на temperature=0 повторы дают тот же обрыв — быстрее отдать вызов fallback-модели
        res = call_llm(JUDGE, EXTRACT_SYS, user, max_tokens=6000, temperature=0, retries=2,
                       fallback=FALLBACK)
    except Exception as e:
        return [], str(e)[:250]
    claims = []
    for i, c in enumerate(_by_num_list(res), 1):
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            continue
        claims.append({"num": i, "text": str(c["text"]).strip(), "src": _as_src(c.get("src")),
                       "verdict": "", "evidence": [], "quote": "", "problem": "",
                       "hedge_ok": True, "fix": "", "flags": [], "second": None,
                       "correct_source": ""})
    return claims, None


def _by_num_list(res):
    """claims из ответа модели (список или dict-обёртка)."""
    raw = res.get("claims") if isinstance(res, dict) else res
    if isinstance(raw, dict):
        raw = list(raw.values())
    return raw or []


def check_claims(claims, ch):
    """Фаза 2: проверка пачками ≤ CLAIMS_PER_CALL с тем же материалом; компактный JSON по номерам.
    Возвращает (число вызовов, ошибка)."""
    calls, err = 0, None
    norm_mat = _norm(ch["material"])
    for i in range(0, len(claims), CLAIMS_PER_CALL):
        batch = claims[i:i + CLAIMS_PER_CALL]
        listing = "\n".join(f"{j}. {c['text']}" for j, c in enumerate(batch, 1))
        user = ("УТВЕРЖДЕНИЯ (проверь каждое по номеру):\n\n" + listing + "\n\n"
                "МАТЕРИАЛ — сообщения разговоров, на которые ссылается раздел "
                "(#id — номер сообщения; ⟪…⟫ — расшифровки медиа; помеченное «…[обрезано]» укорочено; "
                "=== — границы разговоров):\n\n" + ch["material"])
        try:
            res = call_llm(JUDGE, CHECK_SYS, user, max_tokens=6000, temperature=0, retries=2,
                           fallback=FALLBACK)
            calls += 1
        except Exception as e:
            err = str(e)[:250]
            for cl in batch:
                cl["flags"].append("llm_error")   # пачка не проверена — ошибка LLM
            continue
        by_num = _by_num(res.get("claims") if isinstance(res, dict) else res, len(batch))
        for j, cl in enumerate(batch, 1):
            c = by_num.get(j)
            if c is None:
                cl["verdict"], cl["problem"] = "unsupported", "судья не вернул вердикт по утверждению"
                cl["flags"] = ["judge_missing"]
                continue
            verdict = _as_verdict(c.get("verdict"))
            flags = []
            if not verdict:
                verdict, flags = "unsupported", ["bad_verdict"]
            ev = [x for x in _as_ids(c.get("evidence")) if x in ch["valid_ids"]]
            if verdict == "supported" and not ev:
                verdict = "unsupported"          # supported без валидного evidence → понижаем
                flags.append("judge_no_evidence")
            quote = str(c.get("quote") or "").strip()
            q_ok = bool(quote) and _norm(quote) in norm_mat
            if quote and not q_ok:
                flags.append("bad_quote")
            cl["verdict"] = verdict
            cl["evidence"] = ev
            cl["quote"] = quote
            cl["problem"] = str(c.get("problem") or "").strip()
            cl["hedge_ok"] = _as_bool(c.get("hedge_ok"))
            cl["fix"] = str(c.get("fix") or "").strip()
            cl["flags"] = flags
    return calls, err


def retry_truncated(claims, ch):
    """Фаза 3: утверждения с жалобой на обрезку → перепроверка с полным материалом
    (расшифровки до MEDIA_FULL). Возвращает (число вызовов, ошибка)."""
    flagged = [c for c in claims if TRUNC_RE.search(c.get("problem") or "")]
    if not flagged or not ch.get("truncated_ids"):
        return 0, None
    if "full_material" not in ch:
        mat, valid, mc, _ = build_material(ch["convs_resolved"], ch["text"],
                                           limit=FULL_MATERIAL_LIMIT, media_limit=MEDIA_FULL,
                                           shorten=False)
        ch["full_material"], ch["full_valid"], ch["full_msg_conv"] = mat, valid, mc
    calls, err = 0, None
    norm_mat = _norm(ch["full_material"])
    for i in range(0, len(flagged), CLAIMS_PER_CALL):
        batch = flagged[i:i + CLAIMS_PER_CALL]
        listing = "\n".join(f"{j}. {c['text']}" for j, c in enumerate(batch, 1))
        user = ("УТВЕРЖДЕНИЯ, не проверенные из-за урезанных расшифровок (проверь каждое по номеру):\n\n"
                + listing + "\n\n"
                "МАТЕРИАЛ — те же разговоры, расшифровки медиа полностью:\n\n" + ch["full_material"])
        try:
            res = call_llm(JUDGE, CHECK_SYS, user, max_tokens=6000, temperature=0, retries=2,
                           fallback=FALLBACK)
            calls += 1
        except Exception as e:
            err = str(e)[:250]
            for cl in batch:
                cl["flags"].append("llm_error")   # допроверка не удалась — ошибка LLM
            continue
        by_num = _by_num(res.get("claims") if isinstance(res, dict) else res, len(batch))
        for j, cl in enumerate(batch, 1):
            c = by_num.get(j)
            if c is None:
                continue
            verdict = _as_verdict(c.get("verdict"))
            if not verdict:
                continue
            ev = [x for x in _as_ids(c.get("evidence")) if x in ch["full_valid"]]
            quote = str(c.get("quote") or "").strip()
            if verdict == "supported" and not ev:
                continue                       # странный результат — оставляем прежний вердикт
            cl["verdict"] = verdict
            cl["evidence"] = ev
            if quote and _norm(quote) in norm_mat:
                cl["quote"] = quote
            cl["problem"] = str(c.get("problem") or "").strip()
            cl["fix"] = str(c.get("fix") or "").strip()
            cl["flags"].append("media_retry")
    return calls, err


def second_opinion(claims, ch):
    """Фаза 4: unsupported/contradicted перепроверяет вторая модель (пачками, компактный JSON).
    Возвращает (число вызовов, ошибка)."""
    need = [c for c in claims if c["verdict"] in ("unsupported", "contradicted")]
    if not need:
        return 0, None
    calls, serr = 0, None
    norm_mat = _norm(ch["material"])
    for i in range(0, len(need), CLAIMS_PER_CALL):
        batch = need[i:i + CLAIMS_PER_CALL]
        listing = "\n".join(f"{j}. {c['text']}" for j, c in enumerate(batch, 1))
        user = ("УТВЕРЖДЕНИЯ ДЛЯ ПЕРЕПРОВЕРКИ (первый фактчекер: не подтверждено или опровергнуто):\n\n"
                + listing + "\n\n"
                "МАТЕРИАЛ — сообщения разговоров (#id — номер сообщения; ⟪…⟫ — расшифровки медиа):\n\n"
                + ch["material"])
        try:
            res = call_llm(SECOND, SECOND_SYS, user, max_tokens=12000, temperature=0, retries=2,
                           fallback=SECOND_FALLBACK)
            calls += 1
        except Exception as e:
            serr = str(e)[:250]
            for cl in batch:
                cl["flags"].append("llm_error")   # второе мнение не получено — ошибка LLM
            continue
        raw = res.get("claims") if isinstance(res, dict) else res
        by_num = _by_num(raw, len(batch))
        if not by_num and isinstance(raw, list):      # запасной матч — по тексту утверждения
            for c2 in raw:
                if not isinstance(c2, dict):
                    continue
                n = _norm(str(c2.get("text") or ""))
                for j, cl in enumerate(batch, 1):
                    if n and n == _norm(cl["text"]) and j not in by_num:
                        by_num[j] = c2
                        break
        for j, cl in enumerate(batch, 1):
            c2 = by_num.get(j)
            if c2 is None:
                continue
            v2 = _as_verdict(c2.get("verdict")) or "unsupported"
            ev2 = [x for x in _as_ids(c2.get("evidence")) if x in ch["valid_ids"]]
            q2 = str(c2.get("quote") or "").strip()
            vq = bool(q2) and _norm(q2) in norm_mat
            cl["second"] = {"verdict": v2, "evidence": ev2, "quote": q2, "valid_quote": vq}
            if v2 == "supported" and vq:
                cl["verdict"] = "disputed"
            else:
                cl["flags"].append("confirmed_by_second")
    return calls, serr

# ---------------------------------------------------------------- поиск настоящего источника

def conv_number(conv):
    m = re.match(r"c(\d+)", conv or "")
    return int(m.group(1)) if m else None


def _ts(s):
    try:
        return _dt.strptime((s or "")[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def neighbor_convs(topic, conv, exclude):
    """Соседние разговоры той же темы: |Δномер| ≤ 3 или |Δвремя| ≤ 12 ч; без цитируемых и пустых."""
    key = (topic, conv, tuple(sorted(exclude)))
    if key in _NEIGH_CACHE:
        return _NEIGH_CACHE[key]
    num = conv_number(conv)
    row = db.execute("SELECT start_ts FROM convs WHERE topic=? AND conv=?", (topic, conv)).fetchone()
    t0 = _ts(row["start_ts"]) if row is not None else None
    cands = []
    for r in db.execute("SELECT conv, start_ts, msg_ids FROM convs WHERE topic=?", (topic,)).fetchall():
        if r["conv"] == conv or (topic, r["conv"]) in exclude or not json.loads(r["msg_ids"] or "[]"):
            continue
        n2, t2 = conv_number(r["conv"]), _ts(r["start_ts"])
        dnum = abs(n2 - num) if (n2 is not None and num is not None) else 10 ** 9
        dts = abs((t2 - t0).total_seconds()) if (t2 is not None and t0 is not None) else 10 ** 9
        if dnum <= 3 or dts <= 12 * 3600:
            cands.append((dnum, dts, r["conv"]))
    cands.sort()
    res = [c for _, _, c in cands][:NEIGH_MAX]
    _NEIGH_CACHE[key] = res
    return res


def _claim_words(text, top=10):
    """Значимые слова утверждения для FTS: названия (латиница), числа, длинные кириллические."""
    out, seen = [], set()
    for w in re.findall(r"[a-zа-яё0-9]{2,}", (text or "").lower()):
        if w in STOPWORDS or w in seen:
            continue
        if w.isdigit() and len(w) < 3:          # «45» и пр. — шум
            continue
        if w[0] in "abcdefghijklmnopqrstuvwxyz" and len(w) < 3:
            continue
        if w[0] not in "abcdefghijklmnopqrstuvwxyz" and not w.isdigit() and len(w) < 5:
            continue
        seen.add(w)
        out.append(w)
    out.sort(key=lambda w: (0 if w[0] in "abcdefghijklmnopqrstuvwxyz" else 1, -len(w)))
    return out[:top]


def fts_convs(text, exclude, top=FTS_TOP):
    """Топ разговоров по fts_msg: запрос из значимых слов (OR, префиксы), без цитируемых."""
    key = _norm(text)
    if key not in _FTS_CACHE:
        words = _claim_words(text)
        res = []
        for ws in (words, words[:3]):
            if not ws:
                break
            q = " OR ".join(w + "*" for w in ws)
            try:
                rows = db.execute("SELECT topic, conv, COUNT(*) c FROM fts_msg WHERE fts_msg MATCH ? "
                                  "GROUP BY topic, conv ORDER BY c DESC LIMIT 30", (q,)).fetchall()
            except sqlite3.OperationalError:
                continue                        # экзотический токен — пробуем меньше слов
            res = [(r["topic"], r["conv"]) for r in rows]
            break
        _FTS_CACHE[key] = res
    return [tc for tc in _FTS_CACHE[key] if tc not in exclude][:top]


def _search_candidates(claim, ch):
    """Кандидаты в настоящие источники: соседи цитируемого разговора + FTS, без уже проверенных."""
    cited = {(t, c) for t, c, _ in ch["convs_resolved"]}
    src = claim.get("src") or ""
    m = CONV_RE.match(src) if src else None
    bases = [(INV[m.group(1)], m.group(2))] if m else sorted(cited)
    if not bases:
        return []
    exclude = cited | set(bases)
    cands = []
    for topic, conv in bases:
        for c2 in neighbor_convs(topic, conv, exclude):
            if (topic, c2) not in cands:
                cands.append((topic, c2))
    for tc in fts_convs(claim["text"], exclude):
        if tc not in cands:
            cands.append(tc)
    return cands[:SEARCH_MAX_CONVS]


def find_real_source(claim, ch):
    """Фаза 5: итоговый unsupported/contradicted → настоящий источник (один вызов судьи).
    Возвращает ({"conv","evidence","quote"} | None, число вызовов); результат кэшируется по тексту."""
    key = _norm(claim["text"])
    if key in _SEARCH_CACHE:
        return _SEARCH_CACHE[key], 0
    res, calls = None, 0
    cands = _search_candidates(claim, ch)
    if cands:
        mkey = tuple(cands)
        if mkey not in _MAT_CACHE:
            triples = [(t, c, f"{SHORT[t]}-{c}") for t, c in cands]
            # by_conv: кандидаты по порядку близости (сначала соседи, потом FTS), на разговор ≤ SEARCH_CONV_LIMIT
            _MAT_CACHE[mkey] = build_material(triples, claim["text"], limit=SEARCH_MATERIAL_LIMIT,
                                              by_conv=True, conv_limit=SEARCH_CONV_LIMIT)
        mat, valid, msg_conv, _ = _MAT_CACHE[mkey]
        if mat.strip():
            src = claim.get("src") or ""
            user = (f"УТВЕРЖДЕНИЕ ИЗ СТАТЬИ (в статье помечено как взятое из "
                    f"{('[[' + src + ']]') if src else 'указанного разговора'}, "
                    f"но там первый фактчекер его не нашёл):\n\n{claim['text']}\n\n"
                    "МАТЕРИАЛ — сообщения соседних разговоров (те же дни/темы; #id — номер сообщения; "
                    "⟪…⟫ — расшифровки медиа; === — границы разговоров):\n\n" + mat)
            norm_mat = _norm(mat)
            for attempt in (1, 2):              # вторая попытка — если цитата не дословная
                try:
                    r = call_llm(JUDGE, SEARCH_SYS, user, max_tokens=2500, temperature=0, retries=2,
                                 fallback=FALLBACK)
                    calls += 1
                except Exception as e:
                    claim["flags"].append("llm_error")   # поиск источника не удался
                    print(f"[llm] поиск источника не удался ({str(e)[:90]!r}) — "
                          f"утверждение остаётся unsupported", flush=True)
                    break
                if not isinstance(r, dict) or not _as_bool(r.get("found"), default=False):
                    break
                conv = _as_src(r.get("conv"))
                ev = [x for x in _as_ids(r.get("evidence")) if x in valid]
                q = str(r.get("quote") or "").strip()
                if conv and conv in msg_conv.values() and ev and q and _norm(q) in norm_mat:
                    res = {"conv": conv, "evidence": ev, "quote": q}
                    break
                if attempt == 1 and q:
                    user += "\n\nВАЖНО: quote скопируй из материала дословно, символ в символ."
    _SEARCH_CACHE[key] = res
    return res, calls


def check_chunk(heading, ch):
    """Кусок раздела → утверждения с вердиктами (все 5 фаз конвейера).
    Возвращает (claims, число вызовов, ошибка судьи, ошибка второго мнения)."""
    claims, err = extract_claims(heading, ch["text"])
    calls, serr = 1, None
    if err:
        return claims, calls, err, serr
    has_links = bool(ch["convs_resolved"])
    for c in claims:
        if not has_links or not c["src"]:
            c["verdict"] = "no_source"          # у утверждения нет своей ссылки в этом куске
            if not has_links:
                c["problem"] = "в этом куске раздела нет ссылок [[...]] на разговоры"
    todo = [c for c in claims if not c["verdict"]]
    if todo and not ch["material"].strip():
        for c in todo:                          # цитируемые разговоры без сообщений
            c["verdict"] = "unsupported"
            c["flags"] = ["no_material"]
            c["problem"] = "материал цитируемых разговоров пуст (сообщений нет)"
        todo = []
    if todo:
        n, cerr = check_claims(todo, ch)
        calls += n
        if cerr:
            err = cerr
    n, _ = retry_truncated(claims, ch)          # допроверка обрезанных расшифровок
    calls += n
    n, serr = second_opinion(claims, ch)
    calls += n
    for c in claims:                            # поиск настоящего источника
        if c["verdict"] in ("unsupported", "contradicted"):
            found, n = find_real_source(c, ch)
            calls += n
            if found:
                c["verdict"] = "wrong_citation"
                c["correct_source"] = found["conv"]
                c["evidence"] = found["evidence"]
                c["quote"] = found["quote"]
                c["flags"].append("wrong_citation")
    return claims, calls, err, serr


def check_section(sec):
    """Раздел → вердикты: куски секции (у мега-разделов свой материал на кусок). БД не трогаем."""
    t0 = time.time()
    claims, calls, errors, second_errs = [], 0, [], []
    msg_conv = {}
    for ch in sec["chunks"]:
        got, n, err, serr = check_chunk(sec["heading"], ch)
        claims += got
        calls += n
        if err:
            errors.append(err)
        if serr:
            second_errs.append(serr)
        msg_conv.update(ch["msg_conv"])
        if "full_msg_conv" in ch:
            msg_conv.update(ch["full_msg_conv"])
    for c in claims:                            # evidence wrong_citation → правильный источник
        if c["verdict"] == "wrong_citation" and c["correct_source"]:
            msg_conv.update({str(i): c["correct_source"] for i in c["evidence"]})
    cnt = Counter(c["verdict"] for c in claims)
    res = {"heading": sec["heading"], "convs": sec["convs"],
           "material_chars": sum(len(ch["material"]) for ch in sec["chunks"]),
           "chunks": len(sec["chunks"]), "llm_calls": calls, "seconds": round(time.time() - t0, 1),
           "claims": claims,
           "summary": ", ".join(f"{v} {k}" for k, v in cnt.most_common()) or "утверждений нет",
           "msg_conv": {}}
    ids = set()
    for c in claims:
        ids.update(c["evidence"])
        if c["second"]:
            ids.update(c["second"]["evidence"])
    res["msg_conv"] = {str(i): msg_conv[i] for i in ids if i in msg_conv}
    if errors:
        res["error"] = "; ".join(errors)[:300]
    if second_errs:
        res["second_error"] = "; ".join(second_errs)[:300]
    return res

# ---------------------------------------------------------------- запуск и результаты

def stats_of(sections):
    cnt, hedge, flags = Counter(), 0, Counter()
    for sec in sections:
        for c in sec.get("claims", []):
            cnt[c.get("verdict", "?")] += 1
            if c.get("hedge_ok") is False:
                hedge += 1
            for f in c.get("flags") or []:
                flags[f] += 1
    st = {"claims": sum(cnt.values()), "unchecked": cnt.get("", 0) + cnt.get("?", 0)}
    for v in VERDICTS + ("disputed",):
        st[v] = cnt.get(v, 0)
    st["hedge"] = hedge
    if flags:
        st["flags"] = dict(flags)
    return st


def save_article(art, sec_results):
    data = {"article": str(art["path"].relative_to(ROOT)).replace("\\", "/"),
            "title": art["title"], "writer": art["writer"],
            "judge": JUDGE, "second": SECOND,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sections": sec_results, "stats": stats_of(sec_results),
            "seconds": round(time.time() - RUN_T0, 1),
            "llm_calls": sum(s.get("llm_calls", 0) for s in sec_results)}
    f = OUT / (art["path"].stem + ".json")
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return f, data


def check_articles(arts):
    """Все разделы всех статей — в общий пул (WORKERS потоков); статья сохраняется, когда
    готовы её разделы. Запросы к провайдерам ограничивают семафоры в llm.py (reformboss ≤
    llm.LIMIT_REFORMBOSS, HCNSEC ≤ llm.LIMIT_HCNSEC). В консоль — прогресс:
    «готово N/M статей, ~X мин осталось»."""
    global RUN_T0
    RUN_T0 = time.time()
    t0 = RUN_T0
    sec_res = {id(a): {} for a in arts}
    remaining = {id(a): len(a["sections"]) for a in arts}
    n_secs = sum(remaining.values()) or 1
    workers = max(3, min(WORKERS, n_secs))
    done_arts = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {}
        for a in arts:
            if not a["sections"]:               # страховка: статья без разделов
                sec_res[id(a)] = {}
                remaining[id(a)] = 0
                continue
            for i, sec in enumerate(a["sections"]):
                futs[ex.submit(check_section, sec)] = (a, i)
        for fu in as_completed(futs):
            a, i = futs[fu]
            try:
                res = fu.result()
            except Exception as e:   # ошибки уже ловятся в check_section — это страховка
                res = {"heading": a["sections"][i]["heading"], "convs": [], "material_chars": 0,
                       "chunks": 0, "llm_calls": 0, "seconds": 0, "claims": [], "msg_conv": {},
                       "summary": "", "error": str(e)[:300]}
            sec_res[id(a)][i] = res
            remaining[id(a)] -= 1
            note = res.get("error") or res["summary"]
            print(f"  · {a['path'].name[:55]} → «{res['heading'][:45]}»: {note}", flush=True)
            if remaining[id(a)] == 0:
                try:
                    ordered = [sec_res[id(a)][i] for i in sorted(sec_res[id(a)])]
                    f, data = save_article(a, ordered)
                    st = data["stats"]
                    print(f"OK  {a['path'].name}: {st['claims']} утверждений, {len(ordered)} разделов, "
                          f"{st['unsupported'] + st['contradicted'] + st['disputed']} проблемных, "
                          f"wrong_cit {st['wrong_citation']}, {data['seconds']:.0f} с, "
                          f"{data['llm_calls']} вызовов → {f.name}", flush=True)
                except Exception:
                    traceback.print_exc()
                    print(f"ERR не сохранилась: {a['path'].name}", flush=True)
                done_arts += 1
                left = len(arts) - done_arts
                if left:
                    eta = (time.time() - t0) / done_arts * left / 60
                    print(f"прогресс: готово {done_arts}/{len(arts)} статей, "
                          f"~{eta:.0f} мин осталось", flush=True)
        for a in arts:                           # статьи без разделов — сохранить пустыми
            if remaining.get(id(a)) == 0 and not sec_res[id(a)]:
                try:
                    f, data = save_article(a, [])
                    print(f"OK  {a['path'].name}: 0 разделов → {f.name}", flush=True)
                except Exception:
                    traceback.print_exc()
    print(f"проверка заняла {(time.time() - t0) / 60:.1f} мин; usage: {llm.usage}", flush=True)


def claim_md(c, sec, second_model):
    """Проблемное утверждение → markdown-строки для отчёта."""
    L = [f"- **{c.get('verdict') or 'не проверено (ошибка LLM)'}** — {c['text']}"]
    if c.get("src"):
        L.append(f"  - ссылка в статье: [[{c['src']}]]")
    if c.get("problem"):
        L.append(f"  - проблема: {c['problem']}")
    if c.get("verdict") == "wrong_citation" and c.get("correct_source"):
        refs = " ".join(f"#{mid}" for mid in c.get("evidence") or [])
        L.append(f"  - правильный источник: [[{c['correct_source']}]] {refs}"
                 + (f" — «{c['quote']}»" if c.get("quote") else ""))
    if c.get("hedge_ok") is False:
        L.append("  - hedge: в источнике только мнение/слух, в статье подано как факт")
    if c.get("flags"):
        L.append(f"  - флаги: {', '.join(c['flags'])}")
    if c.get("verdict") != "wrong_citation":
        mc = sec.get("msg_conv", {})
        refs = [f"[[{mc.get(str(mid), '?')}]] #{mid}" for mid in c.get("evidence") or []]
        q = c.get("quote")
        if refs or q:
            src = (", ".join(refs) + (f" — «{q}»" if q else "")) if refs else (f"«{q}»" if q else "")
            L.append(f"  - источник: {src}")
    if c.get("second"):
        s = c["second"]
        mc = sec.get("msg_conv", {})
        srefs = [f"[[{mc.get(str(mid), '?')}]] #{mid}" for mid in s.get("evidence") or []]
        L.append(f"  - второе мнение ({second_model}): {s['verdict']}"
                 + (f" — «{s['quote']}»" if s.get("quote") else "")
                 + (f" ({', '.join(srefs)})" if srefs else ""))
    if c.get("fix"):
        L.append(f"  - правка: {c['fix']}")
    return "\n".join(L)


def make_report():
    """Сводный report.md по всем json в data/factcheck (включая закэшированные)."""
    arts = []
    for f in sorted(OUT.glob("*.json")):
        try:
            arts.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"ERR отчёт: {f.name}: {e}", flush=True)
    L = ["# Проверка фактов в статьях по источникам", "",
         f"Судья: `{JUDGE}` (fallback `{FALLBACK}`), второе мнение: `{SECOND}` (fallback `{SECOND_FALLBACK}`). "
         f"Конвейер v2: извлечение утверждений → проверка пачками ≤{CLAIMS_PER_CALL} (компактный JSON "
         f"по номерам) → допроверка обрезанных расшифровок по полному материалу (расшифровки "
         f"до {MEDIA_FULL} симв.) → второе мнение (unsupported/contradicted) → поиск настоящего "
         f"источника: соседи ±3 conv/±12 ч + FTS → wrong_citation. Расшифровки медиа в материале "
         f"до {MEDIA_LIMIT} симв., не влезающее — укороченными строками «…[обрезано]». "
         f"Разделы всех статей идут в общий пул ({WORKERS} потоков); параллельность к провайдерам "
         f"ограничивают семафоры в llm.py: reformboss ≤ {llm.LIMIT_REFORMBOSS}, HCNSEC ≤ "
         f"{llm.LIMIT_HCNSEC} одновременных запросов. Ошибки LLM — повтор с паузой "
         f"до {LLM_TRIES} попыток, затем раздел помечается error и проверка идёт дальше "
         f"(колонка «err» — утверждения, оставшиеся без вердикта из-за ошибок LLM). "
         f"Статей: {len(arts)}. Сгенерирован: {time.strftime('%Y-%m-%d %H:%M')}.", "",
         "| Статья | Утв. | supp, % | partial | unsupp | contr | no_src | wrong_cit | disputed | err | hedge | время, с | вызовов |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot = Counter()
    t_calls = 0
    for a in arts:
        st = a.get("stats", {})
        tot.update({k: v for k, v in st.items() if isinstance(v, int)})
        t_calls += a.get("llm_calls", 0)
        n = st.get("claims", 0)
        pct = round(100 * st.get("supported", 0) / n) if n else 0
        L.append(f"| {a['title']} | {n} | {pct} | {st.get('partial', 0)} | {st.get('unsupported', 0)} | "
                 f"{st.get('contradicted', 0)} | {st.get('no_source', 0)} | {st.get('wrong_citation', 0)} | "
                 f"{st.get('disputed', 0)} | {st.get('unchecked', 0)} | {st.get('hedge', 0)} | "
                 f"{a.get('seconds', 0):.0f} | {a.get('llm_calls', 0)} |")
    n = tot.get("claims", 0)
    pct = round(100 * tot.get("supported", 0) / n) if n else 0
    t_sec = max((a.get("seconds", 0) for a in arts), default=0)
    L.append(f"| **Итого** | **{n}** | **{pct}** | **{tot.get('partial', 0)}** | **{tot.get('unsupported', 0)}** | "
             f"**{tot.get('contradicted', 0)}** | **{tot.get('no_source', 0)}** | "
             f"**{tot.get('wrong_citation', 0)}** | **{tot.get('disputed', 0)}** | "
             f"**{tot.get('unchecked', 0)}** | **{tot.get('hedge', 0)}** | **{t_sec:.0f}** | **{t_calls}** |")
    if arts:
        L += ["", f"«время, с» — от старта прогона до готовности статьи (разделы всех статей идут "
                  f"параллельно в общем пуле, лимиты параллельности — в llm.py); "
                  f"в ячейке «Итого» — самый долгая статья."]
    for a in arts:
        second_model = a.get("second") or SECOND
        L += ["", f"## {a['title']}", "",
              f"Файл: `{a['article']}`" + (f"; писала модель: `{a.get('writer')}`" if a.get("writer") else "")
              + f". Утверждений: {a.get('stats', {}).get('claims', 0)}."
              + f" Время: {a.get('seconds', 0):.0f} с, вызовов: {a.get('llm_calls', 0)}.", ""]
        fixes = []
        for si, sec in enumerate(a.get("sections", [])):
            for c in sec.get("claims", []):
                if c.get("verdict") == "wrong_citation":
                    old = c.get("src") or (sec.get("convs") or ["?"])[0]
                    fixes.append((old, c.get("correct_source") or "?"))
        if fixes:
            L += ["**Исправить ссылки:**"]
            for (old, new), k in Counter(fixes).most_common():
                L.append(f"- исправить ссылку: [[{old}]] → [[{new}]] — {k} утвержд.")
            L.append("")
        sec_errs = [(s.get("heading", "?"), s.get("error"), s.get("second_error"))
                    for s in a.get("sections", []) if s.get("error") or s.get("second_error")]
        if sec_errs:
            L += ["**Ошибки LLM (раздел помечен error, проверка продолжена):**"]
            for h, e, se in sec_errs:
                L.append(f"- «{h}»:{(' ' + e) if e else ''}"
                         + (f"; второе мнение: {se}" if se else ""))
            L.append("")
        probs = []
        for si, sec in enumerate(a.get("sections", [])):
            for c in sec.get("claims", []):
                if c.get("verdict") != "supported" or c.get("hedge_ok") is False:
                    probs.append((si, c))
        if not probs:
            L += ["_Проблемных утверждений нет._"]
            continue
        L += [f"Проблемных утверждений: {len(probs)} (не supported или hedge_ok=false).", ""]
        cur = -1
        for si, c in probs:
            sec = a["sections"][si]
            if si != cur:
                L += [f"### {sec['heading']}", ""]
                cur = si
            L.append(claim_md(c, sec, second_model))
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"отчёт: {OUT / 'report.md'} (статей: {len(arts)})", flush=True)

# ---------------------------------------------------------------- выборка и CLI

def pilot_paths():
    """10 статей пилота, детерминированно (random.seed(7))."""
    random.seed(7)
    sel = random.sample(sorted((VAULT / "NFT").glob("*.md")), 2)
    for d in ("AI, вайбкодинг и разработка", "Трейдинг, перпы, арбитраж, мемкоины",
              "Безопасность и скамы", "Дропы, тестнеты и фарм активностей"):
        sel.append(random.choice(sorted((VAULT / d).glob("*.md"))))
    sch = VAULT / "Схемы"
    subdirs = {p.name for p in sch.iterdir() if p.is_dir()}
    sel.append(random.choice([p for p in sorted(sch.glob("*.md")) if p.stem not in subdirs]))
    sel.append(random.choice(sorted((sch / "Боты и софт (формы, минт, регистрация)").glob("*.md"))))
    cards = []
    for p in sorted((VAULT / "Проекты и инструменты").glob("*.md")):
        m = re.search(r"(?m)^упоминаний:\s*(\d+)", p.read_text(encoding="utf-8"))
        if m:
            cards.append((int(m.group(1)), p))
    cards.sort(key=lambda x: (-x[0], x[1].name))
    sel += [p for _, p in cards[:2]]
    return sel


def all_paths():
    """Все статьи хранилища для --all: папки категорий, Схемы/ (с подпапками),
    Проекты и инструменты/ — кроме Источники/, Медиа-конспекты/, 00 Главная.md
    и «Календаря активностей»."""
    skip_dirs = {"Источники", "Медиа-конспекты"}
    out = []
    for p in sorted(VAULT.rglob("*.md")):
        rel = p.relative_to(VAULT)
        if rel.parts[0] in skip_dirs:
            continue
        if p.name == "00 Главная.md" or p.stem.startswith("Календарь активностей"):
            continue
        out.append(p)
    return out


def main():
    global JUDGE, SECOND
    ap = argparse.ArgumentParser(description="Проверка фактов в статьях vault/ по разговорам-источникам (v2).")
    ap.add_argument("paths", nargs="*", help="пути к статьям .md (можно вместе с --pilot)")
    ap.add_argument("--pilot", action="store_true", help="пилот: 10 статей, random.seed(7)")
    ap.add_argument("--all", action="store_true",
                    help="ВСЕ статьи хранилища (готовые не трогать без --force)")
    ap.add_argument("--force", action="store_true", help="перепроверять даже готовые (кэш)")
    ap.add_argument("--judge", default=JUDGE, help=f"модель-судья (по умолчанию {JUDGE})")
    ap.add_argument("--second", default=SECOND, help=f"модель второго мнения (по умолчанию {SECOND})")
    args = ap.parse_args()
    JUDGE, SECOND = args.judge, args.second
    # HCNSEC-шлюз должен быть запущен (OpenCodeAPIManager.exe): если хоть одна из моделей
    # на нём — проверяем ДО старта, чтобы сразу ошибиться, а не виснуть на таймаутах
    if any(m.startswith(llm.HC_PREFIX) for m in (JUDGE, SECOND, FALLBACK, SECOND_FALLBACK)) \
            and not llm.gateway_ok():
        sys.exit(f"HCNSEC-шлюз ({llm.HCNSEC_BASE}) не отвечает — запусти OpenCode API Manager "
                 f"(OpenCodeAPIManager.exe) и повтори запуск")
    OUT.mkdir(parents=True, exist_ok=True)
    if args.pilot:
        paths = pilot_paths()
    elif args.all:
        paths = all_paths()
    else:
        paths = []
    for p in args.paths:
        p = Path(p)
        if not p.exists() and not (ROOT / p).exists():
            sys.exit(f"нет файла: {p}")
        paths.append((p if p.exists() else ROOT / p).resolve())
    seen, uniq = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    dup = [s for s, n in Counter(p.stem for p in uniq).items() if n > 1]
    if dup:
        sys.exit(f"одинаковые имена статей (коллизия json): {dup}")
    arts = []
    for p in uniq:
        if (OUT / (p.stem + ".json")).exists() and not args.force:
            print(f"кэш: {p.name} уже проверена (--force — перепроверить)", flush=True)
            continue
        try:
            a = prepare_article(p)
            arts.append(a)
            print(f"проверяю: {a['article']} ({len(a['sections'])} разделов)", flush=True)
        except Exception as e:
            print(f"ERR {p}: {e}", flush=True)
    if arts:
        try:
            check_articles(arts)
        except Exception:
            traceback.print_exc()
            print("ERR: проверка упала — строю отчёт по тому, что готово", flush=True)
    make_report()
    print(f"usage: {llm.usage}", flush=True)


if __name__ == "__main__":
    main()
