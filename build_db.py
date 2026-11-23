"""Build data/kb.sqlite — the single source for the Obsidian vault and the map app.

Usage: python build_db.py <data dir>
Tables:
  messages(topic, id, ts, author, fwd_from, reply_to, text, media, media_text, is_bot)
  convs(topic, conv, parent, start_ts, end_ts, n_msgs, msg_ids, verdict, category, tags, key_msgs, summary,
        has_scheme, scheme_types, scheme_summary, label_src)
  notes(topic, msg, kind, title, body)            -- long media notes
  topics(slug, title, n_msgs)
"""
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

BOTS = {"MXW", "MXW NFT", "MXW Predict", "MaxyTools Funding"}
EXCLUDE = {("жизнь_флуд_кайф", "c0175")}
BLOCK = re.compile(r"=== РАЗГОВОР (c[\d-]+) .*?===\n(.*?)(?=^=== РАЗГОВОР|\Z)", re.S | re.M)


def jl(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def labels(data: Path, d: str):
    out = {}
    for f in (data / d).glob("*/*.jsonl"):
        for r in jl(f):
            out[(f.parent.name, r["conv"])] = r
    return out


def conv_msgs(data: Path, d: str):
    out = {}
    for f in (data / d).glob("*/b*.txt"):
        for conv, body in BLOCK.findall(f.read_text(encoding="utf-8")):
            out[(f.parent.name, conv)] = [int(x) for x in re.findall(r"^#(\d+) ", body, re.M)]
    return out


def main(data: Path):
    db_path = data / "kb.sqlite"
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript("""
    CREATE TABLE topics(slug TEXT PRIMARY KEY, title TEXT, n_msgs INT);
    CREATE TABLE messages(topic TEXT, id INT, ts TEXT, author TEXT, fwd_from TEXT, reply_to INT, text TEXT,
                          media TEXT, media_text TEXT, is_bot INT, PRIMARY KEY(topic, id));
    CREATE TABLE convs(topic TEXT, conv TEXT, parent TEXT, start_ts TEXT, end_ts TEXT, n_msgs INT, msg_ids TEXT,
                       verdict TEXT, category TEXT, tags TEXT, key_msgs TEXT, summary TEXT,
                       has_scheme INT, scheme_types TEXT, scheme_summary TEXT, label_src TEXT,
                       PRIMARY KEY(topic, conv));
    CREATE TABLE notes(topic TEXT, msg INT, kind TEXT, title TEXT, body TEXT);
    """)

    titles = {t["slug"]: t["topic"] for t in json.loads((data / "index.json").read_text(encoding="utf-8"))}
    media = defaultdict(list)
    for f in (data / "media_text").glob("*.jsonl"):
        for r in jl(f):
            media[(r["topic"], r["msg"])].append({"kind": r["kind"], "file": Path(r["path"]).name, "text": r["text"]})

    for f in sorted((data / "messages").glob("*.jsonl")):
        slug = f.stem
        rows = jl(f)
        db.execute("INSERT INTO topics VALUES(?,?,?)", (slug, titles.get(slug, slug), len(rows)))
        db.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)", [
            (slug, m["id"], m["ts"], m["author"], m["fwd_from"], m["reply_to"], m["text"],
             json.dumps(m["media"], ensure_ascii=False),
             json.dumps(media.get((slug, m["id"]), []), ensure_ascii=False) if (slug, m["id"]) in media else None,
             int(m["author"] in BOTS)) for m in rows])

    base, big, abuse, med = (labels(data, d) for d in ("labels", "labels_big", "labels_abuse", "labels_media"))
    msgs = conv_msgs(data, "batches")
    msgs.update(conv_msgs(data, "batches_big"))
    parents = {(t, c.split("-")[0]) for (t, c) in big}
    final = {k: (v, "labels") for k, v in base.items() if k not in parents}
    final.update({k: (v, "labels_big") for k, v in big.items()})
    final.update({k: (v, "labels_media") for k, v in med.items()})
    ts = {(t, i): s for t, i, s in db.execute("SELECT topic, id, ts FROM messages")}
    n = 0
    for (topic, conv), (r, src) in sorted(final.items()):
        if (topic, conv) in EXCLUDE or (topic, conv.split("-")[0]) in EXCLUDE:
            continue
        ids = msgs.get((topic, conv), [])
        sch = med.get((topic, conv)) or abuse.get((topic, conv)) or {}
        has_s = bool(sch.get("has_scheme"))
        db.execute("INSERT INTO convs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            topic, conv, conv.split("-")[0] if "-" in conv else None,
            ts.get((topic, ids[0])) if ids else None, ts.get((topic, ids[-1])) if ids else None, len(ids),
            json.dumps(ids), r.get("verdict"), r.get("category"), json.dumps(r.get("tags", []), ensure_ascii=False),
            json.dumps(r.get("key_msgs", [])), r.get("summary", ""),
            int(has_s), json.dumps(sch.get("scheme_types", []) if has_s else []),
            (sch.get("scheme_summary") or sch.get("summary", "")) if has_s else "", src))
        n += 1

    for f in (data / "long_media_notes").glob("*/*.md"):
        body = f.read_text(encoding="utf-8")
        msg, kind = f.stem.split("__", 1)
        title = (re.search(r"^#\s+(.+)$", body, re.M) or [None, f.stem])[1]
        db.execute("INSERT INTO notes VALUES(?,?,?,?,?)", (f.parent.name, int(msg), kind, title, body))

    db.execute("CREATE INDEX convs_cat ON convs(category)")
    db.commit()
    for row in db.execute("SELECT verdict, COUNT(*), SUM(has_scheme) FROM convs GROUP BY verdict"):
        print("verdict", row)
    print("convs:", n, "| messages:", db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
          "| with media text:", db.execute("SELECT COUNT(*) FROM messages WHERE media_text IS NOT NULL").fetchone()[0],
          "| notes:", db.execute("SELECT COUNT(*) FROM notes").fetchone()[0])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
