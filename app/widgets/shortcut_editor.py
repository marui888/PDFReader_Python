from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLineEdit

from app.services.shortcuts import normalized_shortcut


class ShortcutEditor(QLineEdit):
    focused = Signal(str)
    NORMAL_STYLE = (
        "QLineEdit {"
        "background: #ffffff;"
        "border: 1px solid #9a9a9a;"
        "padding: 2px 4px;"
        "min-height: 20px;"
        "}"
    )
    CONFLICT_STYLE = (
        "QLineEdit {"
        "background: #fff0f0;"
        "border: 1px solid #c00000;"
        "padding: 2px 4px;"
        "min-height: 20px;"
        "}"
    )

    def __init__(self, shortcut_key: str, parent=None) -> None:
        super().__init__(parent)
        self.shortcut_key = shortcut_key
        self.setPlaceholderText("Press shortcut, Backspace/Delete clears")
        self.setStyleSheet(self.NORMAL_STYLE)

    def shortcut_text(self) -> str:
        return normalized_shortcut(self.text())

    def set_shortcut_text(self, value: object) -> None:
        self.setText(normalized_shortcut(value))

    def set_conflict(self, is_conflict: bool) -> None:
        if is_conflict:
            self.setStyleSheet(self.CONFLICT_STYLE)
        else:
            self.setStyleSheet(self.NORMAL_STYLE)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear()
            event.accept()
            return
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_AltGr,
        ):
            event.accept()
            return
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            super().keyPressEvent(event)
            return

        shortcut_text = self.event_shortcut_text(event)
        if shortcut_text:
            self.setText(shortcut_text)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focused.emit(self.shortcut_key)

    def event_shortcut_text(self, event) -> str:
        try:
            sequence = QKeySequence(event.keyCombination())
        except AttributeError:
            modifiers = event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
            modifier_value = modifiers.value if hasattr(modifiers, "value") else int(modifiers)
            sequence = QKeySequence(modifier_value | int(event.key()))
        return sequence.toString(QKeySequence.SequenceFormat.PortableText)
