import os
import re
import gc
import shutil
import sys
import tempfile
from datetime import datetime
from math import atan2, ceil, cos, pi, sin
from pathlib import Path

import pymupdf as fitz
from app.annotation_index import AnnotationIndex
from app.annotation_interaction import AnnotationInteractionController
from app.annotation_items import AnnotationItemRenderer
from app.annotation_list import AnnotationListWidget
from app.annotation_properties import AnnotationPropertiesWidget
from app.annotation_repository import AnnotationRepository
from app.annotation_search import AnnotationSearchWidget
from app.document_session import DocumentSession
from app.annotation_selection import AnnotationSelectionRenderer
from app.index_worker import ReindexWorker
from app.models import (
    ANNOTATION_COLORS,
    DRAGGABLE_APP_TYPES,
    EDITABLE_APP_TYPES,
    AnnotationModel,
)
from app.pdf_canvas import AnnotationScene, PdfCanvasView
from app.pdf_audit import audit_current_page as run_audit_current_page
from app.pdf_audit import audit_document_summary as run_audit_document_summary
from app.pdf_audit import format_audit_report
from app.pdf_audit import report_has_errors
from app.settings import AppSettings, load_settings, save_settings, settings_path
from app.undo import UndoAction
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QThread, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QFileDialog,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.doc: fitz.Document | None = None
        self.annotation_repo: AnnotationRepository | None = None
        self.pdf_path: Path | None = None
        self.page_index = 0
        self.zoom = 1.5
        self.use_foxit_freetext = False
        self.freetext_font_size_min = 4
        self.freetext_font_size_max = 20
        self.default_freetext_font_size = 7
        self.default_highlight_color = (1, 1, 0)
        self.default_highlight_opacity = 0.45
        self.extract_highlight_text_on_reindex = False
        self.search_page_size = 500
        self.recent_files: list[dict] = []
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
        self.annotation_search_widget: AnnotationSearchWidget | None = None
        self.annotation_search_restore_geometry = None
        self.annotation_search_maximized = False
        self.reindex_thread: QThread | None = None
        self.reindex_worker: ReindexWorker | None = None
        self.reindex_pdf_path: Path | None = None
        self.open_recent_menu = None
        self.annotations_table: AnnotationListWidget | None = None
        self.properties_page: AnnotationPropertiesWidget | None = None
        self.updating_page_spin = False
        self.updating_table_selection = False
        self.updating_scene_selection = False
        self.applying_property_change = False
        self.active_tool: str | None = None
        self.tool_start_scene_pos: QPointF | None = None
        self.tool_preview_item: QGraphicsItem | None = None
        self.tool_preview_items: list[QGraphicsItem] = []
        self.text_lines_cache_page_index: int | None = None
        self.text_lines_cache: list[list[dict]] | None = None

        self.scene = AnnotationScene(self)
        self.scene.selectionChanged.connect(self.on_scene_selection_changed)
        self.view = PdfCanvasView(self)
        self.view.setScene(self.scene)
        self.page_item = QGraphicsPixmapItem()
        self.page_item.setZValue(0)
        self.scene.addItem(self.page_item)
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
        self.scroll_boundary_label = QLabel("")
        self.scroll_boundary_label.setVisible(False)
        self.scroll_boundary_label.setStyleSheet(
            "QLabel { color: white; background: rgb(190, 80, 0); padding: 2px 8px; font-weight: 700; }"
        )
        self.statusBar().addPermanentWidget(self.scroll_boundary_label)

        self.update_window_title()
        self.resize(1200, 850)
        self.create_actions()
        self.create_menus()
        self.create_toolbar()
        self.show_current_page_annotations()
        self.update_actions()

    def create_actions(self) -> None:
        self.open_action = QAction("Open", self)
        self.open_action.triggered.connect(self.open_pdf)

        self.close_action = QAction("Close", self)
        self.close_action.triggered.connect(self.close_pdf)

        self.save_action = QAction("Save", self)
        self.save_action.triggered.connect(lambda checked=False: self.save())

        self.save_incremental_action = QAction("Save Incremental", self)
        self.save_incremental_action.triggered.connect(lambda checked=False: self.save_incremental())

        self.save_as_action = QAction("Save As", self)
        self.save_as_action.triggered.connect(self.save_as)

        self.settings_action = QAction("Settings...", self)
        self.settings_action.triggered.connect(self.open_settings)

        self.undo_action_qt = QAction("Undo", self)
        self.undo_action_qt.setShortcut("Ctrl+Z")
        self.undo_action_qt.triggered.connect(self.undo_last_action)

        self.delete_annotation_action = QAction("Delete Annotation", self)
        self.delete_annotation_action.setShortcut("Delete")
        self.delete_annotation_action.triggered.connect(self.delete_selected_annotation)

        self.edit_annotation_action = QAction("Annotation Properties", self)
        self.edit_annotation_action.triggered.connect(self.show_annotation_properties)

        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)

        self.prev_action = QAction("Prev", self)
        self.prev_action.triggered.connect(self.prev_page)

        self.next_action = QAction("Next", self)
        self.next_action.triggered.connect(self.next_page)

        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setEnabled(False)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(self.go_to_page)

        self.page_count_label = QLabel("/ 0")

        self.zoom_out_action = QAction("Zoom -", self)
        self.zoom_out_action.triggered.connect(self.zoom_out)

        self.zoom_in_action = QAction("Zoom +", self)
        self.zoom_in_action.triggered.connect(self.zoom_in)

        self.add_typewriter_action = QAction("Add FreeText", self)
        self.add_typewriter_action.setCheckable(True)
        self.add_typewriter_action.triggered.connect(self.add_typewriter)

        self.add_rectangle_action = QAction("Add Square", self)
        self.add_rectangle_action.setCheckable(True)
        self.add_rectangle_action.triggered.connect(self.add_rectangle)

        self.add_highlight_action = QAction("Add Highlight", self)
        self.add_highlight_action.setCheckable(True)
        self.add_highlight_action.triggered.connect(self.add_highlight)

        self.add_arrow_action = QAction("Add Arrow", self)
        self.add_arrow_action.setCheckable(True)
        self.add_arrow_action.triggered.connect(self.add_arrow)

        self.show_annotations_action = QAction("Annotations", self)
        self.show_annotations_action.triggered.connect(self.show_current_page_annotations)

        self.audit_current_page_action = QAction("Audit Current Page", self)
        self.audit_current_page_action.triggered.connect(self.audit_current_page)

        self.audit_document_summary_action = QAction("Audit Document Summary", self)
        self.audit_document_summary_action.triggered.connect(self.audit_document_summary)

        self.reindex_current_pdf_action = QAction("Reindex Current PDF", self)
        self.reindex_current_pdf_action.triggered.connect(self.reindex_current_pdf)

        self.clear_annotation_index_action = QAction("Clear Annotation Index", self)
        self.clear_annotation_index_action.triggered.connect(self.clear_annotation_index)

        self.index_database_info_action = QAction("Index Database Info", self)
        self.index_database_info_action.triggered.connect(self.show_index_database_info)

        self.search_annotations_action = QAction("Search Annotations", self)
        self.search_annotations_action.triggered.connect(self.show_annotation_search)

        self.debug_log_action = QAction("Debug Log", self)
        self.debug_log_action.triggered.connect(self.show_debug_log)

        self.debug_current_page_state_action = QAction("Debug Current Page State", self)
        self.debug_current_page_state_action.triggered.connect(self.debug_current_page_state)

        self.debug_selected_annotation_pdf_object_action = QAction("Debug Selected Annotation PDF Object", self)
        self.debug_selected_annotation_pdf_object_action.triggered.connect(self.debug_selected_annotation_pdf_object)

    def create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.open_action)
        self.open_recent_menu = file_menu.addMenu("Open Recent")
        self.refresh_recent_files_menu()
        file_menu.addAction(self.close_action)
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(self.settings_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)
        file_menu.addSeparator()
        file_menu.addAction(self.save_incremental_action)

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction(self.undo_action_qt)
        edit_menu.addSeparator()
        edit_menu.addAction(self.edit_annotation_action)
        edit_menu.addAction(self.delete_annotation_action)

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction(self.show_annotations_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self.audit_current_page_action)
        tools_menu.addAction(self.audit_document_summary_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self.reindex_current_pdf_action)
        tools_menu.addAction(self.clear_annotation_index_action)
        tools_menu.addAction(self.index_database_info_action)
        tools_menu.addAction(self.search_annotations_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self.debug_current_page_state_action)
        tools_menu.addAction(self.debug_selected_annotation_pdf_object_action)
        tools_menu.addAction(self.debug_log_action)

    def create_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.prev_action)
        toolbar.addAction(self.next_action)
        toolbar.addWidget(QLabel("Page"))
        toolbar.addWidget(self.page_spin)
        toolbar.addWidget(self.page_count_label)

        toolbar.addSeparator()

        toolbar.addAction(self.zoom_out_action)
        toolbar.addAction(self.zoom_in_action)

        toolbar.addSeparator()

        toolbar.addAction(self.add_typewriter_action)
        toolbar.addAction(self.add_rectangle_action)
        toolbar.addAction(self.add_highlight_action)
        toolbar.addAction(self.add_arrow_action)

    def update_actions(self) -> None:
        has_doc = self.doc is not None
        is_reindexing = self.reindex_thread is not None
        for action in (
            self.close_action,
            self.save_action,
            self.save_incremental_action,
            self.save_as_action,
            self.prev_action,
            self.next_action,
            self.zoom_out_action,
            self.zoom_in_action,
            self.add_typewriter_action,
            self.add_rectangle_action,
            self.add_highlight_action,
            self.add_arrow_action,
            self.show_annotations_action,
            self.audit_current_page_action,
            self.audit_document_summary_action,
            self.debug_current_page_state_action,
            self.reindex_current_pdf_action,
        ):
            action.setEnabled(has_doc)
        self.delete_annotation_action.setEnabled(has_doc and self.selected_annotation_id is not None)
        self.edit_annotation_action.setEnabled(has_doc and self.selected_annotation_id is not None)
        self.debug_selected_annotation_pdf_object_action.setEnabled(
            has_doc and self.selected_annotation_id is not None
        )
        self.undo_action_qt.setEnabled(has_doc and self.undo_action is not None)
        self.clear_annotation_index_action.setEnabled(not is_reindexing)
        self.index_database_info_action.setEnabled(not is_reindexing)
        self.search_annotations_action.setEnabled(not is_reindexing)

        if not has_doc or self.doc is None:
            self.close_action.setEnabled(False)
            self.delete_annotation_action.setEnabled(False)
            self.edit_annotation_action.setEnabled(False)
            self.debug_selected_annotation_pdf_object_action.setEnabled(False)
            self.undo_action_qt.setEnabled(False)
            self.page_spin.setEnabled(False)
            self.page_spin.setMaximum(1)
            self.page_count_label.setText("/ 0")
            return

        self.prev_action.setEnabled(self.page_index > 0)
        self.next_action.setEnabled(self.page_index < len(self.doc) - 1)
        self.page_spin.setEnabled(True)
        self.page_spin.setMaximum(len(self.doc))
        self.page_count_label.setText(f"/ {len(self.doc)}")
        if is_reindexing:
            for action in (
                self.open_action,
                self.close_action,
                self.save_action,
                self.save_incremental_action,
                self.save_as_action,
                self.reindex_current_pdf_action,
                self.clear_annotation_index_action,
                self.index_database_info_action,
                self.search_annotations_action,
            ):
                action.setEnabled(False)

    def open_pdf(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if not file_name:
            return

        self.open_pdf_path(Path(file_name), self.recent_page_index(Path(file_name)))

    def open_pdf_path(self, path: Path, page_index: int = 0) -> bool:
        existing_index = self.session_index_for_path(path)
        if existing_index is not None:
            self.log_debug(f"Open switched to existing tab: {path}")
            self.set_active_session(existing_index, preserve_selection=True)
            return True

        try:
            self.log_debug(f"Open started: {path}")
            new_doc = fitz.open(path)
            if len(new_doc) == 0:
                new_doc.close()
                raise RuntimeError(
                    "This PDF has no readable pages. It may be damaged or have a broken page tree."
                )
            self.log_debug(f"Open loaded: {path} pages={len(new_doc)}")
            self.save_active_session_state()
            session = DocumentSession(
                doc=new_doc,
                path=path,
                page_index=max(0, min(page_index, len(new_doc) - 1)),
                zoom=self.zoom,
            )
            self.sessions.append(session)
            self.add_document_tab(session)
            self.set_active_session(len(self.sessions) - 1)
            self.update_recent_file(path, self.page_index)
            self.log_debug(f"Open completed: {path} page={self.page_index + 1}")
            return True
        except Exception as exc:
            self.log_debug(f"Open failed: {path}: {exc}")
            self.show_error("Open failed", exc)
            return False

    def close_pdf(self) -> None:
        closed_path = self.pdf_path
        if self.active_session_index is not None:
            if self.close_document_tab(self.active_session_index):
                self.log_debug(f"Close completed: {closed_path}")
            else:
                self.log_debug("Close canceled by unsaved changes prompt")
            return

        if not self.confirm_active_unsaved_changes("close this PDF"):
            self.log_debug("Close canceled by unsaved changes prompt")
            return

        self.cancel_add_tool()
        self.doc = None
        self.annotation_repo = None
        self.pdf_path = None
        self.page_index = 0
        self.clear_dirty()
        self.clear_undo()
        self.current_annotations = []
        self.clear_annotation_items()
        self.page_item.setPixmap(QPixmap())
        self.scene.setSceneRect(0, 0, 0, 0)
        self.sync_page_spin()
        self.update_window_title()
        self.statusBar().showMessage("No PDF open")
        self.refresh_annotations_table()
        self.refresh_properties_panel()
        self.refresh_annotation_search_status()
        self.sync_page_spin()
        self.update_actions()
        self.log_debug(f"Close completed: {closed_path}")

    def session_index_for_path(self, path: Path) -> int | None:
        try:
            target = path.resolve()
        except OSError:
            target = path
        for index, session in enumerate(self.sessions):
            try:
                session_path = session.path.resolve()
            except OSError:
                session_path = session.path
            if session_path == target:
                return index
        return None

    def save_active_session_state(self) -> None:
        if self.active_session_index is None:
            return
        if self.active_session_index < 0 or self.active_session_index >= len(self.sessions):
            return
        session = self.sessions[self.active_session_index]
        session.page_index = self.page_index
        session.zoom = self.zoom
        session.is_dirty = self.is_dirty
        session.selected_annotation_id = self.selected_annotation_id
        self.update_document_tab_title(self.active_session_index)

    def add_document_tab(self, session: DocumentSession) -> None:
        self.updating_document_tabs = True
        try:
            self.document_tabs.addTab(self.document_tab_title(session))
            self.document_tabs.setTabToolTip(self.document_tabs.count() - 1, str(session.path))
            self.document_tabs.setVisible(self.document_tabs.count() > 0)
        finally:
            self.updating_document_tabs = False

    def document_tab_title(self, session: DocumentSession) -> str:
        prefix = "*" if session.is_dirty else ""
        return f"{prefix}{self.elided_tab_file_name(session.path.name)}"

    def elided_tab_file_name(self, file_name: str, max_chars: int = 34) -> str:
        if len(file_name) <= max_chars:
            return file_name

        suffix = Path(file_name).suffix
        suffix_len = len(suffix)
        if suffix_len >= max_chars - 8:
            return file_name[: max_chars - 3] + "..."

        stem = file_name[: -suffix_len] if suffix else file_name
        keep = max_chars - suffix_len - 3
        return stem[:keep].rstrip() + "..." + suffix

    def update_document_tab_title(self, index: int | None = None) -> None:
        if index is None:
            index = self.active_session_index
        if index is None or index < 0 or index >= len(self.sessions):
            return
        session = self.sessions[index]
        self.document_tabs.setTabText(index, self.document_tab_title(session))
        self.document_tabs.setTabToolTip(index, str(session.path))

    def set_active_session(self, index: int, preserve_selection: bool = False) -> None:
        if index < 0 or index >= len(self.sessions):
            return

        if self.active_session_index == index:
            self.updating_document_tabs = True
            try:
                self.document_tabs.setCurrentIndex(index)
            finally:
                self.updating_document_tabs = False
            return

        self.cancel_add_tool()
        self.save_active_session_state()
        self.active_session_index = index
        session = self.sessions[index]
        self.doc = session.doc
        self.annotation_repo = AnnotationRepository(self.doc)
        self.pdf_path = session.path
        self.page_index = max(0, min(session.page_index, len(self.doc) - 1))
        self.zoom = session.zoom
        self.is_dirty = session.is_dirty
        self.selected_annotation_id = session.selected_annotation_id if preserve_selection else None
        self.clear_undo()
        self.current_annotations = []

        self.updating_document_tabs = True
        try:
            self.document_tabs.setCurrentIndex(index)
        finally:
            self.updating_document_tabs = False
        self.render_page(preserve_selection=preserve_selection)
        self.update_recent_file(self.pdf_path, self.page_index)
        self.refresh_annotation_search_status()
        self.log_debug(f"Document tab activated: {self.pdf_path} page={self.page_index + 1}")

    def on_document_tab_changed(self, index: int) -> None:
        if self.updating_document_tabs:
            return
        self.set_active_session(index, preserve_selection=True)

    def show_document_tab_context_menu(self, pos) -> None:
        index = self.document_tabs.tabAt(pos)
        if index < 0 or index >= len(self.sessions):
            return

        menu = QMenu(self)
        close_action = menu.addAction("Close")
        close_others_action = menu.addAction("Close Others")
        close_all_action = menu.addAction("Close All")
        close_others_action.setEnabled(len(self.sessions) > 1)
        close_all_action.setEnabled(bool(self.sessions))

        selected_action = menu.exec(self.document_tabs.mapToGlobal(pos))
        if selected_action == close_action:
            self.close_document_tab(index)
        elif selected_action == close_others_action:
            self.close_other_document_tabs(index)
        elif selected_action == close_all_action:
            self.close_all_document_tabs()

    def close_document_tab(self, index: int) -> bool:
        if index < 0 or index >= len(self.sessions):
            return True
        if not self.confirm_unsaved_session(index, "close this PDF"):
            self.log_debug(f"Close tab canceled: {self.sessions[index].path}")
            return False
        closed_path = self.sessions[index].path
        self.close_session(index)
        self.log_debug(f"Close tab completed: {closed_path}")
        return True

    def close_other_document_tabs(self, keep_index: int) -> bool:
        if keep_index < 0 or keep_index >= len(self.sessions):
            return True

        keep_path = self.sessions[keep_index].path
        index = len(self.sessions) - 1
        while index >= 0:
            if index != keep_index:
                if not self.close_document_tab(index):
                    return False
                if index < keep_index:
                    keep_index -= 1
            index -= 1

        remaining_index = self.session_index_for_path(keep_path)
        if remaining_index is not None:
            self.set_active_session(remaining_index, preserve_selection=True)
        return True

    def close_all_document_tabs(self) -> bool:
        while self.sessions:
            if not self.close_document_tab(len(self.sessions) - 1):
                return False
        return True

    def close_session(self, index: int) -> None:
        if index < 0 or index >= len(self.sessions):
            return

        was_active = index == self.active_session_index
        session = self.sessions.pop(index)
        if was_active:
            self.update_current_recent_page()
        session.doc.close()

        self.updating_document_tabs = True
        try:
            self.document_tabs.removeTab(index)
            self.document_tabs.setVisible(self.document_tabs.count() > 0)
        finally:
            self.updating_document_tabs = False

        if not self.sessions:
            self.active_session_index = None
            self.doc = None
            self.annotation_repo = None
            self.pdf_path = None
            self.page_index = 0
            self.is_dirty = False
            self.selected_annotation_id = None
            self.clear_undo()
            self.current_annotations = []
            self.clear_annotation_items()
            self.page_item.setPixmap(QPixmap())
            self.scene.setSceneRect(0, 0, 0, 0)
            self.sync_page_spin()
            self.update_window_title()
            self.statusBar().showMessage("No PDF open")
            self.refresh_annotations_table()
            self.refresh_properties_panel()
            self.refresh_annotation_search_status()
            self.update_actions()
            return

        next_index = min(index, len(self.sessions) - 1)
        self.active_session_index = None
        self.set_active_session(next_index, preserve_selection=True)

    def sync_page_spin(self) -> None:
        self.updating_page_spin = True
        try:
            if self.doc is None:
                self.page_spin.setValue(1)
            else:
                self.page_spin.setMaximum(len(self.doc))
                self.page_spin.setValue(self.page_index + 1)
        finally:
            self.updating_page_spin = False

    def mark_dirty(self) -> None:
        self.is_dirty = True
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
        dirty_marker = " *" if self.is_dirty else ""
        if self.doc is None:
            self.setWindowTitle(f"PDF Note Reader{dirty_marker}")
            return
        name = self.pdf_path.name if self.pdf_path else "PDF"
        self.setWindowTitle(f"{name} - Page {self.page_index + 1}/{len(self.doc)} - {self.zoom:.0%}{dirty_marker}")

    def confirm_active_unsaved_changes(self, action_text: str) -> bool:
        if self.active_session_index is not None:
            return self.confirm_unsaved_session(self.active_session_index, action_text)
        return self.confirm_unsaved_changes(action_text)

    def confirm_unsaved_changes(self, action_text: str) -> bool:
        if self.doc is None or not self.is_dirty:
            return True

        choice = self.ask_unsaved_document_action(action_text)
        if choice == "cancel":
            return False
        if choice == "discard":
            return True
        if choice == "save-incremental":
            return self.save_incremental(confirm=False)
        if choice == "save-full":
            return self.save(confirm=True)
        return False

    def ask_unsaved_document_action(self, action_text: str) -> str:
        document_name = self.pdf_path.name if self.pdf_path else "Untitled PDF"
        document_path = str(self.pdf_path) if self.pdf_path else "(no current file path)"
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setWindowTitle("Unsaved Changes")
        message_box.setText(f"This document has unsaved changes before you {action_text}:")
        message_box.setInformativeText(f"{document_name}\n\n{document_path}")
        save_incremental_button = message_box.addButton(
            "Save Incremental",
            QMessageBox.ButtonRole.AcceptRole,
        )
        save_full_button = message_box.addButton(
            "Save Full Rewrite",
            QMessageBox.ButtonRole.AcceptRole,
        )
        discard_button = message_box.addButton(
            "Discard",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = message_box.addButton(
            "Cancel",
            QMessageBox.ButtonRole.RejectRole,
        )
        message_box.setDefaultButton(save_incremental_button)
        message_box.exec()
        clicked_button = message_box.clickedButton()
        if clicked_button == save_incremental_button:
            return "save-incremental"
        if clicked_button == save_full_button:
            return "save-full"
        if clicked_button == discard_button:
            return "discard"
        if clicked_button == cancel_button:
            return "cancel"
        return "cancel"

    def confirm_unsaved_session(self, index: int, action_text: str) -> bool:
        if index < 0 or index >= len(self.sessions):
            return True

        self.save_active_session_state()
        session = self.sessions[index]
        if not session.is_dirty:
            return True

        if self.active_session_index != index:
            self.set_active_session(index, preserve_selection=True)

        return self.confirm_unsaved_changes(action_text)

    def confirm_all_unsaved_for_exit(self) -> bool:
        self.save_active_session_state()
        index = 0
        while index < len(self.sessions):
            if self.sessions[index].is_dirty:
                if not self.confirm_unsaved_session(index, "exit"):
                    return False
                self.save_active_session_state()
            index += 1
        return True

    def go_to_page(self, page_number: int) -> None:
        if self.updating_page_spin or self.doc is None:
            return

        target_index = max(0, min(page_number - 1, len(self.doc) - 1))
        if target_index == self.page_index:
            return

        self.page_index = target_index
        self.cancel_add_tool()
        self.render_page()
        self.update_current_recent_page()
        self.save_active_session_state()

    def current_page(self) -> fitz.Page:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        return self.doc[self.page_index]

    def settings_path(self) -> Path:
        return settings_path(__file__)

    def index_path(self) -> Path:
        return Path(__file__).with_name("PDFReaderIndex.sqlite3")

    def load_app_settings(self) -> None:
        settings = load_settings(self.settings_path(), self.max_recent_files)
        self.use_foxit_freetext = settings.use_foxit_freetext
        self.freetext_font_size_min = settings.freetext_font_size_min
        self.freetext_font_size_max = settings.freetext_font_size_max
        self.default_freetext_font_size = settings.default_freetext_font_size
        self.default_highlight_color = settings.default_highlight_color
        self.default_highlight_opacity = settings.default_highlight_opacity
        self.extract_highlight_text_on_reindex = settings.extract_highlight_text_on_reindex
        self.search_page_size = settings.search_page_size
        self.recent_files = settings.recent_files

    def save_app_settings(self) -> None:
        settings = AppSettings(
            use_foxit_freetext=self.use_foxit_freetext,
            freetext_font_size_min=self.freetext_font_size_min,
            freetext_font_size_max=self.freetext_font_size_max,
            default_freetext_font_size=self.default_freetext_font_size,
            default_highlight_color=self.default_highlight_color,
            default_highlight_opacity=self.default_highlight_opacity,
            extract_highlight_text_on_reindex=self.extract_highlight_text_on_reindex,
            search_page_size=self.search_page_size,
            recent_files=self.recent_files,
        )
        save_settings(self.settings_path(), settings)

    def clamp_freetext_font_size(self, value: int) -> int:
        return max(self.freetext_font_size_min, min(self.freetext_font_size_max, int(value)))

    def recent_file_index(self, path: Path) -> int | None:
        key = str(path).lower()
        for index, record in enumerate(self.recent_files):
            if str(record.get("path", "")).lower() == key:
                return index
        return None

    def recent_page_index(self, path: Path) -> int:
        index = self.recent_file_index(path)
        if index is None:
            return 0
        try:
            return max(0, int(self.recent_files[index].get("last_page_index", 0)))
        except (TypeError, ValueError):
            return 0

    def update_recent_file(self, path: Path, page_index: int | None = None) -> None:
        index = self.recent_file_index(path)
        record = self.recent_files.pop(index) if index is not None else {"path": str(path)}
        record["path"] = str(path)
        record["last_page_index"] = max(0, int(0 if page_index is None else page_index))
        record["last_opened_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.recent_files.insert(0, record)
        self.recent_files = self.recent_files[: self.max_recent_files]
        self.save_app_settings()
        self.refresh_recent_files_menu()

    def update_current_recent_page(self) -> None:
        if self.doc is None or self.pdf_path is None:
            return
        index = self.recent_file_index(self.pdf_path)
        if index is None:
            self.update_recent_file(self.pdf_path, self.page_index)
            return
        self.recent_files[index]["last_page_index"] = self.page_index
        self.save_app_settings()
        self.refresh_recent_files_menu()

    def refresh_recent_files_menu(self) -> None:
        if self.open_recent_menu is None:
            return

        self.open_recent_menu.clear()
        if not self.recent_files:
            empty_action = self.open_recent_menu.addAction("(No Recent Files)")
            empty_action.setEnabled(False)
            return

        for record in self.recent_files:
            path = Path(str(record["path"]))
            page_number = int(record.get("last_page_index", 0)) + 1
            action = self.open_recent_menu.addAction(f"{path.name} - page {page_number}")
            action.setToolTip(str(path))
            action.triggered.connect(lambda checked=False, recent_path=path: self.open_recent_pdf(recent_path))

        self.open_recent_menu.addSeparator()
        clear_action = self.open_recent_menu.addAction("Clear Recent Files")
        clear_action.triggered.connect(self.clear_recent_files)

    def clear_recent_files(self) -> None:
        self.recent_files = []
        self.save_app_settings()
        self.refresh_recent_files_menu()

    def open_recent_pdf(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "Open Recent", f"File not found:\n{path}")
            index = self.recent_file_index(path)
            if index is not None:
                self.recent_files.pop(index)
                self.save_app_settings()
                self.refresh_recent_files_menu()
            return
        self.open_pdf_path(path, self.recent_page_index(path))

    def render_page(self, preserve_selection: bool = False, keep_view_position: bool = False) -> None:
        if self.doc is None:
            return

        horizontal_value = self.view.horizontalScrollBar().value()
        vertical_value = self.view.verticalScrollBar().value()
        self.text_lines_cache_page_index = None
        self.text_lines_cache = None
        selected_annotation_id = self.selected_annotation_id if preserve_selection else None
        page = self.current_page()
        self.current_annotations = self.load_page_annotations(self.page_index)
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.page_item.setPixmap(QPixmap.fromImage(image))
        self.scene.setSceneRect(self.page_item.boundingRect())
        self.render_annotation_overlay()
        if keep_view_position:
            self.view.horizontalScrollBar().setValue(horizontal_value)
            self.view.verticalScrollBar().setValue(vertical_value)
        else:
            self.view.centerOn(self.page_item)

        supported_count = sum(1 for annot in self.current_annotations if annot.is_supported)
        unsupported_count = len(self.current_annotations) - supported_count
        self.update_window_title()
        self.statusBar().showMessage(
            f"Page {self.page_index + 1}/{len(self.doc)} | "
            f"Annotations: {len(self.current_annotations)} | "
            f"Supported: {supported_count} | Unsupported: {unsupported_count}"
        )
        self.refresh_annotations_table()
        self.sync_page_spin()
        self.update_actions()
        if hasattr(self.view, "reset_boundary_turn_state"):
            self.view.reset_boundary_turn_state(clear_status=False)
        self.clear_scroll_boundary_status()
        if selected_annotation_id in self.annotation_model_map:
            self.select_annotation(selected_annotation_id)
        self.save_active_session_state()

    def show_scroll_boundary_status(self, direction: str) -> None:
        if direction == "up":
            self.scroll_boundary_label.setText("已到顶")
            self.scroll_boundary_label.setStyleSheet(
                "QLabel { color: white; background: rgb(30, 120, 190); padding: 2px 8px; font-weight: 700; }"
            )
        elif direction == "down":
            self.scroll_boundary_label.setText("已到底")
            self.scroll_boundary_label.setStyleSheet(
                "QLabel { color: white; background: rgb(190, 80, 0); padding: 2px 8px; font-weight: 700; }"
            )
        else:
            self.scroll_boundary_label.setText("")
        self.scroll_boundary_label.setVisible(bool(self.scroll_boundary_label.text()))

    def clear_scroll_boundary_status(self) -> None:
        self.scroll_boundary_label.clear()
        self.scroll_boundary_label.setVisible(False)

    def render_annotation_overlay(self) -> None:
        self.clear_annotation_items()
        self.annotation_model_map = {model.id: model for model in self.current_annotations}
        self.selected_annotation_id = None
        renderer = AnnotationItemRenderer(
            self.scene,
            self.zoom,
            self.default_freetext_font_size,
            self.default_highlight_opacity,
            self.add_annotation_item,
            self.add_hit_item,
        )
        for model in self.current_annotations:
            if not model.is_supported:
                continue
            renderer.render(model)

    def clear_annotation_items(self) -> None:
        self.clear_selection_items()
        for item in self.annotation_items:
            self.scene.removeItem(item)
        self.annotation_items.clear()
        self.annotation_item_map.clear()
        self.annotation_model_map.clear()
        self.selected_annotation_id = None

    def add_annotation_item(self, item: QGraphicsItem, model: AnnotationModel) -> None:
        item.setZValue(10)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if self.is_draggable_model(model):
            item.setCursor(Qt.CursorShape.SizeAllCursor)
        item.setData(0, model.id)
        item.setData(1, item.pos())
        self.annotation_items.append(item)
        self.annotation_item_map.setdefault(model.id, []).append(item)

    def add_hit_item(self, item: QGraphicsItem, model: AnnotationModel) -> None:
        item.setZValue(11)
        item.setOpacity(0.01)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if self.is_draggable_model(model):
            item.setCursor(Qt.CursorShape.SizeAllCursor)
        item.setData(0, model.id)
        item.setData(1, item.pos())
        self.annotation_items.append(item)
        self.annotation_item_map.setdefault(model.id, []).append(item)

    def is_draggable_model(self, model: AnnotationModel) -> bool:
        return model.app_type in DRAGGABLE_APP_TYPES

    def scene_rect(self, rect: fitz.Rect) -> QRectF:
        return QRectF(
            rect.x0 * self.zoom,
            rect.y0 * self.zoom,
            rect.width * self.zoom,
            rect.height * self.zoom,
        )

    def scene_point(self, point: tuple[float, float]) -> QPointF:
        return QPointF(point[0] * self.zoom, point[1] * self.zoom)

    def pdf_color(self, color: tuple | None, fallback: QColor) -> QColor:
        if not color:
            return QColor(fallback)
        channels = [max(0, min(255, int(value * 255))) for value in color[:3]]
        if len(channels) < 3:
            return QColor(fallback)
        return QColor(channels[0], channels[1], channels[2])

    def arrow_points(self, model: AnnotationModel) -> tuple[QPointF, QPointF]:
        if model.line_start and model.line_end:
            return self.scene_point(model.line_start), self.scene_point(model.line_end)

        rect = model.rect
        return (
            QPointF(rect.x0 * self.zoom, rect.y0 * self.zoom),
            QPointF(rect.x1 * self.zoom, rect.y1 * self.zoom),
        )

    def highlight_polygons(self, model: AnnotationModel) -> list[QPolygonF]:
        polygons: list[QPolygonF] = []
        points = model.quad_points
        for index in range(0, len(points) - 3, 4):
            quad = points[index : index + 4]
            ordered_quad = (quad[0], quad[1], quad[3], quad[2])
            polygons.append(QPolygonF([self.scene_point(point) for point in ordered_quad]))
        return polygons

    def add_arrow_head_lines(
        self, start: QPointF, end: QPointF, size: float, pen: QPen, model: AnnotationModel
    ) -> None:
        left, right = self.arrow_head_points(start, end, size)
        for point in (left, right):
            item = self.scene.addLine(QLineF(end, point), pen)
            self.add_annotation_item(item, model)

    def arrow_head_points(self, start: QPointF, end: QPointF, size: float) -> tuple[QPointF, QPointF]:
        angle = atan2(end.y() - start.y(), end.x() - start.x())
        half_angle = pi / 12
        left = QPointF(
            end.x() - size * cos(angle - half_angle),
            end.y() - size * sin(angle - half_angle),
        )
        right = QPointF(
            end.x() - size * cos(angle + half_angle),
            end.y() - size * sin(angle + half_angle),
        )
        return left, right

    def arrow_head_flags(self, line_ending: str) -> tuple[bool, bool]:
        endings = re.findall(r"/?([A-Za-z]*Arrow|None)", line_ending)
        if len(endings) >= 2:
            return "Arrow" in endings[0], "Arrow" in endings[1]
        if "Arrow" in line_ending:
            return False, True
        return False, True

    def on_scene_selection_changed(self) -> None:
        if self.updating_scene_selection:
            return

        selected_ids = [item.data(0) for item in self.scene.selectedItems() if item.data(0)]
        if not selected_ids:
            if self.selected_annotation_id is not None:
                self.select_annotation(None)
            return

        annotation_id = str(selected_ids[0])
        if annotation_id != self.selected_annotation_id:
            self.select_annotation(annotation_id)

    def on_scene_mouse_press(self, scene_pos: QPointF) -> None:
        self.active_scene_drag_kind = None
        self.active_scene_drag_annotation_id = None

        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None:
            return

        annotation_id, item_role = hit
        if annotation_id is None:
            return

        model = self.annotation_model_map.get(annotation_id)
        if model is None or not model.is_supported:
            return

        if annotation_id != self.selected_annotation_id:
            self.select_annotation(annotation_id)
        self.prepare_annotation_drag(annotation_id)
        self.active_scene_drag_annotation_id = annotation_id
        self.active_scene_drag_start_pos = scene_pos
        if item_role == "resize-handle":
            self.active_scene_drag_kind = "resize"
        elif item_role == "arrow-endpoint-handle":
            self.active_scene_drag_kind = "arrow-endpoint"
        elif self.is_draggable_model(model):
            self.active_scene_drag_kind = "move"

    def annotation_hit_at_scene_pos(self, scene_pos: QPointF) -> tuple[str, str | None] | None:
        for item in self.scene.items(scene_pos):
            annotation_id = item.data(0)
            if not annotation_id:
                continue
            item_role = item.data(2)
            if item_role in {"selection-rect"}:
                continue
            return str(annotation_id), str(item_role) if item_role else None
        return None

    def prepare_annotation_drag(self, annotation_id: str) -> None:
        for item in self.annotation_item_map.get(annotation_id, []):
            item.setData(1, item.pos())
        for item in self.selection_items:
            if item.data(3) == annotation_id and item.data(2) in {"resize-handle", "arrow-endpoint-handle"}:
                item.setData(5, item.pos())

    def restore_annotation_drag_preview(self, annotation_id: str) -> None:
        for item in self.annotation_item_map.get(annotation_id, []):
            start_pos = item.data(1)
            if isinstance(start_pos, QPointF) and item.pos() != start_pos:
                item.setPos(start_pos)

        for item in self.selection_items:
            if item.data(3) != annotation_id:
                continue
            if item.data(2) == "selection-rect" and item.pos() != QPointF(0, 0):
                item.setPos(QPointF(0, 0))

    def is_intentional_annotation_move(self, delta: QPointF) -> bool:
        return abs(delta.x()) >= 3.0 or abs(delta.y()) >= 3.0

    def on_scene_mouse_release(self, scene_pos: QPointF | None = None) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None:
            return

        interaction = None
        if (
            self.active_scene_drag_kind == "move"
            and scene_pos is not None
            and self.active_scene_drag_start_pos is not None
        ):
            delta = scene_pos - self.active_scene_drag_start_pos
            controller = AnnotationInteractionController(self.zoom)
            if self.is_intentional_annotation_move(delta) and not controller.is_small_delta(delta):
                interaction = controller.interaction_from_delta("move", delta)
            else:
                self.restore_annotation_drag_preview(model.id)
        else:
            interaction = AnnotationInteractionController(self.zoom).interaction_for_mouse_release(
                model,
                self.selection_items,
                self.annotation_item_map,
                preferred_kind=self.active_scene_drag_kind,
            )
        self.active_scene_drag_kind = None
        self.active_scene_drag_annotation_id = None
        self.active_scene_drag_start_pos = None
        if interaction is None:
            return

        if interaction.kind == "resize":
            try:
                self.record_geometry_undo(f"Resize {model.pdf_type}", model)
                self.resize_rect_annotation(model, interaction.handle, interaction.dx_pdf, interaction.dy_pdf)
                self.mark_dirty()
                self.render_page(preserve_selection=True, keep_view_position=True)
                self.statusBar().showMessage(f"Resized {model.pdf_type} xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.render_page(preserve_selection=True, keep_view_position=True)
                self.show_error("Resize annotation failed", exc)
            return

        if interaction.kind == "arrow-endpoint":
            try:
                self.record_geometry_undo("Move Arrow endpoint", model)
                self.move_arrow_endpoint(model, interaction.handle, interaction.dx_pdf, interaction.dy_pdf)
                self.mark_dirty()
                self.render_page(preserve_selection=True, keep_view_position=True)
                self.statusBar().showMessage(f"Moved Arrow endpoint xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.render_page(preserve_selection=True, keep_view_position=True)
                self.show_error("Move arrow endpoint failed", exc)
            return

        if interaction.kind == "move":
            try:
                self.record_geometry_undo(f"Move {model.pdf_type}", model)
                self.move_pdf_annotation(model, interaction.dx_pdf, interaction.dy_pdf)
                self.mark_dirty()
                self.render_page(preserve_selection=True, keep_view_position=True)
                self.statusBar().showMessage(f"Moved {model.pdf_type} xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.render_page(preserve_selection=True, keep_view_position=True)
                self.show_error("Move annotation failed", exc)

    def record_geometry_undo(self, label: str, model: AnnotationModel) -> None:
        self.undo_action = UndoAction(
            label=label,
            page_index=model.page_index,
            xref=model.xref,
            app_type=model.app_type,
            operation="geometry",
            rect=fitz.Rect(model.rect) if model.rect is not None else None,
            line_start=model.line_start,
            line_end=model.line_end,
        )
        self.update_actions()

    def record_add_undo(self, label: str, page_index: int, xref: int) -> None:
        self.undo_action = UndoAction(
            label=label,
            operation="add",
            page_index=page_index,
            xref=xref,
            app_type="",
        )
        self.update_actions()

    def record_delete_undo(self, model: AnnotationModel) -> None:
        self.undo_action = UndoAction(
            label=f"Delete {model.pdf_type}",
            operation="delete",
            page_index=model.page_index,
            xref=model.xref,
            app_type=model.app_type,
            rect=fitz.Rect(model.rect),
            text=model.text,
            color=model.color,
            border_width=model.border_width,
            font_size=model.font_size,
            opacity=model.opacity,
            quad_points=list(model.quad_points),
            line_start=model.line_start,
            line_end=model.line_end,
            line_ending=model.line_ending,
        )
        self.update_actions()

    def undo_last_action(self) -> None:
        if self.doc is None or self.undo_action is None:
            return

        action = self.undo_action
        try:
            restored_xref = self.restore_undo_action(action)
            self.undo_action = None
            self.page_index = max(0, min(action.page_index, len(self.doc) - 1))
            self.mark_dirty()
            self.render_page(preserve_selection=True, keep_view_position=True)
            if restored_xref is not None:
                self.select_annotation_by_xref(restored_xref)
            self.statusBar().showMessage(f"Undid {action.label} xref={action.xref}. Use Save to persist.")
            self.update_actions()
        except Exception as exc:
            self.show_error("Undo failed", exc)

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

        if action.app_type == "arrow":
            if action.line_start is None or action.line_end is None:
                raise RuntimeError("The arrow undo action has no saved endpoints.")
            self.set_line_annotation_points(page, annot, action.line_start, action.line_end)
            return action.xref

        if action.rect is None:
            raise RuntimeError("The undo action has no saved rectangle.")
        if action.app_type == "square":
            model = self.annotation_repo.annotation_to_model(action.page_index, annot) if self.annotation_repo else None
            if model is None:
                annot.set_rect(action.rect)
                annot.update()
            else:
                self.set_square_rect(annot, model, action.rect)
            return action.xref
        if action.app_type == "freetext":
            model = self.annotation_repo.annotation_to_model(action.page_index, annot) if self.annotation_repo else None
            if model is None:
                annot.set_rect(action.rect)
                annot.update()
            else:
                self.set_freetext_rect(annot, model, action.rect)
            return action.xref
        annot.set_rect(action.rect)
        annot.update()
        return action.xref

    def undo_added_annotation(self, action: UndoAction) -> None:
        page = self.doc[action.page_index]
        annot = self.find_page_annotation_by_xref(page, action.xref)
        if annot is not None:
            page.delete_annot(annot)

    def restore_deleted_annotation(self, action: UndoAction) -> int:
        page = self.doc[action.page_index]
        if action.app_type == "freetext":
            annot = page.add_freetext_annot(
                action.rect,
                action.text,
                fontsize=action.font_size or self.default_freetext_font_size,
                fontname="helv",
                text_color=action.color or (1, 0, 0),
                fill_color=None,
                border_color=None,
            )
            annot.set_info(title="PDF Note Reader", content=action.text)
            annot.update()
            return annot.xref
        if action.app_type == "square":
            annot = page.add_rect_annot(action.rect)
            annot.set_colors(stroke=action.color or (1, 0, 0))
            annot.set_border(width=action.border_width or 2)
            annot.set_info(title="PDF Note Reader", content=action.text or "Rectangle annotation")
            annot.update()
            return annot.xref
        if action.app_type == "arrow":
            if action.line_start is None or action.line_end is None:
                raise RuntimeError("The deleted arrow has no saved endpoints.")
            annot = page.add_line_annot(action.line_start, action.line_end)
            annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
            annot.set_colors(stroke=action.color or (1, 0, 0))
            annot.set_border(width=action.border_width or 2)
            annot.set_info(title="PDF Note Reader", content=action.text or "Arrow annotation")
            annot.update()
            return annot.xref
        if action.app_type == "highlight":
            rects = self.highlight_rects_from_quad_points(action.quad_points or [])
            if not rects and action.rect is not None:
                rects = [action.rect]
            annot = page.add_highlight_annot(rects)
            annot.set_colors(stroke=action.color or self.default_highlight_color)
            annot.set_info(title="PDF Note Reader", content=action.text or "Highlight annotation")
            annot.update(opacity=action.opacity if action.opacity is not None else self.default_highlight_opacity)
            return annot.xref
        raise RuntimeError(f"Undo delete is not supported for annotation type: {action.app_type}")

    def highlight_rects_from_quad_points(self, quad_points: list[tuple[float, float]]) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []
        for index in range(0, len(quad_points) - 3, 4):
            quad = quad_points[index : index + 4]
            xs = [point[0] for point in quad]
            ys = [point[1] for point in quad]
            rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
        return rects

    def on_scene_mouse_move(self, scene_pos: QPointF | None = None) -> None:
        if self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None:
            return

        if self.active_scene_drag_kind == "move":
            if scene_pos is not None and self.active_scene_drag_start_pos is not None:
                delta = scene_pos - self.active_scene_drag_start_pos
                if not self.is_intentional_annotation_move(delta):
                    self.restore_annotation_drag_preview(model.id)
                    return
            self.update_annotation_move_preview(model, scene_pos)
        if model.app_type not in {"square", "freetext"}:
            return

        if self.active_scene_drag_kind not in {None, "resize"}:
            return

        interaction = AnnotationInteractionController(self.zoom).rect_resize(model, self.selection_items)
        if interaction is None:
            return

        preview_rect = self.resized_scene_rect_preview(
            model,
            interaction.handle,
            interaction.dx_pdf,
            interaction.dy_pdf,
        )
        for item in self.selection_items:
            if item.data(2) == "selection-rect" and item.data(3) == model.id and isinstance(item, QGraphicsRectItem):
                item.setRect(preview_rect)

    def update_annotation_move_preview(self, model: AnnotationModel, scene_pos: QPointF | None = None) -> None:
        if not self.is_draggable_model(model):
            return

        controller = AnnotationInteractionController(self.zoom)
        if controller.rect_resize(model, self.selection_items) is not None:
            return
        if controller.arrow_endpoint_move(model, self.selection_items) is not None:
            return

        if scene_pos is not None and self.active_scene_drag_start_pos is not None:
            delta = scene_pos - self.active_scene_drag_start_pos
        else:
            delta = controller.annotation_drag_delta(model.id, self.annotation_item_map)
        if delta is None:
            return

        for item in self.annotation_item_map.get(model.id, []):
            start_pos = item.data(1)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            target_pos = start_pos + delta
            if item.pos() != target_pos:
                item.setPos(target_pos)

        for item in self.selection_items:
            if item.data(3) != model.id:
                continue
            if item.data(2) != "selection-rect":
                continue
            if item.pos() != delta:
                item.setPos(delta)

    def resized_scene_rect_preview(
        self,
        model: AnnotationModel,
        handle: str,
        dx_pdf: float,
        dy_pdf: float,
    ) -> QRectF:
        min_width = 20.0 if model.app_type == "freetext" else 10.0
        min_height = 12.0 if model.app_type == "freetext" else 10.0
        rect = fitz.Rect(model.rect)
        if handle == "top-left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "top-right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "bottom-right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "bottom-left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "top":
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
        elif handle == "bottom":
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
        return self.scene_rect(rect)

    def on_scene_mouse_double_click(self) -> None:
        if self.selected_annotation_id is not None:
            self.show_annotation_properties()

    def move_pdf_annotation(self, model: AnnotationModel, dx: float, dy: float) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        if model.app_type == "freetext":
            new_rect = fitz.Rect(
                model.rect.x0 + dx,
                model.rect.y0 + dy,
                model.rect.x1 + dx,
                model.rect.y1 + dy,
            )
            annot.set_rect(new_rect)
            annot.update()
            return

        if model.app_type == "square":
            new_rect = fitz.Rect(
                model.rect.x0 + dx,
                model.rect.y0 + dy,
                model.rect.x1 + dx,
                model.rect.y1 + dy,
            )
            self.set_square_rect(annot, model, new_rect)
            return

        if model.app_type == "arrow":
            if model.line_start is None or model.line_end is None:
                raise RuntimeError("The selected arrow annotation has no line endpoints.")
            start = (model.line_start[0] + dx, model.line_start[1] + dy)
            end = (model.line_end[0] + dx, model.line_end[1] + dy)
            self.set_line_annotation_points(page, annot, start, end)
            return

        raise RuntimeError(f"Annotation type cannot be moved: {model.app_type}")

    def resize_rect_annotation(self, model: AnnotationModel, corner: str, dx: float, dy: float) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        min_width = 20.0 if model.app_type == "freetext" else 10.0
        min_height = 12.0 if model.app_type == "freetext" else 10.0
        rect = fitz.Rect(model.rect)
        if corner == "top-left":
            rect.x0 = min(rect.x0 + dx, rect.x1 - min_width)
            rect.y0 = min(rect.y0 + dy, rect.y1 - min_height)
        elif corner == "top-right":
            rect.x1 = max(rect.x1 + dx, rect.x0 + min_width)
            rect.y0 = min(rect.y0 + dy, rect.y1 - min_height)
        elif corner == "bottom-right":
            rect.x1 = max(rect.x1 + dx, rect.x0 + min_width)
            rect.y1 = max(rect.y1 + dy, rect.y0 + min_height)
        elif corner == "bottom-left":
            rect.x0 = min(rect.x0 + dx, rect.x1 - min_width)
            rect.y1 = max(rect.y1 + dy, rect.y0 + min_height)
        elif corner == "top":
            rect.y0 = min(rect.y0 + dy, rect.y1 - min_height)
        elif corner == "right":
            rect.x1 = max(rect.x1 + dx, rect.x0 + min_width)
        elif corner == "bottom":
            rect.y1 = max(rect.y1 + dy, rect.y0 + min_height)
        elif corner == "left":
            rect.x0 = min(rect.x0 + dx, rect.x1 - min_width)
        else:
            raise RuntimeError(f"Unknown resize handle: {corner}")

        if model.app_type == "square":
            self.set_square_rect(annot, model, rect)
        elif model.app_type == "freetext":
            self.set_freetext_rect(annot, model, rect)
        else:
            raise RuntimeError(f"Annotation type cannot be resized: {model.app_type}")

    def set_square_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        inset = max(0.0, (model.border_width or 0.0) / 2)
        if rect.width <= inset * 2 or rect.height <= inset * 2:
            annot.set_rect(rect)
        else:
            annot.set_rect(fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset))
        annot.update()

    def set_freetext_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        annot.set_rect(rect)
        annot.update(
            fontsize=model.font_size or self.default_freetext_font_size,
            fontname="helv",
            text_color=model.color or (1, 0, 0),
            fill_color=None,
            border_color=None,
        )

    def set_line_annotation_points(
        self,
        page: fitz.Page,
        annot: fitz.Annot,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> None:
        page_height = page.rect.height
        raw = f"[ {start[0]:.4f} {page_height - start[1]:.4f} {end[0]:.4f} {page_height - end[1]:.4f} ]"
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        self.doc.xref_set_key(annot.xref, "L", raw)
        annot.update()

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
        if annotation_id is not None and annotation_id not in self.annotation_model_map:
            return

        self.selected_annotation_id = annotation_id
        if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
            self.sessions[self.active_session_index].selected_annotation_id = annotation_id
        self.clear_selection_items()
        self.sync_scene_selection()
        self.sync_table_selection()

        if annotation_id is None:
            self.show_page_status()
            self.update_actions()
            if not self.applying_property_change:
                self.refresh_properties_panel()
            return

        model = self.annotation_model_map[annotation_id]
        if model.is_supported:
            self.draw_selection_for_model(model)
            if center_on:
                self.center_on_annotation(model)

        summary = model.text.strip().replace("\n", " ")
        if len(summary) > 40:
            summary = summary[:40] + "..."
        hint = "Drag to move. Use Save to persist." if self.is_draggable_model(model) else "Highlight cannot be moved."
        if summary:
            self.statusBar().showMessage(f"Selected: {model.pdf_type} xref={model.xref} | {summary} | {hint}")
        else:
            self.statusBar().showMessage(f"Selected: {model.pdf_type} xref={model.xref} | {hint}")
        self.update_actions()
        if not self.applying_property_change:
            self.refresh_properties_panel()

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
        if self.updating_table_selection or self.annotations_table is None:
            return

        row = self.annotations_table.selected_row()
        if row is None:
            self.select_annotation(None)
            return

        if row < 0 or row >= len(self.current_annotations):
            self.select_annotation(None)
            return

        model = self.current_annotations[row]
        if not model.is_supported:
            self.select_annotation(None)
            self.statusBar().showMessage(f"Unsupported: {model.pdf_type} xref={model.xref}")
            return

        self.select_annotation(model.id, center_on=True)

    def delete_selected_annotation(self) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or not model.is_supported:
            return

        summary = model.text.strip().replace("\n", " ")
        if len(summary) > 60:
            summary = summary[:60] + "..."
        message = f"Delete selected annotation?\n\n{model.pdf_type} xref={model.xref}"
        if summary:
            message += f"\n{summary}"

        reply = QMessageBox.question(
            self,
            "Delete Annotation",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            page = self.current_page()
            annot = self.find_page_annotation_by_xref(page, model.xref)
            if annot is None:
                QMessageBox.warning(self, "Delete Annotation", "The selected annotation was not found on this page.")
                self.render_page(keep_view_position=True)
                return

            self.record_delete_undo(model)
            page.delete_annot(annot)
            self.mark_dirty()
            self.selected_annotation_id = None
            self.render_page(keep_view_position=True)
            self.statusBar().showMessage(f"Deleted annotation xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            self.show_error("Delete annotation failed", exc)

    def show_annotation_context_menu(self, scene_pos: QPointF, screen_pos) -> None:
        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None:
            return

        annotation_id, _item_role = hit
        model = self.annotation_model_map.get(annotation_id)
        if model is None or not model.is_supported:
            return

        self.select_annotation(annotation_id)
        menu = QMenu(self)
        delete_action = menu.addAction("Delete")
        selected_action = menu.exec(screen_pos)
        if selected_action == delete_action:
            self.delete_selected_annotation()

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        if self.annotation_repo is None:
            return None
        return self.annotation_repo.find_page_annotation_by_xref(page, xref)

    def edit_selected_annotation(self) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or model.app_type not in EDITABLE_APP_TYPES:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {model.pdf_type} Annotation")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        text_edit: QPlainTextEdit | None = None
        font_size_spin: QSpinBox | None = None
        color_combo: QComboBox | None = None
        width_spin: QSpinBox | None = None

        if model.app_type == "freetext":
            text_edit = QPlainTextEdit()
            text_edit.setPlainText(model.text)
            text_edit.setMinimumSize(360, 160)
            form.addRow("Text", text_edit)

            font_size_spin = QSpinBox()
            font_size_spin.setRange(self.freetext_font_size_min, self.freetext_font_size_max)
            font_size_spin.setValue(self.clamp_freetext_font_size(round(model.font_size or self.default_freetext_font_size)))
            form.addRow("Font size", font_size_spin)
        else:
            color_combo = QComboBox()
            color_combo.addItems(ANNOTATION_COLORS.keys())
            color_combo.setCurrentText(self.color_name_for_tuple(model.color))
            form.addRow("Stroke color", color_combo)

            width_spin = QSpinBox()
            width_spin.setRange(1, 10)
            width_spin.setValue(max(1, min(10, int(round(model.border_width or 1)))))
            label = "Border width" if model.app_type == "square" else "Line width"
            form.addRow(label, width_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            if model.app_type == "freetext":
                if text_edit is None or font_size_spin is None:
                    return
                text = text_edit.toPlainText().strip()
                if not text:
                    QMessageBox.warning(self, "Edit Annotation", "FreeText annotation text cannot be empty.")
                    return
                self.update_freetext_annotation(model, text, font_size_spin.value())
                status_type = "FreeText"
            else:
                if color_combo is None or width_spin is None:
                    return
                self.update_stroked_annotation(model, ANNOTATION_COLORS[color_combo.currentText()], width_spin.value())
                status_type = model.pdf_type
            self.mark_dirty()
            self.render_page(preserve_selection=True)
            self.statusBar().showMessage(f"Edited {status_type} xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            self.render_page(preserve_selection=True)
            self.show_error("Edit annotation failed", exc)

    def update_freetext_annotation(
        self, model: AnnotationModel, text: str, font_size: int, color: tuple[float, float, float] = (1, 0, 0)
    ) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        new_width, new_height = self.estimated_freetext_size(text, font_size)
        page_rect = self.current_page().rect
        x0 = max(page_rect.x0, min(model.rect.x0, page_rect.x1 - new_width))
        y0 = max(page_rect.y0, min(model.rect.y0, page_rect.y1 - new_height))
        annot.set_rect(fitz.Rect(x0, y0, x0 + new_width, y0 + new_height))
        annot.set_info(title="PDF Note Reader", content=text)
        annot.set_border(width=0)
        annot.update(fontsize=font_size, fontname="helv", text_color=color, fill_color=None, border_color=None)
        self.normalize_freetext_annotation(annot, font_size, color)

    def normalize_freetext_annotation(
        self, annot: fitz.Annot, font_size: int, color: tuple[float, float, float]
    ) -> None:
        if self.doc is None:
            return

        xref = annot.xref
        annot.set_border(width=0)
        annot.update(fontsize=font_size, fontname="helv", text_color=color, fill_color=None, border_color=None)

        self.doc.xref_set_key(xref, "DS", fitz.get_pdf_str(self.freetext_default_style(font_size, color)))
        self.doc.xref_set_key(xref, "Q", "0")
        self.doc.xref_set_key(xref, "BS", "<</Type/Border/W 0>>")
        self.remove_annotation_keys(xref, ("RC", "BE", "RD"))

    def freetext_default_style(self, font_size: int, color: tuple[float, float, float]) -> str:
        red, green, blue = (max(0, min(255, int(round(channel * 255)))) for channel in color[:3])
        return f"font: 'Helv' ,sans-serif {font_size:.2f}pt;color:#{red:02X}{green:02X}{blue:02X}"

    def update_highlight_annotation(
        self, model: AnnotationModel, color: tuple[float, float, float], opacity: float
    ) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_colors(stroke=color)
        annot.update(opacity=max(0.05, min(1.0, opacity)))

    def update_stroked_annotation(self, model: AnnotationModel, color: tuple[float, float, float], width: int) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.update()

    def color_name_for_tuple(self, color: tuple | None) -> str:
        if not color:
            return "Red"
        best_name = "Red"
        best_distance = float("inf")
        for name, candidate in ANNOTATION_COLORS.items():
            distance = sum((float(color[index]) - candidate[index]) ** 2 for index in range(3))
            if distance < best_distance:
                best_name = name
                best_distance = distance
        return best_name

    def draw_selection_for_model(self, model: AnnotationModel) -> None:
        renderer = AnnotationSelectionRenderer(self.scene, self.zoom)
        self.selection_items.extend(renderer.draw(model))

    def center_on_annotation(self, model: AnnotationModel) -> None:
        if model.app_type == "arrow":
            start, end = self.arrow_points(model)
            self.view.centerOn((start + end) / 2)
            return
        self.view.centerOn(self.scene_rect(model.rect).center())

    def show_page_status(self) -> None:
        if self.doc is None:
            self.statusBar().showMessage("No PDF open")
            return

        supported_count = sum(1 for annot in self.current_annotations if annot.is_supported)
        unsupported_count = len(self.current_annotations) - supported_count
        self.statusBar().showMessage(
            f"Page {self.page_index + 1}/{len(self.doc)} | "
            f"Annotations: {len(self.current_annotations)} | "
            f"Supported: {supported_count} | Unsupported: {unsupported_count}"
        )

    def load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        if self.annotation_repo is None:
            return []
        warning_start = len(self.annotation_repo.warnings)
        models = self.annotation_repo.load_page_annotations(page_index)
        warnings = [warning.format() for warning in self.annotation_repo.warnings[warning_start:]]
        self.log_annotation_read_warnings("Current page annotation read warnings", warnings)
        return models

    def log_annotation_read_warnings(self, title: str, warnings: list, max_details: int = 30) -> None:
        if not warnings:
            return
        self.log_debug(f"{title}: skipped/problematic={len(warnings)}")
        for warning in warnings[:max_details]:
            self.log_debug(f"  {warning}")
        if len(warnings) > max_details:
            self.log_debug(f"  ... {len(warnings) - max_details} more annotation read warnings")

    def show_current_page_annotations(self) -> None:
        if self.annotations_dock is None:
            self.annotations_dock = QDockWidget("Annotations", self)
            self.annotations_dock.setMinimumWidth(320)
            self.annotations_dock.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            self.annotations_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.annotations_dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetClosable
                | QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )

            self.annotations_tabs = QTabWidget()
            self.annotations_tabs.setMinimumWidth(300)
            self.annotations_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.annotations_tabs.setTabPosition(QTabWidget.TabPosition.East)

            self.annotations_table = AnnotationListWidget(self.rect_text, self.annotation_note)
            self.annotations_table.itemSelectionChanged.connect(self.on_annotations_table_selection_changed)

            self.properties_page = AnnotationPropertiesWidget(
                self.on_highlight_property_change,
                self.on_highlight_default_change,
                self.on_freetext_property_change,
                self.on_stroked_property_change,
            )

            self.annotations_tabs.addTab(self.annotations_table, "Annotation List")
            self.annotations_tabs.addTab(self.properties_page, "Properties")
            self.annotations_dock.setWidget(self.annotations_tabs)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.annotations_dock)
            self.resizeDocks([self.annotations_dock], [340], Qt.Orientation.Horizontal)

        self.show_dock(self.annotations_dock)
        self.refresh_annotations_table()
        self.refresh_properties_panel()

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
        if self.doc is None or self.pdf_path is None:
            return
        if self.reindex_thread is not None:
            return

        pdf_path = self.pdf_path
        self.reindex_pdf_path = pdf_path
        extract_highlight_text = self.should_extract_highlight_text_for_index()
        self.set_reindex_busy(True)
        self.log_debug(
            f"Reindex current PDF started: {pdf_path} "
            f"extract_highlight_text={extract_highlight_text}"
        )

        self.reindex_thread = QThread(self)
        self.reindex_worker = ReindexWorker(self.index_path(), pdf_path, extract_highlight_text)
        self.reindex_worker.moveToThread(self.reindex_thread)
        self.reindex_thread.started.connect(self.reindex_worker.run)
        self.reindex_worker.progress.connect(self.on_reindex_progress)
        self.reindex_worker.finished.connect(self.on_reindex_finished)
        self.reindex_worker.failed.connect(self.on_reindex_failed)
        self.reindex_worker.finished.connect(self.reindex_thread.quit)
        self.reindex_worker.failed.connect(self.reindex_thread.quit)
        self.reindex_worker.finished.connect(self.reindex_worker.deleteLater)
        self.reindex_worker.failed.connect(self.reindex_worker.deleteLater)
        self.reindex_thread.finished.connect(self.cleanup_reindex_thread)
        self.reindex_thread.start()

    def should_extract_highlight_text_for_index(self) -> bool:
        return self.extract_highlight_text_on_reindex

    def set_reindex_busy(self, busy: bool) -> None:
        self.reindex_current_pdf_action.setEnabled(not busy and self.doc is not None)
        self.clear_annotation_index_action.setEnabled(not busy)
        self.search_annotations_action.setEnabled(not busy)
        self.open_action.setEnabled(not busy)
        self.close_action.setEnabled(not busy and self.doc is not None)
        self.save_action.setEnabled(not busy and self.doc is not None)
        self.save_as_action.setEnabled(not busy and self.doc is not None)
        self.save_incremental_action.setEnabled(not busy and self.doc is not None)
        if self.annotation_search_widget is not None:
            self.annotation_search_widget.set_indexing_busy(busy)

        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self.statusBar().showMessage("Indexing current PDF, please wait...")
        else:
            QApplication.restoreOverrideCursor()
            self.update_actions()
        QApplication.processEvents()

    @Slot(int, int, int)
    def on_reindex_progress(self, page_number: int, page_count: int, annotation_count: int) -> None:
        message = f"Indexing page {page_number} / {page_count}, annotations {annotation_count}..."
        self.statusBar().showMessage(message)
        if self.annotation_search_widget is not None:
            self.annotation_search_widget.set_index_status(message, stale=True)

    @Slot(int, list)
    def on_reindex_finished(self, count: int, warnings: list) -> None:
        path = self.reindex_pdf_path
        self.log_debug(f"Reindex current PDF completed: {path} annotations={count}")
        self.log_annotation_read_warnings("Reindex annotation read warnings", warnings)
        self.set_reindex_busy(False)
        self.statusBar().showMessage(f"Indexed {count} annotations.")
        self.refresh_annotation_search_status()
        self.refresh_index_database_info()
        self.refresh_search_indexed_files()
        if self.annotation_search_widget is not None:
            self.annotation_search_widget.clear_results()

    @Slot(str)
    def on_reindex_failed(self, message: str) -> None:
        path = self.reindex_pdf_path
        self.log_debug(f"Reindex current PDF failed: {path}: {message}")
        self.set_reindex_busy(False)
        self.show_error("Reindex Current PDF failed", RuntimeError(message))

    def cleanup_reindex_thread(self) -> None:
        if self.reindex_thread is not None:
            self.reindex_thread.deleteLater()
        self.reindex_worker = None
        self.reindex_thread = None
        self.reindex_pdf_path = None
        self.update_actions()

    def clear_annotation_index(self) -> None:
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setWindowTitle("Clear Annotation Index")
        message_box.setText("Delete all annotation index records?")
        message_box.setInformativeText("This does not modify any PDF files.")
        confirm_button = message_box.addButton("Confirm Clear", QMessageBox.ButtonRole.AcceptRole)
        message_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        message_box.setDefaultButton(confirm_button)
        message_box.exec()
        if message_box.clickedButton() is not confirm_button:
            self.log_debug("Clear annotation index canceled by user")
            return

        try:
            self.annotation_index.clear_all()
            if self.annotation_search_widget is not None:
                self.annotation_search_widget.reset_search_state()
                self.annotation_search_widget.set_index_status("Annotation index is empty.", missing=True)
            self.refresh_search_indexed_files()
            self.refresh_index_database_info()
            self.log_debug("Clear annotation index completed")
            self.statusBar().showMessage("Annotation index cleared.")
            QMessageBox.information(self, "Clear Annotation Index", "Annotation index cleared.")
        except Exception as exc:
            self.log_debug(f"Clear annotation index failed: {exc}")
            self.show_error("Clear Annotation Index failed", exc)

    def show_index_database_info(self) -> None:
        if self.index_database_info_dock is None:
            self.index_database_info_dock = QDockWidget("Index Database Info", self)
            self.index_database_info_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
                | Qt.DockWidgetArea.BottomDockWidgetArea
            )
            self.index_database_info_text = QPlainTextEdit()
            self.index_database_info_text.setReadOnly(True)
            self.index_database_info_dock.setWidget(self.index_database_info_text)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.index_database_info_dock)

        self.refresh_index_database_info()
        self.show_dock(self.index_database_info_dock)

    def refresh_index_database_info(self) -> None:
        if self.index_database_info_text is None:
            return
        try:
            self.index_database_info_text.setPlainText(self.build_index_database_info_report())
        except Exception as exc:
            self.index_database_info_text.setPlainText(f"Index database info unavailable:\n{exc}")
            self.log_debug(f"Index database info unavailable: {exc}")

    def build_index_database_info_report(self) -> str:
        documents = self.annotation_index.database_info()
        total_annotations = sum(document.annotation_count for document in documents)
        lines = [
            "Index database",
            f"Path: {self.index_path()}",
            "",
            f"Indexed documents: {len(documents)}",
            f"Total indexed annotations: {total_annotations}",
        ]
        if not documents:
            lines.append("")
            lines.append("No indexed documents.")
            return "\n".join(lines)

        for index, document in enumerate(documents, start=1):
            status = "missing" if not document.exists else "stale" if document.is_stale else "current"
            lines.extend(
                [
                    "",
                    f"{index}. {document.file_name}",
                    f"Path: {document.path}",
                    f"Indexed at: {document.indexed_at}",
                    f"PDF modified time: {self.format_timestamp(document.modified_time)}",
                    f"File size: {self.format_file_size(document.file_size)}",
                    f"Indexed annotations: {document.annotation_count}",
                    f"Status: {status}",
                ]
            )
        return "\n".join(lines)

    def format_file_size(self, size: int | None) -> str:
        if size is None:
            return "unknown"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def format_timestamp(self, timestamp: float | None) -> str:
        if timestamp is None:
            return "unknown"
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def show_annotation_search(self) -> None:
        if self.annotation_search_dock is None:
            self.annotation_search_dock = QDockWidget("Search Annotations", self)
            self.annotation_search_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
                | Qt.DockWidgetArea.BottomDockWidgetArea
            )
            self.annotation_search_widget = AnnotationSearchWidget(self)
            self.annotation_search_widget.set_page_size(self.search_page_size)
            self.annotation_search_widget.search_requested.connect(self.search_annotations)
            self.annotation_search_widget.result_activated.connect(self.jump_to_search_result)
            self.annotation_search_widget.maximize_requested.connect(self.toggle_search_dock_maximized)
            self.annotation_search_dock.setWidget(self.annotation_search_widget)
            self.annotation_search_dock.topLevelChanged.connect(self.on_search_dock_top_level_changed)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.annotation_search_dock)

        self.show_dock(self.annotation_search_dock)
        self.update_search_dock_maximize_state()
        self.refresh_search_indexed_files()
        self.refresh_annotation_search_status()

    def refresh_search_indexed_files(self) -> None:
        if self.annotation_search_widget is None:
            return
        documents = self.annotation_index.database_info()
        files = [(document.path, document.file_name) for document in documents]
        self.annotation_search_widget.set_indexed_files(files)

    def search_annotations(self, keyword: str, app_type, document_paths=None) -> None:
        try:
            search_response = self.annotation_index.search_with_timing(
                keyword,
                app_type,
                document_paths=document_paths,
            )
            results = search_response.results
            ui_ms = 0.0
            if self.annotation_search_widget is not None:
                ui_ms = self.annotation_search_widget.set_results(results, self.search_page_size)
            total_ms = search_response.sqlite_ms + search_response.build_ms + ui_ms
            self.log_debug(
                f"Search annotations: keyword={keyword!r} app_type={app_type!r} results={len(results)} "
                f"documents={len(document_paths) if document_paths else 'all'} "
                f"page_size={self.search_page_size} "
                f"sqlite={search_response.sqlite_ms:.1f}ms "
                f"build={search_response.build_ms:.1f}ms "
                f"ui={ui_ms:.1f}ms total={total_ms:.1f}ms"
            )
            self.statusBar().showMessage(f"Found {len(results)} indexed annotations.")
        except Exception as exc:
            self.log_debug(f"Search annotations failed: {exc}")
            self.show_error("Search Annotations failed", exc)

    def refresh_annotation_search_status(self) -> None:
        if self.annotation_search_widget is None:
            return
        if self.pdf_path is None:
            self.annotation_search_widget.set_index_status("No PDF open.", missing=True)
            return

        try:
            status = self.annotation_index.document_status(self.pdf_path)
            self.annotation_search_widget.set_index_status(
                status.message,
                stale=status.is_stale,
                missing=not status.is_indexed,
            )
        except Exception as exc:
            self.annotation_search_widget.set_index_status(f"Index status unavailable: {exc}", missing=True)
            self.log_debug(f"Index status unavailable: {exc}")

    def on_search_dock_top_level_changed(self, floating: bool) -> None:
        if not floating:
            self.annotation_search_restore_geometry = None
            self.annotation_search_maximized = False
        self.update_search_dock_maximize_state()

    def update_search_dock_maximize_state(self) -> None:
        if self.annotation_search_dock is None or self.annotation_search_widget is None:
            return
        floating = self.annotation_search_dock.isFloating()
        maximized = bool(floating and self.annotation_search_maximized)
        self.annotation_search_widget.set_maximize_state(floating, maximized)

    def toggle_search_dock_maximized(self) -> None:
        if self.annotation_search_dock is None or self.annotation_search_widget is None:
            return
        if not self.annotation_search_dock.isFloating():
            self.update_search_dock_maximize_state()
            return

        if self.annotation_search_maximized:
            if self.annotation_search_restore_geometry is not None:
                self.annotation_search_dock.restoreGeometry(self.annotation_search_restore_geometry)
            self.annotation_search_dock.showNormal()
            self.annotation_search_maximized = False
        else:
            self.annotation_search_restore_geometry = self.annotation_search_dock.saveGeometry()
            self.annotation_search_dock.showMaximized()
            self.annotation_search_maximized = True
        self.update_search_dock_maximize_state()

    def jump_to_search_result(self, document_path: str, page_index: int, xref: int) -> None:
        target_path = Path(document_path)
        if not target_path.exists():
            message = f"Search result PDF was not found:\n{target_path}"
            QMessageBox.warning(self, "Search Annotations", message)
            self.log_debug(f"Search result jump failed: missing PDF {target_path}")
            return

        opened_new_tab = False
        existing_index = self.session_index_for_path(target_path)
        if existing_index is not None:
            self.set_active_session(existing_index, preserve_selection=False)
            self.log_debug(f"Search result switched to existing PDF tab: {target_path}")
        else:
            opened_new_tab = True
            if not self.open_pdf_path(target_path, page_index):
                self.log_debug(f"Search result jump failed while opening PDF: {target_path}")
                return
            self.log_debug(f"Search result opened PDF in new tab: {target_path}")

        if self.doc is None:
            return

        self.cancel_add_tool()
        self.page_index = max(0, min(page_index, len(self.doc) - 1))
        self.render_page()
        self.update_current_recent_page()
        selected = self.select_annotation_by_xref(xref)
        if selected:
            location = "new tab" if opened_new_tab else "existing tab"
            self.statusBar().showMessage(f"Jumped to search result on page {self.page_index + 1}.")
            self.log_debug(
                f"Search result jumped: {target_path} page={self.page_index + 1} xref={xref} {location}"
            )
            return

        message = (
            f"Search result page opened, but annotation xref={xref} was not found. "
            "The index may be stale. Reindex this PDF."
        )
        self.statusBar().showMessage(message)
        self.log_debug(f"Search result xref not found: {target_path} page={self.page_index + 1} xref={xref}")

    def log_debug(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.debug_log.append(f"{timestamp} {message}")
        if len(self.debug_log) > 1000:
            self.debug_log = self.debug_log[-1000:]
        self.refresh_debug_log()

    def show_debug_log(self) -> None:
        if self.debug_log_dock is None:
            self.debug_log_dock = QDockWidget("Debug Log", self)
            self.debug_log_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
                | Qt.DockWidgetArea.BottomDockWidgetArea
            )
            self.debug_log_text = QPlainTextEdit()
            self.debug_log_text.setReadOnly(True)
            self.debug_log_dock.setWidget(self.debug_log_text)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.debug_log_dock)

        self.refresh_debug_log()
        self.show_dock(self.debug_log_dock)

    def show_dock(self, dock: QDockWidget) -> None:
        dock.setVisible(True)
        dock.show()
        dock.raise_()
        widget = dock.widget()
        if widget is not None:
            widget.setFocus()

    def refresh_debug_log(self) -> None:
        if self.debug_log_text is None:
            return
        text = "\n".join(self.debug_log) if self.debug_log else "Debug log is empty."
        self.debug_log_text.setPlainText(text)
        scrollbar = self.debug_log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

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
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(text)
        layout.addWidget(text_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def refresh_properties_panel(self) -> None:
        if self.properties_page is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id) if self.selected_annotation_id else None
        self.properties_page.set_model(
            model,
            default_highlight_color=self.default_highlight_color,
            default_highlight_opacity=self.default_highlight_opacity,
            freetext_font_size_min=self.freetext_font_size_min,
            freetext_font_size_max=self.freetext_font_size_max,
            default_freetext_font_size=self.default_freetext_font_size,
        )

    def on_highlight_property_change(self, color: tuple[float, float, float], opacity: float) -> None:
        self.apply_property_change(
            lambda selected: self.update_highlight_annotation(selected, color, opacity),
            self.property_status_message("Edited Highlight"),
        )

    def on_highlight_default_change(self, color: tuple[float, float, float], opacity: float) -> None:
        self.default_highlight_color = color
        self.default_highlight_opacity = opacity
        self.save_app_settings()

    def on_freetext_property_change(self, text: str, font_size: int, color: tuple[float, float, float]) -> None:
        self.apply_property_change(
            lambda selected: self.update_freetext_annotation(selected, text, font_size, color),
            self.property_status_message("Edited FreeText"),
        )

    def on_stroked_property_change(self, color: tuple[float, float, float], width: int) -> None:
        model = self.annotation_model_map.get(self.selected_annotation_id) if self.selected_annotation_id else None
        label = f"Edited {model.pdf_type}" if model is not None else "Edited annotation"
        self.apply_property_change(
            lambda selected: self.update_stroked_annotation(selected, color, width),
            self.property_status_message(label),
        )

    def property_status_message(self, prefix: str) -> str:
        model = self.annotation_model_map.get(self.selected_annotation_id) if self.selected_annotation_id else None
        if model is None:
            return f"{prefix}. Use Save to persist."
        return f"{prefix} xref={model.xref}. Use Save to persist."

    def apply_property_change(self, callback, status: str) -> None:
        if self.applying_property_change:
            return
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or not model.is_supported:
            return

        try:
            self.applying_property_change = True
            callback(model)
            self.mark_dirty()
            self.render_page(preserve_selection=True)
            self.statusBar().showMessage(status)
        except Exception as exc:
            self.render_page(preserve_selection=True)
            self.show_error("Edit annotation failed", exc)
        finally:
            self.applying_property_change = False

    def refresh_annotations_table(self) -> None:
        if self.annotations_table is None:
            return

        table = self.annotations_table
        self.updating_table_selection = True
        try:
            table.set_annotations(self.current_annotations)
        finally:
            self.updating_table_selection = False

        self.sync_table_selection()

    def rect_text(self, rect: fitz.Rect) -> str:
        return f"({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f})"

    def annotation_note(self, model: AnnotationModel) -> str:
        if model.app_type == "arrow":
            return f"start={model.line_start}, end={model.line_end}, LE={model.line_ending}"
        if not model.is_supported:
            return "unsupported annotation type"
        return ""

    def select_annotation_by_xref(self, xref: int) -> bool:
        self.render_page()
        for model in self.current_annotations:
            if model.xref == xref:
                self.select_annotation(model.id, center_on=True)
                return True
        return False

    def begin_add_tool(self, tool: str) -> None:
        if self.doc is None:
            return
        if self.active_tool == tool:
            self.cancel_add_tool()
            self.show_page_status()
            return

        self.set_active_tool(tool)

    def set_active_tool(self, tool: str | None) -> None:
        self.remove_tool_preview()
        self.active_tool = tool
        self.tool_start_scene_pos = None
        self.add_typewriter_action.setChecked(tool == "freetext")
        self.add_rectangle_action.setChecked(tool == "square")
        self.add_highlight_action.setChecked(tool == "highlight")
        self.add_arrow_action.setChecked(tool == "arrow")

        if tool is None:
            self.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        if tool == "freetext":
            self.statusBar().showMessage("Click on the page to add FreeText. Press Esc to cancel.")
        elif tool == "highlight":
            self.statusBar().showMessage("Drag over text to add Highlight. Press Esc to cancel.")
        else:
            self.statusBar().showMessage(f"Drag on the page to add {tool}. Press Esc to cancel.")

    def cancel_add_tool(self) -> None:
        self.remove_tool_preview()
        self.set_active_tool(None)

    def on_tool_mouse_press(self, scene_pos: QPointF) -> bool:
        if self.active_tool is None or self.doc is None:
            return False
        self.clear_selection_items()
        start = self.clamp_scene_pos_to_page(scene_pos)
        if self.active_tool == "freetext":
            try:
                xref = self.create_freetext_annotation_at_point(self.pdf_point_from_scene_point(start))
                self.record_add_undo("Add FreeText", self.page_index, xref)
                self.set_active_tool("freetext")
            except Exception as exc:
                self.show_error("Add FreeText failed", exc)
            return True

        self.tool_start_scene_pos = start
        self.update_tool_preview(self.tool_start_scene_pos)
        return True

    def on_tool_mouse_move(self, scene_pos: QPointF) -> bool:
        if self.active_tool is None or self.tool_start_scene_pos is None:
            return False
        self.update_tool_preview(self.clamp_scene_pos_to_page(scene_pos))
        return True

    def on_tool_mouse_release(self, scene_pos: QPointF) -> bool:
        if self.active_tool is None or self.tool_start_scene_pos is None or self.doc is None:
            return False

        tool = self.active_tool
        start = self.tool_start_scene_pos
        end = self.clamp_scene_pos_to_page(scene_pos)
        self.remove_tool_preview()
        self.tool_start_scene_pos = None

        try:
            if tool == "square":
                rect = self.pdf_rect_from_scene_points(start, end)
                min_width = 10.0
                min_height = 10.0
                if rect.width < min_width or rect.height < min_height:
                    self.statusBar().showMessage(f"{tool} area is too small.")
                    return True
                xref = self.create_square_annotation(rect)
                self.record_add_undo("Add Square", self.page_index, xref)
            elif tool == "highlight":
                start_pdf = self.pdf_point_from_scene_point(start)
                end_pdf = self.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 1 and abs(end_pdf[1] - start_pdf[1]) < 1:
                    self.statusBar().showMessage("Highlight area is too small.")
                    return True
                xref = self.create_highlight_annotation_from_text_flow(start_pdf, end_pdf)
                if xref is not None:
                    self.record_add_undo("Add Highlight", self.page_index, xref)
            elif tool == "arrow":
                start_pdf = self.pdf_point_from_scene_point(start)
                end_pdf = self.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 10 and abs(end_pdf[1] - start_pdf[1]) < 10:
                    self.statusBar().showMessage("Arrow is too short.")
                    return True
                xref = self.create_arrow_annotation(start_pdf, end_pdf)
                self.record_add_undo("Add Arrow", self.page_index, xref)
            self.set_active_tool(tool)
        except Exception as exc:
            self.show_error(f"Add {tool} failed", exc)
        return True

    def update_tool_preview(self, scene_pos: QPointF) -> None:
        if self.active_tool is None or self.tool_start_scene_pos is None:
            return

        self.remove_tool_preview()
        if self.active_tool == "highlight":
            self.update_highlight_tool_preview(scene_pos)
            return

        pen = QPen(QColor(0, 0, 0), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        if self.active_tool == "square":
            rect = QRectF(self.tool_start_scene_pos, scene_pos).normalized()
            self.tool_preview_item = self.scene.addRect(rect, pen, QBrush(Qt.BrushStyle.NoBrush))
        elif self.active_tool == "arrow":
            self.tool_preview_item = self.scene.addLine(QLineF(self.tool_start_scene_pos, scene_pos), pen)
        if self.tool_preview_item is not None:
            self.tool_preview_item.setZValue(30)

    def update_highlight_tool_preview(self, scene_pos: QPointF) -> None:
        if self.tool_start_scene_pos is None or self.doc is None:
            return

        start_pdf = self.pdf_point_from_scene_point(self.tool_start_scene_pos)
        end_pdf = self.pdf_point_from_scene_point(scene_pos)
        rects = self.highlight_rects_from_text_flow(self.current_page(), start_pdf, end_pdf)
        if not rects:
            return

        brush = QBrush(QColor(255, 235, 59, 85))
        for rect in rects:
            item = self.scene.addRect(self.scene_rect(rect), QPen(Qt.PenStyle.NoPen), brush)
            item.setZValue(30)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            self.tool_preview_items.append(item)

    def remove_tool_preview(self) -> None:
        if self.tool_preview_item is not None:
            self.scene.removeItem(self.tool_preview_item)
            self.tool_preview_item = None
        for item in self.tool_preview_items:
            self.scene.removeItem(item)
        self.tool_preview_items.clear()

    def clamp_scene_pos_to_page(self, scene_pos: QPointF) -> QPointF:
        rect = self.page_item.boundingRect()
        return QPointF(
            max(rect.left(), min(scene_pos.x(), rect.right())),
            max(rect.top(), min(scene_pos.y(), rect.bottom())),
        )

    def pdf_point_from_scene_point(self, scene_pos: QPointF) -> tuple[float, float]:
        return scene_pos.x() / self.zoom, scene_pos.y() / self.zoom

    def pdf_rect_from_scene_points(self, start: QPointF, end: QPointF) -> fitz.Rect:
        x0, y0 = self.pdf_point_from_scene_point(start)
        x1, y1 = self.pdf_point_from_scene_point(end)
        return fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def create_freetext_annotation_at_point(self, point: tuple[float, float]) -> int:
        text, ok = QInputDialog.getMultiLineText(self, "Add FreeText", "Text:")
        if not ok or not text.strip():
            self.show_page_status()
            return

        rect = self.default_freetext_rect(point, text.strip(), self.default_freetext_font_size)
        return self.create_freetext_annotation(rect, text.strip())

    def default_freetext_rect(self, point: tuple[float, float], text: str, font_size: int) -> fitz.Rect:
        page_rect = self.current_page().rect
        width, height = self.estimated_freetext_size(text, font_size)
        height = min(height, max(12.0, page_rect.height))
        x0 = min(max(page_rect.x0, point[0]), page_rect.x1 - width)
        y0 = min(max(page_rect.y0, point[1]), page_rect.y1 - height)
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
        page = self.current_page()
        annot = page.add_freetext_annot(
            rect,
            text,
            fontsize=self.default_freetext_font_size,
            fontname="helv",
            text_color=(1, 0, 0),
            fill_color=None,
            border_color=None,
        )
        annot.set_info(title="PDF Note Reader", content=text)
        annot.update()
        if self.use_foxit_freetext:
            self.apply_foxit_freetext_keys(annot)
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)
        return annot.xref

    def create_square_annotation(self, rect: fitz.Rect) -> int:
        page = self.current_page()
        annot = page.add_rect_annot(rect)
        annot.set_colors(stroke=(1, 0, 0))
        annot.set_border(width=2)
        annot.set_info(title="PDF Note Reader", content="Rectangle annotation")
        annot.update()
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)
        return annot.xref

    def create_arrow_annotation(self, start: tuple[float, float], end: tuple[float, float]) -> int:
        page = self.current_page()
        annot = page.add_line_annot(start, end)
        annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        annot.set_colors(stroke=(1, 0, 0))
        annot.set_border(width=2)
        annot.set_info(title="PDF Note Reader", content="Arrow annotation")
        annot.update()
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)
        return annot.xref

    def create_highlight_annotation_from_text_flow(
        self, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> int | None:
        page = self.current_page()
        rects = self.highlight_rects_from_text_flow(page, start_point, end_point)
        if not rects:
            self.statusBar().showMessage("No text found in highlight area.")
            return None

        annot = page.add_highlight_annot(rects)
        annot.set_colors(stroke=self.default_highlight_color)
        annot.set_info(title="PDF Note Reader", content="Highlight annotation")
        annot.update(opacity=self.default_highlight_opacity)
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)
        return annot.xref

    def highlight_rects_from_text_flow(
        self, page: fitz.Page, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> list[fitz.Rect]:
        lines = self.current_page_text_lines(page)
        if not lines:
            return []

        start_position = self.text_position_from_point(lines, start_point)
        end_position = self.text_position_from_point(lines, end_point)
        if start_position is None or end_position is None:
            return []

        start_line, start_offset = start_position
        end_line, end_offset = end_position
        if (end_line, end_offset) < (start_line, start_offset):
            start_line, start_offset, end_line, end_offset = end_line, end_offset, start_line, start_offset

        rects: list[fitz.Rect] = []
        for line_index in range(start_line, end_line + 1):
            chars = lines[line_index]
            if not chars:
                continue

            begin = start_offset if line_index == start_line else 0
            finish = end_offset if line_index == end_line else len(chars)
            begin = max(0, min(begin, len(chars)))
            finish = max(0, min(finish, len(chars)))
            if finish <= begin:
                continue

            selected_boxes = [char["bbox"] for char in chars[begin:finish]]
            selected_chars = chars[begin:finish]
            rects.append(self.highlight_rect_from_selected_chars(selected_chars, selected_boxes))
        return rects

    def current_page_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        if self.text_lines_cache_page_index != self.page_index or self.text_lines_cache is None:
            self.text_lines_cache = self.extract_text_lines(page)
            self.text_lines_cache_page_index = self.page_index
        return self.text_lines_cache

    def highlight_rect_from_selected_chars(self, selected_chars: list[dict], selected_boxes: list[fitz.Rect]) -> fitz.Rect:
        line_rect = fitz.Rect(selected_boxes[0])
        for box in selected_boxes[1:]:
            line_rect |= box

        expanded_rect = self.expand_highlight_rect(line_rect)
        metric_rect = self.highlight_metric_rect(selected_chars, line_rect)
        if metric_rect is not None:
            return fitz.Rect(
                line_rect.x0,
                min(metric_rect.y0, expanded_rect.y0),
                line_rect.x1,
                max(metric_rect.y1, expanded_rect.y1),
            )
        return expanded_rect

    def highlight_metric_rect(self, selected_chars: list[dict], line_rect: fitz.Rect) -> fitz.Rect | None:
        metric_chars = [
            char
            for char in selected_chars
            if char.get("origin") is not None
            and char.get("size") is not None
            and char.get("ascender") is not None
            and char.get("descender") is not None
        ]
        if not metric_chars:
            return None

        top_values: list[float] = []
        bottom_values: list[float] = []
        for char in metric_chars:
            _, baseline_y = char["origin"]
            size = float(char["size"])
            ascender = float(char["ascender"])
            descender = abs(float(char["descender"]))
            top_values.append(baseline_y - size * ascender * 0.82)
            bottom_values.append(baseline_y + size * descender * 0.9)

        top = sum(top_values) / len(top_values)
        bottom = sum(bottom_values) / len(bottom_values)
        if bottom <= top:
            return None
        return fitz.Rect(line_rect.x0, top, line_rect.x1, bottom)

    def expand_highlight_rect(self, rect: fitz.Rect) -> fitz.Rect:
        return fitz.Rect(rect)

    def extract_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        raw = page.get_text("rawdict")
        lines: list[list[dict]] = []
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                chars: list[dict] = []
                for span in line.get("spans", []):
                    span_size = span.get("size")
                    span_origin = span.get("origin")
                    span_ascender = span.get("ascender")
                    span_descender = span.get("descender")
                    for char in span.get("chars", []):
                        bbox = fitz.Rect(char.get("bbox"))
                        chars.append(
                            {
                                "text": char.get("c", ""),
                                "bbox": bbox,
                                "origin": char.get("origin", span_origin),
                                "size": span_size,
                                "ascender": span_ascender,
                                "descender": span_descender,
                            }
                        )
                if chars:
                    chars.sort(key=lambda item: item["bbox"].x0)
                    lines.append(chars)
        return lines

    def text_position_from_point(
        self, lines: list[list[dict]], point: tuple[float, float]
    ) -> tuple[int, int] | None:
        if not lines:
            return None

        px, py = point
        best_line_index = 0
        best_distance = float("inf")
        for index, chars in enumerate(lines):
            line_rect = fitz.Rect(chars[0]["bbox"])
            for char in chars[1:]:
                line_rect |= char["bbox"]
            if line_rect.y0 <= py <= line_rect.y1:
                distance = 0.0
            elif py < line_rect.y0:
                distance = line_rect.y0 - py
            else:
                distance = py - line_rect.y1
            if distance < best_distance:
                best_line_index = index
                best_distance = distance

        chars = lines[best_line_index]
        if px <= chars[0]["bbox"].x0:
            return best_line_index, 0
        if px >= chars[-1]["bbox"].x1:
            return best_line_index, len(chars)

        best_offset = 0
        best_x_distance = float("inf")
        for index, char in enumerate(chars):
            bbox = char["bbox"]
            center_x = (bbox.x0 + bbox.x1) / 2
            offset = index if px < center_x else index + 1
            if bbox.x0 <= px <= bbox.x1:
                return best_line_index, offset
            distance = min(abs(px - bbox.x0), abs(px - bbox.x1), abs(px - center_x))
            if distance < best_x_distance:
                best_offset = offset
                best_x_distance = distance
        return best_line_index, best_offset

    def page_center_rect(self, width: float, height: float) -> fitz.Rect:
        page = self.current_page()
        rect = page.rect
        x0 = rect.x0 + (rect.width - width) / 2
        y0 = rect.y0 + (rect.height - height) / 2
        return fitz.Rect(x0, y0, x0 + width, y0 + height)

    def set_foxit_freetext(self, checked: bool) -> None:
        self.use_foxit_freetext = checked

    def open_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")

        layout = QVBoxLayout(dialog)
        foxit_checkbox = QCheckBox("Experimental Foxit Typewriter compatibility")
        foxit_checkbox.setChecked(self.use_foxit_freetext)
        layout.addWidget(foxit_checkbox)

        extract_highlight_text_checkbox = QCheckBox("Extract highlighted page text when reindexing")
        extract_highlight_text_checkbox.setChecked(self.extract_highlight_text_on_reindex)
        layout.addWidget(extract_highlight_text_checkbox)

        form = QFormLayout()
        font_min_spin = QSpinBox()
        font_min_spin.setRange(1, 72)
        font_min_spin.setValue(self.freetext_font_size_min)
        form.addRow("FreeText font size min", font_min_spin)

        font_max_spin = QSpinBox()
        font_max_spin.setRange(1, 72)
        font_max_spin.setValue(self.freetext_font_size_max)
        form.addRow("FreeText font size max", font_max_spin)

        font_size_spin = QSpinBox()
        font_size_spin.setRange(self.freetext_font_size_min, self.freetext_font_size_max)
        font_size_spin.setValue(self.default_freetext_font_size)
        form.addRow("Default FreeText font size", font_size_spin)

        search_page_size_spin = QSpinBox()
        search_page_size_spin.setRange(1, 10000)
        search_page_size_spin.setValue(self.search_page_size)
        form.addRow("Search page size", search_page_size_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_foxit_freetext(foxit_checkbox.isChecked())
            self.extract_highlight_text_on_reindex = extract_highlight_text_checkbox.isChecked()
            self.freetext_font_size_min = max(1, font_min_spin.value())
            self.freetext_font_size_max = max(self.freetext_font_size_min, font_max_spin.value())
            self.default_freetext_font_size = self.clamp_freetext_font_size(font_size_spin.value())
            self.search_page_size = max(1, search_page_size_spin.value())
            if self.annotation_search_widget is not None:
                self.annotation_search_widget.set_page_size(self.search_page_size)
            try:
                self.save_app_settings()
                if self.doc is not None:
                    self.render_page(preserve_selection=True)
            except Exception as exc:
                self.show_error("Save settings failed", exc)

    def pdf_date_now(self) -> str:
        now = datetime.now().astimezone()
        offset = now.strftime("%z")
        pdf_offset = "Z"
        if offset:
            pdf_offset = f"{offset[:3]}'{offset[3:]}'"
        return now.strftime("D:%Y%m%d%H%M%S") + pdf_offset

    def apply_foxit_freetext_keys(self, annot: fitz.Annot) -> None:
        if self.doc is None:
            return

        xref = annot.xref
        self.doc.xref_set_key(xref, "IT", "/FreeTextTypewriter")
        self.doc.xref_set_key(xref, "Subj", fitz.get_pdf_str("打字机"))
        self.doc.xref_set_key(xref, "CreationDate", fitz.get_pdf_str(self.pdf_date_now()))
        self.remove_annotation_keys(xref, ("CL", "RD"))

    def remove_annotation_keys(self, xref: int, keys: tuple[str, ...]) -> None:
        if self.doc is None:
            return

        source = self.doc.xref_object(xref, compressed=False)
        for key in keys:
            source = re.sub(
                rf"\n\s*/{re.escape(key)}\s+"
                r"(?:"
                r"<<.*?>>"
                r"|\[[^\]]*\]"
                r"|<[^<][^>]*>"
                r"|\([^)]*\)"
                r"|/[^\s<>\[\]()/]+"
                r"|-?\d+(?:\.\d+)?"
                r"|null"
                r")\s*",
                "\n",
                source,
                flags=re.DOTALL,
            )
        self.doc.update_object(xref, source)

    def add_typewriter(self) -> None:
        self.begin_add_tool("freetext")

    def add_rectangle(self) -> None:
        self.begin_add_tool("square")

    def add_highlight(self) -> None:
        self.begin_add_tool("highlight")

    def add_arrow(self) -> None:
        self.begin_add_tool("arrow")

    def save(self, confirm: bool = True) -> bool:
        if self.doc is None:
            return True

        if self.pdf_path is None:
            return self.save_as()

        try:
            self.log_debug(f"Save started: {self.pdf_path}")
            if confirm:
                reply = QMessageBox.question(
                    self,
                    "Confirm Save",
                    "This app will save by fully rewriting the PDF and creating a backup copy first.\n\n"
                    "Continue and replace the current PDF file?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.log_debug("Save canceled by user")
                    return False
            self.log_current_page_state_snapshot("Before full save state snapshot")
            if not self.confirm_current_page_audit_before_save():
                self.log_debug("Save canceled after pre-save audit")
                return False
            backup_path = self.save_full_rewrite_to_current_path()
            self.clear_dirty()
            self.clear_undo()
            self.log_debug(f"Save completed: {self.pdf_path} backup={backup_path}")
            QMessageBox.information(self, "Saved", f"Saved:\n{self.pdf_path}\n\nBackup:\n{backup_path}")
            return True
        except Exception as exc:
            self.log_debug(f"Save failed: {exc}")
            self.show_error("Save failed", exc)
            return False

    def save_incremental(self, confirm: bool = True) -> bool:
        if self.doc is None:
            return True

        if self.pdf_path is None:
            QMessageBox.warning(self, "Save Incremental", "This PDF has no current file path. Use Save As instead.")
            return False

        try:
            self.log_debug(f"Save Incremental started: {self.pdf_path}")
            if confirm:
                reply = QMessageBox.question(
                    self,
                    "Confirm Save Incremental",
                    "Save Incremental writes changes directly into the current PDF without creating a backup.\n\n"
                    "Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.log_debug("Save Incremental canceled by user")
                    return False

            self.log_current_page_state_snapshot("Before incremental save state snapshot")
            if not self.doc.can_save_incrementally():
                self.log_debug(f"Save Incremental unavailable: {self.pdf_path}")
                QMessageBox.warning(
                    self,
                    "Save Incremental",
                    "This PDF cannot be saved incrementally. No fallback save was performed.",
                )
                return False

            self.doc.saveIncr()
            self.clear_dirty()
            self.clear_undo()
            self.log_debug(f"Save Incremental completed: {self.pdf_path}")
            QMessageBox.information(self, "Saved", f"Incrementally saved:\n{self.pdf_path}")
            return True
        except Exception as exc:
            self.log_debug(f"Save Incremental failed: {exc}")
            self.show_error("Save Incremental failed", exc)
            return False

    def save_full_rewrite_to_current_path(self) -> Path:
        if self.doc is None or self.pdf_path is None:
            raise RuntimeError("No PDF is open.")

        current_path = self.pdf_path
        page_index = self.page_index
        temp_path: Path | None = None
        backup_path: Path | None = None
        delete_temp_on_exit = True
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{current_path.stem}.",
                suffix=".tmp.pdf",
                dir=str(current_path.parent),
            )
            os.close(fd)
            temp_path = Path(temp_name)
            self.log_debug(f"Full save temp created: {temp_path}")
            self.doc.save(temp_path, garbage=4, deflate=True)
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise RuntimeError(f"Full save did not create a valid temporary file:\n{temp_path}")
            self.log_debug(f"Full save temp written: {temp_path} bytes={temp_path.stat().st_size}")
            self.audit_saved_temp_pdf(temp_path, page_index)

            backup_path = self.backup_path_for(current_path)
            shutil.copy2(current_path, backup_path)
            self.log_debug(f"Full save backup created: {backup_path}")

            self.release_current_pdf_for_replace()
            self.doc.close()
            self.doc = None
            self.annotation_repo = None
            os.replace(temp_path, current_path)
            self.log_debug(f"Full save replaced original: {current_path}")
            self.doc = fitz.open(current_path)
            if len(self.doc) == 0:
                self.doc.close()
                self.doc = None
                raise RuntimeError("The rewritten PDF has no readable pages. The backup was kept.")
            self.annotation_repo = AnnotationRepository(self.doc)
            self.pdf_path = current_path
            self.page_index = max(0, min(page_index, len(self.doc) - 1))
            if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
                session = self.sessions[self.active_session_index]
                session.doc = self.doc
                session.path = current_path
                session.page_index = self.page_index
            self.render_page(preserve_selection=True)
            self.log_debug(f"Full save reopened rewritten PDF: {current_path} pages={len(self.doc)}")
            return backup_path
        except Exception as exc:
            delete_temp_on_exit = False
            self.log_debug(f"Full save rewrite failed: {exc}")
            if self.doc is None and current_path.exists():
                try:
                    self.doc = fitz.open(current_path)
                    if len(self.doc) > 0:
                        self.annotation_repo = AnnotationRepository(self.doc)
                        self.pdf_path = current_path
                        self.page_index = max(0, min(page_index, len(self.doc) - 1))
                        if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
                            session = self.sessions[self.active_session_index]
                            session.doc = self.doc
                            session.path = current_path
                            session.page_index = self.page_index
                        self.render_page(preserve_selection=True)
                    else:
                        self.doc.close()
                        self.doc = None
                        self.annotation_repo = None
                except Exception:
                    self.doc = None
                    self.annotation_repo = None
            details = [str(exc)]
            if backup_path is not None:
                details.append(f"Backup kept at:\n{backup_path}")
            if temp_path is not None and temp_path.exists():
                details.append(f"Temporary rewritten file kept at:\n{temp_path}")
            details.append("If this file is open in Foxit, Acrobat, or another program, close it and try again.")
            raise RuntimeError("\n\n".join(details)) from exc
        finally:
            if delete_temp_on_exit and temp_path is not None and temp_path.exists() and temp_path != current_path:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def confirm_current_page_audit_before_save(self) -> bool:
        if self.doc is None:
            return True

        report = run_audit_current_page(self.doc, self.page_index)
        if not report_has_errors(report):
            self.log_debug(
                f"Pre-save audit OK: page={self.page_index + 1} "
                f"annotations={report.annotations_found} issues={len(report.issues)}"
            )
            return True

        self.log_debug(
            f"Pre-save audit has errors: page={self.page_index + 1} "
            f"annotations={report.annotations_found} issues={len(report.issues)}"
        )
        reply = QMessageBox.question(
            self,
            "Audit Issues Before Save",
            "The current page has audit errors before saving.\n\n"
            f"{format_audit_report(report)}\n\n"
            "Continue saving anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        proceed = reply == QMessageBox.StandardButton.Yes
        self.log_debug(f"Pre-save audit user decision: {'continue' if proceed else 'cancel'}")
        return proceed

    def audit_saved_temp_pdf(self, temp_path: Path, page_index: int) -> None:
        self.log_debug(f"Temp PDF audit started: {temp_path}")
        temp_doc = fitz.open(temp_path)
        try:
            report = run_audit_current_page(temp_doc, min(max(0, page_index), max(0, len(temp_doc) - 1)))
            if report_has_errors(report):
                self.log_debug(
                    f"Temp PDF audit failed: page={page_index + 1} "
                    f"annotations={report.annotations_found} issues={len(report.issues)}"
                )
                raise RuntimeError(
                    "Audit failed on the temporary rewritten PDF. The original file was not replaced.\n\n"
                    + format_audit_report(report)
                )
            self.log_debug(
                f"Temp PDF audit OK: page={page_index + 1} "
                f"annotations={report.annotations_found} issues={len(report.issues)}"
            )
        finally:
            temp_doc.close()

    def backup_path_for(self, path: Path) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return path.with_name(f"{path.name}.bak-{timestamp}.pdf")

    def release_current_pdf_for_replace(self) -> None:
        self.cancel_add_tool()
        self.selected_annotation_id = None
        self.current_annotations = []
        self.clear_annotation_items()
        self.page_item.setPixmap(QPixmap())
        self.scene.setSceneRect(0, 0, 0, 0)
        self.refresh_annotations_table()
        self.refresh_properties_panel()
        gc.collect()

    def save_as(self) -> bool:
        if self.doc is None:
            return True

        default_name = "annotated.pdf"
        if self.pdf_path:
            default_name = f"{self.pdf_path.stem}_annotated.pdf"

        file_name, _ = QFileDialog.getSaveFileName(self, "Save PDF As", default_name, "PDF files (*.pdf)")
        if not file_name:
            self.log_debug("Save As canceled by user")
            return False

        try:
            self.log_debug(f"Save As started: {file_name}")
            self.doc.save(file_name, garbage=4, deflate=True)
            page_index = self.page_index
            self.doc.close()
            self.doc = fitz.open(file_name)
            self.annotation_repo = AnnotationRepository(self.doc)
            self.pdf_path = Path(file_name)
            self.page_index = max(0, min(page_index, len(self.doc) - 1))
            if self.active_session_index is not None and 0 <= self.active_session_index < len(self.sessions):
                session = self.sessions[self.active_session_index]
                session.doc = self.doc
                session.path = self.pdf_path
                session.page_index = self.page_index
            self.clear_dirty()
            self.clear_undo()
            self.update_recent_file(self.pdf_path, self.page_index)
            self.render_page()
            self.log_debug(f"Save As completed: {file_name}")
            QMessageBox.information(self, "Saved", f"Saved to:\n{file_name}")
            return True
        except Exception as exc:
            self.log_debug(f"Save As failed: {exc}")
            self.show_error("Save failed", exc)
            return False

    def prev_page(self) -> None:
        if self.doc is None or self.page_index <= 0:
            return
        self.cancel_add_tool()
        self.page_index -= 1
        self.render_page()
        self.update_current_recent_page()
        self.save_active_session_state()

    def next_page(self) -> None:
        if self.doc is None or self.page_index >= len(self.doc) - 1:
            return
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
        QMessageBox.critical(self, title, str(exc))

    def keyPressEvent(self, event) -> None:
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
        for session in self.sessions:
            session.doc.close()
            self.log_debug(f"Exit closed PDF: {session.path}")
        self.sessions.clear()
        self.active_session_index = None
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
