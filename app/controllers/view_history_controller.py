from __future__ import annotations

from pathlib import Path

from app.main_window import dialogs as main_window_dialogs
from app.models.view_history import ViewLocation


class ViewHistoryController:
    def __init__(self, window, max_history: int = 10) -> None:
        self.window = window
        self.back_stack: list[ViewLocation] = []
        self.forward_stack: list[ViewLocation] = []
        self.max_history = max_history
        self.restoring = False

    def can_go_back(self) -> bool:
        return bool(self.back_stack)

    def can_go_forward(self) -> bool:
        return bool(self.forward_stack)

    def current_location(self) -> ViewLocation | None:
        window = self.window
        if window.doc is None or window.pdf_path is None:
            return None

        selected_xref = None
        if window.selected_annotation_id is not None:
            model = window.annotation_model_map.get(window.selected_annotation_id)
            if model is not None:
                selected_xref = model.xref

        return ViewLocation(
            document_path=str(window.pdf_path),
            page_index=window.page_index,
            zoom=window.zoom,
            scroll_x=window.view.horizontalScrollBar().value(),
            scroll_y=window.view.verticalScrollBar().value(),
            selected_xref=selected_xref,
        )

    def record_before_navigation(self) -> None:
        if self.restoring:
            return

        location = self.current_location()
        if location is None:
            return
        if self.back_stack and self.back_stack[-1].is_near(location):
            return

        self.back_stack.append(location)
        self.back_stack = self.back_stack[-self.max_history :]
        self.forward_stack.clear()
        self.window.update_actions()

    def go_back(self) -> None:
        if not self.back_stack:
            return

        current = self.current_location()
        location = self.back_stack.pop()
        if current is not None:
            self.forward_stack.append(current)
            self.forward_stack = self.forward_stack[-self.max_history :]
        self.restore(location)

    def go_forward(self) -> None:
        if not self.forward_stack:
            return

        current = self.current_location()
        location = self.forward_stack.pop()
        if current is not None:
            self.back_stack.append(current)
            self.back_stack = self.back_stack[-self.max_history :]
        self.restore(location)

    def restore(self, location: ViewLocation) -> None:
        window = self.window
        self.restoring = True
        try:
            target_path = Path(location.document_path)
            existing_index = window.session_index_for_path(target_path)
            if existing_index is not None:
                window.set_active_session(existing_index, preserve_selection=False)
            else:
                if not target_path.exists() or not window.open_pdf_path(target_path, location.page_index):
                    main_window_dialogs.show_warning(window, "View History", f"Could not open:\n{target_path}")
                    return

            if window.doc is None:
                return

            window.zoom = location.zoom
            window.page_index = max(0, min(location.page_index, len(window.doc) - 1))
            window.cancel_add_tool()
            window.render_page(keep_view_position=True)
            window.view.horizontalScrollBar().setValue(location.scroll_x)
            window.view.verticalScrollBar().setValue(location.scroll_y)
            if location.selected_xref is not None:
                window.select_annotation_by_xref(location.selected_xref, render_page=False)
                window.view.horizontalScrollBar().setValue(location.scroll_x)
                window.view.verticalScrollBar().setValue(location.scroll_y)
            window.update_current_recent_page()
            window.save_active_session_state()
        finally:
            self.restoring = False
            window.update_actions()
