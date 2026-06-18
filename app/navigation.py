from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.anchors import AnchorModel, AnchorReferenceModel


class BookmarksWidget(QWidget):
    bookmark_activated = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.empty_label = QLabel("No bookmarks")
        self.empty_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.tree)

        self.tree.itemActivated.connect(self.on_item_activated)
        self.tree.itemClicked.connect(self.on_item_activated)

    def set_toc(self, toc: list[list]) -> None:
        self.tree.clear()
        if not toc:
            self.empty_label.setVisible(True)
            self.tree.setVisible(False)
            return

        self.empty_label.setVisible(False)
        self.tree.setVisible(True)
        parents: dict[int, QTreeWidgetItem] = {}
        for entry in toc:
            if len(entry) < 3:
                continue
            level, title, page_number = entry[:3]
            try:
                level = max(1, int(level))
                page_index = max(0, int(page_number) - 1)
            except (TypeError, ValueError):
                continue

            item = QTreeWidgetItem()
            item.setText(0, str(title))
            item.setData(0, Qt.ItemDataRole.UserRole, page_index)
            item.setToolTip(0, f"Page {page_index + 1}")
            parent = parents.get(level - 1)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            parents[level] = item
            for stale_level in [key for key in parents if key > level]:
                parents.pop(stale_level, None)

        self.tree.expandToDepth(0)

    def clear(self) -> None:
        self.set_toc([])

    def on_item_activated(self, item: QTreeWidgetItem) -> None:
        page_index = item.data(0, Qt.ItemDataRole.UserRole)
        if page_index is None:
            return
        self.bookmark_activated.emit(int(page_index))


class AnchorsWidget(QWidget):
    anchor_activated = Signal(int, int)
    anchor_insert_requested = Signal(str)
    reference_source_activated = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.anchors: list[AnchorModel] = []
        self.copy_button = QPushButton("Copy Ref")
        self.insert_button = QPushButton("Insert Ref")
        self.references_button = QPushButton("Show Refs")
        self.empty_label = QLabel("No anchors")
        self.empty_label.setVisible(False)
        self.references_by_anchor: dict[str, list[AnchorReferenceModel]] = {}
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Ref", "Page", "xref", "Refs", "Text"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.insert_button)
        buttons.addWidget(self.references_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(buttons)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.table)

        self.copy_button.clicked.connect(self.copy_selected_reference)
        self.insert_button.clicked.connect(self.insert_selected_reference)
        self.references_button.clicked.connect(self.show_selected_references)
        self.table.itemDoubleClicked.connect(lambda _item: self.activate_selected_anchor())
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.set_insert_enabled(False)

    def set_anchors(
        self,
        anchors: list[AnchorModel],
        references_by_anchor: dict[str, list[AnchorReferenceModel]] | None = None,
    ) -> None:
        self.anchors = anchors
        self.references_by_anchor = references_by_anchor or {}
        self.table.setRowCount(0)
        self.table.setRowCount(len(anchors))
        for row, anchor in enumerate(anchors):
            values = (
                anchor.reference,
                str(anchor.page_number),
                str(anchor.xref),
                str(len(self.references_by_anchor.get(anchor.reference, []))),
                self.compact_text(anchor.text),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(anchor.text)
                self.table.setItem(row, column, item)
        self.empty_label.setVisible(not anchors)
        self.table.setVisible(bool(anchors))
        self.update_buttons()

    def clear(self) -> None:
        self.set_anchors([])

    def selected_anchor(self) -> AnchorModel | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.anchors):
            return None
        return self.anchors[row]

    def activate_selected_anchor(self) -> None:
        anchor = self.selected_anchor()
        if anchor is None:
            return
        self.anchor_activated.emit(anchor.page_index, anchor.xref)

    def copy_selected_reference(self) -> None:
        anchor = self.selected_anchor()
        if anchor is None:
            return
        QApplication.clipboard().setText(anchor.reference)

    def insert_selected_reference(self) -> None:
        anchor = self.selected_anchor()
        if anchor is None:
            return
        self.anchor_insert_requested.emit(anchor.reference)

    def show_selected_references(self) -> None:
        anchor = self.selected_anchor()
        if anchor is None:
            return
        references = self.references_by_anchor.get(anchor.reference, [])
        dialog = ReferencesDialog(anchor.reference, references, self)
        dialog.reference_activated.connect(self.reference_source_activated.emit)
        dialog.exec()

    def set_insert_enabled(self, enabled: bool) -> None:
        self.insert_button.setProperty("targetEnabled", bool(enabled))
        self.update_buttons()

    def update_buttons(self) -> None:
        has_selection = self.selected_anchor() is not None
        self.copy_button.setEnabled(has_selection)
        self.insert_button.setEnabled(has_selection and bool(self.insert_button.property("targetEnabled")))
        anchor = self.selected_anchor()
        has_references = bool(anchor and self.references_by_anchor.get(anchor.reference))
        self.references_button.setEnabled(has_references)

    def compact_text(self, text: str) -> str:
        compact = " ".join(text.split())
        return compact[:160] + "..." if len(compact) > 160 else compact


class NavigationWidget(QTabWidget):
    bookmark_activated = Signal(int)
    anchor_activated = Signal(int, int)
    anchor_insert_requested = Signal(str)
    reference_source_activated = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bookmarks_widget = BookmarksWidget()
        self.anchors_widget = AnchorsWidget()
        self.addTab(self.bookmarks_widget, "目录")
        self.addTab(self.anchors_widget, "锚点")
        self.bookmarks_widget.bookmark_activated.connect(self.bookmark_activated.emit)
        self.anchors_widget.anchor_activated.connect(self.anchor_activated.emit)
        self.anchors_widget.anchor_insert_requested.connect(self.anchor_insert_requested.emit)
        self.anchors_widget.reference_source_activated.connect(self.reference_source_activated.emit)

    def set_bookmarks(self, toc: list[list]) -> None:
        self.bookmarks_widget.set_toc(toc)

    def clear_bookmarks(self) -> None:
        self.bookmarks_widget.clear()

    def set_anchors(
        self,
        anchors: list[AnchorModel],
        references_by_anchor: dict[str, list[AnchorReferenceModel]] | None = None,
    ) -> None:
        self.anchors_widget.set_anchors(anchors, references_by_anchor)

    def clear_anchors(self) -> None:
        self.anchors_widget.clear()

    def set_anchor_insert_enabled(self, enabled: bool) -> None:
        self.anchors_widget.set_insert_enabled(enabled)


class ReferencesDialog(QDialog):
    reference_activated = Signal(int, int)

    def __init__(self, reference: str, references: list[AnchorReferenceModel], parent=None) -> None:
        super().__init__(parent)
        self.references = references
        self.setWindowTitle(f"Refs to {reference}")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Page", "xref", "Ref", "Text"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.table.itemDoubleClicked.connect(lambda _item: self.activate_selected_reference())
        self.set_references(reference, references)

    def set_references(self, reference: str, references: list[AnchorReferenceModel]) -> None:
        self.table.setRowCount(len(references))
        for row, item in enumerate(references):
            values = (
                str(item.page_number),
                str(item.xref),
                reference,
                " ".join(item.text.split()),
            )
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                table_item.setToolTip(item.text)
                self.table.setItem(row, column, table_item)

    def activate_selected_reference(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.references):
            return
        reference = self.references[row]
        self.reference_activated.emit(reference.page_index, reference.xref)
        self.accept()
