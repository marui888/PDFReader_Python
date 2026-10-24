"""Query input with a shared history entry before the standard edit menu."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget,
    QMainWindow, QPlainTextEdit, QVBoxLayout,
)


class RecentTermLineEdit(QLineEdit):
    def _host_window(self):
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QMainWindow):
            parent = parent.parentWidget()
        return parent

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        first = menu.actions()[0] if menu.actions() else None
        recent = QAction("最近输入…", menu)
        recent.setEnabled(not self.isReadOnly())
        menu.insertAction(first, recent)
        menu.insertSeparator(first)
        try:
            chosen = menu.exec(event.globalPos())
            if chosen is recent:
                self._choose_recent_term()
        finally:
            menu.deleteLater()

    def _choose_recent_term(self):
        host = self._host_window()
        terms = list(getattr(host, "recent_search_terms", []))
        dialog = QDialog(host or self.window())
        dialog.setWindowTitle("最近输入")
        dialog.resize(620, 500)
        layout = QVBoxLayout(dialog)
        hint = QLabel(f"最近 {len(terms)} 条查询输入（最多 50 条）。双击或按 Enter 填入当前输入框。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        filter_input = QLineEdit()
        filter_input.setPlaceholderText("筛选最近输入…")
        layout.addWidget(filter_input)
        term_list = QListWidget()
        layout.addWidget(term_list, 3)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlaceholderText("所选条目的完整内容")
        layout.addWidget(preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("填入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(buttons)

        def selection_changed():
            item = term_list.currentItem()
            preview.setPlainText(item.text() if item is not None else "")
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(item is not None)

        def filter_terms(text):
            term_list.clear()
            term_list.addItems([term for term in terms if text.casefold() in term.casefold()])
            if term_list.count():
                term_list.setCurrentRow(0)
            selection_changed()

        def accept_selection():
            if term_list.currentItem() is not None:
                dialog.accept()

        term_list.currentItemChanged.connect(lambda _current, _previous: selection_changed())
        term_list.itemActivated.connect(lambda _item: accept_selection())
        filter_input.textChanged.connect(filter_terms)
        filter_input.returnPressed.connect(accept_selection)
        buttons.accepted.connect(accept_selection)
        buttons.rejected.connect(dialog.reject)
        filter_terms("")

        def center_dialog():
            parent = host or self.window()
            screen = parent.screen()
            available = screen.availableGeometry() if screen is not None else parent.frameGeometry()
            dialog.resize(min(620, available.width()), min(500, available.height()))
            frame = dialog.frameGeometry()
            frame.moveCenter(parent.frameGeometry().center())
            dialog.move(
                max(available.left(), min(frame.left(), available.right() - frame.width() + 1)),
                max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)),
            )
            filter_input.setFocus()

        QTimer.singleShot(0, center_dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            item = term_list.currentItem()
            if item is not None:
                self.setText(item.text())
                self.setFocus(Qt.FocusReason.OtherFocusReason)
                self.setCursorPosition(len(self.text()))
        dialog.deleteLater()
