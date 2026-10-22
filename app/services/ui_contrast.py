from __future__ import annotations


def clamp_contrast(value: int) -> int:
    return max(0, min(100, int(value)))


def _mix_channel(base: int, target: int, amount: float) -> int:
    return max(0, min(255, round(base + (target - base) * amount)))


def _mix_color(base: tuple[int, int, int], target: tuple[int, int, int], amount: float) -> str:
    red = _mix_channel(base[0], target[0], amount)
    green = _mix_channel(base[1], target[1], amount)
    blue = _mix_channel(base[2], target[2], amount)
    return f"rgb({red}, {green}, {blue})"


def ui_contrast_colors(contrast: int) -> dict[str, str]:
    amount = clamp_contrast(contrast) / 100.0
    return {
        "hover": _mix_color((248, 248, 248), (186, 216, 255), amount),
        "selected": _mix_color((242, 242, 242), (132, 185, 255), amount),
        "pressed": _mix_color((234, 234, 234), (92, 160, 245), amount),
        "tab": "#e7e7e7",
        "tab_hover": _mix_color((236, 240, 246), (172, 207, 255), amount),
        "tab_selected": "#ffffff",
    }


def application_contrast_stylesheet(contrast: int) -> str:
    return "\n".join(
        (
            menu_contrast_stylesheet(contrast),
            button_combo_contrast_stylesheet(contrast),
            toolbar_contrast_stylesheet(contrast),
            tabbar_contrast_stylesheet(contrast),
            table_contrast_stylesheet(contrast),
        )
    )


def menu_contrast_stylesheet(contrast: int) -> str:
    colors = ui_contrast_colors(contrast)
    return f"""
    QMenuBar::item:selected {{
        background-color: {colors["hover"]};
        color: #111111;
    }}
    QMenuBar::item:pressed {{
        background-color: {colors["pressed"]};
        color: #111111;
    }}
    QMenu {{
        selection-background-color: {colors["hover"]};
        selection-color: #111111;
    }}
    QMenu::item {{
        padding: 3px 22px 3px 22px;
    }}
    QMenu::item:selected {{
        background-color: {colors["hover"]};
        color: #111111;
    }}
    QMenu::item:pressed {{
        background-color: {colors["pressed"]};
        color: #111111;
    }}
    """


def button_combo_contrast_stylesheet(contrast: int) -> str:
    colors = ui_contrast_colors(contrast)
    return f"""
    QPushButton:hover {{
        background-color: {colors["hover"]};
        color: #111111;
    }}
    QPushButton:pressed {{
        background-color: {colors["pressed"]};
        color: #111111;
    }}
    QComboBox:hover {{
        background-color: {colors["hover"]};
        color: #111111;
    }}
    QComboBox:focus {{
        background-color: {colors["selected"]};
        color: #111111;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background-color: {colors["hover"]};
        color: #111111;
    }}
    QComboBox QAbstractItemView::item:selected {{
        background-color: {colors["selected"]};
        color: #111111;
    }}
    """


def toolbar_contrast_stylesheet(contrast: int) -> str:
    colors = ui_contrast_colors(contrast)
    return f"""
    QToolBar#mainToolbar {{
        spacing: 1px;
        padding: 0px;
        margin: 0px;
    }}
    QToolBar#mainToolbar QToolButton:!checked:hover {{
        background-color: {colors["hover"]};
        color: #111111;
        border: 1px solid #8aaee6;
    }}
    QToolBar#mainToolbar QToolButton:!checked:pressed {{
        background-color: {colors["pressed"]};
        color: #111111;
        border: 1px solid #5b8def;
    }}
    QToolBar#mainToolbar QToolButton:checked,
    QToolBar#mainToolbar QToolButton:checked:hover,
    QToolBar#mainToolbar QToolButton:checked:pressed {{
        background-color: #0078d4;
        color: #ffffff;
        border: 1px solid #0078d4;
    }}
    """


def tabbar_contrast_stylesheet(contrast: int) -> str:
    colors = ui_contrast_colors(contrast)
    return f"""
    QTabWidget::pane {{
        border: 1px solid #c8c8c8;
    }}
    QTabBar::tab {{
        background-color: {colors["tab"]};
        border: 1px solid #b8b8b8;
        padding: 4px 6px;
        margin: 1px;
    }}
    QTabBar::tab:hover {{
        background-color: {colors["tab_hover"]};
        color: #111111;
    }}
    QTabBar::tab:selected {{
        background-color: {colors["tab_selected"]};
        color: #111111;
    }}
    """


def table_contrast_stylesheet(contrast: int) -> str:
    colors = ui_contrast_colors(contrast)
    return f"""
    QTableWidget::item:hover {{
        background-color: {colors["hover"]};
    }}
    QTableWidget::item:selected {{
        background-color: {colors["selected"]};
        color: #111111;
    }}
    """


def annotation_table_contrast_stylesheet(contrast: int) -> str:
    return table_contrast_stylesheet(contrast)


def document_tabbar_contrast_stylesheet(contrast: int) -> str:
    colors = ui_contrast_colors(contrast)
    return f"""
    QTabBar::tab {{
        background: {colors["tab"]};
        border: 1px solid #b8b8b8;
        border-bottom-color: #8f8f8f;
        padding: 2px 6px;
        margin-right: 1px;
        min-height: 18px;
    }}
    QTabBar::tab:hover {{
        background: {colors["tab_hover"]};
        color: #111111;
    }}
    QTabBar::tab:selected {{
        background: white;
        border: 2px solid #4f7ecb;
        border-bottom-color: white;
        font-weight: 700;
    }}
    QTabBar::tab:!selected {{
        color: #444;
    }}
    """
