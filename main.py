import sys
from datetime import datetime
from math import ceil
from pathlib import Path

import pymupdf as fitz
from app.services.annotation_index import AnnotationIndex
from app.repositories.annotation_repository import AnnotationRepository
from app.search.annotation_search_query import AnnotationSearchQuery
from app.controllers.annotation_controller import AnnotationController
from app.controllers.canvas_controller import CanvasController
from app.controllers.document_controller import DocumentController
from app.controllers.navigation_controller import NavigationController
from app.controllers.search_controller import SearchController
from app.controllers.view_history_controller import ViewHistoryController
from app.models.app_state import AppState
from app.models.document_session import DocumentSession
from app.services.index_worker import ReindexWorker
from app.main_window import actions as main_window_actions
from app.main_window import dialogs as main_window_dialogs
from app.main_window import docks as main_window_docks
from app.main_window import view as main_window_view
from app.models.annotation_model import (
    ANNOTATION_COLORS,
    EDITABLE_APP_TYPES,
    AnnotationModel,
)
from app.canvas.pdf_canvas import AnnotationScene, PdfCanvasView
from app.canvas.inline_freetext_editor import InlineFreeTextEditorManager
from app.services.freetext_batch import FreeTextMatch
from app.services.freetext_batch import add_text_to_match
from app.services.freetext_batch import delete_match_text
from app.services.freetext_batch import find_freetext_matches
from app.services.freetext_batch import replace_match_text
from app.services.pdf_audit import audit_current_page as run_audit_current_page
from app.services.pdf_audit import audit_document_summary as run_audit_document_summary
from app.services.pdf_audit import format_audit_report
from app.services.pdf_annotation_writer import PdfAnnotationWriter
from app.services.settings import AppSettings, load_settings, save_settings, settings_path
from app.models.undo import UndoAction
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QMainWindow,
    QPlainTextEdit,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.doc: fitz.Document | None = None
        self.annotation_repo: AnnotationRepository | None = None
        self.pdf_path: Path | None = None
        self.page_index = 0
        self.zoom = 1.5
        self.use_foxit_freetext = False
        self.use_popup_freetext_input = False
        self.freetext_font_size_min = 4
        self.freetext_font_size_max = 20
        self.default_freetext_font_size = 7
        self.default_highlight_color = (1, 1, 0)
        self.default_highlight_opacity = 0.45
        self.extract_highlight_text_on_reindex = False
        self.quick_audit_detailed = False
        self.qpdf_bin_dir = r"D:\tools\qpdf-12.3.2-msvc64\bin"
        self.save_incremental_safety_default = True
        self.search_page_size = 500
        self.recent_files: list[dict] = []
        self.recent_search_rule_files: list[str] = []
        self.max_recent_files = 10
        self.debug_log: list[str] = []
        self.annotation_index = AnnotationIndex(self.index_path())
        self.load_app_settings()
        self.is_dirty = False
        self.sessions: list[DocumentSession] = []
        self.active_session_index: int | None = None
        self.updating_document_tabs = False
        self.undo_action: UndoAction | None = None
        self.current_annotations: list[AnnotationModel] = []
        self.annotation_items: list[QGraphicsItem] = []
        self.annotation_item_map: dict[str, list[QGraphicsItem]] = {}
        self.annotation_model_map: dict[str, AnnotationModel] = {}
        self.selection_items: list[QGraphicsItem] = []
        self.selected_annotation_id: str | None = None
        self.active_scene_drag_kind: str | None = None
        self.active_scene_drag_annotation_id: str | None = None
        self.active_scene_drag_start_pos: QPointF | None = None
        self.annotations_dock: QDockWidget | None = None
        self.annotations_tabs: QTabWidget | None = None
        self.debug_log_dock: QDockWidget | None = None
        self.debug_log_text: QPlainTextEdit | None = None
        self.index_database_info_dock: QDockWidget | None = None
        self.index_database_info_text: QPlainTextEdit | None = None
        self.annotation_search_dock: QDockWidget | None = None
        self.annotation_search_widget: object | None = None
        self.annotation_search_restore_geometry = None
        self.annotation_search_maximized = False
        self.navigation_dock: QDockWidget | None = None
        self.navigation_widget: object | None = None
        self.navigation_anchor_doc_id: int | None = None
        self.navigation_anchor_dirty = True
        self.annotation_controller = AnnotationController(self)
        self.canvas_controller = CanvasController(self)
        self.document_controller = DocumentController(self)
        self.view_history_controller = ViewHistoryController(self)
        self.navigation_controller = NavigationController(self)
        self.search_controller = SearchController(self)
        self.reindex_thread: QThread | None = None
        self.reindex_worker: ReindexWorker | None = None
        self.reindex_pdf_path: Path | None = None
        self.open_recent_menu = None
        self.annotations_table: object | None = None
        self.quick_audit_text: QPlainTextEdit | None = None
        self.properties_page: object | None = None
        self.batch_freetext_widget: object | None = None
        self.updating_page_spin = False
        self.updating_table_selection = False
        self.updating_scene_selection = False
        self.applying_property_change = False
        self.active_tool: str | None = None
        self.tool_start_scene_pos: QPointF | None = None
        self.tool_preview_item: QGraphicsItem | None = None
        self.tool_preview_items: list[QGraphicsItem] = []
        self.inline_freetext_editor: InlineFreeTextEditorManager | None = None
        self.inline_freetext_edit_annotation_id: str | None = None
        self.hidden_inline_freetext_annotation_id: str | None = None
        self.text_lines_cache_page_index: int | None = None
        self.text_lines_cache: list[list[dict]] | None = None

        self.scene = AnnotationScene(self)
        self.scene.selectionChanged.connect(self.on_scene_selection_changed)
        self.view = PdfCanvasView(self)
        self.view.setScene(self.scene)
        self.page_item = QGraphicsPixmapItem()
        self.page_item.setZValue(0)
        self.scene.addItem(self.page_item)
        self.inline_freetext_editor = InlineFreeTextEditorManager(
            self.scene,
            lambda: self.zoom,
            self.on_inline_freetext_accept,
            self.on_inline_freetext_cancel,
        )
        self.document_tabs = QTabBar()
        self.document_tabs.setExpanding(False)
        self.document_tabs.setMovable(False)
        self.document_tabs.setVisible(False)
        self.document_tabs.setStyleSheet(
            """
            QTabBar::tab {
                background: #e7e7e7;
                border: 1px solid #b8b8b8;
                border-bottom-color: #8f8f8f;
                padding: 5px 12px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: white;
                border: 2px solid #4f7ecb;
                border-bottom-color: white;
                font-weight: 700;
            }
            QTabBar::tab:!selected {
                color: #444;
            }
            """
        )
        self.document_tabs.currentChanged.connect(self.on_document_tab_changed)
        self.document_tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.document_tabs.customContextMenuRequested.connect(self.show_document_tab_context_menu)

        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.document_tabs)
        central_layout.addWidget(self.view, 1)
        self.setCentralWidget(central_widget)
        self.show_navigation()
        main_window_view.create_scroll_boundary_label(self)

        self.update_window_title()
        self.resize(1200, 850)
        self.create_actions()
        self.create_menus()
        self.create_toolbar()
        self.show_current_page_annotations()
        self.update_actions()

    @property
    def updating_document_tabs(self) -> bool:
        return self.state.ui_sync.updating_document_tabs

    @updating_document_tabs.setter
    def updating_document_tabs(self, value: bool) -> None:
        self.state.ui_sync.updating_document_tabs = value

    @property
    def updating_page_spin(self) -> bool:
        return self.state.ui_sync.updating_page_spin

    @updating_page_spin.setter
    def updating_page_spin(self, value: bool) -> None:
        self.state.ui_sync.updating_page_spin = value

    @property
    def updating_table_selection(self) -> bool:
        return self.state.ui_sync.updating_table_selection

    @updating_table_selection.setter
    def updating_table_selection(self, value: bool) -> None:
        self.state.ui_sync.updating_table_selection = value

    @property
    def updating_scene_selection(self) -> bool:
        return self.state.ui_sync.updating_scene_selection

    @updating_scene_selection.setter
    def updating_scene_selection(self, value: bool) -> None:
        self.state.ui_sync.updating_scene_selection = value

    @property
    def applying_property_change(self) -> bool:
        return self.state.ui_sync.applying_property_change

    @applying_property_change.setter
    def applying_property_change(self, value: bool) -> None:
        self.state.ui_sync.applying_property_change = value

    @property
    def active_scene_drag_kind(self) -> str | None:
        return self.state.interaction.active_scene_drag_kind

    @active_scene_drag_kind.setter
    def active_scene_drag_kind(self, value: str | None) -> None:
        self.state.interaction.active_scene_drag_kind = value

    @property
    def active_scene_drag_annotation_id(self) -> str | None:
        return self.state.interaction.active_scene_drag_annotation_id

    @active_scene_drag_annotation_id.setter
    def active_scene_drag_annotation_id(self, value: str | None) -> None:
        self.state.interaction.active_scene_drag_annotation_id = value

    @property
    def active_scene_drag_start_pos(self) -> QPointF | None:
        return self.state.interaction.active_scene_drag_start_pos

    @active_scene_drag_start_pos.setter
    def active_scene_drag_start_pos(self, value: QPointF | None) -> None:
        self.state.interaction.active_scene_drag_start_pos = value

    @property
    def active_tool(self) -> str | None:
        return self.state.interaction.active_tool

    @active_tool.setter
    def active_tool(self, value: str | None) -> None:
        self.state.interaction.active_tool = value

    @property
    def tool_start_scene_pos(self) -> QPointF | None:
        return self.state.interaction.tool_start_scene_pos

    @tool_start_scene_pos.setter
    def tool_start_scene_pos(self, value: QPointF | None) -> None:
        self.state.interaction.tool_start_scene_pos = value

    @property
    def tool_preview_item(self) -> QGraphicsItem | None:
        return self.state.interaction.tool_preview_item

    @tool_preview_item.setter
    def tool_preview_item(self, value: QGraphicsItem | None) -> None:
        self.state.interaction.tool_preview_item = value

    @property
    def tool_preview_items(self) -> list[QGraphicsItem]:
        return self.state.interaction.tool_preview_items

    @tool_preview_items.setter
    def tool_preview_items(self, value: list[QGraphicsItem]) -> None:
        self.state.interaction.tool_preview_items = value

    @property
    def current_annotations(self) -> list[AnnotationModel]:
        return self.state.annotations.current_annotations

    @current_annotations.setter
    def current_annotations(self, value: list[AnnotationModel]) -> None:
        self.state.annotations.current_annotations = value

    @property
    def annotation_items(self) -> list[QGraphicsItem]:
        return self.state.annotations.annotation_items

    @annotation_items.setter
    def annotation_items(self, value: list[QGraphicsItem]) -> None:
        self.state.annotations.annotation_items = value

    @property
    def annotation_item_map(self) -> dict[str, list[QGraphicsItem]]:
        return self.state.annotations.annotation_item_map

    @annotation_item_map.setter
    def annotation_item_map(self, value: dict[str, list[QGraphicsItem]]) -> None:
        self.state.annotations.annotation_item_map = value

    @property
    def annotation_model_map(self) -> dict[str, AnnotationModel]:
        return self.state.annotations.annotation_model_map

    @annotation_model_map.setter
    def annotation_model_map(self, value: dict[str, AnnotationModel]) -> None:
        self.state.annotations.annotation_model_map = value

    @property
    def selection_items(self) -> list[QGraphicsItem]:
        return self.state.annotations.selection_items

    @selection_items.setter
    def selection_items(self, value: list[QGraphicsItem]) -> None:
        self.state.annotations.selection_items = value

    @property
    def selected_annotation_id(self) -> str | None:
        return self.state.annotations.selected_annotation_id

    @selected_annotation_id.setter
    def selected_annotation_id(self, value: str | None) -> None:
        self.state.annotations.selected_annotation_id = value

    @property
    def navigation_anchor_doc_id(self) -> int | None:
        return self.state.navigation.anchor_doc_id

    @navigation_anchor_doc_id.setter
    def navigation_anchor_doc_id(self, value: int | None) -> None:
        self.state.navigation.anchor_doc_id = value

    @property
    def navigation_anchor_dirty(self) -> bool:
        return self.state.navigation.anchor_dirty

    @navigation_anchor_dirty.setter
    def navigation_anchor_dirty(self, value: bool) -> None:
        self.state.navigation.anchor_dirty = value

    @property
    def text_lines_cache_page_index(self) -> int | None:
        return self.state.text_cache.page_index

    @text_lines_cache_page_index.setter
    def text_lines_cache_page_index(self, value: int | None) -> None:
        self.state.text_cache.page_index = value

    @property
    def text_lines_cache(self) -> list[list[dict]] | None:
        return self.state.text_cache.lines

    @text_lines_cache.setter
    def text_lines_cache(self, value: list[list[dict]] | None) -> None:
        self.state.text_cache.lines = value

    @property
    def undo_action(self) -> UndoAction | None:
        return self.state.undo.action

    @undo_action.setter
    def undo_action(self, value: UndoAction | None) -> None:
        self.state.undo.action = value

    @property
    def annotation_search_restore_geometry(self):
        return self.state.search.restore_geometry

    @annotation_search_restore_geometry.setter
    def annotation_search_restore_geometry(self, value) -> None:
        self.state.search.restore_geometry = value

    @property
    def annotation_search_maximized(self) -> bool:
        return self.state.search.maximized

    @annotation_search_maximized.setter
    def annotation_search_maximized(self, value: bool) -> None:
        self.state.search.maximized = value

    @property
    def reindex_pdf_path(self) -> Path | None:
        return self.state.search.reindex_pdf_path

    @reindex_pdf_path.setter
    def reindex_pdf_path(self, value: Path | None) -> None:
        self.state.search.reindex_pdf_path = value

    @property
    def doc(self) -> fitz.Document | None:
        return self.state.document.doc

    @doc.setter
    def doc(self, value: fitz.Document | None) -> None:
        self.state.document.doc = value

    @property
    def annotation_repo(self) -> AnnotationRepository | None:
        return self.state.document.annotation_repo

    @annotation_repo.setter
    def annotation_repo(self, value: AnnotationRepository | None) -> None:
        self.state.document.annotation_repo = value

    @property
    def pdf_path(self) -> Path | None:
        return self.state.document.pdf_path

    @pdf_path.setter
    def pdf_path(self, value: Path | None) -> None:
        self.state.document.pdf_path = value

    @property
    def page_index(self) -> int:
        return self.state.document.page_index

    @page_index.setter
    def page_index(self, value: int) -> None:
        self.state.document.page_index = value

    @property
    def zoom(self) -> float:
        return self.state.document.zoom

    @zoom.setter
    def zoom(self, value: float) -> None:
        self.state.document.zoom = value

    @property
    def is_dirty(self) -> bool:
        return self.state.document.is_dirty

    @is_dirty.setter
    def is_dirty(self, value: bool) -> None:
        self.state.document.is_dirty = value

    @property
    def sessions(self) -> list[DocumentSession]:
        return self.state.document.sessions

    @sessions.setter
    def sessions(self, value: list[DocumentSession]) -> None:
        self.state.document.sessions = value

    @property
    def active_session_index(self) -> int | None:
        return self.state.document.active_session_index

    @active_session_index.setter
    def active_session_index(self, value: int | None) -> None:
        self.state.document.active_session_index = value

    def create_actions(self) -> None:
        main_window_actions.create_actions(self)

    def create_menus(self) -> None:
        main_window_actions.create_menus(self)

    def create_toolbar(self) -> None:
        main_window_actions.create_toolbar(self)

    def update_actions(self) -> None:
        main_window_actions.update_actions(self)

    def open_pdf(self) -> None:
        self.document_controller.open_pdf()

    def open_pdf_path(self, path: Path, page_index: int = 0) -> bool:
        return self.document_controller.open_pdf_path(path, page_index)

    def close_pdf(self) -> None:
        self.document_controller.close_pdf()

    def session_index_for_path(self, path: Path) -> int | None:
        return self.document_controller.session_index_for_path(path)

    def save_active_session_state(self) -> None:
        self.document_controller.save_active_session_state()

    def update_document_tab_title(self, index: int | None = None) -> None:
        self.document_controller.update_document_tab_title(index)

    def set_active_session(self, index: int, preserve_selection: bool = False) -> None:
        self.document_controller.set_active_session(index, preserve_selection)

    def on_document_tab_changed(self, index: int) -> None:
        self.document_controller.on_document_tab_changed(index)

    def show_document_tab_context_menu(self, pos) -> None:
        self.document_controller.show_document_tab_context_menu(pos)

    def backup_current_pdf(self) -> None:
        self.document_controller.backup_current_pdf()

    def qpdf_check_current_pdf(self) -> None:
        self.document_controller.qpdf_check_current_pdf()

    def qpdf_rewrite_current_pdf(self) -> None:
        self.document_controller.qpdf_rewrite_current_pdf()

    def sync_page_spin(self) -> None:
        main_window_view.sync_page_spin(self)

    def mark_dirty(self) -> None:
        self.is_dirty = True
        self.navigation_anchor_dirty = True
        if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
            self.sessions[self.active_session_index].is_dirty = True
            self.update_document_tab_title(self.active_session_index)
        self.update_window_title()

    def clear_dirty(self) -> None:
        self.is_dirty = False
        if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
            self.sessions[self.active_session_index].is_dirty = False
            self.update_document_tab_title(self.active_session_index)
        self.update_window_title()

    def clear_undo(self) -> None:
        self.undo_action = None
        if hasattr(self, "undo_action_qt"):
            self.update_actions()

    def update_window_title(self) -> None:
        main_window_view.update_window_title(self)

    def confirm_active_unsaved_changes(self, action_text: str) -> bool:
        return self.document_controller.confirm_active_unsaved_changes(action_text)

    def confirm_unsaved_session(self, index: int, action_text: str) -> bool:
        return self.document_controller.confirm_unsaved_session(index, action_text)

    def confirm_all_unsaved_for_exit(self) -> bool:
        return self.document_controller.confirm_all_unsaved_for_exit()

    def go_to_page(self, page_number: int) -> None:
        if self.updating_page_spin or self.doc is None:
            return

        target_index = max(0, min(page_number - 1, len(self.doc) - 1))
        if target_index == self.page_index:
            return

        self.record_view_location_before_navigation()
        self.page_index = target_index
        self.cancel_add_tool()
        self.render_page()
        self.update_current_recent_page()
        self.save_active_session_state()

    def record_view_location_before_navigation(self) -> None:
        self.view_history_controller.record_before_navigation()

    def go_back_view(self) -> None:
        self.view_history_controller.go_back()

    def go_forward_view(self) -> None:
        self.view_history_controller.go_forward()

    def current_page(self) -> fitz.Page:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        return self.doc[self.page_index]

    def settings_path(self) -> Path:
        return settings_path(__file__)

    def index_path(self) -> Path:
        return Path(__file__).with_name("PDFReaderIndex.sqlite3")

    def search_rules_dir(self) -> Path:
        return Path(__file__).with_name("search_rules")

    def load_app_settings(self) -> None:
        settings = load_settings(self.settings_path(), self.max_recent_files)
        self.use_foxit_freetext = settings.use_foxit_freetext
        self.use_popup_freetext_input = settings.use_popup_freetext_input
        self.freetext_font_size_min = settings.freetext_font_size_min
        self.freetext_font_size_max = settings.freetext_font_size_max
        self.default_freetext_font_size = settings.default_freetext_font_size
        self.default_highlight_color = settings.default_highlight_color
        self.default_highlight_opacity = settings.default_highlight_opacity
        self.extract_highlight_text_on_reindex = settings.extract_highlight_text_on_reindex
        self.quick_audit_detailed = settings.quick_audit_detailed
        self.qpdf_bin_dir = settings.qpdf_bin_dir
        self.save_incremental_safety_default = settings.save_incremental_safety_default
        self.search_page_size = settings.search_page_size
        self.recent_files = settings.recent_files
        self.recent_search_rule_files = settings.recent_search_rule_files

    def save_app_settings(self) -> None:
        settings = AppSettings(
            use_foxit_freetext=self.use_foxit_freetext,
            use_popup_freetext_input=self.use_popup_freetext_input,
            freetext_font_size_min=self.freetext_font_size_min,
            freetext_font_size_max=self.freetext_font_size_max,
            default_freetext_font_size=self.default_freetext_font_size,
            default_highlight_color=self.default_highlight_color,
            default_highlight_opacity=self.default_highlight_opacity,
            extract_highlight_text_on_reindex=self.extract_highlight_text_on_reindex,
            quick_audit_detailed=self.quick_audit_detailed,
            qpdf_bin_dir=self.qpdf_bin_dir,
            save_incremental_safety_default=self.save_incremental_safety_default,
            search_page_size=self.search_page_size,
            recent_files=self.recent_files,
            recent_search_rule_files=self.recent_search_rule_files,
        )
        save_settings(self.settings_path(), settings)

    def update_recent_search_rule_files(self, recent_files: list[str]) -> None:
        self.recent_search_rule_files = list(recent_files)[: self.max_recent_files]
        self.save_app_settings()

    def clamp_freetext_font_size(self, value: int) -> int:
        return max(self.freetext_font_size_min, min(self.freetext_font_size_max, int(value)))

    def update_recent_file(self, path: Path, page_index: int | None = None) -> None:
        self.document_controller.update_recent_file(path, page_index)

    def update_current_recent_page(self) -> None:
        self.document_controller.update_current_recent_page()

    def refresh_recent_files_menu(self) -> None:
        self.document_controller.refresh_recent_files_menu()

    def clear_recent_files(self) -> None:
        self.document_controller.clear_recent_files()

    def open_recent_pdf(self, path: Path) -> None:
        self.document_controller.open_recent_pdf(path)

    def render_page(self, preserve_selection: bool = False, keep_view_position: bool = False) -> None:
        self.canvas_controller.render_page(preserve_selection, keep_view_position)

    def show_scroll_boundary_status(self, direction: str) -> None:
        main_window_view.show_scroll_boundary_status(self, direction)

    def clear_scroll_boundary_status(self) -> None:
        main_window_view.clear_scroll_boundary_status(self)

    def render_annotation_overlay(self) -> None:
        self.canvas_controller.render_annotation_overlay()

    def refresh_annotation_overlay(self, preserve_selection: bool = True) -> None:
        self.canvas_controller.refresh_annotation_overlay(preserve_selection)

    def clear_annotation_items(self) -> None:
        self.canvas_controller.clear_annotation_items()

    def is_draggable_model(self, model: AnnotationModel) -> bool:
        return self.canvas_controller.is_draggable_model(model)

    def scene_rect(self, rect: fitz.Rect) -> QRectF:
        return self.canvas_controller.scene_rect(rect)

    def arrow_points(self, model: AnnotationModel) -> tuple[QPointF, QPointF]:
        return self.canvas_controller.arrow_points(model)

    def on_scene_selection_changed(self) -> None:
        self.annotation_controller.on_scene_selection_changed()

    def on_scene_mouse_press(self, scene_pos: QPointF) -> None:
        self.annotation_controller.on_scene_mouse_press(scene_pos)

    def clear_scene_drag_state(self) -> None:
        self.annotation_controller.clear_scene_drag_state()

    def annotation_hit_at_scene_pos(self, scene_pos: QPointF) -> tuple[str, str | None] | None:
        return self.annotation_controller.annotation_hit_at_scene_pos(scene_pos)

    def prepare_annotation_drag(self, annotation_id: str) -> None:
        self.annotation_controller.prepare_annotation_drag(annotation_id)

    def restore_annotation_drag_preview(self, annotation_id: str) -> None:
        self.annotation_controller.restore_annotation_drag_preview(annotation_id)

    def is_intentional_annotation_move(self, delta: QPointF) -> bool:
        return self.annotation_controller.is_intentional_annotation_move(delta)

    def on_scene_mouse_release(self, scene_pos: QPointF | None = None) -> None:
        self.annotation_controller.on_scene_mouse_release(scene_pos)

    def record_geometry_undo(self, label: str, model: AnnotationModel) -> None:
        self.annotation_controller.record_geometry_undo(label, model)

    def record_add_undo(self, label: str, page_index: int, xref: int) -> None:
        self.annotation_controller.record_add_undo(label, page_index, xref)

    def record_delete_undo(self, model: AnnotationModel) -> None:
        self.annotation_controller.record_delete_undo(model)

    def undo_last_action(self) -> None:
        self.annotation_controller.undo_last_action()

    def restore_undo_action(self, action: UndoAction) -> int | None:
        if action.operation == "add":
            self.undo_added_annotation(action)
            return None
        if action.operation == "delete":
            return self.restore_deleted_annotation(action)

        page = self.doc[action.page_index]
        annot = self.find_page_annotation_by_xref(page, action.xref)
        if annot is None:
            raise RuntimeError("The annotation to undo was not found.")
        model = self.annotation_repo.annotation_to_model(action.page_index, annot) if self.annotation_repo else None
        PdfAnnotationWriter(self.doc).restore_annotation_geometry(
            page,
            action.xref,
            action.app_type,
            action.rect,
            action.line_start,
            action.line_end,
            model,
            self.default_freetext_font_size,
        )
        return action.xref

    def undo_added_annotation(self, action: UndoAction) -> None:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        page = self.doc[action.page_index]
        try:
            PdfAnnotationWriter(self.doc).delete_annotation(page, action.xref)
        except RuntimeError:
            return

    def restore_deleted_annotation(self, action: UndoAction) -> int:
        return self.annotation_controller.restore_deleted_annotation(action)

    def highlight_rects_from_quad_points(self, quad_points: list[tuple[float, float]]) -> list[fitz.Rect]:
        return self.annotation_controller.highlight_rects_from_quad_points(quad_points)

    def on_scene_mouse_move(self, scene_pos: QPointF | None = None) -> None:
        self.annotation_controller.on_scene_mouse_move(scene_pos)

    def update_annotation_move_preview(self, model: AnnotationModel, scene_pos: QPointF | None = None) -> None:
        self.annotation_controller.update_annotation_move_preview(model, scene_pos)

    def resized_scene_rect_preview(
        self,
        model: AnnotationModel,
        handle: str,
        dx_pdf: float,
        dy_pdf: float,
    ) -> QRectF:
        return self.annotation_controller.resized_scene_rect_preview(model, handle, dx_pdf, dy_pdf)

    def on_scene_mouse_double_click(self, scene_pos: QPointF | None = None) -> None:
        if self.selected_annotation_id is None:
            return
        if (
            not self.use_popup_freetext_input
            and scene_pos is not None
            and self.begin_inline_edit_for_selected_freetext(scene_pos)
        ):
            return
        self.show_annotation_properties()

    def move_pdf_annotation(self, model: AnnotationModel, dx: float, dy: float) -> None:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(self.doc).move_annotation(self.current_page(), model, dx, dy)

    def resize_rect_annotation(self, model: AnnotationModel, corner: str, dx: float, dy: float) -> None:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(self.doc).resize_rect_annotation(
            self.current_page(),
            model,
            corner,
            dx,
            dy,
            self.default_freetext_font_size,
        )

    def set_square_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(self.doc).set_square_rect(annot, model, rect)

    def set_freetext_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(self.doc).set_freetext_rect(annot, model, rect, self.default_freetext_font_size)

    def set_line_annotation_points(
        self,
        page: fitz.Page,
        annot: fitz.Annot,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> None:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(self.doc).set_line_annotation_points(page, annot, start, end)

    def move_arrow_endpoint(self, model: AnnotationModel, endpoint: str, dx: float, dy: float) -> None:
        if model.line_start is None or model.line_end is None:
            raise RuntimeError("The selected arrow annotation has no line endpoints.")

        start = model.line_start
        end = model.line_end
        if endpoint == "start":
            start = (start[0] + dx, start[1] + dy)
        elif endpoint == "end":
            end = (end[0] + dx, end[1] + dy)
        else:
            raise RuntimeError(f"Unknown arrow endpoint: {endpoint}")

        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")
        self.set_line_annotation_points(page, annot, start, end)

    def selected_model_is_editable(self) -> bool:
        if self.selected_annotation_id is None:
            return False
        model = self.annotation_model_map.get(self.selected_annotation_id)
        return model is not None and model.app_type in EDITABLE_APP_TYPES

    def select_annotation(self, annotation_id: str | None, center_on: bool = False) -> None:
        self.annotation_controller.select_annotation(annotation_id, center_on)

    def clear_selection_items(self) -> None:
        for item in self.selection_items:
            self.scene.removeItem(item)
        self.selection_items.clear()

    def sync_scene_selection(self) -> None:
        self.updating_scene_selection = True
        try:
            for item in self.annotation_items:
                should_select = bool(self.selected_annotation_id and item.data(0) == self.selected_annotation_id)
                if item.isSelected() != should_select:
                    item.setSelected(should_select)
        finally:
            self.updating_scene_selection = False

    def sync_table_selection(self) -> None:
        if self.annotations_table is None:
            return

        self.updating_table_selection = True
        try:
            self.annotations_table.select_annotation(self.selected_annotation_id, self.current_annotations)
        finally:
            self.updating_table_selection = False

    def on_annotations_table_selection_changed(self) -> None:
        self.annotation_controller.on_annotations_table_selection_changed()

    def delete_selected_annotation(self) -> None:
        self.annotation_controller.delete_selected_annotation()

    def show_annotation_context_menu(self, scene_pos: QPointF, screen_pos) -> None:
        self.annotation_controller.show_annotation_context_menu(scene_pos, screen_pos)

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        return self.annotation_controller.find_page_annotation_by_xref(page, xref)

    def edit_selected_annotation(self) -> None:
        self.annotation_controller.edit_selected_annotation()

    def update_freetext_annotation(
        self, model: AnnotationModel, text: str, font_size: int, color: tuple[float, float, float] = (1, 0, 0)
    ) -> None:
        self.annotation_controller.update_freetext_annotation(model, text, font_size, color)

    def normalize_freetext_annotation(
        self, annot: fitz.Annot, font_size: int, color: tuple[float, float, float]
    ) -> None:
        self.annotation_controller.normalize_freetext_annotation(annot, font_size, color)

    def freetext_default_style(self, font_size: int, color: tuple[float, float, float]) -> str:
        return self.annotation_controller.freetext_default_style(font_size, color)

    def update_highlight_annotation(
        self, model: AnnotationModel, color: tuple[float, float, float], opacity: float
    ) -> None:
        self.annotation_controller.update_highlight_annotation(model, color, opacity)

    def update_stroked_annotation(self, model: AnnotationModel, color: tuple[float, float, float], width: int) -> None:
        self.annotation_controller.update_stroked_annotation(model, color, width)

    def color_name_for_tuple(self, color: tuple | None) -> str:
        return self.annotation_controller.color_name_for_tuple(color)

    def draw_selection_for_model(self, model: AnnotationModel) -> None:
        self.annotation_controller.draw_selection_for_model(model)

    def center_on_annotation(self, model: AnnotationModel) -> None:
        self.annotation_controller.center_on_annotation(model)

    def show_page_status(self) -> None:
        self.annotation_controller.show_page_status()

    def load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        return self.annotation_controller.load_page_annotations(page_index)

    def log_annotation_read_warnings(self, title: str, warnings: list, max_details: int = 30) -> None:
        self.annotation_controller.log_annotation_read_warnings(title, warnings, max_details)

    def show_navigation(self) -> None:
        self.navigation_controller.show_navigation()

    def refresh_navigation(self) -> None:
        self.navigation_controller.refresh_navigation()

    def go_to_bookmark(self, page_index: int) -> None:
        self.navigation_controller.go_to_bookmark(page_index)

    def go_to_anchor_reference_source(self, page_index: int, xref: int) -> None:
        self.navigation_controller.go_to_anchor_reference_source(page_index, xref)

    def go_to_anchor(self, page_index: int, xref: int) -> None:
        self.navigation_controller.go_to_anchor(page_index, xref)

    def can_insert_anchor_reference(self) -> bool:
        return self.navigation_controller.can_insert_anchor_reference()

    def insert_anchor_reference(self, reference: str) -> None:
        self.navigation_controller.insert_anchor_reference(reference)

    def selected_freetext_model(self) -> AnnotationModel | None:
        return self.navigation_controller.selected_freetext_model()

    def add_serial_number_to_selected_freetext(self) -> None:
        self.navigation_controller.add_serial_number_to_selected_freetext()

    def remove_serial_number_from_selected_freetext(self) -> None:
        self.navigation_controller.remove_serial_number_from_selected_freetext()

    def next_serial_number_for_current_page(self) -> int:
        return self.navigation_controller.next_serial_number_for_current_page()

    def update_selected_freetext_text(self, model: AnnotationModel, text: str, status: str) -> None:
        self.navigation_controller.update_selected_freetext_text(model, text, status)

    def go_to_anchor_reference_by_name(self, reference: str) -> None:
        self.navigation_controller.go_to_anchor_reference_by_name(reference)

    def show_current_page_annotations(self) -> None:
        main_window_docks.show_current_page_annotations(self)

    def show_annotation_properties(self) -> None:
        self.show_current_page_annotations()
        if self.annotations_tabs is not None:
            self.annotations_tabs.setCurrentIndex(1)

    def audit_current_page(self) -> None:
        if self.doc is None:
            return
        report = run_audit_current_page(self.doc, self.page_index)
        self.log_debug(
            f"Audit current page: page={self.page_index + 1} "
            f"annotations={report.annotations_found} issues={len(report.issues)}"
        )
        self.show_audit_report("Audit Current Page", format_audit_report(report))

    def audit_document_summary(self) -> None:
        if self.doc is None:
            return
        report = run_audit_document_summary(self.doc)
        self.log_debug(
            f"Audit document summary: pages={report.page_count} "
            f"annotations={report.annotations_found} issues={len(report.issues)}"
        )
        self.show_audit_report("Audit Document Summary", format_audit_report(report))

    def reindex_current_pdf(self) -> None:
        self.search_controller.reindex_current_pdf()

    @Slot(int, int, int)
    def on_reindex_progress(self, page_number: int, page_count: int, annotation_count: int) -> None:
        self.search_controller.on_reindex_progress(page_number, page_count, annotation_count)

    @Slot(int, list)
    def on_reindex_finished(self, count: int, warnings: list) -> None:
        self.search_controller.on_reindex_finished(count, warnings)

    @Slot(str)
    def on_reindex_failed(self, message: str) -> None:
        self.search_controller.on_reindex_failed(message)

    def cleanup_reindex_thread(self) -> None:
        self.search_controller.cleanup_reindex_thread()

    def clear_annotation_index(self) -> None:
        self.search_controller.clear_annotation_index()

    def show_index_database_info(self) -> None:
        self.search_controller.show_index_database_info()

    def refresh_index_database_info(self) -> None:
        self.search_controller.refresh_index_database_info()

    def show_annotation_search(self) -> None:
        self.search_controller.show_annotation_search()

    def search_annotations(self, keyword: str | AnnotationSearchQuery, app_type, document_paths=None) -> None:
        self.search_controller.search_annotations(keyword, app_type, document_paths)

    def refresh_annotation_search_status(self) -> None:
        self.search_controller.refresh_annotation_search_status()

    def on_search_dock_top_level_changed(self, floating: bool) -> None:
        self.search_controller.on_search_dock_top_level_changed(floating)

    def update_search_dock_maximize_state(self) -> None:
        self.search_controller.update_search_dock_maximize_state()

    def toggle_search_dock_maximized(self) -> None:
        self.search_controller.toggle_search_dock_maximized()

    def jump_to_search_result(self, document_path: str, page_index: int, xref: int) -> None:
        self.search_controller.jump_to_search_result(document_path, page_index, xref)

    def log_debug(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.debug_log.append(f"{timestamp} {message}")
        if len(self.debug_log) > 1000:
            self.debug_log = self.debug_log[-1000:]
        self.refresh_debug_log()

    def show_debug_log(self) -> None:
        main_window_docks.show_debug_log(self)

    def show_dock(self, dock: QDockWidget) -> None:
        dock.setVisible(True)
        dock.show()
        dock.raise_()
        widget = dock.widget()
        if widget is not None:
            widget.setFocus()

    def refresh_debug_log(self) -> None:
        main_window_docks.refresh_debug_log(self)

    def debug_current_page_state(self) -> None:
        for line in self.build_current_page_state_debug_lines("Debug Current Page State"):
            self.log_debug(line)
        self.show_debug_log()

    def debug_selected_annotation_pdf_object(self) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return
        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None:
            self.log_debug("Debug Selected Annotation PDF Object skipped: selected model not found")
            self.show_debug_log()
            return

        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            self.log_debug(f"Debug Selected Annotation PDF Object skipped: xref={model.xref} not found on page")
            self.show_debug_log()
            return

        lines = self.build_selected_annotation_pdf_object_debug_lines(model, annot)
        for line in lines:
            self.log_debug(line)
        self.show_debug_log()

    def build_selected_annotation_pdf_object_debug_lines(
        self, model: AnnotationModel, annot: fitz.Annot
    ) -> list[str]:
        lines = [
            "Debug Selected Annotation PDF Object",
            f"  file: {self.pdf_path}",
            f"  page: {self.page_index + 1}/{len(self.doc)}",
            f"  selected_annotation_id: {self.selected_annotation_id}",
            f"  xref: {model.xref}",
            f"  pdf_type/app_type: {model.pdf_type} / {model.app_type}",
            f"  model rect: {self.rect_text(model.rect)}",
            f"  model text: {model.text!r}",
            f"  model color: {model.color}",
            f"  model border_width: {model.border_width}",
            f"  model font_size: {model.font_size}",
            f"  model opacity: {model.opacity}",
            f"  model vertices/quad_points: {model.quad_points}",
            f"  model line_start/line_end: {model.line_start} / {model.line_end}",
            f"  annot.info: {annot.info}",
            f"  annot.colors: {annot.colors}",
            f"  annot.border: {annot.border}",
        ]
        try:
            lines.append(f"  annot.opacity: {annot.opacity}")
        except Exception as exc:
            lines.append(f"  annot.opacity error: {exc}")
        try:
            lines.append(f"  annot.vertices: {getattr(annot, 'vertices', None)}")
        except Exception as exc:
            lines.append(f"  annot.vertices error: {exc}")

        for key in ("Subtype", "Rect", "DA", "AP", "DS", "RC", "IT", "Q", "Contents", "Subj", "T"):
            lines.append(f"  /{key}: {self.annotation_xref_key(model.xref, key)}")

        lines.append("  raw object:")
        lines.extend(self.annotation_raw_object_lines(model.xref, max_lines=80))
        return lines

    def annotation_xref_key(self, xref: int, key: str) -> str:
        if self.doc is None:
            return "(no document)"
        try:
            key_type, value = self.doc.xref_get_key(xref, key)
        except Exception as exc:
            return f"(error: {exc})"
        if key_type == "null":
            return "null"
        return f"{key_type}: {value}"

    def annotation_raw_object_lines(self, xref: int, max_lines: int = 80) -> list[str]:
        if self.doc is None:
            return ["    (no document)"]
        try:
            source = self.doc.xref_object(xref, compressed=False)
        except Exception as exc:
            return [f"    (error: {exc})"]
        raw_lines = source.splitlines()
        output = [f"    {line}" for line in raw_lines[:max_lines]]
        if len(raw_lines) > max_lines:
            output.append(f"    ... {len(raw_lines) - max_lines} more lines")
        return output

    def log_current_page_state_snapshot(self, title: str) -> None:
        for line in self.build_current_page_state_debug_lines(title):
            self.log_debug(line)

    def build_current_page_state_debug_lines(self, title: str) -> list[str]:
        if self.doc is None:
            return [f"{title} skipped: no PDF open"]

        pdf_annotation_count = 0
        pdf_annotation_error: str | None = None
        try:
            page = self.current_page()
            annot = page.first_annot
            while annot is not None:
                pdf_annotation_count += 1
                annot = annot.next
        except Exception as exc:
            pdf_annotation_error = str(exc)

        model_ids = {model.id for model in self.current_annotations}
        supported_model_ids = {model.id for model in self.current_annotations if model.is_supported and not model.deleted}
        item_model_ids = [item.data(0) for item in self.annotation_items if item.data(0)]
        item_model_id_set = set(item_model_ids)
        missing_item_ids = sorted(supported_model_ids - item_model_id_set)
        orphan_item_ids = sorted(item_id for item_id in item_model_id_set if item_id not in model_ids)
        hit_item_count = sum(1 for item in self.annotation_items if item.opacity() <= 0.02)
        visible_item_count = len(self.annotation_items) - hit_item_count

        xref_counts: dict[int, int] = {}
        for model in self.current_annotations:
            if model.xref > 0:
                xref_counts[model.xref] = xref_counts.get(model.xref, 0) + 1
        duplicate_xrefs = sorted(xref for xref, count in xref_counts.items() if count > 1)

        selected_exists = (
            self.selected_annotation_id is not None and self.selected_annotation_id in self.annotation_model_map
        )
        dirty_count = sum(1 for model in self.current_annotations if model.dirty)
        new_count = sum(1 for model in self.current_annotations if model.source == "new")
        deleted_count = sum(1 for model in self.current_annotations if model.deleted)
        unsupported_count = sum(1 for model in self.current_annotations if not model.is_supported)

        lines = [
            title,
            f"  file: {self.pdf_path}",
            f"  page: {self.page_index + 1}/{len(self.doc)}",
            f"  zoom: {self.zoom}",
            f"  dirty document: {self.is_dirty}",
            f"  pdf page annotations: {pdf_annotation_count}",
            f"  current_annotations: {len(self.current_annotations)}",
            f"  annotation_model_map: {len(self.annotation_model_map)}",
            f"  annotation_items: {len(self.annotation_items)}",
            f"  visible annotation items: {visible_item_count}",
            f"  hit annotation items: {hit_item_count}",
            f"  selection_items: {len(self.selection_items)}",
            f"  selected_annotation_id: {self.selected_annotation_id}",
            f"  selected exists in model map: {selected_exists}",
            f"  dirty models: {dirty_count}",
            f"  new models: {new_count}",
            f"  deleted models: {deleted_count}",
            f"  unsupported models: {unsupported_count}",
            f"  duplicate xrefs: {duplicate_xrefs if duplicate_xrefs else 'none'}",
            f"  supported models without scene item: {missing_item_ids if missing_item_ids else 'none'}",
            f"  scene items without model: {orphan_item_ids if orphan_item_ids else 'none'}",
        ]
        if pdf_annotation_error is not None:
            lines.append(f"  pdf annotation traversal error: {pdf_annotation_error}")

        return lines

    def show_audit_report(self, title: str, text: str) -> None:
        main_window_docks.show_text_report(self, title, text)

    def refresh_properties_panel(self) -> None:
        main_window_docks.refresh_properties_panel(self)

    def on_highlight_property_change(self, color: tuple[float, float, float], opacity: float) -> None:
        self.annotation_controller.on_highlight_property_change(color, opacity)

    def on_highlight_default_change(self, color: tuple[float, float, float], opacity: float) -> None:
        self.annotation_controller.on_highlight_default_change(color, opacity)

    def on_freetext_property_change(self, text: str, font_size: int, color: tuple[float, float, float]) -> None:
        self.annotation_controller.on_freetext_property_change(text, font_size, color)

    def on_stroked_property_change(self, color: tuple[float, float, float], width: int) -> None:
        self.annotation_controller.on_stroked_property_change(color, width)

    def property_status_message(self, prefix: str) -> str:
        return self.annotation_controller.property_status_message(prefix)

    def apply_property_change(self, callback, status: str) -> None:
        self.annotation_controller.apply_property_change(callback, status)

    def refresh_annotations_table(self) -> None:
        main_window_docks.refresh_annotations_table(self)

    def find_batch_freetext(self, keyword: str, scope: str) -> None:
        if self.doc is None:
            if self.batch_freetext_widget is not None:
                self.batch_freetext_widget.set_message("No PDF open.")
            return
        if not keyword.strip():
            if self.batch_freetext_widget is not None:
                self.batch_freetext_widget.set_message("Enter text to find FreeText annotations.")
            return

        matches, warnings = find_freetext_matches(
            self.doc,
            keyword,
            current_page_index=self.page_index,
            scope=scope,
        )
        if self.batch_freetext_widget is not None:
            self.batch_freetext_widget.set_results(matches, warnings)
        scope_text = "current document" if scope == "current_document" else "current page"
        self.log_debug(
            f"Batch FreeText find: scope={scope_text} keyword={keyword!r} "
            f"results={len(matches)} warnings={len(warnings)}"
        )

    def jump_to_batch_freetext_result(self, page_index: int, xref: int) -> None:
        if self.doc is None:
            return
        self.record_view_location_before_navigation()
        self.cancel_add_tool()
        self.page_index = max(0, min(page_index, len(self.doc) - 1))
        self.render_page()
        self.update_current_recent_page()
        selected = self.select_annotation_by_xref(xref)
        if selected:
            self.statusBar().showMessage(f"Jumped to FreeText result on page {self.page_index + 1}.")
            self.log_debug(f"Batch FreeText jumped: page={self.page_index + 1} xref={xref}")
        else:
            main_window_dialogs.show_warning(
                self,
                "Batch FreeText",
                f"FreeText annotation was not found on page {self.page_index + 1}.\n\nxref={xref}",
            )
            self.log_debug(f"Batch FreeText jump failed: page={self.page_index + 1} xref={xref}")

    def replace_selected_batch_freetext(self, replacement: str) -> None:
        result = self.selected_batch_freetext_result()
        if result is None:
            return
        keyword = self.batch_freetext_keyword()
        changed = self.apply_batch_freetext_text_change(
            [result],
            lambda text: replace_match_text(text, keyword, replacement),
            "Replace Selected FreeText",
        )
        self.finish_batch_freetext_text_change(changed, "Replaced selected FreeText match text.")

    def replace_all_batch_freetext(self, replacement: str) -> None:
        if self.batch_freetext_widget is None:
            return
        results = list(self.batch_freetext_widget.results)
        if not results:
            main_window_dialogs.show_warning(self, "Batch FreeText", "No FreeText search results to replace.")
            return
        if not main_window_dialogs.ask_yes_no(
            self,
            "Replace All Results",
            f"Replace matching text in {len(results)} FreeText annotation(s)?",
        ):
            return
        keyword = self.batch_freetext_keyword()
        changed = self.apply_batch_freetext_text_change(
            results,
            lambda text: replace_match_text(text, keyword, replacement),
            "Replace All FreeText",
        )
        self.finish_batch_freetext_text_change(changed, f"Replaced match text in {changed} FreeText annotation(s).")

    def delete_selected_batch_freetext_match(self) -> None:
        result = self.selected_batch_freetext_result()
        if result is None:
            return
        keyword = self.batch_freetext_keyword()
        changed = self.apply_batch_freetext_text_change(
            [result],
            lambda text: delete_match_text(text, keyword),
            "Delete Selected FreeText Match Text",
        )
        self.finish_batch_freetext_text_change(changed, "Deleted selected FreeText match text.")

    def add_selected_batch_freetext(self, addition: str, mode: str) -> None:
        result = self.selected_batch_freetext_result()
        if result is None:
            return
        if not addition:
            main_window_dialogs.show_warning(self, "Batch FreeText", "Enter text to add.")
            return
        keyword = self.batch_freetext_keyword()
        changed = self.apply_batch_freetext_text_change(
            [result],
            lambda text: add_text_to_match(text, keyword, addition, mode),
            "Add Text To Selected FreeText",
        )
        self.finish_batch_freetext_text_change(changed, "Added text to selected FreeText.")

    def delete_selected_batch_freetext_annotation(self) -> None:
        result = self.selected_batch_freetext_result()
        if result is None:
            return
        if not main_window_dialogs.ask_yes_no(
            self,
            "Delete Selected FreeText",
            f"Delete this FreeText annotation?\n\nPage {result.page_index + 1}, xref={result.xref}",
        ):
            return
        deleted = self.apply_batch_freetext_annotation_delete([result], "Delete Selected FreeText")
        self.finish_batch_freetext_annotation_delete(deleted, "Deleted selected FreeText annotation.")

    def delete_all_batch_freetext_annotations(self) -> None:
        if self.batch_freetext_widget is None:
            return
        results = list(self.batch_freetext_widget.results)
        if not results:
            main_window_dialogs.show_warning(self, "Batch FreeText", "No FreeText search results to delete.")
            return
        unique_targets = self.unique_batch_freetext_targets(results)
        if not main_window_dialogs.ask_yes_no(
            self,
            "Delete All Result FreeText",
            f"Delete {len(unique_targets)} FreeText annotation(s) from the current results?",
        ):
            return
        deleted = self.apply_batch_freetext_annotation_delete(unique_targets, "Delete All Result FreeText")
        self.finish_batch_freetext_annotation_delete(deleted, f"Deleted {deleted} FreeText annotation(s).")

    def selected_batch_freetext_result(self) -> FreeTextMatch | None:
        if self.batch_freetext_widget is None:
            return None
        result = self.batch_freetext_widget.selected_result()
        if result is None:
            main_window_dialogs.show_warning(self, "Batch FreeText", "Select a FreeText search result first.")
            return None
        return result

    def batch_freetext_keyword(self) -> str:
        if self.batch_freetext_widget is None:
            return ""
        return self.batch_freetext_widget.current_keyword().strip()

    def apply_batch_freetext_text_change(self, results: list[FreeTextMatch], transform, label: str) -> int:
        if self.doc is None:
            return 0
        keyword = self.batch_freetext_keyword()
        if not keyword:
            main_window_dialogs.show_warning(self, "Batch FreeText", "Enter text to find before editing.")
            return 0

        changed = 0
        for result in results:
            model = self.find_freetext_model_by_xref(result.page_index, result.xref)
            if model is None:
                self.log_debug(
                    f"Batch FreeText skipped missing annotation: page={result.page_index + 1} xref={result.xref}"
                )
                continue
            if keyword not in model.text:
                self.log_debug(
                    f"Batch FreeText skipped no-current-match: page={result.page_index + 1} xref={result.xref}"
                )
                continue

            new_text = transform(model.text)
            if new_text == model.text:
                continue
            font_size = self.clamp_freetext_font_size(int(round(model.font_size or self.default_freetext_font_size)))
            color_name = self.annotation_controller.color_name_for_tuple(model.color)
            color = ANNOTATION_COLORS[color_name]
            self.annotation_controller.update_freetext_annotation_clean_appearance_on_page(
                result.page_index,
                model,
                new_text,
                font_size,
                color,
            )
            changed += 1

        self.log_debug(f"Batch FreeText edit: label={label!r} results={len(results)} changed={changed}")
        return changed

    def unique_batch_freetext_targets(self, results: list[FreeTextMatch]) -> list[FreeTextMatch]:
        unique: dict[tuple[int, int], FreeTextMatch] = {}
        for result in results:
            unique[(result.page_index, result.xref)] = result
        return sorted(unique.values(), key=lambda item: (item.page_index, item.xref), reverse=True)

    def apply_batch_freetext_annotation_delete(self, results: list[FreeTextMatch], label: str) -> int:
        if self.doc is None:
            return 0

        deleted = 0
        writer = PdfAnnotationWriter(self.doc)
        for result in self.unique_batch_freetext_targets(results):
            model = self.find_freetext_model_by_xref(result.page_index, result.xref)
            if model is None:
                self.log_debug(
                    f"Batch FreeText delete skipped missing annotation: "
                    f"page={result.page_index + 1} xref={result.xref}"
                )
                continue
            page = self.doc[result.page_index]
            writer.delete_annotation(page, result.xref)
            deleted += 1

        self.log_debug(f"Batch FreeText delete: label={label!r} results={len(results)} deleted={deleted}")
        return deleted

    def find_freetext_model_by_xref(self, page_index: int, xref: int) -> AnnotationModel | None:
        if self.doc is None:
            return None
        if page_index < 0 or page_index >= len(self.doc):
            return None
        repository = AnnotationRepository(self.doc)
        for model in repository.load_page_annotations(page_index):
            if model.app_type == "freetext" and model.xref == xref:
                return model
        return None

    def finish_batch_freetext_text_change(self, changed: int, status_message: str) -> None:
        if changed <= 0:
            self.statusBar().showMessage("No FreeText annotation was changed.")
            return
        self.mark_dirty()
        self.clear_undo()
        self.refresh_annotation_overlay(preserve_selection=True)
        self.refresh_properties_panel()
        self.statusBar().showMessage(f"{status_message} Use Save to persist.")
        if self.batch_freetext_widget is not None:
            self.find_batch_freetext(
                self.batch_freetext_widget.current_keyword(),
                self.batch_freetext_widget.current_scope(),
            )

    def finish_batch_freetext_annotation_delete(self, deleted: int, status_message: str) -> None:
        if deleted <= 0:
            self.statusBar().showMessage("No FreeText annotation was deleted.")
            return
        self.mark_dirty()
        self.clear_undo()
        self.selected_annotation_id = None
        self.refresh_annotation_overlay(preserve_selection=False)
        self.refresh_annotations_table()
        self.refresh_properties_panel()
        self.statusBar().showMessage(f"{status_message} Use Save to persist.")
        if self.batch_freetext_widget is not None:
            self.find_batch_freetext(
                self.batch_freetext_widget.current_keyword(),
                self.batch_freetext_widget.current_scope(),
            )

    def rect_text(self, rect: fitz.Rect) -> str:
        return self.annotation_controller.rect_text(rect)

    def annotation_note(self, model: AnnotationModel) -> str:
        return self.annotation_controller.annotation_note(model)

    def select_annotation_by_xref(self, xref: int) -> bool:
        return self.annotation_controller.select_annotation_by_xref(xref)

    def begin_add_tool(self, tool: str) -> None:
        self.annotation_controller.begin_add_tool(tool)

    def set_active_tool(self, tool: str | None) -> None:
        self.annotation_controller.set_active_tool(tool)

    def cancel_add_tool(self) -> None:
        self.annotation_controller.cancel_add_tool()

    def begin_inline_freetext_editor(
        self,
        scene_pos: QPointF,
        text: str = "",
        width: float = 220.0,
        height: float = 72.0,
        font_size: int | None = None,
        edit_annotation_id: str | None = None,
    ) -> None:
        if self.inline_freetext_editor is None:
            return
        self.inline_freetext_editor.begin(
            scene_pos,
            text=text,
            width=width,
            height=height,
            font_size=font_size or self.default_freetext_font_size,
        )
        self.inline_freetext_edit_annotation_id = edit_annotation_id

    def default_inline_freetext_editor_height(self, font_size: int | None = None) -> float:
        size = font_size or self.default_freetext_font_size
        font_px = max(1, int(round(size * self.zoom)))
        return float(max(14, ceil(font_px + 2)))

    def begin_inline_freetext_editor_at_left_center(
        self,
        scene_pos: QPointF,
        text: str = "",
        width: float = 220.0,
        height: float | None = None,
        font_size: int | None = None,
    ) -> None:
        height = height or self.default_inline_freetext_editor_height(font_size)
        top_left = QPointF(scene_pos.x(), scene_pos.y() - height / 2)
        page_rect = self.page_item.boundingRect()
        top_left.setX(max(page_rect.left(), min(top_left.x(), page_rect.right() - width)))
        top_left.setY(max(page_rect.top(), min(top_left.y(), page_rect.bottom() - height)))
        self.begin_inline_freetext_editor(top_left, text, width, height, font_size)

    def cancel_inline_freetext_editor(self) -> None:
        if self.inline_freetext_editor is not None:
            self.inline_freetext_editor.cancel()

    def begin_inline_edit_for_selected_freetext(self, scene_pos: QPointF) -> bool:
        if self.selected_annotation_id is None:
            return False
        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None or hit[0] != self.selected_annotation_id:
            return False
        if hit[1] in {"resize-handle", "arrow-endpoint-handle"}:
            return False

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or model.app_type != "freetext":
            return False

        rect = self.scene_rect(model.rect)
        self.hide_inline_freetext_overlay(model.id)
        self.begin_inline_freetext_editor(
            rect.topLeft(),
            text=model.text,
            width=max(40.0, rect.width()),
            height=max(self.default_inline_freetext_editor_height(int(model.font_size or self.default_freetext_font_size)), rect.height()),
            font_size=int(model.font_size or self.default_freetext_font_size),
            edit_annotation_id=model.id,
        )
        self.statusBar().showMessage("Editing FreeText inline. Ctrl+Enter to apply, Esc to cancel.")
        return True

    def on_inline_freetext_accept(self, text: str, scene_pos: QPointF, scene_size) -> None:
        if self.doc is None:
            return
        edit_annotation_id = self.inline_freetext_edit_annotation_id
        self.inline_freetext_edit_annotation_id = None
        if edit_annotation_id is not None:
            self.finish_inline_freetext_edit(edit_annotation_id, text)
            return

        text = text.strip()
        if not text:
            self.log_debug("Inline FreeText accepted empty text; no annotation created")
            return
        try:
            scene_rect = QRectF(scene_pos, scene_size)
            rect = self.pdf_rect_from_scene_rect(scene_rect)
            xref = self.create_freetext_annotation(rect, text)
            self.record_add_undo("Add FreeText", self.page_index, xref)
            self.set_active_tool("freetext")
            self.statusBar().showMessage(f"Added FreeText xref={xref}. Use Save to persist.")
            self.log_debug(
                f"Inline FreeText created: xref={xref} chars={len(text)} "
                f"scene=({scene_pos.x():.1f},{scene_pos.y():.1f}) "
                f"size=({scene_size.width():.1f},{scene_size.height():.1f})"
            )
        except Exception as exc:
            self.show_error("Add FreeText failed", exc)

    def on_inline_freetext_cancel(self) -> None:
        self.inline_freetext_edit_annotation_id = None
        self.show_hidden_inline_freetext_overlay()
        self.log_debug("Inline FreeText canceled")

    def finish_inline_freetext_edit(self, annotation_id: str, text: str) -> None:
        model = self.annotation_model_map.get(annotation_id)
        if model is None or model.app_type != "freetext":
            self.log_debug(f"Inline FreeText edit skipped: annotation not found id={annotation_id}")
            return
        if self.selected_annotation_id != annotation_id:
            self.select_annotation(annotation_id)
        if text == model.text:
            self.show_hidden_inline_freetext_overlay()
            self.statusBar().showMessage(f"FreeText unchanged xref={model.xref}.")
            return
        try:
            font_size = int(model.font_size or self.default_freetext_font_size)
            color = model.color or (1, 0, 0)
            self.annotation_controller.on_freetext_property_change(text, font_size, color)
            self.statusBar().showMessage(f"Edited FreeText xref={model.xref}. Use Save to persist.")
            self.log_debug(f"Inline FreeText edited: xref={model.xref} chars={len(text)}")
        except Exception as exc:
            self.show_hidden_inline_freetext_overlay()
            self.show_error("Edit FreeText failed", exc)

    def hide_inline_freetext_overlay(self, annotation_id: str) -> None:
        self.show_hidden_inline_freetext_overlay()
        for item in self.annotation_item_map.get(annotation_id, []):
            item.setVisible(False)
        for item in self.selection_items:
            item.setVisible(False)
        self.hidden_inline_freetext_annotation_id = annotation_id

    def show_hidden_inline_freetext_overlay(self) -> None:
        annotation_id = self.hidden_inline_freetext_annotation_id
        if annotation_id is None:
            return
        for item in self.annotation_item_map.get(annotation_id, []):
            item.setVisible(True)
        for item in self.selection_items:
            item.setVisible(True)
        self.hidden_inline_freetext_annotation_id = None

    def on_tool_mouse_press(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_tool_mouse_press(scene_pos)

    def on_tool_mouse_move(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_tool_mouse_move(scene_pos)

    def on_tool_mouse_release(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_tool_mouse_release(scene_pos)

    def update_tool_preview(self, scene_pos: QPointF) -> None:
        self.annotation_controller.update_tool_preview(scene_pos)

    def update_highlight_tool_preview(self, scene_pos: QPointF) -> None:
        self.annotation_controller.update_highlight_tool_preview(scene_pos)

    def remove_tool_preview(self) -> None:
        self.annotation_controller.remove_tool_preview()

    def clamp_scene_pos_to_page(self, scene_pos: QPointF) -> QPointF:
        return self.canvas_controller.clamp_scene_pos_to_page(scene_pos)

    def pdf_point_from_scene_point(self, scene_pos: QPointF) -> tuple[float, float]:
        return self.canvas_controller.pdf_point_from_scene_point(scene_pos)

    def pdf_rect_from_scene_points(self, start: QPointF, end: QPointF) -> fitz.Rect:
        return self.canvas_controller.pdf_rect_from_scene_points(start, end)

    def pdf_rect_from_scene_rect(self, rect: QRectF) -> fitz.Rect:
        top_left = rect.topLeft()
        bottom_right = rect.bottomRight()
        return self.pdf_rect_from_scene_points(top_left, bottom_right)

    def create_freetext_annotation_at_point(self, point: tuple[float, float]) -> int | None:
        return self.annotation_controller.create_freetext_annotation_at_point(point)

    def default_freetext_rect(self, point: tuple[float, float], text: str, font_size: int) -> fitz.Rect:
        page_rect = self.current_page().rect
        width, height = self.estimated_freetext_size(text, font_size)
        width = min(width, max(12.0, page_rect.width))
        height = min(height, max(12.0, page_rect.height))
        x0 = min(max(page_rect.x0, point[0]), page_rect.x1 - width)
        y0 = min(max(page_rect.y0, point[1] - height / 2), page_rect.y1 - height)
        return fitz.Rect(x0, y0, x0 + width, y0 + height)

    def estimated_freetext_size(self, text: str, font_size: int) -> tuple[float, float]:
        width = min(260.0, max(30.0, self.estimated_freetext_width(text, font_size) + 8.0))
        line_count = max(1, len(text.splitlines()))
        height = ceil(max(10.0, line_count * font_size * 1.15 + 2.0))
        return width, float(height)

    def estimated_freetext_width(self, text: str, font_size: int) -> float:
        longest_line = max(text.splitlines() or [text], key=len)
        width = 0.0
        for char in longest_line:
            width += font_size * (1.05 if ord(char) > 127 else 0.58)
        return width

    def create_freetext_annotation(self, rect: fitz.Rect, text: str) -> int:
        return self.annotation_controller.create_freetext_annotation(rect, text)

    def create_square_annotation(self, rect: fitz.Rect) -> int:
        return self.annotation_controller.create_square_annotation(rect)

    def create_arrow_annotation(self, start: tuple[float, float], end: tuple[float, float]) -> int:
        return self.annotation_controller.create_arrow_annotation(start, end)

    def create_highlight_annotation_from_text_flow(
        self, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> int | None:
        return self.annotation_controller.create_highlight_annotation_from_text_flow(start_point, end_point)

    def highlight_rects_from_text_flow(
        self, page: fitz.Page, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> list[fitz.Rect]:
        return self.canvas_controller.highlight_rects_from_text_flow(page, start_point, end_point)

    def current_page_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        return self.canvas_controller.current_page_text_lines(page)

    def highlight_rect_from_selected_chars(self, selected_chars: list[dict], selected_boxes: list[fitz.Rect]) -> fitz.Rect:
        return self.canvas_controller.highlight_rect_from_selected_chars(selected_chars, selected_boxes)

    def highlight_metric_rect(self, selected_chars: list[dict], line_rect: fitz.Rect) -> fitz.Rect | None:
        return self.canvas_controller.highlight_metric_rect(selected_chars, line_rect)

    def expand_highlight_rect(self, rect: fitz.Rect) -> fitz.Rect:
        return self.canvas_controller.expand_highlight_rect(rect)

    def extract_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        return self.canvas_controller.extract_text_lines(page)

    def text_position_from_point(
        self, lines: list[list[dict]], point: tuple[float, float]
    ) -> tuple[int, int] | None:
        return self.canvas_controller.text_position_from_point(lines, point)

    def page_center_rect(self, width: float, height: float) -> fitz.Rect:
        return self.canvas_controller.page_center_rect(width, height)

    def set_foxit_freetext(self, checked: bool) -> None:
        self.use_foxit_freetext = checked

    def open_settings(self) -> None:
        main_window_dialogs.open_settings(self)

    def pdf_date_now(self) -> str:
        if self.doc is None:
            return ""
        return PdfAnnotationWriter(self.doc).pdf_date_now()

    def apply_foxit_freetext_keys(self, annot: fitz.Annot) -> None:
        if self.doc is None:
            return

        PdfAnnotationWriter(self.doc).apply_foxit_freetext_keys(annot)

    def remove_annotation_keys(self, xref: int, keys: tuple[str, ...]) -> None:
        if self.doc is None:
            return

        PdfAnnotationWriter(self.doc).remove_annotation_keys(xref, keys)

    def add_typewriter(self) -> None:
        self.annotation_controller.add_typewriter()

    def add_rectangle(self) -> None:
        self.annotation_controller.add_rectangle()

    def add_highlight(self) -> None:
        self.annotation_controller.add_highlight()

    def add_arrow(self) -> None:
        self.annotation_controller.add_arrow()

    def save(self, confirm: bool = True) -> bool:
        return self.document_controller.save(confirm)

    def save_incremental(self, confirm: bool = True) -> bool:
        return self.document_controller.save_incremental(confirm)

    def save_full_rewrite_to_current_path(self) -> Path:
        return self.document_controller.save_full_rewrite_to_current_path()

    def confirm_current_page_audit_before_save(self) -> bool:
        return self.document_controller.confirm_current_page_audit_before_save()

    def audit_saved_temp_pdf(self, temp_path: Path, page_index: int) -> None:
        self.document_controller.audit_saved_temp_pdf(temp_path, page_index)

    def backup_path_for(self, path: Path) -> Path:
        return self.document_controller.backup_path_for(path)

    def release_current_pdf_for_replace(self) -> None:
        self.document_controller.release_current_pdf_for_replace()

    def save_as(self) -> bool:
        return self.document_controller.save_as()

    def prev_page(self) -> None:
        if self.doc is None or self.page_index <= 0:
            return
        self.record_view_location_before_navigation()
        self.cancel_add_tool()
        self.page_index -= 1
        self.render_page()
        self.update_current_recent_page()
        self.save_active_session_state()

    def next_page(self) -> None:
        if self.doc is None or self.page_index >= len(self.doc) - 1:
            return
        self.record_view_location_before_navigation()
        self.cancel_add_tool()
        self.page_index += 1
        self.render_page()
        self.update_current_recent_page()
        self.save_active_session_state()

    def zoom_in(self) -> None:
        self.zoom = min(self.zoom + 0.25, 4.0)
        self.render_page(preserve_selection=True)
        self.save_active_session_state()

    def zoom_out(self) -> None:
        self.zoom = max(self.zoom - 0.25, 0.5)
        self.render_page(preserve_selection=True)
        self.save_active_session_state()

    def show_error(self, title: str, exc: Exception) -> None:
        main_window_dialogs.show_error(self, title, exc)

    def keyPressEvent(self, event) -> None:
        if (
            event.key() == Qt.Key.Key_Escape
            and self.inline_freetext_editor is not None
            and self.inline_freetext_editor.is_active()
        ):
            self.inline_freetext_editor.cancel()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.active_tool is not None:
            self.cancel_add_tool()
            self.show_page_status()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if not self.confirm_all_unsaved_for_exit():
            self.log_debug("Exit canceled by unsaved changes prompt")
            event.ignore()
            return
        self.cancel_add_tool()
        self.update_current_recent_page()
        self.state.navigation.anchor_cache.clear()
        for session in self.sessions:
            session.doc.close()
            self.log_debug(f"Exit closed PDF: {session.path}")
        self.sessions.clear()
        self.active_session_index = None
        super().closeEvent(event)


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        app_id = "JOM.PDFNoteReader.Desktop"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def main() -> int:
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    icon_path = Path(__file__).with_name("assets") / "app_icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
