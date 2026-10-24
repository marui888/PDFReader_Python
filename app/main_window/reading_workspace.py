from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QSplitter

from app.controllers.scroll_sync_controller import ScrollSyncController
from app.models.document_session import DocumentSession
from app.repositories.annotation_repository import AnnotationRepository
from app.widgets.reading_pane import ReadingPane


class ReadingWorkspace(QSplitter):
    def __init__(self, host):
        super().__init__(Qt.Orientation.Horizontal, host)
        self.host = host
        self.ready = False
        self.active = None
        self.side_by_side = False
        self.activation_counter = 0
        self.pending_documents = {}
        self.closing = False
        self.annotation_tool = None
        self.sync = ScrollSyncController(self)
        template = dict(host.__dict__)
        self.panes = [ReadingPane(host, self, template), ReadingPane(host, self, template)]
        self.active = self.panes[0]
        for pane in self.panes:
            self.addWidget(pane)
        self.setChildrenCollapsible(False)
        self.panes[1].hide()
        self.ready = True
        self.paint_active()

    def sessions(self):
        return [(pane, session) for pane in self.panes for session in pane.sessions]

    def references(self, document):
        return [(pane, session) for pane, session in self.sessions() if session.document is document]

    @staticmethod
    def path_key(path):
        return str(Path(path).resolve()).casefold()

    def find_session(self, path, preferred=None):
        key = self.path_key(path)
        panes = [preferred] + [p for p in self.panes if p is not preferred] if preferred else self.panes
        for pane in panes:
            indices = list(range(len(pane.sessions)))
            if pane.active_session_index in indices:
                indices.remove(pane.active_session_index)
                indices.insert(0, pane.active_session_index)
            for index in indices:
                session = pane.sessions[index]
                if self.path_key(session.path) == key:
                    return pane, index
        return None

    def activate(self, pane):
        if not self.ready or self.active is pane:
            return
        previous = self.active
        with self.sync.hold(), self.preserve_tool_mode():
            if previous is not None:
                previous.canvas_controller.annotation_locator.stop()
                if previous.inline_freetext_editor.is_active():
                    previous.inline_freetext_editor.accept_current()
                previous.cancel_add_tool()
                previous.document_controller.save_active_session_state()
            self.active = pane
            self.note_activation(pane)
            self.paint_active()
            self.refresh_active()

    def note_activation(self, pane):
        if pane.session is not None:
            self.activation_counter += 1
            pane.session.last_activated = self.activation_counter

    def paint_active(self):
        for pane in self.panes:
            color = "#3985cc" if pane is self.active else "#777777"
            pane.setStyleSheet(f"ReadingPane {{ border: 2px solid {color}; }}")

    def activate_tool(self, tool):
        """Toolbar commands always address the controller of the active pane."""
        pane = self.active
        if pane.doc is None:
            self.sync_tool_actions()
            return
        if tool is None:
            pane.annotation_controller.activate_text_mode()
        else:
            pane.annotation_controller.begin_add_tool(tool)
        self.sync_tool_actions()

    def set_annotation_tool(self, tool):
        self.annotation_tool = tool
        for pane in self.panes:
            pane.annotation_controller.apply_active_tool(tool)
        self.sync_tool_actions()

    @contextmanager
    def preserve_tool_mode(self):
        tool = self.annotation_tool
        try:
            yield
        finally:
            self.set_annotation_tool(tool)

    def sync_tool_actions(self):
        tools = (("text_mode_action", None), ("add_typewriter_action", "freetext"),
                 ("add_rectangle_action", "square"), ("add_highlight_action", "highlight"),
                 ("add_arrow_action", "arrow"))
        for name, tool in tools:
            action = getattr(self.host, name, None)
            if action is not None:
                action.setChecked(self.active.active_tool == tool)

    def refresh_active(self):
        if not hasattr(self.host, "continuous_view_action"):
            return
        pane = self.active
        pane.update_window_title()
        pane.sync_page_spin()
        pane.refresh_annotations_table()
        pane.refresh_properties_panel()
        pane.refresh_navigation()
        pane.refresh_annotation_search_status()
        pane.show_page_status()
        pane.update_actions()
        self.sync_tool_actions()
        self.update_actions()

    def update_actions(self):
        if not hasattr(self.host, "side_by_side_action"):
            return
        self.host.side_by_side_action.setChecked(self.side_by_side)
        self.host.sync_vertical_action.setEnabled(self.sync.available())
        self.host.sync_vertical_action.setChecked(self.sync.enabled)

    @contextmanager
    def preserve_positions(self):
        anchors = [(p, p.session, p.canvas_controller.capture_view_mode_anchor())
                   for p in self.panes if p.doc is not None]
        with self.sync.hold():
            try:
                yield
            finally:
                for pane, session, anchor in anchors:
                    if pane.session is session and pane.doc is not None:
                        pane.canvas_controller.restore_view_mode_anchor(anchor)
                        pane.document_controller.save_active_session_state()

    def set_side_by_side(self, enabled, choose_partner=True):
        enabled = bool(enabled)
        if enabled == self.side_by_side:
            return
        self.sync.set_enabled(False)
        with self.preserve_positions():
            left, right = self.panes
            if enabled:
                self.side_by_side = True
                right.show()
                if choose_partner and not right.sessions:
                    candidates = [s for s in left.sessions if s is not left.session]
                    if candidates:
                        session = max(candidates, key=lambda s: s.last_activated)
                        self.transfer(left, left.sessions.index(session), right, activate=False)
                self.setSizes([max(1, self.width() // 2)] * 2)
            else:
                selected = self.active.session
                while right.sessions:
                    self.transfer(right, 0, left, activate=False)
                self.side_by_side = False
                right.hide()
                self.active = left
                if selected in left.sessions:
                    left.document_controller.set_active_session(left.sessions.index(selected), True)
            self.paint_active()
            self.refresh_active()
        # Qt completes splitter geometry after the current event.
        self.restore_after_layout()

    def restore_after_layout(self):
        snapshots = [(p, p.session, p.session.view_anchor if p.session else None) for p in self.panes]
        def restore():
            if self.closing:
                return
            with self.sync.hold():
                for pane, session, anchor in snapshots:
                    if pane.session is session and pane.doc is not None:
                        pane.canvas_controller.restore_view_mode_anchor(anchor)
        QTimer.singleShot(0, self, restore)

    def other_pane(self, pane):
        return self.panes[1] if pane is self.panes[0] else self.panes[0]

    def move_to_other(self, source, index):
        if not 0 <= index < len(source.sessions):
            return
        session = source.sessions[index]
        previous = source.session
        if not self.side_by_side:
            self.set_side_by_side(True, choose_partner=False)
        self.transfer(source, index, self.other_pane(source), activate=True)
        if previous is not session and previous in source.sessions:
            source.document_controller.set_active_session(source.sessions.index(previous), True)

    def open_in_other(self, source, index):
        if not 0 <= index < len(source.sessions):
            return
        source.document_controller.save_active_session_state()
        original = source.sessions[index]
        if not self.side_by_side:
            self.set_side_by_side(True, choose_partner=False)
        target = self.other_pane(source)
        duplicate = DocumentSession(
            original.document, original.page_index, original.zoom,
            view_mode=original.view_mode,
            view_anchor=dict(original.view_anchor) if original.view_anchor else None,
        )
        self.sync.set_enabled(False)
        target.sessions.append(duplicate)
        target.document_controller.add_document_tab(duplicate)
        self.activate(target)
        target.document_controller.set_active_session(len(target.sessions) - 1, True)

    def transfer(self, source, index, target, activate):
        self.sync.set_enabled(False)
        with self.sync.hold(), self.preserve_tool_mode():
            if source.active_session_index == index:
                if source.inline_freetext_editor.is_active():
                    source.inline_freetext_editor.accept_current()
                source.cancel_add_tool()
            source.document_controller.save_active_session_state()
            target.document_controller.save_active_session_state()
            session = source.sessions[index]
            was_current = source.session is session
            source.sessions.pop(index)
            if was_current:
                self.clear_pane(source)
            elif source.active_session_index is not None and source.active_session_index > index:
                source.active_session_index -= 1
            source.updating_document_tabs = True
            try:
                source.document_tabs.removeTab(index)
                source.document_tabs.setVisible(bool(source.sessions))
            finally:
                source.updating_document_tabs = False
            if was_current and source.sessions:
                source.document_controller.set_active_session(min(index, len(source.sessions) - 1), True)
            target.sessions.append(session)
            target.document_controller.add_document_tab(session)
            if activate:
                self.activate(target)
            target.document_controller.set_active_session(len(target.sessions) - 1, True)

    def clear_pane(self, pane):
        pane.canvas_controller.annotation_locator.stop()
        pane.stop_continuous_scroll_timer()
        pane.active_session_index = None
        pane.state.document.doc = None
        pane.state.document.pdf_path = None
        pane.annotation_repo = None
        pane.state.document.is_dirty = False
        pane.state.undo.action = None
        pane.selected_annotation_id = None
        pane.current_annotations = []
        pane.clear_annotation_items()
        pane.clear_continuous_page_items()
        pane.clear_continuous_annotation_cache()
        pane.clear_continuous_pixmap_cache()
        pane.continuous_page_layouts = []
        pane.continuous_layout_doc_id = None
        pane.continuous_layout_zoom = None
        pane.page_item.setPixmap(QPixmap())
        pane.scene.setSceneRect(0, 0, 0, 0)
        pane.page_index = 0

    def document_changed(self, source):
        document = source.document
        if document is None or self.closing:
            return
        self.pending_documents[id(document)] = (document, source)
        if len(self.pending_documents) == 1:
            QTimer.singleShot(0, self, self.flush_document_changes)

    def flush_document_changes(self):
        pending, self.pending_documents = self.pending_documents, {}
        for document, source in pending.values():
            if document.doc is None or document.doc.is_closed:
                continue
            with self.sync.hold():
                for pane, session in self.references(document):
                    pane.document_controller.update_document_tab_title(pane.sessions.index(session))
                    if pane.session is not session or pane is source:
                        continue
                    anchor = pane.canvas_controller.capture_view_mode_anchor()
                    pane.clear_continuous_annotation_cache()
                    pane.clear_continuous_page_items()
                    pane.navigation_anchor_dirty = True
                    pane.state.navigation.anchor_cache.clear()
                    pane.render_page(preserve_selection=True, keep_view_position=True)
                    pane.canvas_controller.restore_view_mode_anchor(anchor)
        self.update_actions()

    def prepare_document_save(self, source):
        """Release view-owned PDF references before the mandatory close/reopen."""
        document = source.document
        snapshots = []
        with self.sync.hold():
            for pane, session in self.references(document):
                if pane.session is session:
                    if pane.inline_freetext_editor.is_active():
                        pane.inline_freetext_editor.accept_current()
                    pane.cancel_add_tool()
                    pane.document_controller.save_active_session_state()
                    snapshots.append((pane, session, session.view_anchor))
                    pane.stop_continuous_scroll_timer()
                    pane.clear_annotation_items()
                    pane.clear_continuous_page_items()
                    pane.clear_continuous_annotation_cache()
                    pane.clear_continuous_pixmap_cache()
                    pane.current_annotations = []
                    pane.annotation_model_map.clear()
                    pane.annotation_repo = None
                    pane.state.navigation.anchor_cache.clear()
                    pane.navigation_anchor_doc_id = None
            document.freetext_snapshot = None
        return document, snapshots

    @contextmanager
    def saving_document(self, source):
        with self.sync.hold(), self.preserve_tool_mode():
            original_doc = source.doc
            document, snapshots = self.prepare_document_save(source)
            try:
                yield
            finally:
                if document.doc is not original_doc:
                    document.undo_action = None
                self.finish_document_save(document, snapshots)

    def finish_document_save(self, document, snapshots):
        with self.sync.hold():
            for pane, session in self.references(document):
                pane.document_controller.update_document_tab_title(pane.sessions.index(session))
            for pane, session, anchor in snapshots:
                if pane.session is not session:
                    continue
                pane.state.document.doc = document.doc
                pane.state.document.pdf_path = document.path
                if document.doc is None or document.doc.is_closed:
                    pane.annotation_repo = None
                    pane.page_item.setPixmap(QPixmap())
                    continue
                pane.annotation_repo = AnnotationRepository(document.doc)
                pane.continuous_layout_doc_id = None
                pane.navigation_anchor_dirty = True
                pane.page_index = max(0, min(session.page_index, len(document.doc) - 1))
                pane.render_page(preserve_selection=False, keep_view_position=True)
                pane.canvas_controller.restore_view_mode_anchor(anchor)
                pane.document_controller.save_active_session_state()
        if not self.sync.available():
            self.sync.set_enabled(False)
        self.refresh_active()

    def confirm_exit(self):
        for pane in self.panes:
            if pane.inline_freetext_editor.is_active():
                pane.inline_freetext_editor.accept_current()
        seen = set()
        for pane, session in self.sessions():
            document = session.document
            if id(document) in seen:
                continue
            seen.add(id(document))
            if document.is_dirty:
                self.activate(pane)
                if not pane.document_controller.confirm_unsaved_session(
                    pane.sessions.index(session), "exit"
                ):
                    return False
        return True

    def shutdown(self):
        self.closing = True
        documents = {id(s.document): s.document for _, s in self.sessions()}
        for pane in self.panes:
            pane.cancel_add_tool()
            pane.stop_continuous_scroll_timer()
        for document in documents.values():
            if document.doc is not None and not document.doc.is_closed:
                document.doc.close()
        for pane in self.panes:
            pane.sessions.clear()
            pane.active_session_index = None
