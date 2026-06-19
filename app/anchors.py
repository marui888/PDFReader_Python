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


def references_in_text(text: str) -> list[str]:
    return [match.group(0) for match in REFERENCE_PATTERN.finditer(text)]


def scan_document_anchors(doc: fitz.Document) -> list[AnchorModel]:
    return scan_document_anchor_data(doc).anchors


def scan_document_anchor_data(doc: fitz.Document) -> AnchorScanResult:
    repository = AnnotationRepository(doc)
    anchors: list[AnchorModel] = []
    references: list[AnchorReferenceModel] = []
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
            page_number = page_index + 1
            if is_anchor_text(model.text):
                number = serial_number(model.text)
                if number is None:
                    continue
                symbol = SERIAL_BY_NUMBER.get(number, "")
                anchors.append(
                    AnchorModel(
                        reference=anchor_reference(page_number, symbol),
                        symbol=symbol,
                        serial_number=number,
                        page_index=page_index,
                        page_number=page_number,
                        xref=model.xref,
                        text=model.text,
                        rect=fitz.Rect(model.rect),
                    )
                )
            for reference in references_in_text(model.text):
                references.append(
                    AnchorReferenceModel(
                        reference=reference,
                        page_index=page_index,
                        page_number=page_number,
                        xref=model.xref,
                        text=model.text,
                        rect=fitz.Rect(model.rect),
                    )
                )

    anchor_names = {anchor.reference for anchor in anchors}
    references_by_anchor: dict[str, list[AnchorReferenceModel]] = {name: [] for name in anchor_names}
    unresolved: list[AnchorReferenceModel] = []
    for reference in references:
        if reference.reference in references_by_anchor:
            references_by_anchor[reference.reference].append(reference)
        else:
            unresolved.append(reference)

    return AnchorScanResult(
        anchors=anchors,
        references_by_anchor=references_by_anchor,
        unresolved_references=unresolved,
    )
