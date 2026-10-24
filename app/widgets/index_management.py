from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)


class IndexManagementDialog(QDialog):
    add_files = Signal()
    add_folder = Signal()
    add_recent = Signal(object)
    remove_new = Signal()
    row_changed = Signal(int, int, bool)
    clear_requested = Signal()
    restore_requested = Signal()
    update_requested = Signal()
    cancel_requested = Signal()
    selection_changed = Signal()
    check_requested = Signal()

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.rendering = False
        self.setWindowTitle("Index Management")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.setModal(False)
        self.resize(1400, 800)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(1400, available.width() - 40), min(800, available.height() - 60))
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.add_files_button = QPushButton("添加 PDF 文件…")
        self.add_folder_button = QPushButton("添加文件夹…")
        self.remove_button = QPushButton("移除新来源")
        self.check_button = QPushButton("重新检查")
        for button in (self.add_files_button, self.add_folder_button, self.remove_button, self.check_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b04a00; font-weight: bold;")
        layout.addWidget(self.warning)
        vertical = QSplitter(Qt.Orientation.Vertical)
        horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.recent_table = self.make_table(["类型", "最近来源"])
        self.source_table = self.make_table([
            "类型", "当前来源路径", "子目录", "PDF 数", "状态", "最后索引时间", "删除", "更新", "检查修改时间",
        ])
        for title, table in (("最近来源", self.recent_table), ("当前来源", self.source_table)):
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            panel_layout.addWidget(QLabel(title))
            panel_layout.addWidget(table)
            horizontal.addWidget(panel)
        horizontal.setSizes([330, 1040])
        horizontal.setChildrenCollapsible(False)
        vertical.addWidget(horizontal)
        self.tabs = QTabWidget()
        self.details = QPlainTextEdit()
        self.database_info = QPlainTextEdit()
        self.log = QPlainTextEdit()
        self.log.setMaximumBlockCount(2000)
        for widget, title in ((self.details, "来源明细"), (self.database_info, "数据库信息"), (self.log, "任务日志")):
            widget.setReadOnly(True)
            self.tabs.addTab(widget, title)
        vertical.addWidget(self.tabs)
        vertical.setSizes([460, 180])
        layout.addWidget(vertical, 1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.status = QLabel("等待操作")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.clear_button = QPushButton("Clear")
        self.restore_button = QPushButton("Restore")
        self.update_button = QPushButton("Update")
        self.cancel_button = QPushButton("取消任务")
        self.close_button = QPushButton("关闭")
        bottom.addWidget(self.clear_button)
        bottom.addWidget(self.restore_button)
        bottom.addStretch()
        for button in (self.update_button, self.cancel_button, self.close_button):
            bottom.addWidget(button)
        layout.addLayout(bottom)
        self.add_files_button.clicked.connect(lambda: self.add_files.emit())
        self.add_folder_button.clicked.connect(lambda: self.add_folder.emit())
        self.remove_button.clicked.connect(lambda: self.remove_new.emit())
        self.check_button.clicked.connect(lambda: self.check_requested.emit())
        self.clear_button.clicked.connect(lambda: self.clear_requested.emit())
        self.restore_button.clicked.connect(lambda: self.restore_requested.emit())
        self.update_button.clicked.connect(lambda: self.update_requested.emit())
        self.cancel_button.clicked.connect(lambda: self.cancel_requested.emit())
        self.close_button.clicked.connect(self.close)
        self.source_table.itemChanged.connect(self._item_changed)
        self.source_table.itemSelectionChanged.connect(self.selection_changed.emit)
        self.recent_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_table.customContextMenuRequested.connect(self._recent_menu)
        self.recent_enabled = True
        self.set_state(False, False, False, False)

    @staticmethod
    def make_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.verticalHeader().hide()
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        return table

    @Slot(QTableWidgetItem)
    def _item_changed(self, item):
        if not self.rendering and item.column() in (6, 7, 8):
            self.row_changed.emit(item.row(), item.column(), item.checkState() == Qt.CheckState.Checked)

    @Slot(QPoint)
    def _recent_menu(self, pos):
        row = self.recent_table.rowAt(pos.y())
        if row < 0:
            return
        selected = {index.row() for index in self.recent_table.selectionModel().selectedRows()}
        if row not in selected:
            self.recent_table.selectRow(row)
        menu = QMenu(self)
        action = menu.addAction("添加到来源")
        action.setEnabled(self.recent_enabled)
        if menu.exec(self.recent_table.viewport().mapToGlobal(pos)) == action:
            rows = sorted(index.row() for index in self.recent_table.selectionModel().selectedRows())
            self.add_recent.emit(rows)

    def show_sources(self, drafts, snapshot, plan=None):
        selected_keys = {
            self.source_table.item(index.row(), 1).data(Qt.ItemDataRole.UserRole)
            for index in self.source_table.selectionModel().selectedRows()
            if self.source_table.item(index.row(), 1) is not None
        }
        self.rendering = True
        try:
            self.source_table.setRowCount(len(drafts))
            for row, draft in enumerate(drafts):
                source = draft.source
                members = (plan.members.get(source.key, snapshot.members.get(source.key, set()))
                           if plan else snapshot.members.get(source.key, set()))
                state = "新增" if draft.is_new else "待删除" if draft.delete else "待更新" if draft.update else "已有"
                values = ["PDF" if source.kind == "file" else "目录", source.path,
                          ("是" if source.recursive else "否") if source.kind == "folder" else "—",
                          str(len(members)) if not draft.is_new or plan else "—", state,
                          source.indexed_at.replace("T", " ") or "—"]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, source.key)
                    item.setToolTip(source.path if col == 1 else value)
                    font = item.font()
                    font.setBold(draft.update and not draft.delete)
                    item.setFont(font)
                    if draft.delete:
                        item.setForeground(QColor("#888888"))
                    elif draft.is_new:
                        item.setForeground(QColor("#1266b3"))
                    self.source_table.setItem(row, col, item)
                for col, checked in ((6, draft.delete), (7, draft.update), (8, draft.check_modified)):
                    item = QTableWidgetItem("—" if draft.is_new else "")
                    if not draft.is_new:
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                        if draft.delete and col != 6:
                            item.setFlags(Qt.ItemFlag.ItemIsSelectable)
                        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                    self.source_table.setItem(row, col, item)
            self.source_table.clearSelection()
            for row, draft in enumerate(drafts):
                if draft.source.key in selected_keys:
                    for col in range(self.source_table.columnCount()):
                        self.source_table.item(row, col).setSelected(True)
        finally:
            self.rendering = False

    def show_recent(self, recent):
        self.recent_table.setRowCount(len(recent))
        for row, source in enumerate(recent):
            self.recent_table.setItem(row, 0, QTableWidgetItem("PDF" if source.kind == "file" else "目录"))
            item = QTableWidgetItem(source.path)
            item.setToolTip(source.path)
            self.recent_table.setItem(row, 1, item)

    def set_state(self, busy, clear, dirty, can_update, checking=False):
        self.setWindowTitle("Index Management" + (" *" if dirty else "") + (" — 检查中" if checking else ""))
        editable = not busy and not clear
        for button in (self.add_files_button, self.add_folder_button, self.remove_button, self.check_button):
            button.setEnabled(editable)
        self.source_table.setEnabled(editable)
        self.recent_enabled = editable
        self.clear_button.setText("UNclear" if clear else "Clear")
        self.clear_button.setEnabled(not busy)
        self.restore_button.setEnabled(not busy)
        self.update_button.setEnabled(not busy and can_update)
        self.cancel_button.setEnabled(busy)
        self.warning.setText(
            "待清空：如果接下来执行 Update，数据库中的全部索引数据和来源记录将被清空。磁盘上的 PDF 不受影响。"
            if clear else ""
        )

    def closeEvent(self, event):
        if self.controller.request_close():
            event.accept()
        else:
            event.ignore()
