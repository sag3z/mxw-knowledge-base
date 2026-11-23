"""MXW map: local web app over data/kb.sqlite + vault/.

Run:  python app/server.py   → http://127.0.0.1:8765
Front:  / — новый каркас app/web/ (задачи 019+), /old — прежний одностраничный фронт,
        /web/... — статика из app/web (только внутри каталога, защита от ..).
Поиск: смысловые эмбеддинги — локально, без сети (embed_local.py: multilingual-e5-small,
ONNX через onnxruntime; таблицы emb_local/emb_art). Прежний шлюз HCNSEC (embed.py) в поиске
больше не участвует — файл оставлен для истории. Чат/дайджест — официальный DeepSeek
(llm.FAST = ds:deepseek-flash) через llm.py, fallback — reformboss deepseek-v4.1-flash.
"""
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.error
from bisect import bisect_left
from collections import Counter, OrderedDict
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import llm  # noqa: E402
import embed_local  # noqa: E402  (локальные эмбеддинги e5-small: embed_local.embed_query)


DB = ROOT / "data" / "kb.sqlite"
VAULT = ROOT / "vault"
WEB = Path(__file__).with_name("web")            # новый фронт (задача 019+)
STATIC = WEB / "index.html"
OLD = Path(__file__).with_name("index.html")     # старый фронт — на /old (до задачи 023)
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
          ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
MEDIA_INDEX = ROOT / "data" / "media_index.jsonl"
MEDIA_ROOT = Path(r"C:\Users\fl3xx\Desktop\MXW Data Base")
SHORT = {"nft_движуха": "nft", "ai_вайбкод_билдинг_хакатоны": "ai", "жизнь_флуд_кайф": "life",
         "крипта_работа_проекты": "crypto", "перпы_фандинг_dex_ы": "perps", "предикшены_и_ставки": "predict",
         "софты": "soft", "щитки_мемы_деген_плеи": "shitki", "важное_саммари_и_коллы": "important"}
LONG = {v: k for k, v in SHORT.items()}
local = threading.local()


def db():
    if not hasattr(local, "db"):
        local.db = sqlite3.connect(DB, check_same_thread=False)
        local.db.row_factory = sqlite3.Row
    return local.db


_emb = {}


def emb_matrix():
    n = db().execute("SELECT COUNT(*) FROM emb_local").fetchone()[0]
    if _emb.get("n") != n:   # reload when embeddings were added
        _emb["n"] = n
        rows = db().execute("SELECT topic, conv, vec FROM emb_local").fetchall()
        _emb["keys"] = [(r["topic"], r["conv"]) for r in rows]
        m = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]) if rows else np.zeros((0, embed_local.DIM))
        _emb["m"] = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    return _emb["keys"], _emb["m"]


_ema = {}


def emb_art_matrix():
    """Куски статей из emb_art: ([(path, chunk)], нормированная матрица). Перезагрузка при изменении числа строк."""
    if not db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='emb_art'").fetchone():
        return [], np.zeros((0, embed_local.DIM))
    n = db().execute("SELECT COUNT(*) FROM emb_art").fetchone()[0]
    if _ema.get("n") != n:
        _ema["n"] = n
        rows = db().execute("SELECT path, chunk, vec FROM emb_art").fetchall()
        _ema["keys"] = [(r["path"], r["chunk"]) for r in rows]
        m = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]) if rows else np.zeros((0, embed_local.DIM))
        _ema["m"] = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    return _ema["keys"], _ema["m"]


# --- стоп-слова и сборка FTS-запроса ------------------------------------------
# Служебные слова (рус/англ) выкидываются из FTS-запроса: иначе поиск цепляется за «какие»/«для»
# вместо темы. Слова короче 3 символов тоже выкидываем — кроме чисел и латинских тикеров
# из 2+ ЗАГЛАВНЫХ (AI, GM, OP…). ё→е и регистр не важны.

STOP = {
    # русские
    "а", "без", "бы", "был", "была", "были", "было", "будет", "быть", "в", "вам", "вас", "весь",
    "везде", "вот", "все", "всё", "всего", "всем", "всеми", "всему", "всех", "вы", "где", "да",
    "для", "до", "его", "ее", "её", "если", "есть", "еще", "ещё", "за", "здесь", "и", "из", "или",
    "им", "ими", "их", "к", "как", "какая", "какие", "какой", "каков", "когда", "кто", "куда",
    "ли", "либо", "между", "мне", "мной", "может", "можно", "мой", "моего", "моей", "моя", "мы", "над",
    "надо", "нам", "нас", "наш", "нашего", "нашей", "не", "него", "нее", "неё", "нет", "ни", "нибудь", "но", "о",
    "об", "обо", "он", "она", "они", "оно", "опять", "от", "откуда", "перед", "по",
    "под", "пока", "после", "потому", "почему", "почти", "при", "про", "сам", "сама", "сами",
    "свой", "своего", "своей", "своя", "свои", "со", "совсем", "так", "также", "там", "твой",
    "твоего", "твоей", "твоя", "те", "тем",
    "теперь", "то", "тогда", "тоже", "той", "только", "том", "тому", "тот", "ту", "тут", "ты", "у",
    "уж", "уже", "хоть", "хотя", "через", "что", "чтоб", "чтобы", "чуть", "эта", "эти", "это",
    "этого", "этой", "этим", "этими", "этот", "эту", "же",
    # английские
    "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "been", "being", "but",
    "by", "can", "could", "did", "do", "does", "doing", "for", "from", "had", "has", "have",
    "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is",
    "it", "its", "just", "me", "my", "no", "nor", "not", "of", "on", "or", "our", "ours", "out",
    "own", "s", "she", "should", "so", "some", "such", "t", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "those", "to", "too", "under", "up",
    "was", "we", "were", "what", "when", "where", "which", "who", "whom", "whose", "why",
    "will", "with", "would", "you", "your",
}


def _norm(w):
    return w.lower().replace("ё", "е")


def _keep_word(w):
    """Слово идёт в FTS-запрос: не стоп-слово, и длина ≥3 (либо число, либо латинский тикер 2+ ЗАГЛАВНЫХ)."""
    if _norm(w) in STOP:
        return False
    if len(w) >= 3:
        return True
    return w.isdecimal() or bool(re.fullmatch(r"[A-Z]{2,}", w))


def _fts_terms(words):
    """Слова → уникальные FTS5 префиксные термы (кавычки экранированы)."""
    terms, seen = [], set()
    for w in words:
        if _keep_word(w) and _norm(w) not in seen:
            seen.add(_norm(w))
            terms.append('"' + w.replace('"', '""') + '"*')
    return terms


def fts_query(q):
    """FTS5-запрос из слов запроса: без стоп-слов, длина ≥3 (кроме чисел и тикеров), префиксные термы через OR."""
    return " OR ".join(_fts_terms(re.findall(r"\w+", q)))


def fts_query_kw(keywords, cap=40):
    """FTS5-запрос из ключевых слов LLM: каждое ключевое слово — один терм через OR: однословное —
    префиксный терм (те же фильтры стоп-слов/длины), многословное — фраза из всех его слов
    (префикс на последнем; дефисы в тексте и запросе токенизируются одинаково). Фраза не
    расщепляется на отдельные слова — иначе «антидетект-браузер» тонет в общих «браузер»."""
    terms, seen = [], set()
    for kw in keywords:
        words = re.findall(r"\w+", kw)
        if not words:
            continue
        if len(words) == 1:
            if not _keep_word(words[0]):
                continue
            term = '"' + words[0].replace('"', '""') + '"*'
        elif not any(_keep_word(w) for w in words):
            continue   # все слова фразы — служебные
        else:
            term = '"' + " ".join(words).replace('"', '""') + '"*'
        if term not in seen:
            seen.add(term)
            terms.append(term)
    return " OR ".join(terms[:cap])


# --- индекс медиа Telegram-экспорта --------------------------------------------
MIME_FIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
            ".ogg": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".m4a": "audio/mp4", ".flac": "audio/flac"}
MEDIA_CHUNK = 256 * 1024


def _load_media_index():
    """Индекс медиа: files[i] — путь/kind/миниатюра по номеру строки индекса;
    bymsg[(topic, msg)] — [{"i", "kind", "name", "size", "th"}] для conv_detail.
    th=1: рядом с файлом лежит миниатюра <имя>_thumb.jpg. Клиенту пути не отдаются."""
    files, bymsg = [], {}
    if not MEDIA_INDEX.exists():
        return files, bymsg
    with open(MEDIA_INDEX, encoding="utf-8") as f:
        for i, line in enumerate(f):
            d = json.loads(line)
            p = Path(d["path"])
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            thumb = p.with_name(p.stem + "_thumb.jpg")
            has_thumb = thumb.exists()
            files.append({"path": p, "kind": d["kind"], "name": p.name,
                          "thumb": thumb if has_thumb else None})
            bymsg.setdefault((d["topic"], d["msg"]), []).append(
                {"i": i, "kind": d["kind"], "name": p.name, "size": size, "th": 1 if has_thumb else 0})
    return files, bymsg


MEDIA_FILES, MEDIA_BY_MSG = _load_media_index()


RRF_K = 60          # Reciprocal Rank Fusion constant (кусочки статей — _art_hits)
SEM_TOP = 120       # кандидатов-разговоров из смысловой части
FTS_CONV_TOP = 60   # кандидатов-разговоров из fts_conv
FTS_MSG_TOP = 60    # кандидатов-разговоров из fts_msg (ранг разговора = лучший ранг его сообщений)
FTS_MSG_FETCH = 300 # сообщений из fts_msg: покрывает топ-FTS_MSG_TOP разговоров и сниппеты к ним
HITS_PER_CONV = 3   # сниппетов на разговор


# --- эвристика «пустого» сообщения (noise) -----------------------------------
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002190-\U000021FF\U00002300-\U000023FF\U00002500-\U000027BF"
                      "\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U0000200D\U0000203C\U00002049\U00002122\U00002139]+")
NOTWORD_RE = re.compile(r"[^\w]")
# короткие реакции (регистронезависимо, возможны повторы букв и эмодзи)
REACT = {"ору", "лол", "кек", "+", "++", "ок", "окей", "да", "нет", "жиза", "база", "пон", "понял",
         "спс", "спасибо", "ясно", "ага", "угу", "хм", "бля", "пиздец", "ебать", "топ", "имба", "гг",
         "lol", "lmao", "ok", "gm", "gn", "wow", "xd"}


def _collapse_runs(s):
    """аааахахах → ахахах, оруууу → ору (повторы букв схлопываются)."""
    return "".join(c for i, c in enumerate(s) if i == 0 or s[i - 1] != c)


def _react_token(tok):
    if tok in REACT:
        return True
    c = _collapse_runs(tok)
    if c in REACT:
        return True
    for p in ("ахах", "хах"):   # ахах*/хах*: продолжение только из букв а/х
        if c.startswith(p) and set(c) <= set("ах"):
            return True
    return False


def is_noise(text, media_text, files=None):
    """Эвристика пустого сообщения: (нет расшифровок медиа и «чистый» текст короче 4 символов)
    ИЛИ текст целиком из коротких реакций. Сообщение с файлами из экспорта (фото/видео/голос/документы;
    стикеров в индексе нет) — содержательное, не noise. Ключевые сообщения не помечаются здесь — см. conv_detail()."""
    if files:
        return False
    if not _is_reaction_text(text):
        return not media_text and len(NOTWORD_RE.sub("", EMOJI_RE.sub("", text or ""))) < 4
    return True


def _is_reaction_text(text):
    if not text or not text.strip():
        return False
    s = EMOJI_RE.sub(" ", text.lower())
    toks = re.findall(r"[\w+]+", s)   # слова и «+»/«++»
    return bool(toks) and all(_react_token(t) for t in toks)


def _conv_where(branch, category, schemes, drop, date_from, date_to):
    """SQL-условия по convs (алиас c) + параметры — для FTS-запросов."""
    where, params = ["1=1"], []
    if branch:
        if branch not in LONG:
            raise ValueError("bad branch")
        where.append("c.topic=?")
        params.append(LONG[branch])
    if category:
        where.append("c.category=?")
        params.append(category)
    if schemes:
        where.append("c.has_scheme=1")
    if not drop:
        where.append("c.verdict!='drop'")
    if date_from:
        where.append("substr(c.start_ts,1,10)>=?")
        params.append(date_from)
    if date_to:
        where.append("substr(c.start_ts,1,10)<=?")
        params.append(date_to)
    return " AND ".join(where), params


# --- скоринг гибридного поиска: смысл главный, слова бонусом ---------------------
W_KW_CONV = 0.15    # вес kw_conv — доли значимых термов запроса/keywords в выжимке+тегах разговора
W_KW_MSG = 0.08     # вес kw_msg — совпадения в сообщениях
W_KEEP = 0.05       # вес keep — verdict='keep'
KW_DF_FRAC = 0.04   # терм, встречающийся более чем в этой доле разговоров fts_conv, — «общий», бонуса не даёт

_kwdf = {}


def _conv_kw_index():
    """Индекс слов по fts_conv (кэш в памяти, перезагрузка при изменении числа строк):
    tokens — нормализованные слова body (выжимка+теги) разговора, df — в скольких разговорах
    встречается слово, sorted_tokens — все слова по алфавиту (префиксные выборки bisect'ом)."""
    try:
        n = db().execute("SELECT COUNT(*) FROM fts_conv").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    if _kwdf.get("n") != n:
        tokens, df = {}, Counter()
        try:
            rows = db().execute("SELECT topic, conv, body FROM fts_conv").fetchall()
        except sqlite3.OperationalError:   # fts-таблиц нет (build_fts.py не запускался)
            rows = []
        for r in rows:
            toks = frozenset(_norm(w) for w in re.findall(r"\w+", r["body"] or ""))
            tokens[(r["topic"], r["conv"])] = toks
            df.update(toks)
        _kwdf.update(n=n, tokens=tokens, df=df, sorted_tokens=sorted(df), n_docs=len(rows))
    return _kwdf


def _prefix_tokens(term):
    """Слова индекса fts_conv, начинающиеся с term (префикс — как термы FTS-запроса)."""
    ix = _conv_kw_index()
    toks, i = ix["sorted_tokens"], bisect_left(ix["sorted_tokens"], term)
    out = set()
    while i < len(toks) and toks[i].startswith(term):
        out.add(toks[i])
        i += 1
    return out


def _sig_terms(q, keywords):
    """Значимые термы для kw_conv: слова из keywords (если заданы — они же питают FTS-часть),
    иначе слова запроса; после стоп-слов и длины (_keep_word) и df-фильтра: терм, встречающийся
    более чем в KW_DF_FRAC разговоров fts_conv, — «общий», бонуса не даёт (df считается один
    раз и кэшируется — см. _conv_kw_index). Возвращает [(норм-терм, {слова индекса с этим
    префиксом})], без повторов."""
    words = ([w for kw in keywords for w in re.findall(r"\w+", kw)] if keywords else re.findall(r"\w+", q))
    ix = _conv_kw_index()
    out, seen = [], set()
    for w in words:
        t = _norm(w)
        if t in seen or not _keep_word(w):
            continue
        seen.add(t)
        toks = _prefix_tokens(t)
        if sum(ix["df"].get(x, 0) for x in toks) > KW_DF_FRAC * ix["n_docs"]:
            continue   # «общий» терм (частый в выжимках) — бонуса не даёт
        out.append((t, toks))
    return out


def hybrid_search(q, k=20, date_from=None, date_to=None, branch=None, category=None,
                  schemes=False, drop=False, keywords=None):
    """Гибридный поиск: смысл главный — слова бонусом. RRF для разговоров больше не
    используется (словесные хиты не вытесняют смысловые), слияние — через скор.
    q — запрос для смысловой части; keywords — ключевые слова от LLM (FTS-запрос и kw_conv
    строятся из них), None → FTS и kw_conv по словам q без стоп-слов.

    Кандидаты: топ-SEM_TOP по смыслу ∪ топ-FTS_CONV_TOP из fts_conv ∪ топ-FTS_MSG_TOP
    разговоров из fts_msg (ранг разговора — лучший ранг его сообщений). Скор:
        score = sim_norm + W_KW_CONV*kw_conv + W_KW_MSG*kw_msg + W_KEEP*keep,
    где sim_norm — косинус запроса с вектором разговора из emb_local, линейно нормированный
    по кандидатам в [0, 1] (у разговоров без вектора — drop'ов — sim = минимальный среди
    кандидатов − 0.01); kw_conv — доля РАЗНЫХ значимых термов запроса/keywords, найденных
    в выжимке+тегах разговора (fts_conv body; значимые термы — см. _sig_terms); kw_msg — 1,
    если есть совпадения в сообщениях, иначе 0; keep — 1 для verdict='keep'.
    Сниппеты hits — как раньше из fts_msg. Возвращает (список разговоров, semantic_ok)."""
    where, params = _conv_where(branch, category, schemes, drop, date_from, date_to)

    def keep(r):   # те же фильтры в Python — для смысловой части
        if branch and r["topic"] != LONG[branch]:
            return False
        if category and r["category"] != category:
            return False
        if schemes and not r["has_scheme"]:
            return False
        if not drop and r["verdict"] == "drop":
            return False
        d = (r["start_ts"] or "")[:10]
        return not ((date_from and d < date_from) or (date_to and d > date_to))

    convs = {(r["topic"], r["conv"]): r for r in db().execute("SELECT * FROM convs")}

    # 1) смысл: косинусы запроса со всеми разговорами с векторами → топ-SEM_TOP + карта key→sim
    semantic_ok = True
    sem, key2sim = [], {}
    try:
        keys, m = emb_matrix()
        if keys:
            v = embed_local.embed_query(q)   # модель в кэше, после прогрева < 0,1 с
            sims = m @ (v / (np.linalg.norm(v) + 1e-9))
            key2sim = dict(zip(keys, sims.tolist()))
            for i in np.argsort(-sims):
                if len(sem) >= SEM_TOP:
                    break
                if keep(convs[keys[i]]):
                    sem.append(keys[i])
        else:   # таблица emb_local пуста (embed_local.py не запускался) — поиск по словам
            semantic_ok = False
    except Exception:   # ошибка смысловой части (нет модели и т.п.) — поиск по словам
        semantic_ok = False

    # 2) слова: fts_conv по bm25 → топ-FTS_CONV_TOP разговоров; термы — из keywords (если заданы)
    terms = fts_query_kw(keywords) if keywords else fts_query(q)
    fconv, fmsg = [], []
    groups = {}   # (topic, conv) → [{msg, author, snippet}] (до 3 лучших сообщений)
    if terms:
        try:
            sql = (f"SELECT fts_conv.topic AS ft, fts_conv.conv AS fc, bm25(fts_conv) AS r "
                   f"FROM fts_conv JOIN convs c ON c.topic=fts_conv.topic AND c.conv=fts_conv.conv "
                   f"WHERE fts_conv MATCH ? AND {where} ORDER BY r LIMIT {FTS_CONV_TOP}")
            fconv = [(r["ft"], r["fc"]) for r in db().execute(sql, (terms, *params))]
            # fts_msg по bm25 → топ-FTS_MSG_FETCH сообщений; ранг разговора = лучший ранг его сообщений
            sql = (f"SELECT fts_msg.topic AS ft, fts_msg.conv AS fc, fts_msg.id AS mid, "
                   f"snippet(fts_msg, 0, '«', '»', '…', 14) AS snip, bm25(fts_msg) AS r "
                   f"FROM fts_msg JOIN convs c ON c.topic=fts_msg.topic AND c.conv=fts_msg.conv "
                   f"WHERE fts_msg MATCH ? AND {where} ORDER BY r LIMIT {FTS_MSG_FETCH}")
            for r in db().execute(sql, (terms, *params)):
                key = (r["ft"], r["fc"])
                if key not in groups:
                    groups[key] = []
                    fmsg.append(key)
                if len(groups[key]) < HITS_PER_CONV:
                    groups[key].append({"msg": r["mid"], "author": None, "snippet": r["snip"]})
            # авторы для сниппетов — из messages
            ids_by_topic = {}
            for (t, c), hs in groups.items():
                ids_by_topic.setdefault(t, set()).update(h["msg"] for h in hs)
            authors = {}
            for t, mids in ids_by_topic.items():
                sql = f"SELECT id, author FROM messages WHERE topic=? AND id IN ({','.join('?' * len(mids))})"
                for r in db().execute(sql, (t, *mids)):
                    authors[(t, r["id"])] = r["author"]
            for (t, c), hs in groups.items():
                for h in hs:
                    h["author"] = authors.get((t, h["msg"])) or ""
        except sqlite3.OperationalError:   # fts-таблиц нет (build_fts.py не запускался) — только смысл
            fconv, fmsg, groups = [], [], {}

    # 3) кандидаты: смысл ∪ fts_conv ∪ топ-FTS_MSG_TOP разговоров из fts_msg
    cand = list(dict.fromkeys(sem + fconv + fmsg[:FTS_MSG_TOP]))

    # 3а) sim_norm: косинус из emb_local; у разговоров без вектора (drop'ов) — минимум − 0.01
    real = {key: key2sim[key] for key in cand if key in key2sim}
    if real:
        base = min(real.values()) - 0.01
        sc = {key: real.get(key, base) for key in cand}
        mn, mx = min(sc.values()), max(sc.values())
        span = mx - mn
        sim_norm = {key: ((s - mn) / span if span > 1e-12 else 1.0) for key, s in sc.items()}
    else:   # смысловой части нет — ранжируют только слова
        sim_norm = dict.fromkeys(cand, 0.0)

    # 3б) скор: sim_norm + бонусы за слова (kw_conv, kw_msg) и keep
    sig = _sig_terms(q, keywords)
    body_toks = _conv_kw_index()["tokens"]
    scored = []
    for key in cand:
        r = convs[key]
        kw_conv = 0.0
        if sig:
            toks = body_toks.get(key)
            if toks:
                kw_conv = sum(1 for _t, ts in sig if not ts.isdisjoint(toks)) / len(sig)
        s = sim_norm[key] + W_KW_CONV * kw_conv
        if key in groups:   # kw_msg: есть совпадения в сообщениях
            s += W_KW_MSG
        if r["verdict"] == "keep":
            s += W_KEEP
        scored.append((key, s))

    out = []
    for (t, c), s in sorted(scored, key=lambda x: (-x[1], x[0]))[:k]:
        r = convs[(t, c)]
        hits = groups.get((t, c), [])
        summary = r["summary"]
        if not summary and r["verdict"] == "drop":   # мусор без выжимки — текст первого найденного сообщения
            mid = hits[0]["msg"] if hits else (json.loads(r["msg_ids"]) or [None])[0]
            if mid is not None:
                row = db().execute("SELECT text, media_text FROM messages WHERE topic=? AND id=?", (t, mid)).fetchone()
                if row:   # тело как в fts_msg: текст + расшифровки медиа
                    parts = [row["text"] or ""] + [m.get("text") or "" for m in json.loads(row["media_text"] or "[]")]
                    summary = " ".join(p for p in parts if p)[:200]
        out.append({"id": f"{SHORT[t]}-{c}", "score": round(float(s), 4),
                    "date": (r["start_ts"] or "")[:10], "topic": t,
                    "title": _title_from_summary(summary), "branch": topic_titles().get(t, t),
                    "summary": summary,
                    "category": r["category"], "has_scheme": r["has_scheme"], "hits": hits})
    return out, semantic_ok


def search(q, k=20, date_from=None, date_to=None, branch=None, category=None, schemes=False, drop=False,
           keywords=None):
    """Список результатов (прежний контракт — им пользуется digest()); keywords → FTS-часть."""
    return hybrid_search(q, k, date_from, date_to, branch, category, schemes, drop, keywords=keywords)[0]


def _compute_show(msgs, focus_id):
    """Индексы показываемых сообщений для режима «Главное»:
    key ∪ hit ∪ фокус ∪ (reply_to-источник, прямые не-noise ответы, по 1 не-noise соседу с каждой
    стороны для каждого из них). noise не показывается никогда (кроме фокуса). Если ни key, ни hit
    нет — все не-noise (drop-разговор)."""
    n = len(msgs)
    idx = {m["id"]: i for i, m in enumerate(msgs)}
    focus_i = idx.get(focus_id) if focus_id is not None else None
    if not any(m["key"] or m["hit"] for m in msgs):
        show = {i for i, m in enumerate(msgs) if not m["noise"]}
        if focus_i is not None:
            show.add(focus_i)
        return sorted(show)
    replies = {}
    for i, m in enumerate(msgs):
        if m["reply_to"] is not None:
            replies.setdefault(m["reply_to"], []).append(i)
    seeds = [i for i, m in enumerate(msgs) if m["key"] or m["hit"]]
    if focus_i is not None:
        show = {focus_i}
        seeds.append(focus_i)
    else:
        show = set()
    for i in seeds:
        m = msgs[i]
        if not m["noise"]:
            show.add(i)
        rt = m["reply_to"]
        if rt is not None and rt in idx and not msgs[idx[rt]]["noise"]:
            show.add(idx[rt])
        for j in replies.get(m["id"], ()):
            if not msgs[j]["noise"]:
                show.add(j)
        if i > 0 and not msgs[i - 1]["noise"]:
            show.add(i - 1)
        if i + 1 < n and not msgs[i + 1]["noise"]:
            show.add(i + 1)
    return sorted(show)


def conv_detail(cid, q=None, focus=None):
    short, conv = cid.split("-", 1)
    t = LONG[short]
    r = db().execute("SELECT * FROM convs WHERE topic=? AND conv=?", (t, conv)).fetchone()
    if not r:
        return None
    ids = json.loads(r["msg_ids"])
    keys = set(json.loads(r["key_msgs"]))
    msgs = []
    if ids:
        sql = f"SELECT * FROM messages WHERE topic=? AND id IN ({','.join('?' * len(ids))}) ORDER BY id"
        for m in db().execute(sql, (t, *ids)):
            media_text = json.loads(m["media_text"] or "[]")
            files = MEDIA_BY_MSG.get((t, m["id"]), [])
            msgs.append({"id": m["id"], "ts": m["ts"], "author": m["author"], "fwd": m["fwd_from"],
                         "reply_to": m["reply_to"], "text": m["text"],
                         "media": json.loads(m["media"] or "[]"), "media_text": media_text,
                         "files": files,
                         "key": m["id"] in keys,
                         "noise": m["id"] not in keys and is_noise(m["text"], media_text, files),
                         "hit": False})
    if q:   # hit: сообщения этого разговора, совпавшие с FTS-запросом (как в search)
        terms = fts_query(q)
        if terms:
            try:
                hits = {row[0] for row in db().execute(
                    "SELECT id FROM fts_msg WHERE fts_msg MATCH ? AND topic=? AND conv=?", (terms, t, conv))}
                for m in msgs:
                    if m["id"] in hits:
                        m["hit"] = True
            except sqlite3.OperationalError:   # fts-таблиц нет
                pass
    try:
        focus = int(focus) if focus not in (None, "") else None
    except (TypeError, ValueError):
        focus = None
    show_ids = {msgs[i]["id"] for i in _compute_show(msgs, focus)}
    for m in msgs:
        m["show"] = m["id"] in show_ids
    title = db().execute("SELECT title FROM topics WHERE slug=?", (t,)).fetchone()[0]
    return {"id": cid, "topic": title, "verdict": r["verdict"], "category": r["category"], "tags": json.loads(r["tags"]),
            "summary": r["summary"], "has_scheme": r["has_scheme"], "scheme_types": json.loads(r["scheme_types"]),
            "scheme_summary": r["scheme_summary"], "start": r["start_ts"], "end": r["end_ts"], "messages": msgs}


DIGEST_SYS = """Ты отвечаешь участнику крипто-комьюнити MXW на вопрос по материалам их Telegram-чата. Тебе даны найденные разговоры со ссылками [[id]] — выжимки и ключевые сообщения (с расшифровками медиа в ⟪…⟫).
Отвечай по-русски, конкретно (названия, цифры, даты, кто сказал), после фактов ставь ссылки [[id]] из материала. Непроверенное помечай «по словам…». Если в материале нет ответа — так и скажи. Без воды. Формат: markdown, 1–6 абзацев или список."""

CHAT_SYS = """Ты отвечаешь участнику крипто-комьюнити MXW на вопрос по материалам их Telegram-чата. Тебе даны найденные разговоры и статьи базы знаний со сквозной нумерацией источников [n] — выжимки и ключевые сообщения (с расшифровками медиа в ⟪…⟫).
Отвечай по-русски, конкретно (названия, цифры, даты, кто сказал), после фактов ставь сноску-номер [n] того источника из материала, откуда факт. Ссылайся только на номера источников, которые есть в материале, — не выдумывай номера и не используй другие форматы ссылок. Непроверенное помечай «по словам…». Если в материале нет ответа — так и скажи. Без воды. Формат: markdown, 1–6 абзацев или список.

Это не разовый вопрос, а диалог: на уточняющие вопросы отвечай с учётом предыдущих вопросов и ответов (не повторяй уже сказанное, разворачивай тему). В конце материала есть блок «ЭКСПЕРТЫ ПО НАЙДЕННЫМ ТЕМАМ» — используй его, когда спрашивают, кто в чём разбирается, кого спросить или кто по делу писал по теме. Если в материале нет ответа — так и скажи и предложи, как переформулировать или уточнить вопрос. Если участники в материале названы «Участник XXX» — обращайся к ним этими псевдонимами, не выдумывай и не раскрывай ники."""

ANON_SYS = "\n\nАнонимный режим: авторов называй только псевдонимами вида \"Участник XXX\". " \
           "Никогда не пиши рядом имя/ник и псевдоним, не раскрывай, кто скрывается за псевдонимом; " \
           "если в тексте материала встречается имя — пересказывай без указания, какой это участник."

PLAN_SYS = """Ты готовишь поиск по базе знаний крипто-чата. Дан диалог (может быть пустым) и последний вопрос пользователя.
1) Перепиши последний вопрос как самостоятельный поисковый запрос: без местоимений и отсылок к диалогу, но с темой и названиями из истории («а кто это пробовал?» → «кто пробовал X»).
2) Подбери 5–12 ключевых слов и близких терминов по теме запроса: названия, тикеры, инструменты, сленг — те, что реально встречаются в крипто-чатах (включая английские названия вроде AdsPower, Dolphin, proxy, ban).
Верни только JSON без пояснений: {"query": "самостоятельный поисковый запрос", "keywords": ["слово", ...]}"""

EXPAND_TIMEOUT = 8.0    # жёсткий таймаут LLM-расширения/переформулировки, с (и для чата — вызов один)
EXPAND_CACHE_MAX = 500  # LRU-кэш планов по нормализованному тексту вопроса
_plan_lock = threading.Lock()
_plan_cache = OrderedDict()           # норм-вопрос → {"query", "keywords"}
_plan_runs = {}                       # норм-вопрос → {"ev", "plan"} — LLM-вызовы в полёте (дедупликация)


def _norm_question(s):
    """Нормализация текста вопроса — ключ кэша расширений: регистр, ё→е, пробелы."""
    return " ".join((s or "").lower().replace("ё", "е").split())


def _plan_worker(norm, user, run, model=None):
    """LLM-вызов плана в отдельном потоке: готовый план кладётся в кэш — даже если его уже
    никто не ждёт (опоздавший результат послужит следующему такому же вопросу).
    model — llm.Provider из ⚙ Настройки сайта (тогда без фолбэка на ключи сервера)."""
    try:
        d = llm.chat_json(model or llm.FAST, PLAN_SYS, user, max_tokens=300, temperature=0, retries=2,
                          fallback=None if model else llm.WRITER)
        query = str(d.get("query") or "").strip()
        keywords = [str(x).strip() for x in (d.get("keywords") or []) if str(x).strip()][:12]
        run["plan"] = {"query": query, "keywords": keywords} if 2 <= len(query) <= 400 and keywords else None
    except Exception:
        run["plan"] = None
    with _plan_lock:
        if run["plan"]:
            _plan_cache[norm] = run["plan"]
            _plan_cache.move_to_end(norm)
            while len(_plan_cache) > EXPAND_CACHE_MAX:
                _plan_cache.popitem(last=False)
        _plan_runs.pop(norm, None)
    run["ev"].set()


def _search_plan(msgs, timeout=EXPAND_TIMEOUT, model=None):
    """Переформулировка + ключевые слова одним коротким вызовом LLM (max_tokens=300,
    temperature=0) — в отдельном потоке, ждём не дольше timeout с. Кэш и дедупликация
    параллельных вызовов — по нормализованному тексту последнего вопроса (LRU
    EXPAND_CACHE_MAX). Не успел / LLM недоступен → {"query": последний вопрос,
    "keywords": []} — поиск по смыслу и словам вопроса; результат LLM, если придёт
    позже, ляжет в кэш для следующего раза."""
    last = msgs[-1]["content"]
    hist = "\n".join(f"{'Вопрос' if m['role'] == 'user' else 'Ответ'}: {m['content'][:600]}" for m in msgs[-6:-1])
    norm = _norm_question(last)
    with _plan_lock:
        plan = _plan_cache.get(norm)
        if plan is not None:
            _plan_cache.move_to_end(norm)
            return plan
        run = _plan_runs.get(norm)
        own = run is None
        if own:
            run = {"ev": threading.Event(), "plan": None}
            _plan_runs[norm] = run
    if own:
        user = (f"Диалог:\n{hist}\n\n" if hist else "") + f"Последний вопрос: {last}"
        threading.Thread(target=_plan_worker, args=(norm, user, run, model), daemon=True,
                         name="search-plan").start()
    run["ev"].wait(timeout)
    if run["plan"]:
        return run["plan"]
    return {"query": last, "keywords": []}


def conv_block(h, d, key_limit=12, ref=None):
    """Блок материала разговора для LLM (общий у digest и чата):
    маркер ref (по умолчанию [[id]]; чат передаёт [n]) дата | ветка / выжимка / схема /
    ключевые сообщения с расшифровками медиа."""
    keym = [m for m in d["messages"] if m["key"]][:key_limit]
    lines = [f"{ref or '[[%s]]' % h['id']} {h['date']} | {d['topic']}", "Выжимка: " + (d["summary"] or "")]
    if d["scheme_summary"]:
        lines.append("Схема: " + d["scheme_summary"])
    for m in keym:
        s = f"#{m['id']} {m['author']}: {(m['text'] or '')[:600]}"
        for mt in m["media_text"]:
            flat = re.sub(r"\s+", " ", mt["text"])[:600]
            s += f" ⟪{mt['kind']}: {flat}⟫"
        lines.append(s)
    return "\n".join(lines)


def digest(q, date_from=None, date_to=None):
    hits = search(q, k=25, date_from=date_from, date_to=date_to)
    blocks = [conv_block(h, conv_detail(h["id"])) for h in hits]
    answer = llm.chat(llm.FAST, DIGEST_SYS, f"Вопрос: {q}\n\nМАТЕРИАЛ:\n\n" + "\n\n".join(blocks),
                      max_tokens=4000, fallback=llm.WRITER)
    valid = {h["id"] for h in hits}
    answer = re.sub(r"\[\[([^\]]+)\]\]", lambda m: m.group(0) if m.group(1) in valid else "", answer)
    return {"answer": answer, "sources": hits}


# --- чат с базой (POST /api/chat) ---------------------------------------------
PIN_CONV_RE = re.compile(r"^(nft|ai|life|crypto|perps|predict|soft|shitki|important)-c[\d-]+$")


def _fnv(s):
    """FNV-1a по UTF-16 кодовым юнитам — в точности как charCodeAt/Math.imul на фронте."""
    h = 0x811C9DC5
    for u in memoryview(s.encode("utf-16-le")).cast("H"):
        h ^= u
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def anon_name(s):
    return "Участник " + format(_fnv(s) & 0xFFF, "03x")


def _anon_sub(text, authors):
    """Имена авторов в материале → «Участник XXX» (тот же хеш, что на фронте).
    Короткие и без букв не трогаем; слово должно стоять отдельно (не внутри более длинного слова)."""
    for a in sorted(authors, key=len, reverse=True):
        if not a or len(a) < 3 or not re.search(r"\w", a):
            continue
        text = re.sub(r"(?<!\w)" + re.escape(a) + r"(?!\w)", lambda _m, a=a: anon_name(a), text)
    return text


# --- карточки источников для /api/chat (формат meta) --------------------------
CAT_RU = {"nft": "NFT", "ai_dev": "AI и разработка", "tools": "Инструменты", "security": "Безопасность",
          "trading": "Трейдинг", "drops_testnets": "Дропы и тестнеты", "market_news": "Новости рынка",
          "predictions": "Предикшены", "jobs_income": "Работа", "community": "Комьюнити", "other": "Разное"}

_topic_titles = {}


def topic_titles():
    """slug ветки → человекочитаемое название (кэш)."""
    if not _topic_titles:
        for r in db().execute("SELECT slug, title FROM topics"):
            _topic_titles[r["slug"]] = r["title"]
    return _topic_titles


def _title_from_summary(summary):
    """Заголовок разговора для карточки: первое предложение выжимки, ~90 символов, обрез по слову
    (тот же алгоритм, что convTitle на фронте)."""
    s = re.sub(r"\s+", " ", summary or "").strip()
    if not s:
        return "Без выжимки"
    t = s
    # конец предложения: [.!?] + пробел/конец, но не точка в дате/числе («26.06») и не инициал («С. Rollins»)
    for m in re.finditer(r"[.!?](?=\s|$)", s):
        word = re.search(r"(\S+)$", s[:m.start()])
        if word and (len(word.group(1)) <= 1 or word.group(1)[-1].isdigit()):
            continue
        t = s[:m.start()].strip() or s
        break
    if len(t) > 90:
        t = t[:90]
        sp = t.rfind(" ")
        if sp > 50:
            t = t[:sp]
        t = t.strip() + "…"
    return t


def _snippet(text, limit=200):
    """Однострочный сниппет для карточки источника."""
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _read_md(f):
    """Текст статьи .md с нормализацией переводов строк: vault в Windows-формате (CRLF),
    а разбор frontmatter и ридер на клиенте считают строки по \n."""
    return f.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _parse_fm(md):
    """Простой разбор YAML-frontmatter статьи (плоские ключи)."""
    md = md.replace("\r\n", "\n").replace("\r", "\n")   # CRLF vault — тоже нормализуем
    fm = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", md, flags=re.S)
    if m:
        for line in m.group(1).split("\n"):
            i = line.find(":")
            if i > 0:
                fm[line[:i].strip()] = line[i + 1:].strip()
    return fm


def _art_meta(rel):
    """(категория, название ветки, дата начала периода) статьи из frontmatter — для карточки."""
    f = VAULT / rel
    if not f.is_file():
        return "", "", ""
    fm = _parse_fm(_read_md(f))
    titles = {SHORT[slug]: t for slug, t in topic_titles().items()}
    branches = [b.strip() for b in (fm.get("ветки") or "").strip("[]").split(",") if b.strip()]
    m = re.search(r"\d{4}-\d{2}-\d{2}", fm.get("период") or "")
    return (fm.get("категория") or "", titles.get(branches[0], branches[0]) if branches else "",
            m.group(0) if m else "")


_experts_profiles = None


def _expert_profiles():
    """author → profile из таблицы experts (кэш)."""
    global _experts_profiles
    if _experts_profiles is None:
        _experts_profiles = {r["author"]: r["profile"] for r in db().execute("SELECT author, profile FROM experts")}
    return _experts_profiles


def _experts_block(cats, schemes, is_anon, max_topics=6, per_topic=3, total=9):
    """Блок «Эксперты по найденным темам» для материала: по категориям и типам схем найденных
    разговоров — топ-3 из expert_topics (kind category/scheme) с профилем из experts.profile и числом
    ключевых сообщений. При anon имена → псевдонимы (тот же хеш, что на фронте)."""
    if not db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='expert_topics'").fetchone():
        return ""
    topics = ([("category", c) for c, _ in sorted(cats.items(), key=lambda kv: -kv[1])]
              + [("scheme", s) for s in sorted(schemes)])[:max_topics]
    if not topics:
        return ""
    profiles = _expert_profiles()
    lines, used = [], Counter()
    for kind, code in topics:
        rows = db().execute("SELECT author, score, n_key, n_convs FROM expert_topics "
                            "WHERE kind=? AND topic=? ORDER BY score DESC LIMIT ?",
                            (kind, code, per_topic)).fetchall()
        label = CAT_RU.get(code, code) if kind == "category" else SCHEME_RU.get(code, code)
        for r in rows:
            if used[r["author"]] >= 2:   # один человек — максимум в двух темах
                continue
            used[r["author"]] += 1
            name = anon_name(r["author"]) if is_anon else r["author"]
            prof = (profiles.get(r["author"]) or "").strip()
            if len(prof) > 300:
                prof = prof[:300].rsplit(" ", 1)[0] + "…"
            lines.append(f"- {label}: {name} ({r['n_key']} ключевых сообщений) — {prof}")
        if len(lines) >= total:
            break
    if not lines:
        return ""
    return ("ЭКСПЕРТЫ ПО НАЙДЕННЫМ ТЕМАМ (кто в чате в этом разбирается — используй, когда спрашивают, "
            "кого спросить или кто по делу писал по теме):\n" + "\n".join(lines[:total]))


def _art_hits(query, keywords=None, k=3):
    """Топ-k кусков статей: fts_art (bm25, термы из keywords либо из слов query) + emb_art
    (косинус локальных эмбеддингов по query), слияние через RRF."""
    terms = fts_query_kw(keywords) if keywords else fts_query(query)
    fts = []   # [{title, path, chunk}] по bm25
    if terms:
        try:
            rows = db().execute(f"SELECT body, path, title, bm25(fts_art) AS r FROM fts_art "
                                 f"WHERE fts_art MATCH ? ORDER BY r LIMIT 10", (terms,)).fetchall()
            fts = [{"title": r["title"], "path": r["path"], "chunk": r["body"]} for r in rows]
        except sqlite3.OperationalError:   # fts_art нет — build_fts.py не запускался
            fts = []
    sem = []   # [{title, path, chunk}] по косинусу
    try:
        keys, m = emb_art_matrix()
        if keys:
            v = embed_local.embed_query(query)
            sims = m @ (v / (np.linalg.norm(v) + 1e-9))
            for i in np.argsort(-sims)[:10]:
                path, chunk = keys[i]
                sem.append({"title": Path(path).stem, "path": path, "chunk": chunk})
    except Exception:   # нет модели/таблицы — только FTS
        sem = []
    if not fts and not sem:
        return []
    scores = {}   # RRF по двум спискам
    for lst in (fts, sem):
        for rank, h in enumerate(lst, 1):
            key = (h["path"], h["chunk"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    by_key = {}
    for h in fts + sem:
        by_key[(h["path"], h["chunk"])] = h
    return [by_key[key] for key, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def _pin_parts(pin, n=0):
    """Контекст по кнопке «спросить про это»: разговор (все не-noise сообщения, до 60)
    или статья (до 12000 символов). Возвращает (блок материала с маркером [n], карточку
    источника без поля n — или (None, None))."""
    if not pin or not isinstance(pin, str):
        return None, None
    mark = f"[{n}]"
    if PIN_CONV_RE.match(pin):
        d = conv_detail(pin)
        if not d:
            return None, None
        lines = [f"{mark} {d['start'][:10]} | {d['topic']} — разговор целиком (контекст диалога)",
                 "Выжимка: " + (d["summary"] or "")]
        if d["scheme_summary"]:
            lines.append("Схема: " + d["scheme_summary"])
        for m in [m for m in d["messages"] if not m["noise"]][:60]:
            s = f"#{m['id']} {m['author']}: {(m['text'] or '')[:600]}"
            for mt in m["media_text"]:
                flat = re.sub(r"\s+", " ", mt["text"])[:600]
                s += f" ⟪{mt['kind']}: {flat}⟫"
            lines.append(s)
        card = {"kind": "conv", "id": pin, "title": _title_from_summary(d["summary"]),
                "date": (d["start"] or "")[:10], "branch": d["topic"], "category": d["category"],
                "snippet": _snippet(d["summary"])}
        return "\n".join(lines), card
    f = (VAULT / pin).resolve()
    if f.suffix != ".md" or VAULT.resolve() not in f.parents or not f.is_file():
        return None, None
    rel = f.relative_to(VAULT).as_posix()
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", _read_md(f), flags=re.S)[:12000]
    cat, branch, dt = _art_meta(rel)
    card = {"kind": "article", "path": rel, "title": f.stem, "date": dt, "branch": branch,
            "category": cat, "snippet": _snippet(body)}
    return f"{mark} статья базы знаний (контекст диалога):\n{body}", card


def chat_context(msgs, date_from=None, date_to=None, branch=None, schemes=False, pin=None, is_anon=False,
                 plan_model=None):
    """Сборка контекста для /api/chat: план поиска (переформулировка + ключевые слова — один LLM-вызов),
    поиск (18 разговоров + 3 куска статей + pin), общий материал со сквозной нумерацией источников [n].
    Возвращает (query, cards, articles, material): cards — источники для meta в формате карточек
    {n, kind:"conv"|"article", id|path, title, date, branch, category, snippet}; articles — прежний
    формат {title, path} (для /old-фронта)."""
    plan = _search_plan(msgs, model=plan_model)
    query = plan["query"]
    hits = search(query, k=18, date_from=date_from, date_to=date_to, branch=branch, schemes=schemes,
                  keywords=plan["keywords"])
    blocks, cards, authors = [], [], set()
    cats, schemes_found = {}, set()

    n = 0
    pin_block, pin_card = _pin_parts(pin, 1)
    if pin_card:
        n = 1
        blocks.append(pin_block)
        pin_card["n"] = 1
        cards.append(pin_card)
        if pin_card.get("category"):
            cats[pin_card["category"]] = cats.get(pin_card["category"], 0) + 1

    for h in hits:
        d = conv_detail(h["id"])
        if not d:
            continue
        cats[d["category"]] = cats.get(d["category"], 0) + 1
        if d["has_scheme"]:
            schemes_found.update(d["scheme_types"] or [])
        authors.update(m["author"] for m in d["messages"] if m["author"])
        if pin_card and pin_card.get("id") == h["id"]:
            continue   # блок уже добавлен pin'ом (карточка n=1)
        n += 1
        blocks.append(conv_block(h, d, ref=f"[{n}]"))
        summary = h["summary"] or d["summary"]
        snip = (h["hits"] or [{}])[0].get("snippet") or _snippet(summary)
        cards.append({"n": n, "kind": "conv", "id": h["id"], "title": _title_from_summary(summary),
                      "date": h["date"], "branch": d["topic"], "category": d["category"],
                      "snippet": _snippet(snip, 220)})

    articles = []
    for a in _art_hits(query, plan["keywords"], 3):
        if pin_card and pin_card.get("path") == a["path"]:
            continue   # уже в материале pin'ом
        n += 1
        blocks.append(f"[{n}] статья базы знаний:\n{a['chunk']}")
        cat, branch_t, dt = _art_meta(a["path"])
        cards.append({"n": n, "kind": "article", "path": a["path"], "title": a["title"],
                      "date": dt, "branch": branch_t, "category": cat, "snippet": _snippet(a["chunk"])})
        articles.append({"title": a["title"], "path": a["path"]})
    if pin_card and "path" in pin_card:   # pin — статья: первой в списке статей
        articles.insert(0, {"title": pin_card["title"], "path": pin_card["path"]})

    material = "\n\n".join(b for b in blocks if b)
    exp = _experts_block(cats, schemes_found, is_anon)
    if exp:
        material += "\n\n" + exp
    if is_anon:
        material = _anon_sub(material, authors)
        for c in cards:   # в карточках источников — псевдонимы
            c["title"] = _anon_sub(c["title"], authors)
            c["snippet"] = _anon_sub(c["snippet"], authors)
    return query, cards, articles, material


def chat_history(msgs, material):
    """History для chat_stream: последние 8 сообщений, материал приклеен к последнему user-вопросу."""
    hist = [dict(m) for m in msgs[-8:]]
    hist[-1]["content"] = hist[-1]["content"] + "\n\nМАТЕРИАЛ:\n\n" + material
    return hist


def graph():
    def has_table(name):
        return db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()

    nodes = []
    lay = {}
    if has_table("layout"):   # semantic mode coordinates (absent until layout.py runs)
        for r in db().execute("SELECT topic, conv, x, y, k FROM layout"):
            lay[(r["topic"], r["conv"])] = (r["x"], r["y"], r["k"])
    for r in db().execute("SELECT topic, conv, verdict, category, start_ts, n_msgs, summary, has_scheme FROM convs"):
        L = lay.get((r["topic"], r["conv"]))
        nodes.append({"id": f"{SHORT[r['topic']]}-{r['conv']}", "b": SHORT[r["topic"]], "v": r["verdict"],
                      "c": r["category"], "t": (r["start_ts"] or "")[:16], "n": r["n_msgs"],
                      "s": (r["summary"] or "")[:140], "sc": r["has_scheme"],
                      "ux": L[0] if L else None, "uy": L[1] if L else None, "k": L[2] if L else None})
    topics = [{"short": SHORT[r["slug"]], "title": r["title"]} for r in db().execute("SELECT slug, title FROM topics")]
    clusters = []
    if has_table("clusters"):
        clusters = [{"k": r["k"], "name": r["name"], "descr": r["descr"], "n": r["n"], "x": r["x"], "y": r["y"]}
                    for r in db().execute("SELECT k, name, descr, n, x, y FROM clusters ORDER BY n DESC")]
    hist = Counter()
    for (d,) in db().execute("SELECT substr(ts,1,10) FROM messages WHERE is_bot=0"):
        hist[d] += 1
    authors = [a for (a,) in db().execute(
        "SELECT DISTINCT author FROM messages WHERE author IS NOT NULL AND author != '' ORDER BY author")]
    return {"nodes": nodes, "topics": topics, "hist": sorted(hist.items()), "clusters": clusters, "authors": authors}


_articles_cache = None


def _art_fc(fm):
    """Поля проверки фактов из frontmatter (после factcheck_apply.py) →
    (дата «проверено» ISO или None, «подтверждено» в % или None, «исправлено» n или None).
    /api/articles отдаёт их ридеру (плашка в шапке) и списку статей (отметка «✓ 90%»)."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", fm.get("проверено") or "")

    def num(key):
        try:
            return int(re.sub(r"\D+", "", fm.get(key) or ""))
        except ValueError:
            return None

    return (m.group(0) if m else None, num("подтверждено"), num("исправлено"))


def articles():
    """Статьи базы знаний: путь, раздел (папка), серия (подпапка 2-го уровня — для схем),
    название, период и число источников из frontmatter; поля проверки фактов —
    checked/confirmed/fixed (None, если статья не проверялась). Кэш в памяти — файлы статичны."""
    global _articles_cache
    if _articles_cache is not None:
        return _articles_cache
    out = []
    for f in sorted(VAULT.rglob("*.md")):
        rel = f.relative_to(VAULT)
        if rel.parts[0] == "Источники":
            continue
        fm = _parse_fm(_read_md(f))
        try:
            nsrc = int(fm.get("источников") or 0)
        except ValueError:
            nsrc = 0
        checked, confirmed, fixed = _art_fc(fm)
        out.append({"path": str(rel).replace("\\", "/"), "section": rel.parts[0] if len(rel.parts) > 1 else "",
                    "series": rel.parts[1] if len(rel.parts) > 2 else "", "title": f.stem,
                    "period": fm.get("период") or "", "sources": nsrc,
                    "checked": checked, "confirmed": confirmed, "fixed": fixed})
    _articles_cache = out
    return out


# --- доска активностей (таблица events из extract_events.py) -------------------
EV_ORDER = {"active": 0, "soon": 1, "future": 2, "past": 3, "undated": 4}


def _ev_date(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def events(type_=None, q=None, chain=None):
    """События с датами из чата + статус относительно сегодня. Данные — из Telegram-экспорта:
    после last_msg (дата последнего сообщения в базе) новых событий нет.
    Статус: если date нет, но есть end_date — active, пока end_date >= сегодня (иначе past,
    сортировка по end_date). date в прошлом, а end_date дальше 120 дней от date — почти наверняка
    ошибка извлечения: статус по date как без end_date (past), end_date не показываем."""
    today = date.today()
    last_msg = db().execute("SELECT max(ts) FROM messages").fetchone()[0]
    out = []
    has_events = db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
    if has_events:
        for r in db().execute("SELECT * FROM events"):
            e = dict(r)
            e["sources"] = json.loads(e["sources"] or "[]")
            d, ed = _ev_date(e["date"]), _ev_date(e["end_date"])
            if d is None and ed is None:
                e["status"] = "undated"
            elif d is None:
                e["status"] = "active" if ed >= today else "past"
            elif ed is not None and d < today and (ed - d).days > 120:
                e["status"] = "past"   # считаем как без end_date
                e["end_date"] = None   # и в UI end_date не показываем
            elif d <= today <= (ed or d):
                e["status"] = "active"
            elif d > today:
                e["status"] = "soon" if (d - today).days <= 14 else "future"
            else:
                e["status"] = "past"
            if type_ and e["type"] != type_:
                continue
            if chain and (e["chain"] or "").lower() != chain.lower():
                continue
            if q and q.lower() not in f"{e['project']} {e['title']} {e['details'] or ''}".lower():
                continue
            out.append(e)
    groups = [[] for _ in EV_ORDER]
    for e in out:
        groups[EV_ORDER[e["status"]]].append(e)
    for i in (0, 1, 2):   # active, soon, future — по возрастанию даты (нет date — по end_date)
        groups[i].sort(key=lambda e: (e["date"] or e["end_date"] or "9999-99-99", (e["project"] or "").lower()))
    groups[3].sort(key=lambda e: (e["date"] or e["end_date"] or "", (e["project"] or "").lower()), reverse=True)   # past — по убыванию
    groups[4].sort(key=lambda e: (e["first_seen"] or "", (e["project"] or "").lower()), reverse=True)   # без даты — свежие сверху
    return {"today": today.isoformat(), "last_msg": last_msg, "events": sum(groups, [])}


# --- «кто в чём разбирается» (experts.py → /api/experts) ------------------------
SCHEME_RU = {"multiaccount": "Мультиаккаунты", "antisybil": "Антисибил", "farms": "Фермы", "accounts": "Аккаунты",
             "bots_soft": "Боты и софт", "kyc_verification": "KYC", "proxy_antidetect": "Прокси и антидетект",
             "bans_risks": "Баны и риски"}

_experts_cache = None


def _topic_label(kind, topic):
    """Человекочитаемое имя темы: типы схем — по-русски; категории — кодом (фронт переведёт по CAT)."""
    return SCHEME_RU.get(topic, topic) if kind == "scheme" else topic


def experts():
    """Люди и темы экспертизы из таблиц experts/expert_topics (заполняет experts.py).
    Кластеры не отдаются — раздел «Люди» (задача 021) использует только категории (коды;
    фронт переведёт по CAT) и типы схем (по-русски). people[].topics — все темы человека
    с score/n_key/n_convs и примерами-разговорами (карточка, сортировка по чипу темы);
    people[].top — те же темы, топ-6 (прежний фронт на /old). topics — темы для чипов:
    n_people и топ-6 людей. Кэш в памяти — данные статичны."""
    global _experts_cache
    if _experts_cache is not None:
        return _experts_cache
    if not db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='experts'").fetchone():
        raise ValueError("таблиц experts нет — запусти experts.py")
    people = []
    for r in db().execute("SELECT author, n_msgs, n_key, n_convs, first_ts, last_ts, profile, skills FROM experts"):
        tops = db().execute("SELECT kind, topic, score, n_key, n_convs, examples FROM expert_topics "
                            "WHERE author=? AND kind!='cluster' ORDER BY score DESC, kind, topic",
                            (r["author"],)).fetchall()
        topics = [{"kind": t["kind"], "topic": _topic_label(t["kind"], t["topic"]), "score": t["score"],
                   "n_key": t["n_key"], "n_convs": t["n_convs"], "examples": json.loads(t["examples"] or "[]")}
                  for t in tops]
        people.append({"author": r["author"], "n_msgs": r["n_msgs"], "n_key": r["n_key"], "n_convs": r["n_convs"],
                       "first_ts": r["first_ts"], "last_ts": r["last_ts"], "profile": r["profile"],
                       "skills": json.loads(r["skills"] or "[]"),
                       "topics": topics, "top": topics[:6]})
    people.sort(key=lambda p: (-p["n_key"], -p["n_msgs"], p["author"]))
    groups = OrderedDict()
    for r in db().execute("SELECT kind, topic, author, score, n_key, n_convs, examples "
                          "FROM expert_topics WHERE kind!='cluster'"):
        groups.setdefault((r["kind"], _topic_label(r["kind"], r["topic"])), []).append(r)
    topics = []
    for (kind, topic), rs in groups.items():
        rs.sort(key=lambda r: (-r["score"], r["author"]))
        topics.append({"kind": kind, "topic": topic, "n_people": len(rs),
                       "top": [{"author": r["author"], "score": r["score"], "n_key": r["n_key"],
                                "n_convs": r["n_convs"], "examples": json.loads(r["examples"] or "[]")}
                               for r in rs[:6]]})
    topics.sort(key=lambda t: (-t["n_people"], t["kind"], t["topic"]))
    _experts_cache = {"people": people, "topics": topics}
    return _experts_cache


# --- «темы во времени» (GET /api/trends?by=cluster|category) --------------------
_trends_cache = {}


def _top_tags():
    """Топ-теги разговоров по месяцам (полезные разговоры): «2026-08» → [{tag, n}] топ-10
    по числу разговоров с этим тегом (месяц — по дате начала разговора)."""
    by_month = {}
    for r in db().execute("SELECT tags, start_ts FROM convs WHERE verdict!='drop' AND tags!='[]'"):
        mon = (r["start_ts"] or "")[:7]
        if len(mon) != 7:
            continue
        for t in json.loads(r["tags"]):
            cnt = by_month.setdefault(mon, {})
            cnt[t] = cnt.get(t, 0) + 1
    return {mon: [{"tag": t, "n": n} for t, n in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
            for mon, cnt in by_month.items()}


def trends(by="cluster"):
    """Недельная активность тем: by=cluster — смысловые кластеры (layout.k → clusters.name),
    by=category — категории разговоров. Значение серии за неделю — число не-бот сообщений
    в полезных разговорах этой темы (сообщение относится к разговору через convs.msg_ids).
    Недели — понедельники (ISO) от недели первого до недели последнего сообщения.
    Серии отсортированы по total по убыванию. Для by=category дополнительно:
    convs — разговоры по темам и неделям (для списка в drawer: тема → неделя → id),
    top_tags — топ-теги разговоров по месяцам (для «Главные проекты месяца»).
    Кэш в памяти — данные статичны."""
    if by not in ("cluster", "category"):
        raise ValueError("by=cluster|category")
    if by in _trends_cache:
        return _trends_cache[by]

    def has_table(name):
        return db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()

    d0, d1 = db().execute("SELECT substr(min(ts),1,10), substr(max(ts),1,10) FROM messages").fetchone()
    if not d0 or not d1:
        _trends_cache[by] = {"weeks": [], "series": []}
        return _trends_cache[by]
    w0 = date.fromisoformat(d0)
    w1 = date.fromisoformat(d1)
    w0 -= timedelta(days=w0.weekday())   # понедельник недели первого сообщения
    w1 -= timedelta(days=w1.weekday())   # понедельник недели последнего сообщения
    weeks, w = [], w0
    while w <= w1:
        weeks.append(w.isoformat())
        w += timedelta(days=7)
    widx = {s: i for i, s in enumerate(weeks)}

    # тема разговора: кластер k из layout или категория
    names = {}
    if by == "cluster":
        if not has_table("layout"):   # layout.py не запускался — пусто, сервер не падает
            _trends_cache[by] = {"weeks": weeks, "series": []}
            return _trends_cache[by]
        tkey = {(r["topic"], r["conv"]): r["k"] for r in db().execute("SELECT topic, conv, k FROM layout")}
        names = {r["k"]: r["name"] for r in db().execute("SELECT k, name FROM clusters")}
    else:   # категории — коды, фронт переведёт их в названия по CAT
        tkey = None

    # не-бот сообщения → индекс недели (даты немного, кэшируем перевод даты в неделю)
    msgs, dmap = {}, {}
    for r in db().execute("SELECT topic, id, substr(ts,1,10) AS d FROM messages WHERE is_bot=0"):
        if r["d"] in dmap:
            i = dmap[r["d"]]
        else:
            try:
                dd = date.fromisoformat(r["d"])
                i = widx.get((dd - timedelta(days=dd.weekday())).isoformat())
            except ValueError:
                i = None
            dmap[r["d"]] = i
        if i is not None:
            msgs.setdefault(r["topic"], {})[r["id"]] = i

    counts = {}   # key темы → значения по неделям
    for r in db().execute("SELECT topic, conv, msg_ids, category FROM convs WHERE verdict!='drop'"):
        key = tkey.get((r["topic"], r["conv"])) if by == "cluster" else r["category"]
        if key is None:
            continue
        row = counts.setdefault(key, [0] * len(weeks))
        m = msgs.get(r["topic"]) or {}
        for mid in json.loads(r["msg_ids"]):
            i = m.get(mid)
            if i is not None:
                row[i] += 1
    series = [{"key": k, "name": names.get(k, k), "values": v, "total": sum(v)} for k, v in counts.items()]
    series.sort(key=lambda s: (-s["total"], str(s["key"])))
    out = {"weeks": weeks, "series": series}
    if by == "category":
        # разговоры темы за неделю (id короткие): тема → неделя → [id] — для drawer по клику
        cw = {}
        for r in db().execute("SELECT topic, conv, msg_ids, category FROM convs WHERE verdict!='drop'"):
            m = msgs.get(r["topic"]) or {}
            wseen = set()
            for mid in json.loads(r["msg_ids"]):
                i = m.get(mid)
                if i is not None:
                    wseen.add(i)
            for i in wseen:
                cw.setdefault(r["category"], {}).setdefault(weeks[i], []).append(f"{SHORT[r['topic']]}-{r['conv']}")
        out["convs"] = cw
        out["top_tags"] = _top_tags()
    _trends_cache[by] = out
    return out


def user_llm(cfg):
    """{base, key, model, fast} из ⚙ Настройки сайта → (модель ответа, модель плана) как llm.Provider;
    нет настроек → (None, None): чат идёт через провайдеров сервера (.llm_env)."""
    if not isinstance(cfg, dict):
        return None, None
    base = str(cfg.get("base") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not model or not re.match(r"^https?://[^\s/]+", base):
        return None, None
    key = str(cfg.get("key") or "")
    fast = str(cfg.get("fast") or "").strip() or model
    return llm.Provider(model, base, key), llm.Provider(fast, base, key)


def llm_models(body):
    """POST /api/llm/models {base, key} → {"models": [...]} — список моделей провайдера (прокси:
    браузер не упирается в CORS провайдера)."""
    base = str(body.get("base") or "").strip()
    if not re.match(r"^https?://[^\s/]+", base):
        raise ValueError("адрес API должен начинаться с http:// или https://")
    try:
        return {"models": llm.list_models(base, str(body.get("key") or ""))}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")


def llm_test(body):
    """POST /api/llm/test {base, key, model} → {"ok", "reply", "ms"} — один короткий запрос к модели."""
    m, _ = user_llm(body)
    if not m:
        raise ValueError("нужны адрес API и модель")
    t = time.time()
    try:
        text, _u, _f = llm._stream(m, "Отвечай очень коротко.", "Ответь одним словом: работает?", 300, 0)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
    return {"ok": True, "reply": (text or "").strip()[:200], "ms": int((time.time() - t) * 1000)}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # keep-alive: много миниатюр по одному соединению

    def log_message(self, *a):
        pass

    def send(self, obj, ctype="application/json; charset=utf-8", code=200, head=False):
        data = obj if isinstance(obj, bytes) else (obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")   # фронт часто меняется: браузер не должен держать старые js
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def send_media(self, i, thumb, head):
        """Файл из индекса медиа по номеру строки: только пути из индекса (проверка, что
        resolved-путь лежит внутри MEDIA_ROOT), Content-Type по расширению, HTTP Range,
        потоковая отдача кусками, Cache-Control; для kind=files — attachment."""
        if not isinstance(i, int) or not 0 <= i < len(MEDIA_FILES):
            self.send({"error": "not found"}, code=404, head=head)
            return
        e = MEDIA_FILES[i]
        p = e["thumb"] if thumb and e["thumb"] else e["path"]
        rp = p.resolve()
        root = MEDIA_ROOT.resolve()
        if root != rp and root not in rp.parents:
            self.send({"error": "forbidden"}, code=404, head=head)
            return
        try:
            size = rp.stat().st_size
            f = open(rp, "rb")
        except OSError:
            self.send({"error": "not found"}, code=404, head=head)
            return
        start, length, code = 0, size, 200
        rng = self.headers.get("Range")
        if rng:   # поддерживаем bytes=a-b, bytes=a-, bytes=-n; прочее игнорируем → 200 целиком
            m2 = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if m2 and (m2.group(1) or m2.group(2)):
                if m2.group(1) == "":                     # последние N байт
                    start = max(0, size - int(m2.group(2)))
                    length = size - start
                else:
                    start = int(m2.group(1))
                    end = min(int(m2.group(2)) if m2.group(2) else size - 1, size - 1)
                    if start > end or start >= size:      # диапазон вне файла
                        f.close()
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    length = end - start + 1
                code = 206
        try:
            self.send_response(code)
            self.send_header("Content-Type", MIME_FIX.get(rp.suffix.lower()) or guess_type(rp.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "max-age=86400")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{start + length - 1}/{size}")
            if e["kind"] == "files":
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(e["name"]))
            self.end_headers()
            if head:
                return
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(MEDIA_CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        except (ConnectionResetError, BrokenPipeError):
            pass   # браузер оборвал соединение (перемотка) — молча
        finally:
            f.close()

    def do_GET(self):
        self._get(False)

    def do_HEAD(self):
        self._get(True)

    def _send_file(self, f, head):
        """Статический файл с Content-Type по расширению (для / и /web/...)."""
        self.send(f.read_bytes(), CTYPES.get(f.suffix.lower()) or guess_type(f.name)[0] or "application/octet-stream", head=head)

    def _web_static(self, rel, head):
        """/web/<rel> → файл из app/web; resolved-путь обязан лежать внутри app/web (защита от ..)."""
        if not rel or rel.endswith("/"):
            self.send({"error": "not found"}, code=404, head=head)
            return
        f = (WEB / rel).resolve()
        root = WEB.resolve()
        if root != f and root not in f.parents or not f.is_file():
            self.send({"error": "not found"}, code=404, head=head)
            return
        self._send_file(f, head)

    def _get(self, head):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                self._send_file(STATIC, head)
            elif u.path == "/old":
                self._send_file(OLD, head)
            elif u.path.startswith("/web/"):
                self._web_static(u.path[len("/web/"):], head)
            elif u.path == "/api/graph":
                self.send(graph())
            elif u.path == "/api/media":
                try:
                    i = int(q.get("i", ""))
                except ValueError:
                    i = -1
                self.send_media(i, q.get("thumb") == "1", head)
            elif u.path == "/api/conv":
                d = conv_detail(q["id"], q.get("q"), q.get("focus"))
                self.send(d if d else {"error": "not found"}, code=200 if d else 404)
            elif u.path == "/api/search":
                qq = q["q"]
                kw = None
                if q.get("expand") == "1":   # LLM-расширение: ≤EXPAND_TIMEOUT с; не успел — без keywords
                    plan = _search_plan([{"role": "user", "content": qq}])
                    if plan["query"]:
                        qq = plan["query"]
                    kw = plan["keywords"] or None
                res, sem = hybrid_search(qq, 30, q.get("from"), q.get("to"), branch=q.get("branch"),
                                         category=q.get("category"), schemes=q.get("schemes") == "1",
                                         drop=q.get("drop") == "1", keywords=kw)
                self.send({"semantic": sem, "results": res})
            elif u.path == "/api/articles":
                self.send(articles())
            elif u.path == "/api/events":
                self.send(events(q.get("type"), q.get("q"), q.get("chain")))
            elif u.path == "/api/experts":
                self.send(experts())
            elif u.path == "/api/trends":
                self.send(trends(q.get("by") or "cluster"))
            elif u.path == "/api/article":
                f = (VAULT / q["path"]).resolve()
                if VAULT.resolve() not in f.parents:
                    raise ValueError("bad path")
                self.send(_read_md(f), "text/plain; charset=utf-8")
            elif u.path == "/api/find_article":
                name = q["name"]
                hit = next((a for a in articles() if a["title"] == name), None)
                self.send(hit or {"error": "not found"}, code=200 if hit else 404)
            else:
                self.send({"error": "not found"}, code=404)
        except Exception as e:
            self.send({"error": str(e)[:300]}, code=500)

    def do_POST(self):
        u = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if u.path == "/api/digest":
                self.send(digest(body["q"], body.get("from"), body.get("to")))
            elif u.path == "/api/chat":
                self.chat_sse(body)
            elif u.path == "/api/llm/models":
                self.send(llm_models(body))
            elif u.path == "/api/llm/test":
                self.send(llm_test(body))
            else:
                self.send({"error": "not found"}, code=404)
        except Exception as e:
            self.send({"error": str(e)[:300]}, code=500)

    def _sse(self, name, obj):
        """Одно SSE-событие в сокет, с flush."""
        self.wfile.write(f"event: {name}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def chat_sse(self, body):
        """POST /api/chat → text/event-stream: meta (query, sources-карточки, articles) →
        status («провайдер занят, повторяю (n/5)…») → delta… → done | error.
        Источники приходят в meta уже в формате карточек {n, kind, id|path, title, date, branch,
        category, snippet}. Если LLM недоступен (после всех повторов и фолбэка) — error с текстом
        «ИИ сейчас недоступен — вот что нашлось в базе»: карточки у клиента уже есть.
        При обрыве клиентом генерация тихо прекращается (генератор закрывается в finally)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        meta_sent = produced = False
        try:
            msgs = [{"role": m.get("role") or "user", "content": (m.get("content") or "").strip()}
                    for m in (body.get("messages") or []) if (m.get("content") or "").strip()]
            if not msgs or msgs[-1]["role"] != "user":
                raise ValueError("нет вопроса")
            anon = bool(body.get("anon"))
            own, own_fast = user_llm(body.get("llm"))   # провайдер из ⚙ Настройки сайта, если задан
            query, cards, articles, material = chat_context(
                msgs, date_from=body.get("from") or None, date_to=body.get("to") or None,
                branch=body.get("branch") or None, schemes=bool(body.get("schemes")),
                pin=body.get("pin") or None, is_anon=anon, plan_model=own_fast)
            self._sse("meta", {"query": query, "sources": cards, "articles": articles})
            meta_sent = True
            stream = llm.chat_stream(own or llm.FAST, CHAT_SYS + (ANON_SYS if anon else ""),
                                     chat_history(msgs, material), fallback=None if own else llm.WRITER,
                                     on_status=lambda n, total: self._sse(
                                         "status", {"text": f"провайдер занят, повторяю ({n}/{total})…"}))
            try:
                for piece in stream:   # повторы/фолбэк — внутри llm.chat_stream (до первого куска)
                    produced = True
                    self._sse("delta", {"text": piece})
            finally:
                stream.close()   # недобранный генератор: usage допишется в llm.chat_stream
            if not produced:
                raise RuntimeError("LLM вернул пустой ответ")
            self._sse("done", {})
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass   # клиент оборвал соединение — тихо прекращаем
        except Exception as e:
            try:
                msg = str(e)[:300]
                if meta_sent and not produced:   # LLM не ответил — источники у клиента уже есть
                    msg = "ИИ сейчас недоступен — вот что нашлось в базе"
                    if own or not llm.BASE:      # свой провайдер / нет .llm_env — покажем причину
                        msg += f" ({str(e)[:200]})"
                self._sse("error", {"error": msg})
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
                pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"MXW map: http://127.0.0.1:{port}")
    threading.Thread(target=embed_local.embed_query, args=("прогрев",), daemon=True,
                     name="emb-local-warmup").start()   # прогрев локальной модели (загрузка ONNX ~2 с) — первый поиск не платит за холодный старт
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
