import re
from collections.abc import Callable
from math import atan2, cos, pi, sin

import pymupdf as fitz
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from app.models import AnnotationModel


class AnnotationItemRenderer:
    def __init__(
        self,
        scene: QGraphicsScene,
        zoom: float,
        default_freetext_font_size: int,
        default_highlight_opacity: float,
        register_item: Callable[[QGraphicsItem, AnnotationModel], None],
        register_hit_item: Callable[[QGraphicsItem, AnnotationModel], None],
    ) -> None:
        self.scene = scene
        self.zoom = zoom
        self.default_freetext_font_size = default_freetext_font_size
        self.default_highlight_opacity = default_highlight_opacity
        self.register_item = register_item
        self.register_hit_item = register_hit_item

    def render(self, model: AnnotationModel) -> None:
        if model.app_type == "highlight":
            self.add_highlight_item(model)
        elif model.app_type == "freetext":
            self.add_freetext_item(model)
        elif model.app_type == "square":
            self.add_square_item(model)
        elif model.app_type == "arrow":
            self.add_arrow_item(model)

    def add_highlight_item(self, model: AnnotationModel) -> None:
        color = self.pdf_color(model.color, QColor(255, 235, 59))
        opacity = model.opacity if model.opacity is not None else self.default_highlight_opacity
        color.setAlpha(max(20, min(255, int(255 * opacity))))

        quad_polygons = self.highlight_polygons(model)
        if quad_polygons:
            for polygon in quad_polygons:
                item = self.scene.addPolygon(polygon, QPen(Qt.PenStyle.NoPen), QBrush(color))
                self.register_item(item, model)
                self.add_highlight_hit_item(polygon, model)
            return

        rect = self.scene_rect(model.rect)
        item = self.scene.addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(color))
        self.register_item(item, model)
        self.add_rect_hit_item(rect, model)

    def add_freetext_item(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        text_item = self.scene.addText(model.text)
        text_item.setDefaultTextColor(self.pdf_color(model.color, QColor(255, 0, 0)))
        font_size = model.font_size or self.default_freetext_font_size
        text_item.setFont(QFont("Arial", max(1, int(font_size * self.zoom))))
        text_item.setTextWidth(rect.width())
        text_item.setPos(rect.topLeft())
        self.register_item(text_item, model)
        self.add_rect_hit_item(rect, model)

    def add_square_item(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        color = self.pdf_color(model.color, QColor(255, 0, 0))
        width = max(1.0, (model.border_width or 1.0) * self.zoom)
        item = self.scene.addRect(rect, QPen(color, width), QBrush(Qt.BrushStyle.NoBrush))
        self.register_item(item, model)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.add_square_hit_item(rect, model)

    def add_arrow_item(self, model: AnnotationModel) -> None:
        start, end = self.arrow_points(model)
        color = self.pdf_color(model.color, QColor(255, 0, 0))
        width = max(1.0, (model.border_width or 1.0) * self.zoom)
        pen = QPen(color, width)
        line_item = self.scene.addLine(QLineF(start, end), pen)
        self.register_item(line_item, model)
        self.add_line_hit_item(start, end, model)

        start_arrow, end_arrow = self.arrow_head_flags(model.line_ending)
        size = max(8.0, 9.0 * self.zoom)
        if start_arrow:
            self.add_arrow_head_lines(end, start, size, pen, model)
        if end_arrow:
            self.add_arrow_head_lines(start, end, size, pen, model)

    def add_rect_hit_item(self, rect: QRectF, model: AnnotationModel) -> None:
        margin = max(4.0, 4.0 * self.zoom)
        hit_rect = rect.adjusted(-margin, -margin, margin, margin)
        item = self.scene.addRect(hit_rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0)))
        self.register_hit_item(item, model)

    def add_square_hit_item(self, rect: QRectF, model: AnnotationModel) -> None:
        width = max(8.0, ((model.border_width or 1.0) * self.zoom) + 6.0)
        pen = QPen(QColor(0, 0, 0), width)
        lines = (
            QLineF(rect.topLeft(), rect.topRight()),
            QLineF(rect.topRight(), rect.bottomRight()),
            QLineF(rect.bottomRight(), rect.bottomLeft()),
            QLineF(rect.bottomLeft(), rect.topLeft()),
        )
        for line in lines:
            item = self.scene.addLine(line, pen)
            self.register_hit_item(item, model)

    def add_highlight_hit_item(self, polygon: QPolygonF, model: AnnotationModel) -> None:
        margin = max(5.0, 5.0 * self.zoom)
        hit_rect = polygon.boundingRect().adjusted(-margin, -margin, margin, margin)
        item = self.scene.addRect(hit_rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0)))
        self.register_hit_item(item, model)

    def add_line_hit_item(self, start: QPointF, end: QPointF, model: AnnotationModel) -> None:
        width = max(12.0, 10.0 * self.zoom)
        item = self.scene.addLine(QLineF(start, end), QPen(QColor(0, 0, 0), width))
        self.register_hit_item(item, model)

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

    def add_arrow_head_lines(
        self, start: QPointF, end: QPointF, size: float, pen: QPen, model: AnnotationModel
    ) -> None:
        left, right = self.arrow_head_points(start, end, size)
        for point in (left, right):
            item = self.scene.addLine(QLineF(end, point), pen)
            self.register_item(item, model)

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
