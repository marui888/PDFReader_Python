from __future__ import annotations

import json
from typing import Any

import pymupdf as fitz


PAGE_LABEL_STYLES = {"D", "r", "R", "a", "A"}


def page_label_for_index(doc: fitz.Document | None, page_index: int) -> str:
    if doc is None or page_index < 0 or page_index >= len(doc):
        return ""
    try:
        return str(doc[page_index].get_label() or "")
    except Exception:
        return ""


def page_index_for_label(doc: fitz.Document | None, label: str) -> int | None:
    if doc is None:
        return None
    normalized = label.strip()
    if not normalized:
        return None
    try:
        matches = doc.get_page_numbers(normalized, only_one=True)
    except Exception:
        return None
    if not matches:
        return None
    return int(matches[0])


def page_labels_json(doc: fitz.Document | None) -> str:
    labels = [] if doc is None else doc.get_page_labels()
    return json.dumps(labels, ensure_ascii=False, indent=2)


def parse_page_labels_json(source: str, page_count: int) -> list[dict[str, Any]]:
    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("Page Labels JSON must be a list of objects.")

    rules: list[dict[str, Any]] = []
    previous_startpage = -1
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Rule {index} must be an object.")
        if "startpage" not in item:
            raise ValueError(f"Rule {index} is missing required field: startpage.")
        startpage = item["startpage"]
        if not isinstance(startpage, int):
            raise ValueError(f"Rule {index} startpage must be an integer.")
        if startpage < 0 or startpage >= page_count:
            raise ValueError(f"Rule {index} startpage must be between 0 and {page_count - 1}.")
        if startpage <= previous_startpage:
            raise ValueError("Rule startpage values must be strictly increasing.")
        previous_startpage = startpage

        rule: dict[str, Any] = {"startpage": startpage}

        if "prefix" in item:
            if not isinstance(item["prefix"], str):
                raise ValueError(f"Rule {index} prefix must be a string.")
            rule["prefix"] = item["prefix"]

        if "style" in item:
            style = item["style"]
            if not isinstance(style, str):
                raise ValueError(f"Rule {index} style must be a string.")
            if style and style not in PAGE_LABEL_STYLES:
                raise ValueError(f"Rule {index} style must be one of D, r, R, a, A.")
            if style:
                rule["style"] = style

        if "firstpagenum" in item:
            firstpagenum = item["firstpagenum"]
            if not isinstance(firstpagenum, int) or firstpagenum < 1:
                raise ValueError(f"Rule {index} firstpagenum must be a positive integer.")
            rule["firstpagenum"] = firstpagenum
        else:
            rule["firstpagenum"] = 1

        rules.append(rule)

    return rules
