from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TextSearchCondition:
    kind: str
    text: str

    def normalized_text(self) -> str:
        return self.text.strip()

    def is_valid(self) -> bool:
        return self.kind in {"include", "exclude"} and bool(self.normalized_text())


@dataclass
class AnnotationSearchQuery:
    keyword: str = ""
    include_mode: str = "all"
    text_conditions: list[TextSearchCondition] = field(default_factory=list)

    def normalized_keyword(self) -> str:
        return self.keyword.strip()

    def normalized_include_mode(self) -> str:
        return "any" if self.include_mode == "any" else "all"

    def conditions(self, kind: str | None = None) -> list[TextSearchCondition]:
        conditions = [condition for condition in self.text_conditions if condition.is_valid()]
        if kind is None:
            return conditions
        return [condition for condition in conditions if condition.kind == kind]

    def include_terms(self) -> list[str]:
        return [condition.normalized_text() for condition in self.conditions("include")]

    def exclude_terms(self) -> list[str]:
        return [condition.normalized_text() for condition in self.conditions("exclude")]

    def has_advanced_conditions(self) -> bool:
        return bool(self.conditions())

    def summary(self) -> str:
        parts: list[str] = []
        keyword = self.normalized_keyword()
        if keyword and not self.has_advanced_conditions():
            parts.append(f"Keyword: {keyword}")

        include_terms = self.include_terms()
        if include_terms:
            joiner = " OR " if self.normalized_include_mode() == "any" else " AND "
            parts.append("Include: " + joiner.join(include_terms))

        exclude_terms = self.exclude_terms()
        if exclude_terms:
            parts.append("Exclude: " + " AND ".join(exclude_terms))

        return " | ".join(parts) if parts else "No text condition"


def split_terms(text: str) -> list[str]:
    normalized = text.replace("\r", "\n").replace(",", "\n").replace(";", "\n")
    return [term.strip() for term in normalized.splitlines() if term.strip()]


def build_search_query(
    keyword: str = "",
    include_mode: str = "all",
    include_text: str = "",
    exclude_text: str = "",
) -> AnnotationSearchQuery:
    conditions = [
        *(TextSearchCondition("include", term) for term in split_terms(include_text)),
        *(TextSearchCondition("exclude", term) for term in split_terms(exclude_text)),
    ]
    return AnnotationSearchQuery(
        keyword=keyword,
        include_mode=include_mode,
        text_conditions=conditions,
    )


def search_rule_to_dict(
    include_mode: str,
    include_text: str,
    exclude_text: str,
    name: str = "",
) -> dict:
    return {
        "version": 1,
        "name": name,
        "include_mode": "any" if include_mode == "any" else "all",
        "include_text": include_text,
        "exclude_text": exclude_text,
    }


def save_search_rule(
    path: Path,
    include_mode: str,
    include_text: str,
    exclude_text: str,
    name: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = search_rule_to_dict(include_mode, include_text, exclude_text, name)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_search_rule(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Search rule file must contain a JSON object.")
    return {
        "include_mode": "any" if data.get("include_mode") == "any" else "all",
        "include_text": str(data.get("include_text", "")),
        "exclude_text": str(data.get("exclude_text", "")),
        "name": str(data.get("name", "")),
    }
