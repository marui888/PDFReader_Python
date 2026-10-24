from collections.abc import Callable
import json
from pathlib import Path
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.annotation_index import AnnotationSearchResult
from app.models.index_management import IndexSnapshot
from app.services.ui_contrast import table_contrast_stylesheet
from app.widgets.recent_term_input import RecentTermLineEdit
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
        self.source_snapshot = IndexSnapshot()
        # None means All; an empty set means no remaining selected sources, never All.
        self.selected_source_keys: set[str] | None = None
        self.refresh_sources: Callable[[], None] | None = None
        self.search_rule_dir = Path.cwd() / "search_rules"
        self.recent_search_rule_files: list[str] = []
        self.recent_search_rules_changed: Callable[[list[str]], None] | None = None
        self.include_mode_combo = QComboBox()
        self.include_mode_combo.addItem("AND", "all")
        self.include_mode_combo.addItem("OR", "any")
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
        self.exclude_mode_combo.addItem("AND", "all")
        self.exclude_mode_combo.addItem("OR", "any")
        self.exclude_mode_combo.setCurrentIndex(1)
        self.exclude_inputs = [RecentTermLineEdit(self) for _index in range(2)]
        for index, input_box in enumerate(self.exclude_inputs, start=1):
            input_box.setPlaceholderText(f"term{index}")
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        self.type_combo.addItem("FreeText", "freetext")
        self.type_combo.addItem("Highlight", "highlight")
        self.type_combo.addItem("Square", "square")
        self.type_combo.addItem("Arrow", "arrow")
        self.type_combo.addItem("Unsupported", "unsupported")
        self.file_filter_button = QPushButton("All sources")
        self.clear_include_button = QPushButton("Clear")
        self.clear_exclude_button = QPushButton("Clear")
        self.save_query_button = QPushButton("Save")
        self.load_query_button = QPushButton("Load")
        for button in (
            self.clear_include_button,
            self.clear_exclude_button,
            self.save_query_button,
            self.load_query_button,
        ):
            button.setFixedWidth(46)
        self.advanced_button = QPushButton("Advanced...")
        self.search_button = QPushButton("Search")
        self.maximize_button = QPushButton("Maximize")
        self.maximize_button.setEnabled(False)
        self.prev_button = QPushButton("Prev")
        self.next_button = QPushButton("Next")
        self.page_label = QLabel("No results.")
        self.status_label = QLabel("No PDF open.")
        self.status_label.setWordWrap(True)

        controls = QVBoxLayout()
        include_header = QHBoxLayout()
        include_header.addWidget(QLabel("Including:"))
        include_header.addWidget(QLabel("Mode:"))
        include_header.addWidget(self.include_mode_combo)
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
        exclude_header.addWidget(self.clear_exclude_button)
        exclude_header.addStretch(1)
        exclude_row = QHBoxLayout()
        exclude_row.addWidget(self.exclude_inputs[0], 1)
        exclude_row.addWidget(self.exclude_inputs[1], 1)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type"))
        type_row.addWidget(self.type_combo, 1)
        files_row = QHBoxLayout()
        files_row.addWidget(QLabel("Sources"))
        files_row.addWidget(self.file_filter_button, 1)
        button_row = QHBoxLayout()
        button_row.addWidget(self.search_button)
        button_row.addWidget(self.advanced_button)
        button_row.addWidget(self.maximize_button)
        button_row.addStretch(1)

        controls.addLayout(include_header)
        controls.addLayout(include_row_1)
        controls.addLayout(include_row_2)
        controls.addLayout(exclude_header)
        controls.addLayout(exclude_row)
        controls.addLayout(type_row)
        controls.addLayout(files_row)
        controls.addLayout(button_row)
        controls.addStretch(1)

        page_controls = QHBoxLayout()
        page_controls.addWidget(self.prev_button)
        page_controls.addWidget(self.next_button)
        page_controls.addWidget(self.page_label, 1)
        page_controls.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("File", "Page", "Type", "xref", "Text"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        results_layout = QVBoxLayout()
        results_layout.setSpacing(4)
        results_layout.addWidget(self.table, 1)
        results_layout.addLayout(page_controls, 0)

        query_panel = QWidget(self)
        query_layout = QVBoxLayout(query_panel)
        query_layout.setContentsMargins(0, 0, 0, 0)
        query_layout.addWidget(self.status_label)
        query_layout.addLayout(controls)
        query_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        query_scroll = QScrollArea(self)
        query_scroll.setWidgetResizable(True)
        query_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        query_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        query_scroll.setWidget(query_panel)
        query_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addLayout(results_layout, 3)
        layout.addWidget(query_scroll, 1)

        self.search_button.clicked.connect(self.emit_search_requested)
        self.save_query_button.clicked.connect(self.save_query_to_file)
        self.load_query_button.clicked.connect(self.load_query_from_file)
        self.clear_include_button.clicked.connect(self.clear_include_terms)
        self.clear_exclude_button.clicked.connect(self.clear_exclude_terms)
        self.file_filter_button.clicked.connect(self.show_file_filter_dialog)
        self.advanced_button.clicked.connect(self.show_advanced_search_dialog)
        self.maximize_button.clicked.connect(self.maximize_requested.emit)
        self.prev_button.clicked.connect(self.show_previous_page)
        self.next_button.clicked.connect(self.show_next_page)
        for input_box in self.include_inputs + self.exclude_inputs:
            input_box.returnPressed.connect(self.emit_search_requested)
        for index, checkbox in enumerate(self.include_regex_checks):
            checkbox.toggled.connect(lambda checked, item_index=index: self.on_include_regex_toggled(item_index, checked))
        self.table.itemDoubleClicked.connect(lambda _item: self.emit_result_activated())
        self.apply_ui_contrast(35)
        self.update_page_controls()

    def apply_ui_contrast(self, contrast: int) -> None:
        self.table.setStyleSheet(table_contrast_stylesheet(contrast))

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
        try:
            if self.refresh_sources is not None:
                self.refresh_sources()
        except Exception as exc:
            QMessageBox.warning(self, "Sources", f"Unable to read indexed sources:\n{exc}")
            return
        if self.selected_source_keys is not None and not self.selected_source_keys:
            QMessageBox.information(self, "Sources", "Please select All or at least one source.")
            return
        selected_paths = self.selected_source_document_paths()
        self.search_requested.emit(self.current_search_query(), self.type_combo.currentData(), selected_paths)

    def current_search_query(self) -> AnnotationSearchQuery:
        return build_search_query(
            keyword="",
            include_mode=self.current_include_mode(),
            include_text=self.current_include_text(),
            include_whole_words=self.current_include_whole_words(),
            include_regexes=self.current_include_regexes(),
            exclude_text=self.current_exclude_text(),
            exclude_mode=self.current_exclude_mode(),
        )

    def current_include_mode(self) -> str:
        return self.include_mode_combo.currentData()

    def current_exclude_mode(self) -> str:
        return self.exclude_mode_combo.currentData()

    def current_include_text(self) -> str:
        return "\n".join(input_box.text().strip() for input_box in self.include_inputs if input_box.text().strip())

    def current_include_whole_words(self) -> list[bool]:
        values: list[bool] = []
        for input_box, checkbox, regex_checkbox in zip(
            self.include_inputs,
            self.include_whole_word_checks,
            self.include_regex_checks,
        ):
            if input_box.text().strip():
                values.append(checkbox.isChecked() and not regex_checkbox.isChecked())
        return values

    def current_include_regexes(self) -> list[bool]:
        values: list[bool] = []
        for input_box, checkbox in zip(self.include_inputs, self.include_regex_checks):
            if input_box.text().strip():
                values.append(checkbox.isChecked())
        return values

    def current_exclude_text(self) -> str:
        return "\n".join(input_box.text().strip() for input_box in self.exclude_inputs if input_box.text().strip())

    def set_term_inputs(self, inputs: list[QLineEdit], text: str) -> None:
        terms = [term.strip() for term in text.replace("\r", "\n").replace(",", "\n").replace(";", "\n").splitlines() if term.strip()]
        for index, input_box in enumerate(inputs):
            input_box.setText(terms[index] if index < len(terms) else "")

    def clear_include_terms(self) -> None:
        for input_box in self.include_inputs:
            input_box.clear()
        for checkbox in self.include_whole_word_checks:
            checkbox.setChecked(False)
        for checkbox in self.include_regex_checks:
            checkbox.setChecked(False)

    def clear_exclude_terms(self) -> None:
        for input_box in self.exclude_inputs:
            input_box.clear()

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
        mode_combo.setCurrentIndex(1 if self.current_include_mode() == "any" else 0)
        mode_row.addWidget(mode_combo, 1)
        layout.addLayout(mode_row)

        include_edit = QPlainTextEdit()
        include_edit.setPlaceholderText("Terms to include. Separate with commas, semicolons, or new lines.")
        include_edit.setPlainText(self.current_include_text())
        include_edit.setMinimumHeight(90)
        layout.addWidget(QLabel("Include"))
        layout.addWidget(include_edit)

        exclude_edit = QPlainTextEdit()
        exclude_edit.setPlaceholderText("Terms to exclude. Separate with commas, semicolons, or new lines.")
        exclude_edit.setPlainText(self.current_exclude_text())
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

        self.include_mode_combo.setCurrentIndex(1 if mode_combo.currentData() == "any" else 0)
        self.set_term_inputs(self.include_inputs, include_edit.toPlainText())
        for checkbox in self.include_whole_word_checks:
            checkbox.setChecked(False)
        for checkbox in self.include_regex_checks:
            checkbox.setChecked(False)
        self.set_term_inputs(self.exclude_inputs, exclude_edit.toPlainText())
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
            "any",
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

    def query_state(self) -> dict:
        return {
            "include_mode": self.current_include_mode(),
            "include_terms": [input_box.text() for input_box in self.include_inputs],
            "include_whole_words": [checkbox.isChecked() for checkbox in self.include_whole_word_checks],
            "include_regexes": [checkbox.isChecked() for checkbox in self.include_regex_checks],
            "exclude_mode": self.current_exclude_mode(),
            "exclude_terms": [input_box.text() for input_box in self.exclude_inputs],
            "type": self.type_combo.currentData(),
            "selected_source_keys": (
                sorted(self.selected_source_keys) if self.selected_source_keys is not None else None
            ),
        }

    def apply_query_state(self, state: dict) -> None:
        self.include_mode_combo.setCurrentIndex(max(0, self.include_mode_combo.findData(state.get("include_mode", "all"))))
        self.exclude_mode_combo.setCurrentIndex(max(0, self.exclude_mode_combo.findData(state.get("exclude_mode", "any"))))
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
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(state.get("type"))))
        if self.refresh_sources is not None:
            self.refresh_sources()
        if "selected_source_keys" in state:
            keys = state["selected_source_keys"]
            if keys is not None and not isinstance(keys, list):
                raise ValueError("selected_source_keys must be a list or null")
            self.selected_source_keys = (
                {str(key) for key in keys} & self.source_snapshot.source_keys
                if keys is not None else None
            )
        else:
            # Old query files filtered individual PDFs. Only migrate exact source coverage;
            # never silently widen the search to a whole folder or to All.
            from app.models.index_management import path_key
            paths = state.get("selected_document_paths", [])
            requested = {path_key(str(path)) for path in paths} if isinstance(paths, list) else set()
            self.selected_source_keys = {
                key for key, members in self.source_snapshot.members.items()
                if members and members <= requested
            } if requested else None
            covered = set().union(*(
                self.source_snapshot.members[key] for key in self.selected_source_keys
            )) if self.selected_source_keys else set()
            if requested and covered != requested:
                QMessageBox.information(
                    self, "Sources", "The saved Files filter cannot be fully mapped to current sources. "
                    "Please choose the Sources filter again."
                )
        self.update_file_filter_button()
        self.update_advanced_button()

    def save_query_to_file(self) -> None:
        self.search_rule_dir.mkdir(parents=True, exist_ok=True)
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Search Annotations Query",
            str(self.search_rule_dir / "SearchAnnotations.SearchAnnotations.json"),
            "Search Annotations Query (*.SearchAnnotations.json);;JSON Files (*.json)",
        )
        if not file_name:
            return
        path = Path(file_name)
        if path.suffix.lower() != ".json" or not path.name.endswith(".SearchAnnotations.json"):
            path = path.with_name(path.stem + ".SearchAnnotations.json")
        try:
            path.write_text(json.dumps(self.query_state(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.add_recent_search_rule_file(path)
            self.status_label.setText(f"Saved query: {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Search Annotations Query failed", str(exc))

    def load_query_from_file(self) -> None:
        self.search_rule_dir.mkdir(parents=True, exist_ok=True)
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load Search Annotations Query",
            str(self.search_rule_dir),
            "Search Annotations Query (*.SearchAnnotations.json);;JSON Files (*.json)",
        )
        if not file_name:
            return
        try:
            path = Path(file_name)
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Query file must contain a JSON object.")
            self.apply_query_state(data)
            self.add_recent_search_rule_file(path)
            self.status_label.setText(f"Loaded query: {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Search Annotations Query failed", str(exc))

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

    def set_indexed_sources(self, snapshot: IndexSnapshot) -> None:
        self.source_snapshot = snapshot
        if self.selected_source_keys is not None:
            self.selected_source_keys.intersection_update(snapshot.source_keys)
        self.update_file_filter_button()

    def selected_source_document_paths(self) -> list[str] | None:
        if self.selected_source_keys is None:
            return None
        members: set[str] = set()
        for key in self.selected_source_keys:
            members.update(self.source_snapshot.members.get(key, set()))
        return sorted(
            self.source_snapshot.documents[key]["path"]
            for key in members if key in self.source_snapshot.documents
        )

    def show_file_filter_dialog(self) -> None:
        try:
            if self.refresh_sources is not None:
                self.refresh_sources()
        except Exception as exc:
            QMessageBox.warning(self, "Sources", f"Unable to read indexed sources:\n{exc}")
            return
        host = self.parentWidget()
        while host is not None and not isinstance(host, QMainWindow):
            host = host.parentWidget()
        host = host or self.window()
        dialog = QDialog(host)
        dialog.setWindowTitle("Select Sources")
        dialog.resize(680, 580)
        layout = QVBoxLayout(dialog)
        all_checkbox = QCheckBox("All")
        all_checkbox.setChecked(self.selected_source_keys is None)
        layout.addWidget(all_checkbox)
        source_list = QListWidget()
        source_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        sources = {source.key: source for source in self.source_snapshot.sources}
        for source in self.source_snapshot.sources:
            kind = "Folder" if source.kind == "folder" else "File"
            name = Path(source.path).name or source.path
            item = QListWidgetItem(f"[{kind}] {name}")
            item.setData(Qt.ItemDataRole.UserRole, source.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if self.selected_source_keys is not None
                and source.key in self.selected_source_keys else Qt.CheckState.Unchecked
            )
            source_list.addItem(item)
        layout.addWidget(source_list, 3)
        info_group = QGroupBox("Selected Source Info")
        info_layout = QVBoxLayout(info_group)
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        info_layout.addWidget(info)
        layout.addWidget(info_group, 2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def checked_keys() -> set[str]:
            return {
                source_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(source_list.count())
                if source_list.item(row).checkState() == Qt.CheckState.Checked
            }

        def update_details(inspect_row: bool = False) -> None:
            if all_checkbox.isChecked() and not inspect_row:
                info.setPlainText(
                    f"All sources\nSources: {len(sources)}\n"
                    f"Indexed PDFs (unique): {len(self.source_snapshot.documents)}"
                )
                return
            item = source_list.currentItem()
            source = sources.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
            if source is None:
                info.setPlainText("Select a source row to view its details.")
                return
            members = self.source_snapshot.members.get(source.key, set())
            annotation_count = sum(
                self.source_snapshot.documents[key].get("annotation_count", 0)
                for key in members if key in self.source_snapshot.documents
            )
            lines = [
                f"Type: {'Folder' if source.kind == 'folder' else 'File'}",
                f"Name: {Path(source.path).name or source.path}",
                f"Full path: {source.path}",
                f"Indexed at: {source.indexed_at or 'Not recorded'}",
                f"Indexed PDFs: {len(members)}",
                f"Indexed annotations: {annotation_count}",
            ]
            if source.kind == "folder":
                lines.append(f"Include subfolders: {'Yes' if source.recursive else 'No'}")
            info.setPlainText("\n".join(lines))

        def update_validity() -> None:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                all_checkbox.isChecked() or bool(checked_keys())
            )

        def all_changed(checked: bool) -> None:
            # Keep rows selectable for inspection even when All is active.
            if checked:
                source_list.blockSignals(True)
                for row in range(source_list.count()):
                    source_list.item(row).setCheckState(Qt.CheckState.Unchecked)
                source_list.blockSignals(False)
            update_details()
            update_validity()

        def item_changed(item: QListWidgetItem) -> None:
            if item.checkState() == Qt.CheckState.Checked:
                all_checkbox.setChecked(False)
            update_validity()

        def row_changed(current, _previous) -> None:
            # Selecting a row only inspects it; it does not change the search scope.
            update_details(inspect_row=current is not None)

        all_checkbox.toggled.connect(all_changed)
        source_list.itemChanged.connect(item_changed)
        source_list.currentItemChanged.connect(row_changed)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        update_details()
        update_validity()

        def center_dialog() -> None:
            screen = host.screen()
            available = screen.availableGeometry() if screen is not None else host.frameGeometry()
            dialog.resize(min(680, available.width()), min(580, available.height()))
            frame = dialog.frameGeometry()
            frame.moveCenter(host.frameGeometry().center())
            x = max(available.left(), min(frame.left(), available.right() - frame.width() + 1))
            y = max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1))
            dialog.move(x, y)

        QTimer.singleShot(0, center_dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.selected_source_keys = None if all_checkbox.isChecked() else checked_keys()
        self.update_file_filter_button()

    def update_file_filter_button(self) -> None:
        self.file_filter_button.setToolTip("")
        if self.selected_source_keys is None:
            self.file_filter_button.setText("All sources")
            return
        if len(self.selected_source_keys) == 1:
            source = next((source for source in self.source_snapshot.sources
                           if source.key in self.selected_source_keys), None)
            self.file_filter_button.setText(self.elided_file_name(
                Path(source.path).name or source.path
            ) if source else "No sources selected")
            return
        self.file_filter_button.setText(
            f"{len(self.selected_source_keys)} sources" if self.selected_source_keys else "No sources selected"
        )

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
        self.include_mode_combo.setCurrentIndex(0)
        self.exclude_mode_combo.setCurrentIndex(1)
        self.clear_include_terms()
        self.clear_exclude_terms()
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
        for input_box in self.include_inputs + self.exclude_inputs:
            input_box.setEnabled(not busy)
        for checkbox in self.include_whole_word_checks:
            checkbox.setEnabled(not busy)
        for checkbox in self.include_regex_checks:
            checkbox.setEnabled(not busy)
        self.include_mode_combo.setEnabled(not busy)
        self.exclude_mode_combo.setEnabled(not busy)
        self.clear_include_button.setEnabled(not busy)
        self.clear_exclude_button.setEnabled(not busy)
        self.save_query_button.setEnabled(not busy)
        self.load_query_button.setEnabled(not busy)
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

    def on_include_regex_toggled(self, index: int, checked: bool) -> None:
        if index < 0 or index >= len(self.include_whole_word_checks):
            return
        whole_word_checkbox = self.include_whole_word_checks[index]
        if checked:
            whole_word_checkbox.setChecked(False)
        whole_word_checkbox.setEnabled(not checked)

    def tooltip_text(self, result: AnnotationSearchResult, column: int) -> str:
        if column != 4:
            return result.document_path
        compact = " ".join(result.text.split())
        if len(compact) > 800:
            return compact[:800] + "..."
        return compact
