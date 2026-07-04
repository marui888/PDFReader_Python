from __future__ import annotations

from collections.abc import Callable
from math import ceil

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPen, QTextCursor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QMenu,
    QPlainTextEdit,
)

from app.services.keyword_repository import KeywordGroup
from app.widgets.keyword_popup import KeywordPopup


class InlineFreeTextEdit(QPlainTextEdit):
    accepted = Signal(str)
    canceled = Signal()
    heightChanged = Signal(int)

    def __init__(
        self,
        text: str = "",
        keyword_groups: list[KeywordGroup] | None = None,
        stable_edit_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.keyword_groups = keyword_groups or []
        self.keyword_popup: KeywordPopup | None = None
        self.stable_edit_mode = stable_edit_mode
        self.auto_grow_height = False
        self.minimum_editor_height = 24
        self.maximum_editor_height: int | None = None
        self.adjust_height_pending = False
        self.visual_height_padding = 0
        self.height_limited_by_maximum = False
        self.setPlainText(text)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.setViewportMargins(0, 0, 0, 0)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            "QPlainTextEdit {"
            "background: rgba(255, 255, 255, 12);"
            "border: 1px solid rgba(20, 20, 20, 180);"
            "color: red;"
            "padding: 0px;"
            "margin: 0px;"
            "}"
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.accepted.emit(self.toPlainText())
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.accepted.emit(self.toPlainText())
            event.accept()
            return
        super().keyPressEvent(event)
        self.schedule_height_adjustment()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.stable_edit_mode and event.button() == Qt.MouseButton.LeftButton:
            cursor = self.cursorForPosition(event.position().toPoint())
            cursor.clearSelection()
            self.setTextCursor(cursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.stable_edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def configure_auto_grow_height(
        self,
        minimum_height: int,
        maximum_height: int | None,
        visual_height_padding: int = 0,
    ) -> None:
        self.auto_grow_height = True
        self.visual_height_padding = max(0, int(visual_height_padding))
        self.minimum_editor_height = max(14, int(minimum_height))
        self.maximum_editor_height = max(self.minimum_editor_height, int(maximum_height)) if maximum_height else None
        self.document().contentsChanged.connect(self.schedule_height_adjustment)
        self.schedule_height_adjustment()

    def adjust_height_to_content(self) -> None:
        self.adjust_height_pending = False
        if not self.auto_grow_height:
            return
        desired_height = max(self.minimum_editor_height, self.estimated_wrapped_text_height() + self.visual_height_padding)
        target_height = desired_height
        self.height_limited_by_maximum = False
        if self.maximum_editor_height is not None:
            target_height = min(target_height, self.maximum_editor_height)
            self.height_limited_by_maximum = target_height < desired_height
        if self.height() != target_height:
            self.heightChanged.emit(target_height)
        elif not self.height_limited_by_maximum:
            self.reset_internal_scroll_to_top()

    def reset_internal_scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(0)

    def schedule_height_adjustment(self) -> None:
        if self.adjust_height_pending:
            return
        self.adjust_height_pending = True
        QTimer.singleShot(0, self.adjust_height_to_content)

    def estimated_wrapped_text_height(self) -> int:
        metrics = QFontMetrics(self.font())
        usable_width = max(1, self.width() - 8)
        line_count = 0
        for paragraph in self.toPlainText().split("\n"):
            line_count += max(1, self.estimated_wrapped_line_count(paragraph, usable_width, metrics))
        return ceil(line_count * metrics.lineSpacing() + 2)

    def estimated_wrapped_line_count(self, text: str, usable_width: int, metrics: QFontMetrics) -> int:
        if not text:
            return 1
        line_count = 1
        current_width = 0
        for character in text:
            char_width = max(1, metrics.horizontalAdvance(character))
            if current_width > 0 and current_width + char_width > usable_width:
                line_count += 1
                current_width = char_width
            else:
                current_width += char_width
        return line_count

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self.show_keyword_popup(event.globalPos()):
            event.accept()
            return

        super().contextMenuEvent(event)

    def show_keyword_popup(self, global_pos) -> bool:
        if not self.keyword_groups:
            return False

        popup = KeywordPopup(self.keyword_groups, None)
        self.keyword_popup = popup
        popup.keyword_selected.connect(self.insert_text_at_cursor)
        popup.finished.connect(lambda _result: setattr(self, "keyword_popup", None))
        popup.move(global_pos.toPoint() if hasattr(global_pos, "toPoint") else global_pos)
        popup.show()
        return True

    def insert_text_at_cursor(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)


class InlineFreeTextGraphicsEdit(QGraphicsTextItem):
    EMPTY_CARET_PLACEHOLDER = " "

    def __init__(
        self,
        text: str,
        accepted_callback: Callable[[str], None],
        canceled_callback: Callable[[], None] | None = None,
        keyword_groups: list[KeywordGroup] | None = None,
    ) -> None:
        self.empty_placeholder_active = text == ""
        super().__init__(self.EMPTY_CARET_PLACEHOLDER if self.empty_placeholder_active else text)
        self.accepted_callback = accepted_callback
        self.canceled_callback = canceled_callback
        self.keyword_groups = keyword_groups or []
        self.keyword_popup: KeywordPopup | None = None
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setDefaultTextColor(QColor(255, 0, 0))
        self.document().setDocumentMargin(0)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsFocusable, True)

    def toPlainText(self) -> str:
        text = super().toPlainText()
        if text == self.EMPTY_CARET_PLACEHOLDER:
            return ""
        if self.empty_placeholder_active and text.startswith(self.EMPTY_CARET_PLACEHOLDER):
            return text[len(self.EMPTY_CARET_PLACEHOLDER) :]
        return text

    def clear_empty_placeholder(self) -> None:
        if not self.empty_placeholder_active:
            return
        self.empty_placeholder_active = False
        super().setPlainText("")
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)

    def ensure_empty_placeholder_if_needed(self) -> None:
        if self.empty_placeholder_active or super().toPlainText():
            return
        self.empty_placeholder_active = True
        super().setPlainText(self.EMPTY_CARET_PLACEHOLDER)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.empty_placeholder_active and self.canceled_callback is not None:
                self.canceled_callback()
                event.accept()
                return
            self.accepted_callback(self.toPlainText())
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            if self.empty_placeholder_active and self.canceled_callback is not None:
                self.canceled_callback()
                event.accept()
                return
            self.accepted_callback(self.toPlainText())
            event.accept()
            return
        if self.empty_placeholder_active:
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                event.accept()
                return
            if event.text():
                self.clear_empty_placeholder()
        super().keyPressEvent(event)
        self.ensure_empty_placeholder_if_needed()

    def inputMethodEvent(self, event) -> None:
        if self.empty_placeholder_active and (event.commitString() or event.preeditString()):
            self.clear_empty_placeholder()
        super().inputMethodEvent(event)
        self.ensure_empty_placeholder_if_needed()

    def contextMenuEvent(self, event) -> None:
        self.show_context_menu(event.screenPos())
        event.accept()

    def show_context_menu(self, global_pos) -> bool:
        menu = QMenu()
        keyword_action = menu.addAction("Keywords...")
        keyword_action.setEnabled(bool(self.keyword_groups))
        menu.addSeparator()

        cursor = self.textCursor()
        has_selection = cursor.hasSelection()
        clipboard_text = QApplication.clipboard().text()

        cut_action = menu.addAction("Cut")
        cut_action.setEnabled(has_selection)
        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(has_selection)
        paste_action = menu.addAction("Paste")
        paste_action.setEnabled(bool(clipboard_text))
        select_all_action = menu.addAction("Select All")
        select_all_action.setEnabled(bool(self.toPlainText()))

        selected_action = menu.exec(global_pos)
        if selected_action == keyword_action:
            self.show_keyword_popup(global_pos)
        elif selected_action == cut_action:
            self.cut_selection()
        elif selected_action == copy_action:
            self.copy_selection()
        elif selected_action == paste_action:
            self.insert_text_at_cursor(clipboard_text)
        elif selected_action == select_all_action:
            self.select_all_text()
        return True

    def show_keyword_popup(self, global_pos) -> bool:
        if not self.keyword_groups:
            return False

        popup = KeywordPopup(self.keyword_groups, None)
        self.keyword_popup = popup
        popup.keyword_selected.connect(self.insert_text_at_cursor)
        popup.finished.connect(lambda _result: setattr(self, "keyword_popup", None))
        popup.move(global_pos.toPoint() if hasattr(global_pos, "toPoint") else global_pos)
        popup.show()
        return True

    def insert_text_at_cursor(self, text: str) -> None:
        self.clear_empty_placeholder()
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def copy_selection(self) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            QApplication.clipboard().setText(cursor.selectedText().replace("\u2029", "\n"))

    def cut_selection(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        QApplication.clipboard().setText(cursor.selectedText().replace("\u2029", "\n"))
        cursor.removeSelectedText()
        self.setTextCursor(cursor)
        self.ensure_empty_placeholder_if_needed()

    def select_all_text(self) -> None:
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)


class InlineFreeTextEditorManager:
    def __init__(
        self,
        scene: QGraphicsScene,
        zoom_getter: Callable[[], float],
        accepted_callback: Callable[[str, QPointF, QSizeF], None] | None = None,
        canceled_callback: Callable[[], None] | None = None,
        keyword_groups: list[KeywordGroup] | None = None,
        use_graphics_editor_getter: Callable[[], bool] | None = None,
    ) -> None:
        self.scene = scene
        self.zoom_getter = zoom_getter
        self.accepted_callback = accepted_callback
        self.canceled_callback = canceled_callback
        self.keyword_groups = keyword_groups or []
        self.use_graphics_editor_getter = use_graphics_editor_getter or (lambda: False)
        self.proxy: QGraphicsProxyWidget | None = None
        self.editor: InlineFreeTextEdit | None = None
        self.graphics_editor: InlineFreeTextGraphicsEdit | None = None
        self.graphics_border: QGraphicsRectItem | None = None
        self.visual_height_padding = 0
        self.graphics_width = 0
        self.graphics_min_height = 0
        self.graphics_max_height: float | None = None

    def is_active(self) -> bool:
        return self.proxy is not None or self.graphics_editor is not None

    def contains_scene_pos(self, scene_pos: QPointF) -> bool:
        if self.graphics_border is not None:
            return self.graphics_border.sceneBoundingRect().contains(scene_pos)
        if self.proxy is None:
            return False
        return self.proxy.sceneBoundingRect().contains(scene_pos)

    def show_keyword_popup(self, global_pos) -> bool:
        if self.graphics_editor is not None:
            return self.graphics_editor.show_context_menu(global_pos)
        if self.editor is None:
            return False
        return self.editor.show_keyword_popup(global_pos)

    def begin(
        self,
        scene_pos: QPointF,
        text: str = "",
        width: float = 220.0,
        height: float = 72.0,
        font_size: int = 7,
        select_all: bool = True,
        stable_edit_mode: bool = False,
        max_height: float | None = None,
    ) -> None:
        self.cancel()
        if self.use_graphics_editor_getter():
            self.begin_graphics(
                scene_pos,
                text,
                width,
                height,
                font_size,
                select_all,
                max_height,
            )
            return

        editor = InlineFreeTextEdit(text, self.keyword_groups, stable_edit_mode=stable_edit_mode)
        zoom = self.zoom_getter()
        font = QFont("Arial")
        font.setPixelSize(max(1, int(round(font_size * zoom))))
        editor.setFont(font)
        initial_width = max(40, int(width))
        initial_commit_height = max(24, int(height))
        self.visual_height_padding = self.editor_visual_height_padding(font) if stable_edit_mode else 0
        initial_height = initial_commit_height + self.visual_height_padding
        if max_height is not None:
            initial_height = min(initial_height, max(14, int(max_height)))
            self.visual_height_padding = max(0, initial_height - initial_commit_height)
        editor.setFixedSize(initial_width, initial_height)
        editor.accepted.connect(self.accept)
        editor.canceled.connect(self.cancel)
        editor.heightChanged.connect(self.resize_editor_height)

        proxy = self.scene.addWidget(editor)
        proxy.setZValue(1000)
        proxy.setPos(scene_pos)
        proxy.resize(initial_width, initial_height)
        self.editor = editor
        self.proxy = proxy
        editor.configure_auto_grow_height(
            initial_height,
            int(max_height) if max_height is not None else None,
            self.visual_height_padding,
        )
        editor.setFocus()
        if select_all:
            editor.selectAll()
        else:
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            editor.setTextCursor(cursor)

    def begin_graphics(
        self,
        scene_pos: QPointF,
        text: str,
        width: float,
        height: float,
        font_size: int,
        select_all: bool,
        max_height: float | None,
    ) -> None:
        zoom = self.zoom_getter()
        font = QFont("Arial")
        font.setPixelSize(max(1, int(round(font_size * zoom))))
        editor = InlineFreeTextGraphicsEdit(text, self.accept_graphics, self.cancel, self.keyword_groups)
        editor.setFont(font)
        editor.setTextWidth(max(40.0, float(width)))
        editor.setPos(scene_pos)
        editor.setZValue(1001)

        border = self.scene.addRect(QRectF(scene_pos, QSizeF(max(40.0, width), max(24.0, height))), QPen(QColor(20, 20, 20, 180), 1))
        border.setZValue(1000)
        self.scene.addItem(editor)

        self.graphics_editor = editor
        self.graphics_border = border
        self.graphics_width = max(40, int(width))
        self.graphics_min_height = max(24, int(height))
        self.graphics_max_height = max_height
        editor.document().contentsChanged.connect(lambda: self.resize_graphics_editor(max_height))
        self.resize_graphics_editor(max_height)
        cursor = editor.textCursor()
        if select_all:
            cursor.select(QTextCursor.SelectionType.Document)
        else:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        self.scene.setFocusItem(editor, Qt.FocusReason.MouseFocusReason)
        editor.setFocus(Qt.FocusReason.MouseFocusReason)

    def resize_graphics_editor(self, max_height: float | None = None) -> None:
        if self.graphics_editor is None or self.graphics_border is None:
            return
        height = max(float(self.graphics_min_height), self.graphics_editor.boundingRect().height() + 2.0)
        if max_height is not None:
            height = min(height, max(14.0, float(max_height)))
        rect = QRectF(self.graphics_editor.pos(), QSizeF(float(self.graphics_width), height))
        self.graphics_border.setRect(rect)
        self.scene.update(rect)

    def resize_editor_height(self, height: int) -> None:
        if self.editor is None or self.proxy is None:
            return
        width = self.editor.width()
        self.editor.setFixedSize(width, height)
        self.editor.updateGeometry()
        self.editor.viewport().update()
        self.proxy.resize(width, height)
        self.proxy.update()
        self.scene.update(self.proxy.sceneBoundingRect())
        if not self.editor.height_limited_by_maximum:
            self.editor.reset_internal_scroll_to_top()

    def editor_visual_height_padding(self, font: QFont) -> int:
        metrics = QFontMetrics(font)
        return max(4, int(ceil(metrics.lineSpacing() * 0.35)))

    def accept(self, text: str) -> None:
        if self.proxy is None or self.editor is None:
            return
        self.editor.adjust_height_to_content()
        pos = self.proxy.pos()
        committed_height = max(14, self.editor.height() - self.visual_height_padding)
        size = QSizeF(self.editor.width(), committed_height)
        self.clear()
        if self.accepted_callback is not None:
            self.accepted_callback(text, pos, size)

    def accept_graphics(self, text: str) -> None:
        if self.graphics_editor is None:
            return
        self.resize_graphics_editor(self.graphics_max_height)
        pos = self.graphics_editor.pos()
        height = max(float(self.graphics_min_height), self.graphics_editor.boundingRect().height() + 2.0)
        if self.graphics_max_height is not None:
            height = min(height, max(14.0, float(self.graphics_max_height)))
        size = QSizeF(float(self.graphics_width), height)
        self.clear()
        if self.accepted_callback is not None:
            self.accepted_callback(text, pos, size)

    def accept_current(self) -> None:
        if self.graphics_editor is not None:
            self.accept_graphics(self.graphics_editor.toPlainText())
            return
        if self.editor is None:
            return
        self.accept(self.editor.toPlainText())

    def cancel(self) -> None:
        if self.proxy is None and self.editor is None and self.graphics_editor is None:
            return
        self.clear()
        if self.canceled_callback is not None:
            self.canceled_callback()

    def clear(self) -> None:
        proxy = self.proxy
        editor = self.editor
        graphics_editor = self.graphics_editor
        graphics_border = self.graphics_border
        self.proxy = None
        self.editor = None
        self.graphics_editor = None
        self.graphics_border = None
        self.visual_height_padding = 0
        self.graphics_width = 0
        self.graphics_min_height = 0
        self.graphics_max_height = None
        if proxy is not None:
            self.scene.removeItem(proxy)
        if editor is not None:
            editor.deleteLater()
        if graphics_editor is not None:
            self.scene.removeItem(graphics_editor)
        if graphics_border is not None:
            self.scene.removeItem(graphics_border)
