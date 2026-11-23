"""Применение результатов фактчекинга к статьям хранилища (задача 033).

Читает data/factcheck/<статья>.json (результаты python factcheck.py, сам прогон не трогает)
и правит статьи vault/ по вердиктам:
  1) wrong_citation — в предложении с утверждением неверная ссылка [[старая]]
     заменяется на [[correct_source]]. Если заметки-источника нет (разговор в drop —
     файлы vault/Источники/ для них не пишет build_vault.py sources), она создаётся
     из базы через build_vault.write_source_note (задача 035; в --dry — только
     сообщение в лог); разговора нет в базе — ссылка всё равно ставится, в лог
     пишется предупреждение. Если в предложении заменить однозначно нельзя —
     правильная ссылка добавляется рядом, старая остаётся. Предложения с такой
     правкой не получают сноску ⚠️ от дублей тех же утверждений (unsupported).
  2) contradicted (подтверждённый вторым мнением) и partial, где в problem речь о
     неверной цифре/дате/авторе, — предложение переписывается по fix/цитате одним
     вызовом Qwen на раздел (llm.QWEN, fallback llm.QWEN_ALT; официальный DeepSeek
     не используется). Модель получает текст раздела и список проблемных утверждений
     и возвращает раздел целиком. Проверка результата: все [[...]] ссылки исходного
     раздела сохранены (кроме заменённых по п.1), длина не меньше 70% исходной,
     заголовок раздела и сноски ⚠️ на месте — иначе правка отбрасывается, утверждения
     помечаются как в п.3. После переписывания изменённые предложения проходят
     вычитку тем же Qwen («исправь только грамматические ошибки, ничего больше не
     меняй; верни то же предложение»): правка принимается, только если изменила
     ≤ 15% символов и не тронула [[...]]/⚠️ — иначе остаётся версия до вычитки.
  3) unsupported — не удаляется, а помечается: после предложения вставляется
     «⚠️^[не подтверждено источником при проверке <дата>]» (inline-сноска Obsidian).
     disputed / no_source / supported не трогаются.
  4) в frontmatter статьи добавляются/обновляются: проверено, утверждений_проверено,
     подтверждено, исправлено.
Утверждение в тексте ищется по максимальному совпадению значимых слов/чисел/названий
(доля слов утверждения ≥ 0.5); не найдено — пропуск с записью в лог.

Запуск:
  python factcheck_apply.py --dry [пути .md | --all]   # ничего не пишет: diff + итог по статье
  python factcheck_apply.py [пути .md | --all]         # применяет; копия исходника в
                                                       # backup/vault_before_apply/<путь>
Повторный запуск идемпотентен: статья с frontmatter «проверено» не старее даты json
пропускается целиком; уже заменённые ссылки и предложения со сноской ⚠️ не трогаются.
"""
import argparse
import difflib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import llm
import build_vault          # write_source_note — заметка-источник для drop-разговора (задача 035)

# при фоновом запуске stdout на Windows = cp1251 — форсим utf-8 (как factcheck.py)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "vault"
FC_DIR = ROOT / "data" / "factcheck"
BACKUP_DIR = ROOT / "backup" / "vault_before_apply"
LOG = ROOT / "data" / "factcheck_apply.log"

MODEL = llm.QWEN            # hc:Qwen3.8-27B — вся обработка только на Qwen
FALLBACK = llm.QWEN_ALT     # hc:Qwen3.8-Flash-Next — fallback (ds: не используется)

MATCH_MIN = 0.5             # порог совпадения утверждения с предложением
LEN_MIN = 0.7               # переписанный раздел ≥ 70% длины исходного
DIFF_MAX_LINES = 60         # строк diff на статью в --dry
PROOF_MAX_CHANGE = 0.15     # вычитка грамматики: максимум изменённых символов предложения
# имя вида life-c0254 / nft-c0263-07 (фактчек пишет correct_source из имён разговоров)
SRC_NAME_RE = re.compile(r"^(?:ai|crypto|important|life|nft|perps|predict|shitki|soft)"
                         r"-c\d+(?:-\d+)?$")

# correct_source без заметки-источника, о котором уже сообщено в этом запуске
# (одно сообщение на источник — иначе дубли в логе на каждое утверждение)
_noted_sources = set()

WARN = "⚠️"
LINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
FM_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.S)
SECTION_HEAD_RE = re.compile(r"(?m)^##[^\S\n]+")
WORD_RE = re.compile(r"[0-9a-zа-яё$₽%]+", re.I)

STOP = frozenset("""
и в во не на я ты он она оно они мы вы с со а но да или что чтобы как это этот эта эти
тот та те там тут здесь где когда если уже ещё еще только же бы ли за по из у к о от до
для при про без над под все всё весь вся его её ее их мой наш ваш был была было были
есть нет может нельзя надо нужно например кстати также тоже потом сейчас потому поэтому
такой такая такие таким такими очень более менее самый самое
the a an and or but with for from to in on at by of is are was were be been it its this
that these those there here they them their
""".split())

# лёгкая нормализация словоформ: отбрасываем частые русские окончания (стеб ≥ 4 букв)
SUFFIXES = sorted(
    ("иями", "ями", "ами", "ого", "его", "ыми", "ими", "ому", "ему", "ешь", "ишь",
     "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ов", "ев", "ам", "ям", "ах", "ях",
     "ом", "ем", "ым", "их", "ых", "ую", "юю", "ла", "ло", "ли", "на", "но", "ны", "ни",
     "ть", "ся", "сь", "ет", "ит", "ут", "ют", "ат", "ят", "ь", "я", "а", "о", "у", "е",
     "ы", "и", "й", "ю"),
    key=len, reverse=True)

CONTRAST_RE = re.compile(
    r"(?<![а-яёa-z])а не|вместо|неверн|ошибочн|перепутан|не соответствует")
AUTHOR_RE = re.compile(
    r"не (участв|говорил|говорит|сказал|утвержда|упоминал|высказывал)|"
    r"говорит только|сказал только|другой автор|другого участника|не тот автор|"
    r"не тот участник")
VALUEWORD_RE = re.compile(r"цифр|цен[аеы]|дат[аеы]|числ|лимит|сумм|процент|автор|тикер")


def log_line(msg):
    """Строка в консоль и в data/factcheck_apply.log."""
    print(msg, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg.rstrip() + "\n")


def num_key(tok):
    """«$50»/«50%»/«1,424» → '50'/'50'/'1424' — числовое сравнение токенов."""
    s = tok.strip("$₽%")
    if s and any(ch.isdigit() for ch in s) and all(ch.isdigit() or ch in ".,-" for ch in s):
        return re.sub(r"[.,]", "", s)
    return None


def stem(w):
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: len(w) - len(suf)]
    return w


def tok_match(a, b):
    """Совпадение двух значимых токенов (точное / по стебу / числовое / по префиксу)."""
    if a == b:
        return True
    sa, sb = stem(a), stem(b)
    if sa == sb:
        return True
    na, nb = num_key(a), num_key(b)
    if na is not None and na == nb:
        return True
    if len(sa) >= 6 and len(sb) >= 6:
        n = min(len(sa), len(sb)) - 2
        if sa[:n] == sb[:n]:
            return True
    return False


def sig_tokens(text):
    """Значимые токены: числа всегда; слова ≥ 3 букв, кроме стоп-слов; в стеб-форме."""
    out = []
    for t in WORD_RE.findall(text.lower().replace("ё", "е")):
        if any(ch.isdigit() for ch in t):
            out.append(t)
        elif len(t) >= 3 and t not in STOP:
            out.append(stem(t))
    return out


def norm_heading(h):
    return re.sub(r"\s+", " ", h.replace("ё", "е")).strip().casefold()


# ---------------------------------------------------------------- предложения

def sentence_spans(text):
    """Спаны предложений: границы — [.!?;…] + пробел/конец, а также переводы строк."""
    spans, start, i = [], 0, 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ".!?;…" and (i + 1 >= n or text[i + 1].isspace()):
            spans.append((start, i + 1))
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            start = i = j
            continue
        if ch == "\n":
            end = i
            if end > start and text[end - 1] == "\r":   # CRLF: \r не входит в предложение
                end -= 1
            if text[start:end].strip():
                spans.append((start, end))
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            start = i = j
            continue
        i += 1
    if start < n and text[start:].strip():
        spans.append((start, n))
    return spans


class SentenceIndex:
    """Предложения раздела + их токены: поиск предложения под утверждение (п.5)."""

    def __init__(self, text):
        self.text = text
        self.spans = sentence_spans(text)
        self.toks = [sig_tokens(text[s:e]) for s, e in self.spans]

    def best(self, claim_text):
        """(индекс, доля значимых слов утверждения, встретившихся в предложении)."""
        claim_toks = sig_tokens(claim_text or "")
        if not claim_toks:
            return None, 0.0
        best, best_score = None, 0.0
        for idx, toks in enumerate(self.toks):
            if not toks:
                continue
            hit = sum(1 for t in claim_toks if any(tok_match(t, u) for u in toks))
            score = hit / len(claim_toks)
            if score > best_score:
                best, best_score = idx, score
        return best, best_score

    def sentence(self, idx):
        s, e = self.spans[idx]
        return self.text[s:e]

    def mark(self, idx, footnote):
        """Вставить «⚠️^[…]» после предложения idx; True если вставили (не дублируя)."""
        s, e = self.spans[idx]
        if WARN in self.text[s:e]:
            return False
        self.text = self.text[:e] + f" {WARN}^[{footnote}]" + self.text[e:]
        self.__init__(self.text)          # пересчитать спаны после вставки
        return True


# ---------------------------------------------------------------- разбор статьи

def split_sections(body):
    """Тело (без frontmatter) → секции по строкам «## »: спаны разрезают body без потерь."""
    marks = [m.start() for m in SECTION_HEAD_RE.finditer(body)]
    bounds = ([0] if not marks or marks[0] > 0 else []) + marks + [len(body)]
    secs = []
    for a, b in zip(bounds, bounds[1:]):
        if a >= b:
            continue
        text = body[a:b]
        first = text.lstrip().split("\n", 1)[0].strip()
        if first.startswith("## "):
            heading = first[3:].strip()
        elif first.startswith("# "):
            heading = first[2:].strip()
        else:
            heading = first.lstrip("#").strip() or "(без заголовка)"
        secs.append({"start": a, "end": b, "heading": heading, "text": text})
    return secs


def fmt_date(iso_ts):
    return str(iso_ts or "").split(" ")[0] or time.strftime("%Y-%m-%d")


def claim_verdict(c):
    return c.get("verdict") or c.get("final") or ""


def partial_wrong_value(c):
    """partial о неверной цифре/дате/авторе — только такие исправляются по п.2."""
    p = c.get("problem") or ""
    has_digit = any(ch.isdigit() for ch in p)
    author = bool(AUTHOR_RE.search(p))
    if author:
        return True                        # неверный автор высказывания
    if not has_digit:
        return False                       # без цифр это не «неверная цифра/дата»
    if CONTRAST_RE.search(p):
        return True                        # «в материале X, а не Y»
    if VALUEWORD_RE.search(p):
        # слово о величине + цифра в problem, которой нет в утверждении —
        # источник называет другое значение
        pnums = set(re.findall(r"\d+", p))
        cnums = set(re.findall(r"\d+", c.get("text") or ""))
        return bool(pnums - cnums)
    return False


def apply_edits(text, edits):
    """Правки [(start, end, new)] к тексту: с конца к началу; вставки позиции — сливаются."""
    ins = {}
    reps = {}
    for start, end, new in edits:
        if start == end:
            ins.setdefault(start, []).append(new)
        else:
            reps.setdefault((start, end), new)
    merged = [(s, s, "".join(parts)) for s, parts in ins.items()]
    merged += [(s, e, t) for (s, e), t in reps.items()]
    for start, end, new in sorted(merged, key=lambda x: (x[0], x[1]), reverse=True):
        text = text[:start] + new + text[end:]
    return text


# ---------------------------------------------------------------- переписывание раздела (Qwen)

REWRITE_SYS = """Ты — аккуратный редактор базы знаний Obsidian. Тебе дают раздел статьи и список \
проблемных утверждений, найденных фактчекингом по первоисточникам (переписка). Задача: переписать в \
разделе ТОЛЬКО те предложения, которые содержат проблемные утверждения, чтобы утверждение стало \
соответствовать источнику — ориентируйся на поля «исправление» и «цитата источника». Если в исправлении \
сказано «удалить» — убери это утверждение из предложения, сохранив остальную часть предложения, если \
она несёт отдельный смысл. Все остальные предложения раздела сохрани дословно: не меняй формулировки, \
порядок предложений и их количество. Правь минимально: в проблемном предложении меняй только неверную \
часть, остальное оставляй слово в слово, и следи, чтобы русский язык оставался грамматически \
правильным (согласование рода, числа и падежа) после замены слов.

ЖЁСТКИЕ ПРАВИЛА:
1. Все wiki-ссылки [[...]] обязаны сохраниться в том же количестве и с теми же именами: не удаляй, \
не добавляй, не переименовывай и не перемещай их.
2. Первая строка раздела (заголовок «## …» или «# …») остаётся без изменений.
3. Сноски вида «⚠️^[…]» сохрани ровно как есть: не удаляй и не переформулируй.
4. Не добавляй вступлений, пояснений и блоков кода. Верни исправленный раздел целиком, \
начиная прямо с заголовка, и ничего больше.
5. Грамматика: заменённые слова обязаны согласовываться с остальной частью предложения по роду, \
числу и падежу («при гипотетической раздаче», не «при гипотетическом раздаче»)."""


PROOFREAD_SYS = """Ты — корректор русской речи. Дано одно предложение из статьи базы знаний. \
Исправь только грамматические ошибки, ничего больше не меняй; верни то же предложение. \
Допустимо только согласование рода, числа и падежа и исправление неверных словоформ: не меняй \
слова и их порядок, пунктуацию, числа, термины; wiki-ссылки [[...]] и сноски «⚠️^[…]» сохрани \
дословно. Верни только исправленное предложение, без пояснений."""


def rewrite_section(base_text, items):
    """Один вызов Qwen на раздел: (текст, проблемные утверждения) → новый текст | None.
    Валидация: все [[...]] сохранены (мультимножество имён), длина ≥ 70% исходной,
    заголовок первой строкой и все сноски ⚠️ на месте."""
    claims = []
    for i, it in enumerate(items, 1):
        claims.append(
            f"{i}. Предложение из раздела: «{it['sentence']}»\n"
            f"   Утверждение: «{it['text']}»\n"
            f"   Вердикт: {it['verdict']}\n"
            f"   Проблема: {it['problem'] or '—'}\n"
            f"   Исправление: {it['fix'] or '—'}\n"
            f"   Цитата источника: «{it['quote'] or '—'}»")
    user = ("РАЗДЕЛ СТАТЬИ (как есть):\n\n" + base_text.rstrip() + "\n\n"
            "ПРОБЛЕМНЫЕ УТВЕРЖДЕНИЯ (проверено фактчекингом):\n\n" + "\n\n".join(claims) +
            "\n\nВерни исправленный раздел целиком — только текст раздела, без пояснений.")
    max_tokens = max(4000, min(16000, int(len(base_text) * 0.7)))
    resp = llm.chat(MODEL, REWRITE_SYS, user, max_tokens=max_tokens, temperature=0.2,
                    fallback=FALLBACK)
    new = re.sub(r"^```[a-z]*\s*|\s*```$", "", resp.strip(), flags=re.S).strip()
    return validate_rewrite(new, base_text)


def validate_rewrite(new, base):
    if not new.strip():
        return None
    links_new = Counter(m.group(1).strip() for m in LINK_RE.finditer(new))
    links_base = Counter(m.group(1).strip() for m in LINK_RE.finditer(base))
    if links_new != links_base:
        return None
    if len(new.strip()) < LEN_MIN * len(base.strip()):
        return None
    if new.lstrip().split("\n", 1)[0].strip() != base.lstrip().split("\n", 1)[0].strip():
        return None
    if new.count(WARN) != base.count(WARN):
        return None
    # хвостовые переводы строк раздела обязаны сохраниться (иначе следующий «## »
    # приклеится к тексту при сборке статьи)
    m = re.search(r"[\r\n]+$", base)
    new = re.sub(r"[\r\n]+$", "", new) + (m.group(0) if m else "")
    return new


def change_ratio(orig, new):
    """Доля изменённых символов при переходе orig → new (замена символа = удаление+вставка)."""
    sm = difflib.SequenceMatcher(None, orig, new, autojunk=False)
    same = sum(b.size for b in sm.get_matching_blocks())
    return (len(orig) + len(new) - 2 * same) / max(1, len(orig))


def proofread_section(new, base, alog):
    """Вычитка грамматики изменённых предложений после переписывания раздела.

    Изменённое предложение = его нет дословно в тексте до переписывания (base). На каждое —
    один короткий вызов того же Qwen («исправь только грамматические ошибки…»). Правка
    принимается, только если это то же предложение: изменилось ≤ PROOF_MAX_CHANGE символов,
    те же [[...]] (мультимножество) и столько же ⚠️, без переводов строк и заголовков.
    Иначе (и при ошибке LLM) остаётся версия до вычитки. Возвращает (текст, вызовы, правки)."""
    calls = fixed = 0
    for s, e in reversed(sentence_spans(new)):
        sent = new[s:e]
        if sent.strip() in base:            # предложение не менялось — вычитка не нужна
            continue
        calls += 1
        try:
            resp = llm.chat(MODEL, PROOFREAD_SYS, sent.strip(),
                            max_tokens=max(500, min(4000, int(len(sent) * 1.5) + 300)),
                            temperature=0, fallback=FALLBACK)
        except Exception as ex:
            alog.append(f"    вычитка не удалась: {str(ex)[:100]} — предложение как переписано")
            continue
        proof = re.sub(r"^```[a-z]*\s*|\s*```$", "", resp.strip(), flags=re.S).strip()
        ok = (bool(proof) and "\n" not in proof and not proof.lstrip().startswith("#")
              and Counter(m.group(1).strip() for m in LINK_RE.finditer(proof))
              == Counter(m.group(1).strip() for m in LINK_RE.finditer(sent))
              and proof.count(WARN) == sent.count(WARN)
              and change_ratio(sent.strip(), proof) <= PROOF_MAX_CHANGE)
        if not ok:
            alog.append(f"    вычитка отклонена (не то же предложение): {sent.strip()[:70]}")
            continue
        new = new[:s] + proof + new[e:]
        fixed += 1
    return new, calls, fixed


# ---------------------------------------------------------------- обработка раздела

def footnote_text(verdict, date):
    if verdict == "contradicted":
        return f"опровергнуто источником при проверке {date}"
    if verdict == "partial":
        return f"частично подтверждено источником при проверке {date}"
    return f"не подтверждено источником при проверке {date}"


def process_section(sec_text, claims, date, alog, dry=False):
    """Раздел + его утверждения из json → (новый текст раздела, счётчики правок).

    dry=True (режим --dry): отсутствующая заметка-источник не создаётся —
    только сообщение в alog, что она будет создана."""
    cnt = Counter()
    claims = [c for c in claims if isinstance(c, dict) and claim_verdict(c)]
    index = SentenceIndex(sec_text)
    matched = {}
    for c in claims:
        v = claim_verdict(c)
        if v not in ("wrong_citation", "contradicted", "unsupported", "partial"):
            continue
        idx, score = index.best(c.get("text"))
        if idx is None or score < MATCH_MIN:
            if v != "unsupported" and (v != "partial" or partial_wrong_value(c)):
                cnt["notfound"] += 1
                alog.append(f"    не найдено предложение: [{v}] {(c.get('text') or '')[:100]}")
            continue
        matched[id(c)] = idx

    edits = []
    marked_idx = set()

    def mark_edit(idx, verdict):
        """Пометка ⚠️^[…] после предложения idx (правка от позиций исходного текста)."""
        if idx in marked_idx:
            return False
        s, e = index.spans[idx]
        if WARN in sec_text[s:e]:
            cnt["mark_already"] += 1       # уже помечено (идемпотентность)
            return False
        line = sec_text[s:e].lstrip()
        if line.startswith("|"):           # строка таблицы: не рвём markdown — пропускаем
            cnt["mark_table"] += 1
            alog.append(f"    не помечено (строка таблицы): "
                        f"{sec_text[s:e].strip()[:80]}")
            return False
        edits.append((e, e, f" {WARN}^[{footnote_text(verdict, date)}]"))
        marked_idx.add(idx)
        return True

    # 1) wrong_citation: в предложении с утверждением [[старая]] → [[correct]]
    fixed_idx = set()      # предложения с поправленной ссылкой: ⚠️ туда не ставится (п.3)
    for c in claims:
        if claim_verdict(c) != "wrong_citation" or id(c) not in matched:
            continue
        src = (c.get("src") or "").strip()
        correct = (c.get("correct_source") or "").strip()
        idx = matched[id(c)]
        s, e = index.spans[idx]
        if not src or not correct or src == correct:
            cnt["wc_skip"] += 1
            alog.append(f"    wrong_citation без src/correct: {(c.get('text') or '')[:80]}")
            continue
        if f"[[{correct}]]" in sec_text[s:e]:
            cnt["wc_already"] += 1          # уже заменено (идемпотентность)
            fixed_idx.add(idx)
            continue
        short = correct.split("-")[0]
        if not SRC_NAME_RE.match(correct) or not (VAULT / "Источники" / short).is_dir():
            cnt["wc_badsrc"] += 1
            alog.append(f"    правильный источник {correct!r} не похож на имя разговора — "
                        f"замена пропущена (claim: {(c.get('text') or '')[:80]})")
            continue
        if not (VAULT / "Источники" / short / f"{correct}.md").exists():
            # заметки-источника может не быть (разговор в drop — build_vault.py sources
            # пишет заметки только полезным) — создаём её из базы (задача 035), чтобы
            # ссылка вела на заметку, а не на заглушку; в --dry только сообщение.
            # Один источник — одно сообщение за запуск (_noted_sources).
            if correct not in _noted_sources:
                _noted_sources.add(correct)
                if dry:
                    alog.append(f"    нет заметки vault/Источники/{short}/{correct}.md — "
                                f"будет создана заметка-источник (claim: "
                                f"{(c.get('text') or '')[:80]})")
                else:
                    note = build_vault.write_source_note(*build_vault.resolve(correct))
                    if note:
                        cnt["src_created"] += 1
                        alog.append(f"    создана заметка-источник "
                                    f"{note.relative_to(build_vault.ROOT).as_posix()} "
                                    f"(claim: {(c.get('text') or '')[:80]})")
                    else:
                        cnt["wc_nofile"] += 1
                        alog.append(f"    разговор {correct} не найден в базе — заметки нет, "
                                    f"замена всё равно выполнена, ссылка на заметку-заглушку "
                                    f"(claim: {(c.get('text') or '')[:80]})")
        occ = [m for m in LINK_RE.finditer(sec_text[s:e]) if m.group(1).strip() == src]
        if len(occ) == 1:                   # однозначно — заменяем неверную ссылку
            m = occ[0]
            edits.append((s + m.start(), s + m.end(), f"[[{correct}]]"))
            cnt["replaced"] += 1
            fixed_idx.add(idx)
        elif occ:                           # несколько одинаковых — правильную рядом
            edits.append((s + occ[0].end(), s + occ[0].end(), f" [[{correct}]]"))
            cnt["wc_added"] += 1
            fixed_idx.add(idx)
        else:                               # ссылки в предложении нет — добавляем в конец
            cut = e
            while cut > s and sec_text[cut - 1] in ".!?;… \t\r":
                cut -= 1
            edits.append((cut, cut, f" [[{correct}]]"))
            cnt["wc_added"] += 1
            fixed_idx.add(idx)

    # 3) unsupported: после предложения «⚠️^[не подтверждено источником …]» — но не в
    #     предложениях, где wrong_citation уже поправил ссылку (дубль того же утверждения)
    for c in claims:
        if claim_verdict(c) == "unsupported" and id(c) in matched:
            if matched[id(c)] in fixed_idx:
                cnt["suppressed"] += 1      # пометка погашена заменой ссылки
                continue
            if mark_edit(matched[id(c)], "unsupported"):
                cnt["marked"] += 1

    base = apply_edits(sec_text, edits) if edits else sec_text

    # 2) contradicted / partial-цифра: одно переписывание раздела на Qwen
    rewrite_items = []
    for c in claims:
        v = claim_verdict(c)
        if v == "contradicted":
            second = c.get("second") or {}
            if second and second.get("verdict") == "supported":
                continue        # оспорено вторым мнением — не трогаем
        elif v == "partial":
            if not partial_wrong_value(c) or not (c.get("fix") or c.get("quote")):
                continue
        else:
            continue
        if id(c) not in matched:
            continue
        sent = index.sentence(matched[id(c)]).strip()
        bindex = SentenceIndex(base)        # предложение после правок п.1/п.3
        bidx, bscore = bindex.best(c.get("text"))
        if bidx is not None and bscore >= MATCH_MIN:
            sent = bindex.sentence(bidx).strip()
        rewrite_items.append({"text": c.get("text") or "", "sentence": sent, "verdict": v,
                              "problem": c.get("problem") or "", "fix": c.get("fix") or "",
                              "quote": c.get("quote") or "", "_claim": c})

    if rewrite_items:
        new = None
        head = sec_text.lstrip().splitlines()[0][:70] if sec_text.strip() else ""
        try:
            new = rewrite_section(base, rewrite_items)
        except Exception as ex:
            alog.append(f"    LLM не переписал раздел «{head}»: {str(ex)[:150]}")
        if new is not None:
            # вычитка грамматики изменённых предложений (тот же Qwen, короткие вызовы)
            new, pr_calls, pr_fixed = proofread_section(new, base, alog)
            base = new
            cnt["rewritten_sections"] += 1
            cnt["rewritten_claims"] += len(rewrite_items)
            cnt["proof_calls"] += pr_calls
            cnt["proof_fixed"] += pr_fixed
        else:
            # правка раздела отброшена — проблемные утверждения просто помечаем (п.3)
            cnt["rewrite_failed"] += 1
            bindex = SentenceIndex(base)
            for it in rewrite_items:
                bidx, bscore = bindex.best(it["text"])
                if bidx is None or bscore < MATCH_MIN:
                    cnt["notfound"] += 1
                    alog.append(f"    не найдено предложение: [{it['verdict']}] "
                                f"{it['text'][:100]}")
                    continue
                if bindex.mark(bidx, footnote_text(it["verdict"], date)):
                    base = bindex.text
                    cnt["marked"] += 1
    return base, cnt


# ---------------------------------------------------------------- статья

def update_frontmatter(fm_text, vals, nl):
    lines = [l.rstrip("\r") for l in fm_text.split("\n") if l.strip() != ""]
    keys = {l.split(":", 1)[0].strip() for l in lines if ":" in l}
    out = []
    for l in lines:
        k = l.split(":", 1)[0].strip()
        out.append(f"{k}: {vals[k]}" if k in vals else l)
    for k, v in vals.items():
        if k not in keys:
            out.append(f"{k}: {v}")
    return nl.join(out)


def process_article(md_path, dry=False):
    """Статья + её json фактчекинга → (счётчики, лог, (старый, новый) текст).

    dry передаётся в process_section: заметки-источники не создаются — только
    сообщения в лог («будет создана заметка-источник …»)."""
    jpath = FC_DIR / (md_path.stem + ".json")
    data = json.loads(jpath.read_text(encoding="utf-8"))
    with md_path.open(encoding="utf-8", newline="") as f:
        raw = f.read()
    fm = FM_RE.match(raw)
    fm_text, body = (fm.group(1), raw[fm.end():]) if fm else ("", raw)
    nl = "\r\n" if "\r" in fm_text[:200] else "\n"
    date = fmt_date(data.get("checked_at"))
    stats = data.get("stats") or {}
    alog = []
    cnt = Counter()
    claims_all = []

    done = re.search(r"(?m)^проверено:\s*(\d{4}-\d{2}-\d{2})", fm_text)
    if done and done.group(1) >= date:
        alog.append(f"  уже применено (проверено: {done.group(1)} ≥ json {date}) — пропуск")
        return Counter({"skipped": 1}), alog, (raw, raw)

    secs = split_sections(body)
    used = set()
    new_secs = []
    for js in data.get("sections", []):
        jhead = norm_heading(js.get("heading") or "")
        target = None
        for i, sec in enumerate(secs):
            if i not in used and norm_heading(sec["heading"]) == jhead:
                target = i
                break
        if target is None:
            alog.append(f"  раздел json не найден в статье: «{(js.get('heading') or '')[:60]}»")
            continue
        while len(new_secs) < target:
            new_secs.append(secs[len(new_secs)])
        used.add(target)
        claims = js.get("claims", [])
        claims_all += claims
        new_text, ccnt = process_section(secs[target]["text"], claims, date, alog, dry)
        if nl == "\r\n":        # LLM возвращает текст с \n — приводим к концам строк статьи
            new_text = re.sub(r"(?<!\r)\n", "\r\n", new_text)
        cnt += ccnt
        new_secs.append(new_text)
    while len(new_secs) < len(secs):
        new_secs.append(secs[len(new_secs)])
    new_body = "".join(new_secs)

    n_claims = stats.get("claims") or len(claims_all)
    supported = stats.get("supported",
                          sum(1 for c in claims_all if claim_verdict(c) == "supported"))
    fixed = cnt["replaced"] + cnt["wc_added"] + cnt["marked"] + cnt["rewritten_claims"]
    vals = {"проверено": date,
            "утверждений_проверено": n_claims,
            "подтверждено": f"{round(100 * supported / n_claims)}%" if n_claims else "0%",
            "исправлено": fixed}
    if fm:
        new_fm = update_frontmatter(fm_text, vals, nl)
        new_raw = f"---{nl}{new_fm}{nl}---{nl}" + new_body
    else:
        new_fm = nl.join(f"{k}: {v}" for k, v in vals.items())
        new_raw = f"---{nl}{new_fm}{nl}---{nl}{nl}" + new_body
    return cnt, alog, (raw, new_raw)


# ---------------------------------------------------------------- вывод и CLI

def short_diff(old, new, name):
    diff = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile=f"a/{name}", tofile=f"b/{name}",
        n=1, lineterm=""))
    if len(diff) > DIFF_MAX_LINES:
        diff = diff[:DIFF_MAX_LINES] + [f"… (ещё {len(diff) - DIFF_MAX_LINES} строк diff)"]
    return diff


def collect_paths(args):
    out = []
    if args.all:
        for jp in sorted(FC_DIR.glob("*.json")):
            art = (json.loads(jp.read_text(encoding="utf-8")).get("article") or "").strip()
            p = ROOT / art if art else None
            if p and p.exists():
                out.append(p)
            else:
                print(f"[!] json без статьи: {jp.name[:60]}")
        return out
    for a in args.paths:
        p = Path(a)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            print(f"[!] не найден файл: {a}")
            continue
        if not (FC_DIR / (p.stem + ".json")).exists():
            print(f"[!] нет json проверки — статья пропущена: {p.name[:60]}")
            continue
        out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Применение результатов фактчекинга (data/factcheck/*.json) к статьям vault/.")
    ap.add_argument("paths", nargs="*", help="пути к статьям .md (или --all)")
    ap.add_argument("--all", action="store_true",
                    help="все статьи, для которых есть json проверки")
    ap.add_argument("--dry", action="store_true", help="ничего не писать: diff и итоги")
    args = ap.parse_args()
    if not args.paths and not args.all:
        ap.error("укажи пути статей или --all")
    paths = collect_paths(args)
    if not paths:
        print("нет статей для обработки")
        return
    if not any(llm.gateway_ok() for _ in range(4)) and not llm.gateway_ok(timeout=10):
        print("[!] HCNSEC-шлюз не отвечает — запусти OpenCode API Manager (Qwen недоступен)")
        return

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_line(f"=== factcheck_apply {'--dry ' if args.dry else ''}{stamp}: "
             f"{len(paths)} статей ===")
    total = Counter()
    t0 = time.time()
    for p in paths:
        cnt, alog, (old, new) = process_article(p, args.dry)
        total += cnt
        rel = p.relative_to(ROOT).as_posix()
        changed = new != old
        summary = (f"заменено ссылок {cnt['replaced']} (+ добавлено рядом {cnt['wc_added']}), "
                   f"переписано разделов {cnt['rewritten_sections']}, помечено ⚠️ "
                   f"{cnt['marked']}"
                   + (f", создано заметок-источников {cnt['src_created']}"
                      if cnt['src_created'] else "")
                   + (f", погашено заменой {cnt['suppressed']}" if cnt['suppressed'] else "")
                   + (f", грамматика поправлена в {cnt['proof_fixed']}/{cnt['proof_calls']}"
                      if cnt['proof_calls'] else "")
                   + f", не найдено {cnt['notfound']}")
        if args.dry:
            log_line(f"\n— {rel}   [{'ИЗМЕНИТСЯ' if changed else 'без изменений'}]")
            if cnt.get("skipped"):
                log_line("  (уже применено, пропуск)")
            elif changed:
                for ln in short_diff(old, new, rel):
                    log_line("  " + ln)
            if not cnt.get("skipped"):
                log_line(f"  итог: {summary}")
            for ln in alog:
                log_line(ln)
        else:
            if cnt.get("skipped"):
                log_line(f"— {rel}: уже применено, пропуск")
                total["articles_skipped"] += 1
            elif changed:
                bpath = BACKUP_DIR / p.relative_to(ROOT)
                bpath.parent.mkdir(parents=True, exist_ok=True)
                with bpath.open("w", encoding="utf-8", newline="") as f:
                    f.write(old)
                with p.open("w", encoding="utf-8", newline="") as f:
                    f.write(new)
                log_line(f"— {rel}: {summary} (копия: {bpath.relative_to(ROOT).as_posix()})")
            else:
                log_line(f"— {rel}: изменений нет")
            for ln in alog:
                log_line(ln)
        total["articles_changed" if changed else "articles_same"] += 1

    mode = "DRY (не записано)" if args.dry else "ПРИМЕНЕНО"
    log_line(f"\n=== {mode} · статей: {len(paths)} — изменено {total['articles_changed']}, "
             f"без изменений {total['articles_same']}, пропущено "
             f"{total['articles_skipped']} ===")
    log_line(f"заменено ссылок: {total['replaced']} (+ рядом {total['wc_added']}); "
             f"создано заметок-источников: {total['src_created']}; "
             f"переписано разделов: {total['rewritten_sections']} (не прошло проверку: "
             f"{total['rewrite_failed']}); вычитано предложений: {total['proof_calls']} "
             f"(принято {total['proof_fixed']}); помечено ⚠️: {total['marked']} (погашено "
             f"заменой {total['suppressed']}); не найдено "
             f"утверждений: {total['notfound']}; LLM: {llm.usage['calls']} вызовов, "
             f"{round(time.time() - t0)} с")


if __name__ == "__main__":
    main()
