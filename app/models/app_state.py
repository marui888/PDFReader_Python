from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from app.models.annotation_model import AnnotationModel
from app.models.document_session import DocumentSession
from app.models.undo import UndoAction


@dataclass
class DocumentState:
    doc: fitz.Document | None = None
    annotation_repo: object | None = None
    pdf_path: Path | None = None
    page_index: int = 0
    zoom: float = 1.5
    is_dirty: bool = False
    sessions: list[DocumentSession] = field(default_factory=list)
    active_session_index: int | None = None


@dataclass
class AnnotationViewState:
    current_annotations: list[AnnotationModel] = field(default_factory=list)
    annotation_items: list[QGraphicsItem] = field(default_factory=list)
    annotation_item_map: dict[str, list[QGraphicsItem]] = field(default_factory=dict)
    annotation_model_map: dict[str, AnnotationModel] = field(default_factory=dict)
    selection_items: list[QGraphicsItem] = field(default_factory=list)
    selected_annotation_id: str | None = None


@dataclass
class InteractionState:
    active_scene_drag_kind: str | None = None
    active_scene_drag_annotation_id: str | None = None
    active_scene_drag_start_pos: QPointF | None = None
    active_tool: str | None = None
    tool_start_scene_pos: QPointF | None = None
    tool_preview_item: QGraphicsItem | None = None
    tool_preview_items: list[QGraphicsItem] = field(default_factory=list)


@dataclass
class UiSyncState:
    updating_document_tabs: bool = False
    updating_page_spin: bool = False
    updating_table_selection: bool = False
    updating_scene_selection: bool = False
    applying_property_change: bool = False


@dataclass
class NavigationState:
    anchor_doc_id: int | None = None
    anchor_dirty: bool = True
    anchor_cache: dict[int, object] = field(default_factory=dict)


@dataclass
class SearchRuntimeState:
    restore_geometry: object | None = None
    maximized: bool = False
    reindex_pdf_path: Path | None = None


@dataclass
class TextCacheState:
    page_index: int | None = None
    lines: list[list[dict]] | None = None


@dataclass
class UndoState:
    action: UndoAction | None = None


@dataclass
class AppState:
    document: DocumentState = field(default_factory=DocumentState)
    annotations: AnnotationViewState = field(default_factory=AnnotationViewState)
    interaction: InteractionState = field(default_factory=InteractionState)
    ui_sync: UiSyncState = field(default_factory=UiSyncState)
    navigation: NavigationState = field(default_factory=NavigationState)
    search: SearchRuntimeState = field(default_factory=SearchRuntimeState)
    text_cache: TextCacheState = field(default_factory=TextCacheState)
    undo: UndoState = field(default_factory=UndoState)
