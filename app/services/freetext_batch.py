from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

from app.repositories.annotation_repository import AnnotationRepository


@dataclass
class FreeTextMatch:
    page_index: int
    xref: int
    text: str
    match_count: int
    first_match_start: int
    first_match_end: int


def find_freetext_matches(
    doc: fitz.Document,
    keyword: str,
    *,
    current_page_index: int = 0,
    scope: str = "current_page",
) -> tuple[list[FreeTextMatch], list[str]]:
    query = keyword.strip()
    if not query:
        return [], []

    repository = AnnotationRepository(doc)
    page_indexes = page_indexes_for_scope(doc, current_page_index, scope)
    matches: list[FreeTextMatch] = []

    for page_index in page_indexes:
        for model in repository.load_page_annotations(page_index):
            if model.app_type != "freetext" or model.deleted:
                continue
            match_count = model.text.count(query)
            if match_count <= 0:
                continue
            start = model.text.find(query)
            matches.append(
                FreeTextMatch(
                    page_index=page_index,
                    xref=model.xref,
                    text=model.text,
                    match_count=match_count,
                    first_match_start=start,
                    first_match_end=start + len(query),
                )
            )

    warnings = [warning.format() for warning in repository.warnings]
    return matches, warnings


def page_indexes_for_scope(doc: fitz.Document, current_page_index: int, scope: str) -> range:
    if scope == "current_document":
        return range(len(doc))
    page_index = max(0, min(current_page_index, max(0, len(doc) - 1)))
    return range(page_index, page_index + 1)


def replace_match_text(text: str, keyword: str, replacement: str) -> str:
    if not keyword:
        return text
    return text.replace(keyword, replacement)


def delete_match_text(text: str, keyword: str) -> str:
    return replace_match_text(text, keyword, "")


def add_text_to_match(text: str, keyword: str, addition: str, mode: str) -> str:
    if not addition:
        return text
    if mode == "end":
        return text + addition
    if not keyword:
        return text
    if mode == "before":
        return text.replace(keyword, addition + keyword)
    if mode == "after":
        return text.replace(keyword, keyword + addition)
    return text
