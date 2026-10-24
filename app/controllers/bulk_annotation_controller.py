from pathlib import Path
from time import monotonic
import traceback

from PySide6.QtCore import QObject, QTimer, Slot, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.bulk_highlight_edit import edit_highlights
from app.widgets.bulk_annotation_edit import BulkAnnotationEditDialog


class BulkAnnotationController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.rendering_suspended = False
        self.dialog = None

    @Slot()
    def show(self):
        window = self.window
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        source = window.reading_workspace.active
        if source.document is None or source.doc is None:
            return
        if (window.index_management_controller.applying or window.index_management_controller.preparing
                or window.reindex_thread is not None):
            QMessageBox.information(window, "Edit Annotation", "请先等待索引任务结束。")
            return
        self.source = source
        self.document = source.document
        self.page_index = source.page_index
        dialog = BulkAnnotationEditDialog(window, Path(self.document.path), self.page_index)
        self.dialog = dialog
        dialog.apply_requested.connect(self.apply)

        def center():
            frame = dialog.frameGeometry()
            frame.moveCenter(window.frameGeometry().center())
            dialog.move(frame.topLeft())

        QTimer.singleShot(0, dialog, center)
        try:
            dialog.exec()
        finally:
            self.dialog = None
            dialog.deleteLater()

    @Slot()
    def apply(self):
        if self.busy:
            return
        window = self.window
        workspace = window.reading_workspace
        source, document = self.source, self.document
        if source.document is not document or document.doc is None or document.doc.is_closed:
            QMessageBox.warning(self.dialog, "Edit Annotation", "目标文档已改变，请关闭窗口后重试。")
            return
        if (window.index_management_controller.applying or window.index_management_controller.preparing
                or window.reindex_thread is not None):
            QMessageBox.warning(self.dialog, "Edit Annotation", "索引任务仍在运行，不能执行修改。")
            return
        color, opacity, scope = self.dialog.options()
        if color is None and opacity is None:
            return
        original_doc = document.doc
        self.busy = True
        self.dialog.set_busy(True)
        workspace_enabled = workspace.isEnabled()
        result = None
        error = None
        error_details = ""
        touched = False
        changed = 0
        cursor_set = False
        last_progress = [0.0, ""]

        def progress(text, value=0, maximum=0):
            phase = text.split("：", 1)[0]
            now = monotonic()
            if maximum and value != maximum and phase == last_progress[1] and now - last_progress[0] < 0.1:
                return
            last_progress[:] = [now, phase]
            self.dialog.status.setText(text)
            self.dialog.progress.setRange(0, maximum)
            self.dialog.progress.setValue(value)
            window.statusBar().showMessage(f"Edit Annotation — {document.path.name}：{text}")
            # Update feedback without pumping events or reentering PDF rendering.
            self.dialog.status.repaint()
            self.dialog.progress.repaint()
            window.statusBar().repaint()

        try:
            # Commit only active inline text editors, just like ordinary editing.
            # Existing unsaved document changes are kept; there is no save/discard prompt.
            for pane, session in workspace.references(document):
                if pane.session is session and pane.inline_freetext_editor.is_active():
                    pane.inline_freetext_editor.accept_current()
                if pane.session is session:
                    pane.document_controller.save_active_session_state()
            self.rendering_suspended = True
            workspace.setEnabled(False)
            for pane in workspace.panes:
                pane.stop_continuous_scroll_timer()
                pane.canvas_controller.annotation_locator.stop()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cursor_set = True

            def confirm(targets, matching):
                nonlocal cursor_set
                QApplication.restoreOverrideCursor()
                cursor_set = False
                changes = []
                if color is not None:
                    changes.append("颜色")
                if opacity is not None:
                    changes.append(f"Opacity = {opacity:.0%}")
                message = (f"文件：{document.path}\n"
                           f"范围：{'整篇文档' if scope == 'document' else f'第 {self.page_index + 1} 页'}\n"
                           f"Highlight 共 {matching} 个，实际修改 {targets} 个。\n"
                           f"修改：{'、'.join(changes)}；未选择的属性保留各自原值。\n\n"
                           "此批量操作不支持 Undo，并将清除该文档原有撤销记录。\n"
                           "只修改当前内存文档，不关闭 session，不自动保存到磁盘。\n"
                           "已有未保存编辑会保留；关闭并放弃修改将一并丢弃该文档的所有未保存编辑。\n"
                           "是否继续？")
                answer = QMessageBox.question(self.dialog, "批量修改确认", message,
                                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                              QMessageBox.StandardButton.No)
                if document.doc is not original_doc or source.document is not document:
                    raise RuntimeError("目标文档在确认期间发生变化，未执行批量修改。")
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                cursor_set = True
                return answer == QMessageBox.StandardButton.Yes

            with workspace.sync.hold(), workspace.preserve_tool_mode():
                result = edit_highlights(original_doc, self.page_index if scope == "page" else None,
                                         color, opacity, confirm, progress, log=window.log_debug)
                touched = result["touched"]
                changed = result["changed"]
        except Exception as exc:
            error = str(exc)
            touched = touched or bool(getattr(exc, "touched", False))
            changed = max(changed, int(getattr(exc, "changed", 0)))
            error_details = getattr(exc, "details", "") or traceback.format_exc()
            window.log_debug(f"Bulk Highlight in-memory failed: changed={changed} touched={touched}\n"
                             f"{error}\n{error_details}")
            traceback.clear_frames(exc.__traceback__)
        finally:
            if cursor_set:
                QApplication.restoreOverrideCursor()
            self.rendering_suspended = False
            try:
                if touched:
                    # Same persistence semantics as FreeText Replace All.
                    source.mark_dirty()
                    source.clear_undo()
                self._refresh_document_views(document)
            except Exception as exc:
                refresh_details = traceback.format_exc()
                error_details += "\n\n刷新阅读视图时的异常：\n" + refresh_details
                error = (error + "\n\n" if error else "") + f"刷新阅读视图失败：{exc}\n磁盘 PDF 未写入。"
                window.log_debug(f"Bulk Highlight refresh failed:\n{refresh_details}")
                traceback.clear_frames(exc.__traceback__)
            workspace.setEnabled(workspace_enabled)
            self.busy = False
            self.dialog.set_busy(False)
            self.dialog.progress.setRange(0, 100)
            self.dialog.progress.setValue(100 if result and not result["cancelled"] and not error else 0)
            window.update_actions()
        if error:
            self.dialog.status.setText(error)
            window.statusBar().showMessage("Edit Annotation 未正常完成；请检查内存中的修改，磁盘 PDF 未写入。")
            box = QMessageBox(self.dialog)
            box.setWindowTitle("Edit Annotation")
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(error)
            box.setInformativeText("本操作未自动保存。若已产生部分修改，文档会标记为未保存；"
                                   "放弃修改会同时丢弃该文档其他未保存编辑。")
            box.setDetailedText(error_details)
            box.exec()
        elif result is not None:
            message = ("已取消，未执行批量修改。" if result["cancelled"] else
                       f"已在内存中修改 {changed} 个 Highlight；请手动保存。" if changed else
                       f"找到 {result['matching']} 个 Highlight，无需修改。")
            self.dialog.status.setText(message)
            window.statusBar().showMessage(message)

    def _refresh_document_views(self, document):
        """Reuse FreeText's refresh path; retain every session and its exact view."""
        workspace = self.window.reading_workspace
        if document.doc is None or document.doc.is_closed:
            return
        with workspace.sync.hold():
            for pane, session in workspace.references(document):
                pane.document_controller.update_document_tab_title(pane.sessions.index(session))
                if pane.session is not session:
                    continue
                pane.clear_continuous_annotation_cache()
                pane.canvas_controller.refresh_after_batch_annotation_change(preserve_selection=True)
            # All active views were refreshed, including any pending inline edits.
            # Leave other documents' queued notifications untouched.
            workspace.pending_documents.pop(id(document), None)
