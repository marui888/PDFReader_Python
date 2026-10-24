from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.models.index_management import IndexSource, SourceDraft
from app.services.index_management import read_snapshot, duplicate_names_report
from app.services.index_management_worker import IndexManagementWorker
from app.widgets.index_management import IndexManagementDialog


class IndexManagementController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.view = None
        self.snapshot = None
        self.drafts = []
        self.recent = []
        self.clear = False
        self.plan = None
        self.error = ""
        self.thread = None
        self.worker = None
        self.applying = False
        self.preparing = False
        self.version = 0
        self.task_version = 0
        self.pending_preview = False
        self.closing_preview = False
        self.close_after_update = False
        self.result = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(300)
        self.preview_timer.timeout.connect(self._preview)

    @Slot()
    def show(self):
        if self.view is None:
            self.view = IndexManagementDialog(self.window, self)
            self.view.add_files.connect(self.add_files)
            self.view.add_folder.connect(self.add_folder)
            self.view.add_recent.connect(self.add_recent)
            self.view.remove_new.connect(self.remove_new)
            self.view.row_changed.connect(self.row_changed)
            self.view.clear_requested.connect(self.toggle_clear)
            self.view.restore_requested.connect(self.restore)
            self.view.update_requested.connect(self.update)
            self.view.cancel_requested.connect(self.cancel)
            self.view.selection_changed.connect(self.selection_changed)
            self.view.check_requested.connect(self.edited)
            self.recent = self._load_recent()
            self.restore()
        elif not self.view.isVisible() and not self.applying and not self.preparing:
            self.restore()
        self.view.show()
        self.view.raise_()
        self.view.activateWindow()

    def _load_recent(self):
        sources, seen = [], set()
        for record in self.window.recent_index_sources:
            try:
                source = IndexSource(str(record["path"]), str(record["kind"]), bool(record.get("recursive", True)))
                if source.kind in {"file", "folder"} and source.key not in seen:
                    sources.append(source)
                    seen.add(source.key)
            except (KeyError, TypeError, ValueError, OSError):
                continue
        return sources[:30]

    def _remember(self, source):
        self.recent = [source] + [item for item in self.recent if item.key != source.key]
        self.recent = self.recent[:30]
        self.window.recent_index_sources = [item.record() for item in self.recent]
        self.window.save_app_settings()
        self.view.show_recent(self.recent)

    @Slot()
    def restore(self):
        if self.applying or self.preparing:
            return
        self.preview_timer.stop()
        self.version += 1
        if self.worker is not None:
            self.worker.cancel.set()
        try:
            self.snapshot = read_snapshot(self.window.index_path())
        except Exception as exc:
            QMessageBox.critical(self.view, "Index Management", str(exc))
            return
        self.drafts = [SourceDraft(source) for source in self.snapshot.sources]
        self.clear = False
        self.plan = None
        self.error = ""
        self.pending_preview = False
        self.view.show_recent(self.recent)
        self.view.status.setText("已读取数据库来源。添加、标记、Clear 和 Restore 均不修改索引数据库。")
        self.refresh()

    def has_intent(self):
        if self.snapshot is None:
            return False
        if self.clear:
            return bool(self.snapshot.sources or self.snapshot.documents)
        return any(item.is_new or item.delete or item.update for item in self.drafts)

    def is_dirty(self):
        return self.plan.changes if self.plan is not None else self.has_intent()

    @Slot()
    def edited(self):
        if self.applying or self.preparing:
            return
        self.version += 1
        self.plan = None
        self.error = ""
        if self.worker is not None:
            self.worker.cancel.set()
        self.pending_preview = self.has_intent()
        if self.pending_preview:
            self.preview_timer.start()
        else:
            self.preview_timer.stop()
            self.view.status.setText("没有待提交变更。")
        self.refresh()

    @Slot()
    def _preview(self):
        if self.applying or self.preparing or not self.has_intent():
            return
        if self.thread is not None:
            self.pending_preview = True
            return
        self.pending_preview = False
        self.view.status.setText("正在后台扫描所选来源并检查修改时间；索引数据库未改变…")
        self._start_task(False)

    def _start_task(self, apply, check_names=False):
        self.result = None
        self.task_version = self.version
        self.applying = apply
        self.preparing = check_names
        self.view.progress.setRange(0, 0)
        self.worker = IndexManagementWorker(
            self.window.index_path(), copy.deepcopy(self.drafts), self.clear,
            self.snapshot.source_signature, apply, self.window.extract_highlight_text_on_reindex,
            check_names=check_names,
        )
        self.worker.progress.connect(self._progress)
        self.worker.committing.connect(self._committing)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        if apply:
            # Worker and timer retain GUI-thread affinity; no MuPDF in QThread.
            self.worker.stopped.connect(self._thread_finished)
            QTimer.singleShot(0, self.worker.run)
        else:
            self.thread = QThread(self)
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.stopped.connect(self.thread.quit)
            self.worker.stopped.connect(self.worker.deleteLater)
            self.thread.finished.connect(self._thread_finished)
            self.thread.start()
        self.refresh()
        self.window.update_actions()

    @Slot(object)
    def _completed(self, result):
        if self.applying or self.preparing:
            self.result = result
        elif self.task_version == self.version:
            self.plan = result
            self.error = ""
            self.view.status.setText("检查完成。" if result.changes else "检查完成：无需修改索引数据库，* 已取消。")
            self.refresh()

    @Slot(str)
    def _failed(self, message):
        if self.applying or self.preparing or self.task_version == self.version:
            self.error = message
            self.view.status.setText(message)
            self.view.log.appendPlainText(message)

    @Slot(str, int, int)
    def _progress(self, message, value, maximum):
        self.view.status.setText(message)
        self.view.progress.setRange(0, max(1, maximum))
        self.view.progress.setValue(value)

    @Slot()
    def _committing(self):
        self.view.status.setText("全部 PDF 已准备完成，正在原子提交索引数据库…")
        self.view.cancel_button.setEnabled(False)

    @Slot()
    def _thread_finished(self):
        was_applying = self.applying
        was_preparing = self.preparing
        result = self.result
        thread = self.thread
        worker = self.worker
        self.thread = None
        self.worker = None
        self.applying = False
        self.preparing = False
        if thread is not None:
            thread.deleteLater()
        elif worker is not None:
            worker.deleteLater()
        if was_preparing:
            self.view.progress.setRange(0, 100)
            self.view.progress.setValue(0)
            self.refresh()
            if result is None:
                self.view.status.setText(self.error or "重名检查已取消，Update 未开始。")
            elif result:
                box = QMessageBox(self.view)
                box.setWindowTitle("发现重名 PDF")
                box.setIcon(QMessageBox.Icon.Warning)
                box.setText("保留来源中存在同名 PDF，Update 未开始。请整理来源后重试。")
                box.setInformativeText(f"共 {len(result)} 组重名文件；展开详细信息查看完整路径。")
                box.setDetailedText(duplicate_names_report(result))
                box.exec()
            else:
                self._confirm_update()
            if not self.applying:
                self.close_after_update = False
            return
        self.view.progress.setRange(0, 100)
        self.view.progress.setValue(100 if was_applying and result is not None else 0)
        if was_applying:
            if result is not None:
                self.restore()
                message = f"Update 完成：生成 {result['indexed']} 个 PDF 索引，删除 {result['deleted']} 个 PDF 索引。"
                self.view.status.setText(message)
                self.view.log.appendPlainText(message)
                for warning in result["warnings"]:
                    self.view.log.appendPlainText(warning)
                try:
                    self._refresh_search()
                except Exception as exc:
                    self.view.log.appendPlainText(f"索引已提交，但搜索界面刷新失败，请重新打开搜索窗口：{exc}")
                if self.close_after_update:
                    self.close_after_update = False
                    self.view.close()
            else:
                self.close_after_update = False
                self.plan = None
                # Keep drafts, clear state and the error for correction/retry.
        if self.pending_preview and not was_applying:
            self.preview_timer.start()
        self.refresh()
        self.window.update_actions()
        if self.closing_preview:
            self.closing_preview = False
            self.view.close()

    def _refresh_search(self):
        controller = self.window.search_controller
        if self.window.annotation_search_widget is not None:
            self.window.annotation_search_widget.clear_results()
        controller.refresh_search_indexed_files()
        controller.refresh_annotation_search_status()
        controller.refresh_index_database_info()

    @Slot()
    def add_files(self):
        if self.clear or self.applying or self.preparing:
            return
        paths, _ = QFileDialog.getOpenFileNames(self.view, "添加 PDF 来源", "", "PDF files (*.pdf)")
        for path in paths:
            self._add_source(IndexSource(str(Path(path).resolve()), "file", False))
        if paths:
            self.edited()

    @Slot()
    def add_folder(self):
        if self.clear or self.applying or self.preparing:
            return
        path = QFileDialog.getExistingDirectory(self.view, "添加文件夹来源")
        if not path:
            return
        answer = QMessageBox.question(self.view, "包含子目录", "是否扫描此文件夹的所有子目录？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return
        self._add_source(IndexSource(str(Path(path).resolve()), "folder", answer == QMessageBox.StandardButton.Yes))
        self.edited()

    def _add_source(self, source):
        for row, item in enumerate(self.drafts):
            if item.source.key == source.key:
                item.delete = False
                self._remember(item.source)
                self.view.source_table.selectRow(row)
                return
        self.drafts.append(SourceDraft(source, is_new=True))
        self._remember(source)

    @Slot(object)
    def add_recent(self, rows):
        if self.clear or self.applying or self.preparing:
            return
        chosen = [self.recent[row] for row in rows if 0 <= row < len(self.recent)]
        for source in chosen:
            self._add_source(source)
        self.edited()

    @Slot()
    def remove_new(self):
        if self.clear or self.applying or self.preparing:
            return
        rows = {index.row() for index in self.view.source_table.selectionModel().selectedRows()}
        self.drafts = [item for row, item in enumerate(self.drafts) if row not in rows or not item.is_new]
        self.edited()

    @Slot(int, int, bool)
    def row_changed(self, row, column, checked):
        if self.clear or self.applying or self.preparing or not 0 <= row < len(self.drafts):
            return
        draft = self.drafts[row]
        if draft.is_new:
            return
        if column == 6:
            draft.delete = checked
            if checked:
                draft.update = False
        elif column == 7:
            draft.update = checked
            if checked:
                draft.delete = False
        elif column == 8:
            draft.check_modified = checked
        self.edited()

    @Slot()
    def toggle_clear(self):
        if not self.applying and not self.preparing:
            self.clear = not self.clear
            self.edited()

    @Slot()
    def update(self):
        if self.window.bulk_annotation_controller.dialog is not None:
            return
        if self.applying or self.preparing or self.thread is not None or self.plan is None or not self.plan.changes:
            return
        self.preview_timer.stop()
        self.error = ""
        self.view.status.setText("正在检查所有保留来源中的 PDF 文件名，阅读窗口尚未关闭…")
        self._start_task(False, check_names=True)

    def _confirm_update(self):
        message = ("清空全部来源记录、关联关系和批注索引。\n" if self.clear else "按当前列表提交以下变更：\n")
        message += self.plan.summary() + "\n\n磁盘上的 PDF 不会删除或修改。是否执行 Update？"
        if QMessageBox.question(self.view, "确认 Update", message,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if not self._close_reading_sessions():
            return
        self.preview_timer.stop()
        self.error = ""
        self.view.progress.setValue(0)
        self.view.log.appendPlainText("开始 Update。所有处理先写入临时索引，成功后整批提交。")
        self._start_task(True)

    def _close_reading_sessions(self):
        workspace = self.window.reading_workspace
        if self.window.reindex_thread is not None:
            QMessageBox.information(self.view, "Index Management", "请先等待已有 PDF 后台任务结束。")
            return False
        if not workspace.sessions():
            if any(pane.doc is not None for pane in workspace.panes):
                QMessageBox.warning(self.view, "Index Management", "PDF 尚未全部关闭，Update 未开始。")
                return False
            return True
        self.preparing = True
        try:
            box = QMessageBox(self.view)
            box.setWindowTitle("Update 前关闭全部 PDF")
            box.setText("Update 需要先关闭左右窗口中的全部 PDF。\n"
                        "未保存修改将逐一询问；完成后不会自动重新打开 PDF。\n"
                        "若中途取消，Update 不执行，已关闭的窗口保持关闭。")
            proceed = box.addButton("关闭全部并继续", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() != proceed:
                return False
            self.view.setEnabled(False)
            for pane in list(workspace.panes):
                if not pane.document_controller.close_all_document_tabs():
                    return False
            if workspace.sessions() or any(pane.doc is not None for pane in workspace.panes):
                QMessageBox.warning(self.view, "Index Management", "PDF 尚未全部关闭，Update 未开始。")
                return False
            return True
        finally:
            self.preparing = False
            self.view.setEnabled(True)

    @Slot()
    def cancel(self):
        if self.worker is not None:
            self.worker.cancel.set()
            self.view.status.setText("正在取消，等待当前处理安全退出…")

    def is_path_busy(self, path):
        if not self.applying or path is None:
            return False
        return True  # No PDF may be opened or saved while the main-thread index owns MuPDF.

    def refresh(self):
        if self.view is None or self.snapshot is None:
            return
        checking = self.thread is not None and not self.applying or self.preview_timer.isActive()
        if not self.clear:
            self.view.show_sources(self.drafts, self.snapshot, self.plan)
        self.view.set_state(self.applying or self.preparing, self.clear, self.is_dirty(),
                            self.plan is not None and self.plan.changes and not checking, checking)
        if self.plan is not None:
            self.view.summary.setText(self.plan.summary())
        elif self.has_intent():
            self.view.summary.setText(self.error or "有待执行操作，正在检查实际变更…")
        else:
            self.view.summary.setText("没有待提交变更。")
        docs = self.snapshot.documents.values()
        self.view.database_info.setPlainText(
            f"数据库：{self.window.index_path()}\n来源：{len(self.snapshot.sources)}\n"
            f"PDF：{len(self.snapshot.documents)}\n批注：{sum(d['annotation_count'] for d in docs)}\n"
            "只有 Update 修改索引数据库；最近来源历史保存在 APP 设置中。"
        )
        self.selection_changed()

    @Slot()
    def selection_changed(self):
        if self.view is None or self.view.rendering:
            return
        rows = [index.row() for index in self.view.source_table.selectionModel().selectedRows()]
        self.view.remove_button.setEnabled(
            not self.clear and not self.applying and not self.preparing
            and any(0 <= row < len(self.drafts) and self.drafts[row].is_new for row in rows)
        )
        if not rows or rows[0] >= len(self.drafts):
            self.view.details.setPlainText("选择当前来源查看其 PDF 索引明细。")
            return
        source = self.drafts[rows[0]].source
        members = self.plan.members.get(source.key, set()) if self.plan else self.snapshot.members.get(source.key, set())
        lines = [source.path, f"PDF 数：{len(members)}", ""]
        for key in sorted(members):
            row = self.snapshot.documents.get(key)
            if row:
                stamp = datetime.fromtimestamp(row["modified_time"]).isoformat(sep=" ", timespec="seconds") if row["modified_time"] else "—"
                lines.extend([row["path"], f"  批注 {row['annotation_count']} | 索引 {row['indexed_at']} | 索引时的文件修改时间 {stamp}"])
            else:
                lines.append((self.plan.files.get(key, key) if self.plan else key) + "（待建立）")
        self.view.details.setPlainText("\n".join(lines))

    def request_close(self):
        if self.preparing:
            return False
        if self.applying:
            QMessageBox.information(self.view, "任务仍在运行", "请先取消任务并等待安全退出，再关闭窗口。")
            return False
        self.preview_timer.stop()
        if self.thread is not None:
            self.version += 1
            self.pending_preview = False
            self.closing_preview = True
            self.cancel()
            return False
        if not self.is_dirty():
            return True
        box = QMessageBox(self.view)
        box.setWindowTitle("未提交的索引变更")
        box.setText("当前来源列表有未提交变更。")
        update = box.addButton("Update", QMessageBox.ButtonRole.AcceptRole)
        update.setEnabled(self.plan is not None and self.plan.changes)
        discard = box.addButton("放弃更改", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == discard:
            self.pending_preview = False
            return True
        if box.clickedButton() == update:
            self.update()
            self.close_after_update = self.applying or self.preparing
        elif self.plan is None and self.has_intent() and not self.error:
            self.preview_timer.start()
        return False

    def can_close_app(self):
        if self.applying or self.preparing:
            if self.view is not None:
                self.view.show()
                if self.applying:
                    self.request_close()
            return False
        if self.view is None or (not self.view.isVisible() and self.thread is None):
            return True
        return self.view.close()
