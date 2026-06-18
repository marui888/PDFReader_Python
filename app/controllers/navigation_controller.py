from __future__ import annotations

from app.main_window import dialogs as main_window_dialogs
from app.main_window import docks as main_window_docks
from app.anchors import (
    add_serial_prefix,
    is_anchor_text,
    remove_serial_prefix,
    scan_document_anchor_data,
    serial_number,
)


class NavigationController:
    def __init__(self, window) -> None:
        self.window = window

    def show_navigation(self) -> None:
        window = self.window
        main_window_docks.show_navigation(window)
        self.refresh_navigation()

    def refresh_navigation(self) -> None:
        window = self.window
        if window.navigation_widget is None:
            return
        if window.doc is None:
            window.navigation_widget.clear_bookmarks()
            window.navigation_widget.clear_anchors()
            window.navigation_widget.set_anchor_insert_enabled(False)
            window.navigation_anchor_doc_id = None
            window.navigation_anchor_dirty = True
            return
        try:
            window.navigation_widget.set_bookmarks(window.doc.get_toc(simple=True))
            doc_id = id(window.doc)
            anchor_data = window.state.navigation.anchor_cache.get(doc_id)
            if window.navigation_anchor_dirty or anchor_data is None:
                anchor_data = scan_document_anchor_data(window.doc)
                window.state.navigation.anchor_cache[doc_id] = anchor_data
                window.log_debug(
                    f"Navigation anchors scanned: file={window.pdf_path} anchors={len(anchor_data.anchors)}"
                )
            if window.navigation_anchor_doc_id != doc_id or window.navigation_anchor_dirty:
                window.navigation_widget.set_anchors(anchor_data.anchors, anchor_data.references_by_anchor)
                window.navigation_anchor_doc_id = doc_id
                window.navigation_anchor_dirty = False
            window.navigation_widget.set_anchor_insert_enabled(self.can_insert_anchor_reference())
        except Exception as exc:
            window.navigation_widget.clear_bookmarks()
            window.navigation_widget.clear_anchors()
            window.navigation_anchor_doc_id = None
            window.navigation_anchor_dirty = True
            window.log_debug(f"Refresh navigation failed: {exc}")

    def go_to_bookmark(self, page_index: int) -> None:
        window = self.window
        if window.doc is None:
            return
        target_index = max(0, min(int(page_index), len(window.doc) - 1))
        if target_index == window.page_index:
            return
        window.record_view_location_before_navigation()
        window.page_index = target_index
        window.cancel_add_tool()
        window.render_page()
        window.update_current_recent_page()
        window.save_active_session_state()

    def go_to_anchor_reference_source(self, page_index: int, xref: int) -> None:
        self.go_to_anchor(page_index, xref)

    def go_to_anchor(self, page_index: int, xref: int) -> None:
        window = self.window
        if window.doc is None:
            return
        target_index = max(0, min(int(page_index), len(window.doc) - 1))
        window.record_view_location_before_navigation()
        window.page_index = target_index
        window.cancel_add_tool()
        window.render_page()
        window.select_annotation_by_xref(int(xref))
        window.update_current_recent_page()
        window.save_active_session_state()

    def can_insert_anchor_reference(self) -> bool:
        model = self.selected_freetext_model()
        return model is not None

    def insert_anchor_reference(self, reference: str) -> None:
        window = self.window
        if not self.can_insert_anchor_reference():
            main_window_dialogs.show_information(window, "Insert Ref", "Select a FreeText annotation first.")
            return
        inserted = False
        if window.properties_page is not None:
            inserted = window.properties_page.insert_text_into_freetext_editor(reference)
        if not inserted:
            window.show_annotation_properties()
            if window.properties_page is not None:
                inserted = window.properties_page.insert_text_into_freetext_editor(reference)
        if not inserted:
            main_window_dialogs.show_information(window, "Insert Ref", "FreeText properties editor is not available.")

    def selected_freetext_model(self):
        window = self.window
        if window.selected_annotation_id is None:
            return None
        model = window.annotation_model_map.get(window.selected_annotation_id)
        if model is None or model.app_type != "freetext":
            return None
        return model

    def add_serial_number_to_selected_freetext(self) -> None:
        window = self.window
        model = self.selected_freetext_model()
        if model is None:
            main_window_dialogs.show_information(window, "Add Serial Number", "Select a FreeText annotation first.")
            return
        if is_anchor_text(model.text):
            return

        number = self.next_serial_number_for_current_page()
        text = add_serial_prefix(model.text, number)
        self.update_selected_freetext_text(model, text, "Added serial number")

    def remove_serial_number_from_selected_freetext(self) -> None:
        window = self.window
        model = self.selected_freetext_model()
        if model is None:
            main_window_dialogs.show_information(window, "Remove Serial Number", "Select a FreeText annotation first.")
            return
        if not is_anchor_text(model.text):
            return

        text = remove_serial_prefix(model.text)
        if not text.strip():
            main_window_dialogs.show_warning(window, "Remove Serial Number", "FreeText annotation text cannot be empty.")
            return
        self.update_selected_freetext_text(model, text, "Removed serial number")

    def next_serial_number_for_current_page(self) -> int:
        window = self.window
        used = {
            value
            for value in (
                serial_number(model.text)
                for model in window.current_annotations
                if model.app_type == "freetext"
            )
            if value is not None
        }
        for number in range(1, 21):
            if number not in used:
                return number
        return max(used, default=20) + 1

    def update_selected_freetext_text(self, model, text: str, status: str) -> None:
        window = self.window
        try:
            window.update_freetext_annotation(
                model,
                text,
                int(round(model.font_size or window.default_freetext_font_size)),
                model.color or (1, 0, 0),
            )
            window.mark_dirty()
            window.render_page(preserve_selection=True, keep_view_position=True)
            window.statusBar().showMessage(f"{status} xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            window.render_page(preserve_selection=True, keep_view_position=True)
            window.show_error(f"{status} failed", exc)

    def go_to_anchor_reference_by_name(self, reference: str) -> None:
        window = self.window
        if window.doc is None:
            return
        try:
            anchor_data = scan_document_anchor_data(window.doc)
        except Exception as exc:
            main_window_dialogs.show_warning(window, "Go to Ref", f"Could not scan anchors:\n{exc}")
            return
        for anchor in anchor_data.anchors:
            if anchor.reference == reference:
                self.go_to_anchor(anchor.page_index, anchor.xref)
                return
        main_window_dialogs.show_warning(window, "Go to Ref", f"Anchor not found: {reference}")
