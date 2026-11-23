"""Embed useful conversations into data/kb.sqlite (table emb) with Qwen3-Embedding-8B via the local HCNSEC gateway.

Usage: python embed.py   (OpenCode API Manager must be running; resumable)
"""
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

import numpy as np

BASE = "http://127.0.0.1:43117/p/ocam-1d85357dbaed/v1"
MODEL = "Qwen3-Embedding-8B"
DOC_MAX = 6000
db = sqlite3.connect(Path(__file__).with_name("data") / "kb.sqlite")
db.execute("CREATE TABLE IF NOT EXISTS emb(topic TEXT, conv TEXT, doc TEXT, vec BLOB, PRIMARY KEY(topic, conv))")


def embed(texts, retries=5):
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    for a in range(retries):
        try:
            req = urllib.request.Request(BASE + "/embeddings", data=body, headers={"Content-Type": "application/json"})
            d = json.load(urllib.request.urlopen(req, timeout=300))
            return [x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])]
        except Exception as e:
            err = e
            time.sleep(5 * 2 ** a)
    raise RuntimeError(err)


def doc(c):
    parts = [f"Ветка: {c['topic']}. Категория: {c['category']}. Теги: {', '.join(json.loads(c['tags']))}.",
             c["summary"] or "", c["scheme_summary"] or ""]
    ids = json.loads(c["key_msgs"])
    if ids:
        q = f"SELECT text, media_text FROM messages WHERE topic=? AND id IN ({','.join('?' * len(ids))})"
        for text, mt in db.execute(q, (c["topic"], *ids)):
            parts.append(text or "")
            for m in json.loads(mt or "[]"):
                parts.append(re.sub(r"\s+", " ", m["text"])[:800])
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p))[:DOC_MAX]


def main():
    db.row_factory = sqlite3.Row
    done = {(r[0], r[1]) for r in db.execute("SELECT topic, conv FROM emb")}
    todo = [c for c in db.execute("SELECT * FROM convs WHERE verdict!='drop'") if (c["topic"], c["conv"]) not in done]
    print("к эмбеддингу:", len(todo))
    for i in range(0, len(todo), 16):
        batch = todo[i:i + 16]
        docs = [doc(c) for c in batch]
        vecs = embed(docs)
        db.executemany("INSERT OR REPLACE INTO emb VALUES(?,?,?,?)", [
            (c["topic"], c["conv"], d, np.asarray(v, dtype=np.float32).tobytes()) for c, d, v in zip(batch, docs, vecs)])
        db.commit()
        if (i // 16) % 10 == 0:
            print(f"{i + len(batch)}/{len(todo)}", flush=True)
    print("готово:", db.execute("SELECT COUNT(*) FROM emb").fetchone()[0])


if __name__ == "__main__":
    main()
