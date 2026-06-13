from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


class PdfCanvasView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)


class AnnotationScene(QGraphicsScene):
    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner

    def mousePressEvent(self, event) -> None:
        if self.owner.on_tool_mouse_press(event.scenePos()):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.owner.on_tool_mouse_move(event.scenePos()):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.owner.on_tool_mouse_release(event.scenePos()):
            return
        super().mouseReleaseEvent(event)
        self.owner.on_scene_mouse_release()

    def mouseDoubleClickEvent(self, event) -> None:
        super().mouseDoubleClickEvent(event)
        self.owner.on_scene_mouse_double_click()
