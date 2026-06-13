from collections.abc import Callable
from pathlib import Path
import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.annotation_index import AnnotationSearchResult


class AnnotationSearchWidget(QWidget):
    search_requested = Signal(str, object)
    result_activated = Signal(str, int, int)
    maximize_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.results: list[AnnotationSearchResult] = []
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search annotation text")
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        self.type_combo.addItem("FreeText", "freetext")
        self.type_combo.addItem("Highlight", "highlight")
        self.type_combo.addItem("Square", "square")
        self.type_combo.addItem("Arrow", "arrow")
        self.type_combo.addItem("Unsupported", "unsupported")
        self.search_button = QPushButton("Search")
        self.maximize_button = QPushButton("Maximize")
        self.maximize_button.setEnabled(False)
        self.status_label = QLabel("No PDF open.")
        self.status_label.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.type_combo)
        controls.addWidget(self.search_button)
        controls.addWidget(self.maximize_button)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("File", "Page", "Type", "xref", "Text"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(controls)
        layout.addWidget(self.table)

        self.search_button.clicked.connect(self.emit_search_requested)
        self.maximize_button.clicked.connect(self.maximize_requested.emit)
        self.search_input.returnPressed.connect(self.emit_search_requested)
        self.table.itemDoubleClicked.connect(lambda _item: self.emit_result_activated())

    def emit_search_requested(self) -> None:
        self.search_requested.emit(self.search_input.text(), self.type_combo.currentData())

    def emit_result_activated(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.results):
            return
        result = self.results[row]
        self.result_activated.emit(result.document_path, result.page_index, result.xref)

    def set_results(self, results: list[AnnotationSearchResult]) -> float:
        started = time.perf_counter()
        self.results = results
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            self.table.clearContents()
            self.table.setRowCount(0)
            self.table.setRowCount(len(results))
            for row, result in enumerate(results):
                values = (
                    Path(result.file_name).name,
                    str(result.page_number),
                    f"{result.pdf_type} / {result.app_type}",
                    str(result.xref),
                    self.compact_text(result.text),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(self.tooltip_text(result, column))
                    self.table.setItem(row, column, item)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        return (time.perf_counter() - started) * 1000

    def clear_results(self) -> None:
        self.results = []
        self.table.clearSelection()
        self.table.clearContents()
        self.table.setRowCount(0)

    def reset_search_state(self) -> None:
        self.search_input.clear()
        self.type_combo.setCurrentIndex(0)
        self.clear_results()

    def set_index_status(self, text: str, stale: bool = False, missing: bool = False) -> None:
        self.status_label.setText(text)
        if stale:
            self.status_label.setStyleSheet("color: rgb(160, 90, 0);")
        elif missing:
            self.status_label.setStyleSheet("color: rgb(160, 0, 0);")
        else:
            self.status_label.setStyleSheet("color: rgb(0, 100, 0);")

    def set_indexing_busy(self, busy: bool) -> None:
        self.search_button.setEnabled(not busy)
        self.search_input.setEnabled(not busy)
        self.type_combo.setEnabled(not busy)
        if busy:
            self.set_index_status("Indexing current PDF, please wait...", stale=True)

    def set_maximize_state(self, enabled: bool, maximized: bool = False) -> None:
        self.maximize_button.setEnabled(enabled)
        self.maximize_button.setText("Restore" if maximized else "Maximize")

    def compact_text(self, text: str) -> str:
        compact = " ".join(text.split())
        if len(compact) > 220:
            return compact[:220] + "..."
        return compact

    def tooltip_text(self, result: AnnotationSearchResult, column: int) -> str:
        if column != 4:
            return result.document_path
        compact = " ".join(result.text.split())
        if len(compact) > 800:
            return compact[:800] + "..."
        return compact
