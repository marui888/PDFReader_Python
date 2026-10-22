from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsRectItem, QInputDialog, QMenu

from app.main_window import dialogs as main_window_dialogs
from app.canvas.annotation_interaction import AnnotationInteractionController
from app.canvas.annotation_selection import AnnotationSelectionRenderer
from app.anchors import is_anchor_text, references_in_text, text_affects_anchor_data
from app.models.annotation_model import ANNOTATION_COLORS, QUICK_HIGHLIGHT_COLORS, AnnotationModel
from app.services.pdf_annotation_writer import PdfAnnotationWriter
from app.services.pdf_text_extract import highlight_selected_text, selected_text_from_rects
from app.models.undo import UndoAction


class AnnotationController:
    def __init__(self, window) -> None:
        self.window = window

    def on_scene_selection_changed(self) -> None:
        window = self.window
        if window.reading_workspace.active is not window:
            return
        if window.updating_scene_selection:
            return

        selected_ids = [item.data(0) for item in window.scene.selectedItems() if item.data(0)]
        if not selected_ids:
            if window.selected_annotation_id is not None:
                self.select_annotation(None)
            return

        annotation_id = str(selected_ids[0])
        if annotation_id != window.selected_annotation_id:
            self.select_annotation(annotation_id)

    def annotation_hit_at_scene_pos(self, scene_pos) -> tuple[str, str | None] | None:
        window = self.window
        candidates: list[tuple[int, int, str, str | None]] = []
        for item in window.scene.items(scene_pos):
            item_role = item.data(2)
            annotation_id = item.data(3) if item_role in {"resize-handle", "arrow-endpoint-handle"} else item.data(0)
            if not annotation_id:
                continue
            if item_role in {"selection-rect"}:
                continue
            model = window.annotation_model_map.get(str(annotation_id))
            if model is None:
                continue
            if (
                model.app_type == "square"
                and item_role != "resize-handle"
                and not self.is_scene_pos_near_square_border(model, scene_pos)
            ):
                continue
            priority = self.annotation_hit_priority(model, str(item_role) if item_role else None)
            candidates.append((priority, len(candidates), str(annotation_id), str(item_role) if item_role else None))
        if not candidates:
            return None
        candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
        _priority, _order, annotation_id, item_role = candidates[0]
        return annotation_id, item_role

    def is_scene_pos_near_square_border(self, model: AnnotationModel, scene_pos) -> bool:
        if getattr(self.window, "view_mode", "single") == "continuous":
            rect = self.window.canvas_controller.continuous_scene_rect_for_pdf_rect(model.page_index, model.rect)
        else:
            rect = self.window.scene_rect(model.rect)
        if not rect.contains(scene_pos):
            return False
        tolerance = self.square_border_hit_tolerance(model)
        distances = (
            abs(scene_pos.x() - rect.left()),
            abs(scene_pos.x() - rect.right()),
            abs(scene_pos.y() - rect.top()),
            abs(scene_pos.y() - rect.bottom()),
        )
        return min(distances) <= tolerance

    def square_border_hit_tolerance(self, model: AnnotationModel) -> float:
        stroke_width = float(model.border_width or 1.0) * float(self.window.zoom)
        return max(5.0, min(10.0, stroke_width / 2.0 + 4.0))

    def annotation_hit_priority(self, model: AnnotationModel, item_role: str | None) -> int:
        if item_role in {"resize-handle", "arrow-endpoint-handle"}:
            return 1000
        if model.id == self.window.selected_annotation_id:
            return 900
        type_priority = {
            "freetext": 700,
            "arrow": 600,
            "square": 500,
            "highlight": 100,
        }
        return type_priority.get(model.app_type, 0)

    def record_delete_undo(self, model: AnnotationModel) -> None:
        window = self.window
        window.undo_action = UndoAction(
            label=f"Delete {model.pdf_type}",
            operation="delete",
            page_index=model.page_index,
            xref=model.xref,
            app_type=model.app_type,
            rect=fitz.Rect(model.rect),
            text=model.text,
            color=model.color,
            border_width=model.border_width,
            font_size=model.font_size,
            opacity=model.opacity,
            quad_points=list(model.quad_points),
            line_start=model.line_start,
            line_end=model.line_end,
            line_ending=model.line_ending,
        )
        window.update_actions()

    def record_property_undo(self, label: str, model: AnnotationModel) -> None:
        window = self.window
        window.undo_action = UndoAction(
            label=label,
            operation="property",
            page_index=model.page_index,
            xref=model.xref,
            app_type=model.app_type,
            rect=fitz.Rect(model.rect),
            text=model.text,
            color=model.color,
            border_width=model.border_width,
            font_size=model.font_size,
            opacity=model.opacity,
            quad_points=list(model.quad_points),
            line_start=model.line_start,
            line_end=model.line_end,
            line_ending=model.line_ending,
        )
        window.update_actions()

    def undo_last_action(self) -> None:
        window = self.window
        if window.doc is None or window.undo_action is None:
            return

        action = window.undo_action
        is_continuous = getattr(window, "view_mode", "single") == "continuous"
        try:
            window.page_index = max(0, min(action.page_index, len(window.doc) - 1))
            if action.operation == "delete":
                restored_xref = self.restore_deleted_annotation(action)
            elif action.operation == "property":
                restored_xref = self.restore_annotation_property(action)
            else:
                restored_xref = window.restore_undo_action(action)
            window.undo_action = None
            window.mark_dirty()
            if is_continuous:
                xref_to_select = restored_xref if restored_xref is not None else action.xref
                self.refresh_continuous_page_after_geometry_edit(action.page_index, xref_to_select)
            else:
                window.refresh_annotation_overlay(preserve_selection=True)
                if restored_xref is not None:
                    self.select_current_overlay_annotation_by_xref(restored_xref)
            window.statusBar().showMessage(f"Undid {action.label} xref={action.xref}. Use Save to persist.")
            window.update_actions()
        except Exception as exc:
            window.show_error("Undo failed", exc)

    def restore_annotation_property(self, action: UndoAction) -> int | None:
        window = self.window
        if not window.current_annotations or window.page_index != action.page_index:
            window.current_annotations = window.load_page_annotations(action.page_index)
        model = None
        for candidate in window.current_annotations:
            if candidate.xref == action.xref:
                model = candidate
                break
        if model is None:
            raise RuntimeError("The annotation to undo was not found.")

        if action.app_type == "freetext":
            self.update_freetext_annotation_on_page(
                action.page_index,
                model,
                action.text,
                int(round(action.font_size or window.default_freetext_font_size)),
                action.color or (1, 0, 0),
            )
        elif action.app_type == "highlight":
            self.update_highlight_annotation_on_page(
                action.page_index,
                model,
                action.color or window.default_highlight_color,
                action.opacity if action.opacity is not None else window.default_highlight_opacity,
            )
        elif action.app_type in {"square", "arrow"}:
            self.update_stroked_annotation_on_page(
                action.page_index,
                model,
                action.color or (1, 0, 0),
                int(round(action.border_width or 1)),
            )
        else:
            raise RuntimeError(f"Property undo is not supported for annotation type: {action.app_type}")
        return action.xref

    def restore_deleted_annotation(self, action: UndoAction) -> int:
        window = self.window
        page = window.doc[action.page_index]
        writer = PdfAnnotationWriter(window.doc)
        if action.app_type == "freetext":
            annot = writer.add_freetext_annotation(
                page,
                action.rect,
                action.text,
                action.font_size or window.default_freetext_font_size,
                action.color or (1, 0, 0),
            )
            return annot.xref
        if action.app_type == "square":
            annot = writer.add_square_annotation(
                page,
                action.rect,
                action.color or (1, 0, 0),
                action.border_width or 2,
                action.text or "Rectangle annotation",
            )
            return annot.xref
        if action.app_type == "arrow":
            if action.line_start is None or action.line_end is None:
                raise RuntimeError("The deleted arrow has no saved endpoints.")
            annot = writer.add_arrow_annotation(
                page,
                action.line_start,
                action.line_end,
                action.color or (1, 0, 0),
                action.border_width or 2,
                action.text or "Arrow annotation",
            )
            return annot.xref
        if action.app_type == "highlight":
            rects = self.highlight_rects_from_quad_points(action.quad_points or [])
            if not rects and action.rect is not None:
                rects = [action.rect]
            annot = writer.add_highlight_annotation(
                page,
                rects,
                action.color or window.default_highlight_color,
                action.opacity if action.opacity is not None else window.default_highlight_opacity,
                action.text or "Highlight annotation",
            )
            return annot.xref
        raise RuntimeError(f"Undo delete is not supported for annotation type: {action.app_type}")

    def highlight_rects_from_quad_points(self, quad_points: list[tuple[float, float]]) -> list[fitz.Rect]:
        rects: list[fitz.Rect] = []
        for index in range(0, len(quad_points) - 3, 4):
            quad = quad_points[index : index + 4]
            xs = [point[0] for point in quad]
            ys = [point[1] for point in quad]
            rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
        return rects

    def record_add_undo(self, label: str, page_index: int, xref: int) -> None:
        window = self.window
        window.undo_action = UndoAction(
            label=label,
            operation="add",
            page_index=page_index,
            xref=xref,
            app_type="",
        )
        window.update_actions()

    def record_geometry_undo(self, label: str, model: AnnotationModel) -> None:
        window = self.window
        window.undo_action = UndoAction(
            label=label,
            page_index=model.page_index,
            xref=model.xref,
            app_type=model.app_type,
            operation="geometry",
            rect=fitz.Rect(model.rect) if model.rect is not None else None,
            line_start=model.line_start,
            line_end=model.line_end,
        )
        window.update_actions()

    def on_scene_mouse_press(self, scene_pos) -> bool:
        window = self.window
        if window.is_inline_freetext_editor_hit(scene_pos):
            return False
        if window.confirm_inline_freetext_editor_from_canvas_click(scene_pos):
            return True
        self.clear_scene_drag_state()
        if self.prepare_annotation_interaction_at_scene_pos(scene_pos):
            self.clear_text_selection()
            return False
        if window.active_tool is None and self.begin_default_text_selection(scene_pos):
            return True
        self.clear_text_selection()
        return False

    def prepare_annotation_interaction_at_scene_pos(self, scene_pos) -> bool:
        window = self.window
        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None:
            return False

        annotation_id, item_role = hit
        if annotation_id is None:
            return False

        model = window.annotation_model_map.get(annotation_id)
        if model is None or not model.is_supported:
            return False

        if annotation_id != window.selected_annotation_id:
            self.select_annotation(annotation_id)
        self.prepare_annotation_drag(annotation_id)
        window.active_scene_drag_annotation_id = annotation_id
        window.active_scene_drag_start_pos = scene_pos
        if item_role == "resize-handle":
            window.active_scene_drag_kind = "resize"
        elif item_role == "arrow-endpoint-handle":
            window.active_scene_drag_kind = "arrow-endpoint"
        elif window.is_draggable_model(model):
            window.active_scene_drag_kind = "move"
        return True

    def clear_scene_drag_state(self) -> None:
        window = self.window
        window.active_scene_drag_kind = None
        window.active_scene_drag_annotation_id = None
        window.active_scene_drag_start_pos = None
        window.active_scene_drag_handle = None

    def begin_default_text_selection(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None or not self.is_text_at_scene_pos(scene_pos):
            return False
        self.clear_text_selection()
        self.select_annotation(None)
        window.text_selection_active = True
        window.text_selection_start_scene_pos = scene_pos
        window.text_selection_page_index = window.page_index
        window.statusBar().showMessage("Selecting text")
        return True

    def begin_continuous_default_text_selection(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None:
            return False
        page_index = self.continuous_page_index_at_scene_pos(scene_pos)
        if page_index is None or not self.is_continuous_text_at_scene_pos(page_index, scene_pos):
            return False
        self.clear_text_selection()
        self.select_annotation(None)
        window.text_selection_active = True
        window.text_selection_start_scene_pos = scene_pos
        window.text_selection_page_index = page_index
        window.statusBar().showMessage("Selecting text")
        return True

    def clear_text_selection(self) -> None:
        window = self.window
        for item in window.text_selection_items:
            try:
                window.scene.removeItem(item)
            except RuntimeError:
                pass
        window.text_selection_items.clear()
        window.text_selection_rects.clear()
        window.text_selection_active = False
        window.text_selection_start_scene_pos = None
        window.text_selection_page_index = None

    def is_text_at_scene_pos(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None:
            return False
        px, py = window.pdf_point_from_scene_point(scene_pos)
        try:
            lines = window.canvas_controller.current_page_text_lines(window.current_page())
        except Exception:
            return False
        margin = 1.5
        for chars in lines:
            for char in chars:
                bbox = fitz.Rect(char["bbox"])
                if (
                    bbox.contains(fitz.Point(px, py))
                    or bbox.contains(fitz.Point(px + margin, py))
                    or bbox.contains(fitz.Point(px - margin, py))
                ):
                    return True
        return False

    def is_continuous_text_at_scene_pos(self, page_index: int, scene_pos) -> bool:
        window = self.window
        if window.doc is None or page_index < 0 or page_index >= len(window.doc):
            return False
        px, py = self.continuous_pdf_point_from_scene_point(page_index, scene_pos)
        try:
            lines = window.canvas_controller.current_page_text_lines(window.doc[page_index])
        except Exception:
            return False
        margin = 1.5
        for chars in lines:
            for char in chars:
                bbox = fitz.Rect(char["bbox"])
                if (
                    bbox.contains(fitz.Point(px, py))
                    or bbox.contains(fitz.Point(px + margin, py))
                    or bbox.contains(fitz.Point(px - margin, py))
                ):
                    return True
        return False

    def prepare_annotation_drag(self, annotation_id: str) -> None:
        window = self.window
        for item in window.annotation_item_map.get(annotation_id, []):
            item.setData(1, item.pos())
        for item in window.selection_items:
            if item.data(3) == annotation_id and item.data(2) in {"resize-handle", "arrow-endpoint-handle"}:
                item.setData(5, item.pos())

    def restore_annotation_drag_preview(self, annotation_id: str) -> None:
        window = self.window
        for item in window.annotation_item_map.get(annotation_id, []):
            start_pos = item.data(1)
            if isinstance(start_pos, QPointF) and item.pos() != start_pos:
                item.setPos(start_pos)

        for item in window.selection_items:
            if item.data(3) != annotation_id:
                continue
            if item.data(2) == "selection-rect" and item.pos() != QPointF(0, 0):
                item.setPos(QPointF(0, 0))

    def is_intentional_annotation_move(self, delta: QPointF) -> bool:
        return abs(delta.x()) >= 3.0 or abs(delta.y()) >= 3.0

    def on_scene_mouse_release(self, scene_pos=None) -> None:
        window = self.window
        if window.text_selection_active:
            self.update_default_text_selection(scene_pos)
            window.text_selection_active = False
            window.text_selection_start_scene_pos = None
            if window.text_selection_items:
                window.statusBar().showMessage("Text selected")
            return

        if window.doc is None or window.selected_annotation_id is None:
            return

        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None:
            return

        interaction = None
        if (
            window.active_scene_drag_kind == "move"
            and scene_pos is not None
            and window.active_scene_drag_start_pos is not None
        ):
            delta = scene_pos - window.active_scene_drag_start_pos
            controller = AnnotationInteractionController(window.zoom)
            if self.is_intentional_annotation_move(delta) and not controller.is_small_delta(delta):
                interaction = controller.interaction_from_delta("move", delta)
            else:
                self.restore_annotation_drag_preview(model.id)
        else:
            interaction = AnnotationInteractionController(window.zoom).interaction_for_mouse_release(
                model,
                window.selection_items,
                window.annotation_item_map,
                preferred_kind=window.active_scene_drag_kind,
            )
        window.active_scene_drag_kind = None
        window.active_scene_drag_annotation_id = None
        window.active_scene_drag_start_pos = None
        if interaction is None:
            return

        if interaction.kind == "resize":
            try:
                self.record_geometry_undo(f"Resize {model.pdf_type}", model)
                window.resize_rect_annotation(model, interaction.handle, interaction.dx_pdf, interaction.dy_pdf)
                window.mark_dirty()
                window.refresh_annotation_overlay(preserve_selection=True)
                window.statusBar().showMessage(f"Resized {model.pdf_type} xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                window.refresh_annotation_overlay(preserve_selection=True)
                window.show_error("Resize annotation failed", exc)
            return

        if interaction.kind == "arrow-endpoint":
            try:
                self.record_geometry_undo("Move Arrow endpoint", model)
                window.move_arrow_endpoint(model, interaction.handle, interaction.dx_pdf, interaction.dy_pdf)
                window.mark_dirty()
                window.refresh_annotation_overlay(preserve_selection=True)
                window.statusBar().showMessage(f"Moved Arrow endpoint xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                window.refresh_annotation_overlay(preserve_selection=True)
                window.show_error("Move arrow endpoint failed", exc)
            return

        if interaction.kind == "move":
            try:
                self.record_geometry_undo(f"Move {model.pdf_type}", model)
                window.move_pdf_annotation(model, interaction.dx_pdf, interaction.dy_pdf)
                window.mark_dirty()
                window.refresh_annotation_overlay(preserve_selection=True)
                window.statusBar().showMessage(f"Moved {model.pdf_type} xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                window.refresh_annotation_overlay(preserve_selection=True)
                window.show_error("Move annotation failed", exc)

    def on_continuous_scene_mouse_press(self, scene_pos, button=None) -> bool:
        window = self.window
        if window.doc is None or getattr(window, "view_mode", "single") != "continuous":
            return False
        if button is not None and button != Qt.MouseButton.LeftButton:
            return False
        if window.is_inline_freetext_editor_hit(scene_pos):
            return False
        if window.confirm_inline_freetext_editor_from_canvas_click(scene_pos):
            return True
        self.clear_scene_drag_state()
        if self.begin_continuous_existing_annotation_interaction(scene_pos):
            return True
        if window.active_tool is not None:
            return self.on_continuous_tool_mouse_press(scene_pos)
        if self.begin_continuous_default_text_selection(scene_pos):
            return True

        self.select_annotation(None)
        return True

    def begin_continuous_existing_annotation_interaction(self, scene_pos) -> bool:
        window = self.window
        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None:
            return False

        annotation_id, item_role = hit
        model = window.annotation_model_map.get(annotation_id)
        if model is None or not model.is_supported:
            return True

        if annotation_id != window.selected_annotation_id:
            self.select_annotation(annotation_id, center_on=False)

        if model.app_type not in {"freetext", "square", "arrow"}:
            return True

        self.prepare_continuous_annotation_drag(model)
        if item_role == "resize-handle" and model.app_type in {"freetext", "square"}:
            window.active_scene_drag_kind = "resize"
            window.active_scene_drag_handle = self.active_resize_handle_at_scene_pos(scene_pos, model.id)
        elif item_role == "arrow-endpoint-handle" and model.app_type == "arrow":
            window.active_scene_drag_kind = "arrow-endpoint"
            window.active_scene_drag_handle = self.active_arrow_endpoint_handle_at_scene_pos(scene_pos, model.id)
        elif model.app_type == "square" and not self.is_scene_pos_near_continuous_square_border(model, scene_pos):
            return True
        else:
            window.active_scene_drag_kind = "move"
            window.active_scene_drag_handle = None
        window.active_scene_drag_annotation_id = model.id
        window.active_scene_drag_start_pos = scene_pos
        return True

    def is_scene_pos_near_continuous_square_border(self, model: AnnotationModel, scene_pos) -> bool:
        return self.is_scene_pos_near_square_border(model, scene_pos)

    def has_active_continuous_annotation_interaction(self) -> bool:
        window = self.window
        return (
            getattr(window, "view_mode", "single") == "continuous"
            and window.active_scene_drag_kind in {"move", "resize", "arrow-endpoint"}
            and window.active_scene_drag_start_pos is not None
            and window.selected_annotation_id is not None
        )

    def active_resize_handle_at_scene_pos(self, scene_pos, annotation_id: str) -> str | None:
        for item in self.window.scene.items(scene_pos):
            if item.data(2) == "resize-handle" and item.data(3) == annotation_id:
                value = item.data(4)
                return str(value) if value else None
        return None

    def active_arrow_endpoint_handle_at_scene_pos(self, scene_pos, annotation_id: str) -> str | None:
        for item in self.window.scene.items(scene_pos):
            if item.data(2) == "arrow-endpoint-handle" and item.data(3) == annotation_id:
                value = item.data(4)
                return str(value) if value else None
        return None

    def on_continuous_scene_mouse_move(self, scene_pos) -> bool:
        window = self.window
        if window.text_selection_active:
            self.update_continuous_default_text_selection(scene_pos)
            return True
        if window.active_tool is not None and not self.has_active_continuous_annotation_interaction():
            return self.on_continuous_tool_mouse_move(scene_pos)
        if (
            window.doc is None
            or getattr(window, "view_mode", "single") != "continuous"
            or window.active_scene_drag_kind not in {"move", "resize", "arrow-endpoint"}
            or window.active_scene_drag_start_pos is None
            or window.selected_annotation_id is None
        ):
            return False

        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None:
            return False

        if window.active_scene_drag_kind == "resize":
            if model.app_type not in {"freetext", "square"}:
                return False
            delta = scene_pos - window.active_scene_drag_start_pos
            self.update_continuous_resize_preview(model, window.active_scene_drag_handle or "", delta)
            return True

        if window.active_scene_drag_kind == "arrow-endpoint":
            if model.app_type != "arrow":
                return False
            delta = scene_pos - window.active_scene_drag_start_pos
            self.update_continuous_arrow_endpoint_preview(model, window.active_scene_drag_handle or "", delta)
            return True

        delta = self.clamp_continuous_move_delta(model, scene_pos - window.active_scene_drag_start_pos)
        if not self.is_intentional_annotation_move(delta):
            self.restore_continuous_annotation_drag_preview(model)
            return True

        window.view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        self.update_continuous_annotation_move_preview(model, delta)
        return True

    def on_continuous_scene_mouse_release(self, scene_pos) -> bool:
        window = self.window
        if window.text_selection_active:
            self.update_continuous_default_text_selection(scene_pos)
            window.text_selection_active = False
            window.text_selection_start_scene_pos = None
            if window.text_selection_items:
                window.statusBar().showMessage("Text selected")
            return True
        if window.active_tool is not None and not self.has_active_continuous_annotation_interaction():
            return self.on_continuous_tool_mouse_release(scene_pos)
        if (
            window.doc is None
            or getattr(window, "view_mode", "single") != "continuous"
            or window.active_scene_drag_kind not in {"move", "resize", "arrow-endpoint"}
            or window.active_scene_drag_start_pos is None
            or window.selected_annotation_id is None
        ):
            self.clear_scene_drag_state()
            return False

        model = window.annotation_model_map.get(window.selected_annotation_id)
        drag_kind = window.active_scene_drag_kind
        drag_handle = window.active_scene_drag_handle
        delta = scene_pos - window.active_scene_drag_start_pos
        if model is not None and drag_kind == "move":
            delta = self.clamp_continuous_move_delta(model, delta)
        self.clear_scene_drag_state()
        window.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if model is None:
            return True

        if drag_kind == "resize":
            if model.app_type not in {"freetext", "square"}:
                return True
            handle = drag_handle or ""
            if not handle:
                self.restore_continuous_annotation_drag_preview(model)
                return True
            dx_pdf = delta.x() / window.zoom
            dy_pdf = delta.y() / window.zoom
            controller = AnnotationInteractionController(window.zoom)
            if controller.is_small_delta(delta):
                self.restore_continuous_annotation_drag_preview(model)
                return True
            try:
                self.record_geometry_undo(f"Resize {model.pdf_type}", model)
                safe_dx_pdf, safe_dy_pdf = self.continuous_resize_safe_delta(
                    model,
                    handle,
                    dx_pdf,
                    dy_pdf,
                )
                PdfAnnotationWriter(window.doc).resize_rect_annotation(
                    window.doc[model.page_index],
                    model,
                    handle,
                    safe_dx_pdf,
                    safe_dy_pdf,
                    window.default_freetext_font_size,
                )
                window.mark_dirty()
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
                window.statusBar().showMessage(f"Resized {model.pdf_type} xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.restore_continuous_annotation_drag_preview(model)
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
                window.show_error("Resize annotation failed", exc)
            return True

        if drag_kind == "arrow-endpoint":
            if model.app_type != "arrow":
                return True
            handle = drag_handle or ""
            if not handle:
                self.restore_continuous_annotation_drag_preview(model)
                return True
            controller = AnnotationInteractionController(window.zoom)
            if controller.is_small_delta(delta):
                self.restore_continuous_annotation_drag_preview(model)
                return True
            start, end = self.safe_continuous_arrow_endpoint_points(model, handle, delta)
            try:
                self.record_geometry_undo("Move Arrow endpoint", model)
                annot = self.find_page_annotation_by_xref(window.doc[model.page_index], model.xref)
                if annot is None:
                    raise RuntimeError("The selected arrow annotation was not found on this page.")
                PdfAnnotationWriter(window.doc).set_line_annotation_points(
                    window.doc[model.page_index],
                    annot,
                    start,
                    end,
                )
                window.mark_dirty()
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
                window.statusBar().showMessage(f"Moved Arrow endpoint xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.restore_continuous_annotation_drag_preview(model)
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
                window.show_error("Move arrow endpoint failed", exc)
            return True

        if model.app_type not in {"freetext", "square", "arrow"}:
            return True

        controller = AnnotationInteractionController(window.zoom)
        if not self.is_intentional_annotation_move(delta) or controller.is_small_delta(delta):
            self.restore_continuous_annotation_drag_preview(model)
            return True

        dx_pdf = delta.x() / window.zoom
        dy_pdf = delta.y() / window.zoom
        try:
            self.record_geometry_undo(f"Move {model.pdf_type}", model)
            PdfAnnotationWriter(window.doc).move_annotation(window.doc[model.page_index], model, dx_pdf, dy_pdf)
            window.mark_dirty()
            self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            window.statusBar().showMessage(f"Moved {model.pdf_type} xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            self.restore_continuous_annotation_drag_preview(model)
            self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            window.show_error("Move annotation failed", exc)
        return True

    def on_continuous_scene_mouse_double_click(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None or getattr(window, "view_mode", "single") != "continuous":
            return False
        if window.is_inline_freetext_editor_hit(scene_pos):
            return False

        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if window.active_tool == "freetext" and hit is None:
            return self.begin_continuous_freetext_creation_at_scene_pos(scene_pos)
        if hit is None:
            return False
        annotation_id, item_role = hit
        if item_role in {"resize-handle", "arrow-endpoint-handle"}:
            return False

        model = window.annotation_model_map.get(annotation_id)
        if model is None:
            return False
        if model.app_type != "freetext":
            if annotation_id != window.selected_annotation_id:
                self.select_annotation(annotation_id, center_on=False)
            window.show_annotation_properties()
            return True

        if annotation_id != window.selected_annotation_id:
            self.select_annotation(annotation_id, center_on=False)
        self.begin_continuous_inline_freetext_edit(model)
        return True

    def begin_continuous_inline_freetext_edit(self, model: AnnotationModel) -> None:
        window = self.window
        rect = window.canvas_controller.continuous_scene_rect_for_pdf_rect(model.page_index, model.rect)
        layout = window.canvas_controller.continuous_layout_for_page(model.page_index)
        font_size = int(model.font_size or window.default_freetext_font_size)
        max_height = (layout.scene_rect.bottom() - rect.top()) if layout is not None else None
        self.hide_continuous_freetext_overlay(model)
        window.begin_inline_freetext_editor(
            rect.topLeft(),
            text=model.text,
            width=max(40.0, rect.width()),
            height=max(14.0, rect.height()),
            font_size=font_size,
            edit_annotation_id=model.id,
            select_all=False,
            stable_edit_mode=True,
            max_height=max_height,
        )
        window.inline_freetext_edit_page_index = model.page_index
        window.inline_freetext_edit_xref = model.xref
        window.statusBar().showMessage("Editing FreeText inline. Ctrl+Enter or Esc to apply.")

    def hide_continuous_freetext_overlay(self, model: AnnotationModel) -> None:
        for item in self.continuous_annotation_items_for_model(model):
            item.setVisible(False)
        for item in self.window.selection_items:
            item.setVisible(False)

    def continuous_page_index_at_scene_pos(self, scene_pos: QPointF) -> int | None:
        for layout in self.window.continuous_page_layouts:
            if layout.scene_rect.contains(scene_pos):
                return layout.page_index
        return None

    def continuous_pdf_point_from_scene_point(self, page_index: int, scene_pos: QPointF) -> tuple[float, float]:
        layout = self.window.canvas_controller.continuous_layout_for_page(page_index)
        if layout is None:
            return 0.0, 0.0
        return (
            (scene_pos.x() - layout.scene_rect.x()) / self.window.zoom,
            (scene_pos.y() - layout.scene_rect.y()) / self.window.zoom,
        )

    def continuous_pdf_rect_from_scene_points(self, page_index: int, start: QPointF, end: QPointF) -> fitz.Rect:
        x0, y0 = self.continuous_pdf_point_from_scene_point(page_index, start)
        x1, y1 = self.continuous_pdf_point_from_scene_point(page_index, end)
        page_rect = self.window.doc[page_index].rect
        rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        return fitz.Rect(
            max(page_rect.x0, min(rect.x0, page_rect.x1)),
            max(page_rect.y0, min(rect.y0, page_rect.y1)),
            max(page_rect.x0, min(rect.x1, page_rect.x1)),
            max(page_rect.y0, min(rect.y1, page_rect.y1)),
        )

    def clamp_scene_pos_to_continuous_page(self, page_index: int, scene_pos: QPointF) -> QPointF:
        layout = self.window.canvas_controller.continuous_layout_for_page(page_index)
        if layout is None:
            return scene_pos
        rect = layout.scene_rect
        return QPointF(
            max(rect.left(), min(scene_pos.x(), rect.right())),
            max(rect.top(), min(scene_pos.y(), rect.bottom())),
        )

    def on_continuous_tool_mouse_press(self, scene_pos) -> bool:
        window = self.window
        page_index = self.continuous_page_index_at_scene_pos(scene_pos)
        if page_index is None:
            return True
        start = self.clamp_scene_pos_to_continuous_page(page_index, scene_pos)
        if window.active_tool == "freetext":
            self.clear_text_selection()
            self.select_annotation(None)
            return True

        window.tool_start_scene_pos = start
        self.update_continuous_tool_preview(start)
        return True

    def begin_continuous_freetext_creation_at_scene_pos(self, scene_pos) -> bool:
        window = self.window
        page_index = self.continuous_page_index_at_scene_pos(scene_pos)
        if page_index is None or window.doc is None:
            return False
        start = self.clamp_scene_pos_to_continuous_page(page_index, scene_pos)
        if window.use_popup_freetext_input:
            point = self.continuous_pdf_point_from_scene_point(page_index, start)
            text, ok = QInputDialog.getMultiLineText(window, "Add FreeText", "Text:")
            if ok and text.strip():
                rect = window.default_freetext_rect(point, text.strip(), window.default_freetext_font_size)
                annot = PdfAnnotationWriter(window.doc).add_freetext_annotation(
                    window.doc[page_index],
                    rect,
                    text.strip(),
                    window.default_freetext_font_size,
                    (1, 0, 0),
                    window.use_foxit_freetext,
                )
                window.mark_dirty()
                if text_affects_anchor_data("", text.strip()):
                    window.mark_navigation_anchors_dirty()
                window.update_freetext_snapshot_text(page_index, annot.xref, text.strip())
                self.record_add_undo("Add FreeText", page_index, annot.xref)
                self.refresh_continuous_page_after_geometry_edit(page_index, annot.xref)
            self.set_active_tool("freetext")
            return True

        height = window.default_inline_freetext_editor_height(window.default_freetext_font_size)
        width = 220.0
        top_left = QPointF(start.x(), start.y() - height / 2)
        layout = window.canvas_controller.continuous_layout_for_page(page_index)
        if layout is not None:
            top_left.setX(max(layout.scene_rect.left(), min(top_left.x(), layout.scene_rect.right() - width)))
            top_left.setY(max(layout.scene_rect.top(), min(top_left.y(), layout.scene_rect.bottom() - height)))
        max_height = (layout.scene_rect.bottom() - top_left.y()) if layout is not None else None
        window.begin_inline_freetext_editor(
            top_left,
            width=width,
            height=height,
            font_size=window.default_freetext_font_size,
            max_height=max_height,
        )
        window.inline_freetext_edit_page_index = page_index
        window.inline_freetext_edit_xref = None
        return True

    def on_continuous_tool_mouse_move(self, scene_pos) -> bool:
        window = self.window
        if window.active_tool is None or window.tool_start_scene_pos is None:
            return False
        page_index = self.continuous_page_index_at_scene_pos(window.tool_start_scene_pos)
        if page_index is None:
            return True
        self.update_continuous_tool_preview(self.clamp_scene_pos_to_continuous_page(page_index, scene_pos))
        return True

    def on_continuous_tool_mouse_release(self, scene_pos) -> bool:
        window = self.window
        if window.active_tool is None or window.tool_start_scene_pos is None or window.doc is None:
            return False
        tool = window.active_tool
        start = window.tool_start_scene_pos
        page_index = self.continuous_page_index_at_scene_pos(start)
        self.remove_tool_preview()
        window.tool_start_scene_pos = None
        if page_index is None:
            return True
        end = self.clamp_scene_pos_to_continuous_page(page_index, scene_pos)

        try:
            start_pdf = self.continuous_pdf_point_from_scene_point(page_index, start)
            end_pdf = self.continuous_pdf_point_from_scene_point(page_index, end)
            xref = None
            if tool == "square":
                rect = self.continuous_pdf_rect_from_scene_points(page_index, start, end)
                if rect.width < 10.0 or rect.height < 10.0:
                    self.select_annotation(None)
                    self.show_page_status()
                    return True
                annot = PdfAnnotationWriter(window.doc).add_square_annotation(window.doc[page_index], rect)
                xref = annot.xref
                self.record_add_undo("Add Square", page_index, xref)
            elif tool == "arrow":
                if abs(end_pdf[0] - start_pdf[0]) < 10 and abs(end_pdf[1] - start_pdf[1]) < 10:
                    self.select_annotation(None)
                    self.show_page_status()
                    return True
                annot = PdfAnnotationWriter(window.doc).add_arrow_annotation(window.doc[page_index], start_pdf, end_pdf)
                xref = annot.xref
                self.record_add_undo("Add Arrow", page_index, xref)
            elif tool == "highlight":
                rects = window.highlight_rects_from_text_flow(window.doc[page_index], start_pdf, end_pdf)
                if not rects:
                    self.select_annotation(None)
                    self.show_page_status()
                    return True
                annot = PdfAnnotationWriter(window.doc).add_highlight_annotation(
                    window.doc[page_index],
                    rects,
                    window.default_highlight_color,
                    window.default_highlight_opacity,
                )
                xref = annot.xref
                self.record_add_undo("Add Highlight", page_index, xref)
            if xref is not None:
                window.mark_dirty()
                self.refresh_continuous_page_after_geometry_edit(page_index, xref)
                window.statusBar().showMessage(f"Added {tool} xref={xref}. Use Save to persist.")
            self.set_active_tool(tool)
        except Exception as exc:
            window.show_error(f"Add {tool} failed", exc)
        return True

    def update_continuous_tool_preview(self, scene_pos) -> None:
        window = self.window
        if window.active_tool is None or window.tool_start_scene_pos is None:
            return
        self.remove_tool_preview()
        if window.active_tool == "highlight":
            self.update_continuous_highlight_tool_preview(scene_pos)
            return
        pen = QPen(QColor(0, 0, 0), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        if window.active_tool == "square":
            rect = QRectF(window.tool_start_scene_pos, scene_pos).normalized()
            window.tool_preview_item = window.scene.addRect(rect, pen, QBrush(Qt.BrushStyle.NoBrush))
        elif window.active_tool == "arrow":
            window.tool_preview_item = window.scene.addLine(QLineF(window.tool_start_scene_pos, scene_pos), pen)
        if window.tool_preview_item is not None:
            window.tool_preview_item.setZValue(30)

    def update_continuous_highlight_tool_preview(self, scene_pos) -> None:
        window = self.window
        if window.tool_start_scene_pos is None or window.doc is None:
            return
        page_index = self.continuous_page_index_at_scene_pos(window.tool_start_scene_pos)
        if page_index is None:
            return
        start_pdf = self.continuous_pdf_point_from_scene_point(page_index, window.tool_start_scene_pos)
        end_pdf = self.continuous_pdf_point_from_scene_point(page_index, scene_pos)
        rects = window.highlight_rects_from_text_flow(window.doc[page_index], start_pdf, end_pdf)
        if not rects:
            return
        red, green, blue = (
            max(0, min(255, int(round(channel * 255)))) for channel in window.default_highlight_color[:3]
        )
        alpha = max(20, min(255, int(round(window.default_highlight_opacity * 255))))
        brush = QBrush(QColor(red, green, blue, alpha))
        for rect in rects:
            scene_rect = window.canvas_controller.continuous_scene_rect_for_pdf_rect(page_index, rect)
            item = window.scene.addRect(scene_rect, QPen(Qt.PenStyle.NoPen), brush)
            item.setZValue(30)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            window.tool_preview_items.append(item)

    def continuous_annotation_items_for_model(self, model: AnnotationModel) -> list[QGraphicsItem]:
        item = self.window.continuous_page_items.get(model.page_index)
        if item is None:
            return []
        return [
            annotation_item
            for annotation_item in (item.annotation_items or [])
            if annotation_item.data(0) == model.id
        ]

    def prepare_continuous_annotation_drag(self, model: AnnotationModel) -> None:
        for item in self.continuous_annotation_items_for_model(model):
            item.setData(1, item.pos())
        for item in self.window.selection_items:
            item.setData(1, item.pos())

    def restore_continuous_annotation_drag_preview(self, model: AnnotationModel) -> None:
        for item in self.continuous_annotation_items_for_model(model):
            start_pos = item.data(1)
            if isinstance(start_pos, QPointF) and item.pos() != start_pos:
                item.setPos(start_pos)
        for item in self.window.selection_items:
            start_pos = item.data(1)
            if isinstance(start_pos, QPointF) and item.pos() != start_pos:
                item.setPos(start_pos)

    def update_continuous_annotation_move_preview(self, model: AnnotationModel, delta: QPointF) -> None:
        for item in self.continuous_annotation_items_for_model(model):
            start_pos = item.data(1)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            target_pos = start_pos + delta
            if item.pos() != target_pos:
                item.setPos(target_pos)
        for item in self.window.selection_items:
            start_pos = item.data(1)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            target_pos = start_pos + delta
            if item.pos() != target_pos:
                item.setPos(target_pos)

    def update_continuous_resize_preview(self, model: AnnotationModel, handle: str, delta: QPointF) -> None:
        if not handle:
            return
        dx_pdf = delta.x() / self.window.zoom
        dy_pdf = delta.y() / self.window.zoom
        preview_rect = self.continuous_resized_scene_rect_preview(
            model,
            handle,
            dx_pdf,
            dy_pdf,
        )
        for item in self.window.selection_items:
            if item.data(2) == "selection-rect" and item.data(3) == model.id and isinstance(item, QGraphicsRectItem):
                item.setRect(preview_rect)
            elif item.data(2) == "resize-handle" and item.data(3) == model.id and isinstance(item, QGraphicsRectItem):
                self.update_resize_handle_preview(item, preview_rect)

    def update_resize_handle_preview(self, item: QGraphicsRectItem, rect: QRectF) -> None:
        handle = str(item.data(4))
        points = {
            "top-left": rect.topLeft(),
            "top": QPointF(rect.center().x(), rect.top()),
            "top-right": rect.topRight(),
            "right": QPointF(rect.right(), rect.center().y()),
            "bottom-right": rect.bottomRight(),
            "bottom": QPointF(rect.center().x(), rect.bottom()),
            "bottom-left": rect.bottomLeft(),
            "left": QPointF(rect.left(), rect.center().y()),
        }
        point = points.get(handle)
        if point is None:
            return
        current = item.rect()
        size = max(current.width(), current.height(), 6.0)
        item.setRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))

    def update_continuous_arrow_endpoint_preview(self, model: AnnotationModel, handle: str, delta: QPointF) -> None:
        if not handle:
            return
        start, end = self.safe_continuous_arrow_endpoint_points(model, handle, delta)
        start_point = self.window.canvas_controller.continuous_scene_point_for_pdf_point(model.page_index, start)
        end_point = self.window.canvas_controller.continuous_scene_point_for_pdf_point(model.page_index, end)
        for item in self.window.selection_items:
            role = item.data(2)
            if role == "arrow-endpoint-handle" and item.data(3) == model.id and isinstance(item, QGraphicsRectItem):
                point = start_point if item.data(4) == "start" else end_point
                current = item.rect()
                size = max(current.width(), current.height(), 5.0)
                item.setRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))

    def safe_continuous_arrow_endpoint_points(
        self,
        model: AnnotationModel,
        handle: str,
        delta: QPointF,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        if model.line_start is None or model.line_end is None:
            return (model.rect.x0, model.rect.y0), (model.rect.x1, model.rect.y1)
        page_rect = self.window.doc[model.page_index].rect
        dx_pdf = delta.x() / self.window.zoom
        dy_pdf = delta.y() / self.window.zoom
        start = list(model.line_start)
        end = list(model.line_end)
        target = start if handle == "start" else end
        target[0] = max(page_rect.x0, min(page_rect.x1, target[0] + dx_pdf))
        target[1] = max(page_rect.y0, min(page_rect.y1, target[1] + dy_pdf))
        return (float(start[0]), float(start[1])), (float(end[0]), float(end[1]))

    def continuous_resized_scene_rect_preview(
        self,
        model: AnnotationModel,
        handle: str,
        dx_pdf: float,
        dy_pdf: float,
    ) -> QRectF:
        page = self.window.doc[model.page_index]
        min_width = 20.0 if model.app_type == "freetext" else 10.0
        min_height = 12.0 if model.app_type == "freetext" else 10.0
        rect = fitz.Rect(model.rect)
        if handle == "top-left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "top-right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "bottom-right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "bottom-left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "top":
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
        elif handle == "bottom":
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)

        rect.x0 = max(page.rect.x0, min(rect.x0, page.rect.x1 - min_width))
        rect.y0 = max(page.rect.y0, min(rect.y0, page.rect.y1 - min_height))
        rect.x1 = min(page.rect.x1, max(rect.x1, rect.x0 + min_width))
        rect.y1 = min(page.rect.y1, max(rect.y1, rect.y0 + min_height))
        return self.window.canvas_controller.continuous_scene_rect_for_pdf_rect(model.page_index, rect)

    def continuous_resize_safe_delta(
        self,
        model: AnnotationModel,
        handle: str,
        dx_pdf: float,
        dy_pdf: float,
    ) -> tuple[float, float]:
        page = self.window.doc[model.page_index]
        min_width = 20.0 if model.app_type == "freetext" else 10.0
        min_height = 12.0 if model.app_type == "freetext" else 10.0
        rect = fitz.Rect(model.rect)
        proposed = fitz.Rect(rect)
        if handle == "top-left":
            proposed.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            proposed.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "top-right":
            proposed.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            proposed.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "bottom-right":
            proposed.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            proposed.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "bottom-left":
            proposed.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            proposed.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "top":
            proposed.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "right":
            proposed.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
        elif handle == "bottom":
            proposed.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "left":
            proposed.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)

        proposed.x0 = max(page.rect.x0, min(proposed.x0, page.rect.x1 - min_width))
        proposed.y0 = max(page.rect.y0, min(proposed.y0, page.rect.y1 - min_height))
        proposed.x1 = min(page.rect.x1, max(proposed.x1, proposed.x0 + min_width))
        proposed.y1 = min(page.rect.y1, max(proposed.y1, proposed.y0 + min_height))

        if handle in {"top-left", "bottom-left", "left"}:
            safe_dx = proposed.x0 - rect.x0
        elif handle in {"top-right", "bottom-right", "right"}:
            safe_dx = proposed.x1 - rect.x1
        else:
            safe_dx = 0.0

        if handle in {"top-left", "top-right", "top"}:
            safe_dy = proposed.y0 - rect.y0
        elif handle in {"bottom-left", "bottom-right", "bottom"}:
            safe_dy = proposed.y1 - rect.y1
        else:
            safe_dy = 0.0
        return safe_dx, safe_dy

    def continuous_annotation_bbox(self, model: AnnotationModel) -> QRectF:
        if model.app_type == "arrow" and model.line_start is not None and model.line_end is not None:
            start = self.window.canvas_controller.continuous_scene_point_for_pdf_point(model.page_index, model.line_start)
            end = self.window.canvas_controller.continuous_scene_point_for_pdf_point(model.page_index, model.line_end)
            return QRectF(start, end).normalized()
        return self.window.canvas_controller.continuous_scene_rect_for_pdf_rect(model.page_index, model.rect)

    def clamp_continuous_move_delta(self, model: AnnotationModel, delta: QPointF) -> QPointF:
        layout = self.window.canvas_controller.continuous_layout_for_page(model.page_index)
        if layout is None:
            return delta

        bbox = self.continuous_annotation_bbox(model)
        page_rect = layout.scene_rect
        dx = delta.x()
        dy = delta.y()
        if bbox.left() + dx < page_rect.left():
            dx = page_rect.left() - bbox.left()
        elif bbox.right() + dx > page_rect.right():
            dx = page_rect.right() - bbox.right()

        if bbox.top() + dy < page_rect.top():
            dy = page_rect.top() - bbox.top()
        elif bbox.bottom() + dy > page_rect.bottom():
            dy = page_rect.bottom() - bbox.bottom()

        return QPointF(dx, dy)

    def refresh_continuous_page_after_geometry_edit(self, page_index: int, xref: int) -> None:
        window = self.window
        window.clear_selection_items()
        window.canvas_controller.invalidate_continuous_annotation_cache(page_index)
        if page_index in window.continuous_page_items:
            window.canvas_controller.unload_continuous_page(page_index)
        window.canvas_controller.ensure_continuous_page_rendered(page_index)
        window.current_annotations = window.canvas_controller.continuous_load_page_annotations(window.page_index)
        selected = next(
            (model for model in window.canvas_controller.continuous_load_page_annotations(page_index) if model.xref == xref),
            None,
        )
        if selected is not None:
            window.annotation_model_map[selected.id] = selected
            self.select_annotation(selected.id, center_on=False)
        else:
            self.select_annotation(None)
        window.refresh_annotations_table()
        window.refresh_properties_panel()
        window.update_actions()
        window.save_active_session_state()

    def on_scene_mouse_move(self, scene_pos=None) -> None:
        window = self.window
        if window.text_selection_active:
            self.update_default_text_selection(scene_pos)
            return

        if window.selected_annotation_id is None:
            return

        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None:
            return

        if window.active_scene_drag_kind == "move":
            if scene_pos is not None and window.active_scene_drag_start_pos is not None:
                delta = scene_pos - window.active_scene_drag_start_pos
                if not self.is_intentional_annotation_move(delta):
                    self.restore_annotation_drag_preview(model.id)
                    return
            self.update_annotation_move_preview(model, scene_pos)
        if model.app_type not in {"square", "freetext"}:
            return

        if window.active_scene_drag_kind not in {None, "resize"}:
            return

        interaction = AnnotationInteractionController(window.zoom).rect_resize(model, window.selection_items)
        if interaction is None:
            return

        preview_rect = self.resized_scene_rect_preview(
            model,
            interaction.handle,
            interaction.dx_pdf,
            interaction.dy_pdf,
        )
        for item in window.selection_items:
            if item.data(2) == "selection-rect" and item.data(3) == model.id and isinstance(item, QGraphicsRectItem):
                item.setRect(preview_rect)

    def update_annotation_move_preview(self, model: AnnotationModel, scene_pos=None) -> None:
        window = self.window
        if not window.is_draggable_model(model):
            return

        controller = AnnotationInteractionController(window.zoom)
        if controller.rect_resize(model, window.selection_items) is not None:
            return
        if controller.arrow_endpoint_move(model, window.selection_items) is not None:
            return

        if scene_pos is not None and window.active_scene_drag_start_pos is not None:
            delta = scene_pos - window.active_scene_drag_start_pos
        else:
            delta = controller.annotation_drag_delta(model.id, window.annotation_item_map)
        if delta is None:
            return

        for item in window.annotation_item_map.get(model.id, []):
            start_pos = item.data(1)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            target_pos = start_pos + delta
            if item.pos() != target_pos:
                item.setPos(target_pos)

        for item in window.selection_items:
            if item.data(3) != model.id:
                continue
            if item.data(2) != "selection-rect":
                continue
            if item.pos() != delta:
                item.setPos(delta)

    def on_scene_mouse_hover(self, scene_pos) -> None:
        window = self.window
        if window.doc is None:
            return
        if self.annotation_hit_at_scene_pos(scene_pos) is not None:
            window.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if window.active_tool is not None:
            if window.active_tool == "highlight":
                window.view.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            else:
                window.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        cursor = Qt.CursorShape.IBeamCursor if self.is_text_at_scene_pos(scene_pos) else Qt.CursorShape.ArrowCursor
        window.view.viewport().setCursor(cursor)

    def on_continuous_scene_mouse_hover(self, scene_pos) -> None:
        window = self.window
        if window.doc is None or getattr(window, "view_mode", "single") != "continuous":
            return
        if self.annotation_hit_at_scene_pos(scene_pos) is not None:
            window.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        if window.active_tool == "highlight":
            window.view.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        elif window.active_tool is not None:
            window.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            page_index = self.continuous_page_index_at_scene_pos(scene_pos)
            cursor = (
                Qt.CursorShape.IBeamCursor
                if page_index is not None and self.is_continuous_text_at_scene_pos(page_index, scene_pos)
                else Qt.CursorShape.ArrowCursor
            )
            window.view.viewport().setCursor(cursor)

    def update_default_text_selection(self, scene_pos) -> None:
        window = self.window
        if window.doc is None or window.text_selection_start_scene_pos is None or scene_pos is None:
            return

        start_pdf = window.pdf_point_from_scene_point(window.text_selection_start_scene_pos)
        end_pdf = window.pdf_point_from_scene_point(scene_pos)
        rects = window.highlight_rects_from_text_flow(window.current_page(), start_pdf, end_pdf)
        self.replace_text_selection_rects(rects)

    def update_continuous_default_text_selection(self, scene_pos) -> None:
        window = self.window
        page_index = window.text_selection_page_index
        if (
            window.doc is None
            or page_index is None
            or page_index < 0
            or page_index >= len(window.doc)
            or window.text_selection_start_scene_pos is None
            or scene_pos is None
        ):
            return

        end_scene_pos = self.clamp_scene_pos_to_continuous_page(page_index, scene_pos)
        start_pdf = self.continuous_pdf_point_from_scene_point(page_index, window.text_selection_start_scene_pos)
        end_pdf = self.continuous_pdf_point_from_scene_point(page_index, end_scene_pos)
        rects = window.highlight_rects_from_text_flow(window.doc[page_index], start_pdf, end_pdf)
        self.replace_text_selection_rects(rects)

    def replace_text_selection_rects(self, rects: list[fitz.Rect]) -> None:
        window = self.window
        for item in window.text_selection_items:
            try:
                window.scene.removeItem(item)
            except RuntimeError:
                pass
        window.text_selection_items.clear()

        window.text_selection_rects = [fitz.Rect(rect) for rect in rects]
        brush = QBrush(QColor(120, 120, 120, 95))
        for rect in rects:
            if getattr(window, "view_mode", "single") == "continuous" and window.text_selection_page_index is not None:
                scene_rect = window.canvas_controller.continuous_scene_rect_for_pdf_rect(
                    window.text_selection_page_index,
                    rect,
                )
            else:
                scene_rect = window.scene_rect(rect)
            item = window.scene.addRect(scene_rect, QPen(Qt.PenStyle.NoPen), brush)
            item.setZValue(28)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            window.text_selection_items.append(item)

    def on_scene_mouse_double_click(self, scene_pos) -> bool:
        window = self.window
        if scene_pos is None:
            return False

        if window.active_tool == "freetext":
            hit = self.annotation_hit_at_scene_pos(scene_pos)
            if hit is not None:
                annotation_id, _item_role = hit
                model = window.annotation_model_map.get(annotation_id)
                if model is not None:
                    if annotation_id != window.selected_annotation_id:
                        self.select_annotation(annotation_id)
                    if model.app_type == "freetext" and not window.use_popup_freetext_input:
                        return window.begin_inline_edit_for_selected_freetext(scene_pos)
                    window.show_annotation_properties()
                    return True
            return self.begin_single_freetext_creation_at_scene_pos(scene_pos)

        if window.active_tool == "highlight":
            if self.add_highlight_for_word_at_scene_pos(scene_pos):
                return True
            window.statusBar().showMessage("No word found at double-click position.")
            return True

        if window.active_tool is None and self.should_text_double_click_select_word(scene_pos):
            if self.select_word_at_scene_pos(scene_pos):
                return True

        if window.selected_annotation_id is None:
            return False
        if not window.use_popup_freetext_input and window.begin_inline_edit_for_selected_freetext(scene_pos):
            return True
        window.show_annotation_properties()
        return True

    def on_annotation_tool_blank_right_double_click(self, scene_pos) -> bool:
        window = self.window
        if scene_pos is None or window.active_tool not in {"freetext", "square", "highlight", "arrow"}:
            return False
        if window.is_inline_freetext_editor_hit(scene_pos):
            return False
        if self.annotation_hit_at_scene_pos(scene_pos) is not None:
            return False
        self.cancel_add_tool()
        self.show_page_status()
        return True

    def begin_single_freetext_creation_at_scene_pos(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None:
            return False
        start = window.clamp_scene_pos_to_page(scene_pos)
        if window.use_popup_freetext_input:
            try:
                xref = self.create_freetext_annotation_at_point(window.pdf_point_from_scene_point(start))
                if xref is not None:
                    self.record_add_undo("Add FreeText", window.page_index, xref)
                self.set_active_tool("freetext")
            except Exception as exc:
                window.show_error("Add FreeText failed", exc)
            return True
        window.begin_inline_freetext_editor_at_left_center(start)
        return True

    def should_text_double_click_select_word(self, scene_pos) -> bool:
        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None:
            return True
        annotation_id, _item_role = hit
        model = self.window.annotation_model_map.get(annotation_id)
        return model is not None and model.app_type == "highlight"

    def select_word_at_scene_pos(self, scene_pos) -> bool:
        rects = self.word_rects_at_scene_pos(scene_pos)
        if not rects:
            return False
        self.clear_text_selection()
        self.replace_text_selection_rects(rects)
        self.window.statusBar().showMessage("Word selected")
        return True

    def add_highlight_for_word_at_scene_pos(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None:
            return False
        rects = self.word_rects_at_scene_pos(scene_pos)
        if not rects:
            return False
        try:
            annot = PdfAnnotationWriter(window.doc).add_highlight_annotation(
                window.current_page(),
                [fitz.Rect(rect) for rect in rects],
                window.default_highlight_color,
                window.default_highlight_opacity,
            )
            window.mark_dirty()
            self.record_add_undo("Add Highlight", window.page_index, annot.xref)
            self.refresh_overlay_and_select_annotation_by_xref(annot.xref, center_on=False)
            self.set_active_tool("highlight")
            window.statusBar().showMessage(f"Added Highlight xref={annot.xref}. Use Save to persist.")
            return True
        except Exception as exc:
            window.show_error("Add Highlight failed", exc)
            return True

    def word_rects_at_scene_pos(self, scene_pos) -> list[fitz.Rect]:
        window = self.window
        if window.doc is None:
            return []

        px, py = window.pdf_point_from_scene_point(scene_pos)
        try:
            lines = window.canvas_controller.current_page_text_lines(window.current_page())
        except Exception:
            return []

        hit = self.word_char_range_at_pdf_point(lines, px, py)
        if hit is None:
            return []
        line_index, begin, finish = hit
        chars = lines[line_index][begin:finish]
        if not chars:
            return []
        boxes = [char["bbox"] for char in chars]
        return [window.canvas_controller.highlight_rect_from_selected_chars(chars, boxes)]

    def word_char_range_at_pdf_point(
        self, lines: list[list[dict]], px: float, py: float
    ) -> tuple[int, int, int] | None:
        for line_index, chars in enumerate(lines):
            if not chars:
                continue
            line_rect = fitz.Rect(chars[0]["bbox"])
            for char in chars[1:]:
                line_rect |= char["bbox"]
            if not line_rect.contains(fitz.Point(px, py)):
                continue

            char_index = self.char_index_at_pdf_point(chars, px, py)
            if char_index is None:
                return None
            kind = self.word_char_kind(str(chars[char_index].get("text", "")))
            if kind is None:
                return None

            begin = char_index
            while begin > 0 and self.word_char_kind(str(chars[begin - 1].get("text", ""))) == kind:
                begin -= 1

            finish = char_index + 1
            while finish < len(chars) and self.word_char_kind(str(chars[finish].get("text", ""))) == kind:
                finish += 1

            return line_index, begin, finish
        return None

    def char_index_at_pdf_point(self, chars: list[dict], px: float, py: float) -> int | None:
        best_index = None
        best_distance = float("inf")
        for index, char in enumerate(chars):
            bbox = fitz.Rect(char["bbox"])
            if bbox.contains(fitz.Point(px, py)):
                return index
            center_x = (bbox.x0 + bbox.x1) / 2
            center_y = (bbox.y0 + bbox.y1) / 2
            distance = abs(px - center_x) + abs(py - center_y)
            if distance < best_distance:
                best_index = index
                best_distance = distance
        return best_index

    def word_char_kind(self, text: str) -> str | None:
        if not text:
            return None
        character = text[0]
        if self.is_cjk_char(character):
            return "cjk"
        if character.isascii() and (character.isalnum() or character in {"_", "'", "-"}):
            return "latin"
        return None

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

    def apply_quick_highlight_color(self, color: tuple[float, float, float], opacity: float) -> None:
        window = self.window
        opacity = max(0.0, min(1.0, float(opacity)))
        if window.doc is not None and window.text_selection_rects:
            try:
                page_index = (
                    window.text_selection_page_index
                    if getattr(window, "view_mode", "single") == "continuous"
                    and window.text_selection_page_index is not None
                    else window.page_index
                )
                annot = PdfAnnotationWriter(window.doc).add_highlight_annotation(
                    window.doc[page_index],
                    [fitz.Rect(rect) for rect in window.text_selection_rects],
                    color,
                    opacity,
                )
                window.mark_dirty()
                self.clear_text_selection()
                if getattr(window, "view_mode", "single") == "continuous":
                    self.refresh_continuous_page_after_geometry_edit(page_index, annot.xref)
                else:
                    self.refresh_overlay_and_select_annotation_by_xref(annot.xref, center_on=False)
                window.statusBar().showMessage(f"Added Highlight xref={annot.xref}. Use Save to persist.")
            except Exception as exc:
                window.show_error("Add Highlight failed", exc)
            return

        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if window.doc is not None and model is not None and model.app_type == "highlight":
            try:
                if getattr(window, "view_mode", "single") == "continuous":
                    self.update_highlight_annotation_on_page(model.page_index, model, color, opacity)
                else:
                    self.update_highlight_annotation(model, color, opacity)
                window.mark_dirty()
                if getattr(window, "view_mode", "single") == "continuous":
                    self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
                else:
                    window.refresh_annotation_overlay(preserve_selection=True)
                window.statusBar().showMessage(f"Edited Highlight xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                if getattr(window, "view_mode", "single") == "continuous":
                    self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
                else:
                    window.refresh_annotation_overlay(preserve_selection=True)
                window.show_error("Edit Highlight failed", exc)
            return

        window.statusBar().showMessage("Double-click a highlight color button to set the default color.")

    def copy_selected_pdf_text_to_clipboard(self) -> bool:
        window = self.window
        if window.doc is None:
            return False

        text = ""
        if window.text_selection_rects:
            if getattr(window, "view_mode", "single") == "continuous" and window.text_selection_page_index is not None:
                text = selected_text_from_rects(window.doc[window.text_selection_page_index], window.text_selection_rects)
            else:
                text = selected_text_from_rects(window.current_page(), window.text_selection_rects)
        elif window.selected_annotation_id is not None:
            model = window.annotation_model_map.get(window.selected_annotation_id)
            if model is not None and model.app_type == "highlight":
                text = highlight_selected_text(window.current_page(), model)

        text = self.normalize_copied_text(text)
        if not text:
            return False

        QApplication.clipboard().setText(text)
        window.statusBar().showMessage("Copied text")
        return True

    def normalize_copied_text(self, text: str) -> str:
        lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        return "\n".join(line for line in lines if line).strip()

    def set_default_highlight_color(self, color: tuple[float, float, float]) -> None:
        window = self.window
        window.default_highlight_color = color
        window.save_app_settings()
        if hasattr(window, "quick_highlight_opacity_spin"):
            from app.main_window.actions import refresh_quick_highlight_button_styles

            refresh_quick_highlight_button_styles(window)
        window.statusBar().showMessage(
            f"Default highlight color updated. Opacity {round(window.default_highlight_opacity * 100)}%."
        )

    def resized_scene_rect_preview(
        self,
        model: AnnotationModel,
        handle: str,
        dx_pdf: float,
        dy_pdf: float,
    ) -> QRectF:
        window = self.window
        min_width = 20.0 if model.app_type == "freetext" else 10.0
        min_height = 12.0 if model.app_type == "freetext" else 10.0
        rect = fitz.Rect(model.rect)
        if handle == "top-left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "top-right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "bottom-right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "bottom-left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "top":
            rect.y0 = min(rect.y0 + dy_pdf, rect.y1 - min_height)
        elif handle == "right":
            rect.x1 = max(rect.x1 + dx_pdf, rect.x0 + min_width)
        elif handle == "bottom":
            rect.y1 = max(rect.y1 + dy_pdf, rect.y0 + min_height)
        elif handle == "left":
            rect.x0 = min(rect.x0 + dx_pdf, rect.x1 - min_width)
        return window.scene_rect(rect)

    def begin_add_tool(self, tool: str) -> None:
        window = self.window
        if window.doc is None:
            return
        if window.active_tool == tool:
            self.cancel_add_tool()
            self.show_page_status()
            return

        self.set_active_tool(tool)

    def activate_text_mode(self) -> None:
        self.set_active_tool(None)
        self.show_page_status()

    def set_active_tool(self, tool: str | None) -> None:
        self.window.reading_workspace.set_annotation_tool(tool)

    def apply_active_tool(self, tool: str | None) -> None:
        window = self.window
        self.remove_tool_preview()
        if tool is not None:
            self.clear_text_selection()
        window.active_tool = tool
        window.tool_start_scene_pos = None
        if window.reading_workspace.active is window:
            window.reading_workspace.sync_tool_actions()

        if tool is None:
            window.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        if tool == "highlight":
            window.view.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        else:
            window.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        if window.reading_workspace.active is not window:
            return
        if tool == "freetext":
            window.statusBar().showMessage("Click on the page to add FreeText. Press Esc to cancel.")
        elif tool == "highlight":
            window.statusBar().showMessage(
                f"Highlight mode: default opacity {round(window.default_highlight_opacity * 100)}%. "
                "Drag text or double-click a word."
            )
        else:
            window.statusBar().showMessage(f"Drag on the page to add {tool}. Press Esc to cancel.")

    def cancel_add_tool(self) -> None:
        self.remove_tool_preview()
        self.set_active_tool(None)

    def clear_tool_interaction(self) -> None:
        """End an unfinished gesture without changing the workspace tool."""
        self.remove_tool_preview()
        self.window.tool_start_scene_pos = None
        self.window.clear_scene_drag_state()
        self.clear_text_selection()

    def on_tool_mouse_press(self, scene_pos, button=None) -> bool:
        window = self.window
        if window.active_tool is None or window.doc is None:
            return False
        if button is not None and button != Qt.MouseButton.LeftButton:
            return False
        if window.is_inline_freetext_editor_hit(scene_pos):
            return False
        if window.confirm_inline_freetext_editor_from_canvas_click(scene_pos):
            return True
        if self.annotation_hit_at_scene_pos(scene_pos) is not None:
            return False
        if window.active_tool == "freetext":
            self.clear_text_selection()
            self.select_annotation(None)
            return True
        window.clear_selection_items()
        start = window.clamp_scene_pos_to_page(scene_pos)

        window.tool_start_scene_pos = start
        self.update_tool_preview(window.tool_start_scene_pos)
        return True

    def on_tool_mouse_move(self, scene_pos) -> bool:
        window = self.window
        if window.active_tool is None or window.tool_start_scene_pos is None:
            return False
        self.update_tool_preview(window.clamp_scene_pos_to_page(scene_pos))
        return True

    def on_tool_mouse_release(self, scene_pos) -> bool:
        window = self.window
        if window.active_tool is None or window.tool_start_scene_pos is None or window.doc is None:
            return False

        tool = window.active_tool
        start = window.tool_start_scene_pos
        end = window.clamp_scene_pos_to_page(scene_pos)
        self.remove_tool_preview()
        window.tool_start_scene_pos = None

        try:
            if tool == "square":
                rect = window.pdf_rect_from_scene_points(start, end)
                min_width = 10.0
                min_height = 10.0
                if rect.width < min_width or rect.height < min_height:
                    self.select_annotation(None)
                    self.show_page_status()
                    return True
                xref = self.create_square_annotation(rect)
                self.record_add_undo("Add Square", window.page_index, xref)
            elif tool == "highlight":
                start_pdf = window.pdf_point_from_scene_point(start)
                end_pdf = window.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 1 and abs(end_pdf[1] - start_pdf[1]) < 1:
                    self.select_annotation(None)
                    self.show_page_status()
                    return True
                xref = self.create_highlight_annotation_from_text_flow(start_pdf, end_pdf)
                if xref is not None:
                    self.record_add_undo("Add Highlight", window.page_index, xref)
            elif tool == "arrow":
                start_pdf = window.pdf_point_from_scene_point(start)
                end_pdf = window.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 10 and abs(end_pdf[1] - start_pdf[1]) < 10:
                    self.select_annotation(None)
                    self.show_page_status()
                    return True
                xref = self.create_arrow_annotation(start_pdf, end_pdf)
                self.record_add_undo("Add Arrow", window.page_index, xref)
            self.set_active_tool(tool)
        except Exception as exc:
            window.show_error(f"Add {tool} failed", exc)
        return True

    def update_tool_preview(self, scene_pos) -> None:
        window = self.window
        if window.active_tool is None or window.tool_start_scene_pos is None:
            return

        self.remove_tool_preview()
        if window.active_tool == "highlight":
            self.update_highlight_tool_preview(scene_pos)
            return

        pen = QPen(QColor(0, 0, 0), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        if window.active_tool == "square":
            rect = QRectF(window.tool_start_scene_pos, scene_pos).normalized()
            window.tool_preview_item = window.scene.addRect(rect, pen, QBrush(Qt.BrushStyle.NoBrush))
        elif window.active_tool == "arrow":
            window.tool_preview_item = window.scene.addLine(QLineF(window.tool_start_scene_pos, scene_pos), pen)
        if window.tool_preview_item is not None:
            window.tool_preview_item.setZValue(30)

    def update_highlight_tool_preview(self, scene_pos) -> None:
        window = self.window
        if window.tool_start_scene_pos is None or window.doc is None:
            return

        start_pdf = window.pdf_point_from_scene_point(window.tool_start_scene_pos)
        end_pdf = window.pdf_point_from_scene_point(scene_pos)
        rects = window.highlight_rects_from_text_flow(window.current_page(), start_pdf, end_pdf)
        if not rects:
            return

        red, green, blue = (
            max(0, min(255, int(round(channel * 255)))) for channel in window.default_highlight_color[:3]
        )
        alpha = max(20, min(255, int(round(window.default_highlight_opacity * 255))))
        brush = QBrush(QColor(red, green, blue, alpha))
        for rect in rects:
            item = window.scene.addRect(window.scene_rect(rect), QPen(Qt.PenStyle.NoPen), brush)
            item.setZValue(30)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            window.tool_preview_items.append(item)

    def remove_tool_preview(self) -> None:
        window = self.window
        if window.tool_preview_item is not None:
            window.scene.removeItem(window.tool_preview_item)
            window.tool_preview_item = None
        for item in window.tool_preview_items:
            window.scene.removeItem(item)
        window.tool_preview_items.clear()

    def create_freetext_annotation_at_point(self, point: tuple[float, float]) -> int | None:
        window = self.window
        text, ok = QInputDialog.getMultiLineText(window, "Add FreeText", "Text:")
        if not ok or not text.strip():
            self.show_page_status()
            return None

        rect = window.default_freetext_rect(point, text.strip(), window.default_freetext_font_size)
        return self.create_freetext_annotation(rect, text.strip())

    def create_freetext_annotation(self, rect: fitz.Rect, text: str) -> int:
        window = self.window
        writer = PdfAnnotationWriter(window.doc)
        annot = writer.add_freetext_annotation(
            window.current_page(),
            rect,
            text,
            window.default_freetext_font_size,
            (1, 0, 0),
            window.use_foxit_freetext,
        )
        window.mark_dirty()
        if text_affects_anchor_data("", text):
            window.mark_navigation_anchors_dirty()
        window.update_freetext_snapshot_text(window.page_index, annot.xref, text)
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref, center_on=False)
        return annot.xref

    def create_square_annotation(self, rect: fitz.Rect) -> int:
        window = self.window
        annot = PdfAnnotationWriter(window.doc).add_square_annotation(window.current_page(), rect)
        window.mark_dirty()
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref, center_on=False)
        return annot.xref

    def create_arrow_annotation(self, start: tuple[float, float], end: tuple[float, float]) -> int:
        window = self.window
        annot = PdfAnnotationWriter(window.doc).add_arrow_annotation(window.current_page(), start, end)
        window.mark_dirty()
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref, center_on=False)
        return annot.xref

    def create_highlight_annotation_from_text_flow(
        self, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> int | None:
        window = self.window
        page = window.current_page()
        rects = window.highlight_rects_from_text_flow(page, start_point, end_point)
        if not rects:
            window.statusBar().showMessage("No text found in highlight area.")
            return None

        annot = PdfAnnotationWriter(window.doc).add_highlight_annotation(
            page,
            rects,
            window.default_highlight_color,
            window.default_highlight_opacity,
        )
        window.mark_dirty()
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref, center_on=False)
        return annot.xref

    def add_typewriter(self) -> None:
        self.begin_add_tool("freetext")

    def add_rectangle(self) -> None:
        self.begin_add_tool("square")

    def add_highlight(self) -> None:
        self.begin_add_tool("highlight")

    def add_arrow(self) -> None:
        self.begin_add_tool("arrow")

    def select_annotation(self, annotation_id: str | None, center_on: bool = False) -> None:
        window = self.window
        is_active = window.reading_workspace.active is window
        if annotation_id is not None and annotation_id not in window.annotation_model_map:
            return

        window.selected_annotation_id = annotation_id
        if window.active_session_index is not None and 0 <= window.active_session_index < len(window.sessions):
            window.sessions[window.active_session_index].selected_annotation_id = annotation_id
        window.clear_selection_items()
        window.sync_scene_selection()
        window.sync_table_selection()

        if annotation_id is None:
            self.show_page_status()
            window.update_actions()
            if is_active and window.navigation_widget is not None:
                window.navigation_widget.set_anchor_insert_enabled(False)
            if not window.applying_property_change:
                window.refresh_properties_panel()
            return

        if annotation_id not in window.annotation_model_map:
            window.selected_annotation_id = None
            self.show_page_status()
            window.update_actions()
            if not window.applying_property_change:
                window.refresh_properties_panel()
            return

        model = window.annotation_model_map[annotation_id]
        if model.is_supported:
            self.draw_selection_for_model(model)
            if center_on:
                self.center_on_annotation(model)

        if not is_active:
            return

        summary = model.text.strip().replace("\n", " ")
        if len(summary) > 40:
            summary = summary[:40] + "..."
        hint = "Drag to move. Use Save to persist." if window.is_draggable_model(model) else "Highlight cannot be moved."
        if summary:
            window.statusBar().showMessage(f"Selected: {model.pdf_type} xref={model.xref} | {summary} | {hint}")
        else:
            window.statusBar().showMessage(f"Selected: {model.pdf_type} xref={model.xref} | {hint}")
        window.update_actions()
        if window.navigation_widget is not None:
            window.navigation_widget.set_anchor_insert_enabled(window.can_insert_anchor_reference())
        if not window.applying_property_change:
            window.refresh_properties_panel()

    def on_annotations_table_selection_changed(self) -> None:
        window = self.window
        if window.updating_table_selection or window.annotations_table is None:
            return

        row = window.annotations_table.selected_row()
        if row is None:
            self.select_annotation(None)
            return

        if row < 0 or row >= len(window.current_annotations):
            self.select_annotation(None)
            return

        model = window.current_annotations[row]
        if not model.is_supported:
            self.select_annotation(None)
            window.statusBar().showMessage(f"Unsupported: {model.pdf_type} xref={model.xref}")
            return

        if getattr(window, "view_mode", "single") == "continuous":
            window.clear_selection_items()
            window.annotation_model_map[model.id] = model
            window.canvas_controller.scroll_continuous_rect_into_view(model.page_index, model.rect)
            window.annotation_model_map[model.id] = model
            self.select_annotation(model.id, center_on=False)
            return

        self.select_annotation(model.id, center_on=True)

    def delete_selected_annotation(self) -> None:
        window = self.window
        if window.doc is None or window.selected_annotation_id is None:
            return

        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None or not model.is_supported:
            return

        summary = model.text.strip().replace("\n", " ")
        if len(summary) > 60:
            summary = summary[:60] + "..."
        message = f"Delete selected annotation?\n\n{model.pdf_type} xref={model.xref}"
        if summary:
            message += f"\n{summary}"

        if not main_window_dialogs.confirm_delete_annotation(window, message):
            return

        try:
            page_index = model.page_index if getattr(window, "view_mode", "single") == "continuous" else window.page_index
            page = window.doc[page_index]
            annot = self.find_page_annotation_by_xref(page, model.xref)
            if annot is None:
                main_window_dialogs.show_warning(
                    window,
                    "Delete Annotation",
                    "The selected annotation was not found on this page.",
                )
                self.refresh_after_delete(page_index)
                return

            self.record_delete_undo(model)
            PdfAnnotationWriter(window.doc).delete_annotation(page, model.xref)
            if model.app_type == "freetext":
                if text_affects_anchor_data(model.text, ""):
                    window.mark_navigation_anchors_dirty()
                window.remove_freetext_snapshot_record(page_index, model.xref)
            window.mark_dirty()
            window.selected_annotation_id = None
            self.refresh_after_delete(page_index)
            window.statusBar().showMessage(f"Deleted annotation xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            window.show_error("Delete annotation failed", exc)

    def refresh_after_delete(self, page_index: int) -> None:
        window = self.window
        if getattr(window, "view_mode", "single") == "continuous":
            window.clear_selection_items()
            window.canvas_controller.invalidate_continuous_annotation_cache(page_index)
            if page_index in window.continuous_page_items:
                window.canvas_controller.unload_continuous_page(page_index)
            window.canvas_controller.ensure_continuous_page_rendered(page_index)
            window.current_annotations = window.canvas_controller.continuous_load_page_annotations(window.page_index)
            window.refresh_annotations_table()
            window.refresh_properties_panel()
            window.update_actions()
            window.save_active_session_state()
            return
        window.refresh_annotation_overlay(preserve_selection=False)

    def edit_selected_annotation(self) -> None:
        self.window.show_annotation_properties()

    def update_freetext_annotation(
        self, model: AnnotationModel, text: str, font_size: int, color: tuple[float, float, float] = (1, 0, 0)
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        self.update_freetext_annotation_on_page(window.page_index, model, text, font_size, color)

    def update_freetext_annotation_on_page(
        self,
        page_index: int,
        model: AnnotationModel,
        text: str,
        font_size: int,
        color: tuple[float, float, float] = (1, 0, 0),
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_freetext_annotation_clean_appearance(
            window.doc[page_index],
            model,
            text,
            font_size,
            color,
            window.estimated_freetext_size,
        )
        if text_affects_anchor_data(model.text, text):
            window.mark_navigation_anchors_dirty()
        window.update_freetext_snapshot_text(page_index, model.xref, text)

    def update_freetext_annotation_clean_appearance_on_page(
        self,
        page_index: int,
        model: AnnotationModel,
        text: str,
        font_size: int,
        color: tuple[float, float, float] = (1, 0, 0),
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_freetext_annotation_clean_appearance(
            window.doc[page_index],
            model,
            text,
            font_size,
            color,
            window.estimated_freetext_size,
        )
        if text_affects_anchor_data(model.text, text):
            window.mark_navigation_anchors_dirty()
        window.update_freetext_snapshot_text(page_index, model.xref, text)

    def update_freetext_annotation_clean_appearance_in_rect_on_page(
        self,
        page_index: int,
        model: AnnotationModel,
        rect: fitz.Rect,
        text: str,
        font_size: int,
        color: tuple[float, float, float] = (1, 0, 0),
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_freetext_annotation_clean_appearance_in_rect(
            window.doc[page_index],
            model,
            rect,
            text,
            font_size,
            color,
        )
        if text_affects_anchor_data(model.text, text):
            window.mark_navigation_anchors_dirty()
        window.update_freetext_snapshot_text(page_index, model.xref, text)

    def normalize_freetext_annotation(
        self, annot: fitz.Annot, font_size: int, color: tuple[float, float, float]
    ) -> None:
        window = self.window
        if window.doc is None:
            return
        PdfAnnotationWriter(window.doc).normalize_freetext_annotation(annot, font_size, color)

    def freetext_default_style(self, font_size: int, color: tuple[float, float, float]) -> str:
        return PdfAnnotationWriter.freetext_default_style(font_size, color)

    def update_highlight_annotation(
        self, model: AnnotationModel, color: tuple[float, float, float], opacity: float
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_highlight_annotation(window.current_page(), model, color, opacity)

    def update_highlight_annotation_on_page(
        self, page_index: int, model: AnnotationModel, color: tuple[float, float, float], opacity: float
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_highlight_annotation(window.doc[page_index], model, color, opacity)

    def update_stroked_annotation(self, model: AnnotationModel, color: tuple[float, float, float], width: int) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_stroked_annotation(window.current_page(), model, color, width)

    def update_stroked_annotation_on_page(
        self, page_index: int, model: AnnotationModel, color: tuple[float, float, float], width: int
    ) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_stroked_annotation(window.doc[page_index], model, color, width)

    def on_highlight_property_change(self, color: tuple[float, float, float], opacity: float) -> None:
        self.apply_property_change(
            lambda selected: self.update_highlight_annotation_on_page(selected.page_index, selected, color, opacity),
            self.property_status_message("Edited Highlight"),
        )

    def on_highlight_default_change(self, color: tuple[float, float, float], opacity: float) -> None:
        window = self.window
        window.default_highlight_color = color
        window.default_highlight_opacity = opacity
        window.save_app_settings()

    def on_freetext_property_change(self, text: str, font_size: int, color: tuple[float, float, float]) -> None:
        self.apply_property_change(
            lambda selected: self.update_freetext_annotation_on_page(selected.page_index, selected, text, font_size, color),
            self.property_status_message("Edited FreeText"),
        )

    def on_stroked_property_change(self, color: tuple[float, float, float], width: int) -> None:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        label = f"Edited {model.pdf_type}" if model is not None else "Edited annotation"
        self.apply_property_change(
            lambda selected: self.update_stroked_annotation_on_page(selected.page_index, selected, color, width),
            self.property_status_message(label),
        )

    def property_status_message(self, prefix: str) -> str:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if model is None:
            return f"{prefix}. Use Save to persist."
        return f"{prefix} xref={model.xref}. Use Save to persist."

    def color_name_for_tuple(self, color: tuple | None) -> str:
        if not color:
            return "Red"
        best_name = "Red"
        best_distance = float("inf")
        for name, candidate in ANNOTATION_COLORS.items():
            distance = sum((float(color[index]) - candidate[index]) ** 2 for index in range(3))
            if distance < best_distance:
                best_name = name
                best_distance = distance
        return best_name

    def draw_selection_for_model(self, model: AnnotationModel) -> None:
        window = self.window
        if getattr(window, "view_mode", "single") == "continuous":
            layout = window.canvas_controller.continuous_layout_for_page(model.page_index)
            scene_offset = layout.scene_rect.topLeft() if layout is not None else QPointF(0, 0)
            read_only = model.app_type not in {"freetext", "square", "arrow"}
            renderer = AnnotationSelectionRenderer(
                window.scene,
                window.zoom,
                scene_offset=scene_offset,
                read_only=read_only,
            )
        else:
            renderer = AnnotationSelectionRenderer(window.scene, window.zoom)
        window.selection_items.extend(renderer.draw(model))

    def center_on_annotation(self, model: AnnotationModel) -> None:
        window = self.window
        if getattr(window, "view_mode", "single") == "continuous":
            if model.app_type == "arrow":
                start = model.line_start or (model.rect.x0, model.rect.y0)
                end = model.line_end or (model.rect.x1, model.rect.y1)
                start_point = window.canvas_controller.continuous_scene_point_for_pdf_point(model.page_index, start)
                end_point = window.canvas_controller.continuous_scene_point_for_pdf_point(model.page_index, end)
                window.view.centerOn((start_point + end_point) / 2)
                return
            window.view.centerOn(
                window.canvas_controller.continuous_scene_rect_for_pdf_rect(model.page_index, model.rect).center()
            )
            return
        if model.app_type == "arrow":
            start, end = window.arrow_points(model)
            window.view.centerOn((start + end) / 2)
            return
        window.view.centerOn(window.scene_rect(model.rect).center())

    def show_page_status(self) -> None:
        window = self.window
        if window.reading_workspace.active is not window:
            return
        if window.doc is None:
            window.statusBar().showMessage("No PDF open")
            return

        supported_count = sum(1 for annot in window.current_annotations if annot.is_supported)
        unsupported_count = len(window.current_annotations) - supported_count
        window.statusBar().showMessage(
            f"Page {window.page_index + 1}/{len(window.doc)} | "
            f"Annotations: {len(window.current_annotations)} | "
            f"Supported: {supported_count} | Unsupported: {unsupported_count}"
        )

    def load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        window = self.window
        if window.annotation_repo is None:
            return []
        warning_start = len(window.annotation_repo.warnings)
        models = window.annotation_repo.load_page_annotations(page_index)
        warnings = [warning.format() for warning in window.annotation_repo.warnings[warning_start:]]
        self.log_annotation_read_warnings("Current page annotation read warnings", warnings)
        return models

    def log_annotation_read_warnings(self, title: str, warnings: list, max_details: int = 30) -> None:
        window = self.window
        if not warnings:
            return
        window.log_debug(f"{title}: skipped/problematic={len(warnings)}")
        for warning in warnings[:max_details]:
            window.log_debug(f"  {warning}")
        if len(warnings) > max_details:
            window.log_debug(f"  ... {len(warnings) - max_details} more annotation read warnings")

    def refresh_annotations_table(self) -> None:
        window = self.window
        if window.annotations_table is None:
            return

        table = window.annotations_table
        window.updating_table_selection = True
        try:
            table.set_annotations(window.current_annotations)
        finally:
            window.updating_table_selection = False

        window.sync_table_selection()

    def rect_text(self, rect: fitz.Rect) -> str:
        return f"({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f})"

    def annotation_note(self, model: AnnotationModel) -> str:
        if model.app_type == "arrow":
            return f"start={model.line_start}, end={model.line_end}, LE={model.line_ending}"
        if not model.is_supported:
            return "unsupported annotation type"
        return ""

    def apply_property_change(self, callback, status: str) -> None:
        window = self.window
        if window.applying_property_change:
            return
        if window.doc is None or window.selected_annotation_id is None:
            return

        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None or not model.is_supported:
            return

        try:
            window.applying_property_change = True
            callback(model)
            window.mark_dirty()
            if getattr(window, "view_mode", "single") == "continuous":
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            else:
                window.refresh_annotation_overlay(preserve_selection=True)
            window.statusBar().showMessage(status)
        except Exception as exc:
            if getattr(window, "view_mode", "single") == "continuous" and model is not None:
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            else:
                window.refresh_annotation_overlay(preserve_selection=True)
            window.show_error("Edit annotation failed", exc)
        finally:
            window.applying_property_change = False

    def show_annotation_context_menu(self, scene_pos, screen_pos) -> None:
        window = self.window
        window.clear_scene_drag_state()
        hit = self.annotation_hit_at_scene_pos(scene_pos)
        if hit is None:
            return

        annotation_id, _item_role = hit
        model = window.annotation_model_map.get(annotation_id)
        if model is None or not model.is_supported:
            return

        self.select_annotation(annotation_id)
        menu = QMenu(window)
        reference_actions = {}
        property_actions = {}
        if model.app_type == "freetext":
            references = list(dict.fromkeys(references_in_text(model.text)))
            if references:
                references_menu = menu.addMenu("Go to Ref")
                for reference in references:
                    reference_actions[references_menu.addAction(reference)] = reference
            auto_fit_action = menu.addAction("Auto Fit")
            self.add_context_freetext_font_size_menu(menu, model, property_actions)
            if is_anchor_text(model.text):
                serial_action = menu.addAction("Remove Serial Number")
            else:
                serial_action = menu.addAction("Add Serial Number")
        else:
            serial_action = None
            auto_fit_action = None
        if model.app_type in {"square", "arrow"}:
            self.add_context_color_menu(menu, ANNOTATION_COLORS, property_actions)
            self.add_context_line_width_menu(menu, model, property_actions)
        elif model.app_type == "highlight":
            self.add_context_color_menu(menu, QUICK_HIGHLIGHT_COLORS, property_actions)
        delete_action = menu.addAction("Delete")
        selected_action = menu.exec(screen_pos)
        if selected_action is None:
            return
        if selected_action in reference_actions:
            window.go_to_anchor_reference_by_name(reference_actions[selected_action])
            return
        if selected_action in property_actions:
            property_actions[selected_action]()
            return
        if auto_fit_action is not None and selected_action == auto_fit_action:
            self.auto_fit_selected_freetext()
            return
        if serial_action is not None and selected_action == serial_action:
            if model.app_type == "freetext" and is_anchor_text(model.text):
                window.remove_serial_number_from_selected_freetext()
            else:
                window.add_serial_number_to_selected_freetext()
            return
        if selected_action == delete_action:
            self.delete_selected_annotation()

    def add_context_freetext_font_size_menu(self, menu: QMenu, model: AnnotationModel, actions: dict) -> None:
        font_menu = menu.addMenu("Font Size")
        for font_size in (7, 9, 10, 12, 14, 16, 18):
            action = font_menu.addAction(str(font_size))
            action.setCheckable(True)
            action.setChecked(round(model.font_size or self.window.default_freetext_font_size) == font_size)
            actions[action] = lambda selected_size=font_size: self.apply_context_freetext_font_size_change(
                selected_size
            )

    def add_context_color_menu(self, menu: QMenu, colors: dict[str, tuple], actions: dict) -> None:
        color_menu = menu.addMenu("Color")
        for name, color in colors.items():
            action = color_menu.addAction(name)
            actions[action] = lambda selected_color=color: self.apply_context_color_change(selected_color)

    def add_context_line_width_menu(self, menu: QMenu, model: AnnotationModel, actions: dict) -> None:
        width_menu = menu.addMenu("Line Width")
        for width in (1, 2, 3, 4, 5):
            action = width_menu.addAction(str(width))
            action.setCheckable(True)
            action.setChecked(round(model.border_width or 1) == width)
            actions[action] = lambda selected_width=width: self.apply_context_line_width_change(selected_width)

    def apply_context_color_change(self, color: tuple[float, float, float]) -> None:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if model is None:
            return
        if model.app_type == "highlight":
            self.record_property_undo("Edit Highlight Color", model)
            self.on_highlight_property_change(color, model.opacity if model.opacity is not None else window.default_highlight_opacity)
        elif model.app_type in {"square", "arrow"}:
            self.record_property_undo(f"Edit {model.pdf_type} Color", model)
            self.on_stroked_property_change(color, int(round(model.border_width or 1)))

    def apply_context_line_width_change(self, width: int) -> None:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if model is None or model.app_type not in {"square", "arrow"}:
            return
        self.record_property_undo(f"Edit {model.pdf_type} Line Width", model)
        self.on_stroked_property_change(model.color or (1, 0, 0), width)

    def apply_context_freetext_font_size_change(self, font_size: int) -> None:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if model is None or model.app_type != "freetext":
            return
        self.record_property_undo("Edit FreeText Font Size", model)
        self.on_freetext_property_change(model.text, font_size, model.color or (1, 0, 0))

    def auto_fit_selected_freetext(self) -> None:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if window.doc is None or model is None or model.app_type != "freetext":
            return
        try:
            self.record_geometry_undo("Auto Fit FreeText", model)
            page = window.doc[model.page_index if getattr(window, "view_mode", "single") == "continuous" else window.page_index]
            PdfAnnotationWriter(window.doc).auto_fit_freetext_annotation(
                page,
                model,
                window.default_freetext_font_size,
            )
            window.mark_dirty()
            if getattr(window, "view_mode", "single") == "continuous":
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            else:
                window.refresh_annotation_overlay(preserve_selection=True)
            window.statusBar().showMessage(f"Auto fit FreeText xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            if getattr(window, "view_mode", "single") == "continuous" and model is not None:
                self.refresh_continuous_page_after_geometry_edit(model.page_index, model.xref)
            else:
                window.refresh_annotation_overlay(preserve_selection=True)
            window.show_error("Auto fit FreeText failed", exc)

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        window = self.window
        if window.annotation_repo is None:
            return None
        return window.annotation_repo.find_page_annotation_by_xref(page, xref)

    def select_annotation_by_xref(self, xref: int, render_page: bool = True) -> bool:
        window = self.window
        if render_page:
            window.render_page()
        for model in window.current_annotations:
            if model.xref == xref:
                self.select_annotation(model.id, center_on=True)
                return True
        return False

    def refresh_overlay_and_select_annotation_by_xref(self, xref: int, center_on: bool = True) -> bool:
        window = self.window
        window.refresh_annotation_overlay(preserve_selection=False)
        return self.select_current_overlay_annotation_by_xref(xref, center_on)

    def select_current_overlay_annotation_by_xref(self, xref: int, center_on: bool = True) -> bool:
        window = self.window
        for model in window.current_annotations:
            if model.xref == xref:
                self.select_annotation(model.id, center_on=center_on)
                return True
        return False
