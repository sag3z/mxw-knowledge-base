"""Вливает результаты агентов-фактчекеров (data/fc_agents/out/NNNN.json) в data/factcheck/*.json.

Раздел заменяется, только если готовы ВСЕ его куски. Вердикты нормализуются в формат
factcheck.py: номера по порядку, evidence — только id из материала пакета, quote сверяется
с материалом (не нашлась у supported → флаг quote_mismatch). Исходные JSON — в
data/fc_agents/orig/ (один раз). В конце пересобирается data/factcheck/report.md.
Повторный запуск безопасен: уже влитые разделы (без "error") не трогаются.
"""
import json
import re
import shutil
from collections import Counter, defaultdict

import factcheck as fc

BASE = fc.ROOT / "data" / "fc_agents"
VALID = set(fc.VERDICTS)
FIX = {"verified": "supported", "true": "supported", "confirmed": "supported",
       "not_supported": "unsupported", "false": "contradicted", "nosource": "no_source"}


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[«»\"'“”„]", "", s or "")).strip().lower()


def load_out(pid):
    f = BASE / "out" / f"{pid}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    claims = d.get("claims") if isinstance(d, dict) else d if isinstance(d, list) else None
    return claims if isinstance(claims, list) else None


def clean(raw, it, material):
    valid_ids = set(it["valid_ids"])
    mat = norm(material)
    out = []
    for c in raw:
        if not isinstance(c, dict) or not (c.get("text") or "").strip():
            continue
        v = str(c.get("verdict") or "").strip().lower()
        v = FIX.get(v, v)
        if v not in VALID:
            v = ""
        ev = []
        for x in c.get("evidence") or []:
            try:
                x = int(str(x).lstrip("#"))
            except ValueError:
                continue
            if x in valid_ids:
                ev.append(x)
        flags = []
        quote = (c.get("quote") or "").strip()[:300]
        if v == "supported" and (not quote or norm(quote)[:80] not in mat):
            flags.append("quote_mismatch")
        if v == "supported" and not ev:
            flags.append("no_evidence")
        out.append({"num": 0, "text": c["text"].strip(), "src": (c.get("src") or "").strip(),
                    "verdict": v, "evidence": ev, "quote": quote,
                    "problem": (c.get("problem") or "").strip(),
                    "hedge_ok": c.get("hedge_ok") is not False, "fix": (c.get("fix") or "").strip(),
                    "flags": flags, "second": None, "correct_source": ""})
    return out


def main():
    index = json.loads((BASE / "index.json").read_text(encoding="utf-8"))
    (BASE / "orig").mkdir(exist_ok=True)
    by_sec = defaultdict(list)
    for it in index:
        by_sec[(it["json"], it["section"])].append(it)
    ready = Counter()
    merged_secs, pending_secs, flags = 0, 0, Counter()
    by_json = defaultdict(dict)
    for (jf, si), items in by_sec.items():
        items.sort(key=lambda x: x["chunk"])
        outs = [load_out(it["id"]) for it in items]
        if any(o is None for o in outs):
            pending_secs += 1
            continue
        claims = []
        mc = {}
        for it, o in zip(items, outs):
            material = (BASE / "in" / f"{it['id']}.md").read_text(encoding="utf-8").split("## МАТЕРИАЛ", 1)[-1]
            claims += clean(o, it, material)
            mc.update(it["msg_conv"])
        for k, c in enumerate(claims, 1):
            c["num"] = k
            for fl in c["flags"]:
                flags[fl] += 1
        by_json[jf][si] = (claims, mc, items)
    for jf, secs in by_json.items():
        f = fc.OUT / jf
        orig = BASE / "orig" / jf
        if not orig.exists():
            shutil.copy2(f, orig)
        d = json.loads(f.read_text(encoding="utf-8"))
        changed = False
        for si, (claims, mc, items) in secs.items():
            sec = d["sections"][si]
            if not sec.get("error"):
                continue                      # уже влито раньше
            cnt = Counter(c["verdict"] for c in claims)
            ids = {str(i) for c in claims for i in c["evidence"]}
            sec.update({"claims": claims, "chunks": len(items), "checked_by": "claude-haiku agents",
                        "summary": ", ".join(f"{v} {k}" for k, v in cnt.most_common()) or "утверждений нет",
                        "msg_conv": {i: mc[i] for i in ids if i in mc}})
            sec.pop("error", None)
            sec.pop("second_error", None)
            merged_secs += 1
            changed = True
        if changed:
            d["stats"] = fc.stats_of(d["sections"])
            d["agents"] = "claude-haiku"
            f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            ready[jf] += 1
    fc.make_report()
    left = sum(1 for (jf, si), items in by_sec.items()
               if json.loads((fc.OUT / jf).read_text(encoding="utf-8"))["sections"][si].get("error"))
    print(f"влито разделов: {merged_secs}; статей обновлено: {len(ready)}; "
          f"ещё ждут агентов: {left} разделов; флаги: {dict(flags)}")


if __name__ == "__main__":
    main()
