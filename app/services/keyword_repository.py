from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


KEYWORD_START = "KEYWORD-START"
KEYWORD_END = "KEYWORD-END"
KEYWORD_LINE_RE = re.compile(r"^[A-Za-z\u4e00-\u9fff]")


@dataclass(frozen=True)
class KeywordGroup:
    name: str
    items: tuple[str, ...]


def load_keyword_groups(metadata_dir: Path) -> list[KeywordGroup]:
    if not metadata_dir.exists():
        return []

    groups: list[KeywordGroup] = []
    for path in sorted(metadata_dir.glob("*.txt"), key=lambda item: item.name.lower()):
        items = _extract_keywords(path)
        if items:
            groups.append(KeywordGroup(path.stem, tuple(items)))
    return groups


def _extract_keywords(path: Path) -> list[str]:
    lines = _read_text_lines(path)
    in_block = False
    items: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if KEYWORD_START in line:
            in_block = True
            continue
        if KEYWORD_END in line:
            break
        if not in_block:
            continue
        if not line:
            continue
        if not KEYWORD_LINE_RE.match(line):
            continue
        items.append(line)

    return items


def _read_text_lines(path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=encoding).splitlines()
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace").splitlines()
