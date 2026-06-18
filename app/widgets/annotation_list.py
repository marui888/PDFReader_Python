from collections.abc import Callable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

from app.models.annotation_model import AnnotationModel


class AnnotationListWidget(QTableWidget):
    def __init__(
        self,
        rect_text: Callable[[object], str],
        annotation_note: Callable[[AnnotationModel], str],
    ) -> None:
        super().__init__(0, 6)
        self.rect_text = rect_text
        self.annotation_note = annotation_note
        self.setHorizontalHeaderLabels(("Status", "Type", "xref", "Content", "Rect", "Note"))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setStyleSheet(
            "QTableWidget::item:selected { background-color: rgb(242, 242, 242); color: black; }"
        )

    def set_annotations(self, annotations: list[AnnotationModel]) -> None:
        self.setRowCount(len(annotations))
        for row, model in enumerate(annotations):
            values = (
                "Supported" if model.is_supported else "Unsupported",
                f"{model.pdf_type} / {model.app_type}",
                str(model.xref),
                model.text,
                self.rect_text(model.rect),
                self.annotation_note(model),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not model.is_supported:
                    item.setBackground(QColor(255, 225, 225))
                self.setItem(row, column, item)
        self.resizeColumnsToContents()

    def select_annotation(self, annotation_id: str | None, annotations: list[AnnotationModel]) -> None:
        self.clearSelection()
        if annotation_id is None:
            return
        for row, model in enumerate(annotations):
            if model.id == annotation_id:
                self.selectRow(row)
                return

    def selected_row(self) -> int | None:
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        return rows[0].row()
