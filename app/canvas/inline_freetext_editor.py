from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QSizeF, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QGraphicsProxyWidget, QGraphicsScene, QPlainTextEdit


class InlineFreeTextEdit(QPlainTextEdit):
    accepted = Signal(str)
    canceled = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
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
            self.canceled.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.accepted.emit(self.toPlainText())
            event.accept()
            return
        super().keyPressEvent(event)


class InlineFreeTextEditorManager:
    def __init__(
        self,
        scene: QGraphicsScene,
        zoom_getter: Callable[[], float],
        accepted_callback: Callable[[str, QPointF, QSizeF], None] | None = None,
        canceled_callback: Callable[[], None] | None = None,
    ) -> None:
        self.scene = scene
        self.zoom_getter = zoom_getter
        self.accepted_callback = accepted_callback
        self.canceled_callback = canceled_callback
        self.proxy: QGraphicsProxyWidget | None = None
        self.editor: InlineFreeTextEdit | None = None

    def is_active(self) -> bool:
        return self.proxy is not None

    def begin(
        self,
        scene_pos: QPointF,
        text: str = "",
        width: float = 220.0,
        height: float = 72.0,
        font_size: int = 7,
    ) -> None:
        self.cancel()
        editor = InlineFreeTextEdit(text)
        zoom = self.zoom_getter()
        font = QFont("Arial")
        font.setPixelSize(max(1, int(round(font_size * zoom))))
        editor.setFont(font)
        editor.setMinimumSize(max(40, int(width)), max(24, int(height)))
        editor.resize(max(40, int(width)), max(24, int(height)))
        editor.accepted.connect(self.accept)
        editor.canceled.connect(self.cancel)

        proxy = self.scene.addWidget(editor)
        proxy.setZValue(1000)
        proxy.setPos(scene_pos)
        self.editor = editor
        self.proxy = proxy
        editor.setFocus()
        editor.selectAll()

    def accept(self, text: str) -> None:
        if self.proxy is None or self.editor is None:
            return
        pos = self.proxy.pos()
        size = QSizeF(self.editor.width(), self.editor.height())
        self.clear()
        if self.accepted_callback is not None:
            self.accepted_callback(text, pos, size)

    def cancel(self) -> None:
        if self.proxy is None and self.editor is None:
            return
        self.clear()
        if self.canceled_callback is not None:
            self.canceled_callback()

    def clear(self) -> None:
        proxy = self.proxy
        editor = self.editor
        self.proxy = None
        self.editor = None
        if proxy is not None:
            self.scene.removeItem(proxy)
        if editor is not None:
            editor.deleteLater()
