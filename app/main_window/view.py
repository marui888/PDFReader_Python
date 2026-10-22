from __future__ import annotations

from PySide6.QtWidgets import QLabel

from app.services.page_labels import page_label_for_index


def create_scroll_boundary_label(window) -> None:
    window.scroll_boundary_label = QLabel("")
    window.scroll_boundary_label.setVisible(False)
    window.scroll_boundary_label.setStyleSheet(
        "QLabel { color: white; background: rgb(190, 80, 0); padding: 2px 8px; font-weight: 700; }"
    )
    window.statusBar().addPermanentWidget(window.scroll_boundary_label)


def sync_page_spin(window) -> None:
    window.updating_page_spin = True
    try:
        if window.doc is None:
            window.page_spin.setValue(1)
            if hasattr(window, "book_label_input"):
                window.book_label_input.clear()
        else:
            window.page_spin.setMaximum(len(window.doc))
            window.page_spin.setValue(window.page_index + 1)
            if hasattr(window, "book_label_input"):
                window.book_label_input.setText(page_label_for_index(window.doc, window.page_index))
    finally:
        window.updating_page_spin = False


def update_window_title(window) -> None:
    window.setWindowTitle("PDF Note Reader")


def show_scroll_boundary_status(window, direction: str) -> None:
    if direction == "up":
        window.scroll_boundary_label.setText("\u5df2\u5230\u9876")
        window.scroll_boundary_label.setStyleSheet(
            "QLabel { color: white; background: rgb(30, 120, 190); padding: 2px 8px; font-weight: 700; }"
        )
    elif direction == "down":
        window.scroll_boundary_label.setText("\u5df2\u5230\u5e95")
        window.scroll_boundary_label.setStyleSheet(
            "QLabel { color: white; background: rgb(190, 80, 0); padding: 2px 8px; font-weight: 700; }"
        )
    else:
        window.scroll_boundary_label.setText("")
    window.scroll_boundary_label.setVisible(bool(window.scroll_boundary_label.text()))


def clear_scroll_boundary_status(window) -> None:
    window.scroll_boundary_label.clear()
    window.scroll_boundary_label.setVisible(False)
