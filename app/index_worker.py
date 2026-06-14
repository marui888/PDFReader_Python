from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QObject, Signal, Slot

from app.annotation_index import AnnotationIndex


class ReindexWorker(QObject):
    progress = Signal(int, int, int)
    finished = Signal(int, list)
    failed = Signal(str)

    def __init__(self, db_path: Path, pdf_path: Path, extract_highlight_text: bool) -> None:
        super().__init__()
        self.db_path = db_path
        self.pdf_path = pdf_path
        self.extract_highlight_text = extract_highlight_text

    @Slot()
    def run(self) -> None:
        doc = None
        try:
            doc = fitz.open(self.pdf_path)
            index = AnnotationIndex(self.db_path)
            count = index.reindex_document(
                doc,
                self.pdf_path,
                extract_highlight_text=self.extract_highlight_text,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(count, index.last_warnings)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if doc is not None:
                doc.close()
