"""Prefilter parsed messages (bots, sticker-only) and pack conversations into batch files for the LLM.

Usage: python make_batches.py <data dir>
Reads  <data>/messages/*.jsonl
Writes <data>/batches/<slug>/bNNN.txt, <data>/bot_alerts/<slug>.jsonl, <data>/batches/manifest.json
"""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BOTS = {"MXW", "MXW NFT", "MXW Predict", "MaxyTools Funding"}
CONV_GAP = timedelta(minutes=45)
BATCH_CHARS = 60000


def is_junk(m) -> bool:
    if m["text"].strip():
        return False
    return all(x.startswith("[стикер") for x in m["media"]) if m["media"] else True


def render(m, root) -> str:
    head = f"#{m['id']} {m['ts'][5:16]} {m['author']}"
    if m["fwd_from"]:
        head += f" (переслано от {m['fwd_from']})"
    if m["reply_to"] and m["reply_to"] != root:
        head += f" (ответ на #{m['reply_to']})"
    body = " ".join(m["media"] + ([m["text"]] if m["text"] else []))
    return head + ": " + body.replace("\n", " / ")


def main(data: Path):
    out, bots_dir = data / "batches", data / "bot_alerts"
    out.mkdir(exist_ok=True)
    bots_dir.mkdir(exist_ok=True)
    manifest = []
    for f in sorted((data / "messages").glob("*.jsonl")):
        slug = f.stem
        msgs = [json.loads(l) for l in f.open(encoding="utf-8")]
        topic_title = None
        # the topic root is the most common reply target (Telegram forum topics)
        root = Counter(m["reply_to"] for m in msgs if m["reply_to"]).most_common(1)
        root = root[0][0] if root else None
        bot = [m for m in msgs if m["author"] in BOTS]
        with open(bots_dir / f"{slug}.jsonl", "w", encoding="utf-8") as fh:
            for m in bot:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        human = [m for m in msgs if m["author"] not in BOTS and not is_junk(m)]

        convs, cur, prev = [], [], None
        for m in human:
            ts = datetime.fromisoformat(m["ts"])
            if cur and ts - prev > CONV_GAP:
                convs.append(cur)
                cur = []
            cur.append(m)
            prev = ts
        if cur:
            convs.append(cur)

        tdir = out / slug
        tdir.mkdir(exist_ok=True)
        for old in tdir.glob("b*.txt"):
            old.unlink()
        batches, buf, size = [], [], 0
        for i, c in enumerate(convs, 1):
            block = f"=== РАЗГОВОР c{i:04d} ({c[0]['ts'][:16]} — {c[-1]['ts'][11:16]}, {len(c)} сообщ.) ===\n"
            block += "\n".join(render(m, root) for m in c) + "\n"
            if buf and size + len(block) > BATCH_CHARS:
                batches.append(buf)
                buf, size = [], 0
            buf.append(block)
            size += len(block)
        if buf:
            batches.append(buf)
        for j, b in enumerate(batches, 1):
            (tdir / f"b{j:03d}.txt").write_text("".join(b), encoding="utf-8")
        chars = sum(len(x) for b in batches for x in b)
        manifest.append({"slug": slug, "messages": len(msgs), "bot": len(bot),
                         "dropped_junk": len(msgs) - len(bot) - len(human),
                         "kept_for_llm": len(human), "conversations": len(convs),
                         "batches": len(batches), "chars": chars})
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tot = Counter()
    for r in manifest:
        print(f"{r['slug']:<32} msgs={r['messages']:>6} bot={r['bot']:>5} junk={r['dropped_junk']:>5} "
              f"llm={r['kept_for_llm']:>6} convs={r['conversations']:>5} batches={r['batches']:>3} chars={r['chars']:>8}")
        tot.update({k: v for k, v in r.items() if isinstance(v, int)})
    print("TOTAL", dict(tot))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
