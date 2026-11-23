"""Сводка для отчёта по задаче 029: таблица пилота, сравнение с v1, показательные находки.

Читает готовые json data/factcheck/*.json (v2, по mtime > --since или все) и
backup/factcheck_v1/*.json (v1). Печатает:
  1) таблицу: статья | утверждений | supported % | partial | unsupported | contradicted |
     wrong_citation | disputed | время | вызовов;
  2) сравнение с v1 по общим статьям;
  3) кандидатов в показательные находки (wrong_citation с правильным источником, contradicted,
     disputed, unsupported с вторым мнением) — с цитатами и evidence.

Запуск: python tasks/029_report.py [--since "2026-11-19 22:00"]
"""
import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "factcheck"
V1 = ROOT / "backup" / "factcheck_v1"

COLS = ("claims", "supported", "partial", "unsupported", "contradicted",
        "wrong_citation", "disputed", "seconds", "llm_calls")


def load(d, since=None):
    arts = []
    for f in sorted(d.glob("*.json")):
        if since and f.stat().st_mtime < since:
            continue
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"!! битый json: {f.name}: {e}")
            continue
        if a.get("judge"):          # v2-результаты (v1 не содержит поле judge)
            arts.append(a)
    return arts


def row(a):
    st = a.get("stats", {})
    n = st.get("claims", 0)
    pct = round(100 * st.get("supported", 0) / n) if n else 0
    return (f"| {a['title'][:60]} | {n} | {pct} | {st.get('partial', 0)} | "
            f"{st.get('unsupported', 0)} | {st.get('contradicted', 0)} | "
            f"{st.get('wrong_citation', 0)} | {st.get('disputed', 0)} | "
            f"{a.get('seconds', 0):.0f} | {a.get('llm_calls', 0)} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="учитывать только json новее времени (ISO/epoch)")
    args = ap.parse_args()
    since = None
    if args.since:
        try:
            since = time.mktime(time.strptime(args.since, "%Y-%m-%d %H:%M"))
        except Exception:
            since = float(args.since)

    v2 = load(OUT, since)
    print(f"== v2: {len(v2)} статей ==")
    print("| Статья | Утв. | supp, % | partial | unsupp | contr | wrong_cit | disputed | время, с | вызовов |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    tot = {k: 0 for k in COLS}
    for a in v2:
        print(row(a))
        st = a.get("stats", {})
        for k in COLS:
            tot[k] += st.get(k, 0) or a.get(k, 0) or 0
    n = tot["claims"]
    if n:
        print(f"| **Итого** | **{n}** | **{round(100 * tot['supported'] / n)}** | **{tot['partial']}** | "
              f"**{tot['unsupported']}** | **{tot['contradicted']}** | **{tot['wrong_citation']}** | "
              f"**{tot['disputed']}** | — | **{tot['llm_calls']}** |")
        print(f"среднее время на статью (сумма seconds / число статей, параллельно): "
              f"{tot['seconds'] / max(1, len(v2)):.0f} с")

    v1 = {a["title"]: a for a in load_v1()}
    common = [a for a in v2 if a["title"] in v1]
    print(f"\n== сравнение с v1 (общие статьи: {len(common)}) ==")
    print("| Статья | v1: утв / supp% / unsupp / contr | v2: утв / supp% / unsupp / contr / wrong_cit |")
    print("|---|---|---|")
    for a in common:
        s1, s2 = v1[a["title"]].get("stats", {}), a.get("stats", {})
        n1, n2 = s1.get("claims", 0), s2.get("claims", 0)
        p1 = round(100 * s1.get("supported", 0) / n1) if n1 else 0
        p2 = round(100 * s2.get("supported", 0) / n2) if n2 else 0
        print(f"| {a['title'][:60]} | {n1} / {p1}% / {s1.get('unsupported', 0)} / {s1.get('contradicted', 0)} | "
              f"{n2} / {p2}% / {s2.get('unsupported', 0)} / {s2.get('contradicted', 0)} / "
              f"{s2.get('wrong_citation', 0)} |")

    print("\n== кандидаты в показательные находки ==")
    shown = 0
    for a in v2:
        for sec in a.get("sections", []):
            for c in sec.get("claims", []):
                v = c.get("verdict")
                if v not in ("wrong_citation", "contradicted", "disputed") and not (
                        v == "unsupported" and c.get("second")):
                    continue
                print(f"\n[{v}] {a['title'][:50]} / «{sec['heading'][:40]}»")
                print(f"  утвержд.: {c.get('text', '')[:160]}")
                if c.get("src"):
                    print(f"  ссылка в статье: [[{c['src']}]]")
                if c.get("correct_source"):
                    print(f"  правильный источник: [[{c['correct_source']}]] "
                          f"{c.get('evidence') or ''}")
                if c.get("quote"):
                    print(f"  цитата: «{c.get('quote', '')[:180]}»")
                if c.get("problem"):
                    print(f"  проблема: {c.get('problem', '')[:160]}")
                if c.get("second"):
                    s = c["second"]
                    print(f"  второе мнение: {s.get('verdict')} — «{str(s.get('quote', ''))[:120]}»"
                          f" {s.get('evidence') or ''}")
                if c.get("fix"):
                    print(f"  правка: {c.get('fix', '')[:140]}")
                shown += 1
    print(f"\nвсего кандидатов: {shown}")


def load_v1():
    arts = []
    for f in sorted(V1.glob("*.json")):
        try:
            arts.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"!! битый v1 json: {f.name}: {e}")
    return arts


if __name__ == "__main__":
    main()
