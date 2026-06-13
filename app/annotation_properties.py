from collections.abc import Callable

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget

from app.models import ANNOTATION_COLORS, AnnotationModel


class AnnotationPropertiesWidget(QWidget):
    def __init__(
        self,
        on_highlight_change: Callable[[tuple[float, float, float], float], None],
        on_highlight_default: Callable[[tuple[float, float, float], float], None],
        on_freetext_change: Callable[[str, int, tuple[float, float, float]], None],
        on_stroked_change: Callable[[tuple[float, float, float], int], None],
    ) -> None:
        super().__init__()
        self.on_highlight_change = on_highlight_change
        self.on_highlight_default = on_highlight_default
        self.on_freetext_change = on_freetext_change
        self.on_stroked_change = on_stroked_change
        self.updating = False
        self.layout = QVBoxLayout(self)
        self.show_empty()

    def set_model(
        self,
        model: AnnotationModel | None,
        *,
        default_highlight_color: tuple[float, float, float],
        default_highlight_opacity: float,
        freetext_font_size_min: int,
        freetext_font_size_max: int,
        default_freetext_font_size: int,
    ) -> None:
        self.updating = True
        try:
            self.clear()
            if model is None:
                self.show_empty()
                return

            self.layout.addWidget(QLabel(f"{model.pdf_type} xref={model.xref}"))
            if not model.is_supported:
                self.layout.addWidget(QLabel("Unsupported annotation type."))
                self.layout.addStretch()
                return

            form = QFormLayout()
            self.layout.addLayout(form)
            if model.app_type == "highlight":
                self.populate_highlight_properties(form, model, default_highlight_color, default_highlight_opacity)
            elif model.app_type == "freetext":
                self.populate_freetext_properties(
                    form,
                    model,
                    freetext_font_size_min,
                    freetext_font_size_max,
                    default_freetext_font_size,
                )
            elif model.app_type in {"square", "arrow"}:
                self.populate_stroked_properties(form, model)
            self.layout.addStretch()
        finally:
            self.updating = False

    def show_empty(self) -> None:
        self.layout.addWidget(QLabel("Select an annotation to edit its properties."))
        self.layout.addStretch()

    def clear(self) -> None:
        self.clear_layout_items(self.layout)

    def clear_layout_items(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            if child_layout is not None:
                self.clear_layout_items(child_layout)
                child_layout.deleteLater()
                continue

            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def populate_highlight_properties(
        self,
        form: QFormLayout,
        model: AnnotationModel,
        default_highlight_color: tuple[float, float, float],
        default_highlight_opacity: float,
    ) -> None:
        color_combo = self.create_color_combo(model.color, "Yellow")
        opacity_spin = QSpinBox()
        opacity_spin.setRange(5, 100)
        opacity_spin.setSuffix("%")
        opacity_spin.setValue(round((model.opacity if model.opacity is not None else default_highlight_opacity) * 100))
        default_check = QCheckBox("Use these values as Highlight default")
        default_check.setChecked(
            self.color_name_for_tuple(model.color) == self.color_name_for_tuple(default_highlight_color)
            and abs((model.opacity or default_highlight_opacity) - default_highlight_opacity) < 0.01
        )

        form.addRow("Color", color_combo)
        form.addRow("Opacity", opacity_spin)
        form.addRow("", default_check)

        def apply() -> None:
            if self.updating:
                return
            color = ANNOTATION_COLORS[color_combo.currentText()]
            opacity = opacity_spin.value() / 100
            self.on_highlight_change(color, opacity)
            if default_check.isChecked():
                self.on_highlight_default(color, opacity)

        color_combo.currentTextChanged.connect(lambda _text: apply())
        opacity_spin.valueChanged.connect(lambda _value: apply())
        default_check.toggled.connect(lambda checked: apply() if checked else None)

    def populate_freetext_properties(
        self,
        form: QFormLayout,
        model: AnnotationModel,
        freetext_font_size_min: int,
        freetext_font_size_max: int,
        default_freetext_font_size: int,
    ) -> None:
        text_edit = QPlainTextEdit()
        text_edit.setPlainText(model.text)
        text_edit.setMinimumHeight(120)

        font_size_spin = QSpinBox()
        font_size_spin.setRange(freetext_font_size_min, freetext_font_size_max)
        font_size = round(model.font_size or default_freetext_font_size)
        font_size_spin.setValue(max(freetext_font_size_min, min(freetext_font_size_max, font_size)))

        color_combo = self.create_color_combo(model.color, "Red")
        form.addRow("Text", text_edit)
        form.addRow("Font size", font_size_spin)
        form.addRow("Color", color_combo)

        def apply() -> None:
            if self.updating:
                return
            text = text_edit.toPlainText()
            if not text.strip():
                return
            self.on_freetext_change(text, font_size_spin.value(), ANNOTATION_COLORS[color_combo.currentText()])

        text_edit.textChanged.connect(apply)
        font_size_spin.valueChanged.connect(lambda _value: apply())
        color_combo.currentTextChanged.connect(lambda _text: apply())

    def populate_stroked_properties(self, form: QFormLayout, model: AnnotationModel) -> None:
        color_combo = self.create_color_combo(model.color, "Red")
        width_spin = QSpinBox()
        width_spin.setRange(1, 10)
        width_spin.setValue(max(1, min(10, int(round(model.border_width or 1)))))
        label = "Border width" if model.app_type == "square" else "Line width"
        form.addRow("Stroke color", color_combo)
        form.addRow(label, width_spin)

        def apply() -> None:
            if self.updating:
                return
            self.on_stroked_change(ANNOTATION_COLORS[color_combo.currentText()], width_spin.value())

        color_combo.currentTextChanged.connect(lambda _text: apply())
        width_spin.valueChanged.connect(lambda _value: apply())

    def create_color_combo(self, color: tuple | None, fallback_name: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(ANNOTATION_COLORS.keys())
        combo.setCurrentText(self.color_name_for_tuple(color) if color else fallback_name)
        return combo

    def color_name_for_tuple(self, color: tuple | None) -> str:
        if not color:
            return "Red"
        best_name = "Red"
        best_distance = float("inf")
        for name, candidate in ANNOTATION_COLORS.items():
            distance = sum((float(color[index]) - candidate[index]) ** 2 for index in range(3))
            if distance < best_distance:
                best_name = name
                best_distance = distance
        return best_name
