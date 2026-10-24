"""Independent reader state with an adapter for the existing reader commands.

The main window owns menus, docks and settings. Each pane owns its scene,
controllers and reading state. Legacy reader commands use ordinary Python
forwarders; Qt signals connect only to slots declared on this QWidget.
"""
from __future__ import annotations

import copy
import inspect

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Slot
from PySide6.QtWidgets import QFrame, QGraphicsPixmapItem, QTabBar, QVBoxLayout

from app.canvas.inline_freetext_editor import InlineFreeTextEditorManager
from app.canvas.pdf_canvas import AnnotationScene, PdfCanvasView
from app.controllers.annotation_controller import AnnotationController
from app.controllers.canvas_controller import CanvasController
from app.controllers.document_controller import DocumentController
from app.controllers.navigation_controller import NavigationController
from app.controllers.view_history_controller import ViewHistoryController
from app.models.app_state import AppState
from app.services.ui_contrast import document_tabbar_contrast_stylesheet


READER_FIELDS = {
    "state", "doc", "pdf_path", "annotation_repo", "page_index", "zoom",
    "is_dirty", "sessions", "active_session_index", "undo_action",
    "current_annotations", "annotation_items", "annotation_item_map",
    "annotation_model_map", "selection_items", "selected_annotation_id",
    "updating_document_tabs", "updating_page_spin", "updating_table_selection",
    "updating_scene_selection", "applying_property_change",
    "active_scene_drag_kind", "active_scene_drag_annotation_id",
    "active_scene_drag_start_pos", "active_scene_drag_handle", "active_tool",
    "tool_start_scene_pos", "tool_preview_item", "tool_preview_items",
    "text_lines_cache_page_index", "text_lines_cache", "view_mode",
    "scene", "view", "page_item", "document_tabs", "inline_freetext_editor",
    "annotation_controller", "canvas_controller", "document_controller",
    "view_history_controller", "navigation_controller",
    "navigation_anchor_doc_id", "navigation_anchor_dirty",
    "navigation_anchor_manual_refresh_required", "navigation_anchor_manual_refresh_doc_id",
}


def is_reader_field(name: str) -> bool:
    if name == "continuous_view_action":
        return False
    return name in READER_FIELDS or name.startswith((
        "continuous_", "text_selection_", "inline_freetext_", "hidden_inline_freetext_",
    ))


# Rendering a passive pane must not replace the shared toolbar or dock contents.
ACTIVE_UI_METHODS = {
    "sync_page_spin", "update_window_title", "update_actions", "show_page_status",
    "refresh_annotations_table", "refresh_properties_panel", "refresh_navigation",
    "refresh_annotation_search_status", "show_scroll_boundary_status",
    "clear_scroll_boundary_status", "refresh_quick_audit",
    "sync_table_selection",
}
HOST_METHODS = {
    "record_recent_search_terms",
    "show_current_page_annotations", "show_annotation_properties", "show_navigation",
    "show_annotation_search", "show_debug_log", "show_index_database_info",
    "show_dock", "apply_ui_contrast", "set_ui_contrast", "save_app_settings",
    "load_app_settings", "apply_shortcuts", "open_settings", "log_debug",
    "refresh_debug_log", "refresh_recent_files_menu",
}


class ReadingPane(QFrame):
    def __init__(self, host, workspace, template: dict) -> None:
        super().__init__(host)
        self.host_window = host
        self.reading_workspace = workspace
        self._reader_commands = {}
        self.state = AppState()
        self.state.search = template["state"].search
        for name, value in template.items():
            if name != "state" and is_reader_field(name) and not name.endswith("controller"):
                if name not in {"continuous_scroll_timer", "inline_freetext_editor"}:
                    setattr(self, name, copy.copy(value))
        self.annotation_controller = AnnotationController(self)
        self.canvas_controller = CanvasController(self)
        self.document_controller = DocumentController(self)
        self.view_history_controller = ViewHistoryController(self)
        self.navigation_controller = NavigationController(self)
        self.continuous_scroll_timer = QTimer(self)
        self.continuous_scroll_timer.setSingleShot(True)
        self.continuous_scroll_timer.setInterval(40)
        self.continuous_scroll_timer.timeout.connect(self._on_scroll_timer)
        self.scene = AnnotationScene(self)
        self.scene.selectionChanged.connect(self._on_scene_selection_changed)
        self.view = PdfCanvasView(self)
        self.view.setScene(self.scene)
        self.view.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.page_item = QGraphicsPixmapItem()
        self.scene.addItem(self.page_item)
        self.inline_freetext_editor = InlineFreeTextEditorManager(
            self.scene, lambda: self.zoom, self.on_inline_freetext_accept,
            self.on_inline_freetext_cancel, self.keyword_groups,
            lambda: self.use_graphics_freetext_editor,
        )
        self.document_tabs = QTabBar(self)
        self.document_tabs.setExpanding(False)
        self.document_tabs.setMovable(False)
        self.document_tabs.setIconSize(QSize(8, 8))
        self.document_tabs.hide()
        self.document_tabs.setStyleSheet(document_tabbar_contrast_stylesheet(self.ui_contrast))
        self.document_tabs.currentChanged.connect(self._tab_changed)
        self.document_tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.document_tabs.customContextMenuRequested.connect(self._tab_menu)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        layout.addWidget(self.document_tabs)
        layout.addWidget(self.view, 1)
        for widget in (self, self.view, self.view.viewport(), self.document_tabs,
                       self.view.verticalScrollBar(), self.view.horizontalScrollBar()):
            widget.installEventFilter(self)

    def __getattr__(self, name):
        # Do not present MainWindow's Qt/Python metadata as ReadingPane metadata.
        if name.startswith("__"):
            raise AttributeError(name)
        host = self.__dict__.get("host_window")
        if host is None:
            raise AttributeError(name)
        if name in HOST_METHODS:
            return getattr(host, name)
        descriptor = inspect.getattr_static(type(host), name, None)
        if isinstance(descriptor, property):
            return descriptor.fget(self)
        if inspect.isfunction(descriptor):
            commands = self._reader_commands
            if name not in commands:
                def invoke_reader_command(*args, **kwargs):
                    if name in ACTIVE_UI_METHODS and self.reading_workspace.active is not self:
                        return None
                    return descriptor(self, *args, **kwargs)
                commands[name] = invoke_reader_command
            return commands[name]
        return getattr(host, name)

    def __setattr__(self, name, value):
        host = self.__dict__.get("host_window")
        if host is not None and name not in {"host_window", "reading_workspace"}:
            descriptor = inspect.getattr_static(type(self), name, None)
            if isinstance(descriptor, property):
                descriptor.fset(self, value)
                return
            descriptor = inspect.getattr_static(type(host), name, None)
            if isinstance(descriptor, property):
                descriptor.fset(self, value)
                return
            if not is_reader_field(name) and name in host.__dict__:
                setattr(host, name, value)
                return
        super().__setattr__(name, value)

    @property
    def active_tool(self):
        return self.reading_workspace.annotation_tool

    @active_tool.setter
    def active_tool(self, value):
        self.reading_workspace.annotation_tool = value
        self.state.interaction.active_tool = value

    @property
    def session(self):
        index = self.active_session_index
        return self.sessions[index] if index is not None and 0 <= index < len(self.sessions) else None

    @property
    def document(self):
        return self.session.document if self.session is not None else None

    @property
    def doc(self):
        return self.document.doc if self.document is not None else self.state.document.doc

    @doc.setter
    def doc(self, value):
        self.state.document.doc = value
        if self.document is not None:
            self.document.doc = value

    @property
    def pdf_path(self):
        return self.document.path if self.document is not None else self.state.document.pdf_path

    @pdf_path.setter
    def pdf_path(self, value):
        self.state.document.pdf_path = value
        if self.document is not None and value is not None:
            self.document.path = value

    @property
    def is_dirty(self):
        return self.document.is_dirty if self.document is not None else self.state.document.is_dirty

    @is_dirty.setter
    def is_dirty(self, value):
        self.state.document.is_dirty = value
        if self.document is not None:
            self.document.is_dirty = value

    @property
    def undo_action(self):
        return self.document.undo_action if self.document is not None else self.state.undo.action

    @undo_action.setter
    def undo_action(self, value):
        self.state.undo.action = value
        if self.document is not None:
            self.document.undo_action = value

    def statusBar(self):
        return self.host_window.statusBar()

    def menuBar(self):
        return self.host_window.menuBar()

    def setWindowTitle(self, title):
        if self.reading_workspace.active is self:
            self.host_window.setWindowTitle(title)

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
                            QEvent.Type.KeyPress, QEvent.Type.FocusIn}:
            self.reading_workspace.activate(self)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        workspace = self.__dict__.get("reading_workspace")
        if workspace is not None and workspace.ready:
            self._schedule_scroll_update()

    def _schedule_scroll_update(self):
        if self.host_window.bulk_annotation_controller.rendering_suspended:
            return
        if (self.doc is not None and self.view_mode == "continuous"
                and not self.continuous_suppress_scroll_sync):
            self.continuous_scroll_timer.start()

    @Slot()
    def _on_scroll_timer(self):
        if self.host_window.bulk_annotation_controller.rendering_suspended:
            return
        if (self.doc is not None and self.view_mode == "continuous"
                and not self.continuous_suppress_scroll_sync):
            self.canvas_controller.on_view_scrolled()

    @Slot()
    def _on_scene_selection_changed(self):
        self.annotation_controller.on_scene_selection_changed()

    @Slot(int)
    def _scrolled(self, _value):
        self.reading_workspace.sync.scrolled(self)
        self._schedule_scroll_update()
        if self.session is not None and not self.reading_workspace.sync.hold_depth:
            self.session.view_anchor = self.canvas_controller.capture_view_mode_anchor()

    @Slot(int)
    def _tab_changed(self, index):
        if not self.updating_document_tabs:
            self.reading_workspace.activate(self)
            self.on_document_tab_changed(index)

    @Slot(QPoint)
    def _tab_menu(self, pos):
        self.reading_workspace.activate(self)
        self.show_document_tab_context_menu(pos)
