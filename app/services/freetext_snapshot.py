from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

from app.repositories.annotation_repository import AnnotationRepository
from app.services.freetext_batch import FreeTextMatch
from app.services.freetext_batch import include_match_stats
from app.services.freetext_batch import normalize_terms
from app.services.freetext_batch import page_indexes_for_scope
from app.services.freetext_batch import terms_match
from app.services.text_match import TermCondition
from app.services.text_match import normalize_term_conditions


@dataclass
class FreeTextSnapshotRecord:
    page_index: int
    xref: int
    text: str


class FreeTextSnapshot:
    def __init__(self, records: dict[tuple[int, int], FreeTextSnapshotRecord], warnings: list[str]) -> None:
        self.records = records
        self.warnings = warnings

    @classmethod
    def from_document(cls, doc: fitz.Document) -> FreeTextSnapshot:
        repository = AnnotationRepository(doc)
        records: dict[tuple[int, int], FreeTextSnapshotRecord] = {}
        for page_index in range(len(doc)):
            for model in repository.load_page_annotations(page_index):
                if model.app_type != "freetext" or model.deleted:
                    continue
                records[(page_index, model.xref)] = FreeTextSnapshotRecord(
                    page_index=page_index,
                    xref=model.xref,
                    text=model.text,
                )
        return cls(records, [warning.format() for warning in repository.warnings])

    def find(
        self,
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
            return [], list(self.warnings)

        page_indexes = set(page_indexes_for_scope(doc, current_page_index, scope))
        matches: list[FreeTextMatch] = []
        for record in sorted(self.records.values(), key=lambda item: (item.page_index, item.xref)):
            if record.page_index not in page_indexes:
                continue
            if exclude_queries and terms_match(record.text, exclude_queries, exclude_mode):
                continue
            if include_queries:
                if not terms_match(record.text, include_queries, include_mode):
                    continue
                match_count, start, end = include_match_stats(record.text, include_queries)
            else:
                match_count = 0
                start = -1
                end = -1
            matches.append(
                FreeTextMatch(
                    page_index=record.page_index,
                    xref=record.xref,
                    text=record.text,
                    match_count=match_count,
                    first_match_start=start,
                    first_match_end=end,
                )
            )
        return matches, list(self.warnings)

    def update_text(self, page_index: int, xref: int, text: str) -> None:
        key = (page_index, xref)
        record = self.records.get(key)
        if record is None:
            self.records[key] = FreeTextSnapshotRecord(page_index=page_index, xref=xref, text=text)
            return
        record.text = text

    def remove(self, page_index: int, xref: int) -> None:
        self.records.pop((page_index, xref), None)
