"""Докрутка проверки фактов агентами: пакеты для разделов, упавших с ошибкой LLM.

Для каждой статьи из data/factcheck/*.json, у которой есть разделы с "error", собирает те же
куски и материал, что factcheck.py (prepare_article, без LLM), и пишет по пакету на кусок
в data/fc_agents/in/NNNN.md + индекс data/fc_agents/index.json. Пакеты раскладываются по
партиям (batches.json) примерно равного объёма — партия = одно задание агенту.
Результаты агентов (data/fc_agents/out/NNNN.json) вливает factcheck_haiku_merge.py.
"""
import json
import sys
from pathlib import Path

import factcheck as fc

ROOT = fc.ROOT
BASE = ROOT / "data" / "fc_agents"
BATCH_CHARS = int(sys.argv[1]) if len(sys.argv) > 1 else 160_000   # символов материала на партию


def failed_articles():
    for f in sorted(fc.OUT.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or "sections" not in d:
            continue
        bad = [i for i, s in enumerate(d["sections"]) if s.get("error") or not s.get("claims")]
        if any(d["sections"][i].get("error") for i in bad):
            yield f, d, set(bad)


def main():
    (BASE / "in").mkdir(parents=True, exist_ok=True)
    (BASE / "out").mkdir(parents=True, exist_ok=True)
    index, n = [], 0
    for f, d, bad in failed_articles():
        path = ROOT / d["article"]
        if not path.exists():
            print("нет статьи:", d["article"])
            continue
        art = fc.prepare_article(path)
        if len(art["sections"]) != len(d["sections"]):
            print("разделы не совпали, пропуск:", d["article"])
            continue
        for si, sec in enumerate(art["sections"]):
            if si not in bad or not d["sections"][si].get("error"):
                continue
            for ci, ch in enumerate(sec["chunks"]):
                n += 1
                pid = f"{n:04d}"
                body = (f"# ПАКЕТ {pid}\n\nСтатья: {art['title']}\nРаздел: {sec['heading']}\n\n"
                        f"## ТЕКСТ РАЗДЕЛА\n\n{ch['text']}\n\n## МАТЕРИАЛ\n\n"
                        f"{ch['material'] or '(у этого куска нет ссылок [[...]] на разговоры — материала нет)'}\n")
                (BASE / "in" / f"{pid}.md").write_text(body, encoding="utf-8")
                index.append({"id": pid, "json": f.name, "section": si, "chunk": ci,
                              "heading": sec["heading"], "convs": ch["convs"],
                              "valid_ids": sorted(int(x) for x in ch["valid_ids"]),
                              "msg_conv": {str(k): v for k, v in ch["msg_conv"].items()},
                              "chars": len(body)})
    (BASE / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    # партии: подряд по индексу, пока не наберётся BATCH_CHARS
    batches, cur, size = [], [], 0
    for it in index:
        if cur and size + it["chars"] > BATCH_CHARS:
            batches.append(cur); cur, size = [], 0
        cur.append(it["id"]); size += it["chars"]
    if cur:
        batches.append(cur)
    (BASE / "batches.json").write_text(json.dumps(batches), encoding="utf-8")
    tot = sum(it["chars"] for it in index)
    print(f"пакетов: {len(index)}, символов: {tot:,}, партий: {len(batches)}")


if __name__ == "__main__":
    main()
