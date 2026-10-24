from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QSpinBox, QVBoxLayout, QWidget,
)

from app.models.annotation_model import QUICK_HIGHLIGHT_COLORS


class BulkAnnotationEditDialog(QDialog):
    apply_requested = Signal()

    def __init__(self, parent, path, page_index):
        super().__init__(parent)
        self.busy = False
        self.last_color_index = 0
        self.setWindowTitle("Edit Annotation")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        target = QLabel(f"目标文件：{path.name}\n{path}")
        target.setWordWrap(True)
        target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(target)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        self.type_combo = QComboBox()
        self.type_combo.addItem("Highlight", "highlight")
        form.addRow("Annotation Type", self.type_combo)
        self.color_combo = QComboBox()
        self.color_combo.addItem("不修改", None)
        for name, color in QUICK_HIGHLIGHT_COLORS.items():
            self.color_combo.addItem(name, color)
        self.color_combo.addItem("自定义颜色…", "custom")
        form.addRow("Color", self.color_combo)
        opacity_row = QHBoxLayout()
        self.opacity_check = QCheckBox("修改（不勾选则保留各自原值）")
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(5, 100)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setValue(45)
        self.opacity_spin.setEnabled(False)
        opacity_row.addWidget(self.opacity_check)
        opacity_row.addWidget(self.opacity_spin)
        form.addRow("Opacity", opacity_row)
        opacity_hint = QLabel("不透明度：数值越大越不透明，100% 为完全不透明。")
        opacity_hint.setWordWrap(True)
        form.addRow(opacity_hint)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem(f"Current Page（第 {page_index + 1} 页）", "page")
        self.scope_combo.addItem("Current Document（整篇文档）", "document")
        form.addRow("Scope", self.scope_combo)
        layout.addWidget(self.controls)
        note = QLabel("仅支持 Highlight。Apply 与 FreeText Replace All 一样，只修改当前内存文档。\n"
                      "不关闭 session，不自动保存；已有未保存编辑会保留。\n"
                      "本批量操作不支持 Undo，并清除该文档原有撤销记录。请手动保存；"
                      "关闭并放弃修改会一并丢弃该文档所有未保存编辑。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("请选择至少一个需要修改的属性。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.apply_button = QPushButton("Apply")
        self.apply_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.color_combo.currentIndexChanged.connect(self._color_changed)
        self.opacity_check.toggled.connect(self._opacity_changed)
        self.apply_button.clicked.connect(self.apply_requested.emit)
        self.close_button.clicked.connect(self.reject)

    def _color_changed(self, index):
        if self.color_combo.itemData(index) == "custom":
            color = QColorDialog.getColor(QColor("yellow"), self, "Highlight Color")
            self.color_combo.blockSignals(True)
            if color.isValid():
                value = (color.redF(), color.greenF(), color.blueF())
                custom_index = self.color_combo.count() - 1
                self.color_combo.insertItem(custom_index, f"Custom {color.name()}", value)
                self.color_combo.setCurrentIndex(custom_index)
                self.last_color_index = custom_index
            else:
                self.color_combo.setCurrentIndex(self.last_color_index)
            self.color_combo.blockSignals(False)
        else:
            self.last_color_index = index
        self._update_enabled()

    def _opacity_changed(self, checked):
        self.opacity_spin.setEnabled(checked)
        self._update_enabled()

    def _update_enabled(self):
        self.apply_button.setEnabled(not self.busy and
                                     (self.color_combo.currentData() is not None or self.opacity_check.isChecked()))

    def options(self):
        color = self.color_combo.currentData()
        if color == "custom":
            raise ValueError("请先选择有效颜色。")
        return (tuple(color) if color is not None else None,
                self.opacity_spin.value() / 100 if self.opacity_check.isChecked() else None,
                self.scope_combo.currentData())

    def set_busy(self, busy):
        self.busy = busy
        self.controls.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self._update_enabled()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
