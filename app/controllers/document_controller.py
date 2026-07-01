from __future__ import annotations

import gc
import os
import shutil
import tempfile
from time import perf_counter
from datetime import datetime
from pathlib import Path

import pymupdf as fitz
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QMenu

from app.repositories.annotation_repository import AnnotationRepository
from app.main_window import dialogs as main_window_dialogs
from app.models.document_session import DocumentSession
from app.services.pdf_audit import audit_current_page as run_audit_current_page
from app.services.pdf_audit import format_audit_report
from app.services.pdf_audit import report_has_errors
from app.services.pdf_external_tools import backup_pdf_file
from app.services.pdf_external_tools import run_qpdf_check
from app.services.pdf_external_tools import rewrite_pdf_with_qpdf


class DocumentController:
    def __init__(self, window) -> None:
        self.window = window

    def open_pdf(self) -> None:
        window = self.window
        file_name, _ = QFileDialog.getOpenFileName(window, "Open PDF", "", "PDF files (*.pdf)")
        if not file_name:
            return

        path = Path(file_name)
        self.open_pdf_path(path, self.recent_page_index(path))

    def open_pdf_path(self, path: Path, page_index: int = 0) -> bool:
        window = self.window
        existing_index = self.session_index_for_path(path)
        if existing_index is not None:
            window.log_debug(f"Open switched to existing tab: {path}")
            self.set_active_session(existing_index, preserve_selection=True)
            return True

        try:
            window.log_debug(f"Open started: {path}")
            new_doc = fitz.open(path)
            if len(new_doc) == 0:
                new_doc.close()
                raise RuntimeError(
                    "This PDF has no readable pages. It may be damaged or have a broken page tree."
                )
            window.log_debug(f"Open loaded: {path} pages={len(new_doc)}")
            self.save_active_session_state()
            session = DocumentSession(
                doc=new_doc,
                path=path,
                page_index=max(0, min(page_index, len(new_doc) - 1)),
                zoom=window.zoom,
            )
            window.sessions.append(session)
            self.add_document_tab(session)
            self.set_active_session(len(window.sessions) - 1)
            self.update_recent_file(path, window.page_index)
            window.log_debug(f"Open completed: {path} page={window.page_index + 1}")
            return True
        except Exception as exc:
            window.log_debug(f"Open failed: {path}: {exc}")
            window.show_error("Open failed", exc)
            return False

    def close_pdf(self) -> None:
        window = self.window
        closed_path = window.pdf_path
        if window.active_session_index is not None:
            if self.close_document_tab(window.active_session_index):
                window.log_debug(f"Close completed: {closed_path}")
            else:
                window.log_debug("Close canceled by unsaved changes prompt")
            return

        if not window.confirm_active_unsaved_changes("close this PDF"):
            window.log_debug("Close canceled by unsaved changes prompt")
            return

        window.cancel_add_tool()
        window.doc = None
        window.annotation_repo = None
        window.pdf_path = None
        window.page_index = 0
        window.clear_dirty()
        window.clear_undo()
        window.current_annotations = []
        window.clear_annotation_items()
        window.page_item.setPixmap(QPixmap())
        window.scene.setSceneRect(0, 0, 0, 0)
        window.sync_page_spin()
        window.update_window_title()
        window.statusBar().showMessage("No PDF open")
        window.refresh_annotations_table()
        window.refresh_properties_panel()
        window.refresh_navigation()
        window.refresh_annotation_search_status()
        window.sync_page_spin()
        window.update_actions()
        window.log_debug(f"Close completed: {closed_path}")

    def session_index_for_path(self, path: Path) -> int | None:
        window = self.window
        try:
            target = path.resolve()
        except OSError:
            target = path
        for index, session in enumerate(window.sessions):
            try:
                session_path = session.path.resolve()
            except OSError:
                session_path = session.path
            if session_path == target:
                return index
        return None

    def save_active_session_state(self) -> None:
        window = self.window
        if window.active_session_index is None:
            return
        if window.active_session_index < 0 or window.active_session_index >= len(window.sessions):
            return
        session = window.sessions[window.active_session_index]
        session.page_index = window.page_index
        session.zoom = window.zoom
        session.is_dirty = window.is_dirty
        session.selected_annotation_id = window.selected_annotation_id
        self.update_document_tab_title(window.active_session_index)

    def add_document_tab(self, session: DocumentSession) -> None:
        window = self.window
        window.updating_document_tabs = True
        try:
            window.document_tabs.addTab(self.document_tab_title(session))
            window.document_tabs.setTabToolTip(window.document_tabs.count() - 1, str(session.path))
            window.document_tabs.setVisible(window.document_tabs.count() > 0)
        finally:
            window.updating_document_tabs = False

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
        window = self.window
        if index is None:
            index = window.active_session_index
        if index is None or index < 0 or index >= len(window.sessions):
            return
        session = window.sessions[index]
        window.document_tabs.setTabText(index, self.document_tab_title(session))
        window.document_tabs.setTabToolTip(index, str(session.path))

    def set_active_session(self, index: int, preserve_selection: bool = False) -> None:
        window = self.window
        started_at = perf_counter()
        if index < 0 or index >= len(window.sessions):
            return

        if window.active_session_index == index:
            same_started_at = perf_counter()
            window.updating_document_tabs = True
            try:
                window.document_tabs.setCurrentIndex(index)
            finally:
                window.updating_document_tabs = False
            window.log_debug(
                f"Tab switch perf: same-session index={index} total={(perf_counter() - same_started_at) * 1000:.1f}ms"
            )
            return

        cancel_started_at = perf_counter()
        window.cancel_add_tool()
        cancel_ms = (perf_counter() - cancel_started_at) * 1000

        save_state_started_at = perf_counter()
        self.save_active_session_state()
        save_state_ms = (perf_counter() - save_state_started_at) * 1000

        assign_started_at = perf_counter()
        window.active_session_index = index
        session = window.sessions[index]
        window.doc = session.doc
        window.annotation_repo = AnnotationRepository(window.doc)
        window.pdf_path = session.path
        window.page_index = max(0, min(session.page_index, len(window.doc) - 1))
        window.zoom = session.zoom
        window.is_dirty = session.is_dirty
        window.selected_annotation_id = session.selected_annotation_id if preserve_selection else None
        window.clear_undo()
        window.current_annotations = []
        assign_ms = (perf_counter() - assign_started_at) * 1000

        tab_started_at = perf_counter()
        window.updating_document_tabs = True
        try:
            window.document_tabs.setCurrentIndex(index)
        finally:
            window.updating_document_tabs = False
        tab_ms = (perf_counter() - tab_started_at) * 1000

        render_started_at = perf_counter()
        window.render_page(preserve_selection=preserve_selection)
        render_ms = (perf_counter() - render_started_at) * 1000

        recent_started_at = perf_counter()
        self.update_recent_file(window.pdf_path, window.page_index)
        recent_ms = (perf_counter() - recent_started_at) * 1000

        search_started_at = perf_counter()
        window.refresh_annotation_search_status()
        search_ms = (perf_counter() - search_started_at) * 1000
        total_ms = (perf_counter() - started_at) * 1000
        window.log_debug(f"Document tab activated: {window.pdf_path} page={window.page_index + 1}")
        window.log_debug(
            "Tab switch perf: "
            f"index={index} total={total_ms:.1f}ms "
            f"cancel={cancel_ms:.1f}ms save_state={save_state_ms:.1f}ms assign={assign_ms:.1f}ms "
            f"tab={tab_ms:.1f}ms render={render_ms:.1f}ms recent={recent_ms:.1f}ms search={search_ms:.1f}ms"
        )

    def on_document_tab_changed(self, index: int) -> None:
        window = self.window
        if window.updating_document_tabs:
            return
        window.record_view_location_before_navigation()
        self.set_active_session(index, preserve_selection=True)

    def show_document_tab_context_menu(self, pos) -> None:
        window = self.window
        index = window.document_tabs.tabAt(pos)
        if index < 0 or index >= len(window.sessions):
            return

        menu = QMenu(window)
        save_incremental_action = menu.addAction("Save Incremental")
        backup_action = menu.addAction("Backup Current PDF")
        qpdf_check_action = menu.addAction("QPDF Check Current PDF")
        qpdf_rewrite_action = menu.addAction("QPDF Rewrite Current PDF")
        menu.addSeparator()
        close_action = menu.addAction("Close")
        close_others_action = menu.addAction("Close Others")
        close_all_action = menu.addAction("Close All")
        close_others_action.setEnabled(len(window.sessions) > 1)
        close_all_action.setEnabled(bool(window.sessions))

        selected_action = menu.exec(window.document_tabs.mapToGlobal(pos))
        if selected_action == save_incremental_action:
            self.set_active_session(index, preserve_selection=True)
            window.save_incremental()
        elif selected_action == backup_action:
            self.set_active_session(index, preserve_selection=True)
            self.backup_current_pdf()
        elif selected_action == qpdf_check_action:
            self.set_active_session(index, preserve_selection=True)
            self.qpdf_check_current_pdf()
        elif selected_action == qpdf_rewrite_action:
            self.set_active_session(index, preserve_selection=True)
            self.qpdf_rewrite_current_pdf()
        elif selected_action == close_action:
            self.close_document_tab(index)
        elif selected_action == close_others_action:
            self.close_other_document_tabs(index)
        elif selected_action == close_all_action:
            self.close_all_document_tabs()

    def current_clean_pdf_path_for_external_operation(self, operation_name: str) -> Path | None:
        window = self.window
        if window.doc is None or window.pdf_path is None:
            main_window_dialogs.show_warning(window, operation_name, "No PDF is open.")
            return None

        self.save_active_session_state()
        if window.is_dirty:
            main_window_dialogs.show_warning(
                window,
                operation_name,
                "This PDF has unsaved changes.\n\n"
                "Save or Save Incremental before running this operation.",
            )
            return None

        if not window.pdf_path.exists():
            main_window_dialogs.show_warning(
                window,
                operation_name,
                f"PDF file not found:\n{window.pdf_path}",
            )
            return None

        return window.pdf_path

    def backup_current_pdf(self) -> None:
        window = self.window
        pdf_path = self.current_clean_pdf_path_for_external_operation("Backup Current PDF")
        if pdf_path is None:
            return

        try:
            window.log_debug(f"Backup current PDF started: {pdf_path}")
            backup_path = backup_pdf_file(pdf_path)
            window.log_debug(f"Backup current PDF completed: {backup_path}")
            main_window_dialogs.show_information(window, "Backup Current PDF", f"Backup created:\n{backup_path}")
        except Exception as exc:
            window.log_debug(f"Backup current PDF failed: {exc}")
            window.show_error("Backup Current PDF failed", exc)

    def qpdf_check_current_pdf(self) -> None:
        window = self.window
        pdf_path = self.current_clean_pdf_path_for_external_operation("QPDF Check Current PDF")
        if pdf_path is None:
            return

        try:
            window.log_debug(f"QPDF check started: {pdf_path}")
            report_path = run_qpdf_check(pdf_path, window.qpdf_bin_dir)
            window.log_debug(f"QPDF check completed: {report_path}")
            main_window_dialogs.show_information(
                window,
                "QPDF Check Current PDF",
                f"QPDF report created:\n{report_path}",
            )
        except Exception as exc:
            window.log_debug(f"QPDF check failed: {exc}")
            window.show_error("QPDF Check Current PDF failed", exc)

    def qpdf_rewrite_current_pdf(self) -> None:
        window = self.window
        pdf_path = self.current_clean_pdf_path_for_external_operation("QPDF Rewrite Current PDF")
        if pdf_path is None:
            return

        try:
            window.log_debug(f"QPDF rewrite started: {pdf_path}")
            rewritten_path = rewrite_pdf_with_qpdf(pdf_path, window.qpdf_bin_dir)
            window.log_debug(f"QPDF rewrite completed: {rewritten_path}")
            main_window_dialogs.show_information(
                window,
                "QPDF Rewrite Current PDF",
                f"Rewritten PDF created:\n{rewritten_path}",
            )
        except Exception as exc:
            window.log_debug(f"QPDF rewrite failed: {exc}")
            window.show_error("QPDF Rewrite Current PDF failed", exc)

    def close_document_tab(self, index: int) -> bool:
        window = self.window
        if index < 0 or index >= len(window.sessions):
            return True
        if not window.confirm_unsaved_session(index, "close this PDF"):
            window.log_debug(f"Close tab canceled: {window.sessions[index].path}")
            return False
        closed_path = window.sessions[index].path
        self.close_session(index)
        window.log_debug(f"Close tab completed: {closed_path}")
        return True

    def close_other_document_tabs(self, keep_index: int) -> bool:
        window = self.window
        if keep_index < 0 or keep_index >= len(window.sessions):
            return True

        keep_path = window.sessions[keep_index].path
        index = len(window.sessions) - 1
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
        window = self.window
        while window.sessions:
            if not self.close_document_tab(len(window.sessions) - 1):
                return False
        return True

    def close_session(self, index: int) -> None:
        window = self.window
        if index < 0 or index >= len(window.sessions):
            return

        was_active = index == window.active_session_index
        session = window.sessions.pop(index)
        if was_active:
            self.update_current_recent_page()
        window.state.navigation.anchor_cache.pop(id(session.doc), None)
        if window.navigation_anchor_doc_id == id(session.doc):
            window.navigation_anchor_doc_id = None
            window.navigation_anchor_dirty = True
        session.doc.close()

        window.updating_document_tabs = True
        try:
            window.document_tabs.removeTab(index)
            window.document_tabs.setVisible(window.document_tabs.count() > 0)
        finally:
            window.updating_document_tabs = False

        if not window.sessions:
            window.active_session_index = None
            window.doc = None
            window.annotation_repo = None
            window.pdf_path = None
            window.page_index = 0
            window.is_dirty = False
            window.selected_annotation_id = None
            window.clear_undo()
            window.current_annotations = []
            window.clear_annotation_items()
            window.page_item.setPixmap(QPixmap())
            window.scene.setSceneRect(0, 0, 0, 0)
            window.sync_page_spin()
            window.update_window_title()
            window.statusBar().showMessage("No PDF open")
            window.refresh_annotations_table()
            window.refresh_properties_panel()
            window.refresh_navigation()
            window.refresh_annotation_search_status()
            window.update_actions()
            return

        next_index = min(index, len(window.sessions) - 1)
        window.active_session_index = None
        self.set_active_session(next_index, preserve_selection=True)

    def recent_file_index(self, path: Path) -> int | None:
        window = self.window
        key = str(path).lower()
        for index, record in enumerate(window.recent_files):
            if str(record.get("path", "")).lower() == key:
                return index
        return None

    def recent_page_index(self, path: Path) -> int:
        window = self.window
        index = self.recent_file_index(path)
        if index is None:
            return 0
        try:
            return max(0, int(window.recent_files[index].get("last_page_index", 0)))
        except (TypeError, ValueError):
            return 0

    def update_recent_file(self, path: Path, page_index: int | None = None) -> None:
        window = self.window
        index = self.recent_file_index(path)
        record = window.recent_files.pop(index) if index is not None else {"path": str(path)}
        record["path"] = str(path)
        record["last_page_index"] = max(0, int(0 if page_index is None else page_index))
        record["last_opened_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        window.recent_files.insert(0, record)
        window.recent_files = window.recent_files[: window.max_recent_files]
        window.save_app_settings()
        self.refresh_recent_files_menu()

    def update_current_recent_page(self) -> None:
        window = self.window
        if window.doc is None or window.pdf_path is None:
            return
        index = self.recent_file_index(window.pdf_path)
        if index is None:
            self.update_recent_file(window.pdf_path, window.page_index)
            return
        window.recent_files[index]["last_page_index"] = window.page_index
        window.save_app_settings()
        self.refresh_recent_files_menu()

    def refresh_recent_files_menu(self) -> None:
        window = self.window
        if window.open_recent_menu is None:
            return

        window.open_recent_menu.clear()
        if not window.recent_files:
            empty_action = window.open_recent_menu.addAction("(No Recent Files)")
            empty_action.setEnabled(False)
            return

        for record in window.recent_files:
            path = Path(str(record["path"]))
            page_number = int(record.get("last_page_index", 0)) + 1
            action = window.open_recent_menu.addAction(f"{path.name} - page {page_number}")
            action.setToolTip(str(path))
            action.triggered.connect(lambda checked=False, recent_path=path: window.open_recent_pdf(recent_path))

        window.open_recent_menu.addSeparator()
        clear_action = window.open_recent_menu.addAction("Clear Recent Files")
        clear_action.triggered.connect(window.clear_recent_files)

    def clear_recent_files(self) -> None:
        window = self.window
        window.recent_files = []
        window.save_app_settings()
        self.refresh_recent_files_menu()

    def open_recent_pdf(self, path: Path) -> None:
        window = self.window
        if not path.exists():
            main_window_dialogs.show_warning(window, "Open Recent", f"File not found:\n{path}")
            index = self.recent_file_index(path)
            if index is not None:
                window.recent_files.pop(index)
                window.save_app_settings()
                self.refresh_recent_files_menu()
            return
        self.open_pdf_path(path, self.recent_page_index(path))

    def confirm_active_unsaved_changes(self, action_text: str) -> bool:
        window = self.window
        if window.active_session_index is not None:
            return self.confirm_unsaved_session(window.active_session_index, action_text)
        return self.confirm_unsaved_changes(action_text)

    def confirm_unsaved_changes(self, action_text: str) -> bool:
        window = self.window
        if window.doc is None or not window.is_dirty:
            return True

        choice = self.ask_unsaved_document_action(action_text)
        if choice == "cancel":
            return False
        if choice == "discard":
            return True
        if choice == "save-incremental":
            return self.save_incremental(confirm=True)
        if choice == "save-full":
            return self.save(confirm=True)
        return False

    def ask_unsaved_document_action(self, action_text: str) -> str:
        return main_window_dialogs.ask_unsaved_document_action(self.window, action_text)

    def confirm_unsaved_session(self, index: int, action_text: str) -> bool:
        window = self.window
        if index < 0 or index >= len(window.sessions):
            return True

        self.save_active_session_state()
        session = window.sessions[index]
        if not session.is_dirty:
            return True

        if window.active_session_index != index:
            self.set_active_session(index, preserve_selection=True)

        return self.confirm_unsaved_changes(action_text)

    def confirm_all_unsaved_for_exit(self) -> bool:
        window = self.window
        self.save_active_session_state()
        index = 0
        while index < len(window.sessions):
            if window.sessions[index].is_dirty:
                if not self.confirm_unsaved_session(index, "exit"):
                    return False
                self.save_active_session_state()
            index += 1
        return True

    def save(self, confirm: bool = True) -> bool:
        window = self.window
        if window.doc is None:
            return True

        if window.pdf_path is None:
            return self.save_as()

        try:
            window.log_debug(f"Save started: {window.pdf_path}")
            if confirm:
                if not main_window_dialogs.confirm_full_save(window):
                    window.log_debug("Save canceled by user")
                    return False
            window.log_current_page_state_snapshot("Before full save state snapshot")
            if not self.confirm_current_page_audit_before_save():
                window.log_debug("Save canceled after pre-save audit")
                return False
            backup_path = self.save_full_rewrite_to_current_path()
            window.clear_dirty()
            window.clear_undo()
            window.log_debug(f"Save completed: {window.pdf_path} backup={backup_path}")
            main_window_dialogs.show_information(
                window,
                "Saved",
                f"Saved:\n{window.pdf_path}\n\nBackup:\n{backup_path}",
            )
            return True
        except Exception as exc:
            window.log_debug(f"Save failed: {exc}")
            window.show_error("Save failed", exc)
            return False

    def save_incremental(self, confirm: bool = True) -> bool:
        window = self.window
        if window.doc is None:
            return True

        if window.pdf_path is None:
            main_window_dialogs.show_warning(
                window,
                "Save Incremental",
                "This PDF has no current file path. Use Save As instead.",
            )
            return False

        try:
            window.log_debug(f"Save Incremental started: {window.pdf_path}")
            run_safety_checks = True
            if confirm:
                proceed, run_safety_checks = main_window_dialogs.confirm_save_incremental(window)
                if not proceed:
                    window.log_debug("Save Incremental canceled by user")
                    return False

            window.log_current_page_state_snapshot("Before incremental save state snapshot")
            if not window.doc.can_save_incrementally():
                window.log_debug(f"Save Incremental unavailable: {window.pdf_path}")
                main_window_dialogs.show_warning(
                    window,
                    "Save Incremental",
                    "This PDF cannot be saved incrementally. No fallback save was performed.",
                )
                return False

            backup_path = None
            report_path = None
            if run_safety_checks:
                backup_path = backup_pdf_file(window.pdf_path)
                window.log_debug(f"Save Incremental backup created: {backup_path}")
            window.doc.saveIncr()
            if run_safety_checks:
                report_path = run_qpdf_check(window.pdf_path, window.qpdf_bin_dir, fail_on_error=True)
                window.log_debug(f"Save Incremental qpdf check OK: {report_path}")
            self.reopen_current_pdf_after_incremental_save(window.page_index, window.selected_annotation_id)
            window.clear_dirty()
            window.clear_undo()
            window.log_debug(f"Save Incremental completed: {window.pdf_path} backup={backup_path}")
            window.statusBar().showMessage("Incrementally saved and reopened.")
            details = [f"Incrementally saved and reopened:\n{window.pdf_path}"]
            if backup_path is not None:
                details.append(f"Backup:\n{backup_path}")
            if report_path is not None:
                details.append(f"QPDF report:\n{report_path}")
            main_window_dialogs.show_information(
                window,
                "Saved",
                "\n\n".join(details),
            )
            return True
        except Exception as exc:
            window.log_debug(f"Save Incremental failed: {exc}")
            window.show_error("Save Incremental failed", exc)
            return False

    def reopen_current_pdf_after_incremental_save(
        self, page_index: int, selected_annotation_id: str | None = None
    ) -> None:
        window = self.window
        if window.pdf_path is None or window.doc is None:
            return

        current_path = window.pdf_path
        window.log_debug(f"Reopen after Save Incremental started: {current_path}")
        window.cancel_add_tool()
        window.doc.close()
        window.doc = fitz.open(current_path)
        if len(window.doc) == 0:
            window.doc.close()
            window.doc = None
            window.annotation_repo = None
            raise RuntimeError("The incrementally saved PDF has no readable pages after reopening.")

        window.annotation_repo = AnnotationRepository(window.doc)
        window.page_index = max(0, min(page_index, len(window.doc) - 1))
        window.selected_annotation_id = selected_annotation_id
        if window.active_session_index is not None and 0 <= window.active_session_index < len(window.sessions):
            session = window.sessions[window.active_session_index]
            session.doc = window.doc
            session.path = current_path
            session.page_index = window.page_index
            session.selected_annotation_id = selected_annotation_id
        window.render_page(preserve_selection=selected_annotation_id is not None, keep_view_position=True)
        window.log_debug(f"Reopen after Save Incremental completed: {current_path} pages={len(window.doc)}")

    def save_full_rewrite_to_current_path(self) -> Path:
        window = self.window
        if window.doc is None or window.pdf_path is None:
            raise RuntimeError("No PDF is open.")

        current_path = window.pdf_path
        page_index = window.page_index
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
            window.log_debug(f"Full save temp created: {temp_path}")
            window.doc.save(temp_path, garbage=4, deflate=True)
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise RuntimeError(f"Full save did not create a valid temporary file:\n{temp_path}")
            window.log_debug(f"Full save temp written: {temp_path} bytes={temp_path.stat().st_size}")
            self.audit_saved_temp_pdf(temp_path, page_index)

            backup_path = self.backup_path_for(current_path)
            shutil.copy2(current_path, backup_path)
            window.log_debug(f"Full save backup created: {backup_path}")

            self.release_current_pdf_for_replace()
            window.doc.close()
            window.doc = None
            window.annotation_repo = None
            os.replace(temp_path, current_path)
            window.log_debug(f"Full save replaced original: {current_path}")
            window.doc = fitz.open(current_path)
            if len(window.doc) == 0:
                window.doc.close()
                window.doc = None
                raise RuntimeError("The rewritten PDF has no readable pages. The backup was kept.")
            window.annotation_repo = AnnotationRepository(window.doc)
            window.pdf_path = current_path
            window.page_index = max(0, min(page_index, len(window.doc) - 1))
            if window.active_session_index is not None and 0 <= window.active_session_index < len(window.sessions):
                session = window.sessions[window.active_session_index]
                session.doc = window.doc
                session.path = current_path
                session.page_index = window.page_index
            window.render_page(preserve_selection=True)
            window.log_debug(f"Full save reopened rewritten PDF: {current_path} pages={len(window.doc)}")
            return backup_path
        except Exception as exc:
            delete_temp_on_exit = False
            window.log_debug(f"Full save rewrite failed: {exc}")
            if window.doc is None and current_path.exists():
                try:
                    window.doc = fitz.open(current_path)
                    if len(window.doc) > 0:
                        window.annotation_repo = AnnotationRepository(window.doc)
                        window.pdf_path = current_path
                        window.page_index = max(0, min(page_index, len(window.doc) - 1))
                        if window.active_session_index is not None and 0 <= window.active_session_index < len(window.sessions):
                            session = window.sessions[window.active_session_index]
                            session.doc = window.doc
                            session.path = current_path
                            session.page_index = window.page_index
                        window.render_page(preserve_selection=True)
                    else:
                        window.doc.close()
                        window.doc = None
                        window.annotation_repo = None
                except Exception:
                    window.doc = None
                    window.annotation_repo = None
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
        window = self.window
        if window.doc is None:
            return True

        report = run_audit_current_page(window.doc, window.page_index)
        if not report_has_errors(report):
            window.log_debug(
                f"Pre-save audit OK: page={window.page_index + 1} "
                f"annotations={report.annotations_found} issues={len(report.issues)}"
            )
            return True

        window.log_debug(
            f"Pre-save audit has errors: page={window.page_index + 1} "
            f"annotations={report.annotations_found} issues={len(report.issues)}"
        )
        proceed = main_window_dialogs.confirm_pre_save_audit(window, format_audit_report(report))
        window.log_debug(f"Pre-save audit user decision: {'continue' if proceed else 'cancel'}")
        return proceed

    def audit_saved_temp_pdf(self, temp_path: Path, page_index: int) -> None:
        window = self.window
        window.log_debug(f"Temp PDF audit started: {temp_path}")
        temp_doc = fitz.open(temp_path)
        try:
            report = run_audit_current_page(temp_doc, min(max(0, page_index), max(0, len(temp_doc) - 1)))
            if report_has_errors(report):
                window.log_debug(
                    f"Temp PDF audit failed: page={page_index + 1} "
                    f"annotations={report.annotations_found} issues={len(report.issues)}"
                )
                raise RuntimeError(
                    "Audit failed on the temporary rewritten PDF. The original file was not replaced.\n\n"
                    + format_audit_report(report)
                )
            window.log_debug(
                f"Temp PDF audit OK: page={page_index + 1} "
                f"annotations={report.annotations_found} issues={len(report.issues)}"
            )
        finally:
            temp_doc.close()

    def backup_path_for(self, path: Path) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return path.with_name(f"{path.name}.bak-{timestamp}.pdf")

    def release_current_pdf_for_replace(self) -> None:
        window = self.window
        window.cancel_add_tool()
        window.selected_annotation_id = None
        window.current_annotations = []
        window.clear_annotation_items()
        window.page_item.setPixmap(QPixmap())
        window.scene.setSceneRect(0, 0, 0, 0)
        window.refresh_annotations_table()
        window.refresh_properties_panel()
        gc.collect()

    def save_as(self) -> bool:
        window = self.window
        if window.doc is None:
            return True

        default_name = "annotated.pdf"
        if window.pdf_path:
            default_name = f"{window.pdf_path.stem}_annotated.pdf"

        file_name, _ = QFileDialog.getSaveFileName(window, "Save PDF As", default_name, "PDF files (*.pdf)")
        if not file_name:
            window.log_debug("Save As canceled by user")
            return False

        try:
            window.log_debug(f"Save As started: {file_name}")
            window.doc.save(file_name, garbage=4, deflate=True)
            page_index = window.page_index
            window.doc.close()
            window.doc = fitz.open(file_name)
            window.annotation_repo = AnnotationRepository(window.doc)
            window.pdf_path = Path(file_name)
            window.page_index = max(0, min(page_index, len(window.doc) - 1))
            if window.active_session_index is not None and 0 <= window.active_session_index < len(window.sessions):
                session = window.sessions[window.active_session_index]
                session.doc = window.doc
                session.path = window.pdf_path
                session.page_index = window.page_index
            window.clear_dirty()
            window.clear_undo()
            self.update_recent_file(window.pdf_path, window.page_index)
            window.render_page()
            window.log_debug(f"Save As completed: {file_name}")
            main_window_dialogs.show_information(window, "Saved", f"Saved to:\n{file_name}")
            return True
        except Exception as exc:
            window.log_debug(f"Save As failed: {exc}")
            window.show_error("Save failed", exc)
            return False
