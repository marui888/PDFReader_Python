from dataclasses import dataclass

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from app.models import DRAGGABLE_APP_TYPES, AnnotationModel


@dataclass
class AnnotationInteraction:
    kind: str
    dx_pdf: float
    dy_pdf: float
    handle: str = ""


class AnnotationInteractionController:
    def __init__(self, zoom: float) -> None:
        self.zoom = zoom

    def interaction_for_mouse_release(
        self,
        model: AnnotationModel,
        selection_items: list[QGraphicsItem],
        annotation_item_map: dict[str, list[QGraphicsItem]],
        preferred_kind: str | None = None,
    ) -> AnnotationInteraction | None:
        if preferred_kind == "move":
            if model.app_type not in DRAGGABLE_APP_TYPES:
                return None
            delta = self.annotation_drag_delta(model.id, annotation_item_map)
            if delta is None:
                return None
            return self.interaction_from_delta("move", delta)

        if preferred_kind == "resize":
            return self.rect_resize(model, selection_items)

        if preferred_kind == "arrow-endpoint":
            return self.arrow_endpoint_move(model, selection_items)

        arrow_endpoint = self.arrow_endpoint_move(model, selection_items)
        if arrow_endpoint is not None:
            return arrow_endpoint

        rect_resize = self.rect_resize(model, selection_items)
        if rect_resize is not None:
            return rect_resize

        if model.app_type not in DRAGGABLE_APP_TYPES:
            return None

        delta = self.annotation_drag_delta(model.id, annotation_item_map)
        if delta is None:
            return None
        return self.interaction_from_delta("move", delta)

    def rect_resize(
        self,
        model: AnnotationModel,
        selection_items: list[QGraphicsItem],
    ) -> AnnotationInteraction | None:
        if model.app_type not in {"square", "freetext"}:
            return None

        for item in selection_items:
            if item.data(2) != "resize-handle" or item.data(3) != model.id:
                continue
            delta = self.item_delta(item, 5)
            if self.is_small_delta(delta):
                continue
            return self.interaction_from_delta("resize", delta, str(item.data(4)))
        return None

    def arrow_endpoint_move(
        self,
        model: AnnotationModel,
        selection_items: list[QGraphicsItem],
    ) -> AnnotationInteraction | None:
        if model.app_type != "arrow":
            return None

        for item in selection_items:
            if item.data(2) != "arrow-endpoint-handle" or item.data(3) != model.id:
                continue
            delta = self.item_delta(item, 5)
            if self.is_small_delta(delta):
                continue
            return self.interaction_from_delta("arrow-endpoint", delta, str(item.data(4)))
        return None

    def annotation_drag_delta(
        self,
        annotation_id: str,
        annotation_item_map: dict[str, list[QGraphicsItem]],
    ) -> QPointF | None:
        for item in annotation_item_map.get(annotation_id, []):
            delta = self.item_delta(item, 1)
            if not self.is_small_delta(delta):
                return delta
        return None

    def item_delta(self, item: QGraphicsItem, start_data_key: int) -> QPointF:
        start_pos = item.data(start_data_key)
        if start_pos is None:
            start_pos = QPointF(0, 0)
        return item.pos() - start_pos

    def interaction_from_delta(self, kind: str, delta: QPointF, handle: str = "") -> AnnotationInteraction:
        return AnnotationInteraction(
            kind=kind,
            handle=handle,
            dx_pdf=delta.x() / self.zoom,
            dy_pdf=delta.y() / self.zoom,
        )

    def is_small_delta(self, delta: QPointF) -> bool:
        return abs(delta.x()) < 0.1 and abs(delta.y()) < 0.1
