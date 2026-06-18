from dataclasses import dataclass

import pymupdf as fitz


@dataclass
class UndoAction:
    label: str
    page_index: int
    xref: int
    app_type: str
    operation: str = "geometry"
    rect: fitz.Rect | None = None
    text: str = ""
    color: tuple | None = None
    border_width: float | None = None
    font_size: float | None = None
    opacity: float | None = None
    quad_points: list[tuple[float, float]] | None = None
    line_start: tuple[float, float] | None = None
    line_end: tuple[float, float] | None = None
    line_ending: str = ""
