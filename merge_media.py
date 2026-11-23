"""Inline media transcripts/descriptions into conversation batches and select conversations to re-label.

Usage: python merge_media.py <data dir>
Reads  <data>/media_text/*.jsonl, <data>/batches, <data>/batches_big, <data>/labels*
Writes <data>/batches_media/<slug>/bNNN.txt   conversations whose meaning may change with media
       <data>/long_media/<slug>__<msg>__<kind>.txt   transcripts too long to inline (handled as standalone docs)
"""
import json
import re
from collections import defaultdict
from pathlib import Path
import sys

INLINE_MAX = 8000          # longer transcripts become standalone documents
PHOTO_TEXT_MAX = 900
BATCH_CHARS = 60000
SPEECH_KINDS = {"voice_messages", "round_video_messages", "video_files"}
LOW_INFO_PHOTO = re.compile(r"ТИП:\s*(мем|фото|NFT-арт)", re.I)
BLOCK = re.compile(r"(=== РАЗГОВОР (c[\d-]+) .*?===\n)(.*?)(?=^=== РАЗГОВОР|\Z)", re.S | re.M)
EXCLUDE = {("жизнь_флуд_кайф", "c0175")}


def label_kind(r):
    k, ext = r["kind"], Path(r["path"]).suffix.lower()
    if k == "files":
        if ext in {".mp3", ".wav", ".m4a", ".ogg"}:
            return "аудиофайл", True
        if ext in {".zip", ".7z"}:
            return "архив", False
        return f"файл {Path(r['path']).name}", False
    return {"voice_messages": "голосовое", "round_video_messages": "кружок",
            "video_files": "видео", "photos": "фото"}[k], k in SPEECH_KINDS


def load_media(data: Path):
    media = defaultdict(list)
    for f in (data / "media_text").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                media[(r["topic"], r["msg"])].append(r)
    return media


def render_media(r, long_dir: Path):
    name, speech = label_kind(r)
    text = r["text"].strip()
    if r["kind"] == "photos":
        text = re.sub(r"\s*\n\s*", " / ", text)[:PHOTO_TEXT_MAX]
    elif len(text) > INLINE_MAX:
        doc = long_dir / r["topic"] / f"{r['msg']}__{r['kind']}.txt"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(f"ВЕТКА: {r['topic']}\nСООБЩЕНИЕ: #{r['msg']}\nТИП: {name}\n\n{text}\n", encoding="utf-8")
        text = re.sub(r"\s+", " ", text[:1500]) + f" … [полный текст {len(text)} симв. вынесен в отдельный документ]"
    else:
        text = re.sub(r"\s*\n\s*", " / ", text)
    return f" ⟪{name}: {text}⟫", speech and "[нет речи]" not in r["text"]


def blocks(folder: Path):
    for b in sorted(folder.glob("b*.txt")):
        for head, conv, body in BLOCK.findall(b.read_text(encoding="utf-8")):
            yield conv, head, body


def load_verdicts(data: Path):
    """(slug, conv) -> verdict from the first pass; re-split segments override nothing here (own ids)."""
    v = {}
    for ld in ("labels", "labels_big"):
        for f in (data / ld).glob("*/*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    v[(f.parent.name, r["conv"])] = r["verdict"]
    return v


def main(data: Path):
    media = load_media(data)
    verdicts = load_verdicts(data)
    long_dir = data / "long_media"
    out_root = data / "batches_media"
    for tdir in sorted((data / "batches").iterdir()):
        if not tdir.is_dir():
            continue
        slug = tdir.name
        big_dir = data / "batches_big" / slug
        big = list(blocks(big_dir)) if big_dir.exists() else []
        big_parents = {c.split("-")[0] for c, _, _ in big}
        chosen, stats = [], defaultdict(int)
        for conv, head, body in list(blocks(tdir)) + big:
            if conv in big_parents or (slug, conv) in EXCLUDE:
                continue
            lines, has_speech, info_photos = [], False, 0
            for line in body.splitlines():
                m = re.match(r"^#(\d+) ", line)
                extra = ""
                if m:
                    for r in media.get((slug, int(m.group(1))), []):
                        s, speech = render_media(r, long_dir)
                        extra += s
                        has_speech |= speech
                        if r["kind"] == "photos" and not LOW_INFO_PHOTO.search(r["text"]):
                            info_photos += 1
                lines.append(line + extra)
            # re-label when speech appears; screenshots only matter for conversations dropped blind
            # (useful conversations are re-read with media at KB build time anyway)
            if has_speech or (info_photos >= 1 and verdicts.get((slug, conv)) == "drop"):
                stats["speech" if has_speech else "photos"] += 1
                chosen.append(head + "\n".join(lines) + "\n")
        out = out_root / slug
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("b*.txt"):
            old.unlink()
        batches, buf, size = [], [], 0
        for blk in chosen:
            if buf and size + len(blk) > BATCH_CHARS:
                batches.append(buf)
                buf, size = [], 0
            buf.append(blk)
            size += len(blk)
        if buf:
            batches.append(buf)
        for j, bt in enumerate(batches, 1):
            (out / f"b{j:03d}.txt").write_text("".join(bt), encoding="utf-8")
        print(f"{slug}: {len(chosen)} convs (речь: {stats['speech']}, только скрины: {stats['photos']}), "
              f"{len(batches)} batches, {sum(map(len, chosen))} chars")
    n = len(list(long_dir.rglob("*.txt"))) if long_dir.exists() else 0
    print(f"длинных документов: {n}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
