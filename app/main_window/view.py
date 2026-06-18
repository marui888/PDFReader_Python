from __future__ import annotations

from PySide6.QtWidgets import QLabel


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
        else:
            window.page_spin.setMaximum(len(window.doc))
            window.page_spin.setValue(window.page_index + 1)
    finally:
        window.updating_page_spin = False


def update_window_title(window) -> None:
    dirty_marker = " *" if window.is_dirty else ""
    if window.doc is None:
        window.setWindowTitle(f"PDF Note Reader{dirty_marker}")
        return
    name = window.pdf_path.name if window.pdf_path else "PDF"
    window.setWindowTitle(f"{name} - Page {window.page_index + 1}/{len(window.doc)} - {window.zoom:.0%}{dirty_marker}")


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
