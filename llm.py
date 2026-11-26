"""Minimal OpenAI-compatible client for three providers (keys in .llm_env, never printed).

Маршрутизация по префиксу модели: "ds:<model>" → официальный DeepSeek
(DEEPSEEK_BASE_URL/DEEPSEEK_API_KEY), "hc:<model>" → HCNSEC-шлюз — локальный OpenCode API
Manager (ключ не нужен, шлюз подставляет сам; llm.gateway_ok проверяет, что он запущен),
без префикса → reformboss (LLM_BASE_URL/LLM_API_KEY). У каждого провайдера свой семафор
параллельности (LIMIT_REFORMBOSS/LIMIT_DEEPSEEK/LIMIT_HCNSEC) — все вызовы
chat/chat_json/chat_stream проходят через него, поэтому параллельные потоки в скриптах
не устраивают провайдеру 429. Официальный DeepSeek вызывается с выключенным thinking
(иначе max_tokens съедается reasoning-токенами) и используется ТОЛЬКО чатом сайта
(app/server.py); пакетная обработка (фактчек и т.п.) — на Qwen через HCNSEC
(llm.QWEN/llm.QWEN_ALT). Нет DEEPSEEK_* в .llm_env → ds: модели падают понятной ошибкой
без повторов (NoProviderError) — срабатывает fallback."""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_ENV_FILE = Path(__file__).with_name(".llm_env")   # нет файла (копия с GitHub) — провайдер задаётся в ⚙ Настройки сайта
ENV = dict(l.split("=", 1) for l in _ENV_FILE.read_text().split() if "=" in l) if _ENV_FILE.exists() else {}
BASE, KEY = ENV.get("LLM_BASE_URL", ""), ENV.get("LLM_API_KEY", "")

DS_PREFIX = "ds:"                              # префикс модели → официальный DeepSeek
DS_BASE = ENV.get("DEEPSEEK_BASE_URL") or ""   # официальный провайдер (ключ не печатается)
DS_KEY = ENV.get("DEEPSEEK_API_KEY") or ""

HC_PREFIX = "hc:"                              # префикс модели → HCNSEC-шлюз
HCNSEC_BASE = ENV.get("HCNSEC_BASE_URL") or "http://127.0.0.1:43117/p/ocam-1d85357dbaed/v1"
                                               # локальный шлюз OpenCode API Manager (OpenCodeAPIManager.exe);
                                               # Authorization не отправляем — шлюз подставляет ключ сам

LIMIT_REFORMBOSS = 3   # reformboss держит ~3 одновременных запроса (на 8 параллельных 7 получают 429)
LIMIT_DEEPSEEK = 16    # официальный DeepSeek выдерживает 24 параллельных без 429 — берём 16 с запасом
LIMIT_HCNSEC = 12      # HCNSEC: 12 параллельных запросов к Qwen3.8-27B — все ок за 4 с (замеры)

_sem_reformboss = threading.Semaphore(LIMIT_REFORMBOSS)
_sem_deepseek = threading.Semaphore(LIMIT_DEEPSEEK)
_sem_hcnsec = threading.Semaphore(LIMIT_HCNSEC)
LIMIT_CUSTOM = 8       # провайдер из ⚙ Настройки сайта (любой OpenAI-совместимый)
_sem_custom = threading.Semaphore(LIMIT_CUSTOM)


class Provider(str):
    """Модель у произвольного OpenAI-совместимого провайдера (из ⚙ Настройки сайта):
    строка = имя модели (так она и попадает в логи — ключ нигде не печатается),
    плюс .base (адрес API до /chat/completions) и .key (может быть пустым — Ollama, LM Studio)."""
    def __new__(cls, name, base, key=""):
        s = str.__new__(cls, name)
        s.base = (base or "").strip().rstrip("/")
        s.key = (key or "").strip()
        return s

PLANNER = "deepseek-v4.1-flash"   # kimi-k3.1-pro on reformboss is ~60 s per reply — too slow for batch work
WRITER = "deepseek-v4.1-flash"
ENTITY = "glm-5.3"
FAST = "ds:deepseek-flash"        # официальный DeepSeek: быстрый (~1.8 с на короткий запрос) — только чат сайта
PRO = "ds:deepseek-v4-pro"        # официальный DeepSeek: тяжёлые задачи, точнее — только чат сайта
QWEN = "hc:Qwen3.8-27B"           # HCNSEC: 4 с на короткий запрос — вся пакетная обработка (фактчек)
QWEN_ALT = "hc:Qwen3.8-Flash-Next"  # HCNSEC: помедленнее (~11 с) — fallback пакетной обработки

usage = {"prompt": 0, "completion": 0, "calls": 0}
_lock = threading.Lock()
LOG = Path(__file__).with_name("data") / "llm_calls.log"


class NoProviderError(RuntimeError):
    """Провайдер модели не настроен (нет ключей в .llm_env) — повторять бессмысленно."""


def _route(model):
    """Модель → (имя у провайдера, base_url, ключ, семафор, официальный ли DeepSeek):
    "ds:<model>" → официальный DeepSeek, "hc:<model>" → HCNSEC-шлюз (ключ пустой —
    Authorization не отправляем, шлюз подставляет ключ сам), без префикса → reformboss.
    Для ds: без DEEPSEEK_* — NoProviderError: вызов падает понятной ошибкой, chat/chat_stream
    не ретраят — сразу fallback."""
    if isinstance(model, Provider):
        return str(model), model.base, model.key, _sem_custom, None
    if model.startswith(HC_PREFIX):
        return model[len(HC_PREFIX):], HCNSEC_BASE, "", _sem_hcnsec, False
    if model.startswith(DS_PREFIX):
        if not DS_BASE or not DS_KEY:
            raise NoProviderError(f"{model}: в .llm_env нет DEEPSEEK_BASE_URL/DEEPSEEK_API_KEY")
        return model[len(DS_PREFIX):], DS_BASE, DS_KEY, _sem_deepseek, True
    if not BASE:
        raise NoProviderError(f"{model}: нет .llm_env — укажи провайдера ИИ в ⚙ Настройки сайта")
    return model, BASE, KEY, _sem_reformboss, False


def _headers(key):
    """Заголовки /chat/completions: Authorization только при непустом ключе — HCNSEC-шлюз
    ключа не требует (подставляет сам), пустой Bearer ему не шлём."""
    h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def gateway_ok(timeout=3.0):
    """Доступность HCNSEC-шлюза (OpenCodeAPIManager.exe должен быть запущен): короткий GET
    к базе шлюза с таймаутом. Любой HTTP-ответ (в т.ч. 404 на GET) — шлюз поднят;
    connection refused/таймаут — не запущен. Вызвавший скрипт по False сразу печатает
    понятную ошибку «запусти OpenCode API Manager», а не виснет на длинных таймаутах."""
    try:
        urllib.request.urlopen(HCNSEC_BASE, timeout=timeout)
    except urllib.error.HTTPError:
        return True          # сервер ответил — шлюз запущен (404 на GET — нормально)
    except Exception:
        return False
    return True


def _payload(name, messages, max_tokens, temperature, ds):
    """Тело запроса к /chat/completions. Для официального DeepSeek выключаем thinking
    ({"thinking": {"type": "disabled"}}): модель иначе тратит max_tokens на reasoning
    (короткий вызов может вернуться без content вовсе), а нам нужны быстрые ответы —
    и чат, и компактный JSON фактчекинга."""
    body = {"model": name, "temperature": temperature, "max_tokens": max_tokens, "stream": True,
            "messages": messages}
    if ds is not None:   # свои провайдеры: stream_options понимают не все (ds=None — Provider)
        body["stream_options"] = {"include_usage": True}
    if ds:
        body["thinking"] = {"type": "disabled"}
    return json.dumps(body).encode()


def _stream(model, system, user, max_tokens, temperature):
    """Streaming call: bytes keep flowing, so the provider's ~100 s Cloudflare timeout (HTTP 524) doesn't hit.
    Маршрутизация по префиксу модели (_route): "ds:" → официальный DeepSeek, "hc:" → HCNSEC-шлюз,
    иначе reformboss; семафор провайдера держится на всё время запроса — параллельных вызовов
    не больше лимита."""
    name, base, key, sem, ds = _route(model)
    body = _payload(name, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens, temperature, ds)
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=_headers(key))
    parts, u, finish, buf = [], {}, None, []

    def handle(data):
        nonlocal u, finish
        d = json.loads(data)
        if d.get("error"):
            raise RuntimeError(str(d["error"])[:300])
        u = d.get("usage") or u
        for ch in d.get("choices") or []:
            parts.append((ch.get("delta") or {}).get("content") or "")
            finish = ch.get("finish_reason") or finish

    with sem, urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:   # SSE: an event may span several "data:" lines; a blank line ends it
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
                continue
            if line == "" and buf:
                data = "\n".join(buf)
                buf = []
                if data.strip() == "[DONE]":
                    break
                handle(data)
        if buf and "\n".join(buf).strip() != "[DONE]":
            handle("\n".join(buf))
    return "".join(parts), u, finish


def _log_usage(model, u):
    """Usage одного завершённого вызова — в общий счётчик и в data/llm_calls.log."""
    with _lock:
        usage["prompt"] += u.get("prompt_tokens", 0) or 0
        usage["completion"] += u.get("completion_tokens", 0) or 0
        usage["calls"] += 1
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {model} in={u.get('prompt_tokens')} out={u.get('completion_tokens')}\n")


def chat(model, system, user, max_tokens=16000, temperature=0.3, retries=6, fallback=None):
    err = ""
    for attempt in range(retries):
        try:
            text, u, finish = _stream(model, system, user, max_tokens, temperature)
            _log_usage(model, u)
            if finish == "length" and not text.strip():
                # модели с рассуждениями (Qwen на HCNSEC) могут потратить весь max_tokens
                # на reasoning и вернуть пустой content — один повтор с удвоенным max_tokens
                print(f"[llm] {model}: пустой ответ при finish_reason=length — "
                      f"повтор с max_tokens={max_tokens * 2}", flush=True)
                text, u, finish = _stream(model, system, user, max_tokens * 2, temperature)
                _log_usage(model, u)
            if finish == "length":
                raise RuntimeError("truncated (finish_reason=length)")
            if not text.strip():
                raise RuntimeError("empty response")
            return text
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            if e.code in (400, 401, 403):
                break
        except NoProviderError as e:
            err = str(e)[:300]
            break   # провайдер не настроен — повторы не помогут, сразу fallback (если задан)
        except Exception as e:
            err = str(e)[:300]
        time.sleep(min(120, 5 * 2 ** attempt))
    if fallback and fallback != model:
        print(f"[llm] {model} не ответил ({err[:80]}), переключаюсь на {fallback}", flush=True)
        return chat(fallback, system, user, max_tokens, temperature, retries, None)
    raise RuntimeError(f"{model}: {err}")


def _stream_once(model, system, messages, max_tokens, temperature):
    """Одна попытка потокового вызова: yield кусков текста по мере поступления (диалоговая history).
    Usage (даже при обрыве) логируется в data/llm_calls.log в finally — как в chat().
    Маршрутизация по префиксу модели (_route): "ds:" → официальный DeepSeek, "hc:" → HCNSEC-шлюз,
    иначе reformboss; семафор провайдера держится на всё время стрима."""
    name, base, key, sem, ds = _route(model)
    body = _payload(name, [{"role": "system", "content": system}] +
                    [{"role": m["role"], "content": m["content"]} for m in messages],
                    max_tokens, temperature, ds)
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=_headers(key))
    buf, u = [], {}

    def handle(data):
        d = json.loads(data)
        if d.get("error"):
            raise RuntimeError(str(d["error"])[:300])
        if d.get("usage"):
            u.update(d["usage"])
        return "".join((ch.get("delta") or {}).get("content") or "" for ch in d.get("choices") or [])

    try:
        with sem, urllib.request.urlopen(req, timeout=900) as r:
            for raw in r:   # SSE: событие может занимать несколько строк "data:", пустая строка завершает
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    buf.append(line[5:].lstrip())
                    continue
                if line == "" and buf:
                    data = "\n".join(buf)
                    buf = []
                    if data.strip() == "[DONE]":
                        break
                    piece = handle(data)
                    if piece:
                        yield piece
            if buf and "\n".join(buf).strip() != "[DONE]":
                piece = handle("\n".join(buf))
                if piece:
                    yield piece
    finally:
        if u:   # usage пришёл (даже при обрыве) — логируем как chat()
            _log_usage(model, u)


RETRY_DELAYS = (2, 4, 8, 15, 20)   # паузы перед повторами стрима (только пока не отдан ни один кусок)


def chat_stream(model, system, messages, max_tokens=4000, temperature=0.3, fallback=None, on_status=None):
    """Потоковая генерация: yield кусков текста по мере поступления (диалоговая history).
    messages — список {role, content} ПОСЛЕ system. Usage логируется в data/llm_calls.log как в chat().
    HTTP 429/5xx/обрыв/пустой стрим ДО первого куска → до 5 повторов с паузами 2, 4, 8, 15, 20 с
    (400/401/403 не повторяем); перед каждым повтором вызывается on_status(n, total). После первого
    куска повторов нет (иначе текст задвоится) — ошибка пробрасывается вызывающему. Все попытки
    исчерпаны и задан fallback ≠ model → одна попытка fallback-модели. .close() у недобранного
    генератора допишет usage."""
    for retry in range(1 + len(RETRY_DELAYS)):
        if retry:
            if on_status:
                on_status(retry, len(RETRY_DELAYS))
            time.sleep(RETRY_DELAYS[retry - 1])
        produced, err = False, ""
        try:
            for piece in _stream_once(model, system, messages, max_tokens, temperature):
                produced = True
                yield piece
        except urllib.error.HTTPError as e:
            if produced or e.code in (400, 401, 403):
                raise
            err = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
        except NoProviderError as e:
            err = str(e)[:300]
            break   # провайдер не настроен — повторы не помогут, сразу fallback
        except Exception as e:   # обрыв соединения, таймаут, ошибка провайдера — до первого куска
            if produced:
                raise
            err = str(e)[:300]
        if produced:
            return
        err = err or "пустой ответ"
    if fallback and fallback != model:
        print(f"[llm] {model} не ответил ({err[:80]}), переключаюсь на {fallback}", flush=True)
        yield from _stream_once(fallback, system, messages, max_tokens, temperature)
        return
    raise RuntimeError(f"{model}: {err}")


def chat_json(model, system, user, **kw):
    """Ask for JSON; strip code fences; one repair retry on parse failure."""
    text = chat(model, system, user, **kw)
    kw.pop("fallback", None)
    for _ in range(2):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
        m = re.search(r"[\[{].*[\]}]", s, re.S)
        try:
            return json.loads(m.group(0) if m else s)
        except json.JSONDecodeError as e:
            text = chat(model, "Исправь JSON. Верни только валидный JSON без пояснений.",
                        f"Ошибка: {e}\n\n{text}", **kw)
    raise RuntimeError(f"{model}: invalid JSON")


def list_models(base, key="", timeout=20, max_pages=30):
    """GET <base>/models у OpenAI-совместимого провайдера → отсортированный список id ВСЕХ моделей.
    Листает страницы, если провайдер отдаёт список частями: OpenAI-стиль (has_more + last_id →
    ?after=), Google-стиль (nextPageToken → ?pageToken=), next_page_token/next."""
    from urllib.parse import urlencode
    h = {"Accept": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    url0 = base.strip().rstrip("/") + "/models"
    ids, params = set(), {}
    for _ in range(max_pages):
        url = url0 + ("?" + urlencode(params) if params else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        items = d.get("data") if isinstance(d, dict) else d
        if not isinstance(items, list) and isinstance(d, dict):
            items = d.get("models") or []
        page = [(x.get("id") or x.get("name") or "") if isinstance(x, dict) else str(x) for x in items or []]
        before = len(ids)
        ids.update(i for i in page if i)
        if not isinstance(d, dict) or len(ids) == before:
            break
        tok = d.get("nextPageToken") or d.get("next_page_token")
        if tok:
            params = {"pageToken": tok, "page_token": tok}
        elif d.get("has_more") and (d.get("last_id") or page):
            params = {"after": d.get("last_id") or page[-1]}
        else:
            break
    return sorted(ids)
