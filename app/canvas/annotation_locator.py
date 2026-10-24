"""Short-lived search target marker; never changes the PDF or its annotations."""
from time import monotonic
from math import isfinite

from PySide6.QtCore import QObject, QTimer, Qt, Slot
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem
from shiboken6 import isValid


class AnnotationLocator(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.items = []
        self.target_id = None
        self.context = None
        self.deadline = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)

    def _context(self):
        window = self.window
        return (id(window.doc), id(getattr(window, "session", None)),
                window.page_index, window.zoom, window.view_mode)

    def show(self, model):
        self.stop()
        window = self.window
        controller = window.canvas_controller
        rect = (controller.continuous_annotation_scene_rect(model)
                if window.view_mode == "continuous" else controller.scene_rect(model.rect))
        if not all(isfinite(value) for value in (rect.x(), rect.y(), rect.width(), rect.height())):
            return
        rect = rect.normalized().adjusted(-5, -5, 5, 5)
        if not rect.isValid():
            return
        for color, width, z_value in (("white", 6, 10000), ("#e60073", 3, 10001)):
            item = QGraphicsRectItem(rect)
            pen = QPen(QColor(color))
            pen.setWidthF(width)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(z_value)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setAcceptHoverEvents(False)
            window.scene.addItem(item)
            self.items.append(item)
        self.target_id = model.id
        self.context = self._context()
        self.deadline = monotonic() + 2.0
        self.timer.start()

    @Slot()
    def _tick(self):
        window = self.window
        if (monotonic() >= self.deadline or self.context != self._context()
                or window.reading_workspace.active is not window
                or any(not isValid(item) or item.scene() is not window.scene for item in self.items)):
            self.stop()
            return
        visible = not self.items[0].isVisible()
        for item in self.items:
            item.setVisible(visible)

    def stop(self):
        self.timer.stop()
        for item in self.items:
            if isValid(item) and item.scene() is not None:
                item.scene().removeItem(item)
        self.items.clear()
        self.target_id = None
        self.context = None
