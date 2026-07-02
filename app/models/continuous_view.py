from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem


@dataclass
class PageLayout:
    page_index: int
    page_width: float
    page_height: float
    scene_rect: QRectF
    y_top: float
    y_bottom: float


@dataclass
class PageViewItem:
    page_index: int
    pixmap_item: QGraphicsPixmapItem | None = None
    annotation_items: list[QGraphicsItem] | None = None
    annotation_model_ids: list[str] | None = None
    is_rendered: bool = False
    annotation_count: int = 0
    supported_annotation_count: int = 0
    unsupported_annotation_count: int = 0
    pixmap_ms: float = 0.0
    annotation_read_ms: float = 0.0
    overlay_ms: float = 0.0
    total_ms: float = 0.0
    annotation_error: str | None = None
