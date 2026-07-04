from __future__ import annotations

import re
from dataclasses import dataclass


class InvalidRegexError(ValueError):
    def __init__(self, pattern: str, error: re.error) -> None:
        self.pattern = pattern
        self.error = error
        super().__init__(f"Invalid regex {pattern!r}: {error}")


@dataclass(frozen=True)
class TermCondition:
    text: str
    whole_word: bool = False
    regex: bool = False

    def normalized_text(self) -> str:
        return self.text.strip()

    def is_valid(self) -> bool:
        return bool(self.normalized_text())


def normalize_term_conditions(terms) -> list[TermCondition]:
    conditions: list[TermCondition] = []
    for item in terms or []:
        if isinstance(item, TermCondition):
            condition = item
        elif isinstance(item, dict):
            condition = TermCondition(
                text=str(item.get("text", "")),
                whole_word=bool(item.get("whole_word", False)),
                regex=bool(item.get("regex", False)),
            )
        else:
            condition = TermCondition(text=str(item or ""))
        if condition.is_valid():
            conditions.append(condition)
    return conditions


def term_matches(text: str, condition: TermCondition) -> bool:
    term = condition.normalized_text()
    if not term:
        return True
    if condition.regex:
        try:
            return re.search(term, text) is not None
        except re.error as exc:
            raise InvalidRegexError(term, exc) from exc
    if not condition.whole_word:
        return term in text
    return whole_word_match_count(text, term) > 0


def term_match_count(text: str, condition: TermCondition) -> int:
    term = condition.normalized_text()
    if not term:
        return 0
    if condition.regex:
        try:
            return len(list(re.finditer(term, text)))
        except re.error as exc:
            raise InvalidRegexError(term, exc) from exc
    if not condition.whole_word:
        return text.count(term)
    return whole_word_match_count(text, term)


def term_first_match(text: str, condition: TermCondition) -> tuple[int, int]:
    term = condition.normalized_text()
    if not term:
        return -1, -1
    if condition.regex:
        try:
            match = re.search(term, text)
        except re.error as exc:
            raise InvalidRegexError(term, exc) from exc
        return (match.start(), match.end()) if match is not None else (-1, -1)
    if not condition.whole_word:
        start = text.find(term)
        return (start, start + len(term)) if start >= 0 else (-1, -1)

    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return -1, -1
        end = index + len(term)
        if has_space_boundaries(text, index, end):
            return index, end
        start = index + 1


def whole_word_match_count(text: str, term: str) -> int:
    count = 0
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return count
        end = index + len(term)
        if has_space_boundaries(text, index, end):
            count += 1
        start = index + 1


def has_space_boundaries(text: str, start: int, end: int) -> bool:
    left_ok = start == 0 or text[start - 1] == " "
    right_ok = end == len(text) or text[end] == " "
    return left_ok and right_ok
