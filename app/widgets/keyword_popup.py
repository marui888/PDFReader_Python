from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.keyword_repository import KeywordGroup


class KeywordPopup(QDialog):
    keyword_selected = Signal(str)

    def __init__(self, groups: Sequence[KeywordGroup], parent=None) -> None:
        super().__init__(parent or QApplication.activeWindow())
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Keyword")
        self.resize(760, 420)
        self.setStyleSheet(
            "QDialog { background: #f6f6f6; border: 1px solid #777; }"
            "QTabWidget::pane { border: 1px solid #aaa; background: white; }"
            "QScrollArea { background: white; border: none; }"
            "QPushButton { text-align: left; padding: 3px 6px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        tabs = QTabWidget()
        for group in groups:
            tabs.addTab(self._create_group_page(group), group.name)
        layout.addWidget(tabs)

    def _create_group_page(self, group: KeywordGroup) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(4)

        if not group.items:
            page_layout.addWidget(QLabel("No keywords"))
            return page

        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        columns = self._column_count(len(group.items))
        for index, item in enumerate(group.items):
            button = QPushButton(item)
            button.setToolTip(item)
            button.setMinimumWidth(150)
            button.setMaximumWidth(240)
            button.clicked.connect(lambda checked=False, value=item: self._select_keyword(value))
            row = index // columns
            column = index % columns
            grid.addWidget(button, row, column)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        return page

    def _select_keyword(self, keyword: str) -> None:
        self.keyword_selected.emit(keyword)
        self.accept()

    @staticmethod
    def _column_count(item_count: int) -> int:
        if item_count >= 120:
            return 4
        if item_count >= 40:
            return 3
        return 2
