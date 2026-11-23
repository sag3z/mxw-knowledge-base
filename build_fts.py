"""Build FTS5 full-text index (fts_msg, fts_conv, fts_art) in data/kb.sqlite.

Run after the DB and convs exist:  python build_fts.py
- fts_msg: message text + all media_text transcriptions (is_bot=1 and empty bodies skipped),
  UNINDEXED topic/id/conv; conv = the smallest conversation owning the message (segment, not parent).
- fts_conv: summary + tags + scheme_summary per conversation, UNINDEXED topic/conv.
- fts_art: статьи базы знаний (все .md из vault/ кроме vault/Источники/), куски по заголовкам «##»
  (≤2500 символов, frontmatter выкинут), UNINDEXED path/title.
Drop-verdict conversations are indexed too (they have no embeddings but are findable by words).
"""
import json
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("data") / "kb.sqlite"
TOKENIZER = "unicode61 remove_diacritics 2"   # русский + латиница, без диакритики


def msg_to_conv(conn):
    """(topic, msg_id) → conv. Сообщение должно относиться к самому мелкому разговору (сегменту),
    а не к родителю: сначала пишем родительские строки, затем сегменты перезаписывают."""
    rows = conn.execute("SELECT topic, conv, parent, msg_ids FROM convs").fetchall()
    keys = {(r["topic"], r["conv"]) for r in rows}
    stored = sum(1 for r in rows if r["parent"] is not None and (r["topic"], r["parent"]) in keys)
    print(f"сегментов с parent: {sum(1 for r in rows if r['parent'] is not None)}; "
          f"родительские строки в convs: {'хранятся' if stored else 'не хранятся'} ({stored})")
    mc = {}
    for r in rows:                       # родители (parent IS NULL) — первыми
        if r["parent"] is None:
            for mid in json.loads(r["msg_ids"]):
                mc[(r["topic"], mid)] = r["conv"]
    for r in rows:                       # сегменты перезаписывают родителей
        if r["parent"] is not None:
            for mid in json.loads(r["msg_ids"]):
                mc[(r["topic"], mid)] = r["conv"]
    return mc


VAULT = Path(__file__).with_name("vault")
ART_MAX = 2500   # кусок статьи ≤2500 символов


def article_chunks(text):
    """Куски статьи: frontmatter выкинуть, резать по заголовкам «##» (интро до первого «##» — тоже кусок);
    кусок длиннее ART_MAX — режем по абзацам, затем жёстко по ART_MAX."""
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    parts = re.split(r"(?m)^(## .*)$", body)
    chunks = [parts[0].strip()] if parts and parts[0].strip() else []
    for i in range(1, len(parts), 2):
        chunks.append((parts[i] + "\n" + parts[i + 1]).strip())
    out = []
    for ch in chunks:
        cur = ""
        for para in ch.split("\n\n"):
            cur = (cur + "\n\n" + para) if cur else para
            while len(cur) > ART_MAX:
                out.append(cur[:ART_MAX])
                cur = cur[ART_MAX:]
            if len(cur) == ART_MAX:
                out.append(cur)
                cur = ""
        if cur.strip():
            out.append(cur)
    return [c for c in out if c.strip()]


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    mc = msg_to_conv(conn)

    conn.execute("DROP TABLE IF EXISTS fts_msg")
    conn.execute(f"CREATE VIRTUAL TABLE fts_msg USING fts5("
                 f"body, topic UNINDEXED, id UNINDEXED, conv UNINDEXED, tokenize='{TOKENIZER}')")
    n_msg = n_bot = n_empty = n_noconv = 0
    for r in conn.execute("SELECT topic, id, text, media_text, is_bot FROM messages ORDER BY topic, id"):
        if r["is_bot"]:
            n_bot += 1
            continue
        parts = [r["text"] or ""]
        for m in json.loads(r["media_text"] or "[]"):
            parts.append(m.get("text") or "")
        body = " ".join(p for p in parts if p).strip()
        if not body:
            n_empty += 1
            continue
        conv = mc.get((r["topic"], r["id"]))
        if conv is None:                 # вне разговоров — в поиск не попадают (результаты = разговоры)
            n_noconv += 1
            continue
        conn.execute("INSERT INTO fts_msg VALUES(?,?,?,?)", (body, r["topic"], r["id"], conv))
        n_msg += 1

    conn.execute("DROP TABLE IF EXISTS fts_conv")
    conn.execute(f"CREATE VIRTUAL TABLE fts_conv USING fts5(body, topic UNINDEXED, conv UNINDEXED, tokenize='{TOKENIZER}')")
    n_conv = n_conv_empty = 0
    for r in conn.execute("SELECT topic, conv, summary, tags, scheme_summary FROM convs"):
        parts = [r["summary"] or "", " ".join(json.loads(r["tags"] or "[]")), r["scheme_summary"] or ""]
        body = " ".join(p for p in parts if p).strip()
        if not body:
            n_conv_empty += 1
            continue
        conn.execute("INSERT INTO fts_conv VALUES(?,?,?)", (body, r["topic"], r["conv"]))
        n_conv += 1

    conn.execute("DROP TABLE IF EXISTS fts_art")
    conn.execute(f"CREATE VIRTUAL TABLE fts_art USING fts5("
                 f"body, path UNINDEXED, title UNINDEXED, tokenize='{TOKENIZER}')")
    n_art = n_chunk = 0
    for f in sorted(VAULT.rglob("*.md")):   # статьи базы, кроме vault/Источники/
        rel = f.relative_to(VAULT)
        if rel.parts[0] == "Источники":
            continue
        for ch in article_chunks(f.read_text(encoding="utf-8")):
            conn.execute("INSERT INTO fts_art VALUES(?,?,?)", (ch, str(rel).replace("\\", "/"), f.stem))
            n_chunk += 1
        n_art += 1

    # локальные эмбеддинги (embed_local.py) не пересобираем, но вычищаем устаревшее:
    # emb_local — разговоры, ставшие drop (векторы бесполезны); emb_art — куски, которых
    # больше нет в fts_art (после пересборки). Новые куски/разговоры эмбеддит embed_local.py.
    for sql in ("DELETE FROM emb_local WHERE (topic, conv) NOT IN "
                "(SELECT topic, conv FROM convs WHERE verdict != 'drop')",
                "DELETE FROM emb_art WHERE (path, chunk) NOT IN (SELECT path, body FROM fts_art)"):
        try:
            cur = conn.execute(sql)
            if cur.rowcount > 0:
                print(f"вычищено устаревших векторов: {cur.rowcount} ({sql.split(' FROM ')[1].split(' WHERE')[0]})")
        except sqlite3.OperationalError:   # таблицы emb_* ещё нет — embed_local.py не запускался
            pass

    conn.commit()
    print(f"fts_msg: {n_msg} строк (пропущено: {n_bot} ботов, {n_empty} пустых, {n_noconv} вне разговоров)")
    print(f"fts_conv: {n_conv} строк (пропущено пустых: {n_conv_empty})")
    print(f"fts_art: {n_chunk} кусков из {n_art} статей")


if __name__ == "__main__":
    main()
