import sys
from datetime import datetime
from math import ceil
from pathlib import Path

import pymupdf as fitz
from shiboken6 import isValid
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
from app.services.keyword_repository import load_keyword_groups
from app.main_window import actions as main_window_actions
from app.main_window import dialogs as main_window_dialogs
from app.main_window import docks as main_window_docks
from app.main_window import view as main_window_view
from app.main_window.reading_workspace import ReadingWorkspace
from app.widgets.reading_pane import is_reader_field
from app.models.annotation_model import (
    ANNOTATION_COLORS,
    EDITABLE_APP_TYPES,
    AnnotationModel,
)
from app.anchors import text_affects_anchor_data
from app.models.continuous_view import PageLayout, PageViewItem
from app.canvas.inline_freetext_editor import InlineFreeTextEditorManager
from app.services.freetext_batch import FreeTextMatch
from app.services.freetext_batch import add_text_to_match
from app.services.freetext_batch import delete_match_text
from app.services.freetext_batch import replace_match_text
from app.services.freetext_batch import replace_match_text_with_mode
from app.services.freetext_batch import preview_replace_text_with_mode
from app.services.freetext_snapshot import FreeTextSnapshot
from app.services.pdf_audit import audit_current_page as run_audit_current_page
from app.services.pdf_audit import audit_document_summary as run_audit_document_summary
from app.services.pdf_audit import format_audit_report
from app.services.pdf_annotation_writer import PdfAnnotationWriter
from app.services.page_labels import page_index_for_label
from app.services.page_labels import page_labels_json
from app.services.page_labels import parse_page_labels_json
from app.services.settings import AppSettings, load_settings, save_settings, settings_path
from app.services.shortcuts import apply_shortcuts as apply_window_shortcuts
from app.services.text_match import InvalidRegexError
from app.services.ui_contrast import (
    annotation_table_contrast_stylesheet,
    application_contrast_stylesheet,
    clamp_contrast,
    document_tabbar_contrast_stylesheet,
    menu_contrast_stylesheet,
    tabbar_contrast_stylesheet,
    toolbar_contrast_stylesheet,
)
from app.widgets.page_labels_dialog import PageLabelsDialog
from app.widgets.freetext_batch import BatchReplacePreviewDialog
from app.models.undo import UndoAction
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QGraphicsItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
)


class MainWindow(QMainWindow):
    def __getattribute__(self, name):
        if is_reader_field(name):
            workspace = super().__getattribute__("__dict__").get("reading_workspace")
            if workspace is not None and workspace.ready:
                return getattr(workspace.active, name)
        return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if is_reader_field(name):
            workspace = self.__dict__.get("reading_workspace")
            if workspace is not None and workspace.ready:
                setattr(workspace.active, name, value)
                return
        super().__setattr__(name, value)

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
        self.use_graphics_freetext_editor = True
        self.freetext_font_size_min = 4
        self.freetext_font_size_max = 20
        self.default_freetext_font_size = 7
        self.default_highlight_color = (1, 1, 0)
        self.default_highlight_opacity = 0.45
        self.extract_highlight_text_on_reindex = False
        self.quick_audit_detailed = False
        self.qpdf_bin_dir = r"D:\tools\qpdf-12.3.2-msvc64\bin"
        self.save_incremental_safety_default = True
        self.anchor_page_basis = "physical"
        self.search_page_size = 500
        self.page_jump_step = 5
        self.ui_contrast = 35
        self.keyword_groups = load_keyword_groups(self.keyword_metadata_dir())
        self.recent_files: list[dict] = []
        self.recent_search_rule_files: list[str] = []
        self.shortcuts: dict[str, str] = {}
        self.shortcut_objects: list[object] = []
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
        self.active_scene_drag_handle: str | None = None
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
        self.navigation_anchor_manual_refresh_required = False
        self.navigation_anchor_manual_refresh_doc_id: int | None = None
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
        self.text_selection_active = False
        self.text_selection_start_scene_pos: QPointF | None = None
        self.text_selection_page_index: int | None = None
        self.text_selection_items: list[QGraphicsItem] = []
        self.text_selection_rects: list[fitz.Rect] = []
        self.inline_freetext_editor: InlineFreeTextEditorManager | None = None
        self.inline_freetext_edit_annotation_id: str | None = None
        self.inline_freetext_edit_page_index: int | None = None
        self.inline_freetext_edit_xref: int | None = None
        self.inline_freetext_clear_selection_on_accept = False
        self.hidden_inline_freetext_annotation_id: str | None = None
        self.text_lines_cache_page_index: int | None = None
        self.text_lines_cache: list[list[dict]] | None = None
        self.view_mode = "continuous"
        self.continuous_page_gap = 20.0
        self.continuous_cache_radius = 2
        self.continuous_page_layouts: list[PageLayout] = []
        self.continuous_page_items: dict[int, PageViewItem] = {}
        self.continuous_annotation_cache: dict[int, list[AnnotationModel]] = {}
        self.continuous_pixmap_cache: dict[int, QPixmap] = {}
        self.continuous_pixmap_cache_limit = 12
        self.continuous_layout_doc_id: int | None = None
        self.continuous_layout_zoom: float | None = None
        self.continuous_suppress_scroll_sync = False
        self.continuous_scroll_timer = QTimer(self)
        self.continuous_scroll_timer.setSingleShot(True)
        self.continuous_scroll_timer.setInterval(40)
        self.continuous_scroll_timer.timeout.connect(self.on_continuous_scroll_timer)
        self.ui_contrast_save_timer = QTimer(self)
        self.ui_contrast_save_timer.setSingleShot(True)
        self.ui_contrast_save_timer.setInterval(600)
        self.ui_contrast_save_timer.timeout.connect(self.save_app_settings)

        self.continuous_scroll_timer.stop()
        self.continuous_scroll_timer.deleteLater()
        self.reading_workspace = ReadingWorkspace(self)
        self.setCentralWidget(self.reading_workspace)
        self.show_navigation()
        main_window_view.create_scroll_boundary_label(self)

        self.update_window_title()
        self.resize(1200, 850)
        self.create_actions()
        self.create_menus()
        self.create_toolbar()
        self.apply_shortcuts()
        self.apply_ui_contrast()
        self.show_current_page_annotations()
        self.update_actions()

    def keyword_metadata_dir(self) -> Path:
        return Path(__file__).with_name("metaData")

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
    def active_scene_drag_handle(self) -> str | None:
        return self.state.interaction.active_scene_drag_handle

    @active_scene_drag_handle.setter
    def active_scene_drag_handle(self, value: str | None) -> None:
        self.state.interaction.active_scene_drag_handle = value

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
        self.canvas_controller.invalidate_continuous_annotation_cache(self.page_index)
        if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
            self.sessions[self.active_session_index].is_dirty = True
            self.update_document_tab_title(self.active_session_index)
        self.update_window_title()
        if hasattr(self, "reading_workspace"):
            source = self if hasattr(self, "host_window") else self.reading_workspace.active
            self.reading_workspace.document_changed(source)

    def mark_navigation_anchors_dirty(self) -> None:
        self.navigation_anchor_dirty = True
        self.navigation_anchor_manual_refresh_required = True
        self.navigation_anchor_manual_refresh_doc_id = id(self.doc) if self.doc is not None else None
        if self.navigation_widget is not None:
            self.navigation_widget.set_anchor_dirty(True)

    def clear_dirty(self) -> None:
        self.is_dirty = False
        if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
            self.sessions[self.active_session_index].is_dirty = False
            self.update_document_tab_title(self.active_session_index)
        self.update_window_title()
        if hasattr(self, "reading_workspace"):
            source = self if hasattr(self, "host_window") else self.reading_workspace.active
            self.reading_workspace.document_changed(source)

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
        return self.reading_workspace.confirm_exit()

    def go_to_page(self, page_number: int) -> None:
        if self.updating_page_spin or self.doc is None:
            return

        target_index = max(0, min(page_number - 1, len(self.doc) - 1))
        if target_index == self.page_index:
            return

        self.record_view_location_before_navigation()
        self.page_index = target_index
        self.annotation_controller.clear_tool_interaction()
        if self.view_mode == "continuous":
            self.scroll_continuous_to_page(target_index)
            self.update_current_recent_page()
            self.save_active_session_state()
            return
        self.render_page()
        if self.view_mode != "continuous":
            self.defer_scroll_to_page_top_left()
        self.update_current_recent_page()
        self.save_active_session_state()

    def go_to_book_label(self) -> None:
        if self.doc is None:
            return
        label = self.book_label_input.text().strip()
        if not label:
            self.sync_page_spin()
            return
        target_index = page_index_for_label(self.doc, label)
        if target_index is None:
            main_window_dialogs.show_warning(self, "Book Page", f"Book page label not found:\n{label}")
            self.sync_page_spin()
            return
        self.go_to_page(target_index + 1)

    def show_page_labels_dialog(self) -> None:
        if self.doc is None:
            main_window_dialogs.show_warning(self, "Page Labels", "No PDF is open.")
            return
        dialog = PageLabelsDialog(page_labels_json(self.doc), self.apply_page_labels_json, self)
        dialog.exec()

    def apply_page_labels_json(self, source: str) -> bool:
        if self.doc is None:
            main_window_dialogs.show_warning(self, "Page Labels", "No PDF is open.")
            return False
        original_labels = self.doc.get_page_labels()
        try:
            rules = parse_page_labels_json(source, len(self.doc))
            self.doc.set_page_labels(rules)
        except Exception as exc:
            main_window_dialogs.show_error(self, "Page Labels", exc)
            return False

        if self.save_incremental(confirm=True):
            self.sync_page_spin()
            return True

        try:
            if self.doc is not None:
                self.doc.set_page_labels(original_labels)
        except Exception as exc:
            self.log_debug(f"Restore Page Labels after canceled save failed: {exc}")
        self.sync_page_spin()
        return False

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
        self.use_graphics_freetext_editor = settings.use_graphics_freetext_editor
        self.freetext_font_size_min = settings.freetext_font_size_min
        self.freetext_font_size_max = settings.freetext_font_size_max
        self.default_freetext_font_size = settings.default_freetext_font_size
        self.default_highlight_color = settings.default_highlight_color
        self.default_highlight_opacity = settings.default_highlight_opacity
        self.extract_highlight_text_on_reindex = settings.extract_highlight_text_on_reindex
        self.quick_audit_detailed = settings.quick_audit_detailed
        self.qpdf_bin_dir = settings.qpdf_bin_dir
        self.save_incremental_safety_default = settings.save_incremental_safety_default
        self.anchor_page_basis = settings.anchor_page_basis
        self.search_page_size = settings.search_page_size
        self.page_jump_step = settings.page_jump_step
        self.ui_contrast = settings.ui_contrast
        self.recent_files = settings.recent_files
        self.recent_search_rule_files = settings.recent_search_rule_files
        self.shortcuts = settings.shortcuts

    def save_app_settings(self) -> None:
        settings = AppSettings(
            use_foxit_freetext=self.use_foxit_freetext,
            use_popup_freetext_input=self.use_popup_freetext_input,
            use_graphics_freetext_editor=self.use_graphics_freetext_editor,
            freetext_font_size_min=self.freetext_font_size_min,
            freetext_font_size_max=self.freetext_font_size_max,
            default_freetext_font_size=self.default_freetext_font_size,
            default_highlight_color=self.default_highlight_color,
            default_highlight_opacity=self.default_highlight_opacity,
            extract_highlight_text_on_reindex=self.extract_highlight_text_on_reindex,
            quick_audit_detailed=self.quick_audit_detailed,
            qpdf_bin_dir=self.qpdf_bin_dir,
            save_incremental_safety_default=self.save_incremental_safety_default,
            anchor_page_basis=self.anchor_page_basis,
            search_page_size=self.search_page_size,
            page_jump_step=self.page_jump_step,
            ui_contrast=self.ui_contrast,
            recent_files=self.recent_files,
            recent_search_rule_files=self.recent_search_rule_files,
            shortcuts=self.shortcuts,
        )
        save_settings(self.settings_path(), settings)

    def apply_shortcuts(self) -> None:
        apply_window_shortcuts(self)

    def apply_ui_contrast(self) -> None:
        stylesheet = application_contrast_stylesheet(self.ui_contrast)
        menu_stylesheet = menu_contrast_stylesheet(self.ui_contrast)
        tab_stylesheet = tabbar_contrast_stylesheet(self.ui_contrast)
        toolbar_stylesheet = toolbar_contrast_stylesheet(self.ui_contrast)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet)
        self.setStyleSheet(stylesheet)
        if hasattr(self, "menuBar"):
            self.menuBar().setStyleSheet(menu_stylesheet)
            for menu in self.menuBar().findChildren(QMenu):
                menu.setStyleSheet(menu_stylesheet)
        for menu in getattr(self, "contrast_menus", []):
            menu.setStyleSheet(menu_stylesheet)
        if hasattr(self, "document_tabs"):
            if hasattr(self, "reading_workspace"):
                for pane in self.reading_workspace.panes:
                    pane.document_tabs.setStyleSheet(document_tabbar_contrast_stylesheet(self.ui_contrast))
                self.reading_workspace.paint_active()
            else:
                self.document_tabs.setStyleSheet(document_tabbar_contrast_stylesheet(self.ui_contrast))
        if getattr(self, "main_toolbar", None) is not None:
            self.main_toolbar.setStyleSheet(toolbar_stylesheet)
        if getattr(self, "annotations_tabs", None) is not None:
            self.annotations_tabs.setStyleSheet(tab_stylesheet)
            self.annotations_tabs.tabBar().setStyleSheet(tab_stylesheet)
        if getattr(self, "navigation_widget", None) is not None:
            if hasattr(self.navigation_widget, "apply_ui_contrast"):
                self.navigation_widget.apply_ui_contrast(self.ui_contrast)
            else:
                self.navigation_widget.setStyleSheet(tab_stylesheet)
        if getattr(self, "annotations_table", None) is not None:
            if hasattr(self.annotations_table, "apply_ui_contrast"):
                self.annotations_table.apply_ui_contrast(self.ui_contrast)
            else:
                self.annotations_table.setStyleSheet(annotation_table_contrast_stylesheet(self.ui_contrast))
        if getattr(self, "annotation_search_widget", None) is not None and hasattr(
            self.annotation_search_widget,
            "apply_ui_contrast",
        ):
            self.annotation_search_widget.apply_ui_contrast(self.ui_contrast)
        if getattr(self, "batch_freetext_widget", None) is not None and hasattr(
            self.batch_freetext_widget,
            "apply_ui_contrast",
        ):
            self.batch_freetext_widget.apply_ui_contrast(self.ui_contrast)

    def set_ui_contrast(self, value: int) -> None:
        contrast = clamp_contrast(value)
        if contrast == self.ui_contrast:
            return
        self.ui_contrast = contrast
        self.apply_ui_contrast()
        if hasattr(self, "ui_contrast_save_timer"):
            self.ui_contrast_save_timer.start()

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

    def on_view_scrolled(self) -> None:
        self.canvas_controller.on_view_scrolled()

    def schedule_view_scrolled(self) -> None:
        if self.view_mode != "continuous":
            return
        if self.continuous_suppress_scroll_sync:
            return
        if not self.continuous_scroll_timer_is_valid():
            return
        self.continuous_scroll_timer.start()

    def on_continuous_scroll_timer(self) -> None:
        if self.view_mode != "continuous":
            return
        if self.doc is None:
            return
        self.on_view_scrolled()

    def continuous_scroll_timer_is_valid(self) -> bool:
        try:
            return self.continuous_scroll_timer is not None and isValid(self.continuous_scroll_timer)
        except RuntimeError:
            return False

    def stop_continuous_scroll_timer(self) -> None:
        if not self.continuous_scroll_timer_is_valid():
            return
        try:
            self.continuous_scroll_timer.stop()
        except RuntimeError:
            return

    def set_continuous_view_enabled(self, enabled: bool) -> None:
        if self.doc is None:
            return
        target_mode = "continuous" if enabled else "single"
        if self.view_mode == target_mode:
            return
        anchor = self.canvas_controller.capture_view_mode_anchor()
        preserve_selection = False
        if not enabled and self.selected_annotation_id is not None:
            model = self.annotation_model_map.get(self.selected_annotation_id)
            preserve_selection = model is not None and model.page_index == self.page_index
        fit_single_page = (
            not enabled
            and anchor is not None
            and anchor.get("mode") == "continuous"
            and int(anchor.get("visible_page_count", 1)) > 1
        )
        if anchor is not None:
            self.page_index = max(0, min(int(anchor.get("page_index", self.page_index)), len(self.doc) - 1))
        if fit_single_page:
            self.zoom = self.canvas_controller.fit_zoom_for_page(self.page_index)
        active_tool = self.active_tool
        self.remove_tool_preview()
        self.tool_start_scene_pos = None
        self.clear_scene_drag_state()
        self.stop_continuous_scroll_timer()
        self.view_mode = target_mode
        if hasattr(self, "continuous_view_action"):
            self.continuous_view_action.setChecked(enabled)
        self.render_page(preserve_selection=preserve_selection)
        if anchor is not None and not fit_single_page:
            self.canvas_controller.restore_view_mode_anchor(anchor)
        self.set_active_tool(active_tool)
        mode_label = "Continuous" if enabled else "Single Page"
        self.statusBar().showMessage(f"View mode: {mode_label}")
        if hasattr(self, "reading_workspace"):
            self.reading_workspace.sync.set_enabled(False)
            self.save_active_session_state()

    def clear_continuous_page_items(self) -> None:
        self.canvas_controller.clear_continuous_page_items()

    def clear_continuous_annotation_cache(self) -> None:
        self.canvas_controller.clear_continuous_annotation_cache()

    def clear_continuous_pixmap_cache(self) -> None:
        self.canvas_controller.clear_continuous_pixmap_cache()

    def scroll_continuous_to_page(self, page_index: int) -> None:
        self.canvas_controller.scroll_continuous_to_page(page_index)

    def scroll_continuous_to_xref(self, page_index: int, xref: int) -> bool:
        return self.canvas_controller.scroll_continuous_to_xref(page_index, xref)

    def select_continuous_annotation_at_scene_pos(self, scene_pos: QPointF) -> bool:
        return self.canvas_controller.select_continuous_annotation_at_scene_pos(scene_pos)

    def select_continuous_annotation_by_xref(self, page_index: int, xref: int) -> bool:
        return self.canvas_controller.select_continuous_annotation_by_xref(page_index, xref)

    def defer_scroll_to_page_top_left(self) -> None:
        QTimer.singleShot(0, self.scroll_to_page_top_left)

    def defer_scroll_to_page_bottom_left(self) -> None:
        QTimer.singleShot(0, self.scroll_to_page_bottom_left)

    def scroll_to_page_top_left(self) -> None:
        self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().minimum())
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().minimum())

    def scroll_to_page_bottom_left(self) -> None:
        self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().minimum())
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

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

    def on_scene_mouse_press(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_scene_mouse_press(scene_pos)

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

    def on_continuous_scene_mouse_press(self, scene_pos: QPointF, button=None) -> bool:
        return self.annotation_controller.on_continuous_scene_mouse_press(scene_pos, button)

    def on_continuous_scene_mouse_move(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_continuous_scene_mouse_move(scene_pos)

    def on_continuous_scene_mouse_release(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_continuous_scene_mouse_release(scene_pos)

    def on_continuous_scene_mouse_hover(self, scene_pos: QPointF) -> None:
        self.annotation_controller.on_continuous_scene_mouse_hover(scene_pos)

    def on_continuous_scene_mouse_double_click(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_continuous_scene_mouse_double_click(scene_pos)

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

    def on_scene_mouse_double_click(self, scene_pos: QPointF | None = None) -> bool:
        return self.annotation_controller.on_scene_mouse_double_click(scene_pos)

    def on_annotation_tool_blank_right_double_click(self, scene_pos: QPointF | None = None) -> bool:
        return self.annotation_controller.on_annotation_tool_blank_right_double_click(scene_pos)

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
            self.canvas_controller.safe_remove_scene_item(item)
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

    def apply_quick_highlight_color(self, color: tuple[float, float, float]) -> None:
        opacity = self.quick_highlight_opacity_spin.value() / 100 if hasattr(self, "quick_highlight_opacity_spin") else 1
        self.annotation_controller.apply_quick_highlight_color(color, opacity)

    def set_default_highlight_color(self, color: tuple[float, float, float]) -> None:
        self.annotation_controller.set_default_highlight_color(color)

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

    def refresh_navigation_anchors(self) -> None:
        self.navigation_controller.refresh_navigation_anchors()

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

    def audit_continuous_view_state(self) -> None:
        for line in self.build_continuous_view_state_debug_lines("Audit Continuous View State"):
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

    def build_continuous_view_state_debug_lines(self, title: str) -> list[str]:
        if self.doc is None:
            return [f"{title} skipped: no PDF open"]

        rendered_pages = sorted(self.continuous_page_items)
        visible_pages = (
            sorted(self.canvas_controller.visible_continuous_page_indexes())
            if self.view_mode == "continuous" and self.continuous_page_layouts
            else []
        )
        center_page = (
            self.canvas_controller.current_page_index_from_continuous_view()
            if self.view_mode == "continuous" and self.continuous_page_layouts
            else None
        )
        selected_model = (
            self.annotation_model_map.get(self.selected_annotation_id)
            if self.selected_annotation_id is not None
            else None
        )
        selected_rendered = (
            selected_model is not None and selected_model.page_index in self.continuous_page_items
        )
        selected_cached = (
            selected_model is not None
            and any(
                model.xref == selected_model.xref
                for model in self.continuous_annotation_cache.get(selected_model.page_index, [])
            )
        )

        table_rows = self.annotations_table.rowCount() if self.annotations_table is not None else "n/a"
        lines = [
            title,
            f"  file: {self.pdf_path}",
            f"  view_mode: {self.view_mode}",
            f"  current page: {self.page_index + 1}/{len(self.doc)}",
            f"  viewport center page: {center_page + 1 if center_page is not None else 'n/a'}",
            f"  zoom: {self.zoom}",
            f"  continuous layouts: {len(self.continuous_page_layouts)}",
            f"  continuous layout doc id matches: {self.continuous_layout_doc_id == id(self.doc)}",
            f"  continuous layout zoom matches: {self.continuous_layout_zoom == self.zoom}",
            f"  visible pages: {[page + 1 for page in visible_pages]}",
            f"  rendered pages: {[page + 1 for page in rendered_pages]}",
            f"  annotation cache pages: {[page + 1 for page in sorted(self.continuous_annotation_cache)]}",
            f"  pixmap cache pages: {[page + 1 for page in sorted(self.continuous_pixmap_cache)]}",
            f"  selected_annotation_id: {self.selected_annotation_id}",
            f"  selected exists in model map: {selected_model is not None}",
            f"  selected page: {selected_model.page_index + 1 if selected_model is not None else 'n/a'}",
            f"  selected rendered page loaded: {selected_rendered}",
            f"  selected exists in continuous annotation cache: {selected_cached}",
            f"  current_annotations: {len(self.current_annotations)}",
            f"  annotation_model_map: {len(self.annotation_model_map)}",
            f"  selection_items: {len(self.selection_items)}",
            f"  text_selection_active: {self.text_selection_active}",
            f"  text_selection_page_index: {self.text_selection_page_index + 1 if self.text_selection_page_index is not None else 'n/a'}",
            f"  text_selection_rects: {len(self.text_selection_rects)}",
            f"  text_selection_items: {len(self.text_selection_items)}",
            f"  active_tool: {self.active_tool}",
            f"  tool_start_scene_pos: {self.tool_start_scene_pos}",
            f"  active_scene_drag_kind: {self.active_scene_drag_kind}",
            f"  active_scene_drag_annotation_id: {self.active_scene_drag_annotation_id}",
            f"  table rows/current annotations: {table_rows} / {len(self.current_annotations)}",
        ]

        for page_index in rendered_pages:
            item = self.continuous_page_items.get(page_index)
            models = self.continuous_annotation_cache.get(page_index, [])
            if item is None:
                lines.append(f"  page {page_index + 1}: missing PageViewItem")
                continue
            lines.append(
                f"  page {page_index + 1}: "
                f"models={len(models)} annotation_items={len(item.annotation_items)} "
                f"annotation_model_ids={len(item.annotation_model_ids)} "
                f"pixmap={'yes' if item.pixmap_item is not None else 'no'}"
            )

        warnings = []
        if self.view_mode == "continuous":
            if len(self.continuous_page_layouts) != len(self.doc):
                warnings.append("continuous layout count does not match document page count")
            if self.text_selection_page_index is not None and not (0 <= self.text_selection_page_index < len(self.doc)):
                warnings.append("text_selection_page_index is out of range")
            if selected_model is not None and not selected_rendered:
                warnings.append("selected annotation page is not currently rendered")
            if self.page_index < 0 or self.page_index >= len(self.doc):
                warnings.append("current page index is out of range")
        else:
            if self.continuous_page_items:
                warnings.append("single view still has continuous page items")

        lines.append(f"  warnings: {warnings if warnings else 'none'}")
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

    def active_freetext_snapshot(self) -> FreeTextSnapshot | None:
        if self.active_session_index is None:
            return None
        if self.active_session_index < 0 or self.active_session_index >= len(self.sessions):
            return None
        snapshot = self.sessions[self.active_session_index].freetext_snapshot
        return snapshot if isinstance(snapshot, FreeTextSnapshot) else None

    def ensure_freetext_snapshot(self) -> FreeTextSnapshot | None:
        if self.doc is None or self.active_session_index is None:
            return None
        if self.active_session_index < 0 or self.active_session_index >= len(self.sessions):
            return None
        session = self.sessions[self.active_session_index]
        snapshot = session.freetext_snapshot
        if not isinstance(snapshot, FreeTextSnapshot):
            snapshot = FreeTextSnapshot.from_document(self.doc)
            session.freetext_snapshot = snapshot
            self.log_debug(
                "Batch FreeText snapshot built: "
                f"path={session.path} records={len(snapshot.records)} warnings={len(snapshot.warnings)}"
            )
        return snapshot

    def clear_active_freetext_snapshot(self) -> None:
        if self.active_session_index is None:
            return
        if 0 <= self.active_session_index < len(self.sessions):
            self.sessions[self.active_session_index].freetext_snapshot = None

    def update_freetext_snapshot_text(self, page_index: int, xref: int, text: str) -> None:
        snapshot = self.active_freetext_snapshot()
        if snapshot is not None:
            snapshot.update_text(page_index, xref, text)

    def remove_freetext_snapshot_record(self, page_index: int, xref: int) -> None:
        snapshot = self.active_freetext_snapshot()
        if snapshot is not None:
            snapshot.remove(page_index, xref)

    def show_invalid_regex_error(self, exc: InvalidRegexError) -> None:
        main_window_dialogs.show_warning(
            self,
            "Invalid Regex",
            f"Invalid regex:\n{exc.pattern}\n\n{exc.error}",
        )

    def find_batch_freetext(
        self,
        include_terms: list,
        include_mode: str = "and",
        exclude_terms: list[str] | None = None,
        exclude_mode: str = "or",
        scope: str = "current_page",
    ) -> None:
        if self.doc is None:
            if self.batch_freetext_widget is not None:
                self.batch_freetext_widget.set_message("No PDF open.")
            return
        include_terms = [term for term in include_terms if (term.get("text", "").strip() if isinstance(term, dict) else str(term).strip())]
        exclude_terms = [term.strip() for term in (exclude_terms or []) if term.strip()]
        if not include_terms and not exclude_terms:
            if self.batch_freetext_widget is not None:
                self.batch_freetext_widget.set_message("Enter Including or Excluding text to find FreeText annotations.")
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            snapshot = self.ensure_freetext_snapshot()
            if snapshot is None:
                matches, warnings = [], []
            else:
                matches, warnings = snapshot.find(
                    self.doc,
                    include_terms,
                    include_mode,
                    exclude_terms,
                    exclude_mode,
                    current_page_index=self.page_index,
                    scope=scope,
                )
            if self.batch_freetext_widget is not None:
                self.batch_freetext_widget.set_results(matches, warnings)
        except InvalidRegexError as exc:
            self.show_invalid_regex_error(exc)
            self.log_debug(f"Batch FreeText invalid regex: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        scope_text = "current document" if scope == "current_document" else "current page"
        self.log_debug(
            "Batch FreeText find: "
            f"scope={scope_text} include_mode={include_mode!r} include={include_terms!r} "
            f"exclude_mode={exclude_mode!r} exclude={exclude_terms!r} "
            f"results={len(matches)} warnings={len(warnings)}"
        )

    def jump_to_batch_freetext_result(self, page_index: int, xref: int) -> None:
        if self.doc is None:
            return
        self.record_view_location_before_navigation()
        self.annotation_controller.clear_tool_interaction()
        self.page_index = max(0, min(page_index, len(self.doc) - 1))
        if self.view_mode == "continuous":
            found = self.scroll_continuous_to_xref(self.page_index, xref)
            self.update_current_recent_page()
            if found:
                selected = self.select_continuous_annotation_by_xref(self.page_index, xref)
                self.statusBar().showMessage(f"Jumped to FreeText result on page {self.page_index + 1}.")
                self.log_debug(
                    "Batch FreeText jumped in continuous view: "
                    f"page={self.page_index + 1} xref={xref} selected={selected}"
                )
            else:
                main_window_dialogs.show_warning(
                    self,
                    "Batch FreeText",
                    f"FreeText annotation was not found on page {self.page_index + 1}.\n\nxref={xref}",
                )
                self.log_debug(f"Batch FreeText jump failed: page={self.page_index + 1} xref={xref}")
            return
        self.render_page()
        self.update_current_recent_page()
        selected = self.select_annotation_by_xref(xref, render_page=False)
        if selected:
            self.statusBar().showMessage(f"Jumped to FreeText result on page {self.page_index + 1}.")
            self.log_debug(f"Batch FreeText jumped: page={self.page_index + 1} xref={xref}")
        else:
            self.defer_scroll_to_page_top_left()
            main_window_dialogs.show_warning(
                self,
                "Batch FreeText",
                f"FreeText annotation was not found on page {self.page_index + 1}.\n\nxref={xref}",
            )
            self.log_debug(f"Batch FreeText jump failed: page={self.page_index + 1} xref={xref}")

    def replace_selected_batch_freetext(self, options) -> None:
        result = self.selected_batch_freetext_result()
        if result is None:
            return
        keyword = self.batch_freetext_keyword()
        replacement, whole_word, regex = self.batch_freetext_replace_options(options)
        try:
            changed = self.apply_batch_freetext_text_change(
                [result],
                lambda text: replace_match_text_with_mode(
                    text,
                    keyword,
                    replacement,
                    whole_word=whole_word,
                    regex=regex,
                ),
                "Replace Selected FreeText",
            )
        except InvalidRegexError as exc:
            self.show_invalid_regex_error(exc)
            self.log_debug(f"Batch FreeText Replace Selected invalid regex: {exc}")
            return
        self.finish_batch_freetext_text_change(changed, "Replaced selected FreeText match text.")

    def replace_all_batch_freetext(self, options) -> None:
        if self.batch_freetext_widget is None:
            return
        results = list(self.batch_freetext_widget.results)
        if not results:
            main_window_dialogs.show_warning(self, "Batch FreeText", "No FreeText search results to replace.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                changed, match_count = self.execute_replace_all_batch_freetext(options, results)
            except InvalidRegexError as exc:
                self.show_invalid_regex_error(exc)
                self.log_debug(f"Batch FreeText Replace All invalid regex: {exc}")
                return
        finally:
            QApplication.restoreOverrideCursor()
        self.show_batch_freetext_modified_message(changed, match_count)

    def preview_replace_all_batch_freetext(self, options) -> None:
        if self.batch_freetext_widget is None:
            return
        results = list(self.batch_freetext_widget.results)
        if not results:
            main_window_dialogs.show_warning(self, "Batch FreeText", "No FreeText search results to preview.")
            return
        self.show_batch_freetext_replace_preview(options, results)

    def show_regex_help(self) -> None:
        help_path = Path(__file__).with_name("Regex_Query_Replace_Examples.txt")
        if not help_path.exists():
            main_window_dialogs.show_warning(self, "Regex Help", f"Regex help file not found:\n{help_path}")
            return
        try:
            text = help_path.read_text(encoding="utf-8")
        except Exception as exc:
            self.show_error("Regex Help failed", exc)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Regex Query and Replace Examples")
        dialog.resize(820, 640)
        layout = QVBoxLayout(dialog)
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(text)
        layout.addWidget(text_edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_batch_freetext_replace_preview(self, options, results: list[FreeTextMatch]) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                preview_rows = self.batch_freetext_replace_preview_rows(options, results)
            except InvalidRegexError as exc:
                self.show_invalid_regex_error(exc)
                self.log_debug(f"Batch FreeText Replace Preview invalid regex: {exc}")
                return
        finally:
            QApplication.restoreOverrideCursor()
        dialog = BatchReplacePreviewDialog(preview_rows, self)

        def replace_all_from_preview() -> None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                try:
                    changed, match_count = self.execute_replace_all_batch_freetext(options, results)
                except InvalidRegexError as exc:
                    self.show_invalid_regex_error(exc)
                    self.log_debug(f"Batch FreeText Preview Replace All invalid regex: {exc}")
                    return
            finally:
                QApplication.restoreOverrideCursor()
            if changed > 0:
                dialog.accept()
                self.show_batch_freetext_modified_message(changed, match_count)

        dialog.replace_all_requested.connect(replace_all_from_preview)
        dialog.exec()

    def execute_replace_all_batch_freetext(self, options, results: list[FreeTextMatch]) -> tuple[int, int]:
        keyword = self.batch_freetext_keyword()
        replacement, whole_word, regex = self.batch_freetext_replace_options(options)
        match_count = self.batch_freetext_replacement_match_count(options, results)
        changed = self.apply_batch_freetext_text_change(
            results,
            lambda text: replace_match_text_with_mode(
                text,
                keyword,
                replacement,
                whole_word=whole_word,
                regex=regex,
            ),
            "Replace All FreeText",
        )
        self.finish_batch_freetext_text_change(changed, f"Replaced match text in {changed} FreeText annotation(s).")
        return changed, match_count if changed > 0 else 0

    def show_batch_freetext_modified_message(self, changed: int, match_count: int = 0) -> None:
        if changed <= 0:
            return
        main_window_dialogs.show_information(
            self,
            "Batch FreeText",
            f"Modified {changed} FreeText annotation(s), {match_count} match(es).",
        )

    def batch_freetext_replace_preview_rows(self, options, results: list[FreeTextMatch]) -> list[dict]:
        keyword = self.batch_freetext_keyword()
        if not keyword:
            return []
        replacement, whole_word, regex = self.batch_freetext_replace_options(options)
        rows: list[dict] = []
        preview_results = sorted(
            self.unique_batch_freetext_targets(results),
            key=lambda item: (item.page_index, item.xref),
        )
        for result in preview_results:
            try:
                preview = preview_replace_text_with_mode(
                    result.text,
                    keyword,
                    replacement,
                    whole_word=whole_word,
                    regex=regex,
                )
            except Exception as exc:
                self.show_error("Batch FreeText Replace Preview failed", exc)
                return []
            if preview.new_text == result.text:
                continue
            rows.append(
                {
                    "page_number": result.page_index + 1,
                    "old_text": result.text,
                    "new_text": preview.new_text,
                    "old_html": preview.old_html,
                    "new_html": preview.new_html,
                    "match_count": preview.match_count,
                }
            )
        return rows

    def batch_freetext_replacement_match_count(self, options, results: list[FreeTextMatch]) -> int:
        keyword = self.batch_freetext_keyword()
        if not keyword:
            return 0
        replacement, whole_word, regex = self.batch_freetext_replace_options(options)
        total = 0
        for result in self.unique_batch_freetext_targets(results):
            preview = preview_replace_text_with_mode(
                result.text,
                keyword,
                replacement,
                whole_word=whole_word,
                regex=regex,
            )
            if preview.new_text != result.text:
                total += preview.match_count
        return total

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

    def batch_freetext_replace_options(self, options) -> tuple[str, bool, bool]:
        if isinstance(options, dict):
            return (
                str(options.get("replacement", "")),
                bool(options.get("whole_word", False)),
                bool(options.get("regex", False)),
            )
        return str(options), False, False

    def batch_freetext_exclude_keyword(self) -> str:
        if self.batch_freetext_widget is None:
            return ""
        return self.batch_freetext_widget.current_exclude_keyword().strip()

    def apply_batch_freetext_text_change(self, results: list[FreeTextMatch], transform, label: str) -> int:
        if self.doc is None:
            return 0
        if self.view_mode == "continuous":
            self.statusBar().showMessage("Continuous view is read-only. Switch to Single Page to edit Batch FreeText.")
            return 0
        keyword = self.batch_freetext_keyword()
        if not keyword:
            main_window_dialogs.show_warning(self, "Batch FreeText", "Enter Replace target text before editing.")
            return 0

        changed = 0
        snapshot = self.active_freetext_snapshot()
        targets_by_page: dict[int, list[FreeTextMatch]] = {}
        for result in self.unique_batch_freetext_targets(results):
            targets_by_page.setdefault(result.page_index, []).append(result)

        repository = AnnotationRepository(self.doc)
        for page_index, page_results in sorted(targets_by_page.items()):
            if page_index < 0 or page_index >= len(self.doc):
                continue
            models = {
                model.xref: model
                for model in repository.load_page_annotations(page_index)
                if model.app_type == "freetext" and not model.deleted
            }
            for result in page_results:
                model = models.get(result.xref)
                if model is None:
                    self.log_debug(
                        f"Batch FreeText skipped missing annotation: page={result.page_index + 1} xref={result.xref}"
                    )
                    continue
                try:
                    new_text = transform(model.text)
                except Exception as exc:
                    self.show_error(f"{label} failed", exc)
                    return changed
                if new_text == model.text:
                    continue
                if text_affects_anchor_data(model.text, new_text):
                    self.mark_navigation_anchors_dirty()
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
                if snapshot is not None:
                    snapshot.update_text(result.page_index, result.xref, new_text)
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
        if self.view_mode == "continuous":
            self.statusBar().showMessage("Continuous view is read-only. Switch to Single Page to delete Batch FreeText.")
            return 0

        deleted = 0
        writer = PdfAnnotationWriter(self.doc)
        snapshot = self.active_freetext_snapshot()
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
            if text_affects_anchor_data(model.text, ""):
                self.mark_navigation_anchors_dirty()
            if snapshot is not None:
                snapshot.remove(result.page_index, result.xref)
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
                self.batch_freetext_widget.current_include_conditions(),
                self.batch_freetext_widget.current_include_mode(),
                self.batch_freetext_widget.current_exclude_terms(),
                self.batch_freetext_widget.current_exclude_mode(),
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
                self.batch_freetext_widget.current_include_conditions(),
                self.batch_freetext_widget.current_include_mode(),
                self.batch_freetext_widget.current_exclude_terms(),
                self.batch_freetext_widget.current_exclude_mode(),
                self.batch_freetext_widget.current_scope(),
            )

    def rect_text(self, rect: fitz.Rect) -> str:
        return self.annotation_controller.rect_text(rect)

    def annotation_note(self, model: AnnotationModel) -> str:
        return self.annotation_controller.annotation_note(model)

    def select_annotation_by_xref(self, xref: int, render_page: bool = True) -> bool:
        return self.annotation_controller.select_annotation_by_xref(xref, render_page)

    def begin_add_tool(self, tool: str) -> None:
        self.annotation_controller.begin_add_tool(tool)

    def activate_text_mode(self) -> None:
        self.annotation_controller.activate_text_mode()

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
        select_all: bool = True,
        stable_edit_mode: bool = False,
        max_height: float | None = None,
    ) -> None:
        if self.inline_freetext_editor is None:
            return
        self.inline_freetext_editor.begin(
            scene_pos,
            text=text,
            width=width,
            height=height,
            font_size=font_size or self.default_freetext_font_size,
            select_all=select_all,
            stable_edit_mode=stable_edit_mode,
            max_height=max_height,
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
        self.begin_inline_freetext_editor(
            top_left,
            text,
            width,
            height,
            font_size,
            max_height=page_rect.bottom() - top_left.y(),
        )

    def cancel_inline_freetext_editor(self) -> None:
        if self.inline_freetext_editor is not None:
            self.inline_freetext_editor.cancel()

    def confirm_inline_freetext_editor_from_canvas_click(self, scene_pos: QPointF) -> bool:
        if self.inline_freetext_editor is None or not self.inline_freetext_editor.is_active():
            return False
        if self.inline_freetext_editor.contains_scene_pos(scene_pos):
            return False
        self.inline_freetext_clear_selection_on_accept = True
        self.inline_freetext_editor.accept_current()
        return True

    def is_inline_freetext_editor_hit(self, scene_pos: QPointF) -> bool:
        return (
            self.inline_freetext_editor is not None
            and self.inline_freetext_editor.is_active()
            and self.inline_freetext_editor.contains_scene_pos(scene_pos)
        )

    def show_inline_freetext_context_menu(self, screen_pos) -> bool:
        if self.inline_freetext_editor is None:
            return False
        return self.inline_freetext_editor.show_keyword_popup(screen_pos)

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
        font_size = int(model.font_size or self.default_freetext_font_size)
        page_rect = self.page_item.boundingRect()
        self.hide_inline_freetext_overlay(model.id)
        self.begin_inline_freetext_editor(
            rect.topLeft(),
            text=model.text,
            width=max(40.0, rect.width()),
            height=max(14.0, rect.height()),
            font_size=font_size,
            edit_annotation_id=model.id,
            select_all=False,
            stable_edit_mode=True,
            max_height=page_rect.bottom() - rect.top(),
        )
        self.statusBar().showMessage("Editing FreeText inline. Ctrl+Enter to apply, Esc to cancel.")
        return True

    def on_inline_freetext_accept(self, text: str, scene_pos: QPointF, scene_size) -> None:
        if self.doc is None:
            return
        edit_annotation_id = self.inline_freetext_edit_annotation_id
        edit_page_index = self.inline_freetext_edit_page_index
        edit_xref = self.inline_freetext_edit_xref
        clear_selection = self.inline_freetext_clear_selection_on_accept
        self.inline_freetext_edit_annotation_id = None
        self.inline_freetext_edit_page_index = None
        self.inline_freetext_edit_xref = None
        self.inline_freetext_clear_selection_on_accept = False
        if edit_annotation_id is not None:
            self.finish_inline_freetext_edit(edit_annotation_id, text, scene_pos, scene_size, edit_page_index, edit_xref)
            if clear_selection:
                self.select_annotation(None)
            return

        text = text.strip()
        if not text:
            self.log_debug("Inline FreeText accepted empty text; no annotation created")
            if clear_selection:
                self.select_annotation(None)
            return
        try:
            scene_size = self.tight_new_freetext_scene_size(
                text,
                scene_size,
                self.default_freetext_font_size,
            )
            scene_rect = QRectF(scene_pos, scene_size)
            if self.view_mode == "continuous" and edit_page_index is not None:
                rect = self.annotation_controller.continuous_pdf_rect_from_scene_points(
                    edit_page_index,
                    scene_rect.topLeft(),
                    scene_rect.bottomRight(),
                )
                annot = PdfAnnotationWriter(self.doc).add_freetext_annotation(
                    self.doc[edit_page_index],
                    rect,
                    text,
                    self.default_freetext_font_size,
                    (1, 0, 0),
                    self.use_foxit_freetext,
                )
                xref = annot.xref
                self.mark_dirty()
                if text_affects_anchor_data("", text):
                    self.mark_navigation_anchors_dirty()
                self.update_freetext_snapshot_text(edit_page_index, xref, text)
                self.record_add_undo("Add FreeText", edit_page_index, xref)
                self.annotation_controller.refresh_continuous_page_after_geometry_edit(edit_page_index, xref)
                self.set_active_tool("freetext")
            else:
                rect = self.pdf_rect_from_scene_rect(scene_rect)
                xref = self.create_freetext_annotation(rect, text)
                self.update_freetext_snapshot_text(self.page_index, xref, text)
                self.record_add_undo("Add FreeText", self.page_index, xref)
                self.set_active_tool("freetext")
            self.statusBar().showMessage(f"Added FreeText xref={xref}. Use Save to persist.")
            self.log_debug(
                f"Inline FreeText created: xref={xref} chars={len(text)} "
                f"scene=({scene_pos.x():.1f},{scene_pos.y():.1f}) "
                f"size=({scene_size.width():.1f},{scene_size.height():.1f})"
            )
            if clear_selection:
                self.select_annotation(None)
        except Exception as exc:
            self.show_error("Add FreeText failed", exc)

    def on_inline_freetext_cancel(self) -> None:
        self.inline_freetext_edit_annotation_id = None
        self.inline_freetext_edit_page_index = None
        self.inline_freetext_edit_xref = None
        self.inline_freetext_clear_selection_on_accept = False
        self.show_hidden_inline_freetext_overlay()
        if self.view_mode == "continuous":
            self.refresh_annotation_overlay(preserve_selection=True)
        self.log_debug("Inline FreeText canceled")

    def finish_inline_freetext_edit(
        self,
        annotation_id: str,
        text: str,
        scene_pos: QPointF,
        scene_size,
        page_index: int | None = None,
        xref: int | None = None,
    ) -> None:
        model = self.annotation_model_map.get(annotation_id)
        if model is None and self.view_mode == "continuous" and page_index is not None and xref is not None:
            model = next(
                (candidate for candidate in self.canvas_controller.continuous_load_page_annotations(page_index) if candidate.xref == xref),
                None,
            )
        if model is None or model.app_type != "freetext":
            self.log_debug(f"Inline FreeText edit skipped: annotation not found id={annotation_id}")
            return
        if not text.strip():
            self.show_hidden_inline_freetext_overlay()
            if self.view_mode == "continuous":
                self.annotation_controller.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            elif self.selected_annotation_id != annotation_id:
                self.select_annotation(annotation_id)
            self.statusBar().showMessage("FreeText edit ignored: empty text.")
            self.log_debug(f"Inline FreeText edit ignored empty text: xref={model.xref}")
            return
        if self.selected_annotation_id != annotation_id:
            self.select_annotation(annotation_id)
        if text == model.text:
            if self.view_mode == "continuous":
                self.annotation_controller.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            else:
                self.show_hidden_inline_freetext_overlay()
            self.statusBar().showMessage(f"FreeText unchanged xref={model.xref}.")
            return
        try:
            font_size = int(model.font_size or self.default_freetext_font_size)
            color = model.color or (1, 0, 0)
            scene_size = self.tight_new_freetext_scene_size(text, scene_size, font_size)
            scene_rect = QRectF(scene_pos, scene_size)
            if self.view_mode == "continuous":
                edit_page_index = page_index if page_index is not None else model.page_index
                rect = self.annotation_controller.continuous_pdf_rect_from_scene_points(
                    edit_page_index,
                    scene_rect.topLeft(),
                    scene_rect.bottomRight(),
                )
                self.annotation_controller.update_freetext_annotation_clean_appearance_in_rect_on_page(
                    edit_page_index,
                    model,
                    rect,
                    text,
                    font_size,
                    color,
                )
                self.mark_dirty()
                self.update_freetext_snapshot_text(edit_page_index, model.xref, text)
                self.annotation_controller.refresh_continuous_page_after_geometry_edit(edit_page_index, model.xref)
            else:
                rect = self.pdf_rect_from_scene_rect(scene_rect)
                self.annotation_controller.update_freetext_annotation_clean_appearance_in_rect_on_page(
                    self.page_index,
                    model,
                    rect,
                    text,
                    font_size,
                    color,
                )
                self.mark_dirty()
                self.update_freetext_snapshot_text(self.page_index, model.xref, text)
                self.refresh_annotation_overlay(preserve_selection=True)
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

    def on_tool_mouse_press(self, scene_pos: QPointF, button=None) -> bool:
        return self.annotation_controller.on_tool_mouse_press(scene_pos, button)

    def on_tool_mouse_move(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_tool_mouse_move(scene_pos)

    def on_tool_mouse_release(self, scene_pos: QPointF) -> bool:
        return self.annotation_controller.on_tool_mouse_release(scene_pos)

    def on_scene_mouse_hover(self, scene_pos: QPointF) -> None:
        self.annotation_controller.on_scene_mouse_hover(scene_pos)

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

    def tight_inline_freetext_scene_size(self, text: str, scene_size, font_size: int):
        width_scene = max(12.0, float(scene_size.width()))
        width_pdf = max(1.0, width_scene / max(0.01, self.zoom))
        line_count = self.estimated_wrapped_freetext_line_count(text, font_size, width_pdf)
        # PDF FreeText appearance generation needs more vertical room than the
        # QGraphicsTextItem visual bounds. Too-tight rectangles can render as
        # blank in the overlay or external PDF readers.
        height_pdf = ceil(max(12.0, line_count * font_size * 1.35 + 6.0))
        return QSizeF(width_scene, max(14.0, height_pdf * self.zoom))

    def tight_new_freetext_scene_size(self, text: str, scene_size, font_size: int):
        original_width_scene = max(12.0, float(scene_size.width()))
        estimated_width_pdf = self.estimated_freetext_width(text, font_size) + 4.0
        tight_width_scene = max(24.0, min(original_width_scene, ceil(estimated_width_pdf * self.zoom)))
        return self.tight_inline_freetext_scene_size(text, QSizeF(tight_width_scene, scene_size.height()), font_size)

    def estimated_wrapped_freetext_line_count(self, text: str, font_size: int, width_pdf: float) -> int:
        line_count = 0
        max_width = max(1.0, width_pdf - 1.0)
        for paragraph in text.splitlines() or [""]:
            if not paragraph:
                line_count += 1
                continue
            current_width = 0.0
            paragraph_lines = 1
            for char in paragraph:
                char_width = font_size * (1.0 if ord(char) > 127 else 0.55)
                if current_width > 0 and current_width + char_width > max_width:
                    paragraph_lines += 1
                    current_width = char_width
                else:
                    current_width += char_width
            line_count += paragraph_lines
        return max(1, line_count)

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
        self.annotation_controller.clear_tool_interaction()
        self.page_index -= 1
        if self.view_mode == "continuous":
            self.scroll_continuous_to_page(self.page_index)
            self.update_current_recent_page()
            self.save_active_session_state()
            return
        self.render_page()
        if self.view_mode != "continuous":
            self.defer_scroll_to_page_bottom_left()
        self.update_current_recent_page()
        self.save_active_session_state()

    def next_page(self) -> None:
        if self.doc is None or self.page_index >= len(self.doc) - 1:
            return
        self.record_view_location_before_navigation()
        self.annotation_controller.clear_tool_interaction()
        self.page_index += 1
        if self.view_mode == "continuous":
            self.scroll_continuous_to_page(self.page_index)
            self.update_current_recent_page()
            self.save_active_session_state()
            return
        self.render_page()
        if self.view_mode != "continuous":
            self.defer_scroll_to_page_top_left()
        self.update_current_recent_page()
        self.save_active_session_state()

    def jump_pages(self, delta: int) -> None:
        if self.doc is None or delta == 0:
            return
        target_index = max(0, min(len(self.doc) - 1, self.page_index + int(delta)))
        if target_index == self.page_index:
            return
        self.record_view_location_before_navigation()
        self.annotation_controller.clear_tool_interaction()
        self.page_index = target_index
        if self.view_mode == "continuous":
            self.scroll_continuous_to_page(self.page_index)
            self.update_current_recent_page()
            self.save_active_session_state()
            return
        self.render_page()
        if delta < 0:
            self.defer_scroll_to_page_bottom_left()
        else:
            self.defer_scroll_to_page_top_left()
        self.update_current_recent_page()
        self.save_active_session_state()

    def zoom_in(self) -> None:
        self.set_zoom_scale(min(self.zoom + 0.25, 4.0))

    def zoom_out(self) -> None:
        self.set_zoom_scale(max(self.zoom - 0.25, 0.5))

    def set_zoom_scale(self, scale: float) -> None:
        if self.doc is None or not 0.5 <= scale <= 4.0:
            main_window_actions.sync_zoom_scale(self, force=True)
            return
        if abs(self.zoom - scale) < 1e-12:
            main_window_actions.sync_zoom_scale(self, force=True)
            return
        anchor = self.canvas_controller.capture_view_mode_anchor()
        with self.reading_workspace.sync.hold():
            self.zoom = scale
            self.render_page(preserve_selection=True)
            self.canvas_controller.restore_view_mode_anchor(anchor)
            self.save_active_session_state()
        main_window_actions.sync_zoom_scale(self, force=True)

    def show_error(self, title: str, exc: Exception) -> None:
        main_window_dialogs.show_error(self, title, exc)

    def keyPressEvent(self, event) -> None:
        if (
            event.key() == Qt.Key.Key_C
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and self.copy_selected_pdf_text_to_clipboard()
        ):
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Escape
            and self.inline_freetext_editor is not None
            and self.inline_freetext_editor.is_active()
        ):
            self.inline_freetext_editor.accept_current()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.active_tool is not None:
            self.cancel_add_tool()
            self.show_page_status()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selected_pdf_text_to_clipboard(self) -> bool:
        return self.annotation_controller.copy_selected_pdf_text_to_clipboard()

    def closeEvent(self, event) -> None:
        if not self.confirm_all_unsaved_for_exit():
            self.log_debug("Exit canceled by unsaved changes prompt")
            event.ignore()
            return
        if getattr(self, "ui_contrast_save_timer", None) is not None and self.ui_contrast_save_timer.isActive():
            self.ui_contrast_save_timer.stop()
            self.save_app_settings()
        self.stop_continuous_scroll_timer()
        self.cancel_add_tool()
        self.update_current_recent_page()
        self.reading_workspace.shutdown()
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


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def main() -> int:
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    icon_path = resource_path("assets/app_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
