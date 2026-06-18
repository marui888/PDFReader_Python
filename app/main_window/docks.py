from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDockWidget, QPlainTextEdit, QSizePolicy, QTabWidget, QVBoxLayout

from app.widgets.annotation_list import AnnotationListWidget
from app.widgets.annotation_properties import AnnotationPropertiesWidget
from app.widgets.annotation_search import AnnotationSearchWidget
from app.widgets.navigation import NavigationWidget


def show_current_page_annotations(window) -> None:
    if window.annotations_dock is None:
        window.annotations_dock = QDockWidget("Annotations", window)
        window.annotations_dock.setMinimumWidth(40)
        window.annotations_dock.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        window.annotations_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        window.annotations_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        window.annotations_tabs = QTabWidget()
        window.annotations_tabs.setMinimumWidth(40)
        window.annotations_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        window.annotations_tabs.setTabPosition(QTabWidget.TabPosition.East)

        window.annotations_table = AnnotationListWidget(window.rect_text, window.annotation_note)
        window.annotations_table.itemSelectionChanged.connect(window.on_annotations_table_selection_changed)

        window.properties_page = AnnotationPropertiesWidget(
            window.on_highlight_property_change,
            window.on_highlight_default_change,
            window.on_freetext_property_change,
            window.on_stroked_property_change,
            window.go_to_anchor_reference_by_name,
            window.add_serial_number_to_selected_freetext,
            window.remove_serial_number_from_selected_freetext,
        )

        window.annotations_tabs.addTab(window.annotations_table, "Annotation List")
        window.annotations_tabs.addTab(window.properties_page, "Properties")
        window.annotations_dock.setWidget(window.annotations_tabs)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, window.annotations_dock)
        window.resizeDocks([window.annotations_dock], [220], Qt.Orientation.Horizontal)

    window.show_dock(window.annotations_dock)
    refresh_annotations_table(window)
    refresh_properties_panel(window)


def refresh_properties_panel(window) -> None:
    if window.properties_page is None:
        return

    model = window.annotation_model_map.get(window.selected_annotation_id) if window.selected_annotation_id else None
    window.properties_page.set_model(
        model,
        default_highlight_color=window.default_highlight_color,
        default_highlight_opacity=window.default_highlight_opacity,
        freetext_font_size_min=window.freetext_font_size_min,
        freetext_font_size_max=window.freetext_font_size_max,
        default_freetext_font_size=window.default_freetext_font_size,
    )


def refresh_annotations_table(window) -> None:
    window.annotation_controller.refresh_annotations_table()


def show_debug_log(window) -> None:
    if window.debug_log_dock is None:
        window.debug_log_dock = QDockWidget("Debug Log", window)
        window.debug_log_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        window.debug_log_text = QPlainTextEdit()
        window.debug_log_text.setReadOnly(True)
        window.debug_log_dock.setWidget(window.debug_log_text)
        window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, window.debug_log_dock)

    refresh_debug_log(window)
    window.show_dock(window.debug_log_dock)


def refresh_debug_log(window) -> None:
    if window.debug_log_text is None:
        return
    text = "\n".join(window.debug_log) if window.debug_log else "Debug log is empty."
    window.debug_log_text.setPlainText(text)
    scrollbar = window.debug_log_text.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())


def show_text_report(window, title: str, text: str) -> None:
    dialog = QDialog(window)
    dialog.setWindowTitle(title)
    dialog.resize(760, 560)
    layout = QVBoxLayout(dialog)
    text_edit = QPlainTextEdit()
    text_edit.setReadOnly(True)
    text_edit.setPlainText(text)
    layout.addWidget(text_edit)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def show_index_database_info(window) -> None:
    if window.index_database_info_dock is None:
        window.index_database_info_dock = QDockWidget("Index Database Info", window)
        window.index_database_info_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        window.index_database_info_text = QPlainTextEdit()
        window.index_database_info_text.setReadOnly(True)
        window.index_database_info_dock.setWidget(window.index_database_info_text)
        window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, window.index_database_info_dock)

    window.show_dock(window.index_database_info_dock)


def show_annotation_search(window) -> None:
    if window.annotation_search_dock is None:
        window.annotation_search_dock = QDockWidget("Search Annotations", window)
        window.annotation_search_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        window.annotation_search_widget = AnnotationSearchWidget(window)
        window.annotation_search_widget.set_page_size(window.search_page_size)
        window.annotation_search_widget.set_search_rule_storage(
            window.search_rules_dir(),
            window.recent_search_rule_files,
            window.update_recent_search_rule_files,
        )
        window.annotation_search_widget.search_requested.connect(window.search_annotations)
        window.annotation_search_widget.result_activated.connect(window.jump_to_search_result)
        window.annotation_search_widget.maximize_requested.connect(window.toggle_search_dock_maximized)
        window.annotation_search_dock.setWidget(window.annotation_search_widget)
        window.annotation_search_dock.topLevelChanged.connect(window.on_search_dock_top_level_changed)
        window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, window.annotation_search_dock)

    window.show_dock(window.annotation_search_dock)


def update_search_dock_maximize_state(window) -> None:
    if window.annotation_search_dock is None or window.annotation_search_widget is None:
        return
    floating = window.annotation_search_dock.isFloating()
    maximized = bool(floating and window.annotation_search_maximized)
    window.annotation_search_widget.set_maximize_state(floating, maximized)


def toggle_search_dock_maximized(window) -> None:
    if window.annotation_search_dock is None or window.annotation_search_widget is None:
        return
    if not window.annotation_search_dock.isFloating():
        update_search_dock_maximize_state(window)
        return

    if window.annotation_search_maximized:
        if window.annotation_search_restore_geometry is not None:
            window.annotation_search_dock.restoreGeometry(window.annotation_search_restore_geometry)
        window.annotation_search_dock.showNormal()
        window.annotation_search_maximized = False
    else:
        window.annotation_search_restore_geometry = window.annotation_search_dock.saveGeometry()
        window.annotation_search_dock.showMaximized()
        window.annotation_search_maximized = True
    update_search_dock_maximize_state(window)


def show_navigation(window) -> None:
    if window.navigation_dock is None:
        window.navigation_dock = QDockWidget("Navigation", window)
        window.navigation_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        window.navigation_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        window.navigation_widget = NavigationWidget(window)
        window.navigation_widget.bookmark_activated.connect(window.go_to_bookmark)
        window.navigation_widget.anchor_activated.connect(window.go_to_anchor)
        window.navigation_widget.anchor_insert_requested.connect(window.insert_anchor_reference)
        window.navigation_widget.reference_source_activated.connect(window.go_to_anchor_reference_source)
        window.navigation_dock.setWidget(window.navigation_widget)
        window.navigation_dock.setMinimumWidth(40)
        window.navigation_widget.setMinimumWidth(40)
        window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, window.navigation_dock)
        window.resizeDocks([window.navigation_dock], [180], Qt.Orientation.Horizontal)

    window.show_dock(window.navigation_dock)
