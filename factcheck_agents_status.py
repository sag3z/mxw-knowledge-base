"""Статус докрутки фактчека агентами: сколько пакетов без результата и как их раздать.

usage: python factcheck_agents_status.py [пакетов_на_задание=15]
Печатает число готовых/недостающих пакетов, проверяет, что out/*.json — валидный JSON
(битые печатает отдельно, их надо удалить и переделать), и выдаёт список заданий:
по строке «XXXX YYYY» — диапазон номеров для одного агента (в диапазоне могут быть уже
готовые пакеты — агент их пропустит).
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent / "data" / "fc_agents"
PER = int(sys.argv[1]) if len(sys.argv) > 1 else 15

idx = json.loads((BASE / "index.json").read_text(encoding="utf-8"))
ids = [it["id"] for it in idx]
bad, missing = [], []
for pid in ids:
    f = BASE / "out" / f"{pid}.json"
    if not f.exists():
        missing.append(pid)
        continue
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        cl = d.get("claims") if isinstance(d, dict) else d
        if not isinstance(cl, list):
            raise ValueError
    except Exception:
        bad.append(pid)
print(f"всего {len(ids)}, готово {len(ids) - len(missing) - len(bad)}, нет {len(missing)}, битых {len(bad)}")
if bad:
    print("БИТЫЕ (удалить и переделать):", " ".join(bad))
todo = sorted(missing + bad)
print(f"заданий по {PER}: {(len(todo) + PER - 1) // PER}")
for i in range(0, len(todo), PER):
    chunk = todo[i:i + PER]
    print(chunk[0], chunk[-1], f"({len(chunk)} шт.: {','.join(chunk)})" if len(chunk) < int(chunk[-1]) - int(chunk[0]) + 1 else "")
