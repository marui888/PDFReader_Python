from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QInputDialog, QMenu

from app.main_window import dialogs as main_window_dialogs
from app.canvas.annotation_interaction import AnnotationInteractionController
from app.canvas.annotation_selection import AnnotationSelectionRenderer
from app.anchors import is_anchor_text, references_in_text
from app.models.annotation_model import ANNOTATION_COLORS, AnnotationModel
from app.services.pdf_annotation_writer import PdfAnnotationWriter
from app.models.undo import UndoAction


class AnnotationController:
    def __init__(self, window) -> None:
        self.window = window

    def on_scene_selection_changed(self) -> None:
        window = self.window
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
            priority = self.annotation_hit_priority(model, str(item_role) if item_role else None)
            candidates.append((priority, len(candidates), str(annotation_id), str(item_role) if item_role else None))
        if not candidates:
            return None
        candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
        _priority, _order, annotation_id, item_role = candidates[0]
        return annotation_id, item_role

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

    def undo_last_action(self) -> None:
        window = self.window
        if window.doc is None or window.undo_action is None:
            return

        action = window.undo_action
        try:
            if action.operation == "delete":
                restored_xref = self.restore_deleted_annotation(action)
            else:
                restored_xref = window.restore_undo_action(action)
            window.undo_action = None
            window.page_index = max(0, min(action.page_index, len(window.doc) - 1))
            window.mark_dirty()
            window.refresh_annotation_overlay(preserve_selection=True)
            if restored_xref is not None:
                self.select_current_overlay_annotation_by_xref(restored_xref)
            window.statusBar().showMessage(f"Undid {action.label} xref={action.xref}. Use Save to persist.")
            window.update_actions()
        except Exception as exc:
            window.show_error("Undo failed", exc)

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

    def begin_default_text_selection(self, scene_pos) -> bool:
        window = self.window
        if window.doc is None or not self.is_text_at_scene_pos(scene_pos):
            return False
        self.clear_text_selection()
        self.select_annotation(None)
        window.text_selection_active = True
        window.text_selection_start_scene_pos = scene_pos
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
        if window.doc is None or window.active_tool is not None:
            return
        if self.annotation_hit_at_scene_pos(scene_pos) is not None:
            return
        cursor = Qt.CursorShape.IBeamCursor if self.is_text_at_scene_pos(scene_pos) else Qt.CursorShape.ArrowCursor
        window.view.viewport().setCursor(cursor)

    def update_default_text_selection(self, scene_pos) -> None:
        window = self.window
        if window.doc is None or window.text_selection_start_scene_pos is None or scene_pos is None:
            return

        for item in window.text_selection_items:
            try:
                window.scene.removeItem(item)
            except RuntimeError:
                pass
        window.text_selection_items.clear()

        start_pdf = window.pdf_point_from_scene_point(window.text_selection_start_scene_pos)
        end_pdf = window.pdf_point_from_scene_point(scene_pos)
        rects = window.highlight_rects_from_text_flow(window.current_page(), start_pdf, end_pdf)
        window.text_selection_rects = [fitz.Rect(rect) for rect in rects]
        brush = QBrush(QColor(120, 120, 120, 95))
        for rect in rects:
            item = window.scene.addRect(window.scene_rect(rect), QPen(Qt.PenStyle.NoPen), brush)
            item.setZValue(28)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            window.text_selection_items.append(item)

    def apply_quick_highlight_color(self, color: tuple[float, float, float], opacity: float) -> None:
        window = self.window
        opacity = max(0.0, min(1.0, float(opacity)))
        if window.doc is not None and window.text_selection_rects:
            try:
                annot = PdfAnnotationWriter(window.doc).add_highlight_annotation(
                    window.current_page(),
                    [fitz.Rect(rect) for rect in window.text_selection_rects],
                    color,
                    opacity,
                )
                window.mark_dirty()
                self.clear_text_selection()
                self.refresh_overlay_and_select_annotation_by_xref(annot.xref)
                window.statusBar().showMessage(f"Added Highlight xref={annot.xref}. Use Save to persist.")
            except Exception as exc:
                window.show_error("Add Highlight failed", exc)
            return

        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        if window.doc is not None and model is not None and model.app_type == "highlight":
            try:
                self.update_highlight_annotation(model, color, opacity)
                window.mark_dirty()
                window.refresh_annotation_overlay(preserve_selection=True)
                window.statusBar().showMessage(f"Edited Highlight xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                window.refresh_annotation_overlay(preserve_selection=True)
                window.show_error("Edit Highlight failed", exc)
            return

        window.default_highlight_color = color
        window.default_highlight_opacity = opacity
        window.save_app_settings()
        window.statusBar().showMessage("Default highlight color updated.")

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

    def set_active_tool(self, tool: str | None) -> None:
        window = self.window
        self.remove_tool_preview()
        if tool is not None:
            self.clear_text_selection()
        window.active_tool = tool
        window.tool_start_scene_pos = None
        window.add_typewriter_action.setChecked(tool == "freetext")
        window.add_rectangle_action.setChecked(tool == "square")
        window.add_highlight_action.setChecked(tool == "highlight")
        window.add_arrow_action.setChecked(tool == "arrow")

        if tool is None:
            window.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        window.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        if tool == "freetext":
            window.statusBar().showMessage("Click on the page to add FreeText. Press Esc to cancel.")
        elif tool == "highlight":
            window.statusBar().showMessage("Drag over text to add Highlight. Press Esc to cancel.")
        else:
            window.statusBar().showMessage(f"Drag on the page to add {tool}. Press Esc to cancel.")

    def cancel_add_tool(self) -> None:
        self.remove_tool_preview()
        self.set_active_tool(None)

    def on_tool_mouse_press(self, scene_pos) -> bool:
        window = self.window
        if window.active_tool is None or window.doc is None:
            return False
        if window.is_inline_freetext_editor_hit(scene_pos):
            return False
        if window.confirm_inline_freetext_editor_from_canvas_click(scene_pos):
            return True
        if self.annotation_hit_at_scene_pos(scene_pos) is not None:
            return False
        if window.selected_annotation_id is not None and window.active_tool in {"freetext", "square", "arrow"}:
            selected_model = window.annotation_model_map.get(window.selected_annotation_id)
            if selected_model is not None and selected_model.app_type == window.active_tool:
                self.select_annotation(None)
                return True
        window.clear_selection_items()
        start = window.clamp_scene_pos_to_page(scene_pos)
        if window.active_tool == "freetext":
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
                    window.statusBar().showMessage(f"{tool} area is too small.")
                    return True
                xref = self.create_square_annotation(rect)
                self.record_add_undo("Add Square", window.page_index, xref)
            elif tool == "highlight":
                start_pdf = window.pdf_point_from_scene_point(start)
                end_pdf = window.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 1 and abs(end_pdf[1] - start_pdf[1]) < 1:
                    window.statusBar().showMessage("Highlight area is too small.")
                    return True
                xref = self.create_highlight_annotation_from_text_flow(start_pdf, end_pdf)
                if xref is not None:
                    self.record_add_undo("Add Highlight", window.page_index, xref)
            elif tool == "arrow":
                start_pdf = window.pdf_point_from_scene_point(start)
                end_pdf = window.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 10 and abs(end_pdf[1] - start_pdf[1]) < 10:
                    window.statusBar().showMessage("Arrow is too short.")
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

        brush = QBrush(QColor(255, 235, 59, 85))
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
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref)
        return annot.xref

    def create_square_annotation(self, rect: fitz.Rect) -> int:
        window = self.window
        annot = PdfAnnotationWriter(window.doc).add_square_annotation(window.current_page(), rect)
        window.mark_dirty()
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref)
        return annot.xref

    def create_arrow_annotation(self, start: tuple[float, float], end: tuple[float, float]) -> int:
        window = self.window
        annot = PdfAnnotationWriter(window.doc).add_arrow_annotation(window.current_page(), start, end)
        window.mark_dirty()
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref)
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
        self.refresh_overlay_and_select_annotation_by_xref(annot.xref)
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
            if window.navigation_widget is not None:
                window.navigation_widget.set_anchor_insert_enabled(False)
            if not window.applying_property_change:
                window.refresh_properties_panel()
            return

        model = window.annotation_model_map[annotation_id]
        if model.is_supported:
            self.draw_selection_for_model(model)
            if center_on:
                self.center_on_annotation(model)

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
            page = window.current_page()
            annot = self.find_page_annotation_by_xref(page, model.xref)
            if annot is None:
                main_window_dialogs.show_warning(
                    window,
                    "Delete Annotation",
                    "The selected annotation was not found on this page.",
                )
                window.refresh_annotation_overlay(preserve_selection=False)
                return

            self.record_delete_undo(model)
            PdfAnnotationWriter(window.doc).delete_annotation(page, model.xref)
            window.mark_dirty()
            window.selected_annotation_id = None
            window.refresh_annotation_overlay(preserve_selection=False)
            window.statusBar().showMessage(f"Deleted annotation xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            window.show_error("Delete annotation failed", exc)

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

    def update_stroked_annotation(self, model: AnnotationModel, color: tuple[float, float, float], width: int) -> None:
        window = self.window
        if window.doc is None:
            raise RuntimeError("No PDF is open.")
        PdfAnnotationWriter(window.doc).update_stroked_annotation(window.current_page(), model, color, width)

    def on_highlight_property_change(self, color: tuple[float, float, float], opacity: float) -> None:
        self.apply_property_change(
            lambda selected: self.update_highlight_annotation(selected, color, opacity),
            self.property_status_message("Edited Highlight"),
        )

    def on_highlight_default_change(self, color: tuple[float, float, float], opacity: float) -> None:
        window = self.window
        window.default_highlight_color = color
        window.default_highlight_opacity = opacity
        window.save_app_settings()

    def on_freetext_property_change(self, text: str, font_size: int, color: tuple[float, float, float]) -> None:
        self.apply_property_change(
            lambda selected: self.update_freetext_annotation(selected, text, font_size, color),
            self.property_status_message("Edited FreeText"),
        )

    def on_stroked_property_change(self, color: tuple[float, float, float], width: int) -> None:
        window = self.window
        model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
        label = f"Edited {model.pdf_type}" if model is not None else "Edited annotation"
        self.apply_property_change(
            lambda selected: self.update_stroked_annotation(selected, color, width),
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
        renderer = AnnotationSelectionRenderer(window.scene, window.zoom)
        window.selection_items.extend(renderer.draw(model))

    def center_on_annotation(self, model: AnnotationModel) -> None:
        window = self.window
        if model.app_type == "arrow":
            start, end = window.arrow_points(model)
            window.view.centerOn((start + end) / 2)
            return
        window.view.centerOn(window.scene_rect(model.rect).center())

    def show_page_status(self) -> None:
        window = self.window
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
            window.refresh_annotation_overlay(preserve_selection=True)
            window.statusBar().showMessage(status)
        except Exception as exc:
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
        if model.app_type == "freetext":
            references = list(dict.fromkeys(references_in_text(model.text)))
            if references:
                references_menu = menu.addMenu("Go to Ref")
                for reference in references:
                    reference_actions[references_menu.addAction(reference)] = reference
            if is_anchor_text(model.text):
                serial_action = menu.addAction("Remove Serial Number")
            else:
                serial_action = menu.addAction("Add Serial Number")
        else:
            serial_action = None
        delete_action = menu.addAction("Delete")
        selected_action = menu.exec(screen_pos)
        if selected_action in reference_actions:
            window.go_to_anchor_reference_by_name(reference_actions[selected_action])
            return
        if selected_action == serial_action:
            if model.app_type == "freetext" and is_anchor_text(model.text):
                window.remove_serial_number_from_selected_freetext()
            else:
                window.add_serial_number_to_selected_freetext()
            return
        if selected_action == delete_action:
            self.delete_selected_annotation()

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        window = self.window
        if window.annotation_repo is None:
            return None
        return window.annotation_repo.find_page_annotation_by_xref(page, xref)

    def select_annotation_by_xref(self, xref: int) -> bool:
        window = self.window
        window.render_page()
        for model in window.current_annotations:
            if model.xref == xref:
                self.select_annotation(model.id, center_on=True)
                return True
        return False

    def refresh_overlay_and_select_annotation_by_xref(self, xref: int) -> bool:
        window = self.window
        window.refresh_annotation_overlay(preserve_selection=False)
        return self.select_current_overlay_annotation_by_xref(xref)

    def select_current_overlay_annotation_by_xref(self, xref: int) -> bool:
        window = self.window
        for model in window.current_annotations:
            if model.xref == xref:
                self.select_annotation(model.id, center_on=True)
                return True
        return False
