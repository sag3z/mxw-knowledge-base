"""Select conversations that mention multi-accounting / sybil / farms / KYC and pack them for a focused re-label.

Usage: python select_abuse.py <data dir>
Uses batches_big segments instead of their oversized parent conversations.
Writes <data>/batches_abuse/<slug>/bNNN.txt
"""
import re
import sys
from pathlib import Path

PATTERNS = [
    r"\bkyc\b", r"\bкус(ы|а|ом|е|ов|ик)?\b", r"кус-?шоп", r"\bвериф",
    r"мульти-?акк", r"мульти-?аккаунт", r"\bсибил", r"sybil", r"антисибил",
    r"\bферм(а|ы|у|ой|е|ить|ят|ер|еры|инг)\b", r"\bфарм", r"авторег", r"\bабуз", r"\bабьюз", r"\babuse",
    r"антидетект", r"antidetect", r"adspower", r"ads ?power", r"dolphin", r"gologin", r"octo ?browser",
    r"\bпрокси", r"\bproxy", r"\bпроксей", r"\bтвинк",
    r"(купить|покупать|купил|скупать|продаю|продам|берут?) акк", r"\bакки\b", r"\bакков\b", r"\bаккаунтов\b",
    r"гугл ?форм", r"google ?form", r"\bкапч", r"captcha",
    r"смс-?актив", r"sms-?activ", r"\bномера? для", r"виртуальн\w* карт",
    r"\b\d{2,5} ?(кошельк|кошелёк|акк|аккаунт|твиттер|почт|твинк)",
    r"\bсофт(ом|ы|а)? для", r"\bботом\b", r"скрипт\w* для (форм|минт|рег)",
]
RX = re.compile("|".join(PATTERNS), re.I)
BLOCK = re.compile(r"(=== РАЗГОВОР (c[\d-]+) .*?===\n.*?)(?=^=== РАЗГОВОР|\Z)", re.S | re.M)
BATCH_CHARS = 60000
# conversations never to include in the KB (content about minors)
EXCLUDE = {("жизнь_флуд_кайф", "c0175")}


def blocks(folder: Path):
    for b in sorted(folder.glob("b*.txt")):
        for full, conv in BLOCK.findall(b.read_text(encoding="utf-8")):
            yield conv, full


def main(data: Path):
    total = 0
    for tdir in sorted((data / "batches").iterdir()):
        if not tdir.is_dir():
            continue
        big_dir = data / "batches_big" / tdir.name
        big = list(blocks(big_dir)) if big_dir.exists() else []
        big_parents = {c.split("-")[0] for c, _ in big}
        chosen = []
        for conv, full in list(blocks(tdir)) + big:
            if conv in big_parents or (tdir.name, conv) in EXCLUDE:   # parent replaced by its segments
                continue
            hits = len(RX.findall(full))
            if hits:
                chosen.append(full if full.endswith("\n") else full + "\n")
        if not chosen:
            continue
        out = data / "batches_abuse" / tdir.name
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
        total += len(chosen)
        print(f"{tdir.name}: {len(chosen)} convs, {len(batches)} batches, {sum(map(len, chosen))} chars")
    print("TOTAL convs:", total)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
