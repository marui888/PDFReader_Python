from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


PAGE_LABELS_DEFAULT_TEMPLATE = """[
  {
    "startpage": 0,
    "prefix": "Cover",
    "firstpagenum": 1
  },
  {
    "startpage": 2,
    "prefix": "",
    "firstpagenum": 1,
    "style": "r"
  },
  {
    "startpage": 30,
    "prefix": "",
    "firstpagenum": 1,
    "style": "D"
  }
]"""


class PageLabelsDialog(QDialog):
    def __init__(self, labels_json: str, apply_callback: Callable[[str], bool], parent=None) -> None:
        super().__init__(parent)
        self.apply_callback = apply_callback
        self.setWindowTitle("Page Labels")
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        help_label = QLabel(
            '"D" = decimal numbers: 1, 2, 3\n'
            '"r" = lowercase Roman numerals: i, ii, iii\n'
            '"R" = uppercase Roman numerals: I, II, III\n'
            '"a" = lowercase letters: a, b, c\n'
            '"A" = uppercase letters: A, B, C\n'
            "No style = prefix only"
        )
        help_label.setTextInteractionFlags(help_label.textInteractionFlags())
        layout.addWidget(help_label)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(labels_json)
        self.editor.setMinimumHeight(360)
        layout.addWidget(self.editor, 1)

        button_row = QHBoxLayout()
        self.load_template_button = QPushButton("Load Template")
        button_row.addWidget(self.load_template_button)
        button_row.addStretch(1)
        self.apply_button = QPushButton("Apply")
        self.apply_close_button = QPushButton("Apply and Close")
        self.cancel_button = QPushButton("Cancel")
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.apply_close_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self.load_template_button.clicked.connect(self.load_template)
        self.apply_button.clicked.connect(self.apply)
        self.apply_close_button.clicked.connect(self.apply_and_close)
        self.cancel_button.clicked.connect(self.reject)

    def load_template(self) -> None:
        self.editor.setPlainText(PAGE_LABELS_DEFAULT_TEMPLATE)

    def apply(self) -> bool:
        return bool(self.apply_callback(self.editor.toPlainText()))

    def apply_and_close(self) -> None:
        if self.apply():
            self.accept()
