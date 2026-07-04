from __future__ import annotations

import re
from html import escape
from dataclasses import dataclass

import pymupdf as fitz

from app.repositories.annotation_repository import AnnotationRepository
from app.services.text_match import TermCondition
from app.services.text_match import InvalidRegexError
from app.services.text_match import has_space_boundaries
from app.services.text_match import normalize_term_conditions
from app.services.text_match import term_first_match
from app.services.text_match import term_match_count
from app.services.text_match import term_matches


@dataclass
class FreeTextMatch:
    page_index: int
    xref: int
    text: str
    match_count: int
    first_match_start: int
    first_match_end: int


@dataclass
class ReplacePreview:
    old_text: str
    new_text: str
    old_html: str
    new_html: str
    match_count: int


def find_freetext_matches(
    doc: fitz.Document,
    include_terms: list[str] | list[dict] | list[TermCondition],
    include_mode: str = "and",
    exclude_terms: list[str] | None = None,
    exclude_mode: str = "or",
    *,
    current_page_index: int = 0,
    scope: str = "current_page",
) -> tuple[list[FreeTextMatch], list[str]]:
    include_queries = normalize_term_conditions(include_terms)
    exclude_queries = normalize_terms(exclude_terms or [])
    if not include_queries and not exclude_queries:
        return [], []

    repository = AnnotationRepository(doc)
    page_indexes = page_indexes_for_scope(doc, current_page_index, scope)
    matches: list[FreeTextMatch] = []

    for page_index in page_indexes:
        for model in repository.load_page_annotations(page_index):
            if model.app_type != "freetext" or model.deleted:
                continue
            if exclude_queries and terms_match(model.text, exclude_queries, exclude_mode):
                continue
            if include_queries:
                if not terms_match(model.text, include_queries, include_mode):
                    continue
                match_count, start, end = include_match_stats(model.text, include_queries)
            else:
                match_count = 0
                start = -1
                end = -1
            matches.append(
                FreeTextMatch(
                    page_index=page_index,
                    xref=model.xref,
                    text=model.text,
                    match_count=match_count,
                    first_match_start=start,
                    first_match_end=end,
                )
            )

    warnings = [warning.format() for warning in repository.warnings]
    return matches, warnings


def normalize_terms(terms: list[str]) -> list[str]:
    return [term.strip() for term in terms if term and term.strip()]


def terms_match(text: str, terms: list[str] | list[TermCondition], mode: str) -> bool:
    if not terms:
        return True
    if terms and isinstance(terms[0], TermCondition):
        conditions = terms
        if mode == "or":
            return any(term_matches(text, condition) for condition in conditions)
        return all(term_matches(text, condition) for condition in conditions)
    if mode == "or":
        return any(term in text for term in terms)
    return all(term in text for term in terms)


def include_match_stats(text: str, terms: list[TermCondition]) -> tuple[int, int, int]:
    match_count = 0
    first_start = -1
    first_end = -1
    for condition in terms:
        count = term_match_count(text, condition)
        match_count += count
        start, end = term_first_match(text, condition)
        if start >= 0 and (first_start < 0 or start < first_start):
            first_start = start
            first_end = end
    return match_count, first_start, first_end


def page_indexes_for_scope(doc: fitz.Document, current_page_index: int, scope: str) -> range:
    if scope == "current_document":
        return range(len(doc))
    page_index = max(0, min(current_page_index, max(0, len(doc) - 1)))
    return range(page_index, page_index + 1)


def replace_match_text(text: str, keyword: str, replacement: str) -> str:
    if not keyword:
        return text
    return text.replace(keyword, replacement)


def replace_match_text_with_mode(
    text: str,
    keyword: str,
    replacement: str,
    *,
    whole_word: bool = False,
    regex: bool = False,
) -> str:
    if not keyword:
        return text
    if regex:
        try:
            return re.sub(keyword, python_regex_replacement(replacement), text)
        except re.error as exc:
            raise InvalidRegexError(keyword, exc) from exc
    if not whole_word:
        return replace_match_text(text, keyword, replacement)
    output: list[str] = []
    cursor = 0
    start = 0
    while True:
        index = text.find(keyword, start)
        if index < 0:
            output.append(text[cursor:])
            return "".join(output)
        end = index + len(keyword)
        if has_space_boundaries(text, index, end):
            output.append(text[cursor:index])
            output.append(replacement)
            cursor = end
        start = index + 1


def preview_replace_text_with_mode(
    text: str,
    keyword: str,
    replacement: str,
    *,
    whole_word: bool = False,
    regex: bool = False,
) -> ReplacePreview:
    if not keyword:
        return ReplacePreview(text, text, html_escape_text(text), html_escape_text(text), 0)
    replacements = replacement_spans(text, keyword, replacement, whole_word=whole_word, regex=regex)
    if not replacements:
        return ReplacePreview(text, text, html_escape_text(text), html_escape_text(text), 0)

    old_parts: list[str] = []
    new_parts: list[str] = []
    old_cursor = 0
    for old_start, old_end, new_fragment in replacements:
        old_parts.append(html_escape_text(text[old_cursor:old_start]))
        old_parts.append(f"<b>{html_escape_text(text[old_start:old_end])}</b>")
        new_parts.append(html_escape_text(text[old_cursor:old_start]))
        new_parts.append(f"<b>{html_escape_text(new_fragment)}</b>")
        old_cursor = old_end
    old_parts.append(html_escape_text(text[old_cursor:]))
    new_parts.append(html_escape_text(text[old_cursor:]))
    new_text = replacement_text_from_spans(text, replacements)
    return ReplacePreview(
        old_text=text,
        new_text=new_text,
        old_html="".join(old_parts),
        new_html="".join(new_parts),
        match_count=len(replacements),
    )


def replacement_spans(
    text: str,
    keyword: str,
    replacement: str,
    *,
    whole_word: bool = False,
    regex: bool = False,
) -> list[tuple[int, int, str]]:
    if not keyword:
        return []
    if regex:
        try:
            return [
                (match.start(), match.end(), match.expand(python_regex_replacement(replacement)))
                for match in re.finditer(keyword, text)
                if match.start() != match.end()
            ]
        except re.error as exc:
            raise InvalidRegexError(keyword, exc) from exc
    spans: list[tuple[int, int, str]] = []
    start = 0
    while True:
        index = text.find(keyword, start)
        if index < 0:
            return spans
        end = index + len(keyword)
        if not whole_word or has_space_boundaries(text, index, end):
            spans.append((index, end, replacement))
        start = index + 1


def replacement_text_from_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    output: list[str] = []
    cursor = 0
    for start, end, replacement in spans:
        output.append(text[cursor:start])
        output.append(replacement)
        cursor = end
    output.append(text[cursor:])
    return "".join(output)


def html_escape_text(text: str) -> str:
    return escape(text).replace("\n", "<br>")


def python_regex_replacement(replacement: str) -> str:
    return re.sub(r"\$(\d+)", r"\\g<\1>", replacement)


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
