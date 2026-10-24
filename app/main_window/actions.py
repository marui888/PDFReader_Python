from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QMenu, QSpinBox, QToolBar, QToolButton, QWidget

from app.models.annotation_model import QUICK_HIGHLIGHT_COLORS
from app.services.icons import toolbar_icon


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


def set_panel_visible(window, dock_attribute: str, show_panel, visible: bool) -> None:
    if visible:
        show_panel()
    else:
        dock = getattr(window, dock_attribute)
        if dock is not None:
            dock.hide()


def sync_zoom_scale(window, force: bool = False) -> None:
    combo = getattr(window, "zoom_scale_combo", None)
    if combo is None:
        return
    combo.setEnabled(window.doc is not None)
    if force or not combo.lineEdit().hasFocus():
        combo.setCurrentText(f"{window.zoom * 100:.6f}".rstrip("0").rstrip(".") + "%")


def apply_zoom_scale(window) -> None:
    if window.doc is None:
        sync_zoom_scale(window, force=True)
        return
    text = window.zoom_scale_combo.currentText().strip().replace("％", "%")
    number = text.removesuffix("%").strip()
    if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", number):
        sync_zoom_scale(window, force=True)
        window.statusBar().showMessage("Invalid zoom. Enter a percentage from 50% to 400%.", 5000)
        return
    percent = float(number)
    if not 50.0 <= percent <= 400.0:
        sync_zoom_scale(window, force=True)
        window.statusBar().showMessage("Zoom must be between 50% and 400%.", 5000)
        return
    window.set_zoom_scale(percent / 100.0)


def create_actions(window) -> None:
    window.open_action = QAction("Open", window)
    window.open_action.triggered.connect(window.open_pdf)

    window.close_action = QAction("Close", window)
    window.close_action.triggered.connect(window.close_pdf)

    window.save_action = QAction("Save", window)
    window.save_action.setEnabled(False)
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

    window.page_labels_action = QAction("Page Labels...", window)
    window.page_labels_action.triggered.connect(window.show_page_labels_dialog)

    window.exit_action = QAction("Exit", window)
    window.exit_action.triggered.connect(window.close)

    window.prev_action = QAction("Prev", window)
    window.prev_action.triggered.connect(window.prev_page)

    window.next_action = QAction("Next", window)
    window.next_action.triggered.connect(window.next_page)

    window.first_page_action = QAction("First Page", window)
    window.first_page_action.setShortcut("Ctrl+Home")
    window.first_page_action.triggered.connect(
        lambda checked=False: window.go_to_page(1) if window.doc is not None else None
    )
    window.addAction(window.first_page_action)

    window.last_page_action = QAction("Last Page", window)
    window.last_page_action.setShortcut("Ctrl+End")
    window.last_page_action.triggered.connect(
        lambda checked=False: window.go_to_page(len(window.doc)) if window.doc is not None else None
    )
    window.addAction(window.last_page_action)

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
    window.book_label_input = QLineEdit()
    window.book_label_input.setEnabled(False)
    window.book_label_input.setFixedWidth(90)
    window.book_label_input.setPlaceholderText("Book")
    window.book_label_input.returnPressed.connect(window.go_to_book_label)

    window.zoom_out_action = QAction("Zoom -", window)
    window.zoom_out_action.triggered.connect(window.zoom_out)

    window.zoom_in_action = QAction("Zoom +", window)
    window.zoom_in_action.triggered.connect(window.zoom_in)

    window.zoom_scale_combo = QComboBox(window)
    window.zoom_scale_combo.setEditable(True)
    window.zoom_scale_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    window.zoom_scale_combo.addItems([
        "50%", "75%", "100%", "125%", "150%", "175%", "200%", "250%", "300%", "400%",
    ])
    window.zoom_scale_combo.setFixedWidth(100)
    window.zoom_scale_combo.setToolTip("Zoom 50%-400%. Select a value or enter a percentage and press Enter.")
    window.zoom_scale_combo.activated.connect(lambda _index: apply_zoom_scale(window))
    window.zoom_scale_combo.lineEdit().returnPressed.connect(lambda: apply_zoom_scale(window))
    sync_zoom_scale(window, force=True)

    window.continuous_view_action = QAction("Continuous", window)
    window.continuous_view_action.setCheckable(True)
    window.continuous_view_action.triggered.connect(
        lambda checked=False: window.set_continuous_view_enabled(checked)
    )
    window.side_by_side_action = QAction("Side by Side", window)
    window.side_by_side_action.setCheckable(True)
    window.side_by_side_action.triggered.connect(window.reading_workspace.set_side_by_side)
    window.sync_vertical_action = QAction("Sync Vertical Scrolling", window)
    window.sync_vertical_action.setCheckable(True)
    window.sync_vertical_action.setEnabled(False)
    window.sync_vertical_action.triggered.connect(
        lambda checked=False: window.reading_workspace.sync.set_enabled(checked)
    )

    window.text_mode_action = QAction("Text", window)
    window.text_mode_action.setCheckable(True)
    window.text_mode_action.setChecked(True)
    window.text_mode_action.triggered.connect(
        lambda checked=False: window.reading_workspace.activate_tool(None)
    )

    window.add_typewriter_action = QAction("FreeText", window)
    window.add_typewriter_action.setCheckable(True)
    window.add_typewriter_action.triggered.connect(
        lambda checked=False: window.reading_workspace.activate_tool("freetext")
    )

    window.add_rectangle_action = QAction("Square", window)
    window.add_rectangle_action.setCheckable(True)
    window.add_rectangle_action.triggered.connect(
        lambda checked=False: window.reading_workspace.activate_tool("square")
    )

    window.add_highlight_action = QAction("Highlight", window)
    window.add_highlight_action.setCheckable(True)
    window.add_highlight_action.triggered.connect(
        lambda checked=False: window.reading_workspace.activate_tool("highlight")
    )

    window.add_arrow_action = QAction("Arrow", window)
    window.add_arrow_action.setCheckable(True)
    window.add_arrow_action.triggered.connect(
        lambda checked=False: window.reading_workspace.activate_tool("arrow")
    )

    window.annotation_tool_group = QActionGroup(window)
    window.annotation_tool_group.setExclusive(True)
    for action in (window.text_mode_action, window.add_typewriter_action,
                   window.add_rectangle_action, window.add_highlight_action,
                   window.add_arrow_action):
        window.annotation_tool_group.addAction(action)

    window.quick_highlight_opacity_spin = QSpinBox()
    window.quick_highlight_opacity_spin.setRange(0, 100)
    window.quick_highlight_opacity_spin.setValue(round(window.default_highlight_opacity * 100))
    window.quick_highlight_opacity_spin.setFixedWidth(70)
    window.quick_highlight_opacity_spin.setMinimumHeight(22)
    window.quick_highlight_opacity_spin.setToolTip("Highlight opacity 0-100")
    window.quick_highlight_opacity_spin.valueChanged.connect(
        lambda value: on_quick_highlight_opacity_changed(window, value)
    )
    window.ui_contrast_spin = QSpinBox()
    window.ui_contrast_spin.setRange(0, 100)
    window.ui_contrast_spin.setValue(window.ui_contrast)
    window.ui_contrast_spin.setFixedWidth(72)
    window.ui_contrast_spin.setMinimumHeight(22)
    window.ui_contrast_spin.setToolTip("UI hover/selected contrast 0-100")
    window.ui_contrast_spin.valueChanged.connect(lambda value: window.set_ui_contrast(value))
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
    window.show_navigation_action = QAction("Navigation", window)
    for action, dock_attribute, show_panel in (
        (window.show_annotations_action, "annotations_dock", window.show_current_page_annotations),
        (window.show_navigation_action, "navigation_dock", window.show_navigation),
    ):
        action.setCheckable(True)
        action.triggered.connect(
            lambda checked, dock_name=dock_attribute, show=show_panel: set_panel_visible(
                window, dock_name, show, checked
            )
        )
        dock = getattr(window, dock_attribute)
        if dock is not None:
            dock.visibilityChanged.connect(action.setChecked)
            action.setChecked(not dock.isHidden())

    window.audit_current_page_action = QAction("Audit Current Page", window)
    window.bulk_edit_annotation_action = QAction("Edit Annotation…", window)
    window.bulk_edit_annotation_action.triggered.connect(window.bulk_annotation_controller.show)
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

    window.index_management_action = QAction("Index Management…", window)
    window.index_management_action.triggered.connect(window.index_management_controller.show)

    window.search_annotations_action = QAction("Search Annotations", window)
    window.search_annotations_action.triggered.connect(window.show_annotation_search)

    window.debug_log_action = QAction("Debug Log", window)
    window.debug_log_action.triggered.connect(window.show_debug_log)

    window.debug_current_page_state_action = QAction("Debug Current Page State", window)
    window.debug_current_page_state_action.triggered.connect(window.debug_current_page_state)

    window.audit_continuous_view_state_action = QAction("Audit Continuous View State", window)
    window.audit_continuous_view_state_action.triggered.connect(window.audit_continuous_view_state)

    window.debug_selected_annotation_pdf_object_action = QAction("Debug Selected Annotation PDF Object", window)
    window.debug_selected_annotation_pdf_object_action.triggered.connect(window.debug_selected_annotation_pdf_object)


def create_menus(window) -> None:
    file_menu = window.menuBar().addMenu("File")
    window.file_menu = file_menu
    file_menu.addAction(window.open_action)
    window.open_recent_menu = file_menu.addMenu("Open Recent")
    window.refresh_recent_files_menu()
    file_menu.addAction(window.close_action)
    file_menu.addSeparator()
    file_menu.addAction(window.save_incremental_action)
    file_menu.addAction(window.save_action)
    file_menu.addAction(window.save_as_action)
    file_menu.addSeparator()
    file_menu.addAction(window.settings_action)
    file_menu.addSeparator()
    file_menu.addAction(window.exit_action)

    edit_menu = window.menuBar().addMenu("Edit")
    window.edit_menu = edit_menu
    edit_menu.addAction(window.undo_action_qt)
    edit_menu.addSeparator()
    edit_menu.addAction(window.edit_annotation_action)
    edit_menu.addAction(window.delete_annotation_action)
    edit_menu.addSeparator()
    edit_menu.addAction(window.page_labels_action)

    view_menu = window.menuBar().addMenu("View")
    window.view_menu = view_menu
    view_menu.addAction(window.continuous_view_action)
    view_menu.addSeparator()
    view_menu.addAction(window.show_annotations_action)
    view_menu.addAction(window.show_navigation_action)
    view_menu.addSeparator()
    view_menu.addAction(window.side_by_side_action)
    view_menu.addAction(window.sync_vertical_action)

    tools_menu = window.menuBar().addMenu("Tools")
    window.tools_menu = tools_menu
    tools_menu.addAction(window.bulk_edit_annotation_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.audit_current_page_action)
    tools_menu.addAction(window.audit_document_summary_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.backup_current_pdf_action)
    tools_menu.addAction(window.qpdf_check_current_pdf_action)
    tools_menu.addAction(window.qpdf_rewrite_current_pdf_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.index_management_action)
    tools_menu.addAction(window.search_annotations_action)
    tools_menu.addSeparator()
    tools_menu.addAction(window.debug_current_page_state_action)
    tools_menu.addAction(window.audit_continuous_view_state_action)
    tools_menu.addAction(window.debug_selected_annotation_pdf_object_action)
    tools_menu.addAction(window.debug_log_action)
    window.contrast_menus = [
        file_menu,
        window.open_recent_menu,
        edit_menu,
        view_menu,
        tools_menu,
    ]


def create_toolbar(window) -> None:
    toolbar = QToolBar("Main")
    toolbar.setObjectName("mainToolbar")
    window.main_toolbar = toolbar
    toolbar.setMovable(False)
    toolbar.setContentsMargins(0, 0, 0, 0)
    if toolbar.layout() is not None:
        toolbar.layout().setContentsMargins(0, 0, 0, 0)
        toolbar.layout().setSpacing(1)
    window.addToolBar(toolbar)

    toolbar.addAction(window.view_back_action)
    toolbar.addAction(window.view_forward_action)
    toolbar.addSeparator()

    toolbar.addAction(window.prev_action)
    toolbar.addAction(window.next_action)
    toolbar.addWidget(QLabel("PPage"))
    toolbar.addWidget(window.page_spin)
    toolbar.addWidget(window.page_count_label)
    page_book_spacer = QWidget()
    page_book_spacer.setFixedWidth(12)
    toolbar.addWidget(page_book_spacer)
    toolbar.addWidget(QLabel("BPage"))
    toolbar.addWidget(window.book_label_input)

    toolbar.addSeparator()

    toolbar.addAction(window.zoom_out_action)
    toolbar.addAction(window.zoom_in_action)
    toolbar.addWidget(window.zoom_scale_combo)
    toolbar.addAction(window.continuous_view_action)

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
    toolbar.addWidget(QLabel("Opacity"))
    toolbar.addWidget(window.quick_highlight_opacity_spin)
    toolbar.addWidget(QLabel("Contrast"))
    toolbar.addWidget(window.ui_contrast_spin)

    for action, icon_name in (
        (window.zoom_in_action, "zoom_in"),
        (window.zoom_out_action, "zoom_out"),
        (window.add_typewriter_action, "freetext"),
        (window.add_rectangle_action, "square"),
        (window.add_highlight_action, "highlight"),
        (window.add_arrow_action, "arrow"),
    ):
        action.setIcon(toolbar_icon(icon_name))
        button = toolbar.widgetForAction(action)
        if isinstance(button, QToolButton):
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(14, 14))
            button.setAccessibleName(action.text())


def update_actions(window) -> None:
    window.reading_workspace.update_actions()
    sync_zoom_scale(window)
    has_doc = window.doc is not None
    is_reindexing = window.reindex_thread is not None
    is_index_updating = window.index_management_controller.applying
    window.bulk_edit_annotation_action.setEnabled(
        has_doc and not is_reindexing and not is_index_updating
        and not window.index_management_controller.preparing and not window.bulk_annotation_controller.busy
    )
    window.open_action.setEnabled(not is_reindexing and not is_index_updating and not window.bulk_annotation_controller.busy)
    if window.open_recent_menu is not None:
        window.open_recent_menu.setEnabled(not is_index_updating and not window.bulk_annotation_controller.busy)
    is_continuous = getattr(window, "view_mode", "single") == "continuous"
    for action in (
        window.close_action,
        window.save_incremental_action,
        window.save_as_action,
        window.prev_action,
        window.next_action,
        window.first_page_action,
        window.last_page_action,
        window.zoom_out_action,
        window.zoom_in_action,
        window.continuous_view_action,
        window.text_mode_action,
        window.add_typewriter_action,
        window.add_rectangle_action,
        window.add_highlight_action,
        window.add_arrow_action,
        window.audit_current_page_action,
        window.audit_document_summary_action,
        window.backup_current_pdf_action,
        window.qpdf_check_current_pdf_action,
        window.qpdf_rewrite_current_pdf_action,
        window.debug_current_page_state_action,
        window.audit_continuous_view_state_action,
        window.reindex_current_pdf_action,
        window.page_labels_action,
    ):
        action.setEnabled(has_doc)
    window.save_action.setEnabled(False)
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
    for shortcut in getattr(window, "shortcut_objects", []):
        shortcut.setEnabled(has_doc)
    if hasattr(window, "quick_highlight_color_buttons"):
        for button, _color in window.quick_highlight_color_buttons:
            button.setEnabled(has_doc)
    if hasattr(window, "quick_highlight_opacity_spin"):
        window.quick_highlight_opacity_spin.setEnabled(has_doc)
    if hasattr(window, "ui_contrast_spin"):
        window.ui_contrast_spin.setEnabled(True)
    if getattr(window, "batch_freetext_widget", None) is not None:
        window.batch_freetext_widget.set_read_only(
            not has_doc or is_reindexing or is_index_updating or window.bulk_annotation_controller.busy
        )

    window.continuous_view_action.setChecked(is_continuous)
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
        window.book_label_input.clear()
        window.book_label_input.setEnabled(False)
        return

    window.prev_action.setEnabled(window.page_index > 0)
    window.next_action.setEnabled(window.page_index < len(window.doc) - 1)
    window.page_spin.setEnabled(True)
    window.page_spin.setMaximum(len(window.doc))
    window.page_count_label.setText(f"/ {len(window.doc)}")
    window.book_label_input.setEnabled(True)
    if window.index_management_controller.is_path_busy(window.pdf_path):
        window.save_incremental_action.setEnabled(False)
        window.save_as_action.setEnabled(False)
    if is_reindexing:
        for action in (
            window.open_action,
            window.close_action,
            window.save_incremental_action,
            window.save_as_action,
            window.reindex_current_pdf_action,
            window.clear_annotation_index_action,
            window.index_database_info_action,
            window.search_annotations_action,
        ):
            action.setEnabled(False)
