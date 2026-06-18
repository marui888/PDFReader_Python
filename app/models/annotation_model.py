from dataclasses import dataclass

import pymupdf as fitz


SUPPORTED_APP_TYPES = {"highlight", "freetext", "square", "arrow"}
DRAGGABLE_APP_TYPES = {"freetext", "square", "arrow"}
EDITABLE_APP_TYPES = {"freetext", "square", "arrow"}
ANNOTATION_COLORS = {
    "Red": (1, 0, 0),
    "Black": (0, 0, 0),
    "Blue": (0, 0, 1),
    "Green": (0, 0.55, 0),
    "Yellow": (1, 0.85, 0),
}


@dataclass
class AnnotationModel:
    id: str
    xref: int
    page_index: int
    pdf_type: str
    app_type: str
    rect: fitz.Rect
    text: str
    color: tuple | None
    border_width: float | None
    font_size: float | None
    opacity: float | None
    quad_points: list[tuple[float, float]]
    line_start: tuple[float, float] | None
    line_end: tuple[float, float] | None
    line_ending: str
    is_supported: bool
    dirty: bool = False
    deleted: bool = False
    source: str = "pdf"
