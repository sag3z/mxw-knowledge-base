#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отчёт по Obsidian-хранилищу: битые wiki-ссылки, качество статей, сводка по папкам.

Запуск:  python check_vault.py [папка]   (по умолчанию vault)
Код выхода всегда 0: это отчёт, а не тест. Только стандартная библиотека.
"""

from __future__ import annotations

import re
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

MIN_BODY_CHARS = 500          # «пустые или короче 500 символов»
SOURCES_DIR = "Источники"
# папки, которые не считаются папками категорий (для проверки frontmatter)
NOT_CATEGORY_DIRS = {"Источники", "Проекты и инструменты", "Медиа-конспекты", "Схемы"}
MAIN_PAGE = "00 Главная.md"
TOP_BROKEN = 20               # топ-N битых целей
MAX_FILES_PER_TARGET = 5      # сколько файлов показывать у одной битой цели
MAX_ITEMS_PER_LIST = 50       # лимит строк в списках проблемных статей

WIKI_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
H1_RE = re.compile(r"(?m)^#\s")              # заголовок «# ...»
CATEGORY_KEY_RE = re.compile(r"(?m)^\s*категория\s*:")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  (не прочитан {path}: {e})", file=sys.stderr)
        return ""


def split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter, body); если frontmatter нет — ("", text)."""
    if text.startswith("---"):
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            for i in range(1, min(len(lines), 100)):
                if lines[i].strip() == "---":
                    return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "", text


def link_target(raw: str) -> str:
    """[[цель|подпись]] -> цель; отрезаем #якорь и путь (сравнение по имени файла)."""
    t = raw.split("|", 1)[0]
    t = t.split("#", 1)[0]
    t = t.rsplit("/", 1)[-1]
    return t.strip()


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("vault")
    print(f"Хранилище: {root}")
    if not root.is_dir():
        print("Папка не найдена — проверять нечего.")
        return 0

    all_md = sorted(p for p in root.rglob("*.md") if p.is_file())

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    sources: list[Path] = []
    articles: list[Path] = []
    for p in all_md:
        (sources if p.relative_to(root).parts[0] == SOURCES_DIR else articles).append(p)

    # индекс имён (без .md) для резолва ссылок: цель существует, если такой файл есть где угодно
    by_name: dict[str, Path] = {}
    for p in all_md:
        by_name.setdefault(p.stem.casefold(), p)
    src_names = {p.stem.casefold() for p in sources}

    # папка -> [число .md, суммарный размер байт, ссылок на Источники]
    stats: dict[str, list] = defaultdict(lambda: [0, 0, 0])
    for p in all_md:
        parts = p.relative_to(root).parts
        folder = parts[0] if len(parts) > 1 else "(в корне)"
        stats[folder][0] += 1
        try:
            stats[folder][1] += p.stat().st_size
        except OSError:
            pass

    n_links = 0
    broken: Counter[str] = Counter()
    broken_in: defaultdict[str, set] = defaultdict(set)
    short: list[tuple[Path, int]] = []
    no_h1: list[Path] = []
    no_cat: list[Path] = []

    for p in articles:
        parts = p.relative_to(root).parts
        folder = parts[0] if len(parts) > 1 else "(в корне)"
        text = read_text(p)
        fm, body = split_frontmatter(text)

        targets = [t for t in (link_target(raw) for raw in WIKI_RE.findall(text)) if t]
        n_links += len(targets)
        n_src_links = 0
        for t in targets:
            cf = t.casefold()
            if cf in by_name:
                if cf in src_names:
                    n_src_links += 1
            else:
                broken[t] += 1
                broken_in[t].add(rel(p))
        stats[folder][2] += n_src_links

        if len(body.strip()) < MIN_BODY_CHARS:
            short.append((p, len(body.strip())))
        if not H1_RE.search(body):
            no_h1.append(p)
        is_category = (len(parts) > 1
                       and parts[0] not in NOT_CATEGORY_DIRS
                       and p.name != MAIN_PAGE)
        if is_category and not CATEGORY_KEY_RE.search(fm):
            no_cat.append(p)

    print(f"Всего .md: {len(all_md)} (статей: {len(articles)}, источников: {len(sources)})")
    print()

    # 1. Wiki-ссылки
    print("== 1. Wiki-ссылки (во всех .md вне Источники/) ==")
    n_broken = sum(broken.values())
    pct = f" ({n_broken * 100 / n_links:.1f}%)" if n_links else ""
    print(f"Ссылок: {n_links}, битых: {n_broken}{pct}")
    if broken:
        print(f"Топ-{min(TOP_BROKEN, len(broken))} битых целей:")
        for t, cnt in sorted(broken.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_BROKEN]:
            files = sorted(broken_in[t])
            shown = ", ".join(files[:MAX_FILES_PER_TARGET])
            extra = (f" (+{len(files) - MAX_FILES_PER_TARGET} др.)"
                     if len(files) > MAX_FILES_PER_TARGET else "")
            print(f"  {t} — {cnt}: {shown}{extra}")
    print()

    # 2. Статьи
    print("== 2. Статьи (.md вне Источники/) ==")
    print(f"Пустые или короче {MIN_BODY_CHARS} символов: {len(short)}")
    for p, n in sorted(short, key=lambda x: x[1])[:MAX_ITEMS_PER_LIST]:
        print(f"  - {rel(p)} ({n} симв.)")
    if len(short) > MAX_ITEMS_PER_LIST:
        print(f"  … и ещё {len(short) - MAX_ITEMS_PER_LIST}")
    print(f"Без заголовка '# ': {len(no_h1)}")
    for p in no_h1[:MAX_ITEMS_PER_LIST]:
        print(f"  - {rel(p)}")
    if len(no_h1) > MAX_ITEMS_PER_LIST:
        print(f"  … и ещё {len(no_h1) - MAX_ITEMS_PER_LIST}")
    print(f"Без поля 'категория' во frontmatter (папки категорий): {len(no_cat)}")
    for p in no_cat[:MAX_ITEMS_PER_LIST]:
        print(f"  - {rel(p)}")
    if len(no_cat) > MAX_ITEMS_PER_LIST:
        print(f"  … и ещё {len(no_cat) - MAX_ITEMS_PER_LIST}")
    print()

    # 3. Сводка по папкам
    print("== 3. Сводка по папкам ==")
    print("  Папка                                файлов   размер, КБ   Источники/статью")
    ordered = sorted(stats, key=lambda f: (f == "(в корне)", f))
    for folder in ordered:
        n, size, nsrc = stats[folder]
        if folder == SOURCES_DIR or n == 0:
            avg = "—"
        else:
            avg = f"{nsrc / n:.1f}"
        print(f"  {folder[:36].ljust(38)}{n:>7}{size / 1024:>12.1f}{avg:>17}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        traceback.print_exc()
        sys.exit(0)
