from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.shortcuts import (
    DEFAULT_SHORTCUTS,
    SHORTCUT_LABELS,
    find_shortcut_conflicts,
    merged_shortcuts,
)
from app.widgets.shortcut_editor import ShortcutEditor


def show_error(window, title: str, exc: Exception) -> None:
    QMessageBox.critical(window, title, str(exc))


def show_warning(window, title: str, message: str) -> None:
    QMessageBox.warning(window, title, message)


def show_information(window, title: str, message: str) -> None:
    QMessageBox.information(window, title, message)


def ask_yes_no(window, title: str, message: str, default_no: bool = True) -> bool:
    default = QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes
    reply = QMessageBox.question(
        window,
        title,
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        default,
    )
    return reply == QMessageBox.StandardButton.Yes


def ask_unsaved_document_action(window, action_text: str) -> str:
    document_name = window.pdf_path.name if window.pdf_path else "Untitled PDF"
    document_path = str(window.pdf_path) if window.pdf_path else "(no current file path)"
    message_box = QMessageBox(window)
    message_box.setIcon(QMessageBox.Icon.Warning)
    message_box.setWindowTitle("Unsaved Changes")
    message_box.setText(f"This document has unsaved changes before you {action_text}:")
    message_box.setInformativeText(f"{document_name}\n\n{document_path}")
    save_incremental_button = message_box.addButton(
        "Save Incremental",
        QMessageBox.ButtonRole.AcceptRole,
    )
    save_full_button = message_box.addButton(
        "Save Full Rewrite",
        QMessageBox.ButtonRole.AcceptRole,
    )
    discard_button = message_box.addButton(
        "Discard",
        QMessageBox.ButtonRole.DestructiveRole,
    )
    cancel_button = message_box.addButton(
        "Cancel",
        QMessageBox.ButtonRole.RejectRole,
    )
    message_box.setDefaultButton(save_incremental_button)
    message_box.exec()
    clicked_button = message_box.clickedButton()
    if clicked_button == save_incremental_button:
        return "save-incremental"
    if clicked_button == save_full_button:
        return "save-full"
    if clicked_button == discard_button:
        return "discard"
    if clicked_button == cancel_button:
        return "cancel"
    return "cancel"


def confirm_full_save(window) -> bool:
    return ask_yes_no(
        window,
        "Confirm Save",
        "This app will save by fully rewriting the PDF and creating a backup copy first.\n\n"
        "Continue and replace the current PDF file?",
    )


def confirm_save_incremental(window) -> tuple[bool, bool]:
    message_box = QMessageBox(window)
    message_box.setIcon(QMessageBox.Icon.Warning)
    message_box.setWindowTitle("Confirm Save Incremental")
    message_box.setText("Save Incremental writes changes directly into the current PDF.")
    message_box.setInformativeText("After saving, this app will close and reopen the current PDF.")
    safety_checkbox = QCheckBox("Create backup and run QPDF check")
    safety_checkbox.setChecked(window.save_incremental_safety_default)
    message_box.setCheckBox(safety_checkbox)
    confirm_button = message_box.addButton("Save Incremental", QMessageBox.ButtonRole.AcceptRole)
    message_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    message_box.setDefaultButton(confirm_button)
    message_box.exec()
    return message_box.clickedButton() is confirm_button, safety_checkbox.isChecked()


def confirm_pre_save_audit(window, report_text: str) -> bool:
    return ask_yes_no(
        window,
        "Audit Issues Before Save",
        "The current page has audit errors before saving.\n\n"
        f"{report_text}\n\n"
        "Continue saving anyway?",
    )


def confirm_clear_annotation_index(window) -> bool:
    message_box = QMessageBox(window)
    message_box.setIcon(QMessageBox.Icon.Warning)
    message_box.setWindowTitle("Clear Annotation Index")
    message_box.setText("Delete all annotation index records?")
    message_box.setInformativeText("This does not modify any PDF files.")
    confirm_button = message_box.addButton("Confirm Clear", QMessageBox.ButtonRole.AcceptRole)
    message_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    message_box.setDefaultButton(confirm_button)
    message_box.exec()
    return message_box.clickedButton() is confirm_button


def confirm_delete_annotation(window, message: str) -> bool:
    return ask_yes_no(window, "Delete Annotation", message, default_no=False)


def open_settings(window) -> None:
    dialog = QDialog(window)
    dialog.setWindowTitle("Settings")
    dialog.resize(560, 520)

    layout = QVBoxLayout(dialog)
    tabs = QTabWidget(dialog)
    layout.addWidget(tabs)

    normal_page = QWidget(dialog)
    normal_layout = QVBoxLayout(normal_page)
    foxit_checkbox = QCheckBox("Experimental Foxit Typewriter compatibility")
    foxit_checkbox.setChecked(window.use_foxit_freetext)
    normal_layout.addWidget(foxit_checkbox)

    popup_freetext_checkbox = QCheckBox("Use popup input for FreeText")
    popup_freetext_checkbox.setChecked(window.use_popup_freetext_input)
    normal_layout.addWidget(popup_freetext_checkbox)

    extract_highlight_text_checkbox = QCheckBox("Extract highlighted page text when reindexing")
    extract_highlight_text_checkbox.setChecked(window.extract_highlight_text_on_reindex)
    normal_layout.addWidget(extract_highlight_text_checkbox)

    quick_audit_detailed_checkbox = QCheckBox("Quick Audit detailed bounds check")
    quick_audit_detailed_checkbox.setChecked(window.quick_audit_detailed)
    normal_layout.addWidget(quick_audit_detailed_checkbox)

    save_incremental_safety_default_checkbox = QCheckBox("Default backup and QPDF check for Save Incremental")
    save_incremental_safety_default_checkbox.setChecked(window.save_incremental_safety_default)
    normal_layout.addWidget(save_incremental_safety_default_checkbox)

    form = QFormLayout()
    font_min_spin = QSpinBox()
    font_min_spin.setRange(1, 72)
    font_min_spin.setValue(window.freetext_font_size_min)
    form.addRow("FreeText font size min", font_min_spin)

    font_max_spin = QSpinBox()
    font_max_spin.setRange(1, 72)
    font_max_spin.setValue(window.freetext_font_size_max)
    form.addRow("FreeText font size max", font_max_spin)

    font_size_spin = QSpinBox()
    font_size_spin.setRange(window.freetext_font_size_min, window.freetext_font_size_max)
    font_size_spin.setValue(window.default_freetext_font_size)
    form.addRow("Default FreeText font size", font_size_spin)

    search_page_size_spin = QSpinBox()
    search_page_size_spin.setRange(1, 10000)
    search_page_size_spin.setValue(window.search_page_size)
    form.addRow("Search page size", search_page_size_spin)

    qpdf_bin_dir_edit = QLineEdit()
    qpdf_bin_dir_edit.setText(window.qpdf_bin_dir)
    form.addRow("QPDF bin directory", qpdf_bin_dir_edit)
    normal_layout.addLayout(form)
    normal_layout.addStretch(1)
    tabs.addTab(normal_page, "Normal")

    shortcuts_page = QWidget(dialog)
    shortcuts_layout = QVBoxLayout(shortcuts_page)
    shortcuts_form = QFormLayout()
    shortcut_edits: dict[str, ShortcutEditor] = {}
    selected_shortcut_key: dict[str, str | None] = {"key": None}
    current_shortcuts = merged_shortcuts(getattr(window, "shortcuts", {}))
    for key in DEFAULT_SHORTCUTS:
        edit = ShortcutEditor(key)
        edit.set_shortcut_text(current_shortcuts.get(key, ""))
        edit.setToolTip(f"Default: {DEFAULT_SHORTCUTS[key]}")
        edit.focused.connect(lambda shortcut_key, selected=selected_shortcut_key: selected.__setitem__("key", shortcut_key))
        shortcut_edits[key] = edit
        shortcuts_form.addRow(SHORTCUT_LABELS.get(key, key), edit)
    shortcuts_layout.addLayout(shortcuts_form)
    reset_layout = QHBoxLayout()
    reset_layout.addStretch(1)
    reset_selected_button = QPushButton("Reset Selected")
    reset_all_button = QPushButton("Reset All")
    reset_layout.addWidget(reset_selected_button)
    reset_layout.addWidget(reset_all_button)
    shortcuts_layout.addLayout(reset_layout)
    shortcuts_layout.addStretch(1)
    tabs.addTab(shortcuts_page, "Shortcuts")

    button_layout = QHBoxLayout()
    button_layout.addStretch(1)
    apply_button = QPushButton("Apply")
    apply_close_button = QPushButton("Apply and Close")
    cancel_button = QPushButton("Cancel")
    button_layout.addWidget(apply_button)
    button_layout.addWidget(apply_close_button)
    button_layout.addWidget(cancel_button)
    layout.addLayout(button_layout)

    def collect_shortcuts() -> dict[str, str] | None:
        shortcuts = {
            key: edit.shortcut_text()
            for key, edit in shortcut_edits.items()
        }
        conflicts = find_shortcut_conflicts(shortcuts)
        if conflicts:
            lines = []
            for shortcut_text, first_key, second_key in conflicts:
                lines.append(
                    f"{shortcut_text}: "
                    f"{SHORTCUT_LABELS.get(first_key, first_key)} / "
                    f"{SHORTCUT_LABELS.get(second_key, second_key)}"
                )
            show_warning(
                window,
                "Shortcut conflict",
                "One shortcut is assigned to multiple actions:\n\n" + "\n".join(lines),
            )
            return None
        return shortcuts

    def current_shortcut_values() -> dict[str, str]:
        return {
            key: edit.shortcut_text()
            for key, edit in shortcut_edits.items()
        }

    def refresh_shortcut_conflict_styles() -> None:
        conflict_keys: set[str] = set()
        for _shortcut_text, first_key, second_key in find_shortcut_conflicts(current_shortcut_values()):
            conflict_keys.add(first_key)
            conflict_keys.add(second_key)
        for key, edit in shortcut_edits.items():
            edit.set_conflict(key in conflict_keys)

    def reset_selected_shortcut() -> None:
        shortcut_key = selected_shortcut_key.get("key")
        if not shortcut_key:
            return
        shortcut_edits[shortcut_key].set_shortcut_text(DEFAULT_SHORTCUTS.get(shortcut_key, ""))
        refresh_shortcut_conflict_styles()

    def reset_all_shortcuts() -> None:
        for key, edit in shortcut_edits.items():
            edit.set_shortcut_text(DEFAULT_SHORTCUTS.get(key, ""))
        refresh_shortcut_conflict_styles()

    def apply_settings() -> bool:
        shortcuts = collect_shortcuts()
        if shortcuts is None:
            return False

        window.set_foxit_freetext(foxit_checkbox.isChecked())
        window.use_popup_freetext_input = popup_freetext_checkbox.isChecked()
        window.extract_highlight_text_on_reindex = extract_highlight_text_checkbox.isChecked()
        window.quick_audit_detailed = quick_audit_detailed_checkbox.isChecked()
        window.save_incremental_safety_default = save_incremental_safety_default_checkbox.isChecked()
        window.freetext_font_size_min = max(1, font_min_spin.value())
        window.freetext_font_size_max = max(window.freetext_font_size_min, font_max_spin.value())
        window.default_freetext_font_size = window.clamp_freetext_font_size(font_size_spin.value())
        window.search_page_size = max(1, search_page_size_spin.value())
        window.qpdf_bin_dir = qpdf_bin_dir_edit.text().strip()
        window.shortcuts = shortcuts
        window.apply_shortcuts()
        for key, edit in shortcut_edits.items():
            edit.setText(window.shortcuts.get(key, ""))
        refresh_shortcut_conflict_styles()
        if window.annotation_search_widget is not None:
            window.annotation_search_widget.set_page_size(window.search_page_size)
        try:
            window.save_app_settings()
            if window.doc is not None:
                window.render_page(preserve_selection=True)
        except Exception as exc:
            show_error(window, "Save settings failed", exc)
            return False
        return True

    apply_button.clicked.connect(lambda checked=False: apply_settings())
    apply_close_button.clicked.connect(lambda checked=False: dialog.accept() if apply_settings() else None)
    cancel_button.clicked.connect(lambda checked=False: dialog.reject())
    reset_selected_button.clicked.connect(lambda checked=False: reset_selected_shortcut())
    reset_all_button.clicked.connect(lambda checked=False: reset_all_shortcuts())
    for edit in shortcut_edits.values():
        edit.textChanged.connect(lambda _text="": refresh_shortcut_conflict_styles())
    refresh_shortcut_conflict_styles()

    dialog.exec()
