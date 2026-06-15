from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ViewLocation:
    document_path: str
    page_index: int
    zoom: float
    scroll_x: int
    scroll_y: int
    selected_xref: int | None = None

    def is_near(self, other: "ViewLocation") -> bool:
        return (
            self.document_path == other.document_path
            and self.page_index == other.page_index
            and abs(self.zoom - other.zoom) < 0.001
            and abs(self.scroll_x - other.scroll_x) < 4
            and abs(self.scroll_y - other.scroll_y) < 4
            and self.selected_xref == other.selected_xref
        )
