from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence

from app.models.annotation_model import QUICK_HIGHLIGHT_COLORS


DEFAULT_SHORTCUTS: dict[str, str] = {
    "highlight_color_1": "Alt+Shift+A",
    "highlight_color_2": "Alt+Shift+S",
    "highlight_color_3": "Alt+Shift+D",
    "highlight_color_4": "Alt+Shift+Z",
    "highlight_color_5": "Alt+Shift+X",
    "highlight_color_6": "Alt+Shift+C",
    "tool_square": "Alt+G",
    "tool_arrow": "Alt+R",
    "tool_highlight": "Alt+S",
    "tool_freetext": "Alt+O",
    "tool_text": "Alt+T",
    "close_pdf": "Ctrl+W",
    "save_incremental": "Ctrl+S",
}


SHORTCUT_LABELS: dict[str, str] = {
    "highlight_color_1": "Highlight Color 1",
    "highlight_color_2": "Highlight Color 2",
    "highlight_color_3": "Highlight Color 3",
    "highlight_color_4": "Highlight Color 4",
    "highlight_color_5": "Highlight Color 5",
    "highlight_color_6": "Highlight Color 6",
    "tool_square": "Square Mode",
    "tool_arrow": "Arrow Mode",
    "tool_highlight": "Highlight Mode",
    "tool_freetext": "FreeText Mode",
    "tool_text": "Text Mode",
    "close_pdf": "Close Current PDF",
    "save_incremental": "Save Incremental",
}


def normalized_shortcut(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)


def merged_shortcuts(value: Mapping[str, object] | None) -> dict[str, str]:
    shortcuts = dict(DEFAULT_SHORTCUTS)
    if not isinstance(value, Mapping):
        return shortcuts

    for key in DEFAULT_SHORTCUTS:
        if key in value:
            shortcuts[key] = normalized_shortcut(value[key])
    if shortcuts["highlight_color_6"] == "Alt+Shift+V":
        shortcuts["highlight_color_6"] = DEFAULT_SHORTCUTS["highlight_color_6"]
    return shortcuts


def find_shortcut_conflicts(shortcuts: Mapping[str, object]) -> list[tuple[str, str, str]]:
    seen: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    for key in DEFAULT_SHORTCUTS:
        shortcut_text = normalized_shortcut(shortcuts.get(key, ""))
        if not shortcut_text:
            continue
        normalized_key = shortcut_text.lower()
        if normalized_key in seen:
            conflicts.append((shortcut_text, seen[normalized_key], key))
            continue
        seen[normalized_key] = key
    return conflicts


def apply_shortcuts(window) -> None:
    shortcuts = merged_shortcuts(getattr(window, "shortcuts", {}))
    window.shortcuts = shortcuts

    for shortcut in getattr(window, "shortcut_objects", []):
        shortcut.setEnabled(False)
        window.removeAction(shortcut)
        shortcut.deleteLater()
    window.shortcut_objects = []

    set_action_shortcut(window.add_rectangle_action, shortcuts["tool_square"])
    set_action_shortcut(window.add_arrow_action, shortcuts["tool_arrow"])
    set_action_shortcut(window.add_highlight_action, shortcuts["tool_highlight"])
    set_action_shortcut(window.add_typewriter_action, shortcuts["tool_freetext"])
    set_action_shortcut(window.text_mode_action, shortcuts["tool_text"])
    set_action_shortcut(window.close_action, shortcuts["close_pdf"])
    set_action_shortcut(window.save_incremental_action, shortcuts["save_incremental"])

    for index, color in enumerate(QUICK_HIGHLIGHT_COLORS.values(), start=1):
        shortcut_text = shortcuts.get(f"highlight_color_{index}", "")
        if not shortcut_text:
            continue
        shortcut = QAction(f"Highlight Color {index}", window)
        shortcut.setShortcut(QKeySequence(shortcut_text))
        shortcut.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        rgb_color = tuple(color[:3])
        shortcut.triggered.connect(
            lambda checked=False, selected_color=rgb_color: window.apply_quick_highlight_color(selected_color)
        )
        window.addAction(shortcut)
        window.shortcut_objects.append(shortcut)


def set_action_shortcut(action, shortcut_text: str) -> None:
    action.setShortcut(QKeySequence(shortcut_text) if shortcut_text else QKeySequence())
    action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
