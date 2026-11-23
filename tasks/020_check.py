# -*- coding: utf-8 -*-
"""Проверка задачи 020 (единый чат с ИИ): POST /api/chat → SSE.

Диалог «антидетекты» + уточнение «а что из этого бесплатное?»: время до meta, до первого delta,
полный ответ (первые 400 символов), сноски [n] ⊆ номеров источников.
Вопрос «кто разбирается в минт-ботах?» (обычный и anon): в ответе должны быть люди,
в anon — псевдонимы «Участник XXX» и ни одного настоящего ника.

Запуск: python tasks/020_check.py  (сервер должен слушать 127.0.0.1:8765)
"""
import json
import re
import sqlite3
import sys
import io
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:8765"


def chat(body, timeout=600):
    """POST /api/chat → (meta, t_meta, t_first_delta, full_text, done, error, total)."""
    t0 = time.time()
    req = urllib.request.Request(BASE + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    meta, first, deltas, done, err = None, None, [], False, None
    ev, buf = "", ""
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                buf += line[5:].strip()
            elif line == "" and buf:
                o = json.loads(buf)
                buf = ""
                if ev == "meta":
                    meta = o
                    t_meta = time.time() - t0
                elif ev == "delta":
                    if first is None:
                        first = time.time() - t0
                    deltas.append(o["text"])
                elif ev == "status":
                    print("    status:", o.get("text"))
                elif ev == "done":
                    done = True
                elif ev == "error":
                    err = o.get("error")
    return meta, t_meta, first, "".join(deltas), done, err, time.time() - t0


def check_cards(meta, tag):
    """Формат источников в meta: {n, kind, id|path, title, date, branch, category, snippet}."""
    src = (meta or {}).get("sources") or []
    ok = bool(src)
    for i, c in enumerate(src, 1):
        if set(c) < {"n", "kind", "title"}:
            ok = False
        if c.get("n") != i:
            ok = False
        if c.get("kind") not in ("conv", "article"):
            ok = False
        if c.get("kind") == "conv" and not c.get("id"):
            ok = False
        if c.get("kind") == "article" and not c.get("path"):
            ok = False
    print(f"  [{tag}] источников: {len(src)} (convs={sum(1 for c in src if c['kind'] == 'conv')}, "
          f"articles={sum(1 for c in src if c['kind'] == 'article')}), формат карточек: {'OK' if ok else 'FAIL'}")
    return src


def report(meta, t_meta, first, text, done, err, total, tag):
    print(f"  [{tag}] до meta: {t_meta:.1f} с, до первого delta: {first:.1f} с, всего: {total:.1f} с, done={done}, error={err}")
    print(f"  [{tag}] query переформулировки: {(meta or {}).get('query', '')!r}")
    print(f"  [{tag}] ответ (первые 400): {text[:400]!r}")
    return (meta or {}).get("sources") or []


def footnotes_ok(text, src, tag):
    nums = sorted({int(x) for x in re.findall(r"\[(\d+)\]", text)})
    valid = {c["n"] for c in src}
    ok = set(nums) <= valid
    print(f"  [{tag}] сноски в ответе: {nums} ⊆ {sorted(valid)} → {'OK' if ok else 'FAIL'}")
    return ok


def people_in_answer(text, anon, authors, tag):
    """В ответе должны быть люди; в anon — только псевдонимы, без настоящих ников.
    Ники короче 3 символов и вхождение не отдельным словом не считаем (ложные срабатывания)."""
    real = []
    for a in authors:
        if len(a) < 3 or not re.search(r"\w", a):
            continue
        if re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", text):
            real.append(a)
    pseudo = bool(re.search(r"Участник [0-9a-f]{3}", text))
    if anon:
        ok = pseudo and not real
        print(f"  [{tag}] псевдонимы: {pseudo}, настоящие ники в ответе: {real[:3] if real else 'нет'} → {'OK' if ok else 'FAIL'}")
    else:
        ok = bool(real) or pseudo
        print(f"  [{tag}] люди в ответе: {real[:4] if real else ('псевдонимы' if pseudo else 'никого')} → {'OK' if ok else 'FAIL'}")
    return ok


def main():
    db = sqlite3.connect("data/kb.sqlite")
    authors = [a for (a,) in db.execute(
        "SELECT author FROM experts WHERE author IS NOT NULL AND author != ''")]

    print("=== 1. Диалог: антидетекты → уточнение «а что из этого бесплатное?» ===")
    msgs = [{"role": "user", "content": "Какие антидетекты советовали для мультиаккаунтов?"}]
    meta, tm, td, text, done, err, tt = chat({"messages": msgs, "anon": False})
    src = report(meta, tm, td, text, done, err, tt, "ход 1")
    cards_ok = check_cards(meta, "ход 1")
    fn1 = footnotes_ok(text, src, "ход 1")

    msgs.append({"role": "assistant", "content": text})
    msgs.append({"role": "user", "content": "а что из этого бесплатное?"})
    meta2, tm2, td2, text2, done2, err2, tt2 = chat({"messages": msgs, "anon": False})
    src2 = report(meta2, tm2, td2, text2, done2, err2, tt2, "ход 2")
    check_cards(meta2, "ход 2")
    fn2 = footnotes_ok(text2, src2, "ход 2")

    print("\n=== 2. «кто разбирается в минт-ботах?» — люди в ответе ===")
    q = [{"role": "user", "content": "Кто разбирается в минт-ботах?"}]
    meta3, tm3, td3, text3, done3, err3, tt3 = chat({"messages": q, "anon": False})
    report(meta3, tm3, td3, text3, done3, err3, tt3, "минт-боты")
    p3 = people_in_answer(text3, False, authors, "минт-боты")

    meta4, tm4, td4, text4, done4, err4, tt4 = chat({"messages": [dict(q[0])], "anon": True})
    report(meta4, tm4, td4, text4, done4, err4, tt4, "минт-боты, anon")
    p4 = people_in_answer(text4, True, authors, "минт-боты, anon")

    print("\n=== 3. /api/search: поле title ===")
    from urllib.parse import quote
    r = urllib.request.urlopen(BASE + "/api/search?q=" + quote("прокси антидетект"), timeout=60)
    res = json.loads(r.read().decode("utf-8"))
    titles = [x.get("title") for x in res["results"][:3]]
    print(f"  результатов: {len(res['results'])}, title первых: {titles}")
    search_ok = all(t for t in titles)

    ok = all([cards_ok, fn1, fn2, p3, p4, search_ok, done, done2, done3, done4])
    print("\nИТОГ:", "OK" if ok else "ЕСТЬ ПРОБЛЕМЫ (см. FAIL выше)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
