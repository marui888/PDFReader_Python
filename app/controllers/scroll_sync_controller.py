from __future__ import annotations

from contextlib import contextmanager
from math import floor

from PySide6.QtCore import QPointF


class ScrollSyncController:
    """Map page-relative positions, retaining the offset chosen by the reader."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.enabled = False
        self.hold_depth = 0
        self.offset = 0.0

    @contextmanager
    def hold(self):
        self.hold_depth += 1
        try:
            yield
        finally:
            self.hold_depth -= 1

    def available(self):
        return self.workspace.side_by_side and all(
            pane.doc is not None and not pane.doc.is_closed and pane.view_mode == "continuous"
            for pane in self.workspace.panes
        )

    @staticmethod
    def position(pane):
        layouts = pane.continuous_page_layouts
        if not layouts:
            return float(pane.page_index)
        y = pane.view.mapToScene(pane.view.viewport().rect().center()).y()
        for index, layout in enumerate(layouts):
            end = layouts[index + 1].y_top if index + 1 < len(layouts) else layout.y_bottom
            if y < end or index == len(layouts) - 1:
                fraction = max(0.0, min(1.0, (y - layout.y_top) / max(1.0, end - layout.y_top)))
                return index + fraction
        return float(len(layouts))

    @staticmethod
    def set_position(pane, position):
        layouts = pane.continuous_page_layouts
        if not layouts:
            return
        position = max(0.0, min(float(len(layouts)), position))
        index = min(len(layouts) - 1, floor(position))
        layout = layouts[index]
        end = layouts[index + 1].y_top if index + 1 < len(layouts) else layout.y_bottom
        y = layout.y_top + (position - index) * (end - layout.y_top)
        center = pane.view.mapToScene(pane.view.viewport().rect().center())
        pane.view.centerOn(QPointF(center.x(), y))
        # Dispatch directly to this pane, independent of MainWindow's UI routing.
        pane.canvas_controller.on_view_scrolled()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled and self.available())
        if self.enabled:
            left, right = self.workspace.panes
            with self.hold():
                for pane in (left, right):
                    pane.canvas_controller.build_continuous_page_layouts()
                self.offset = self.position(right) - self.position(left)
        self.workspace.update_actions()

    def scrolled(self, source):
        if (not self.workspace.ready or self.hold_depth or not self.enabled
                or source.continuous_suppress_scroll_sync):
            return
        if not self.available():
            self.set_enabled(False)
            return
        left, right = self.workspace.panes
        if source is not left and source is not right:
            return
        target = right if source is left else left
        position = self.position(source) + (self.offset if source is left else -self.offset)
        with self.hold():
            self.set_position(target, position)
