from __future__ import annotations

import re
from math import atan2, cos, pi, sin
from time import perf_counter

import pymupdf as fitz
from shiboken6 import isValid
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem

from app.canvas.annotation_items import AnnotationItemRenderer
from app.canvas.annotation_locator import AnnotationLocator
from app.models.annotation_model import DRAGGABLE_APP_TYPES, AnnotationModel
from app.models.continuous_view import PageLayout, PageViewItem


class CanvasController:
    def __init__(self, window) -> None:
        self.window = window
        self.annotation_locator = AnnotationLocator(window)

    def safe_remove_scene_item(self, item: QGraphicsItem | None) -> None:
        window = self.window
        if item is None or getattr(window, "scene", None) is None:
            return
        try:
            if not isValid(window.scene) or not isValid(item):
                return
            if item.scene() is window.scene:
                window.scene.removeItem(item)
        except RuntimeError:
            return

    def render_page(self, preserve_selection: bool = False, keep_view_position: bool = False) -> None:
        bulk = getattr(self.window, "bulk_annotation_controller", None)
        if bulk is not None and bulk.rendering_suspended:
            return
        self.annotation_locator.stop()
        window = self.window
        workspace = window.reading_workspace
        anchor = self.capture_view_mode_anchor() if keep_view_position else None
        selected = window.selected_annotation_id if preserve_selection else None
        was_updating = window.updating_scene_selection
        with workspace.sync.hold():
            window.updating_scene_selection = True
            try:
                self._render_page(preserve_selection, keep_view_position)
                if anchor is not None:
                    self.restore_view_mode_anchor(anchor)
                if selected in window.annotation_model_map and window.selected_annotation_id != selected:
                    window.select_annotation(selected)
            finally:
                window.updating_scene_selection = was_updating

    def _render_page(self, preserve_selection: bool = False, keep_view_position: bool = False) -> None:
        window = self.window
        if window.doc is None:
            return
        if window.view_mode == "continuous":
            self.render_continuous_view(keep_view_position=keep_view_position)
            return

        started_at = perf_counter()
        self.clear_continuous_page_items()
        window.page_item.setVisible(True)
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

    def render_continuous_view(self, keep_view_position: bool = False) -> None:
        window = self.window
        if window.doc is None:
            return

        started_at = perf_counter()
        horizontal_value = window.view.horizontalScrollBar().value()
        vertical_value = window.view.verticalScrollBar().value()
        window.page_item.setVisible(False)
        self.clear_continuous_page_items()
        self.clear_annotation_items()
        window.current_annotations = self.continuous_load_page_annotations(window.page_index)

        layout_started_at = perf_counter()
        self.build_continuous_page_layouts()
        layout_ms = (perf_counter() - layout_started_at) * 1000

        scene_rect = self.continuous_scene_rect()
        window.scene.setSceneRect(scene_rect)

        if keep_view_position:
            window.view.horizontalScrollBar().setValue(horizontal_value)
            window.view.verticalScrollBar().setValue(vertical_value)
        else:
            layout = self.continuous_layout_for_page(window.page_index)
            if layout is not None:
                window.view.horizontalScrollBar().setValue(window.view.horizontalScrollBar().minimum())
                window.view.verticalScrollBar().setValue(int(layout.y_top))

        render_started_at = perf_counter()
        self.render_visible_continuous_pages()
        render_ms = (perf_counter() - render_started_at) * 1000

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
        window.save_active_session_state()
        ui_ms = (perf_counter() - ui_started_at) * 1000
        total_ms = (perf_counter() - started_at) * 1000
        window.log_debug(
            "Render continuous perf: "
            f"page={window.page_index + 1} total={total_ms:.1f}ms "
            f"layout={layout_ms:.1f}ms render={render_ms:.1f}ms ui={ui_ms:.1f}ms "
            f"items={len(window.continuous_page_items)}"
        )

    def build_continuous_page_layouts(self) -> None:
        window = self.window
        if self.annotation_locator.items and self.annotation_locator.context != self.annotation_locator._context():
            self.annotation_locator.stop()
        if window.doc is None:
            window.continuous_page_layouts = []
            self.clear_continuous_annotation_cache()
            self.clear_continuous_pixmap_cache()
            return
        if window.continuous_layout_doc_id is not None and window.continuous_layout_doc_id != id(window.doc):
            self.clear_continuous_annotation_cache()
            self.clear_continuous_pixmap_cache()
        if window.continuous_layout_zoom is not None and window.continuous_layout_zoom != window.zoom:
            self.clear_continuous_pixmap_cache()
        if (
            window.continuous_page_layouts
            and window.continuous_layout_doc_id == id(window.doc)
            and window.continuous_layout_zoom == window.zoom
        ):
            return

        page_sizes: list[tuple[float, float]] = []
        max_width = 0.0
        for page_index in range(len(window.doc)):
            rect = window.doc[page_index].rect
            width = rect.width * window.zoom
            height = rect.height * window.zoom
            page_sizes.append((width, height))
            max_width = max(max_width, width)

        layouts: list[PageLayout] = []
        y = window.continuous_page_gap
        for page_index, (width, height) in enumerate(page_sizes):
            x = window.continuous_page_gap + (max_width - width) / 2
            scene_rect = QRectF(x, y, width, height)
            layouts.append(
                PageLayout(
                    page_index=page_index,
                    page_width=width,
                    page_height=height,
                    scene_rect=scene_rect,
                    y_top=y,
                    y_bottom=y + height,
                )
            )
            y += height + window.continuous_page_gap

        window.continuous_page_layouts = layouts
        window.continuous_layout_doc_id = id(window.doc)
        window.continuous_layout_zoom = window.zoom
        self.clear_continuous_page_items()
        window.log_debug(
            f"Continuous layout built: pages={len(layouts)} zoom={window.zoom:.2f} "
            f"scene_width={max_width + window.continuous_page_gap * 2:.1f} scene_height={y:.1f}"
        )

    def continuous_load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        window = self.window
        cache = getattr(window, "continuous_annotation_cache", None)
        if cache is None:
            return window.load_page_annotations(page_index)
        if page_index not in cache:
            cache[page_index] = window.load_page_annotations(page_index)
        return cache[page_index]

    def invalidate_continuous_annotation_cache(self, page_index: int | None = None) -> None:
        cache = getattr(self.window, "continuous_annotation_cache", None)
        if cache is None:
            return
        if page_index is None:
            cache.clear()
            return
        cache.pop(page_index, None)

    def clear_continuous_annotation_cache(self) -> None:
        self.invalidate_continuous_annotation_cache(None)

    def continuous_pixmap_for_page(self, page_index: int) -> tuple[QPixmap, bool]:
        window = self.window
        cache = getattr(window, "continuous_pixmap_cache", None)
        if cache is not None and page_index in cache:
            pixmap = cache.pop(page_index)
            cache[page_index] = pixmap
            return pixmap, True

        page = window.doc[page_index]
        matrix = fitz.Matrix(window.zoom, window.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        if cache is not None:
            cache[page_index] = pixmap
            limit = max(1, int(getattr(window, "continuous_pixmap_cache_limit", 12)))
            while len(cache) > limit:
                cache.pop(next(iter(cache)))
        return pixmap, False

    def clear_continuous_pixmap_cache(self) -> None:
        cache = getattr(self.window, "continuous_pixmap_cache", None)
        if cache is not None:
            cache.clear()

    def continuous_scene_rect(self) -> QRectF:
        window = self.window
        if not window.continuous_page_layouts:
            return QRectF(0, 0, 0, 0)
        max_right = max(layout.scene_rect.right() for layout in window.continuous_page_layouts)
        max_bottom = max(layout.scene_rect.bottom() for layout in window.continuous_page_layouts)
        return QRectF(0, 0, max_right + window.continuous_page_gap, max_bottom + window.continuous_page_gap)

    def continuous_layout_for_page(self, page_index: int) -> PageLayout | None:
        window = self.window
        if page_index < 0 or page_index >= len(window.continuous_page_layouts):
            return None
        return window.continuous_page_layouts[page_index]

    def continuous_scene_rect_for_pdf_rect(self, page_index: int, rect: fitz.Rect) -> QRectF:
        layout = self.continuous_layout_for_page(page_index)
        if layout is None:
            return QRectF()
        return QRectF(
            layout.scene_rect.x() + rect.x0 * self.window.zoom,
            layout.scene_rect.y() + rect.y0 * self.window.zoom,
            rect.width * self.window.zoom,
            rect.height * self.window.zoom,
        )

    def continuous_scene_point_for_pdf_point(self, page_index: int, point: tuple[float, float]) -> QPointF:
        layout = self.continuous_layout_for_page(page_index)
        if layout is None:
            return QPointF()
        return QPointF(
            layout.scene_rect.x() + point[0] * self.window.zoom,
            layout.scene_rect.y() + point[1] * self.window.zoom,
        )

    def capture_view_mode_anchor(self) -> dict | None:
        window = self.window
        if window.doc is None:
            return None
        viewport_rect = window.view.viewport().rect()
        if viewport_rect.isEmpty():
            return None
        viewport_center = viewport_rect.center()
        scene_center = window.view.mapToScene(viewport_center)

        if window.view_mode == "continuous":
            self.build_continuous_page_layouts()
            visible_pages = self.visible_continuous_page_indexes()
            page_index = self.continuous_page_index_at_scene_pos(scene_center)
            if page_index is None:
                page_index = self.current_page_index_from_continuous_view()
            if page_index is None:
                page_index = window.page_index
            layout = self.continuous_layout_for_page(page_index)
            if layout is None:
                return None
            pdf_x = (scene_center.x() - layout.scene_rect.x()) / window.zoom
            pdf_y = (scene_center.y() - layout.scene_rect.y()) / window.zoom
        else:
            page_index = window.page_index
            pdf_x, pdf_y = self.pdf_point_from_scene_point(scene_center)
            visible_pages = {page_index}

        page_rect = window.doc[page_index].rect
        return {
            "mode": window.view_mode,
            "page_index": page_index,
            "pdf_x": max(page_rect.x0, min(pdf_x, page_rect.x1)),
            "pdf_y": max(page_rect.y0, min(pdf_y, page_rect.y1)),
            "visible_page_count": len(visible_pages),
            "zoom": window.zoom,
        }

    def continuous_page_index_at_scene_pos(self, scene_pos: QPointF) -> int | None:
        for layout in self.window.continuous_page_layouts:
            if layout.scene_rect.contains(scene_pos):
                return layout.page_index
        return None

    def fit_zoom_for_page(self, page_index: int) -> float:
        window = self.window
        if window.doc is None:
            return window.zoom
        page_index = max(0, min(page_index, len(window.doc) - 1))
        page_rect = window.doc[page_index].rect
        viewport_size = window.view.viewport().size()
        available_width = max(1.0, viewport_size.width() - 24.0)
        available_height = max(1.0, viewport_size.height() - 24.0)
        fit_zoom = min(available_width / page_rect.width, available_height / page_rect.height)
        return max(0.5, min(4.0, fit_zoom))

    def restore_view_mode_anchor(self, anchor: dict | None) -> None:
        window = self.window
        if window.doc is None or not anchor:
            return
        page_index = max(0, min(int(anchor.get("page_index", window.page_index)), len(window.doc) - 1))
        point = (float(anchor.get("pdf_x", 0.0)), float(anchor.get("pdf_y", 0.0)))
        window.page_index = page_index

        if window.view_mode == "continuous":
            self.build_continuous_page_layouts()
            window.scene.setSceneRect(self.continuous_scene_rect())
            scene_point = self.continuous_scene_point_for_pdf_point(page_index, point)
            window.continuous_suppress_scroll_sync = True
            try:
                window.view.centerOn(scene_point)
            finally:
                window.continuous_suppress_scroll_sync = False
            window.current_annotations = self.continuous_load_page_annotations(window.page_index)
            self.render_visible_continuous_pages()
            self.refresh_continuous_current_page_ui()
        else:
            scene_point = self.scene_point(point)
            window.view.centerOn(scene_point)

        window.log_debug(
            "View mode anchor restored: "
            f"mode={window.view_mode} page={page_index + 1} "
            f"pdf=({point[0]:.1f},{point[1]:.1f}) zoom={window.zoom:.2f}"
        )

    def render_visible_continuous_pages(self) -> None:
        bulk = getattr(self.window, "bulk_annotation_controller", None)
        if bulk is not None and bulk.rendering_suspended:
            return
        window = self.window
        if window.doc is None or not window.continuous_page_layouts:
            return
        if getattr(window, "scene", None) is None or not isValid(window.scene):
            return

        visible_indexes = self.visible_continuous_page_indexes()
        if not visible_indexes:
            visible_indexes = {window.page_index}

        first_index = max(0, min(visible_indexes) - window.continuous_cache_radius)
        last_index = min(len(window.continuous_page_layouts) - 1, max(visible_indexes) + window.continuous_cache_radius)
        target_indexes = set(range(first_index, last_index + 1))
        before_items = set(window.continuous_page_items)

        for page_index in sorted(target_indexes):
            self.ensure_continuous_page_rendered(page_index)

        for page_index in list(window.continuous_page_items):
            if page_index not in target_indexes:
                self.unload_continuous_page(page_index)
        after_items = set(window.continuous_page_items)
        loaded_items = sorted(after_items - before_items)
        unloaded_items = sorted(before_items - after_items)
        if loaded_items or unloaded_items:
            window.log_debug(
                "Continuous cache update: "
                f"visible={[index + 1 for index in sorted(visible_indexes)]} "
                f"cached={first_index + 1}-{last_index + 1} "
                f"loaded={[index + 1 for index in loaded_items]} "
                f"unloaded={[index + 1 for index in unloaded_items]}"
            )

    def visible_continuous_page_indexes(self) -> set[int]:
        window = self.window
        viewport_rect = window.view.mapToScene(window.view.viewport().rect()).boundingRect()
        indexes: set[int] = set()
        for layout in window.continuous_page_layouts:
            if layout.scene_rect.intersects(viewport_rect):
                indexes.add(layout.page_index)
        return indexes

    def ensure_continuous_page_rendered(self, page_index: int) -> None:
        window = self.window
        if window.doc is None:
            return
        if page_index in window.continuous_page_items:
            return

        layout = self.continuous_layout_for_page(page_index)
        if layout is None:
            return

        page_started_at = perf_counter()
        pixmap_item = None
        pixmap_ms = 0.0
        try:
            pixmap_started_at = perf_counter()
            pixmap, pixmap_cache_hit = self.continuous_pixmap_for_page(page_index)
            pixmap_item = QGraphicsPixmapItem(pixmap)
            pixmap_item.setZValue(0)
            pixmap_item.setPos(layout.scene_rect.topLeft())
            pixmap_item.setData(10, page_index)
            window.scene.addItem(pixmap_item)
            pixmap_ms = (perf_counter() - pixmap_started_at) * 1000
        except Exception as exc:
            window.log_debug(f"Continuous page pixmap failed: page={page_index + 1} error={exc}")
            return

        (
            annotation_items,
            annotation_model_ids,
            annotation_count,
            supported_count,
            unsupported_count,
            annotation_read_ms,
            overlay_ms,
            annotation_error,
        ) = self.render_continuous_page_annotations(page_index, layout)
        total_ms = (perf_counter() - page_started_at) * 1000
        window.continuous_page_items[page_index] = PageViewItem(
            page_index=page_index,
            pixmap_item=pixmap_item,
            annotation_items=annotation_items,
            annotation_model_ids=annotation_model_ids,
            is_rendered=True,
            annotation_count=annotation_count,
            supported_annotation_count=supported_count,
            unsupported_annotation_count=unsupported_count,
            pixmap_ms=pixmap_ms,
            annotation_read_ms=annotation_read_ms,
            overlay_ms=overlay_ms,
            total_ms=total_ms,
            annotation_error=annotation_error,
        )
        if total_ms >= 25 or annotation_error:
            window.log_debug(
                "Continuous page rendered: "
                f"page={page_index + 1} total={total_ms:.1f}ms pixmap={pixmap_ms:.1f}ms "
                f"annotation_read={annotation_read_ms:.1f}ms overlay={overlay_ms:.1f}ms "
                f"annotations={annotation_count} supported={supported_count} unsupported={unsupported_count}"
                + (" pixmap_cache=hit" if pixmap_cache_hit else "")
                + (f" error={annotation_error}" if annotation_error else "")
            )

    def render_continuous_page_annotations(
        self, page_index: int, layout: PageLayout
    ) -> tuple[list[QGraphicsItem], list[str], int, int, int, float, float, str | None]:
        window = self.window
        annotation_read_started_at = perf_counter()
        annotation_error = None
        try:
            models = self.continuous_load_page_annotations(page_index)
        except Exception as exc:
            models = []
            annotation_error = str(exc)
            window.log_debug(f"Continuous annotation read failed: page={page_index + 1} error={exc}")
        annotation_read_ms = (perf_counter() - annotation_read_started_at) * 1000

        annotation_count = len(models)
        supported_count = sum(1 for model in models if model.is_supported)
        unsupported_count = annotation_count - supported_count
        annotation_items: list[QGraphicsItem] = []
        annotation_model_ids: list[str] = []

        def register_readonly_item(item: QGraphicsItem, model: AnnotationModel) -> None:
            item.setZValue(10)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setData(0, model.id)
            item.setData(10, page_index)
            annotation_items.append(item)

        def register_readonly_hit_item(item: QGraphicsItem, model: AnnotationModel) -> None:
            item.setZValue(11)
            item.setOpacity(0.01)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setData(0, model.id)
            item.setData(10, page_index)
            annotation_items.append(item)

        overlay_started_at = perf_counter()
        renderer = AnnotationItemRenderer(
            window.scene,
            window.zoom,
            window.default_freetext_font_size,
            window.default_highlight_opacity,
            register_readonly_item,
            register_readonly_hit_item,
            scene_offset=layout.scene_rect.topLeft(),
            render_hit_items=True,
        )
        for model in models:
            if not model.is_supported:
                continue
            try:
                window.annotation_model_map[model.id] = model
                annotation_model_ids.append(model.id)
                renderer.render(model)
            except Exception as exc:
                annotation_error = str(exc)
                window.log_debug(
                    f"Continuous annotation overlay failed: page={page_index + 1} "
                    f"xref={model.xref} type={model.pdf_type} error={exc}"
                )
        overlay_ms = (perf_counter() - overlay_started_at) * 1000
        return (
            annotation_items,
            annotation_model_ids,
            annotation_count,
            supported_count,
            unsupported_count,
            annotation_read_ms,
            overlay_ms,
            annotation_error,
        )

    def unload_continuous_page(self, page_index: int) -> None:
        window = self.window
        item = window.continuous_page_items.pop(page_index, None)
        if item is None:
            return
        if window.selected_annotation_id in (item.annotation_model_ids or []):
            window.select_annotation(None)
        for annotation_item in item.annotation_items or []:
            self.safe_remove_scene_item(annotation_item)
        for model_id in item.annotation_model_ids or []:
            if model_id != window.selected_annotation_id:
                window.annotation_model_map.pop(model_id, None)
        if item.pixmap_item is not None:
            self.safe_remove_scene_item(item.pixmap_item)
        if (item.annotation_items or []) or item.annotation_count:
            window.log_debug(
                f"Continuous page unloaded: page={page_index + 1} "
                f"overlay_items={len(item.annotation_items or [])} annotations={item.annotation_count}"
            )

    def clear_continuous_page_items(self) -> None:
        self.annotation_locator.stop()
        window = self.window
        for item in list(getattr(window, "continuous_page_items", {}).values()):
            for annotation_item in item.annotation_items or []:
                self.safe_remove_scene_item(annotation_item)
            for model_id in item.annotation_model_ids or []:
                window.annotation_model_map.pop(model_id, None)
            if item.pixmap_item is not None:
                self.safe_remove_scene_item(item.pixmap_item)
        window.continuous_page_items.clear()

    def on_view_scrolled(self) -> None:
        window = self.window
        if window.doc is None or window.view_mode != "continuous":
            return
        if getattr(window, "continuous_suppress_scroll_sync", False):
            return
        if getattr(window, "scene", None) is None or not isValid(window.scene):
            return
        self.render_visible_continuous_pages()
        new_page_index = self.current_page_index_from_continuous_view()
        if new_page_index is not None and new_page_index != window.page_index:
            window.page_index = new_page_index
            window.current_annotations = self.continuous_load_page_annotations(window.page_index)
            window.update_window_title()
            window.show_page_status()
            window.sync_page_spin()
            window.refresh_annotations_table()
            window.refresh_navigation()
            window.save_active_session_state()
        self.clear_continuous_selection_if_not_visible()
        window.save_active_session_state()

    def current_page_index_from_continuous_view(self) -> int | None:
        window = self.window
        if not window.continuous_page_layouts:
            return None
        viewport_rect = window.view.mapToScene(window.view.viewport().rect()).boundingRect()
        center_y = viewport_rect.center().y()
        best_index = window.page_index
        best_distance = float("inf")
        for layout in window.continuous_page_layouts:
            if layout.y_top <= center_y <= layout.y_bottom:
                return layout.page_index
            distance = min(abs(center_y - layout.y_top), abs(center_y - layout.y_bottom))
            if distance < best_distance:
                best_index = layout.page_index
                best_distance = distance
        return best_index

    def scroll_continuous_to_page(self, page_index: int) -> None:
        window = self.window
        if window.doc is None:
            return
        page_index = max(0, min(page_index, len(window.doc) - 1))
        self.build_continuous_page_layouts()
        window.scene.setSceneRect(self.continuous_scene_rect())
        layout = self.continuous_layout_for_page(page_index)
        if layout is None:
            return
        window.page_index = page_index
        window.current_annotations = self.continuous_load_page_annotations(window.page_index)
        window.view.horizontalScrollBar().setValue(window.view.horizontalScrollBar().minimum())
        window.view.verticalScrollBar().setValue(int(layout.y_top))
        self.render_visible_continuous_pages()
        self.refresh_continuous_current_page_ui()

    def scroll_continuous_to_xref(self, page_index: int, xref: int) -> bool:
        window = self.window
        if window.doc is None:
            return False
        page_index = max(0, min(page_index, len(window.doc) - 1))
        self.build_continuous_page_layouts()
        window.scene.setSceneRect(self.continuous_scene_rect())
        models = self.continuous_load_page_annotations(page_index)
        target_model = next((model for model in models if model.xref == xref), None)
        if target_model is None:
            self.scroll_continuous_to_page(page_index)
            return False

        target_rect = self.continuous_scene_rect_for_pdf_rect(page_index, target_model.rect)
        window.page_index = page_index
        window.current_annotations = models
        window.view.horizontalScrollBar().setValue(max(0, int(target_rect.left()) - 40))
        window.view.verticalScrollBar().setValue(max(0, int(target_rect.top()) - 80))
        self.render_visible_continuous_pages()
        self.refresh_continuous_current_page_ui()
        return True

    def select_continuous_annotation_by_xref(self, page_index: int, xref: int) -> bool:
        window = self.window
        if window.doc is None or window.view_mode != "continuous":
            return False
        page_index = max(0, min(page_index, len(window.doc) - 1))
        models = self.continuous_load_page_annotations(page_index)
        target_model = next((model for model in models if model.xref == xref), None)
        if target_model is None or not target_model.is_supported:
            return False

        window.page_index = page_index
        window.current_annotations = models
        window.annotation_model_map[target_model.id] = target_model
        window.select_annotation(target_model.id, center_on=False)
        self.refresh_continuous_current_page_ui()
        return True

    def scroll_continuous_rect_into_view(self, page_index: int, rect: fitz.Rect, margin: int = 40) -> None:
        window = self.window
        if window.doc is None:
            return
        page_index = max(0, min(page_index, len(window.doc) - 1))
        self.build_continuous_page_layouts()
        window.scene.setSceneRect(self.continuous_scene_rect())
        target_rect = self.continuous_scene_rect_for_pdf_rect(page_index, rect)
        viewport_rect = window.view.mapToScene(window.view.viewport().rect()).boundingRect()
        horizontal = window.view.horizontalScrollBar()
        vertical = window.view.verticalScrollBar()

        next_x = horizontal.value()
        if target_rect.left() < viewport_rect.left() + margin:
            next_x += int(target_rect.left() - viewport_rect.left() - margin)
        elif target_rect.right() > viewport_rect.right() - margin:
            next_x += int(target_rect.right() - viewport_rect.right() + margin)

        next_y = vertical.value()
        if target_rect.top() < viewport_rect.top() + margin:
            next_y += int(target_rect.top() - viewport_rect.top() - margin)
        elif target_rect.bottom() > viewport_rect.bottom() - margin:
            next_y += int(target_rect.bottom() - viewport_rect.bottom() + margin)

        window.continuous_suppress_scroll_sync = True
        try:
            horizontal.setValue(max(horizontal.minimum(), min(horizontal.maximum(), next_x)))
            vertical.setValue(max(vertical.minimum(), min(vertical.maximum(), next_y)))
        finally:
            window.continuous_suppress_scroll_sync = False
        self.render_visible_continuous_pages()

    def continuous_annotation_scene_rect(self, model: AnnotationModel) -> QRectF:
        if model.app_type == "arrow":
            start = model.line_start or (model.rect.x0, model.rect.y0)
            end = model.line_end or (model.rect.x1, model.rect.y1)
            start_point = self.continuous_scene_point_for_pdf_point(model.page_index, start)
            end_point = self.continuous_scene_point_for_pdf_point(model.page_index, end)
            return QRectF(start_point, end_point).normalized()
        return self.continuous_scene_rect_for_pdf_rect(model.page_index, model.rect)

    def continuous_annotation_is_visible(self, model: AnnotationModel, margin: int = 8) -> bool:
        window = self.window
        if not window.continuous_page_layouts:
            return False
        viewport_rect = window.view.mapToScene(window.view.viewport().rect()).boundingRect()
        target_rect = self.continuous_annotation_scene_rect(model)
        return viewport_rect.adjusted(-margin, -margin, margin, margin).intersects(target_rect)

    def clear_continuous_selection_if_not_visible(self) -> None:
        window = self.window
        if window.selected_annotation_id is None:
            return
        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None or not self.continuous_annotation_is_visible(model):
            window.select_annotation(None)

    def refresh_continuous_current_page_ui(self) -> None:
        window = self.window
        window.update_window_title()
        window.show_page_status()
        window.sync_page_spin()
        window.refresh_annotations_table()
        window.refresh_navigation()
        window.update_actions()
        window.save_active_session_state()

    def select_continuous_annotation_at_scene_pos(self, scene_pos: QPointF) -> bool:
        window = self.window
        if window.doc is None or window.view_mode != "continuous":
            return False
        for item in window.scene.items(scene_pos):
            annotation_id = item.data(0)
            if not annotation_id or annotation_id not in window.annotation_model_map:
                continue
            model = window.annotation_model_map[annotation_id]
            if not model.is_supported:
                continue
            if model.page_index != window.page_index:
                window.page_index = model.page_index
                window.current_annotations = self.continuous_load_page_annotations(window.page_index)
            window.select_annotation(annotation_id, center_on=False)
            return True
        window.select_annotation(None)
        return True

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

    def refresh_after_batch_annotation_change(self, preserve_selection: bool = True) -> None:
        """Invalidate every edited page, including off-screen pages, without scrolling.

        Batch FreeText can edit the whole document. The ordinary single-page
        overlay renderer must not be used at a continuous page's scene origin.
        PDF page content has not changed, so cached background pixmaps are reusable.
        """
        window = self.window
        if window.doc is None:
            return
        if window.view_mode != "continuous":
            self.refresh_annotation_overlay(preserve_selection)
            return
        selected = window.selected_annotation_id if preserve_selection else None
        horizontal = window.view.horizontalScrollBar()
        vertical = window.view.verticalScrollBar()
        scroll_x, scroll_y = horizontal.value(), vertical.value()
        updating = window.updating_scene_selection
        suppress_scroll = window.continuous_suppress_scroll_sync
        window.stop_continuous_scroll_timer()
        with window.reading_workspace.sync.hold():
            window.updating_scene_selection = True
            window.continuous_suppress_scroll_sync = True
            try:
                window.selected_annotation_id = None
                window.clear_selection_items()
                self.clear_continuous_page_items()
                self.clear_continuous_annotation_cache()
                window.annotation_model_map.clear()
                # Page geometry is unchanged. Retain exact scrollbar positions
                # rather than recentering the view on a different page/annotation.
                horizontal.setValue(scroll_x)
                vertical.setValue(scroll_y)
                self.render_visible_continuous_pages()
                window.current_annotations = self.continuous_load_page_annotations(window.page_index)
                if selected in window.annotation_model_map:
                    window.annotation_controller.select_annotation(selected, center_on=False)
                else:
                    window.annotation_controller.select_annotation(None)
                horizontal.setValue(scroll_x)
                vertical.setValue(scroll_y)
                window.refresh_annotations_table()
                window.refresh_properties_panel()
                window.update_actions()
                window.save_active_session_state()
            finally:
                window.updating_scene_selection = updating
                window.continuous_suppress_scroll_sync = suppress_scroll

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
            self.safe_remove_scene_item(item)
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
