"""Validate the schemes pass (labels_abuse) against batches_abuse and print stats.

Usage: python check_schemes.py <data dir>
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

TYPES = {"multiaccount", "antisybil", "farms", "accounts", "bots_soft",
         "kyc_verification", "proxy_antidetect", "bans_risks"}


def main(data: Path):
    for tdir in sorted((data / "batches_abuse").iterdir()):
        stats, types, problems = Counter(), Counter(), []
        for batch in sorted(tdir.glob("b*.txt")):
            text = batch.read_text(encoding="utf-8")
            convs = re.findall(r"=== РАЗГОВОР (c[\d-]+)", text)
            ids = {int(x) for x in re.findall(r"^#(\d+) ", text, flags=re.M)}
            lf = data / "labels_abuse" / tdir.name / f"{batch.stem}.jsonl"
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
                stats["schemes" if r.get("has_scheme") else "none"] += 1
                if r.get("has_scheme"):
                    types.update(r.get("scheme_types", []))
                    bad_t = [t for t in r.get("scheme_types", []) if t not in TYPES]
                    if bad_t:
                        problems.append(f"{batch.stem}:{n} неизвестные scheme_types {bad_t}")
                    bad = [i for i in r.get("key_msgs", []) if i not in ids]
                    if bad:
                        problems.append(f"{batch.stem}:{n} key_msgs не из этого файла: {bad[:5]}")
                    if not r.get("summary"):
                        problems.append(f"{batch.stem}:{n} пустой summary")
            missing = [c for c in convs if c not in seen]
            if missing:
                problems.append(f"{batch.stem}: пропущены {len(missing)}: {missing[:8]}")
        print(f"== {tdir.name}: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        print("   типы: " + ", ".join(f"{k}={v}" for k, v in types.most_common()))
        for p in problems[:30]:
            print("   !", p)
        if not problems:
            print("   OK")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
