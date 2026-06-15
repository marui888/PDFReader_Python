from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QPainter, QWheelEvent
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
        self.boundary_turn_direction: str | None = None
        self.boundary_turn_count = 0

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.owner is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.owner.zoom_in()
            elif event.angleDelta().y() < 0:
                self.owner.zoom_out()
            event.accept()
            return
        if self.owner is not None and self.handle_boundary_turn_intent(event.angleDelta().y()):
            event.accept()
            return
        self.reset_boundary_turn_state()
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.owner is not None:
            if event.key() == Qt.Key.Key_PageUp and self.is_at_top():
                self.handle_boundary_turn_intent(1)
                event.accept()
                return
            if event.key() == Qt.Key.Key_PageDown and self.is_at_bottom():
                self.handle_boundary_turn_intent(-1)
                event.accept()
                return
            self.reset_boundary_turn_state()
        super().keyPressEvent(event)

    def handle_boundary_turn_intent(self, delta_y: int) -> bool:
        if delta_y == 0:
            return False
        if delta_y > 0 and self.is_at_top():
            self.register_boundary_turn_intent("up")
            return True
        if delta_y < 0 and self.is_at_bottom():
            self.register_boundary_turn_intent("down")
            return True
        return False

    def register_boundary_turn_intent(self, direction: str) -> None:
        if self.boundary_turn_direction == direction:
            self.boundary_turn_count += 1
        else:
            self.boundary_turn_direction = direction
            self.boundary_turn_count = 1

        if self.owner is not None:
            self.owner.show_scroll_boundary_status(direction)

        if self.boundary_turn_count < 2:
            return

        self.reset_boundary_turn_state(clear_status=False)
        if direction == "up":
            self.owner.prev_page()
        elif direction == "down":
            self.owner.next_page()

    def reset_boundary_turn_state(self, clear_status: bool = True) -> None:
        self.boundary_turn_direction = None
        self.boundary_turn_count = 0
        if clear_status and self.owner is not None:
            self.owner.clear_scroll_boundary_status()

    def is_at_top(self) -> bool:
        vertical_scrollbar = self.verticalScrollBar()
        return vertical_scrollbar.value() <= vertical_scrollbar.minimum()

    def is_at_bottom(self) -> bool:
        vertical_scrollbar = self.verticalScrollBar()
        return vertical_scrollbar.value() >= vertical_scrollbar.maximum()


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
