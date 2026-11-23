"""Validate LLM labels against batch files and print stats.

Usage: python check_labels.py <data dir> [--big|--media] [slug ...]
--big checks batches_big/labels_big (re-split oversized conversations).
--media checks batches_media/labels_media (re-label with transcribed media).
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

CATEGORIES = {"drops_testnets", "nft", "trading", "predictions", "ai_dev", "tools",
              "jobs_income", "security", "community", "market_news", "other"}
VERDICTS = {"keep", "partial", "drop"}


def main(data: Path, slugs: list[str], mode: str = ""):
    bdir, ldir = {"--big": ("batches_big", "labels_big"),
                  "--media": ("batches_media", "labels_media")}.get(mode, ("batches", "labels"))
    slugs = slugs or sorted(p.name for p in (data / ldir).iterdir() if p.is_dir())
    for slug in slugs:
        verdicts, cats, problems = Counter(), Counter(), []
        for batch in sorted((data / bdir / slug).glob("b*.txt")):
            text = batch.read_text(encoding="utf-8")
            convs = re.findall(r"=== РАЗГОВОР (c[\d-]+)", text)
            ids = {int(x) for x in re.findall(r"^#(\d+) ", text, flags=re.M)}
            lf = data / ldir / slug / f"{batch.stem}.jsonl"
            if not lf.exists():
                problems.append(f"{batch.stem}: нет файла разметки")
                continue
            seen = []
            for n, line in enumerate(lf.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError as e:
                    problems.append(f"{batch.stem}:{n} битый JSON ({e.msg})")
                    continue
                seen.append(r.get("conv"))
                v = r.get("verdict")
                verdicts[v] += 1
                if v not in VERDICTS:
                    problems.append(f"{batch.stem}:{n} verdict={v!r}")
                if v != "drop":
                    cats[r.get("category")] += 1
                    if r.get("category") not in CATEGORIES:
                        problems.append(f"{batch.stem}:{n} category={r.get('category')!r}")
                    bad = [i for i in r.get("key_msgs", []) if i not in ids]
                    if bad:
                        problems.append(f"{batch.stem}:{n} key_msgs не из этого файла: {bad[:5]}")
                    if not r.get("summary"):
                        problems.append(f"{batch.stem}:{n} пустой summary у {v}")
            missing = [c for c in convs if c not in seen]
            extra = [c for c in seen if c not in convs]
            if missing:
                problems.append(f"{batch.stem}: пропущены {len(missing)} разговоров: {missing[:8]}")
            if extra:
                problems.append(f"{batch.stem}: лишние conv: {extra[:8]}")
        total = sum(verdicts.values())
        print(f"== {slug}: {total} разговоров | " + ", ".join(f"{k}={v}" for k, v in verdicts.most_common()))
        print("   категории: " + ", ".join(f"{k}={v}" for k, v in cats.most_common()))
        for p in problems[:30]:
            print("   !", p)
        if len(problems) > 30:
            print(f"   ... и ещё {len(problems) - 30} проблем")
        if not problems:
            print("   OK")


if __name__ == "__main__":
    args = sys.argv[2:]
    flags = [a for a in args if a.startswith("--")]
    main(Path(sys.argv[1]), [a for a in args if not a.startswith("--")], flags[0] if flags else "")
