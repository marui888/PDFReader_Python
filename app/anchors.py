from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf as fitz

from app.repositories.annotation_repository import AnnotationRepository


SERIAL_SYMBOLS = tuple(chr(code) for code in range(0x2460, 0x2474))
SERIAL_BY_NUMBER = {number: symbol for number, symbol in enumerate(SERIAL_SYMBOLS, start=1)}
SERIAL_NUMBER_BY_SYMBOL = {symbol: number for number, symbol in SERIAL_BY_NUMBER.items()}
SERIAL_PREFIX_PATTERN = re.compile(r"^\s*([" + re.escape("".join(SERIAL_SYMBOLS)) + r"])")
REFERENCE_PATTERN = re.compile(r"Pg(\d+)-([" + re.escape("".join(SERIAL_SYMBOLS)) + r"])")


@dataclass
class AnchorModel:
    reference: str
    symbol: str
    serial_number: int
    page_index: int
    page_number: int
    xref: int
    text: str
    rect: fitz.Rect
    creation_date: str | None = None
    modified_date: str | None = None


@dataclass
class AnchorReferenceModel:
    reference: str
    page_index: int
    page_number: int
    xref: int
    text: str
    rect: fitz.Rect


@dataclass
class AnchorScanResult:
    anchors: list[AnchorModel]
    references_by_anchor: dict[str, list[AnchorReferenceModel]]
    unresolved_references: list[AnchorReferenceModel]


@dataclass
class AnchorResolveResult:
    anchor: AnchorModel
    matched_basis: str
    fallback_used: bool


def is_anchor_text(text: str) -> bool:
    return SERIAL_PREFIX_PATTERN.match(text) is not None


def anchor_symbol(text: str) -> str:
    number = serial_number(text)
    return SERIAL_BY_NUMBER.get(number, "") if number is not None else ""


def serial_number(text: str) -> int | None:
    match = SERIAL_PREFIX_PATTERN.match(text)
    if not match:
        return None
    return SERIAL_NUMBER_BY_SYMBOL.get(match.group(1))


def remove_serial_prefix(text: str) -> str:
    return SERIAL_PREFIX_PATTERN.sub("", text, count=1).lstrip(" ")


def add_serial_prefix(text: str, number: int) -> str:
    symbol = SERIAL_BY_NUMBER.get(number)
    if symbol is None:
        raise ValueError(f"Serial number is out of supported range: {number}")
    return f"{symbol} {remove_serial_prefix(text)}"


def anchor_reference(page_number: int, symbol: str) -> str:
    return f"Pg{page_number}-{symbol}"


def normalized_anchor_page_basis(page_basis: str | None) -> str:
    return "book" if page_basis == "book" else "physical"


def book_page_label(doc: fitz.Document, page_index: int) -> str:
    try:
        return str(doc[page_index].get_label() or "")
    except Exception:
        return ""


def reference_page_number_for_basis(doc: fitz.Document, page_index: int, page_basis: str) -> int:
    if normalized_anchor_page_basis(page_basis) == "book":
        label = book_page_label(doc, page_index).strip()
        if label.isdigit():
            return int(label)
    return page_index + 1


def anchor_reference_for_basis(doc: fitz.Document, page_index: int, symbol: str, page_basis: str) -> str:
    return anchor_reference(reference_page_number_for_basis(doc, page_index, page_basis), symbol)


def parse_anchor_reference(reference: str) -> tuple[int, str] | None:
    match = re.fullmatch(r"Pg(\d+)-([" + re.escape("".join(SERIAL_SYMBOLS)) + r"])", reference.strip())
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def references_in_text(text: str) -> list[str]:
    return [match.group(0) for match in REFERENCE_PATTERN.finditer(text)]


def has_anchor_reference(text: str) -> bool:
    return REFERENCE_PATTERN.search(text) is not None


def text_has_anchor_data(text: str) -> bool:
    return is_anchor_text(text) or has_anchor_reference(text)


def text_affects_anchor_data(old_text: str | None, new_text: str | None) -> bool:
    old_text = old_text or ""
    new_text = new_text or ""
    if old_text == new_text:
        return False
    return text_has_anchor_data(old_text) or text_has_anchor_data(new_text)


def find_anchor_on_page(
    repository: AnnotationRepository,
    doc: fitz.Document,
    page: fitz.Page,
    page_index: int,
    symbol: str,
    page_basis: str = "physical",
) -> AnchorModel | None:
    target_number = SERIAL_NUMBER_BY_SYMBOL.get(symbol)
    if target_number is None:
        return None
    for annot in repository.iter_page_annotations_by_page(page, page_index):
        try:
            model = repository.annotation_to_model(page_index, annot)
        except Exception as exc:
            repository.add_warning(page_index, "resolve_anchor_annotation_to_model", exc, annot)
            continue
        if model.app_type != "freetext":
            continue
        if serial_number(model.text) != target_number:
            continue
        page_number = reference_page_number_for_basis(doc, page_index, page_basis)
        return AnchorModel(
            reference=anchor_reference(page_number, symbol),
            symbol=symbol,
            serial_number=target_number,
            page_index=page_index,
            page_number=page_number,
            xref=model.xref,
            text=model.text,
            rect=fitz.Rect(model.rect),
            creation_date=model.creation_date,
            modified_date=model.modified_date,
        )
    return None


def resolve_anchor_reference(
    doc: fitz.Document,
    reference: str,
    preferred_basis: str = "physical",
) -> AnchorResolveResult | None:
    parsed = parse_anchor_reference(reference)
    if parsed is None:
        return None
    page_number, symbol = parsed
    repository = AnnotationRepository(doc)
    preferred_basis = normalized_anchor_page_basis(preferred_basis)
    fallback_basis = "book" if preferred_basis == "physical" else "physical"

    def try_physical() -> AnchorModel | None:
        physical_page_index = page_number - 1
        if not 0 <= physical_page_index < len(doc):
            return None
        return find_anchor_on_page(
            repository,
            doc,
            doc[physical_page_index],
            physical_page_index,
            symbol,
            preferred_basis,
        )

    def try_book() -> AnchorModel | None:
        try:
            book_page_indexes = doc.get_page_numbers(str(page_number))
        except Exception:
            book_page_indexes = []
        for page_index in book_page_indexes:
            page_index = int(page_index)
            if 0 <= page_index < len(doc):
                anchor = find_anchor_on_page(
                    repository,
                    doc,
                    doc[page_index],
                    page_index,
                    symbol,
                    preferred_basis,
                )
                if anchor is not None:
                    return anchor
        return None

    first = try_book if preferred_basis == "book" else try_physical
    second = try_physical if preferred_basis == "book" else try_book
    anchor = first()
    if anchor is not None:
        return AnchorResolveResult(anchor=anchor, matched_basis=preferred_basis, fallback_used=False)
    anchor = second()
    if anchor is not None:
        return AnchorResolveResult(anchor=anchor, matched_basis=fallback_basis, fallback_used=True)
    return None


def scan_document_anchors(doc: fitz.Document) -> list[AnchorModel]:
    return scan_document_anchor_data(doc).anchors


def scan_document_anchor_data(doc: fitz.Document, page_basis: str = "physical") -> AnchorScanResult:
    page_basis = normalized_anchor_page_basis(page_basis)
    repository = AnnotationRepository(doc)
    anchors: list[AnchorModel] = []
    references: list[AnchorReferenceModel] = []
    reference_aliases: dict[str, str] = {}
    for page_index in range(len(doc)):
        page = doc[page_index]
        for annot in repository.iter_page_annotations_by_page(page, page_index):
            try:
                model = repository.annotation_to_model(page_index, annot)
            except Exception as exc:
                repository.add_warning(page_index, "anchor_annotation_to_model", exc, annot)
                continue
            if model.app_type != "freetext":
                continue
            display_page_number = reference_page_number_for_basis(doc, page_index, page_basis)
            physical_page_number = page_index + 1
            book_label = book_page_label(doc, page_index).strip()
            if is_anchor_text(model.text):
                number = serial_number(model.text)
                if number is None:
                    continue
                symbol = SERIAL_BY_NUMBER.get(number, "")
                display_reference = anchor_reference(display_page_number, symbol)
                if symbol:
                    reference_aliases[anchor_reference(physical_page_number, symbol)] = display_reference
                    if book_label.isdigit():
                        reference_aliases[anchor_reference(int(book_label), symbol)] = display_reference
                anchors.append(
                    AnchorModel(
                        reference=display_reference,
                        symbol=symbol,
                        serial_number=number,
                        page_index=page_index,
                        page_number=display_page_number,
                        xref=model.xref,
                        text=model.text,
                        rect=fitz.Rect(model.rect),
                        creation_date=model.creation_date,
                        modified_date=model.modified_date,
                    )
                )
            for reference in references_in_text(model.text):
                references.append(
                    AnchorReferenceModel(
                        reference=reference,
                        page_index=page_index,
                        page_number=display_page_number,
                        xref=model.xref,
                        text=model.text,
                        rect=fitz.Rect(model.rect),
                    )
                )

    anchor_names = {anchor.reference for anchor in anchors}
    references_by_anchor: dict[str, list[AnchorReferenceModel]] = {name: [] for name in anchor_names}
    unresolved: list[AnchorReferenceModel] = []
    for reference in references:
        target_reference = reference_aliases.get(reference.reference, reference.reference)
        if target_reference in references_by_anchor:
            references_by_anchor[target_reference].append(reference)
        else:
            unresolved.append(reference)

    return AnchorScanResult(
        anchors=anchors,
        references_by_anchor=references_by_anchor,
        unresolved_references=unresolved,
    )
