from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMenu, QSpinBox, QToolBar, QToolButton

from app.models.annotation_model import QUICK_HIGHLIGHT_COLORS


def color_tuple_near(left: tuple[float, float, float], right: tuple[float, float, float]) -> bool:
    return all(abs(float(a) - float(b)) < 0.01 for a, b in zip(left[:3], right[:3]))


def set_quick_highlight_button_style(
    button: QToolButton,
    color: tuple[float, float, float],
    opacity_percent: int,
    is_default: bool = False,
) -> None:
    red, green, blue = (max(0, min(255, int(round(channel * 255)))) for channel in color)
    alpha = max(0, min(255, int(round(opacity_percent / 100 * 255))))
    border_width = 3 if is_default else 1
    border_color = "#111111" if is_default else "#505050"
    button.setStyleSheet(
        "QToolButton {"
        f"background-color: rgba({red}, {green}, {blue}, {alpha});"
        f"border: {border_width}px solid {border_color};"
        "padding: 0px;"
        "margin-right: 2px;"
        "}"
    )


def refresh_quick_highlight_button_styles(window) -> None:
    opacity_percent = window.quick_highlight_opacity_spin.value()
    for button, color in window.quick_highlight_color_buttons:
        set_quick_highlight_button_style(
            button,
            color,
            opacity_percent,
            color_tuple_near(color, window.default_highlight_color),
        )


def on_quick_highlight_opacity_changed(window, value: int) -> None:
    window.default_highlight_opacity = max(0.0, min(1.0, value / 100))
    window.save_app_settings()
    refresh_quick_highlight_button_styles(window)
    window.statusBar().showMessage(f"Default highlight opacity: {value}%")


def show_quick_highlight_color_menu(window, button: QToolButton, color: tuple[float, float, float], pos) -> None:
    menu = QMenu(button)
    set_default_action = menu.addAction("Set as Default")
    selected_action = menu.exec(button.mapToGlobal(pos))
    if selected_action == set_default_action:
        window.set_default_highlight_color(color)


def create_actions(window) -> None:
    window.open_action = QAction("Open", window)
    window.open_action.triggered.connect(window.open_pdf)

    window.close_action = QAction("Close", window)
    window.close_action.triggered.connect(window.close_pdf)

    window.save_action = QAction("Save", window)
    window.save_action.triggered.connect(lambda checked=False: window.save())

    window.save_incremental_action = QAction("Save Incremental", window)
    window.save_incremental_action.triggered.connect(lambda checked=False: window.save_incremental())

    window.save_as_action = QAction("Save As", window)
    window.save_as_action.triggered.connect(window.save_as)

    window.settings_action = QAction("Settings...", window)
    window.settings_action.triggered.connect(window.open_settings)

    window.undo_action_qt = QAction("Undo", window)
    window.undo_action_qt.setShortcut("Ctrl+Z")
    window.undo_action_qt.triggered.connect(window.undo_last_action)

    window.delete_annotation_action = QAction("Delete Annotation", window)
    window.delete_annotation_action.setShortcut("Delete")
    window.delete_annotation_action.triggered.connect(window.delete_selected_annotation)

    window.edit_annotation_action = QAction("Annotation Properties", window)
    window.edit_annotation_action.triggered.connect(window.show_annotation_properties)

    window.exit_action = QAction("Exit", window)
    window.exit_action.triggered.connect(window.close)

    window.prev_action = QAction("Prev", window)
    window.prev_action.triggered.connect(window.prev_page)

    window.next_action = QAction("Next", window)
    window.next_action.triggered.connect(window.next_page)

    window.view_back_action = QAction("Back", window)
    window.view_back_action.triggered.connect(window.go_back_view)

    window.view_forward_action = QAction("Forward", window)
    window.view_forward_action.triggered.connect(window.go_forward_view)

    window.page_spin = QSpinBox()
    window.page_spin.setMinimum(1)
    window.page_spin.setMaximum(1)
    window.page_spin.setEnabled(False)
    window.page_spin.setKeyboardTracking(False)
    window.page_spin.valueChanged.connect(window.go_to_page)

    window.page_count_label = QLabel("/ 0")

    window.zoom_out_action = QAction("Zoom -", window)
    window.zoom_out_action.triggered.connect(window.zoom_out)

    window.zoom_in_action = QAction("Zoom +", window)
    window.zoom_in_action.triggered.connect(window.zoom_in)

    window.text_mode_action = QAction("Text", window)
    window.text_mode_action.setCheckable(True)
    window.text_mode_action.setChecked(True)
    window.text_mode_action.triggered.connect(window.activate_text_mode)

    window.add_typewriter_action = QAction("FreeText", window)
    window.add_typewriter_action.setCheckable(True)
    window.add_typewriter_action.triggered.connect(window.add_typewriter)

    window.add_rectangle_action = QAction("Square", window)
    window.add_rectangle_action.setCheckable(True)
    window.add_rectangle_action.triggered.connect(window.add_rectangle)

    window.add_highlight_action = QAction("Highlight", window)
    window.add_highlight_action.setCheckable(True)
    window.add_highlight_action.triggered.connect(window.add_highlight)

    window.add_arrow_action = QAction("Arrow", window)
    window.add_arrow_action.setCheckable(True)
    window.add_arrow_action.triggered.connect(window.add_arrow)

    window.quick_highlight_opacity_spin = QSpinBox()
    window.quick_highlight_opacity_spin.setRange(0, 100)
    window.quick_highlight_opacity_spin.setValue(round(window.default_highlight_opacity * 100))
    window.quick_highlight_opacity_spin.setFixedWidth(70)
    window.quick_highlight_opacity_spin.setMinimumHeight(22)
    window.quick_highlight_opacity_spin.setToolTip("Highlight opacity 0-100")
    window.quick_highlight_opacity_spin.valueChanged.connect(
        lambda value: on_quick_highlight_opacity_changed(window, value)
    )
    window.quick_highlight_color_buttons = []
    for name, color in QUICK_HIGHLIGHT_COLORS.items():
        button = QToolButton(window)
        button.setToolTip(name)
        button.setFixedSize(QSize(18, 18))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        set_quick_highlight_button_style(
            button,
            color,
            window.quick_highlight_opacity_spin.value(),
            color_tuple_near(color, window.default_highlight_color),
        )
        button.clicked.connect(lambda checked=False, selected_color=color: window.apply_quick_highlight_color(selected_color))
        button.customContextMenuRequested.connect(
            lambda pos, selected_button=button, selected_color=color: show_quick_highlight_color_menu(
                window,
                selected_button,
                selected_color,
                pos,
            )
        )
        window.quick_highlight_color_buttons.append((button, color))

    window.show_annotations_action = QAction("Annotations", window)
    window.show_annotations_action.triggered.connect(window.show_current_page_annotations)

    window.show_navigation_action = QAction("Navigation", window)
    window.show_navigation_action.triggered.connect(window.show_navigation)

    window.audit_current_page_action = QAction("Audit Current Page", window)
    window.audit_current_page_action.triggered.connect(window.audit_current_page)

    window.audit_document_summary_action = QAction("Audit Document Summary", window)
    window.audit_document_summary_action.triggered.connect(window.audit_document_summary)

    window.backup_current_pdf_action = QAction("Backup Current PDF", window)
    window.backup_current_pdf_action.triggered.connect(window.backup_current_pdf)

    window.qpdf_check_current_pdf_action = QAction("QPDF Check Current PDF", window)
    window.qpdf_check_current_pdf_action.triggered.connect(window.qpdf_check_current_pdf)

    window.qpdf_rewrite_current_pdf_action = QAction("QPDF Rewrite Current PDF", window)
    window.qpdf_rewrite_current_pdf_action.triggered.connect(window.qpdf_rewrite_current_pdf)

    window.reindex_current_pdf_action = QAction("Reindex Current PDF", window)
    window.reindex_current_pdf_action.triggered.connect(window.reindex_current_pdf)

    window.clear_annotation_index_action = QAction("Clear Annotation Index", window)
    window.clear_annotation_index_action.triggered.connect(window.clear_annotation_index)

    window.index_database_info_action = QAction("Index Database Info", window)
    window.index_database_info_action.triggered.connect(window.show_index_database_info)

    window.search_annotations_action = QAction("Search Annotations", window)
    window.search_annotations_action.triggered.connect(window.show_annotation_search)

    window.debug_log_action = QAction("Debug Log", window)
    window.debug_log_action.triggered.connect(window.show_debug_log)

    window.debug_current_page_state_action = QAction("Debug Current Page State", window)
    window.debug_current_page_state_action.triggered.connect(window.debug_current_page_state)

    window.debug_selected_annotation_pdf_object_action = QAction("Debug Selected Annotation PDF Object", window)
    window.debug_selected_annotation_pdf_object_action.triggered.connect(window.debug_selected_annotation_pdf_object)


def create_menus(window) -> None:
    file_menu = window.menuBar().addMenu("File")
    file_menu.addAction(window.open_action)
    window.open_recent_menu = file_menu.addMenu("Open Recent")
    window.refresh_recent_files_menu()
    file_menu.addAction(window.close_action)
    file_menu.addAction(window.save_action)
    file_menu.addAction(window.save_as_action)
    file_menu.addSeparator()
    file_menu.addAction(window.settings_action)
    file_menu.addSeparator()
    file_menu.addAction(window.exit_action)
    file_menu.addSeparator()
    file_menu.addAction(window.save_incremental_action)

    edit_menu = window.menuBar().addMenu("Edit")
    edit_menu.addAction(window.undo_action_qt)
    edit_menu.addSeparator()
    edit_menu.addAction(window.edit_annotation_action)
    edit_menu.addAction(window.delete_annotation_action)

    tools_menu = window.menuBar().addMenu("Tools")
    tools_menu.addAction(window.show_navigation_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.show_annotations_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.audit_current_page_action)
    tools_menu.addAction(window.audit_document_summary_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.backup_current_pdf_action)
    tools_menu.addAction(window.qpdf_check_current_pdf_action)
    tools_menu.addAction(window.qpdf_rewrite_current_pdf_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.reindex_current_pdf_action)
    tools_menu.addAction(window.clear_annotation_index_action)
    tools_menu.addAction(window.index_database_info_action)
    tools_menu.addAction(window.search_annotations_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.debug_current_page_state_action)
    tools_menu.addAction(window.debug_selected_annotation_pdf_object_action)
    tools_menu.addAction(window.debug_log_action)


def create_toolbar(window) -> None:
    toolbar = QToolBar("Main")
    toolbar.setMovable(False)
    window.addToolBar(toolbar)

    toolbar.addAction(window.view_back_action)
    toolbar.addAction(window.view_forward_action)
    toolbar.addSeparator()

    toolbar.addAction(window.prev_action)
    toolbar.addAction(window.next_action)
    toolbar.addWidget(QLabel("Page"))
    toolbar.addWidget(window.page_spin)
    toolbar.addWidget(window.page_count_label)

    toolbar.addSeparator()

    toolbar.addAction(window.zoom_out_action)
    toolbar.addAction(window.zoom_in_action)

    toolbar.addSeparator()

    toolbar.addAction(window.text_mode_action)
    toolbar.addAction(window.add_typewriter_action)
    toolbar.addAction(window.add_rectangle_action)
    toolbar.addAction(window.add_highlight_action)
    toolbar.addAction(window.add_arrow_action)

    toolbar.addSeparator()
    toolbar.addWidget(QLabel("HL"))
    for button, _color in window.quick_highlight_color_buttons:
        toolbar.addWidget(button)
    toolbar.addWidget(QLabel("Op"))
    toolbar.addWidget(window.quick_highlight_opacity_spin)


def update_actions(window) -> None:
    has_doc = window.doc is not None
    is_reindexing = window.reindex_thread is not None
    for action in (
        window.close_action,
        window.save_action,
        window.save_incremental_action,
        window.save_as_action,
        window.prev_action,
        window.next_action,
        window.zoom_out_action,
        window.zoom_in_action,
        window.text_mode_action,
        window.add_typewriter_action,
        window.add_rectangle_action,
        window.add_highlight_action,
        window.add_arrow_action,
        window.show_annotations_action,
        window.audit_current_page_action,
        window.audit_document_summary_action,
        window.backup_current_pdf_action,
        window.qpdf_check_current_pdf_action,
        window.qpdf_rewrite_current_pdf_action,
        window.debug_current_page_state_action,
        window.reindex_current_pdf_action,
    ):
        action.setEnabled(has_doc)
    window.delete_annotation_action.setEnabled(has_doc and window.selected_annotation_id is not None)
    window.edit_annotation_action.setEnabled(has_doc and window.selected_annotation_id is not None)
    window.debug_selected_annotation_pdf_object_action.setEnabled(
        has_doc and window.selected_annotation_id is not None
    )
    window.undo_action_qt.setEnabled(has_doc and window.undo_action is not None)
    window.clear_annotation_index_action.setEnabled(not is_reindexing)
    window.index_database_info_action.setEnabled(not is_reindexing)
    window.search_annotations_action.setEnabled(not is_reindexing)
    window.view_back_action.setEnabled(window.view_history_controller.can_go_back() and not is_reindexing)
    window.view_forward_action.setEnabled(window.view_history_controller.can_go_forward() and not is_reindexing)

    if not has_doc or window.doc is None:
        window.close_action.setEnabled(False)
        window.delete_annotation_action.setEnabled(False)
        window.edit_annotation_action.setEnabled(False)
        window.debug_selected_annotation_pdf_object_action.setEnabled(False)
        window.undo_action_qt.setEnabled(False)
        window.view_back_action.setEnabled(False)
        window.view_forward_action.setEnabled(False)
        window.page_spin.setEnabled(False)
        window.page_spin.setMaximum(1)
        window.page_count_label.setText("/ 0")
        return

    window.prev_action.setEnabled(window.page_index > 0)
    window.next_action.setEnabled(window.page_index < len(window.doc) - 1)
    window.page_spin.setEnabled(True)
    window.page_spin.setMaximum(len(window.doc))
    window.page_count_label.setText(f"/ {len(window.doc)}")
    if is_reindexing:
        for action in (
            window.open_action,
            window.close_action,
            window.save_action,
            window.save_incremental_action,
            window.save_as_action,
            window.reindex_current_pdf_action,
            window.clear_annotation_index_action,
            window.index_database_info_action,
            window.search_annotations_action,
        ):
            action.setEnabled(False)
