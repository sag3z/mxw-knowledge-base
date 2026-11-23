"""Transcribe/describe chat media via OpenRouter (Gemini): voice, round videos, videos, audio files, photos.

Usage: python transcribe_media.py <data dir> [--kinds voice_messages,photos,...] [--limit N] [--workers 8]
Reads  <data>/media_index.jsonl  (built from the HTML export; one line per media item: topic, msg, kind, path)
Writes <data>/media_text/<kind>.jsonl  (appends; already processed paths are skipped, so it can be resumed)
API key: <project>/.openrouter_key (single line) or env OPENROUTER_API_KEY.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MODEL = "qwen/qwen3.8-omni-flash"   # account allows only Alibaba/DeepInfra providers
URL = "https://openrouter.ai/api/v1/chat/completions"
AUDIO_CHUNK_SEC = 600
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac"}
TEXT_EXT = {".md", ".txt", ".py", ".csv", ".html", ".json"}

P_SPEECH = ("Это голосовое/аудио из русскоязычного крипто-чата. Транскрибируй речь дословно, на языке оригинала, "
            "с пунктуацией. Мат и сленг не цензурируй. Названия проектов, монет, сервисов пиши как принято (латиницей). "
            "Если говорят несколько человек — помечай смену говорящего «—». Без вступлений и комментариев. "
            "Если речи нет — ответь ровно: [нет речи]")
P_VIDEO = ("Это видео из русскоязычного крипто-чата: даны кадры и звуковая дорожка. Ответь в формате:\n"
           "РЕЧЬ: дословная транскрипция (или [нет речи])\n"
           "НА ЭКРАНЕ: что показано — сайты, интерфейсы, графики, весь читаемый текст на экране; "
           "если это мем/развлекательное видео — одной фразой.")
P_ROUND = ("Это видео-кружочек из Telegram: дан кадр и звук. Транскрибируй речь дословно на языке оригинала "
           "(или [нет речи]). Последней строкой — «КАДР: ...» с кратким описанием, только если в кадре есть что-то "
           "информативное (экран, текст), иначе не пиши.")
P_PHOTO = ("Это картинка из русскоязычного крипто-чата. Ответь в формате:\n"
           "ТИП: скрин твита / скрин сайта / скрин чата / график / интерфейс кошелька или биржи / NFT-арт / мем / фото / другое\n"
           "ТЕКСТ: весь значимый текст с картинки дословно (ники, цифры, цены, даты, ссылки, названия); "
           "если текста нет — «—»\n"
           "СУТЬ: 1–2 предложения, что изображено и зачем это могли скинуть.\n"
           "Для мемов и личных фото без информации — коротко.")

lock = threading.Lock()


def key() -> str:
    f = Path(__file__).with_name(".openrouter_key")
    k = f.read_text(encoding="utf-8").strip() if f.exists() else os.environ.get("OPENROUTER_API_KEY", "")
    if not k:
        sys.exit("Нет ключа: положи его в .openrouter_key рядом со скриптом")
    return k


def call(content, api_key, retries=5):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": content}],
                       "temperature": 0, "max_tokens": 16000}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(URL, data=body, headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "X-Title": "mxw-kb media"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.loads(r.read())
            if "choices" not in d:
                raise RuntimeError(str(d.get("error", d))[:300])
            u = d.get("usage", {})
            return d["choices"][0]["message"]["content"] or "", u.get("cost") or 0.0
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code in (400, 401, 402, 403):
                raise RuntimeError(f"HTTP {e.code}: {msg}")
            err = f"HTTP {e.code}: {msg}"
        except Exception as e:  # network / timeout / malformed
            err = str(e)[:300]
        time.sleep(2 ** attempt * 3)
    raise RuntimeError(err)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=1800)


def duration(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=120).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def audio_chunks(src, tmp: Path):
    """Extract mono 16 kHz mp3 chunks of AUDIO_CHUNK_SEC. Returns [] if the file has no audio stream."""
    dur = duration(src)
    chunks, start, i = [], 0.0, 0
    while start < max(dur, 0.1):
        out = tmp / f"a{i:03d}.mp3"
        try:
            ff("-ss", str(start), "-t", str(AUDIO_CHUNK_SEC), "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
               "-b:a", "32k", str(out))
        except subprocess.CalledProcessError:
            return []
        if out.exists() and out.stat().st_size > 1000:
            chunks.append(out)
        start += AUDIO_CHUNK_SEC
        i += 1
    return chunks


def frames(src, tmp: Path, n: int):
    dur = duration(src) or 1
    out = []
    for i in range(n):
        f = tmp / f"f{i}.jpg"
        try:
            ff("-ss", str(dur * (i + 0.5) / n), "-i", str(src), "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", str(f))
        except subprocess.CalledProcessError:
            continue
        if f.exists():
            out.append(f)
    return out


def img_part(f: Path):
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64(f)}"}}


def aud_part(f: Path):
    return {"type": "input_audio", "input_audio": {"data": b64(f), "format": "mp3"}}


def process(item, api_key):
    src, kind = Path(item["path"]), item["kind"]
    ext = src.suffix.lower()
    cost = 0.0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if kind == "photos" or (kind == "files" and ext in {".png", ".jpg", ".jpeg", ".webp"}):
            img = tmp / "i.jpg"
            ff("-i", str(src), "-vf", "scale='min(1280,iw)':-2", str(img))
            text, cost = call([{"type": "text", "text": P_PHOTO}, img_part(img)], api_key)
        elif kind == "files" and ext in TEXT_EXT:
            text = src.read_text(encoding="utf-8", errors="replace")[:20000]
        elif kind == "files" and ext in {".zip", ".7z"}:
            text = list_archive(src)
        elif kind in ("voice_messages",) or (kind == "files" and ext in AUDIO_EXT):
            parts = []
            for ch in audio_chunks(src, tmp):
                t, c = call([{"type": "text", "text": P_SPEECH}, aud_part(ch)], api_key)
                parts.append(t.strip())
                cost += c
            text = "\n".join(parts) or "[нет речи]"
        elif kind == "round_video_messages":
            ch = audio_chunks(src, tmp)
            fr = frames(src, tmp, 1)
            content = [{"type": "text", "text": P_ROUND}] + [img_part(f) for f in fr] + [aud_part(c) for c in ch[:1]]
            text, cost = call(content, api_key)
        elif kind == "video_files":
            chunks = audio_chunks(src, tmp)
            fr = frames(src, tmp, 4 if duration(src) < 600 else 8)
            if len(chunks) <= 1:
                content = [{"type": "text", "text": P_VIDEO}] + [img_part(f) for f in fr] + [aud_part(c) for c in chunks]
                text, cost = call(content, api_key)
            else:   # long video: frames once, then speech chunk by chunk
                t0, cost = call([{"type": "text", "text": P_VIDEO + "\nЗвука в этом запросе нет — опиши только экран."}]
                                + [img_part(f) for f in fr], api_key)
                speech = []
                for ch in chunks:
                    t, c = call([{"type": "text", "text": P_SPEECH}, aud_part(ch)], api_key)
                    speech.append(t.strip())
                    cost += c
                text = "РЕЧЬ: " + "\n".join(speech) + "\n" + t0
        else:
            text = f"[файл {src.name}, не обрабатывался]"
    return text.strip(), cost


def list_archive(src: Path) -> str:
    import zipfile
    if src.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            return f"[архив {src.name}: не читается]"
        return f"[архив {src.name}, {len(names)} файлов]\n" + "\n".join(names[:200])
    return f"[архив {src.name}: 7z, содержимое не просматривалось]"


def main():
    global MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--kinds", default="voice_messages,round_video_messages,files,video_files,photos")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    MODEL = a.model
    data = Path(a.data)
    out_dir = data / "media_text"
    out_dir.mkdir(exist_ok=True)
    items = [json.loads(l) for l in (data / "media_index.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    kinds = a.kinds.split(",")
    done = set()
    for k in kinds:
        f = out_dir / f"{k}.jsonl"
        if f.exists():
            done |= {json.loads(l)["path"] for l in f.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [x for x in items if x["kind"] in kinds and x["path"] not in done]
    todo.sort(key=lambda x: kinds.index(x["kind"]))
    if a.limit:
        todo = todo[:a.limit]
    print(f"к обработке: {len(todo)} (уже готово: {len(done)})", flush=True)
    api_key = key()
    total, n, errors = 0.0, 0, 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(process, x, api_key): x for x in todo}
        for fu in as_completed(futs):
            x = futs[fu]
            try:
                text, cost = fu.result()
            except Exception as e:
                errors += 1
                print(f"ОШИБКА {x['kind']} {x['topic']}#{x['msg']}: {e}", flush=True)
                if "HTTP 401" in str(e) or "HTTP 402" in str(e):
                    ex.shutdown(cancel_futures=True)
                    break
                continue
            rec = {**x, "text": text, "model": MODEL, "cost": cost}
            with lock, open(out_dir / f"{x['kind']}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            total += cost
            n += 1
            if n % 25 == 0 or n == len(todo):
                print(f"{n}/{len(todo)} готово, ошибок {errors}, потрачено ${total:.3f}", flush=True)
    print(f"ИТОГО: {n} обработано, ошибок {errors}, ${total:.3f}", flush=True)


if __name__ == "__main__":
    main()
