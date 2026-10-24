from __future__ import annotations

from threading import Event
from time import perf_counter

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.services.index_management import (
    IndexTaskCancelled, make_plan, apply_plan_steps, check_cancel, find_duplicate_source_names,
)


class IndexManagementWorker(QObject):
    """Read-only previews run in QThread; PDF updates stay on the GUI thread."""
    progress = Signal(str, int, int)
    committing = Signal()
    completed = Signal(object)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, db_path, drafts, clear, source_keys, apply=False, extract=False, check_names=False):
        super().__init__()
        self.db_path = db_path
        self.drafts = drafts
        self.clear = clear
        self.source_keys = source_keys
        self.apply = apply
        self.extract = extract
        self.check_names = check_names
        self.cancel = Event()
        self.steps = None
        self.last_progress = 0.0
        self.pending_progress = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)

    @Slot()
    def run(self):
        if self.apply:
            self.steps = apply_plan_steps(
                self.db_path, self.drafts, self.clear, self.source_keys, self.extract,
                self.cancel, self._progress, self._committing,
            )
            self.timer.start(0)
            return
        try:
            result = (find_duplicate_source_names(self.drafts, self.clear, self.cancel)
                      if self.check_names else
                      make_plan(self.db_path, self.drafts, self.clear, self.source_keys, self.cancel))
            if not self.cancel.is_set():
                self.completed.emit(result)
        except IndexTaskCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.stopped.emit()

    def _progress(self, text, value, maximum):
        self.pending_progress = (text, value, maximum)
        if perf_counter() - self.last_progress >= 0.1:
            self._flush_progress()

    def _flush_progress(self):
        if self.pending_progress is not None:
            self.progress.emit(*self.pending_progress)
            self.pending_progress = None
            self.last_progress = perf_counter()

    def _committing(self):
        self._flush_progress()
        self.committing.emit()

    @Slot()
    def _advance(self):
        try:
            check_cancel(self.cancel)
            # One page/stage per turn; no nested processEvents or PDF reentrancy.
            next(self.steps)
        except StopIteration as done:
            self._finish(result=done.value)
        except IndexTaskCancelled:
            self._finish(error="任务已取消，数据库未改变。")
        except Exception as exc:
            self._finish(error=str(exc))
        else:
            self.timer.start(0)

    def _finish(self, result=None, error=None):
        self.timer.stop()
        try:
            self.steps.close()
        except Exception as exc:
            if error is None:
                error = str(exc)
        finally:
            self.steps = None
        self._flush_progress()
        if error is not None:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
        self.stopped.emit()
