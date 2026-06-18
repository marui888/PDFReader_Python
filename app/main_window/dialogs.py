from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)


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


def confirm_save_incremental(window) -> bool:
    return ask_yes_no(
        window,
        "Confirm Save Incremental",
        "Save Incremental writes changes directly into the current PDF without creating a backup.\n\n"
        "Continue?",
    )


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
    return ask_yes_no(window, "Delete Annotation", message)


def open_settings(window) -> None:
    dialog = QDialog(window)
    dialog.setWindowTitle("Settings")

    layout = QVBoxLayout(dialog)
    foxit_checkbox = QCheckBox("Experimental Foxit Typewriter compatibility")
    foxit_checkbox.setChecked(window.use_foxit_freetext)
    layout.addWidget(foxit_checkbox)

    extract_highlight_text_checkbox = QCheckBox("Extract highlighted page text when reindexing")
    extract_highlight_text_checkbox.setChecked(window.extract_highlight_text_on_reindex)
    layout.addWidget(extract_highlight_text_checkbox)

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
    layout.addLayout(form)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    window.set_foxit_freetext(foxit_checkbox.isChecked())
    window.extract_highlight_text_on_reindex = extract_highlight_text_checkbox.isChecked()
    window.freetext_font_size_min = max(1, font_min_spin.value())
    window.freetext_font_size_max = max(window.freetext_font_size_min, font_max_spin.value())
    window.default_freetext_font_size = window.clamp_freetext_font_size(font_size_spin.value())
    window.search_page_size = max(1, search_page_size_spin.value())
    if window.annotation_search_widget is not None:
        window.annotation_search_widget.set_page_size(window.search_page_size)
    try:
        window.save_app_settings()
        if window.doc is not None:
            window.render_page(preserve_selection=True)
    except Exception as exc:
        show_error(window, "Save settings failed", exc)
