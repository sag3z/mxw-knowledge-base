# -*- coding: utf-8 -*-
"""Локальные эмбеддинги: multilingual-e5-small (384-d) через onnxruntime + tokenizers, без сети.

Модель — intfloat/multilingual-e5-small в ONNX-экспорте Xenova (models/e5-small/):
tokenizer.json + onnx/model_quantized.onnx (int8); качаются с Hugging Face при первом запуске
(ensure_model). Тот же экспорт работает в браузере через transformers.js — «Xenova/multilingual-e5-small»
(файл onnx/model_quantized.onnx, тот же tokenizer.json) — это понадобится архиву (задача 023).
Совместимость по векторам: один токенайзер, mean pooling по attention_mask + L2-нормировка,
префиксы e5 — «passage: » для документов, «query: » для запросов; обрезка по 512 токенам.

Таблицы (data/kb.sqlite):
    emb_local(topic, conv, vec BLOB float32) — полезные разговоры (verdict != 'drop');
        документ разговора — тот же, что строит embed.doc() из embed.py;
    emb_art(path, chunk, vec BLOB float32) — куски статей из fts_art (chunk = текст куска),
        если таблица fts_art есть. Устаревшие векторы (куски исчезли из fts_art) вычищаются.

Запуск:  python embed_local.py   — resumable, пачками по 32, прогресс в консоль, в конце время.
Пакеты: onnxruntime, tokenizers, numpy (ставятся вместе с fastembed).
"""
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "kb.sqlite"
MODEL_DIR = ROOT / "models" / "e5-small"
HF = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/"
FILES = {"tokenizer.json": "tokenizer.json", "onnx/model_quantized.onnx": "model_quantized.onnx"}
DIM = 384          # размерность multilingual-e5-small
MAX_LEN = 512      # токенов модели (обрезка документов и запросов)
BATCH = 32

_init_lock = threading.Lock()   # ленивая загрузка модели (один раз)
_infer_lock = threading.Lock()  # инференс сериализуем: токенайзер с padding — общее состояние
_model = None                    # кэш: (Tokenizer, onnxruntime.InferenceSession)


def ensure_model():
    """Скачать файлы модели с Hugging Face, если их нет (однократно)."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for hf, local in FILES.items():
        p = MODEL_DIR / local
        if p.exists() and p.stat().st_size > 0:
            continue
        print(f"качаю {hf} → {p} …", flush=True)
        tmp = p.with_suffix(".part")
        urllib.request.urlretrieve(HF + hf, tmp)
        tmp.rename(p)
        print(f"  готово: {p.name} ({p.stat().st_size // (1 << 20)} МБ)", flush=True)


def _get_model():
    """(tokenizer, session) — загрузка один раз, кэш в памяти (потокобезопасно)."""
    global _model
    if _model is not None:
        return _model
    with _init_lock:
        if _model is None:
            ensure_model()
            from tokenizers import Tokenizer          # ленивые импорты: серверу модель нужна не сразу
            import onnxruntime as ort
            tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
            tok.enable_truncation(max_length=MAX_LEN)
            tok.enable_padding(pad_id=0, pad_token="<pad>")
            sess = ort.InferenceSession(str(MODEL_DIR / "model_quantized.onnx"),
                                        providers=["CPUExecutionProvider"])
            _model = (tok, sess)
        return _model


def encode(texts, prefix):
    """Эмбеддинги списка текстов (prefix: 'query: '/'passage: ') → np.float32 (n, 384), L2-нормированы."""
    tok, sess = _get_model()
    out = []
    with _infer_lock:
        for i in range(0, len(texts), BATCH):
            enc = tok.encode_batch([prefix + t for t in texts[i:i + BATCH]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            hidden = sess.run(None, {"input_ids": ids, "attention_mask": mask,
                                     "token_type_ids": np.zeros_like(ids)})[0]
            m = mask[:, :, None].astype(hidden.dtype)
            v = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)   # mean pooling по attention_mask
            v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)  # L2-нормировка
            out.append(v.astype(np.float32))
    return np.concatenate(out) if out else np.zeros((0, DIM), dtype=np.float32)


def embed_query(text):
    """Эмбеддинг поискового запроса («query: »-префикс) → np.ndarray (384,), L2-нормирован."""
    return encode([text], "query: ")[0]


def _embed_table(db, table, todo, what):
    """Пачками эмбеддит (key, doc) → INSERT OR REPLACE; прогресс в консоль."""
    if not todo:
        print(f"{what}: нечего делать (всё уже есть)")
        return
    todo = sorted(todo, key=lambda kd: len(kd[1]), reverse=True)   # длинные первыми — пачки однороднее
    t0 = time.time()
    print(f"{what}: к эмбеддингу {len(todo)} …", flush=True)
    for i in range(0, len(todo), BATCH):
        b = todo[i:i + BATCH]
        vecs = encode([d for _, d in b], "passage: ")
        db.executemany(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?)",
                       [(*k, v.tobytes()) for (k, _), v in zip(b, vecs)])
        db.commit()
        if (i // BATCH) % 5 == 0 or i + BATCH >= len(todo):
            print(f"  {what} {i + len(b)}/{len(todo)} ({time.time() - t0:.0f} с)", flush=True)


def main():
    t0 = time.time()
    ensure_model()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS emb_local(topic TEXT, conv TEXT, vec BLOB, PRIMARY KEY(topic, conv))")
    db.execute("CREATE TABLE IF NOT EXISTS emb_art(path TEXT, chunk TEXT, vec BLOB, PRIMARY KEY(path, chunk))")

    import embed
    embed.db = db   # doc() читает сообщения тем же соединением — без конфликтов блокировок

    # 1) разговоры: документ — embed.doc(), только полезные (verdict != 'drop')
    db.execute("DELETE FROM emb_local WHERE (topic, conv) NOT IN "
               "(SELECT topic, conv FROM convs WHERE verdict != 'drop')")
    done = {(r["topic"], r["conv"]) for r in db.execute("SELECT topic, conv FROM emb_local")}
    todo = [((r["topic"], r["conv"]), embed.doc(r))
            for r in db.execute("SELECT * FROM convs WHERE verdict != 'drop'")
            if (r["topic"], r["conv"]) not in done]
    _embed_table(db, "emb_local", todo, "emb_local")

    # 2) куски статей из fts_art (если таблица есть); устаревшие куски вычищаем
    try:
        art = {(r["path"], r["body"]) for r in db.execute("SELECT path, body FROM fts_art")}
    except sqlite3.OperationalError:
        art = None
    if art is not None:
        db.execute("DELETE FROM emb_art WHERE (path, chunk) NOT IN (SELECT path, body FROM fts_art)")
        done_art = {(r["path"], r["chunk"]) for r in db.execute("SELECT path, chunk FROM emb_art")}
        todo_art = [((p, ch), ch) for (p, ch) in art if (p, ch) not in done_art]   # ((ключ), документ)
        _embed_table(db, "emb_art", todo_art, "emb_art")

    n_local = db.execute("SELECT COUNT(*) FROM emb_local").fetchone()[0]
    n_art = db.execute("SELECT COUNT(*) FROM emb_art").fetchone()[0]
    print(f"готово: emb_local={n_local} векторов, emb_art={n_art} векторов, "
          f"всего {time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
