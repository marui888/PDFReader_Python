from collections.abc import Callable
from pathlib import Path
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.annotation_index import AnnotationSearchResult
from app.search.annotation_search_query import (
    AnnotationSearchQuery,
    build_search_query,
    load_search_rule,
    save_search_rule,
)


class AnnotationSearchWidget(QWidget):
    search_requested = Signal(object, object, object)
    result_activated = Signal(str, int, int)
    maximize_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.results: list[AnnotationSearchResult] = []
        self.page_size = 500
        self.current_page_index = 0
        self.indexed_files: list[tuple[str, str]] = []
        self.selected_document_paths: set[str] = set()
        self.include_mode = "all"
        self.include_text = ""
        self.exclude_text = ""
        self.search_rule_dir = Path.cwd() / "search_rules"
        self.recent_search_rule_files: list[str] = []
        self.recent_search_rules_changed: Callable[[list[str]], None] | None = None
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search annotation text")
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        self.type_combo.addItem("FreeText", "freetext")
        self.type_combo.addItem("Highlight", "highlight")
        self.type_combo.addItem("Square", "square")
        self.type_combo.addItem("Arrow", "arrow")
        self.type_combo.addItem("Unsupported", "unsupported")
        self.file_filter_button = QPushButton("All files")
        self.advanced_button = QPushButton("Advanced...")
        self.search_button = QPushButton("Search")
        self.maximize_button = QPushButton("Maximize")
        self.maximize_button.setEnabled(False)
        self.prev_button = QPushButton("Prev")
        self.next_button = QPushButton("Next")
        self.page_label = QLabel("No results.")
        self.status_label = QLabel("No PDF open.")
        self.status_label.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.type_combo)
        controls.addWidget(self.file_filter_button)
        controls.addWidget(self.advanced_button)
        controls.addWidget(self.search_button)
        controls.addWidget(self.maximize_button)

        page_controls = QHBoxLayout()
        page_controls.addWidget(self.prev_button)
        page_controls.addWidget(self.next_button)
        page_controls.addWidget(self.page_label, 1)

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
        layout.addLayout(page_controls)
        layout.addWidget(self.table)

        self.search_button.clicked.connect(self.emit_search_requested)
        self.file_filter_button.clicked.connect(self.show_file_filter_dialog)
        self.advanced_button.clicked.connect(self.show_advanced_search_dialog)
        self.maximize_button.clicked.connect(self.maximize_requested.emit)
        self.prev_button.clicked.connect(self.show_previous_page)
        self.next_button.clicked.connect(self.show_next_page)
        self.search_input.returnPressed.connect(self.emit_search_requested)
        self.table.itemDoubleClicked.connect(lambda _item: self.emit_result_activated())
        self.update_page_controls()

    def set_search_rule_storage(
        self,
        rule_dir: Path,
        recent_files: list[str],
        recent_changed: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.search_rule_dir = rule_dir
        self.recent_search_rule_files = list(recent_files)
        self.recent_search_rules_changed = recent_changed

    def emit_search_requested(self) -> None:
        selected_paths = sorted(self.selected_document_paths) if self.selected_document_paths else None
        self.search_requested.emit(self.current_search_query(), self.type_combo.currentData(), selected_paths)

    def current_search_query(self) -> AnnotationSearchQuery:
        return build_search_query(
            keyword=self.search_input.text(),
            include_mode=self.include_mode,
            include_text=self.include_text,
            exclude_text=self.exclude_text,
        )

    def show_advanced_search_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Advanced Text Search")
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Include terms match"))
        mode_combo = QComboBox()
        mode_combo.addItem("All terms", "all")
        mode_combo.addItem("Any term", "any")
        mode_combo.setCurrentIndex(1 if self.include_mode == "any" else 0)
        mode_row.addWidget(mode_combo, 1)
        layout.addLayout(mode_row)

        include_edit = QPlainTextEdit()
        include_edit.setPlaceholderText("Terms to include. Separate with commas, semicolons, or new lines.")
        include_edit.setPlainText(self.include_text)
        include_edit.setMinimumHeight(90)
        layout.addWidget(QLabel("Include"))
        layout.addWidget(include_edit)

        exclude_edit = QPlainTextEdit()
        exclude_edit.setPlaceholderText("Terms to exclude. Separate with commas, semicolons, or new lines.")
        exclude_edit.setPlainText(self.exclude_text)
        exclude_edit.setMinimumHeight(70)
        layout.addWidget(QLabel("Exclude"))
        layout.addWidget(exclude_edit)

        hint = QLabel("Advanced conditions override the quick keyword when Include terms are present.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset
        )
        rule_buttons = QHBoxLayout()
        save_button = QPushButton("Save...")
        load_button = QPushButton("Load...")
        recent_button = QPushButton("Recent")
        rule_buttons.addWidget(save_button)
        rule_buttons.addWidget(load_button)
        rule_buttons.addWidget(recent_button)
        rule_buttons.addStretch(1)
        layout.addLayout(rule_buttons)
        layout.addWidget(buttons)

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        reset_button = buttons.button(QDialogButtonBox.StandardButton.Reset)
        if reset_button is not None:
            reset_button.setText("Clear")
            reset_button.clicked.connect(lambda: (include_edit.clear(), exclude_edit.clear()))

        save_button.clicked.connect(
            lambda: self.save_advanced_search_rule(mode_combo, include_edit, exclude_edit)
        )
        load_button.clicked.connect(
            lambda: self.load_advanced_search_rule_from_dialog(mode_combo, include_edit, exclude_edit)
        )
        recent_button.clicked.connect(
            lambda: self.show_recent_search_rules_menu(recent_button, mode_combo, include_edit, exclude_edit)
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.include_mode = mode_combo.currentData()
        self.include_text = include_edit.toPlainText()
        self.exclude_text = exclude_edit.toPlainText()
        self.update_advanced_button()

    def save_advanced_search_rule(
        self,
        mode_combo: QComboBox,
        include_edit: QPlainTextEdit,
        exclude_edit: QPlainTextEdit,
    ) -> None:
        self.search_rule_dir.mkdir(parents=True, exist_ok=True)
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Search Rule",
            str(self.search_rule_dir / "advanced-search-rule.json"),
            "Search rule (*.json)",
        )
        if not file_name:
            return
        path = Path(file_name)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        save_search_rule(
            path,
            mode_combo.currentData(),
            include_edit.toPlainText(),
            exclude_edit.toPlainText(),
            path.stem,
        )
        self.add_recent_search_rule_file(path)

    def load_advanced_search_rule_from_dialog(
        self,
        mode_combo: QComboBox,
        include_edit: QPlainTextEdit,
        exclude_edit: QPlainTextEdit,
    ) -> None:
        self.search_rule_dir.mkdir(parents=True, exist_ok=True)
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load Search Rule",
            str(self.search_rule_dir),
            "Search rule (*.json)",
        )
        if file_name:
            self.load_advanced_search_rule(Path(file_name), mode_combo, include_edit, exclude_edit)

    def show_recent_search_rules_menu(
        self,
        button: QPushButton,
        mode_combo: QComboBox,
        include_edit: QPlainTextEdit,
        exclude_edit: QPlainTextEdit,
    ) -> None:
        menu = QMenu(self)
        if not self.recent_search_rule_files:
            action = menu.addAction("No recent rules")
            action.setEnabled(False)
        else:
            for path_text in self.recent_search_rule_files:
                path = Path(path_text)
                action = menu.addAction(self.elided_file_name(path.name))
                action.setToolTip(str(path))
                action.triggered.connect(
                    lambda _checked=False, rule_path=path: self.load_advanced_search_rule(
                        rule_path,
                        mode_combo,
                        include_edit,
                        exclude_edit,
                    )
                )
            menu.addSeparator()
            clear_action = menu.addAction("Clear Recent")
            clear_action.triggered.connect(self.clear_recent_search_rule_files)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def load_advanced_search_rule(
        self,
        path: Path,
        mode_combo: QComboBox,
        include_edit: QPlainTextEdit,
        exclude_edit: QPlainTextEdit,
    ) -> None:
        rule = load_search_rule(path)
        mode_combo.setCurrentIndex(1 if rule["include_mode"] == "any" else 0)
        include_edit.setPlainText(rule["include_text"])
        exclude_edit.setPlainText(rule["exclude_text"])
        self.add_recent_search_rule_file(path)

    def add_recent_search_rule_file(self, path: Path) -> None:
        path_text = str(path)
        key = path_text.lower()
        recent = [item for item in self.recent_search_rule_files if item.lower() != key]
        recent.insert(0, path_text)
        self.recent_search_rule_files = recent[:10]
        if self.recent_search_rules_changed is not None:
            self.recent_search_rules_changed(self.recent_search_rule_files)

    def clear_recent_search_rule_files(self) -> None:
        self.recent_search_rule_files = []
        if self.recent_search_rules_changed is not None:
            self.recent_search_rules_changed(self.recent_search_rule_files)

    def update_advanced_button(self) -> None:
        query = self.current_search_query()
        count = len(query.conditions())
        if count:
            self.advanced_button.setText(f"Advanced ({count})")
            self.advanced_button.setToolTip(query.summary())
        else:
            self.advanced_button.setText("Advanced...")
            self.advanced_button.setToolTip("Configure include/exclude text conditions")

    def set_indexed_files(self, files: list[tuple[str, str]]) -> None:
        self.indexed_files = files
        available_paths = {path for path, _file_name in files}
        self.selected_document_paths = {
            path for path in self.selected_document_paths if path in available_paths
        }
        self.update_file_filter_button()

    def show_file_filter_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Indexed Files")
        dialog.setMinimumWidth(420)
        dialog.setWindowFlag(Qt.WindowType.Popup, True)

        layout = QVBoxLayout(dialog)
        all_checkbox = QCheckBox("All files")
        all_checkbox.setChecked(not self.selected_document_paths)
        layout.addWidget(all_checkbox)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        file_checkboxes: list[tuple[QCheckBox, str]] = []
        for path, file_name in self.indexed_files:
            checkbox = QCheckBox(self.elided_file_name(file_name))
            checkbox.setToolTip(path)
            checkbox.setChecked(path in self.selected_document_paths)
            list_layout.addWidget(checkbox)
            file_checkboxes.append((checkbox, path))
        list_layout.addStretch(1)
        scroll_area.setWidget(list_widget)
        layout.addWidget(scroll_area)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def sync_file_checkboxes() -> None:
            all_checked = all_checkbox.isChecked()
            for checkbox, _path in file_checkboxes:
                checkbox.setEnabled(not all_checked)
                if all_checked:
                    checkbox.setChecked(False)

        def on_file_checkbox_changed(_state: int) -> None:
            if any(checkbox.isChecked() for checkbox, _path in file_checkboxes):
                all_checkbox.blockSignals(True)
                all_checkbox.setChecked(False)
                all_checkbox.blockSignals(False)
                sync_file_checkboxes()

        all_checkbox.stateChanged.connect(lambda _state: sync_file_checkboxes())
        for checkbox, _path in file_checkboxes:
            checkbox.stateChanged.connect(on_file_checkbox_changed)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        sync_file_checkboxes()

        top_left = self.file_filter_button.mapToGlobal(self.file_filter_button.rect().bottomLeft())
        dialog.move(top_left)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if all_checkbox.isChecked():
            self.selected_document_paths.clear()
        else:
            self.selected_document_paths = {
                path for checkbox, path in file_checkboxes if checkbox.isChecked()
            }
        self.update_file_filter_button()

    def update_file_filter_button(self) -> None:
        if not self.selected_document_paths:
            self.file_filter_button.setText("All files")
            self.file_filter_button.setToolTip("Search all indexed files")
            return

        if len(self.selected_document_paths) == 1:
            path = next(iter(self.selected_document_paths))
            file_name = next((name for item_path, name in self.indexed_files if item_path == path), Path(path).name)
            self.file_filter_button.setText(self.elided_file_name(file_name))
            self.file_filter_button.setToolTip(path)
            return

        self.file_filter_button.setText(f"{len(self.selected_document_paths)} files")
        self.file_filter_button.setToolTip("\n".join(sorted(self.selected_document_paths)))

    def elided_file_name(self, file_name: str, max_chars: int = 34) -> str:
        if len(file_name) <= max_chars:
            return file_name
        suffix = Path(file_name).suffix
        suffix_len = len(suffix)
        if suffix_len >= max_chars - 8:
            return file_name[: max_chars - 3] + "..."
        stem = file_name[: -suffix_len] if suffix else file_name
        keep = max_chars - suffix_len - 3
        return stem[:keep].rstrip() + "..." + suffix

    def emit_result_activated(self) -> None:
        row = self.table.currentRow()
        result_index = self.current_page_index * self.page_size + row
        if row < 0 or result_index < 0 or result_index >= len(self.results):
            return
        result = self.results[result_index]
        self.result_activated.emit(result.document_path, result.page_index, result.xref)

    def set_page_size(self, page_size: int) -> None:
        self.page_size = max(1, int(page_size))
        if self.results:
            self.current_page_index = min(self.current_page_index, self.page_count() - 1)
            self.show_current_page()
        else:
            self.update_page_controls()

    def set_results(self, results: list[AnnotationSearchResult], page_size: int | None = None) -> float:
        if page_size is not None:
            self.page_size = max(1, int(page_size))
        self.results = results
        self.current_page_index = 0
        return self.show_current_page()

    def show_current_page(self) -> float:
        started = time.perf_counter()
        visible_results = self.current_page_results()
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            self.table.clearContents()
            self.table.setRowCount(0)
            self.table.setRowCount(len(visible_results))
            for row, result in enumerate(visible_results):
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
            self.update_page_controls()
        return (time.perf_counter() - started) * 1000

    def current_page_results(self) -> list[AnnotationSearchResult]:
        start = self.current_page_index * self.page_size
        end = start + self.page_size
        return self.results[start:end]

    def page_count(self) -> int:
        if not self.results:
            return 0
        return ((len(self.results) - 1) // self.page_size) + 1

    def show_previous_page(self) -> None:
        if self.current_page_index <= 0:
            return
        self.current_page_index -= 1
        self.show_current_page()

    def show_next_page(self) -> None:
        if self.current_page_index >= self.page_count() - 1:
            return
        self.current_page_index += 1
        self.show_current_page()

    def update_page_controls(self) -> None:
        page_count = self.page_count()
        if page_count == 0:
            self.page_label.setText("No results.")
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return

        start = self.current_page_index * self.page_size + 1
        end = min(len(self.results), start + self.page_size - 1)
        self.page_label.setText(
            f"Page {self.current_page_index + 1} / {page_count} | "
            f"Results {start}-{end} / {len(self.results)}"
        )
        self.prev_button.setEnabled(self.current_page_index > 0)
        self.next_button.setEnabled(self.current_page_index < page_count - 1)

    def clear_results(self) -> None:
        self.results = []
        self.current_page_index = 0
        self.table.clearSelection()
        self.table.clearContents()
        self.table.setRowCount(0)
        self.update_page_controls()

    def reset_search_state(self) -> None:
        self.search_input.clear()
        self.include_mode = "all"
        self.include_text = ""
        self.exclude_text = ""
        self.update_advanced_button()
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
        self.file_filter_button.setEnabled(not busy)
        self.advanced_button.setEnabled(not busy)
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
