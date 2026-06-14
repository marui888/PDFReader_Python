import re
from math import atan2, cos, pi, sin

import pymupdf as fitz
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from app.models import AnnotationModel


class AnnotationSelectionRenderer:
    def __init__(self, scene: QGraphicsScene, zoom: float) -> None:
        self.scene = scene
        self.zoom = zoom
        self.items: list[QGraphicsItem] = []

    def draw(self, model: AnnotationModel) -> list[QGraphicsItem]:
        self.items = []
        if model.app_type == "freetext":
            self.draw_freetext_selection(model)
        elif model.app_type == "square":
            self.draw_square_selection(model)
        elif model.app_type == "highlight":
            self.draw_highlight_selection(model)
        elif model.app_type == "arrow":
            self.draw_arrow_selection(model)
        return self.items

    def add_selection_item(self, item: QGraphicsItem) -> None:
        item.setZValue(20)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.items.append(item)

    def draw_rect_selection(self, rect: QRectF, color: QColor, width: float, model: AnnotationModel) -> None:
        item = self.scene.addRect(rect, QPen(color, width), QBrush(Qt.BrushStyle.NoBrush))
        item.setData(2, "selection-rect")
        item.setData(3, model.id)
        self.add_selection_item(item)

    def draw_square_selection(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        self.draw_rect_selection(rect, QColor(0, 0, 0), 3.0, model)
        self.add_rect_resize_handles(rect, model)

    def draw_freetext_selection(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        self.draw_rect_selection(rect, QColor(0, 0, 0), 1.2, model)
        self.add_rect_resize_handles(rect, model)

    def add_rect_resize_handles(self, rect: QRectF, model: AnnotationModel) -> None:
        size = max(6.0, 5.0 * self.zoom)
        handles = (
            ("top-left", rect.topLeft(), Qt.CursorShape.SizeFDiagCursor),
            ("top", QPointF(rect.center().x(), rect.top()), Qt.CursorShape.SizeVerCursor),
            ("top-right", rect.topRight(), Qt.CursorShape.SizeBDiagCursor),
            ("right", QPointF(rect.right(), rect.center().y()), Qt.CursorShape.SizeHorCursor),
            ("bottom-right", rect.bottomRight(), Qt.CursorShape.SizeFDiagCursor),
            ("bottom", QPointF(rect.center().x(), rect.bottom()), Qt.CursorShape.SizeVerCursor),
            ("bottom-left", rect.bottomLeft(), Qt.CursorShape.SizeBDiagCursor),
            ("left", QPointF(rect.left(), rect.center().y()), Qt.CursorShape.SizeHorCursor),
        )
        for name, point, cursor in handles:
            handle_rect = QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            item = self.scene.addRect(handle_rect, QPen(QColor(0, 0, 0), 1), QBrush(QColor(255, 255, 255)))
            item.setZValue(25)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setCursor(cursor)
            item.setData(0, model.id)
            item.setData(2, "resize-handle")
            item.setData(3, model.id)
            item.setData(4, name)
            item.setData(5, item.pos())
            self.items.append(item)

    def draw_highlight_selection(self, model: AnnotationModel) -> None:
        fill = QColor(92, 54, 24, 145)
        border = QPen(QColor(220, 0, 0), 1.0)
        border.setStyle(Qt.PenStyle.DashLine)
        polygons = self.highlight_polygons(model)
        if polygons:
            for polygon in polygons:
                item = self.scene.addPolygon(polygon, border, QBrush(fill))
                self.add_selection_item(item)
            return

        item = self.scene.addRect(self.scene_rect(model.rect), border, QBrush(fill))
        self.add_selection_item(item)

    def draw_arrow_selection(self, model: AnnotationModel) -> None:
        start, end = self.arrow_points(model)
        color = self.pdf_color(model.color, QColor(255, 0, 0))
        base_width = max(1.0, (model.border_width or 1.0) * self.zoom)
        pen = QPen(color, base_width + max(1.0, 1.0 * self.zoom))
        item = self.scene.addLine(QLineF(start, end), pen)
        self.add_selection_item(item)

        start_arrow, end_arrow = self.arrow_head_flags(model.line_ending)
        size = max(8.0, 9.0 * self.zoom)
        if start_arrow:
            self.add_selection_arrow_head_lines(end, start, size, pen)
        if end_arrow:
            self.add_selection_arrow_head_lines(start, end, size, pen)
        self.add_endpoint_handles(model, start, end, QColor(0, 0, 0))

    def add_selection_arrow_head_lines(self, start: QPointF, end: QPointF, size: float, pen: QPen) -> None:
        left, right = self.arrow_head_points(start, end, size)
        for point in (left, right):
            item = self.scene.addLine(QLineF(end, point), pen)
            self.add_selection_item(item)

    def add_endpoint_handles(self, model: AnnotationModel, start: QPointF, end: QPointF, color: QColor) -> None:
        size = max(5.0, 5.0 * self.zoom)
        for name, point in (("start", start), ("end", end)):
            rect = QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            item = self.scene.addRect(rect, QPen(color, 1), QBrush(QColor(255, 255, 255)))
            item.setZValue(25)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setCursor(Qt.CursorShape.SizeAllCursor)
            item.setData(0, model.id)
            item.setData(2, "arrow-endpoint-handle")
            item.setData(3, model.id)
            item.setData(4, name)
            item.setData(5, item.pos())
            self.items.append(item)

    def scene_rect(self, rect: fitz.Rect) -> QRectF:
        return QRectF(
            rect.x0 * self.zoom,
            rect.y0 * self.zoom,
            rect.width * self.zoom,
            rect.height * self.zoom,
        )

    def scene_point(self, point: tuple[float, float]) -> QPointF:
        return QPointF(point[0] * self.zoom, point[1] * self.zoom)

    def pdf_color(self, color: tuple | None, fallback: QColor) -> QColor:
        if not color:
            return QColor(fallback)
        channels = [max(0, min(255, int(value * 255))) for value in color[:3]]
        if len(channels) < 3:
            return QColor(fallback)
        return QColor(channels[0], channels[1], channels[2])

    def arrow_points(self, model: AnnotationModel) -> tuple[QPointF, QPointF]:
        if model.line_start and model.line_end:
            return self.scene_point(model.line_start), self.scene_point(model.line_end)

        rect = model.rect
        return (
            QPointF(rect.x0 * self.zoom, rect.y0 * self.zoom),
            QPointF(rect.x1 * self.zoom, rect.y1 * self.zoom),
        )

    def highlight_polygons(self, model: AnnotationModel) -> list[QPolygonF]:
        polygons: list[QPolygonF] = []
        points = model.quad_points
        for index in range(0, len(points) - 3, 4):
            quad = points[index : index + 4]
            ordered_quad = (quad[0], quad[1], quad[3], quad[2])
            polygons.append(QPolygonF([self.scene_point(point) for point in ordered_quad]))
        return polygons

    def arrow_head_points(self, start: QPointF, end: QPointF, size: float) -> tuple[QPointF, QPointF]:
        angle = atan2(end.y() - start.y(), end.x() - start.x())
        half_angle = pi / 12
        left = QPointF(
            end.x() - size * cos(angle - half_angle),
            end.y() - size * sin(angle - half_angle),
        )
        right = QPointF(
            end.x() - size * cos(angle + half_angle),
            end.y() - size * sin(angle + half_angle),
        )
        return left, right

    def arrow_head_flags(self, line_ending: str) -> tuple[bool, bool]:
        endings = re.findall(r"/?([A-Za-z]*Arrow|None)", line_ending)
        if len(endings) >= 2:
            return "Arrow" in endings[0], "Arrow" in endings[1]
        if "Arrow" in line_ending:
            return False, True
        return False, True
