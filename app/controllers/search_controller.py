from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from app.main_window import dialogs as main_window_dialogs
from app.main_window import docks as main_window_docks
from app.search.annotation_search_query import AnnotationSearchQuery
from app.services.index_worker import ReindexWorker


class SearchController:
    def __init__(self, window) -> None:
        self.window = window

    def reindex_current_pdf(self) -> None:
        window = self.window
        if window.doc is None or window.pdf_path is None:
            return
        if window.reindex_thread is not None:
            return

        pdf_path = window.pdf_path
        window.reindex_pdf_path = pdf_path
        extract_highlight_text = self.should_extract_highlight_text_for_index()
        self.set_reindex_busy(True)
        window.log_debug(
            f"Reindex current PDF started: {pdf_path} "
            f"extract_highlight_text={extract_highlight_text}"
        )

        window.reindex_thread = QThread(window)
        window.reindex_worker = ReindexWorker(window.index_path(), pdf_path, extract_highlight_text)
        window.reindex_worker.moveToThread(window.reindex_thread)
        window.reindex_thread.started.connect(window.reindex_worker.run)
        window.reindex_worker.progress.connect(window.on_reindex_progress)
        window.reindex_worker.finished.connect(window.on_reindex_finished)
        window.reindex_worker.failed.connect(window.on_reindex_failed)
        window.reindex_worker.finished.connect(window.reindex_thread.quit)
        window.reindex_worker.failed.connect(window.reindex_thread.quit)
        window.reindex_worker.finished.connect(window.reindex_worker.deleteLater)
        window.reindex_worker.failed.connect(window.reindex_worker.deleteLater)
        window.reindex_thread.finished.connect(window.cleanup_reindex_thread)
        window.reindex_thread.start()

    def should_extract_highlight_text_for_index(self) -> bool:
        return self.window.extract_highlight_text_on_reindex

    def set_reindex_busy(self, busy: bool) -> None:
        window = self.window
        window.reindex_current_pdf_action.setEnabled(not busy and window.doc is not None)
        window.clear_annotation_index_action.setEnabled(not busy)
        window.search_annotations_action.setEnabled(not busy)
        window.open_action.setEnabled(not busy)
        window.close_action.setEnabled(not busy and window.doc is not None)
        window.save_action.setEnabled(not busy and window.doc is not None)
        window.save_as_action.setEnabled(not busy and window.doc is not None)
        window.save_incremental_action.setEnabled(not busy and window.doc is not None)
        if window.annotation_search_widget is not None:
            window.annotation_search_widget.set_indexing_busy(busy)

        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            window.statusBar().showMessage("Indexing current PDF, please wait...")
        else:
            QApplication.restoreOverrideCursor()
            window.update_actions()
        QApplication.processEvents()

    def on_reindex_progress(self, page_number: int, page_count: int, annotation_count: int) -> None:
        window = self.window
        message = f"Indexing page {page_number} / {page_count}, annotations {annotation_count}..."
        window.statusBar().showMessage(message)
        if window.annotation_search_widget is not None:
            window.annotation_search_widget.set_index_status(message, stale=True)

    def on_reindex_finished(self, count: int, warnings: list) -> None:
        window = self.window
        path = window.reindex_pdf_path
        window.log_debug(f"Reindex current PDF completed: {path} annotations={count}")
        window.log_annotation_read_warnings("Reindex annotation read warnings", warnings)
        self.set_reindex_busy(False)
        window.statusBar().showMessage(f"Indexed {count} annotations.")
        self.refresh_annotation_search_status()
        self.refresh_index_database_info()
        self.refresh_search_indexed_files()
        if window.annotation_search_widget is not None:
            window.annotation_search_widget.clear_results()

    def on_reindex_failed(self, message: str) -> None:
        window = self.window
        path = window.reindex_pdf_path
        window.log_debug(f"Reindex current PDF failed: {path}: {message}")
        self.set_reindex_busy(False)
        window.show_error("Reindex Current PDF failed", RuntimeError(message))

    def cleanup_reindex_thread(self) -> None:
        window = self.window
        if window.reindex_thread is not None:
            window.reindex_thread.deleteLater()
        window.reindex_worker = None
        window.reindex_thread = None
        window.reindex_pdf_path = None
        window.update_actions()

    def clear_annotation_index(self) -> None:
        window = self.window
        if not main_window_dialogs.confirm_clear_annotation_index(window):
            window.log_debug("Clear annotation index canceled by user")
            return

        try:
            window.annotation_index.clear_all()
            if window.annotation_search_widget is not None:
                window.annotation_search_widget.reset_search_state()
                window.annotation_search_widget.set_index_status("Annotation index is empty.", missing=True)
            self.refresh_search_indexed_files()
            self.refresh_index_database_info()
            window.log_debug("Clear annotation index completed")
            window.statusBar().showMessage("Annotation index cleared.")
            main_window_dialogs.show_information(window, "Clear Annotation Index", "Annotation index cleared.")
        except Exception as exc:
            window.log_debug(f"Clear annotation index failed: {exc}")
            window.show_error("Clear Annotation Index failed", exc)

    def show_index_database_info(self) -> None:
        window = self.window
        main_window_docks.show_index_database_info(window)
        self.refresh_index_database_info()

    def refresh_index_database_info(self) -> None:
        window = self.window
        if window.index_database_info_text is None:
            return
        try:
            window.index_database_info_text.setPlainText(self.build_index_database_info_report())
        except Exception as exc:
            window.index_database_info_text.setPlainText(f"Index database info unavailable:\n{exc}")
            window.log_debug(f"Index database info unavailable: {exc}")

    def build_index_database_info_report(self) -> str:
        window = self.window
        documents = window.annotation_index.database_info()
        total_annotations = sum(document.annotation_count for document in documents)
        lines = [
            "Index database",
            f"Path: {window.index_path()}",
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
        window = self.window
        main_window_docks.show_annotation_search(window)
        self.update_search_dock_maximize_state()
        self.refresh_search_indexed_files()
        self.refresh_annotation_search_status()

    def refresh_search_indexed_files(self) -> None:
        window = self.window
        if window.annotation_search_widget is None:
            return
        documents = window.annotation_index.database_info()
        files = [(document.path, document.file_name) for document in documents]
        window.annotation_search_widget.set_indexed_files(files)

    def search_annotations(self, keyword: str | AnnotationSearchQuery, app_type, document_paths=None) -> None:
        window = self.window
        try:
            search_response = window.annotation_index.search_with_timing(
                keyword,
                app_type,
                document_paths=document_paths,
            )
            results = search_response.results
            ui_ms = 0.0
            if window.annotation_search_widget is not None:
                ui_ms = window.annotation_search_widget.set_results(results, window.search_page_size)
            total_ms = search_response.sqlite_ms + search_response.build_ms + ui_ms
            query_summary = keyword.summary() if isinstance(keyword, AnnotationSearchQuery) else f"Keyword: {keyword}"
            window.log_debug(
                f"Search annotations: {query_summary!r} app_type={app_type!r} results={len(results)} "
                f"documents={len(document_paths) if document_paths else 'all'} "
                f"page_size={window.search_page_size} "
                f"sqlite={search_response.sqlite_ms:.1f}ms "
                f"build={search_response.build_ms:.1f}ms "
                f"ui={ui_ms:.1f}ms total={total_ms:.1f}ms"
            )
            window.statusBar().showMessage(f"Found {len(results)} indexed annotations. {query_summary}")
        except Exception as exc:
            window.log_debug(f"Search annotations failed: {exc}")
            window.show_error("Search Annotations failed", exc)

    def refresh_annotation_search_status(self) -> None:
        window = self.window
        if window.annotation_search_widget is None:
            return
        if window.pdf_path is None:
            window.annotation_search_widget.set_index_status("No PDF open.", missing=True)
            return

        try:
            status = window.annotation_index.document_status(window.pdf_path)
            window.annotation_search_widget.set_index_status(
                status.message,
                stale=status.is_stale,
                missing=not status.is_indexed,
            )
        except Exception as exc:
            window.annotation_search_widget.set_index_status(f"Index status unavailable: {exc}", missing=True)
            window.log_debug(f"Index status unavailable: {exc}")

    def on_search_dock_top_level_changed(self, floating: bool) -> None:
        window = self.window
        if not floating:
            window.annotation_search_restore_geometry = None
            window.annotation_search_maximized = False
        self.update_search_dock_maximize_state()

    def update_search_dock_maximize_state(self) -> None:
        main_window_docks.update_search_dock_maximize_state(self.window)

    def toggle_search_dock_maximized(self) -> None:
        main_window_docks.toggle_search_dock_maximized(self.window)

    def jump_to_search_result(self, document_path: str, page_index: int, xref: int) -> None:
        window = self.window
        target_path = Path(document_path)
        if not target_path.exists():
            message = f"Search result PDF was not found:\n{target_path}"
            main_window_dialogs.show_warning(window, "Search Annotations", message)
            window.log_debug(f"Search result jump failed: missing PDF {target_path}")
            return

        window.record_view_location_before_navigation()
        opened_new_tab = False
        existing_index = window.session_index_for_path(target_path)
        if existing_index is not None:
            window.set_active_session(existing_index, preserve_selection=False)
            window.log_debug(f"Search result switched to existing PDF tab: {target_path}")
        else:
            opened_new_tab = True
            if not window.open_pdf_path(target_path, page_index):
                window.log_debug(f"Search result jump failed while opening PDF: {target_path}")
                return
            window.log_debug(f"Search result opened PDF in new tab: {target_path}")

        if window.doc is None:
            return

        window.cancel_add_tool()
        window.page_index = max(0, min(page_index, len(window.doc) - 1))
        window.render_page()
        window.update_current_recent_page()
        selected = window.select_annotation_by_xref(xref, render_page=False)
        if selected:
            location = "new tab" if opened_new_tab else "existing tab"
            window.statusBar().showMessage(f"Jumped to search result on page {window.page_index + 1}.")
            window.log_debug(
                f"Search result jumped: {target_path} page={window.page_index + 1} xref={xref} {location}"
            )
            return

        message = (
            f"Search result page opened, but annotation xref={xref} was not found. "
            "The index may be stale. Reindex this PDF."
        )
        window.statusBar().showMessage(message)
        window.defer_scroll_to_page_top_left()
        window.log_debug(f"Search result xref not found: {target_path} page={window.page_index + 1} xref={xref}")
