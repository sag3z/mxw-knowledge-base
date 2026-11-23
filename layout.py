"""Semantic layout for the MXW map: UMAP 2D coordinates + KMeans clusters + LLM cluster names.

Input:  data/kb.sqlite, table emb(topic, conv, vec) — 2048-d float32 (useful convs only).
Output: layout(topic, conv, x, y, k), clusters(k, name, descr, n, x, y), cache data/cluster_names.json.
       Координаты: PCA-поворот (длинная ось — горизонтально), затем независимая нормировка осей:
       x ∈ [-1600, 1600], y ∈ [-1000, 1000] — облако заполняет экран 16:10.

Run:  python layout.py [--k 30] [--rename]
       --rename — ТОЛЬКО переименовать кластеры (новый промпт «понятных» названий):
       координаты и состав кластеров не пересчитываются, меняются name/descr в clusters
       и в кэше data/cluster_names.json.
"""
import argparse
import hashlib
import json
import sqlite3
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import umap
from sklearn.cluster import KMeans

import llm

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "kb.sqlite"
CACHE = ROOT / "data" / "cluster_names.json"

SYS = """Ты именуешь тематические кластеры разговоров Telegram-чата крипто-комьюнити MXW.
Тебе даны выжимки разговоров кластера и его частотные теги.
Верни строго JSON без пояснений:
{"name": "2–5 слов по-русски, конкретно (проекты/механики), без слова 'обсуждение'", "descr": "одно предложение о содержании кластера"}"""

# --rename: «понятные» названия для обычного участника чата (не перечисления проектов)
RENAME_SYS = """Ты переименовываешь тематические кластеры разговоров Telegram-чата крипто-комьюнити MXW.
Тебе даны выжимки разговоров кластера и его частотные теги.
Верни строго JSON без пояснений:
{"name": "понятное название темы для обычного участника чата, 2–4 слова, по сути (механика или тип активности); НЕ перечисляй больше одного проекта, без слова 'обсуждение'",
 "descr": "одно предложение, о чём разговоры кластера"}"""


def cache_key(k_global, ids):
    # Ключ зависит только от K и id разговоров, ближайших к центроиду в пространстве эмбеддингов,
    # и НЕ зависит от 2D-координат: смена раскладки/нормировки не инвалидирует кэш и не зовёт LLM.
    s = f"{k_global}:{','.join(sorted(ids))}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def name_cluster(user, sys_prompt=SYS):
    r = llm.chat_json(llm.WRITER, sys_prompt, user, max_tokens=2000, temperature=0.2, fallback=llm.ENTITY)
    name, descr = (r.get("name") or "").strip(), (r.get("descr") or "").strip()
    if not name:
        raise RuntimeError("empty name")
    return {"name": name, "descr": descr or name}


def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def rename_only(db):
    """--rename: состав кластеров и координаты НЕ пересчитываем — читаем layout/clusters,
    для каждого кластера просим у LLM новое «понятное» название (RENAME_SYS) и обновляем
    name/descr в clusters и в кэше cluster_names.json. n/x/y остаются прежними."""
    if not _has_table(db, "layout") or not _has_table(db, "clusters"):
        raise SystemExit("таблиц layout/clusters нет — сначала полный запуск без --rename")

    rows = db.execute("SELECT topic, conv, vec FROM emb").fetchall()
    keys = [(r["topic"], r["conv"]) for r in rows]
    X = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    kof = {(r["topic"], r["conv"]): r["k"] for r in db.execute("SELECT topic, conv, k FROM layout")}
    K = len({k for k in kof.values() if k is not None})
    print(f"rename: {len(keys)} векторов, K={K} (координаты и состав не меняются)", flush=True)

    convs = {(r["topic"], r["conv"]): r for r in
             db.execute("SELECT topic, conv, tags, summary FROM convs")}
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    jobs = []
    for r in db.execute("SELECT k, name, n FROM clusters ORDER BY n DESC"):
        k = r["k"]
        idx = [i for i, key in enumerate(keys) if kof.get(key) == k]
        if not idx:
            continue
        cent = X[idx].mean(0)
        cent /= np.linalg.norm(cent) + 1e-9
        near = sorted(idx, key=lambda i: -float(X[i] @ cent))[:30]
        ids = [f"{keys[i][0]}-{keys[i][1]}" for i in near]
        sums = [(convs[keys[i]]["summary"] or "")[:200] for i in near]
        tags = Counter()
        for i in idx:
            for t in json.loads(convs[keys[i]]["tags"] or "[]"):
                tags[t] += 1
        top = [t for t, _ in tags.most_common(15)]
        user = (f"Кластер из {len(idx)} разговоров (старое название: «{r['name']}»). Теги: {', '.join(top)}.\n\n"
                + "\n".join(f"- {i} — {s}" for i, s in zip(ids, sums) if s))
        jobs.append((k, cache_key(K, ids), user, r["name"]))

    lock = threading.Lock()

    def work(job):
        k, key, user, old_name = job
        r = name_cluster(user, RENAME_SYS)
        with lock:
            cache[key] = r
        return k, old_name, r

    done = []
    with ThreadPoolExecutor(3) as ex:          # provider limit: ≤3 parallel
        for k, old_name, r in ex.map(work, jobs):
            done.append((k, r))
            print(f"  кластер {k}: {old_name} → {r['name']}", flush=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    db.executemany("UPDATE clusters SET name=?, descr=? WHERE k=?", [(r["name"], r["descr"], k) for k, r in done])
    db.commit()
    print(f"\nобновлено названий: {len(done)} (layout не тронут)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=30)
    ap.add_argument("--rename", action="store_true",
                    help="только переименовать кластеры новым промптом (координаты и состав не пересчитываются)")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    if args.rename:
        rename_only(db)
        return
    K = args.k

    rows = db.execute("SELECT topic, conv, vec FROM emb").fetchall()
    keys = [(r["topic"], r["conv"]) for r in rows]
    X = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    print(f"{len(keys)} векторов, dim={X.shape[1]}, K={K}", flush=True)

    labels = KMeans(n_clusters=K, n_init=10, random_state=42).fit_predict(X)
    xy = umap.UMAP(n_neighbors=20, min_dist=0.15, metric="cosine", random_state=42).fit_transform(X)
    xy = np.asarray(xy, dtype=np.float64)

    # PCA-поворот (собственные векторы ковариации 2×2): длинная ось облака — горизонтально,
    # затем нормируем оси независимо друг от друга → облако заполняет экран 16:10.
    xy = xy - xy.mean(0)
    _, evecs = np.linalg.eigh(np.cov(xy.T))   # eigh: собственные значения по возрастанию
    v = evecs[:, ::-1]                        # столбцы: [главная ось, вторая ось]
    if np.linalg.det(v) < 0:
        v[:, 1] = -v[:, 1]                    # сохраняем ориентацию (без зеркального отражения)
    xy = xy @ v
    mid = (xy.min(0) + xy.max(0)) / 2
    half = np.abs(xy - mid).max(0)            # полуразмах каждой оси
    xy = (xy - mid) / (half + 1e-9) * np.array([1600.0, 1000.0])   # x∈[-1600,1600], y∈[-1000,1000]

    convs = {(r["topic"], r["conv"]): r for r in
             db.execute("SELECT topic, conv, tags, summary FROM convs")}
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    old = {}   # названия прошлой записи по номеру кластера — fallback при промахе кэша без --rename
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clusters'").fetchone():
        for r in db.execute("SELECT k, name, descr FROM clusters"):
            old[r["k"]] = {"name": r["name"], "descr": r["descr"]}

    jobs = []   # (k, key, user)
    for k in range(K):
        idx = np.where(labels == k)[0]
        if not len(idx):
            continue
        cent = X[idx].mean(0)
        cent /= np.linalg.norm(cent) + 1e-9
        near = sorted(idx, key=lambda i: -float(X[i] @ cent))[:30]   # closest to centroid
        ids = [f"{keys[i][0]}-{keys[i][1]}" for i in near]
        sums = [(convs[keys[i]]["summary"] or "")[:200] for i in near]
        tags = Counter()
        for i in np.where(labels == k)[0]:
            for t in json.loads(convs[keys[i]]["tags"] or "[]"):
                tags[t] += 1
        top = [t for t, _ in tags.most_common(15)]
        user = (f"Кластер из {len(idx)} разговоров. Теги: {', '.join(top)}.\n\n"
                + "\n".join(f"- {i} — {s}" for i, s in zip(ids, sums) if s))
        jobs.append((k, cache_key(K, ids), user))

    todo = [(k, key, user) for k, key, user in jobs if key not in cache]
    print(f"названий из кэша: {len(jobs) - len(todo)}, через LLM: {len(todo)}", flush=True)
    if todo:   # LLM при полном пересчёте не зовём — берём старые названия по номеру k
        print(f"ВНИМАНИЕ: кэш не совпал для кластеров {', '.join(str(k) for k, _, _ in todo)}. "
              "Беру старые названия из таблицы clusters по номеру k (состав пересчитался).", flush=True)
        todo = []
    lock = threading.Lock()

    def work(job):
        k, key, user = job
        r = name_cluster(user)
        with lock:
            cache[key] = r
        return k, r

    if todo:
        with ThreadPoolExecutor(3) as ex:          # provider limit: ≤3 parallel
            for k, r in ex.map(work, todo):
                print(f"  кластер {k}: {r['name']}", flush=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    # write tables (recreated on every run)
    db.execute("DROP TABLE IF EXISTS layout")
    db.execute("DROP TABLE IF EXISTS clusters")
    db.execute("CREATE TABLE layout(topic TEXT, conv TEXT, x REAL, y REAL, k INT)")
    db.execute("""CREATE TABLE clusters(k INT PRIMARY KEY, name TEXT, descr TEXT,
                                         n INT, x REAL, y REAL)""")
    db.executemany("INSERT INTO layout VALUES (?,?,?,?,?)",
                   [(keys[i][0], keys[i][1], float(xy[i, 0]), float(xy[i, 1]), int(labels[i]))
                    for i in range(len(keys))])
    cl = []
    for k, key, _ in jobs:
        idx = np.where(labels == k)[0]
        r = cache.get(key) or old.get(k) or {"name": f"Кластер {k}", "descr": ""}
        cl.append((k, r["name"], r["descr"], len(idx),
                   float(np.median(xy[idx, 0])), float(np.median(xy[idx, 1]))))
    db.executemany("INSERT INTO clusters VALUES (?,?,?,?,?,?)", cl)
    db.commit()
    print(f"layout: {len(keys)} строк, clusters: {len(cl)}\n")
    for k, name, _, n, _, _ in sorted(cl, key=lambda c: -c[3]):
        print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
