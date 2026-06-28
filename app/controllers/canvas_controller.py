from __future__ import annotations

import re
from math import atan2, cos, pi, sin
from time import perf_counter

import pymupdf as fitz
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from app.canvas.annotation_items import AnnotationItemRenderer
from app.models.annotation_model import DRAGGABLE_APP_TYPES, AnnotationModel


class CanvasController:
    def __init__(self, window) -> None:
        self.window = window

    def render_page(self, preserve_selection: bool = False, keep_view_position: bool = False) -> None:
        window = self.window
        if window.doc is None:
            return

        started_at = perf_counter()
        horizontal_value = window.view.horizontalScrollBar().value()
        vertical_value = window.view.verticalScrollBar().value()
        window.text_lines_cache_page_index = None
        window.text_lines_cache = None
        selected_annotation_id = window.selected_annotation_id if preserve_selection else None

        page_started_at = perf_counter()
        page = window.current_page()
        page_ms = (perf_counter() - page_started_at) * 1000

        annotations_started_at = perf_counter()
        window.current_annotations = window.load_page_annotations(window.page_index)
        annotations_ms = (perf_counter() - annotations_started_at) * 1000

        pixmap_started_at = perf_counter()
        matrix = fitz.Matrix(window.zoom, window.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        window.page_item.setPixmap(QPixmap.fromImage(image))
        window.scene.setSceneRect(window.page_item.boundingRect())
        pixmap_ms = (perf_counter() - pixmap_started_at) * 1000

        overlay_started_at = perf_counter()
        self.render_annotation_overlay()
        overlay_ms = (perf_counter() - overlay_started_at) * 1000

        view_started_at = perf_counter()
        if keep_view_position:
            window.view.horizontalScrollBar().setValue(horizontal_value)
            window.view.verticalScrollBar().setValue(vertical_value)
        else:
            window.view.centerOn(window.page_item)
        view_ms = (perf_counter() - view_started_at) * 1000

        ui_started_at = perf_counter()
        window.update_window_title()
        window.show_page_status()
        window.refresh_annotations_table()
        window.sync_page_spin()
        window.refresh_navigation()
        window.update_actions()
        if hasattr(window.view, "reset_boundary_turn_state"):
            window.view.reset_boundary_turn_state(clear_status=False)
        window.clear_scroll_boundary_status()
        if selected_annotation_id in window.annotation_model_map:
            window.select_annotation(selected_annotation_id)
        window.save_active_session_state()
        ui_ms = (perf_counter() - ui_started_at) * 1000
        total_ms = (perf_counter() - started_at) * 1000
        window.log_debug(
            "Render page perf: "
            f"page={window.page_index + 1} total={total_ms:.1f}ms "
            f"page_ref={page_ms:.1f}ms annotations={annotations_ms:.1f}ms pixmap={pixmap_ms:.1f}ms "
            f"overlay={overlay_ms:.1f}ms view={view_ms:.1f}ms ui={ui_ms:.1f}ms"
        )

    def render_annotation_overlay(self) -> None:
        window = self.window
        self.clear_annotation_items()
        window.annotation_model_map = {model.id: model for model in window.current_annotations}
        window.selected_annotation_id = None
        renderer = AnnotationItemRenderer(
            window.scene,
            window.zoom,
            window.default_freetext_font_size,
            window.default_highlight_opacity,
            self.add_annotation_item,
            self.add_hit_item,
        )
        for model in window.current_annotations:
            if not model.is_supported:
                continue
            renderer.render(model)

    def refresh_annotation_overlay(self, preserve_selection: bool = True) -> None:
        window = self.window
        if window.doc is None:
            return

        selected_annotation_id = window.selected_annotation_id if preserve_selection else None
        window.current_annotations = window.load_page_annotations(window.page_index)
        self.render_annotation_overlay()
        window.show_page_status()
        window.refresh_annotations_table()
        window.update_actions()
        if selected_annotation_id in window.annotation_model_map:
            window.select_annotation(selected_annotation_id)
        else:
            window.refresh_properties_panel()
        window.save_active_session_state()

    def clear_annotation_items(self) -> None:
        window = self.window
        if getattr(window, "inline_freetext_editor", None) is not None:
            window.inline_freetext_editor.cancel()
        if hasattr(window.annotation_controller, "clear_text_selection"):
            window.annotation_controller.clear_text_selection()
        window.clear_selection_items()
        for item in window.annotation_items:
            window.scene.removeItem(item)
        window.annotation_items.clear()
        window.annotation_item_map.clear()
        window.annotation_model_map.clear()
        window.selected_annotation_id = None

    def add_annotation_item(self, item: QGraphicsItem, model: AnnotationModel) -> None:
        window = self.window
        item.setZValue(10)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if self.is_draggable_model(model):
            item.setCursor(Qt.CursorShape.SizeAllCursor)
        item.setData(0, model.id)
        item.setData(1, item.pos())
        window.annotation_items.append(item)
        window.annotation_item_map.setdefault(model.id, []).append(item)

    def add_hit_item(self, item: QGraphicsItem, model: AnnotationModel) -> None:
        window = self.window
        item.setZValue(11)
        item.setOpacity(0.01)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if self.is_draggable_model(model):
            item.setCursor(Qt.CursorShape.SizeAllCursor)
        item.setData(0, model.id)
        item.setData(1, item.pos())
        window.annotation_items.append(item)
        window.annotation_item_map.setdefault(model.id, []).append(item)

    def is_draggable_model(self, model: AnnotationModel) -> bool:
        return model.app_type in DRAGGABLE_APP_TYPES

    def scene_rect(self, rect: fitz.Rect) -> QRectF:
        zoom = self.window.zoom
        return QRectF(
            rect.x0 * zoom,
            rect.y0 * zoom,
            rect.width * zoom,
            rect.height * zoom,
        )

    def scene_point(self, point: tuple[float, float]) -> QPointF:
        zoom = self.window.zoom
        return QPointF(point[0] * zoom, point[1] * zoom)

    def clamp_scene_pos_to_page(self, scene_pos: QPointF) -> QPointF:
        rect = self.window.page_item.boundingRect()
        return QPointF(
            max(rect.left(), min(scene_pos.x(), rect.right())),
            max(rect.top(), min(scene_pos.y(), rect.bottom())),
        )

    def pdf_point_from_scene_point(self, scene_pos: QPointF) -> tuple[float, float]:
        zoom = self.window.zoom
        return scene_pos.x() / zoom, scene_pos.y() / zoom

    def pdf_rect_from_scene_points(self, start: QPointF, end: QPointF) -> fitz.Rect:
        x0, y0 = self.pdf_point_from_scene_point(start)
        x1, y1 = self.pdf_point_from_scene_point(end)
        return fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def page_center_rect(self, width: float, height: float) -> fitz.Rect:
        page = self.window.current_page()
        rect = page.rect
        x0 = rect.x0 + (rect.width - width) / 2
        y0 = rect.y0 + (rect.height - height) / 2
        return fitz.Rect(x0, y0, x0 + width, y0 + height)

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
        zoom = self.window.zoom
        return (
            QPointF(rect.x0 * zoom, rect.y0 * zoom),
            QPointF(rect.x1 * zoom, rect.y1 * zoom),
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

    def highlight_rects_from_text_flow(
        self, page: fitz.Page, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> list[fitz.Rect]:
        lines = self.current_page_text_lines(page)
        if not lines:
            return []

        start_position = self.text_position_from_point(lines, start_point)
        end_position = self.text_position_from_point(lines, end_point)
        if start_position is None or end_position is None:
            return []

        start_line, start_offset = start_position
        end_line, end_offset = end_position
        if (end_line, end_offset) < (start_line, start_offset):
            start_line, start_offset, end_line, end_offset = end_line, end_offset, start_line, start_offset

        rects: list[fitz.Rect] = []
        for line_index in range(start_line, end_line + 1):
            chars = lines[line_index]
            if not chars:
                continue

            begin = start_offset if line_index == start_line else 0
            finish = end_offset if line_index == end_line else len(chars)
            begin = max(0, min(begin, len(chars)))
            finish = max(0, min(finish, len(chars)))
            if finish <= begin:
                continue

            selected_boxes = [char["bbox"] for char in chars[begin:finish]]
            selected_chars = chars[begin:finish]
            rects.append(self.highlight_rect_from_selected_chars(selected_chars, selected_boxes))
        return rects

    def current_page_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        window = self.window
        if window.text_lines_cache_page_index != window.page_index or window.text_lines_cache is None:
            window.text_lines_cache = self.extract_text_lines(page)
            window.text_lines_cache_page_index = window.page_index
        return window.text_lines_cache

    def highlight_rect_from_selected_chars(self, selected_chars: list[dict], selected_boxes: list[fitz.Rect]) -> fitz.Rect:
        line_rect = fitz.Rect(selected_boxes[0])
        for box in selected_boxes[1:]:
            line_rect |= box

        expanded_rect = self.expand_highlight_rect(line_rect)
        metric_rect = self.highlight_metric_rect(selected_chars, line_rect)
        if metric_rect is not None:
            return self.visual_highlight_rect(line_rect, metric_rect, selected_chars)
        return expanded_rect

    def visual_highlight_rect(
        self, line_rect: fitz.Rect, metric_rect: fitz.Rect, selected_chars: list[dict]
    ) -> fitz.Rect:
        bbox_height = max(0.1, line_rect.height)
        metric_height = max(0.1, metric_rect.height)
        cjk_ratio = self.selected_text_cjk_ratio(selected_chars)
        if cjk_ratio >= 0.35:
            min_ratio = 0.72
            max_ratio = 0.92
            center_offset_ratio = 0.0
            top_expand_ratio = 0.0
        else:
            min_ratio = 0.45
            max_ratio = 0.62
            center_offset_ratio = 0.08
            top_expand_ratio = 0.04

        target_height = self.clamp_highlight_height(metric_height, bbox_height * min_ratio, bbox_height * max_ratio)
        center_y = (line_rect.y0 + line_rect.y1) / 2 + bbox_height * center_offset_ratio
        return fitz.Rect(
            line_rect.x0,
            center_y - target_height / 2 - bbox_height * top_expand_ratio,
            line_rect.x1,
            center_y + target_height / 2,
        )

    def selected_text_cjk_ratio(self, selected_chars: list[dict]) -> float:
        chars = [str(char.get("text", "")) for char in selected_chars if str(char.get("text", "")).strip()]
        if not chars:
            return 0.0
        cjk_count = sum(1 for text in chars for character in text if self.is_cjk_char(character))
        total_count = sum(len(text) for text in chars)
        if total_count <= 0:
            return 0.0
        return cjk_count / total_count

    def is_cjk_char(self, character: str) -> bool:
        if not character:
            return False
        codepoint = ord(character)
        return (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x20000 <= codepoint <= 0x2A6DF
            or 0x2A700 <= codepoint <= 0x2B73F
            or 0x2B740 <= codepoint <= 0x2B81F
            or 0x2B820 <= codepoint <= 0x2CEAF
        )

    def clamp_highlight_height(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(value, maximum))

    def highlight_metric_rect(self, selected_chars: list[dict], line_rect: fitz.Rect) -> fitz.Rect | None:
        metric_chars = [
            char
            for char in selected_chars
            if char.get("origin") is not None
            and char.get("size") is not None
            and char.get("ascender") is not None
            and char.get("descender") is not None
        ]
        if not metric_chars:
            return None

        top_values: list[float] = []
        bottom_values: list[float] = []
        for char in metric_chars:
            _, baseline_y = char["origin"]
            size = float(char["size"])
            ascender = float(char["ascender"])
            descender = abs(float(char["descender"]))
            top_values.append(baseline_y - size * ascender * 0.82)
            bottom_values.append(baseline_y + size * descender * 0.9)

        top = sum(top_values) / len(top_values)
        bottom = sum(bottom_values) / len(bottom_values)
        if bottom <= top:
            return None
        return fitz.Rect(line_rect.x0, top, line_rect.x1, bottom)

    def expand_highlight_rect(self, rect: fitz.Rect) -> fitz.Rect:
        return fitz.Rect(rect)

    def extract_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        raw = page.get_text("rawdict")
        lines: list[list[dict]] = []
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                chars: list[dict] = []
                for span in line.get("spans", []):
                    span_size = span.get("size")
                    span_origin = span.get("origin")
                    span_ascender = span.get("ascender")
                    span_descender = span.get("descender")
                    for char in span.get("chars", []):
                        bbox = fitz.Rect(char.get("bbox"))
                        chars.append(
                            {
                                "text": char.get("c", ""),
                                "bbox": bbox,
                                "origin": char.get("origin", span_origin),
                                "size": span_size,
                                "ascender": span_ascender,
                                "descender": span_descender,
                            }
                        )
                if chars:
                    chars.sort(key=lambda item: item["bbox"].x0)
                    lines.append(chars)
        return lines

    def text_position_from_point(
        self, lines: list[list[dict]], point: tuple[float, float]
    ) -> tuple[int, int] | None:
        if not lines:
            return None

        px, py = point
        best_line_index = 0
        best_distance = float("inf")
        for index, chars in enumerate(lines):
            line_rect = fitz.Rect(chars[0]["bbox"])
            for char in chars[1:]:
                line_rect |= char["bbox"]
            if line_rect.y0 <= py <= line_rect.y1:
                distance = 0.0
            elif py < line_rect.y0:
                distance = line_rect.y0 - py
            else:
                distance = py - line_rect.y1
            if distance < best_distance:
                best_line_index = index
                best_distance = distance

        chars = lines[best_line_index]
        if px <= chars[0]["bbox"].x0:
            return best_line_index, 0
        if px >= chars[-1]["bbox"].x1:
            return best_line_index, len(chars)

        best_offset = 0
        best_x_distance = float("inf")
        for index, char in enumerate(chars):
            bbox = char["bbox"]
            center_x = (bbox.x0 + bbox.x1) / 2
            offset = index if px < center_x else index + 1
            if bbox.x0 <= px <= bbox.x1:
                return best_line_index, offset
            distance = min(abs(px - bbox.x0), abs(px - bbox.x1), abs(px - center_x))
            if distance < best_x_distance:
                best_offset = offset
                best_x_distance = distance
        return best_line_index, best_offset
