"""Re-split oversized conversations into segments and pack them into batches for re-labeling.

Usage: python split_big.py <data dir> [min_msgs]
Writes <data>/batches_big/<slug>/bNNN.txt with conv ids like c0263-07.
Labels go to <data>/labels_big/<slug>/bNNN.jsonl and override the coarse label of the parent conv.
"""
import re
import sys
from pathlib import Path

SEG_MAX = 120          # messages per segment
SEG_GAP_MIN = 10       # prefer cutting at a pause of >= this many minutes
BATCH_CHARS = 60000
LINE = re.compile(r"^#\d+ (\d\d)-(\d\d) (\d\d):(\d\d) ")


def minutes(line):
    m = LINE.match(line)
    if not m:
        return None
    mo, d, h, mi = map(int, m.groups())
    return ((mo * 31 + d) * 24 + h) * 60 + mi


def segments(lines):
    segs, cur = [], []
    for line in lines:
        if cur and len(cur) >= SEG_MAX // 2:
            a, b = minutes(cur[-1]), minutes(line)
            if len(cur) >= SEG_MAX or (a is not None and b is not None and b - a >= SEG_GAP_MIN):
                segs.append(cur)
                cur = []
        cur.append(line)
    if cur:
        segs.append(cur)
    return segs


def main(data: Path, min_msgs: int):
    for tdir in sorted((data / "batches").iterdir()):
        if not tdir.is_dir():
            continue
        blocks = []
        for b in sorted(tdir.glob("b*.txt")):
            text = b.read_text(encoding="utf-8")
            for conv, n, body in re.findall(r"=== РАЗГОВОР (c\d+) \([^,]*, (\d+) сообщ\.\) ===\n(.*?)(?=^=== РАЗГОВОР|\Z)", text, re.S | re.M):
                if int(n) < min_msgs:
                    continue
                lines = [l for l in body.splitlines() if l.strip()]
                segs = segments(lines)
                for i, s in enumerate(segs, 1):
                    head = f"=== РАЗГОВОР {conv}-{i:02d} (часть {i}/{len(segs)} большого разговора {conv}, {len(s)} сообщ.) ===\n"
                    blocks.append(head + "\n".join(s) + "\n")
        if not blocks:
            continue
        out = data / "batches_big" / tdir.name
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("b*.txt"):
            old.unlink()
        batches, buf, size = [], [], 0
        for blk in blocks:
            if buf and size + len(blk) > BATCH_CHARS:
                batches.append(buf)
                buf, size = [], 0
            buf.append(blk)
            size += len(blk)
        if buf:
            batches.append(buf)
        for j, bt in enumerate(batches, 1):
            (out / f"b{j:03d}.txt").write_text("".join(bt), encoding="utf-8")
        print(f"{tdir.name}: {len(blocks)} segments in {len(batches)} batches")


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 300)
