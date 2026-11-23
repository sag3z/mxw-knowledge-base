#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разовая миграция (задача 002): переименовать .md вне vault/Источники/ под
новую логику safe() из build_vault.py и поправить wiki-ссылки [[старое]] и
[[старое|подпись]] на новые имена во всех .md хранилища.

Импортировать safe из build_vault нельзя (там на верхнем уровне import llm,
который читает ключ), поэтому новая логика safe продублирована ниже — она
должна оставаться байт-в-байт идентичной safe() в build_vault.py.

Только стандартная библиотека. Запуск:
  python tasks/002_rename.py --dry   # напечатать план, ничего не менять
  python tasks/002_rename.py         # применить
"""
import re
import sys
from collections import Counter
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent / "vault"
SOURCES_DIR = "Источники"

FORBID_RE = re.compile(r'[\\/:*?"<>|#^\[\]]+')
WS_RE = re.compile(r"\s+")
TAIL_RE = re.compile(r"[\s,.;\-–—]+$")


def safe(name):
    """Копия новой safe() из build_vault.py (см. предупреждение в докстринге)."""
    s = FORBID_RE.sub(" ", name).strip()[:90]
    s = WS_RE.sub(" ", s)
    return TAIL_RE.sub("", s)


def build_plan():
    """Собрать план переименований: {файл: новый stem} + список конфликтов.

    Конфликт = новое имя, на которое претендуют двое (включая файл, который сам
    не переименовывается, — тогда это настоящее занятое имя)."""
    files = sorted(p for p in VAULT.rglob("*.md")
                   if SOURCES_DIR not in p.relative_to(VAULT).parts)
    final = {p: safe(p.stem) for p in files}
    dup = Counter(final.values())
    renames = {}
    conflicts = []
    for p, new in final.items():
        if new == p.stem:
            continue
        if dup[new] > 1:
            conflicts.append((p, new))
        else:
            renames[p] = new
    return renames, conflicts


def apply_renames(renames, dry):
    if dry:
        for p in sorted(renames):
            print(f"  {p.relative_to(VAULT)}  →  {renames[p]}.md")
        return len(renames)
    done, pending = 0, sorted(renames.items())
    while pending:   # несколько проходов: цель может освободиться, переименовавшись сама
        rest, progress = [], False
        for p, new in pending:
            target = p.with_name(new + ".md")
            if target.exists():
                rest.append((p, new))
                continue
            print(f"  {p.relative_to(VAULT)}  →  {new}.md")
            p.rename(target)
            done += 1
            progress = True
        if rest and not progress:
            for p, new in rest:
                print(f"  КОНФЛИКТ: {p.relative_to(VAULT)} → {new}.md — цель занята, пропущено")
            break
        pending = rest
    return done


def fix_links(renames, dry):
    """Заменить [[старое]] / [[старое| на новое имя во всех .md хранилища
    (одним regex-проходом, чтобы замены не цеплялись друг за друга)."""
    by_stem = {p.stem: new for p, new in renames.items()}
    if not by_stem:
        return 0
    pat = re.compile(r"\[\[(" + "|".join(re.escape(o) for o in by_stem) + r")(\]\]|\|)")
    total = 0
    for p in sorted(VAULT.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  (не прочитан {p}: {e})")
            continue
        new_text, n = pat.subn(lambda m: f"[[{by_stem[m.group(1)]}{m.group(2)}", text)
        if n:
            print(f"  ссылки: {p.relative_to(VAULT)}: {n}")
            if not dry:
                p.write_text(new_text, encoding="utf-8")
            total += n
    return total


def main():
    dry = "--dry" in sys.argv
    if not VAULT.is_dir():
        print(f"папка не найдена: {VAULT}")
        return 1
    renames, conflicts = build_plan()
    mode = " (план, --dry)" if dry else ""
    print(f"переименований: {len(renames)}, конфликтов: {len(conflicts)}{mode}")
    for p, new in conflicts:
        print(f"  КОНФЛИКТ: {p.relative_to(VAULT)} → {new}.md — имя занято другим файлом, пропущено")
    done = apply_renames(renames, dry)
    print(f"переименовано: {done}{mode}")
    n = fix_links(renames, dry)
    print(f"ссылок обновлено: {n}{mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
