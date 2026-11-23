"""export_static.py — автономный архив базы MXW для создателя приватки.

Собирает dist/MXW-база-<дата среза>/ из ТЕХ ЖЕ файлов фронта, что и локальный сайт
(app/web/): ES-модули app/web/js/*.js склеиваются в один классический скрипт
app/bundle.js (file:// не умеет ES-модули), данные — JS-файлы в window.MXW_DATA.
Наличие window.MXW_DATA включает автономный режим api.js: разделы считают всё
локально. Смысловой поиск — без сети и без ключа: та же модель e5-small, что у
локального сервера (векторы emb_local/emb_art → int8+масштаб, base64 в app/data/),
запрос эмбеддится прямо в браузере (embed_web.js: onnxruntime-web wasm + токенайзер
transformers.js, файлы модели — base64-шарды app/model/*.js, грузятся при первом
поиске). Ключ любого OpenAI-совместимого API («Настройки ИИ», только localStorage) нужен лишь
для генерации ответов ИИ в чате; без ключа чат показывает найденные источники.

  Открыть карту.html — вход (из app/web/index.html, относительные пути);
  app/               — style.css + lib/ + bundle.js + model/ + данные app/data/*.js + медиа;
  Obsidian/          — копия vault/ (открыть как хранилище);
  ПРОЧТИ.txt         — короткая инструкция.

  python export_static.py --media light   # быстрый прогон: без видео и файлов
  python export_static.py                 # полный (по умолчанию --media full)

Медиа (только для полезных разговоров, из data/media_index.jsonl): фото — миниатюра +
оригинал; голосовые и кружки — всегда; видео ≤ 25 МБ — включить, больше — строка «видео
не включено в архив (N МБ)»; файлы ≤ 10 МБ — включить с предупреждением, больше — не включать.
Имена файлов нейтральные: <i>.<ext>, пути экспорта не раскрываются.

Разговоры: только полезные (verdict != 'drop'); жизнь_флуд_кайф/c0175 исключён в любом случае.
Никогда не включает: .llm_env, .openrouter_key, data/llm_calls.log, исходники пайплайна,
backup/, tasks/. В конце — скан dist на секретные подстроки и итог по размерам.
"""
import argparse
import base64
import json
import re
import shutil
import sys
import time
import types
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
WEB = APP / "web"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP))

# НЕ читаем .llm_env / .openrouter_key: подменяем llm/embed заглушками ДО импорта server,
# чтобы переиспользовать его логику (conv_detail/noise/show, медиа-индекс, events, ...) без ключей.
sys.modules["llm"] = types.ModuleType("llm")
sys.modules["embed"] = types.ModuleType("embed")
import server  # noqa: E402  (app/server.py: локальная версия остаётся нетронутой)
from build_fts import article_chunks  # noqa: E402  (кусочки статей — тот же алгоритм, что у клиента)

DB = server.db()
VAULT = ROOT / "vault"
EXCLUDE_CONV = ("жизнь_флуд_кайф", "c0175")   # исключён в любом случае
VIDEO_MAX = 25 * 1024 * 1024                  # видео ≤ 25 МБ
FILES_MAX = 10 * 1024 * 1024                  # файлы ≤ 10 МБ
TEXT_EXTS = {".html", ".js", ".txt", ".md", ".css", ".json"}

# скан на секреты: код архива — те же подстроки, но sk-/Bearer с границей слова (иначе
# ловятся «ask-conv», «ask-article» из data-act); контент (Obsidian/, app/data/) — строгие
# формы, чтобы не ловить слова чата вроде «api-keys» в ссылке или «KinoPoisk-подобная».
# app/lib/ — вендорные минифицированные библиотеки (d3, marked, ort, transformers…):
# их код не наш, широкие паттерны кода там дают ложные срабатывания (например,
# «Authorization: `Bearer ${e}`» в transformers.js) — для них тоже строгие формы,
# реальный вшитый токен они ловят так же.
SECRET_CODE = [r"(?<![A-Za-z0-9])sk-", r"(?<![A-Za-z0-9])Bearer\s", "api_key",
               "LLM_API_KEY", "reformboss", "127.0.0.1:43117", "ocam-"]
SECRET_CONTENT = [r"sk-[A-Za-z0-9]{20,}", r"Bearer\s+[A-Za-z0-9._-]{16,}",
                  r"(?i)api[_-]key\s*[:=]", "LLM_API_KEY", "reformboss",
                  r"127\.0\.0\.1:43117", "ocam-"]

# порядок ES-модулей app/web/js для bundle.js — топологический по import-графу
# (embed_web без импортов; api импортирует embed_web; main.js — точка входа, последним).
# Проверяется при сборке.
MODULES = ["embed_web", "icons", "api", "ui", "settings", "conv", "chat", "map", "events",
           "people", "topics", "articles", "main"]
# данные, вшиваемые в страницу до bundle.js (шарды разговоров/статей и эмбеддинги
# api.js грузит лениво сам)
DATA_HEAD = ["meta", "graph", "convs", "articles", "events", "experts", "trends"]

# файлы модели поиска (models/e5-small) → ключи embed_web.js: токенайзер,
# int8-модель ONNX, wasm-рантайм onnxruntime-web 1.14 (версия должна совпадать
# с app/web/lib/ort.min.js — иначе сессия не создастся)
MODEL_DIR_SRC = ROOT / "models" / "e5-small"
MODEL_FILES = {"tok": "tokenizer.json", "model": "model_quantized.onnx", "wasm": "ort-wasm-simd.wasm"}
MODEL_SHARD = 15 * 1024 * 1024   # байт на шард (кратно 3 → base64 ~20 МБ, декодируется независимо)


# --- запись JS-файлов (данные как window.MXW_DATA.<часть> = {...}) --------------------
def _js_safe(s: str) -> str:
    """</script и разделители строк U+2028/2029 нельзя оставлять в JS-литерале."""
    return (s.replace("</script", "<\\/script")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def jdump(obj) -> str:
    return _js_safe(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def write_js(path: Path, name: str, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = jdump(obj)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"window.MXW_DATA=window.MXW_DATA||{{}};window.MXW_DATA.{name}={body};\n")


def write_js_head(f, parts):
    f.write("window.MXW_DATA=window.MXW_DATA||{};" +
            "".join(f"window.MXW_DATA.{p}=window.MXW_DATA.{p}||{{}};" for p in parts) + "\n")


# --- эмбеддинги e5-small (как у сервера) → int8 + масштаб на вектор, base64 ----------
def quant_rows(vecs):
    """Список BLOB float32 → (int8-байты одним куском, [масштаб], dim).
    Тот же формат, что читает api.js: байт * масштаб = координата."""
    scales, chunks, dim = [], [], 0
    for v in vecs:
        v = np.frombuffer(v, dtype=np.float32)
        if not dim:
            dim = len(v)
        assert len(v) == dim, "векторы разной размерности в emb-таблице"
        scale = float(np.max(np.abs(v)) / 127.0) or 1e-9
        scales.append(scale)
        chunks.append(np.clip(np.rint(v / scale), -127, 127).astype(np.int8).tobytes())
    return b"".join(chunks), scales, dim


def write_emb_js(path: Path, name: str, ids, vecs, what: str):
    """int8+масштаб+base64 в app/data/<name>.js; возвращает размер файла."""
    codes, scales, dim = quant_rows(vecs)
    obj = {"ids": ids, "dim": dim, "scale": scales,
           "codes": base64.b64encode(codes).decode("ascii")}
    write_js(path, name, obj)
    size = path.stat().st_size / 1e6
    print(f"{path.name}: {len(ids)} векторов × {dim}, {size:.1f} МБ ({what})", flush=True)
    return size


# --- модель поиска для браузера: base64-шарды app/model/*.js ------------------------
def export_model(out_model: Path):
    """model_quantized.onnx, tokenizer.json и ort-wasm-simd.wasm → JS-шарды.
    file:// не даёт fetch локальных файлов — embed_web.js грузит их «<script>»-ами
    и собирает в Uint8Array. manifest.js перечисляет шарды каждого файла;
    границы шардов кратны 3 байтам → base64 каждого шарда декодируется независимо."""
    out_model.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, fname in MODEL_FILES.items():
        src = MODEL_DIR_SRC / fname
        if not src.is_file():
            raise SystemExit(f"нет {src} — файлы модели качает embed_local.py (ensure_model)")
        data = src.read_bytes()
        names, n_shard = [], max(1, -(-len(data) // MODEL_SHARD))
        for i in range(n_shard):
            part = data[i * MODEL_SHARD:(i + 1) * MODEL_SHARD]
            names.append(f"{key}_{i}.js")
            with open(out_model / names[-1], "w", encoding="utf-8", newline="\n") as f:
                f.write(f"window.MXW_MODEL=window.MXW_MODEL||{{}};"
                        f"window.MXW_MODEL.{key}=window.MXW_MODEL.{key}||[];\n"
                        f"window.MXW_MODEL.{key}.push({jdump(base64.b64encode(part).decode('ascii'))});\n")
        files[key] = names
        print(f"model {key}: {src.name} — {len(data) / 1e6:.1f} МБ → {n_shard} шардов", flush=True)
    with open(out_model / "manifest.js", "w", encoding="utf-8", newline="\n") as f:
        f.write("window.MXW_MODEL=window.MXW_MODEL||{};window.MXW_MODEL.files=" + jdump(files) + ";\n")


# --- сборка bundle.js: ES-модули → один классический скрипт --------------------------
# import * as X from "./y.js";   → const X = __m.y;
# import { a, b } from "./y.js"; → const { a, b } = __m.y;   (фигурные — и многострочные)
# export function/const name     → function/const name + name в return
IMPORT_RE = re.compile(r'^import\s+(?:\*\s+as\s+(\w+)\s*|(\{[^}]*?\}))\s*'
                       r'from\s*"\./(\w+)\.js"\s*;?[ \t]*$', re.M)
EXPORT_RE = re.compile(r'^export\s+(async\s+function|function|const)\s+([A-Za-z_$][\w$]*)', re.M)


def _module_deps(src: str):
    return {m.group(3) for m in IMPORT_RE.finditer(src)}


def _convert_import(m):
    if m.group(1):   # namespace import
        return f"const {m.group(1)} = __m.{m.group(3)};"
    names = re.sub(r"\s+", " ", m.group(2))     # многострочные скобки — в одну строку
    return f"const {names} = __m.{m.group(3)};"


def bundle_web_js(out_path: Path):
    """Каждый модуль — IIFE в реестре __m: работает с file://, import-граф
    превращается в порядок инициализации. Файлы фронта не меняются — правится app/web/."""
    js_dir = WEB / "js"
    actual = {p.stem for p in js_dir.glob("*.js")}
    assert actual == set(MODULES), \
        f"app/web/js не совпадает с MODULES: {sorted(actual ^ set(MODULES))} — поправь список"
    parts = ["/* bundle.js — склейка ES-модулей app/web/js/ для file://.\n"
             "   Сгенерирован export_static.py: правь app/web/js/*.js, не этот файл. */\n"
             "var __m = {};\n"]
    done = set()
    for name in MODULES:
        src = (js_dir / f"{name}.js").read_text(encoding="utf-8")
        deps = _module_deps(src)
        late = deps - done
        assert not late, f"{name}.js импортирует {sorted(late)}, а они ещё не в __m — поправь MODULES"
        exports = []

        def strip_export(m):
            exports.append(m.group(2))
            return m.group(1) + " " + m.group(2)

        body = EXPORT_RE.sub(strip_export, src)
        body = IMPORT_RE.sub(_convert_import, body)
        assert exports or name == "main", f"{name}.js без экспортов?"
        ret = "return {" + ", ".join(exports) + "};"
        parts.append(f"__m.{name} = (function(){{\n\"use strict\";\n{body}\n{ret}\n}})();\n")
        done.add(name)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts))
    return done


def build_entry(out_file: Path):
    """«Открыть карту.html» из app/web/index.html: модульный script → данные + bundle,
    затем все /web/ → app/ (style.css, lib/), заголовок — про автономный архив."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    tag = '<script type="module" src="/web/js/main.js"></script>'
    assert tag in html, "в app/web/index.html нет модульного тега main.js — проверь шаблон"
    data_tags = "".join(f'<script src="app/data/{n}.js"></script>\n' for n in DATA_HEAD)
    html = html.replace(tag, f"{data_tags}<script src=\"app/bundle.js\"></script>")
    html = html.replace("/web/", "app/")
    html = html.replace("<title>MXW · база знаний</title>",
                        "<title>MXW · база (автономный архив)</title>")
    with open(out_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


# --- медиа: решение по каждому файлу индекса + копирование ---------------------------
def media_ext(name: str) -> str:
    e = re.sub(r"[^a-z0-9]", "", Path(name).suffix.lower())[:5]
    return "." + (e or "bin")


def export_media(mode, useful_msgs, out_media):
    """Копирует медиа полезных разговоров с нейтральными именами <i>.<ext>.
    Возвращает media_dec: i → {i, kind, name, size, th, ext, ex}; ex: 0 включён,
    1 видео не включено, 2 файл не включено, 3 файлы выключены (--media light)."""
    dec, n_files, n_bytes = {}, 0, 0
    if mode == "none":
        return dec, 0, 0
    t0 = time.time()
    for (topic, msg), files in server.MEDIA_BY_MSG.items():
        if (topic, msg) not in useful_msgs:
            continue
        for f in files:
            i, kind = f["i"], f["kind"]
            src = server.MEDIA_FILES[i]
            d = {"i": i, "kind": kind, "name": f["name"], "size": f["size"],
                 "th": f["th"], "ext": media_ext(f["name"]), "ex": 0}
            orig, thumb = src["path"], src["thumb"]
            if kind == "photos":
                if not orig.exists():
                    d["ex"] = 4
                else:
                    shutil.copyfile(orig, out_media / f"{i}{d['ext']}")
                    n_files += 1; n_bytes += orig.stat().st_size
                    if thumb and thumb.exists():
                        shutil.copyfile(thumb, out_media / f"{i}_t.jpg")
                        n_files += 1; n_bytes += thumb.stat().st_size
            elif kind in ("voice_messages", "round_video_messages"):
                if not orig.exists():
                    d["ex"] = 4
                else:
                    shutil.copyfile(orig, out_media / f"{i}{d['ext']}")
                    n_files += 1; n_bytes += orig.stat().st_size
            elif kind == "video_files":
                if mode == "full" and orig.exists() and orig.stat().st_size <= VIDEO_MAX:
                    shutil.copyfile(orig, out_media / f"{i}{d['ext']}")
                    n_files += 1; n_bytes += orig.stat().st_size
                else:
                    d["ex"] = 1
            elif kind == "files":
                if mode != "full":
                    d["ex"] = 3
                elif not orig.exists() or orig.stat().st_size > FILES_MAX:
                    d["ex"] = 2
                else:
                    shutil.copyfile(orig, out_media / f"{i}{d['ext']}")
                    n_files += 1; n_bytes += orig.stat().st_size
            dec[i] = d
    print(f"медиа [{mode}]: {n_files} файлов, {n_bytes / 1e6:.1f} МБ, "
          f"{time.time() - t0:.0f} c", flush=True)
    return dec, n_files, n_bytes


# --- полезные разговоры ----------------------------------------------------------------
def useful_convs():
    rows = DB.execute("SELECT topic, conv FROM convs WHERE verdict!='drop' ORDER BY topic, conv").fetchall()
    out = [(r["topic"], r["conv"]) for r in rows
           if (r["topic"], r["conv"]) != EXCLUDE_CONV]
    return out


def tree_size(p: Path) -> int:
    """Размер каталога (для сводки размеров архива)."""
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main():
    ap = argparse.ArgumentParser(description="автономный архив базы MXW")
    ap.add_argument("--media", choices=["none", "light", "full"], default="full",
                    help="none — без медиа; light — без видео и файлов; full — по правилам (по умолчанию)")
    ap.add_argument("--out", default="dist", help="каталог вывода (по умолчанию dist)")
    ap.add_argument("--no-zip", action="store_true", help="не собирать zip")
    args = ap.parse_args()

    snapshot = DB.execute("SELECT substr(max(ts),1,10) FROM messages").fetchone()[0]
    out_root = ROOT / args.out
    folder = out_root / f"MXW-база-{snapshot}"
    if folder.exists():
        shutil.rmtree(folder)   # полный перезапуск: не оставляем хвостов прошлых прогонов
    (folder / "app" / "data").mkdir(parents=True)
    (folder / "app" / "lib").mkdir(parents=True)
    (folder / "app" / "media").mkdir(parents=True)
    print(f"экспорт: {folder}  (срез данных {snapshot}, медиа: {args.media})", flush=True)

    convs = useful_convs()
    conv_ids = {f"{server.SHORT[t]}-{c}" for t, c in convs}
    n_useful_db = DB.execute("SELECT COUNT(*) FROM convs WHERE verdict!='drop'").fetchone()[0]
    c175 = DB.execute("SELECT COUNT(*) FROM convs WHERE topic=? AND conv=?", EXCLUDE_CONV).fetchone()[0]
    print(f"полезных разговоров: {len(convs)} из {n_useful_db} в базе; "
          f"жизнь_флуд_кайф/c0175 в базе: {c175} (исключён в любом случае)", flush=True)

    # --- сообщения полезных разговоров: боты, авторы, таймлайн ----------------------
    ids_by_topic = defaultdict(set)
    for t, c in convs:
        for mid in json.loads(DB.execute("SELECT msg_ids FROM convs WHERE topic=? AND conv=?",
                                         (t, c)).fetchone()[0]):
            ids_by_topic[t].add(mid)
    useful_msgs = {(t, m) for t, ids in ids_by_topic.items() for m in ids}
    bots, authors, hist = set(), set(), Counter()
    for t, ids in ids_by_topic.items():
        for mid, ts, is_bot, author in DB.execute(
                "SELECT id, ts, is_bot, author FROM messages WHERE topic=?", (t,)):
            if mid not in ids:
                continue
            if is_bot:
                bots.add((t, mid))
            elif ts:
                hist[ts[:10]] += 1
            if author:
                authors.add(author)
    n_msgs = len(useful_msgs)

    # --- graph: узлы без drop, темы, кластеры, гистограмма, авторы ------------------
    g = server.graph()
    nodes = [n for n in g["nodes"] if n["v"] != "drop" and n["id"] != f"{server.SHORT[EXCLUDE_CONV[0]]}-{EXCLUDE_CONV[1]}"]
    graph = {"nodes": nodes, "topics": g["topics"],
             "clusters": g["clusters"], "authors": sorted(authors),
             "hist": sorted(hist.items())}
    write_js(folder / "app" / "data" / "graph.js", "graph", graph)
    print(f"graph.js: {len(nodes)} узлов, {len(graph['authors'])} авторов", flush=True)

    # --- convs.js: корпус для поиска по выжимкам (аналог fts_conv) ------------------
    convs_index = []
    for t, c in convs:
        r = DB.execute("SELECT verdict, category, start_ts, tags, summary, has_scheme, scheme_summary "
                       "FROM convs WHERE topic=? AND conv=?", (t, c)).fetchone()
        convs_index.append({"id": f"{server.SHORT[t]}-{c}", "b": server.SHORT[t],
                            "c": r["category"], "t": (r["start_ts"] or "")[:10],
                            "sc": r["has_scheme"], "v": r["verdict"], "sum": r["summary"] or "",
                            "tags": json.loads(r["tags"] or "[]"), "sch": r["scheme_summary"] or ""})
    write_js(folder / "app" / "data" / "convs.js", "convsIndex", convs_index)
    print(f"convs.js: {len(convs_index)} записей", flush=True)

    # --- медиа + шард разговоров по веткам (conv_<short>.js) ------------------------
    media_dec, n_media, media_bytes = export_media(args.media, useful_msgs,
                                                   folder / "app" / "media")

    by_branch = defaultdict(list)
    for t, c in convs:
        by_branch[server.SHORT[t]].append(f"{server.SHORT[t]}-{c}")
    n_shard_msgs = 0
    t0 = time.time()
    for short, ids in by_branch.items():
        p = folder / "app" / "data" / f"conv_{short}.js"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            write_js_head(f, ["convs"])
            for cid in ids:
                d = server.conv_detail(cid)   # noise/show — той же логикой, что /api/conv
                for m in d["messages"]:
                    m["bot"] = (server.LONG[cid.split("-", 1)[0]], m["id"]) in bots
                    if args.media == "none":
                        m["files"] = []
                    else:   # ex 0..2 оставляем (клиент покажет «не включено»), 3/4 убираем
                        m["files"] = [dd for fd in m["files"]
                                      if (dd := media_dec.get(fd["i"])) and dd["ex"] in (0, 1, 2)]
                    n_shard_msgs += 1
                f.write(f"window.MXW_DATA.convs[{jdump(cid)}]={jdump(d)};\n")
        print(f"conv_{short}.js: {len(ids)} разговоров, {p.stat().st_size / 1e6:.1f} МБ", flush=True)
    print(f"сообщений в шардах: {n_shard_msgs} ({time.time() - t0:.0f} c)", flush=True)

    # --- статьи: список + тела ленивыми секциями art_<n>.js ------------------------
    # список — как /api/articles (без «Источники/»: это сырые выжимки разговоров —
    # читаются в разговорах, ~12 МБ; тела этих статей в архив не идут)
    arts_list = server.articles()
    sections, sec_idx = [], {}
    for a in arts_list:
        if a["section"] not in sec_idx:
            sec_idx[a["section"]] = len(sections)
            sections.append(a["section"])
    with open(folder / "app" / "data" / "articles.js", "w", encoding="utf-8", newline="\n") as f:
        f.write("window.MXW_DATA=window.MXW_DATA||{};\n")
        f.write(f"window.MXW_DATA.articles={jdump(arts_list)};\n")
        f.write(f"window.MXW_DATA.articleSects={jdump(sections)};\n")
    for sec, idx in sec_idx.items():
        p = folder / "app" / "data" / f"art_{idx}.js"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("window.MXW_DATA=window.MXW_DATA||{};"
                    "window.MXW_DATA.articleBodies=window.MXW_DATA.articleBodies||{};\n")
            for a in arts_list:
                if a["section"] != sec:
                    continue
                body = (VAULT / a["path"]).read_text(encoding="utf-8")
                f.write(f"window.MXW_DATA.articleBodies[{jdump(a['path'])}]={jdump(body)};\n")
    print(f"articles.js: {len(arts_list)} статей, {len(sections)} секций "
          f"(без «Источники/»)", flush=True)

    # --- events / experts / trends ----------------------------------------------------
    ev = server.events()
    for e in ev["events"]:   # источники — только разговоры из экспорта
        e["sources"] = [s for s in e["sources"] if s["id"] in conv_ids]
    write_js(folder / "app" / "data" / "events.js", "events", ev)

    ex = server.experts()
    for grp in ex["topics"]:
        for p in grp["top"]:
            p["examples"] = [e for e in p["examples"] if e["id"] in conv_ids]
    for p in ex["people"]:
        for tp in p["top"]:
            tp["examples"] = [e for e in tp["examples"] if e["id"] in conv_ids]
    write_js(folder / "app" / "data" / "experts.js", "experts", ex)

    trends = {"cluster": server.trends("cluster"), "category": server.trends("category")}
    for cat in (trends["category"].get("convs") or {}).values():   # разговоры из экспорта
        for wk in list(cat):
            cat[wk] = [cid for cid in cat[wk] if cid in conv_ids]
    write_js(folder / "app" / "data" / "trends.js", "trends", trends)
    print(f"events: {len(ev['events'])}, experts: {len(ex['people'])} чел, "
          f"trends: {len(trends['cluster']['series'])}+{len(trends['category']['series'])} серий", flush=True)

    # --- эмбеддинги разговоров (emb_local, e5-small 384-d): int8 + масштаб, base64 ----
    # те же векторы, что у сервера; запрос эмбеддится в браузере (embed_web.js) — ключ не нужен
    t0 = time.time()
    ids, vecs = [], []
    for r in DB.execute("SELECT topic, conv, vec FROM emb_local"):
        cid = f"{server.SHORT[r['topic']]}-{r['conv']}"
        if cid not in conv_ids:
            continue
        ids.append(cid)
        vecs.append(r["vec"])
    write_emb_js(folder / "app" / "data" / "emb.js", "emb", ids, vecs,
                 f"emb_local, {time.time() - t0:.0f} c")
    if len(ids) < len(conv_ids):
        missed = len(conv_ids) - len(ids)
        print(f"  внимание: без векторов {missed} полезных разговоров (embed_local.py не всё эмбеддил)", flush=True)

    # --- эмбеддинги кусков статей (emb_art): те же куски, что fts_art/build_fts -------
    # ключ на клиенте — "path\nci", ci = номер куска статьи (articleChunks в api.js
    # повторяет build_fts.article_chunks — порядок и тексты совпадают)
    t0 = time.time()
    art_rows = {(r["path"], r["chunk"]): r["vec"]
                for r in DB.execute("SELECT path, chunk, vec FROM emb_art")}
    a_ids, a_vecs = [], []
    for a in arts_list:
        body = (VAULT / a["path"]).read_text(encoding="utf-8")
        for ci, ch in enumerate(article_chunks(body)):
            vec = art_rows.pop((a["path"], ch), None)
            if vec is None:
                continue
            a_ids.append(f"{a['path']}\n{ci}")
            a_vecs.append(vec)
    if art_rows:
        print(f"  внимание: {len(art_rows)} векторов emb_art не совпали с кусками статей "
              f"(статья изменилась после embed_local.py?) — не экспортированы", flush=True)
    write_emb_js(folder / "app" / "data" / "emb_art.js", "embArt", a_ids, a_vecs,
                 f"emb_art, {time.time() - t0:.0f} c")

    # --- модель поиска в браузер: base64-шарды app/model/ -----------------------------
    t0 = time.time()
    export_model(folder / "app" / "model")
    print(f"app/model: {tree_size(folder / 'app' / 'model') / 1e6:.1f} МБ "
          f"({time.time() - t0:.0f} c)", flush=True)

    # --- meta, фронт (bundle + стиль + библиотеки), страница входа -------------------
    write_js(folder / "app" / "data" / "meta.js", "meta", {
        "snapshot": snapshot, "exported": date.today().isoformat(),
        "convs": len(convs), "msgs": n_shard_msgs, "media": args.media,
        "mediaFiles": n_media, "usefulTotal": DB.execute(
            "SELECT COUNT(*) FROM convs WHERE verdict!='drop'").fetchone()[0]})

    bundle_web_js(folder / "app" / "bundle.js")
    shutil.copyfile(WEB / "style.css", folder / "app" / "style.css")
    for lib in (WEB / "lib").glob("*"):
        shutil.copyfile(lib, folder / "app" / "lib" / lib.name)
    build_entry(folder / "Открыть карту.html")
    n_libs = len(list((WEB / "lib").glob("*")))
    print(f"фронт: bundle.js {(folder / 'app' / 'bundle.js').stat().st_size / 1e3:.0f} КБ "
          f"({len(MODULES)} модулей), style.css, {n_libs} библиотеки", flush=True)

    # --- Obsidian/ и ПРОЧТИ.txt -------------------------------------------------------
    shutil.copytree(VAULT, folder / "Obsidian")

    read_me = f"""MXW — автономный архив базы знаний приватного чата (срез данных {snapshot})

ЧТО ЭТО
  Оффлайн-копия базы знаний чата MXW: карта разговоров, сами разговоры с медиа,
  поиск по смыслу и словам, чат с базой, статьи, активности, люди, темы.
  Ничего устанавливать не нужно, интернет не требуется: поиск по смыслу работает
  на модели прямо в браузере (без ключей), ключ нужен только для ответов ИИ в чате.

КАК ОТКРЫТЬ КАРТУ
  Двойной клик по «Открыть карту.html» (лучше всего Chrome или Edge). Работает с
  диска, сервер не нужен. Слева — разделы:
    Чат        — спроси что угодно о чате MXW: ответ ИИ по базе со источниками;
                 без ключа показывает найденные источники и подсказку про ключ
    Карта      — карта разговоров: цвет — тема, размер — число сообщений,
                 таймлайн снизу показывает, как росла база
    Активности — события с датами из чата: минты, клеймы, вайтлисты, тестнеты
    Люди       — кто в чём разбирается: темы, примеры, «спросить ИИ»
    Темы       — о чём говорили по неделям, главные проекты месяца
    Статьи     — база знаний: конспекты по проектам и схемам абуза

ПОИСК ПО СМЫСЛУ (без ключа и без интернета)
  Поиск и источники в чате работают полностью локально: при первом вопросе архив
  грузит в браузер модель поиска (~200 МБ из папки app/model, один раз, ~10 секунд
  — дальше мгновенно). Ключ не нужен.

ЧАТ — ОТВЕТЫ ИИ (по желанию)
  Внизу слева нажми «Настройки ИИ» и выбери, какая модель будет «мозгом» чата.
  Подходит любой OpenAI-совместимый API: OpenRouter (сотни моделей по одному
  ключу — проще всего, https://openrouter.ai/keys), OpenAI, DeepSeek, Google Gemini,
  Groq, Mistral, xAI, Together, локальные Ollama / LM Studio или свой адрес.
    1. Провайдер — из списка (адрес API подставится сам).
    2. API-ключ — ссылка «получить ключ» рядом с полем.
    3. Кнопка «Модели» загрузит список моделей провайдера — начни вводить название.
       «Модель для поиска» — быстрая дешёвая модель для переформулировки вопроса
       (можно оставить пустой — будет та же).
    4. «Проверить» — короткий тестовый запрос, потом «Сохранить».
  Ключ хранится только в localStorage этого браузера и уходит только в выбранный
  API — больше никуда. Без ключа чат всё равно ищет и показывает источники.
  Если «Проверить» пишет «нет связи» — провайдер не пускает запросы прямо из
  браузера (CORS); OpenRouter пускает всегда.

OBSIDIAN
  Папка Obsidian/ — то же знание в виде markdown-хранилища: открой её в Obsidian
  (Open folder as vault). Ссылки [[…]] внутри заметок рабочие.

ВАЖНО
  Внутри есть разделы со схемами абуза и ники участников — не пересылай архив дальше.
  Медиа из чата лежат в app/media; файлы из чата запускай только после проверки.

  Разговоров в архиве: {len(convs)} (только полезные, мусор исключён).
  Срез данных: {snapshot}, экспорт: {date.today().isoformat()}.
"""
    (folder / "ПРОЧТИ.txt").write_text(read_me, encoding="utf-8", newline="\n")

    # --- размеры по частям -------------------------------------------------------------
    sz_data = tree_size(folder / "app" / "data") + tree_size(folder / "app" / "lib") \
        + tree_size(folder / "app" / "model") \
        + (folder / "app" / "bundle.js").stat().st_size \
        + (folder / "app" / "style.css").stat().st_size \
        + (folder / "Открыть карту.html").stat().st_size
    sz_media = tree_size(folder / "app" / "media")
    sz_obs = tree_size(folder / "Obsidian")

    # --- скан на секреты ----------------------------------------------------------------
    findings = scan_secrets(folder)

    # --- проверки ------------------------------------------------------------------------
    bad = [n["id"] for n in nodes if n["v"] == "drop"]
    assert not bad, "в экспорте оказались drop-разговоры"
    assert f"{server.SHORT[EXCLUDE_CONV[0]]}-{EXCLUDE_CONV[1]}" not in conv_ids
    assert len(conv_ids) == len(convs)
    assert (folder / "app" / "bundle.js").exists() and (folder / "Открыть карту.html").exists()
    print(f"\nпроверка: разговоров в экспорте {len(convs)} (полезных в базе {n_useful_db}), "
          f"drop нет, {server.SHORT[EXCLUDE_CONV[0]]}-{EXCLUDE_CONV[1]} отсутствует", flush=True)

    # --- zip ------------------------------------------------------------------------------
    zip_path = out_root / f"MXW-база-{snapshot}.zip"
    if not args.no_zip:
        t0 = time.time()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in sorted(folder.rglob("*")):
                if f.is_file():
                    z.write(f, f"{folder.name}/{f.relative_to(folder)}")
        print(f"zip: {zip_path.name} — {zip_path.stat().st_size / 1e6:.1f} МБ ({time.time() - t0:.0f} c)",
              flush=True)

    sz_model = tree_size(folder / "app" / "model")
    print(f"\nитог {folder.name}: данные {(sz_data - sz_model) / 1e6:.1f} МБ + модель поиска {sz_model / 1e6:.1f} МБ "
          f"+ медиа {sz_media / 1e6:.1f} МБ + Obsidian {sz_obs / 1e6:.1f} МБ = "
          f"{(sz_data + sz_media + sz_obs) / 1e6:.1f} МБ")
    if findings:
        print("скан на секреты — НАЙДЕНО:")
        for f in findings:
            print("  " + f)
    else:
        print("скан на секреты: чисто")
    return 0 if not findings else 1


def scan_secrets(folder: Path):
    """Секретные подстроки по текстовым файлам dist. Код архива (всё, кроме Obsidian/,
    app/data/ и app/lib/) — подстроки с границами слов, ожидается пусто. Контент
    (Obsidian/, app/data/ — тексты чата и статьи) и вендорные библиотеки (app/lib/) —
    строгие формы: реальный ключ, а не случайные совпадения слов (api-keys в ссылке,
    KinoPoisk-подобная, заголовок Authorization в transformers.js и т.п.)."""
    out = []
    for f in sorted(folder.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in TEXT_EXTS:
            continue
        rel = f.relative_to(folder).as_posix()
        is_content = rel.startswith("Obsidian/") or rel.startswith("app/data/") or rel.startswith("app/lib/")
        pats = SECRET_CONTENT if is_content else SECRET_CODE
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in pats:
            for m in re.finditer(pat, text):
                ctx = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
                out.append(f"{rel}: «{m.group(0)}» … {ctx} …")
    return out


if __name__ == "__main__":
    sys.exit(main())
