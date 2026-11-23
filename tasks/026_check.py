# -*- coding: utf-8 -*-
"""Проверка задачи 026 (ранжирование: смысл главный, слова бонусом; LLM-расширение ≤ 8 с).

Для 6 вопросов — топ-6 заголовков и время /api/search для expand=0 и expand=1
(второй прогон того же вопроса — из кэша расширений, если LLM успел доложить).
Кириллица кодируется через urllib.parse.urlencode (curl из Git Bash портит кириллицу).
Цели: для первых двух вопросов ≥4 из 6 по теме; expand=0 < 3 с; expand=1 ≤ 9 с в первый
раз (не успел за 8 с — ответ без keywords, опоздавший результат ляжет в кэш).
«По теме» считается грубой эвристикой по титулу+выжимке — финальную оценку даёт человек.

Запуск: python tasks/026_check.py  (сервер должен слушать 127.0.0.1:8765)
"""
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:8765"

QUESTIONS = [
    "Какие антидетекты советовали для мультиаккаунтов?",
    "какие прокси брать для фермы",
    "Как не словить бан Claude из РФ?",
    "Что было с клеймом Ethereal?",
    "Кто делал минт-ботов?",
    "какие проекты оказались скамом",
]

# грубая эвристика «по теме» для первых двух вопросов (титул/выжимка содержат слова темы)
TOPIC_HINTS = {
    QUESTIONS[0]: ["антидетект", "мультиаккаунт", "adspower", "dolphin", "браузер", "профил",
                   "отпечатк", "gologin", "incogniton", "undetectable", "прокси", "кука"],
    QUESTIONS[1]: ["прокси", "ферм", "фарм", "инфр", "vpn", "асати", "webshare", "residential",
                   "proxy", "мобильн", "провайдер", "датацентр", "аккаунт", "регистрац"],
}


def api_search(q, expand, timeout=60):
    qs = urllib.parse.urlencode({"q": q, "expand": str(expand)})
    t0 = time.time()
    with urllib.request.urlopen(BASE + "/api/search?" + qs, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d, time.time() - t0


def show(q, tag, d, t):
    res = d.get("results") or []
    print(f"  [{tag}] {t:.2f} с, semantic={d.get('semantic')}, результатов: {len(res)}")
    n_topic, hints = 0, TOPIC_HINTS.get(q)
    for i, x in enumerate(res[:6], 1):
        print(f"    {i}. [{x.get('score')}] {x.get('title')}")
        if hints:
            blob = (x.get("title") or "").lower() + " " + (x.get("summary") or "").lower()
            if any(h in blob for h in hints):
                n_topic += 1
    if hints:
        print(f"    по теме (эвристика): {n_topic}/6")
    return n_topic


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE + "/api/graph", timeout=5) as r:
                if r.status == 200:
                    time.sleep(3)   # прогрев e5-small фоновым потоком сервера
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    if not wait_ready():
        print("сервер не отвечает на", BASE)
        return 1
    ok = True
    for qi, q in enumerate(QUESTIONS, 1):
        print(f"\n=== {qi}. {q} ===")
        d0, t0 = api_search(q, 0)
        n0 = show(q, "expand=0", d0, t0)
        d1, t1 = api_search(q, 1)   # первый прогон: LLM-расширение, ждём ≤ 8 с
        n1 = show(q, "expand=1 (1-й)", d1, t1)
        d2, t2 = api_search(q, 1)   # второй прогон того же вопроса — из кэша
        n2 = show(q, "expand=1 (2-й, {})".format("кэш" if t2 < 2 else "снова ждали LLM"), d2, t2)
        if t0 >= 3:
            print(f"    !! expand=0 дольше 3 с: {t0:.2f}")
            ok = False
        if t1 > 9:
            print(f"    !! expand=1 (первый) дольше 9 с: {t1:.2f}")
            ok = False
        if qi <= 2 and n0 < 4:
            print(f"    !! expand=0: по теме меньше 4 из 6 ({n0})")
            ok = False
    print("\n=== поздний кэш: вопрос 1 ещё раз (должен быть из кэша, если LLM доложил) ===")
    d, t = api_search(QUESTIONS[0], 1)
    show(QUESTIONS[0], "кэш" if t < 2 else "LLM ещё не доложил", d, t)
    print("\nИТОГ:", "OK" if ok else "ЕСТЬ ПРОБЛЕМЫ (см. !! выше)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
