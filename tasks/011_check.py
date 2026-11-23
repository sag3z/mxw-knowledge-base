"""Проверка /api/chat (задача 011): диалог из 2 ходов через urllib, чтение SSE-потока.

Запуск при работающем сервере:  python tasks/011_check.py [порт]  (по умолчанию 8765)
- ход 1: «что было с клеймом Ethereal?», ход 2: «а кто ещё про это писал и что советовали?»
- печатает: query каждого хода (переформулировка), число источников, первые 300 символов ответа,
  время до первого delta;
- тот же диалог с anon:true — проверяет, что в ответах нет ников авторов источников.
"""
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:" + (sys.argv[1] if len(sys.argv) > 1 else "8765")
DB = Path(__file__).resolve().parent.parent / "data" / "kb.sqlite"
Q1, Q2 = "что было с клеймом Ethereal?", "а кто ещё про это писал и что советовали?"


def chat(messages, pin=None, anon=False):
    """Один ход: POST /api/chat → список (событие, данные, t от старта)."""
    body = json.dumps({"messages": messages, "from": None, "to": None, "pin": pin, "anon": anon}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    t0 = time.time()
    out = []
    with urllib.request.urlopen(req, timeout=600) as r:
        cur = {}
        for raw in r:   # SSE: event:…, data:…, пустая строка
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("event:"):
                cur["ev"] = line[6:].strip()
            elif line.startswith("data:"):
                cur["data"] = line[5:].strip()
            elif line == "" and cur.get("ev"):
                out.append((cur["ev"], json.loads(cur["data"]), time.time() - t0))
                cur = {}
    return out


def conv_authors(ids):
    """Ники авторов сообщений этих разговоров (короткий id → авторы)."""
    db = sqlite3.connect(DB)
    short = {"nft": "nft_движуха", "ai": "ai_вайбкод_билдинг_хакатоны", "life": "жизнь_флуд_кайф",
             "crypto": "крипта_работа_проекты", "perps": "перпы_фандинг_dex_ы", "predict": "предикшены_и_ставки",
             "soft": "софты", "shitki": "щитки_мемы_деген_плеи", "important": "важное_саммари_и_коллы"}
    out = set()
    for cid in ids:
        s, conv = cid.split("-", 1)
        t = short[s]
        msg_ids = [r[0] for r in db.execute("SELECT msg_ids FROM convs WHERE topic=? AND conv=?", (t, conv))]
        ids_list = json.loads(msg_ids[0]) if msg_ids else []
        if ids_list:
            q = f"SELECT DISTINCT author FROM messages WHERE topic=? AND id IN ({','.join('?' * len(ids_list))})"
            out.update(a for (a,) in db.execute(q, (t, *ids_list)) if a)
    return out


def run(anon):
    msgs, report = [], []
    for turn, q in enumerate((Q1, Q2), 1):
        msgs.append({"role": "user", "content": q})
        evs = chat(msgs, anon=anon)
        meta = next((d for e, d, _ in evs if e == "meta"), {})
        answer = "".join(d.get("text", "") for e, d, _ in evs if e == "delta")
        err = next((d.get("error") for e, d, _ in evs if e == "error"), None)
        first = next((t for e, _, t in evs if e == "delta"), None)
        report.append({"query": meta.get("query"), "n_sources": len(meta.get("sources") or []),
                       "n_articles": len(meta.get("articles") or []), "answer": answer, "err": err,
                       "t_first": first, "sources": meta.get("sources") or []})
        msgs.append({"role": "assistant", "content": answer})
        print(f"--- ход {turn} {'(anon) ' if anon else ''}---")
        print(f"вопрос: {q}")
        print(f"query (переформулировка): {report[-1]['query']}")
        print(f"источников: {report[-1]['n_sources']}, статей: {report[-1]['n_articles']}, "
              f"ошибка: {err or '-'}")
        print(f"время до первого delta: {first:.1f} с" if first else "delta не пришёл")
        print(f"ответ ({len(answer)} симв.), первые 300:")
        print(answer[:300].replace("\n", " "))
    return report


if __name__ == "__main__":
    print("=== обычный диалог ===")
    plain = run(False)
    print("\n=== тот же диалог с anon:true ===")
    anon = run(True)

    authors = conv_authors([s["id"] for r in plain for s in r["sources"]]) | \
              conv_authors([s["id"] for r in anon for s in r["sources"]])
    # проверка: ники авторов источников не должны встречаться в ответах anon-диалога
    bad = []
    for r in anon:
        for a in authors:
            if len(a) >= 3 and re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", r["answer"]):
                bad.append((a, r["query"]))
    print(f"\nавторов в источниках: {len(authors)}; ники в anon-ответах: {bad if bad else 'нет'}")
    sys.exit(1 if bad else 0)
