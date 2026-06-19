from __future__ import annotations

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

from app.services.freetext_batch import FreeTextMatch


class FreeTextBatchWidget(QWidget):
    search_requested = Signal(str, str)
    result_activated = Signal(int, int)
    replace_selected_requested = Signal(str)
    replace_all_requested = Signal(str)
    delete_selected_requested = Signal()
    add_selected_requested = Signal(str, str)
    delete_selected_annotation_requested = Signal()
    delete_all_annotations_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.results: list[FreeTextMatch] = []

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find FreeText content")

        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Current Page", "current_page")
        self.scope_combo.addItem("Current Document", "current_document")

        self.search_button = QPushButton("Find")
        self.status_label = QLabel("Enter text to find FreeText annotations.")
        self.status_label.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.scope_combo)
        controls.addWidget(self.search_button)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Page", "xref", "Matches", "First Pos", "Text"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Replace with")
        self.replace_selected_button = QPushButton("Replace Selected")
        self.replace_all_button = QPushButton("Replace All Results")

        replace_controls = QHBoxLayout()
        replace_controls.addWidget(self.replace_input, 1)
        replace_controls.addWidget(self.replace_selected_button)
        replace_controls.addWidget(self.replace_all_button)

        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("Add text")
        self.add_mode_combo = QComboBox()
        self.add_mode_combo.addItem("Before Match", "before")
        self.add_mode_combo.addItem("After Match", "after")
        self.add_mode_combo.addItem("End of Note", "end")
        self.add_selected_button = QPushButton("Add To Selected")
        self.delete_selected_button = QPushButton("Delete Match Text")

        add_controls = QHBoxLayout()
        add_controls.addWidget(self.add_input, 1)
        add_controls.addWidget(self.add_mode_combo)
        add_controls.addWidget(self.add_selected_button)
        add_controls.addWidget(self.delete_selected_button)

        self.delete_selected_annotation_button = QPushButton("Delete Selected Note")
        self.delete_all_annotations_button = QPushButton("Delete All Result Notes")

        delete_controls = QHBoxLayout()
        delete_controls.addStretch(1)
        delete_controls.addWidget(self.delete_selected_annotation_button)
        delete_controls.addWidget(self.delete_all_annotations_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(controls)
        layout.addWidget(self.table, 1)
        layout.addLayout(replace_controls)
        layout.addLayout(add_controls)
        layout.addLayout(delete_controls)

        self.search_button.clicked.connect(self.emit_search_requested)
        self.search_input.returnPressed.connect(self.emit_search_requested)
        self.table.itemDoubleClicked.connect(lambda _item: self.emit_result_activated())
        self.replace_selected_button.clicked.connect(self.emit_replace_selected)
        self.replace_all_button.clicked.connect(self.emit_replace_all)
        self.delete_selected_button.clicked.connect(self.delete_selected_requested.emit)
        self.add_selected_button.clicked.connect(self.emit_add_selected)
        self.delete_selected_annotation_button.clicked.connect(self.delete_selected_annotation_requested.emit)
        self.delete_all_annotations_button.clicked.connect(self.delete_all_annotations_requested.emit)

    def emit_search_requested(self) -> None:
        self.search_requested.emit(self.search_input.text(), self.scope_combo.currentData())

    def set_results(self, results: list[FreeTextMatch], warnings: list[str] | None = None) -> None:
        self.results = list(results)
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            values = (
                str(result.page_index + 1),
                str(result.xref),
                str(result.match_count),
                str(result.first_match_start),
                self.preview_text(result.text),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                self.table.setItem(row, column, item)

        warning_count = len(warnings or [])
        status = f"{len(self.results)} FreeText result(s)."
        if warning_count:
            status += f" {warning_count} annotation read warning(s)."
        self.status_label.setText(status)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

    def set_message(self, message: str) -> None:
        self.results = []
        self.table.setRowCount(0)
        self.status_label.setText(message)

    def emit_result_activated(self) -> None:
        row = self.selected_row()
        if row is None or row < 0 or row >= len(self.results):
            return
        result = self.results[row]
        self.result_activated.emit(result.page_index, result.xref)

    def selected_row(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return rows[0].row()

    def selected_result(self) -> FreeTextMatch | None:
        row = self.selected_row()
        if row is None or row < 0 or row >= len(self.results):
            return None
        return self.results[row]

    def current_keyword(self) -> str:
        return self.search_input.text()

    def current_scope(self) -> str:
        return self.scope_combo.currentData()

    def emit_replace_selected(self) -> None:
        self.replace_selected_requested.emit(self.replace_input.text())

    def emit_replace_all(self) -> None:
        self.replace_all_requested.emit(self.replace_input.text())

    def emit_add_selected(self) -> None:
        self.add_selected_requested.emit(self.add_input.text(), self.add_mode_combo.currentData())

    def preview_text(self, text: str, max_length: int = 160) -> str:
        value = " ".join(text.split())
        if len(value) <= max_length:
            return value
        return value[: max_length - 3].rstrip() + "..."
