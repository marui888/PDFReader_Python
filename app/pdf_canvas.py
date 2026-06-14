from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


class PdfCanvasView(QGraphicsView):
    def __init__(self, owner=None) -> None:
        super().__init__()
        self.owner = owner
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

    def wheelEvent(self, event) -> None:
        if self.owner is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.owner.zoom_in()
            elif event.angleDelta().y() < 0:
                self.owner.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)


class AnnotationScene(QGraphicsScene):
    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner

    def mousePressEvent(self, event) -> None:
        if self.owner.on_tool_mouse_press(event.scenePos()):
            return
        self.owner.on_scene_mouse_press(event.scenePos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.owner.on_tool_mouse_move(event.scenePos()):
            return
        super().mouseMoveEvent(event)
        self.owner.on_scene_mouse_move(event.scenePos())

    def mouseReleaseEvent(self, event) -> None:
        if self.owner.on_tool_mouse_release(event.scenePos()):
            return
        super().mouseReleaseEvent(event)
        self.owner.on_scene_mouse_release(event.scenePos())

    def mouseDoubleClickEvent(self, event) -> None:
        super().mouseDoubleClickEvent(event)
        self.owner.on_scene_mouse_double_click()

    def contextMenuEvent(self, event) -> None:
        self.owner.show_annotation_context_menu(event.scenePos(), event.screenPos())
