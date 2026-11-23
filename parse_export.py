"""Parse Telegram Desktop HTML exports into per-topic JSONL + compact text chunks.

Usage: python -I parse_export.py "<export root>" "<output dir>"
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

CHUNK_GAP = timedelta(minutes=45)   # new conversation chunk after this silence
CHUNK_MAX_CHARS = 12000             # hard cap per chunk


def page_key(p: Path) -> int:
    m = re.search(r"messages(\d*)\.html$", p.name)
    return int(m.group(1) or 1)


def clean(el) -> str:
    if el is None:
        return ""
    for br in el.find_all("br"):
        br.replace_with("\n")
    return re.sub(r"[ \t]+", " ", el.get_text()).strip()


def media_label(body) -> list[str]:
    out = []
    for m in body.select("div.media"):
        cls = " ".join(m.get("class", []))
        title = clean(m.select_one(".title"))
        status = clean(m.select_one(".status"))
        kind = {
            "media_voice_message": "голосовое",
            "media_audio_file": "аудио",
            "media_file": "файл",
            "media_photo": "фото",
            "media_video": "видео",
        }
        k = next((v for c, v in kind.items() if c in cls), "медиа")
        if title.lower().startswith("video message") or "round" in cls:
            k = "кружок"
        if title.lower() == "sticker" or "sticker" in title.lower():
            k = "стикер"
        extra = " ".join(x for x in (title, status) if x and x.lower() not in ("photo", "video file", "voice message", "video message", "sticker"))
        out.append(f"[{k}{': ' + extra if extra else ''}]")
    for _ in body.select("a.photo_wrap"):
        out.append("[фото]")
    for v in body.select("a.video_file_wrap"):
        out.append(f"[видео {clean(v.select_one('.video_duration'))}]".replace(" ]", "]"))
    for p in body.select("div.media_poll"):
        out.append(f"[опрос: {clean(p)[:300]}]")
    return out


def parse_topic(folder: Path):
    pages = sorted(folder.glob("messages*.html"), key=page_key)
    first = BeautifulSoup(pages[0].read_text(encoding="utf-8"), "lxml")
    title = clean(first.select_one(".page_header .text.bold")) or folder.name
    msgs, last_author = [], None
    for page in pages:
        soup = first if page == pages[0] else BeautifulSoup(page.read_text(encoding="utf-8"), "lxml")
        for div in soup.select("div.message.default"):
            mid = int(div["id"].replace("message", ""))
            body = div.select_one(":scope > div.body")
            date_el = body.select_one("div.date.details")
            ts = datetime.strptime(date_el["title"][:19], "%d.%m.%Y %H:%M:%S")
            fwd = body.select_one("div.forwarded.body")
            author_el = body.select_one(":scope > div.from_name")
            if author_el:
                last_author = clean(author_el)
            reply = body.select_one("div.reply_to a")
            reply_to = int(re.search(r"\d+", reply["href"]).group()) if reply and reply.get("href") else None
            src = fwd or body
            text = clean(src.select_one("div.text"))
            media = media_label(src)
            msgs.append({
                "id": mid,
                "ts": ts.isoformat(sep=" "),
                "author": last_author,
                "fwd_from": clean(fwd.select_one("div.from_name")) if fwd else None,
                "reply_to": reply_to,
                "text": text,
                "media": media,
            })
    return title, msgs


def render(m) -> str:
    parts = [f"#{m['id']} {m['ts'][5:16]} {m['author']}"]
    if m["fwd_from"]:
        parts.append(f"(переслано от {m['fwd_from']})")
    if m["reply_to"]:
        parts.append(f"(ответ на #{m['reply_to']})")
    body = " ".join(m["media"] + ([m["text"]] if m["text"] else []))
    return " ".join(parts) + ": " + body.replace("\n", " / ")


def chunk(msgs):
    chunks, cur, size, prev = [], [], 0, None
    for m in msgs:
        ts = datetime.fromisoformat(m["ts"])
        line = render(m)
        if cur and (ts - prev > CHUNK_GAP or size + len(line) > CHUNK_MAX_CHARS):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
        prev = ts
    if cur:
        chunks.append(cur)
    return chunks


def slug(s: str) -> str:
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE).strip("_").lower()
    return s[:40]


def main(root: Path, out: Path):
    topics: dict[str, dict[int, dict]] = {}
    for folder in sorted(root.glob("ChatExport*")):
        title, msgs = parse_topic(folder)
        bucket = topics.setdefault(title, {})
        before = len(bucket)
        for m in msgs:
            bucket.setdefault(m["id"], m)
        print(f"{folder.name}: '{title}' {len(msgs)} msgs, +{len(bucket) - before} new")

    (out / "messages").mkdir(parents=True, exist_ok=True)
    (out / "chunks").mkdir(parents=True, exist_ok=True)
    index = []
    for title, bucket in topics.items():
        msgs = sorted(bucket.values(), key=lambda m: m["id"])
        s = slug(title)
        with open(out / "messages" / f"{s}.jsonl", "w", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        chunks = chunk(msgs)
        chunk_dir = out / "chunks" / s
        chunk_dir.mkdir(exist_ok=True)
        total_chars = 0
        for i, c in enumerate(chunks, 1):
            text = "\n".join(c)
            total_chars += len(text)
            (chunk_dir / f"{i:04d}.txt").write_text(f"ВЕТКА: {title}\nЧАНК {i}/{len(chunks)}\n\n{text}\n", encoding="utf-8")
        index.append({"topic": title, "slug": s, "messages": len(msgs), "chunks": len(chunks), "chars": total_chars})
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    for t in index:
        print(f"{t['topic']:<40} msgs={t['messages']:>6} chunks={t['chunks']:>5} chars={t['chars']:>9}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
