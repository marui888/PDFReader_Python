from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QGridLayout,
    QVBoxLayout,
    QWidget,
)

from app.services.freetext_batch import FreeTextMatch
from app.services.ui_contrast import table_contrast_stylesheet
from app.widgets.recent_term_input import RecentTermLineEdit


class FreeTextBatchWidget(QWidget):
    search_requested = Signal(list, str, list, str, str)
    result_activated = Signal(int, int)
    replace_selected_requested = Signal(object)
    replace_all_requested = Signal(object)
    replace_preview_requested = Signal(object)
    regex_help_requested = Signal()
    delete_selected_requested = Signal()
    add_selected_requested = Signal(str, str)
    delete_selected_annotation_requested = Signal()
    delete_all_annotations_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.results: list[FreeTextMatch] = []
        self.last_auto_replace_target = ""

        self.include_mode_combo = QComboBox()
        self.include_mode_combo.addItem("AND", "and")
        self.include_mode_combo.addItem("OR", "or")
        self.include_inputs = [RecentTermLineEdit(self) for _index in range(4)]
        for index, input_box in enumerate(self.include_inputs, start=1):
            input_box.setPlaceholderText(f"term{index}")
        self.include_whole_word_checks = [QCheckBox("W") for _index in range(4)]
        for checkbox in self.include_whole_word_checks:
            checkbox.setToolTip("whole word")
            checkbox.setFixedWidth(34)
        self.include_regex_checks = [QCheckBox("R") for _index in range(4)]
        for checkbox in self.include_regex_checks:
            checkbox.setToolTip("regex")
            checkbox.setFixedWidth(34)

        self.exclude_mode_combo = QComboBox()
        self.exclude_mode_combo.addItem("AND", "and")
        self.exclude_mode_combo.addItem("OR", "or")
        self.exclude_mode_combo.setCurrentIndex(1)
        self.exclude_inputs = [RecentTermLineEdit(self) for _index in range(2)]
        for index, input_box in enumerate(self.exclude_inputs, start=1):
            input_box.setPlaceholderText(f"term{index}")

        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Page", "current_page")
        self.scope_combo.addItem("Document", "current_document")
        self.scope_combo.setCurrentIndex(1)
        self.scope_combo.setFixedWidth(92)

        self.search_button = QPushButton("Find")
        self.save_query_button = QPushButton("Save")
        self.load_query_button = QPushButton("Load")
        for button in (self.save_query_button, self.load_query_button):
            button.setFixedWidth(46)
        self.status_label = QLabel("Enter text to find FreeText annotations.")
        self.status_label.setWordWrap(True)

        include_header = QHBoxLayout()
        include_header.addWidget(QLabel("Including:"))
        include_header.addWidget(QLabel("Mode:"))
        include_header.addWidget(self.include_mode_combo)
        self.clear_include_button = QPushButton("Clear")
        self.clear_include_button.setFixedWidth(46)
        include_header.addWidget(self.clear_include_button)
        include_header.addWidget(self.save_query_button)
        include_header.addWidget(self.load_query_button)
        include_header.addStretch(1)

        include_row_1 = QHBoxLayout()
        include_row_1.addWidget(self.include_inputs[0], 1)
        include_row_1.addWidget(self.include_whole_word_checks[0])
        include_row_1.addWidget(self.include_regex_checks[0])
        include_row_1.addWidget(self.include_inputs[1], 1)
        include_row_1.addWidget(self.include_whole_word_checks[1])
        include_row_1.addWidget(self.include_regex_checks[1])

        include_row_2 = QHBoxLayout()
        include_row_2.addWidget(self.include_inputs[2], 1)
        include_row_2.addWidget(self.include_whole_word_checks[2])
        include_row_2.addWidget(self.include_regex_checks[2])
        include_row_2.addWidget(self.include_inputs[3], 1)
        include_row_2.addWidget(self.include_whole_word_checks[3])
        include_row_2.addWidget(self.include_regex_checks[3])

        exclude_header = QHBoxLayout()
        exclude_header.addWidget(QLabel("Excluding:"))
        exclude_header.addWidget(QLabel("Mode:"))
        exclude_header.addWidget(self.exclude_mode_combo)
        self.clear_exclude_button = QPushButton("Clear")
        self.clear_exclude_button.setFixedWidth(46)
        exclude_header.addWidget(self.clear_exclude_button)
        exclude_header.addStretch(1)

        exclude_row = QHBoxLayout()
        exclude_row.addWidget(self.exclude_inputs[0], 1)
        exclude_row.addWidget(self.exclude_inputs[1], 1)

        search_controls = QHBoxLayout()
        search_controls.addWidget(self.scope_combo)
        search_controls.addWidget(self.search_button)
        search_controls.addStretch(1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Text", "Page", "xref", "Matches", "First Pos"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self.replace_target_input = QLineEdit()
        self.replace_target_input.setPlaceholderText("Replace target")
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Replace with")
        self.replace_whole_word_checkbox = QCheckBox("Whole")
        self.replace_regex_checkbox = QCheckBox("Regex")
        self.replace_selected_button = QPushButton("Replace")
        self.replace_all_button = QPushButton("Replace All")
        self.replace_preview_button = QPushButton("Preview...")
        self.regex_help_button = QPushButton("Regex...")

        replace_controls = QGridLayout()
        replace_controls.addWidget(QLabel("Replace:"), 0, 0)
        replace_controls.addWidget(self.replace_target_input, 0, 1)
        replace_controls.addWidget(QLabel("With:"), 0, 2)
        replace_controls.addWidget(self.replace_input, 0, 3)
        replace_mode_controls = QHBoxLayout()
        replace_mode_controls.addWidget(self.replace_whole_word_checkbox)
        replace_mode_controls.addWidget(self.replace_regex_checkbox)
        replace_mode_controls.addStretch(1)
        replace_button_controls = QHBoxLayout()
        replace_button_controls.addWidget(self.replace_selected_button)
        replace_button_controls.addWidget(self.replace_all_button)
        replace_button_controls.addWidget(self.replace_preview_button)
        replace_button_controls.addWidget(self.regex_help_button)
        replace_button_controls.addStretch(1)

        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("Add text")
        self.add_mode_combo = QComboBox()
        self.add_mode_combo.addItem("Before Match", "before")
        self.add_mode_combo.addItem("After Match", "after")
        self.add_mode_combo.addItem("End of Note", "end")
        self.add_selected_button = QPushButton("Add")
        self.delete_selected_button = QPushButton("Del Text")

        add_controls = QGridLayout()
        add_controls.addWidget(QLabel("Add"), 0, 0)
        add_controls.addWidget(self.add_input, 0, 1, 1, 3)
        add_button_controls = QHBoxLayout()
        add_button_controls.addWidget(self.add_mode_combo)
        add_button_controls.addWidget(self.add_selected_button)
        add_button_controls.addStretch(1)

        self.delete_selected_annotation_button = QPushButton("Del Note")
        self.delete_all_annotations_button = QPushButton("Del All Notes")

        delete_controls = QHBoxLayout()
        delete_controls.addWidget(QLabel("Delete"))
        delete_controls.addWidget(self.delete_selected_button)
        delete_controls.addWidget(self.delete_selected_annotation_button)
        delete_controls.addWidget(self.delete_all_annotations_button)
        delete_controls.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(include_header)
        layout.addLayout(include_row_1)
        layout.addLayout(include_row_2)
        layout.addLayout(exclude_header)
        layout.addLayout(exclude_row)
        layout.addLayout(search_controls)
        layout.addWidget(self.table, 1)
        layout.addLayout(replace_controls)
        layout.addLayout(replace_mode_controls)
        layout.addLayout(replace_button_controls)
        layout.addLayout(add_controls)
        layout.addLayout(add_button_controls)
        layout.addLayout(delete_controls)

        self.search_button.clicked.connect(self.emit_search_requested)
        self.save_query_button.clicked.connect(self.save_query_to_file)
        self.load_query_button.clicked.connect(self.load_query_from_file)
        self.clear_include_button.clicked.connect(self.clear_include_terms)
        self.clear_exclude_button.clicked.connect(self.clear_exclude_terms)
        for input_box in self.include_inputs + self.exclude_inputs:
            input_box.returnPressed.connect(self.emit_search_requested)
        for input_box in self.include_inputs:
            input_box.textChanged.connect(self.sync_replace_target_from_include)
        for index, checkbox in enumerate(self.include_regex_checks):
            checkbox.toggled.connect(lambda checked, item_index=index: self.on_include_regex_toggled(item_index, checked))
        self.replace_regex_checkbox.toggled.connect(self.on_replace_regex_toggled)
        self.replace_target_input.textEdited.connect(self.mark_replace_target_touched)
        self.table.itemDoubleClicked.connect(lambda _item: self.emit_result_activated())
        self.replace_selected_button.clicked.connect(self.emit_replace_selected)
        self.replace_all_button.clicked.connect(self.emit_replace_all)
        self.replace_preview_button.clicked.connect(self.emit_replace_preview)
        self.regex_help_button.clicked.connect(self.regex_help_requested.emit)
        self.delete_selected_button.clicked.connect(self.delete_selected_requested.emit)
        self.add_selected_button.clicked.connect(self.emit_add_selected)
        self.delete_selected_annotation_button.clicked.connect(self.delete_selected_annotation_requested.emit)
        self.delete_all_annotations_button.clicked.connect(self.delete_all_annotations_requested.emit)
        self.apply_ui_contrast(35)

    def apply_ui_contrast(self, contrast: int) -> None:
        self.table.setStyleSheet(table_contrast_stylesheet(contrast))

    def emit_search_requested(self) -> None:
        self.search_requested.emit(
            self.current_include_conditions(),
            self.current_include_mode(),
            self.current_exclude_terms(),
            self.current_exclude_mode(),
            self.scope_combo.currentData(),
        )

    def set_results(self, results: list[FreeTextMatch], warnings: list[str] | None = None) -> None:
        self.results = list(results)
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            values = (
                self.preview_text(result.text),
                str(result.page_index + 1),
                str(result.xref),
                str(result.match_count),
                str(result.first_match_start),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                self.table.setItem(row, column, item)

        warning_count = len(warnings or [])
        match_count = sum(result.match_count for result in self.results)
        match_text = str(match_count) if match_count > 0 else "-"
        status = f"FreeText: {len(self.results)} | Matches: {match_text}"
        if warning_count:
            status += f" {warning_count} annotation read warning(s)."
        self.status_label.setText(status)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def set_message(self, message: str) -> None:
        self.results = []
        self.table.setRowCount(0)
        self.status_label.setText(message)

    def set_read_only(self, read_only: bool) -> None:
        """Availability depends on document/task state, not the reading mode."""
        for widget in (
            self.replace_selected_button,
            self.replace_all_button,
            self.replace_preview_button,
            self.add_selected_button,
            self.delete_selected_button,
            self.delete_selected_annotation_button,
            self.delete_all_annotations_button,
        ):
            widget.setEnabled(not read_only)

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
        return self.replace_target_input.text().strip()

    def current_exclude_keyword(self) -> str:
        for term in self.current_exclude_terms():
            if term:
                return term
        return ""

    def current_include_terms(self) -> list[str]:
        return [input_box.text().strip() for input_box in self.include_inputs if input_box.text().strip()]

    def current_include_conditions(self) -> list[dict]:
        conditions = []
        for input_box, whole_word_checkbox, regex_checkbox in zip(
            self.include_inputs,
            self.include_whole_word_checks,
            self.include_regex_checks,
        ):
            text = input_box.text().strip()
            if text:
                conditions.append(
                    {
                        "text": text,
                        "whole_word": whole_word_checkbox.isChecked() and not regex_checkbox.isChecked(),
                        "regex": regex_checkbox.isChecked(),
                    }
                )
        return conditions

    def current_include_mode(self) -> str:
        return self.include_mode_combo.currentData()

    def current_exclude_terms(self) -> list[str]:
        return [input_box.text().strip() for input_box in self.exclude_inputs if input_box.text().strip()]

    def current_exclude_mode(self) -> str:
        return self.exclude_mode_combo.currentData()

    def current_scope(self) -> str:
        return self.scope_combo.currentData()

    def mark_replace_target_touched(self) -> None:
        if self.replace_target_input.text().strip() != self.last_auto_replace_target:
            self.last_auto_replace_target = ""

    def first_include_term(self) -> str:
        for input_box in self.include_inputs:
            value = input_box.text().strip()
            if value:
                return value
        return ""

    def sync_replace_target_from_include(self) -> None:
        current_target = self.replace_target_input.text().strip()
        if current_target and current_target != self.last_auto_replace_target:
            return
        next_target = self.first_include_term()
        self.last_auto_replace_target = next_target
        self.replace_target_input.setText(next_target)

    def clear_include_terms(self) -> None:
        for input_box in self.include_inputs:
            input_box.clear()
        for checkbox in self.include_whole_word_checks:
            checkbox.setChecked(False)
        for checkbox in self.include_regex_checks:
            checkbox.setChecked(False)
        if not self.replace_target_input.text().strip() or self.replace_target_input.text().strip() == self.last_auto_replace_target:
            self.last_auto_replace_target = ""
            self.replace_target_input.clear()

    def clear_exclude_terms(self) -> None:
        for input_box in self.exclude_inputs:
            input_box.clear()

    def query_state(self) -> dict:
        return {
            "include_mode": self.current_include_mode(),
            "include_terms": [input_box.text() for input_box in self.include_inputs],
            "include_whole_words": [checkbox.isChecked() for checkbox in self.include_whole_word_checks],
            "include_regexes": [checkbox.isChecked() for checkbox in self.include_regex_checks],
            "exclude_mode": self.current_exclude_mode(),
            "exclude_terms": [input_box.text() for input_box in self.exclude_inputs],
            "scope": self.current_scope(),
            "replace_target": self.replace_target_input.text(),
            "replace_with": self.replace_input.text(),
            "replace_whole_word": self.replace_whole_word_checkbox.isChecked(),
            "replace_regex": self.replace_regex_checkbox.isChecked(),
        }

    def apply_query_state(self, state: dict) -> None:
        self.include_mode_combo.setCurrentIndex(max(0, self.include_mode_combo.findData(state.get("include_mode", "and"))))
        self.exclude_mode_combo.setCurrentIndex(max(0, self.exclude_mode_combo.findData(state.get("exclude_mode", "or"))))
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(state.get("scope", "current_document"))))
        include_terms = list(state.get("include_terms", []))
        include_whole_words = list(state.get("include_whole_words", []))
        include_regexes = list(state.get("include_regexes", []))
        exclude_terms = list(state.get("exclude_terms", []))
        for index, input_box in enumerate(self.include_inputs):
            input_box.setText(str(include_terms[index]) if index < len(include_terms) else "")
        for index, checkbox in enumerate(self.include_whole_word_checks):
            checkbox.setChecked(bool(include_whole_words[index]) if index < len(include_whole_words) else False)
        for index, checkbox in enumerate(self.include_regex_checks):
            checkbox.setChecked(bool(include_regexes[index]) if index < len(include_regexes) else False)
        for index, input_box in enumerate(self.exclude_inputs):
            input_box.setText(str(exclude_terms[index]) if index < len(exclude_terms) else "")
        self.replace_input.setText(str(state.get("replace_with", "")))
        self.replace_whole_word_checkbox.setChecked(bool(state.get("replace_whole_word", False)))
        self.replace_regex_checkbox.setChecked(bool(state.get("replace_regex", False)))
        replace_target = str(state.get("replace_target", ""))
        self.replace_target_input.setText(replace_target or self.first_include_term())
        self.last_auto_replace_target = "" if replace_target else self.replace_target_input.text().strip()

    def on_include_regex_toggled(self, index: int, checked: bool) -> None:
        if index < 0 or index >= len(self.include_whole_word_checks):
            return
        whole_word_checkbox = self.include_whole_word_checks[index]
        if checked:
            whole_word_checkbox.setChecked(False)
        whole_word_checkbox.setEnabled(not checked)

    def on_replace_regex_toggled(self, checked: bool) -> None:
        if checked:
            self.replace_whole_word_checkbox.setChecked(False)
        self.replace_whole_word_checkbox.setEnabled(not checked)

    def save_query_to_file(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Batch FreeText Query",
            "BatchFreeText.FindFreetext.json",
            "Find FreeText Query (*.FindFreetext.json);;JSON Files (*.json)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json" or not target.name.endswith(".FindFreetext.json"):
            target = target.with_name(target.stem + ".FindFreetext.json")
        try:
            target.write_text(json.dumps(self.query_state(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_label.setText(f"Saved query: {target.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Batch FreeText Query failed", str(exc))

    def load_query_from_file(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Batch FreeText Query",
            "",
            "Find FreeText Query (*.FindFreetext.json);;JSON Files (*.json)",
        )
        if not path:
            return
        try:
            state = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("Query file must contain a JSON object.")
            self.apply_query_state(state)
            self.status_label.setText(f"Loaded query: {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Batch FreeText Query failed", str(exc))

    def emit_replace_selected(self) -> None:
        self.replace_selected_requested.emit(self.replace_options())

    def emit_replace_all(self) -> None:
        self.replace_all_requested.emit(self.replace_options())

    def emit_replace_preview(self) -> None:
        self.replace_preview_requested.emit(self.replace_options())

    def replace_options(self) -> dict:
        return {
            "replacement": self.replace_input.text(),
            "whole_word": self.replace_whole_word_checkbox.isChecked() and not self.replace_regex_checkbox.isChecked(),
            "regex": self.replace_regex_checkbox.isChecked(),
        }

    def emit_add_selected(self) -> None:
        self.add_selected_requested.emit(self.add_input.text(), self.add_mode_combo.currentData())

    def preview_text(self, text: str, max_length: int = 160) -> str:
        value = " ".join(text.split())
        if len(value) <= max_length:
            return value
        return value[: max_length - 3].rstrip() + "..."


class BatchReplacePreviewDialog(QDialog):
    replace_all_requested = Signal()

    def __init__(self, rows: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Batch FreeText Replace Preview")
        self.resize(920, 560)

        layout = QVBoxLayout(self)
        match_count = sum(int(item.get("match_count", 0)) for item in rows)
        self.status_label = QLabel(f"FreeText: {len(rows)} | Matches: {match_count}")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Page", "Old Text", "New Text"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.setStyleSheet(table_contrast_stylesheet(35))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            page_number = str(item["page_number"])
            old_text = str(item["old_text"])
            new_text = str(item["new_text"])
            page_item = QTableWidgetItem(page_number)
            page_item.setToolTip(page_number)
            self.table.setItem(row, 0, page_item)
            old_cell = self.preview_text_cell(str(item.get("old_html", escape_plain(old_text))))
            old_cell.setToolTip(old_text)
            self.table.setCellWidget(row, 1, old_cell)
            new_cell = self.preview_text_cell(str(item.get("new_html", escape_plain(new_text))))
            new_cell.setToolTip(new_text)
            new_cell.setStyleSheet("QLabel { color: #be0000; padding: 2px; }")
            self.table.setCellWidget(row, 2, new_cell)
        self.table.resizeRowsToContents()
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.replace_all_button = QPushButton("Replace All")
        self.cancel_button = QPushButton("Cancel")
        self.replace_all_button.setEnabled(bool(rows))
        buttons.addWidget(self.replace_all_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self.replace_all_button.clicked.connect(self.replace_all_requested.emit)
        self.cancel_button.clicked.connect(self.reject)

    def preview_text_cell(self, html: str) -> QLabel:
        label = QLabel(html)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setStyleSheet("QLabel { padding: 2px; }")
        return label


def escape_plain(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
