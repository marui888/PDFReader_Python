from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz


@dataclass
class SharedDocument:
    """One editable PDF, regardless of how many reading tabs display it."""

    doc: fitz.Document | None
    path: Path
    is_dirty: bool = False
    undo_action: object | None = None
    freetext_snapshot: object | None = None


@dataclass(eq=False)
class DocumentSession:
    document: SharedDocument
    page_index: int = 0
    zoom: float = 1.5
    selected_annotation_id: str | None = None
    view_mode: str = "continuous"
    view_anchor: dict | None = None
    history_back: list = field(default_factory=list)
    history_forward: list = field(default_factory=list)
    last_activated: int = 0

    @property
    def doc(self):
        return self.document.doc

    @doc.setter
    def doc(self, value):
        self.document.doc = value

    @property
    def path(self):
        return self.document.path

    @path.setter
    def path(self, value):
        self.document.path = value

    @property
    def is_dirty(self):
        return self.document.is_dirty

    @is_dirty.setter
    def is_dirty(self, value):
        self.document.is_dirty = value

    @property
    def freetext_snapshot(self):
        return self.document.freetext_snapshot

    @freetext_snapshot.setter
    def freetext_snapshot(self, value):
        self.document.freetext_snapshot = value
