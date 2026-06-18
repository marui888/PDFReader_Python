from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz


@dataclass
class DocumentSession:
    doc: fitz.Document
    path: Path
    page_index: int = 0
    zoom: float = 1.5
    is_dirty: bool = False
    selected_annotation_id: str | None = None

