import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from math import atan2, cos, pi, sin
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QFileDialog,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


SUPPORTED_APP_TYPES = {"highlight", "freetext", "square", "arrow"}
DRAGGABLE_APP_TYPES = {"freetext", "square", "arrow"}
EDITABLE_APP_TYPES = {"freetext", "square", "arrow"}
ANNOTATION_COLORS = {
    "Red": (1, 0, 0),
    "Black": (0, 0, 0),
    "Blue": (0, 0, 1),
    "Green": (0, 0.55, 0),
    "Yellow": (1, 0.85, 0),
}


@dataclass
class AnnotationModel:
    id: str
    xref: int
    page_index: int
    pdf_type: str
    app_type: str
    rect: fitz.Rect
    text: str
    color: tuple | None
    border_width: float | None
    font_size: float | None
    opacity: float | None
    quad_points: list[tuple[float, float]]
    line_start: tuple[float, float] | None
    line_end: tuple[float, float] | None
    line_ending: str
    is_supported: bool
    dirty: bool = False
    deleted: bool = False
    source: str = "pdf"


class PdfCanvasView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)


class AnnotationScene(QGraphicsScene):
    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(owner)
        self.owner = owner

    def mousePressEvent(self, event) -> None:
        if self.owner.on_tool_mouse_press(event.scenePos()):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.owner.on_tool_mouse_move(event.scenePos()):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.owner.on_tool_mouse_release(event.scenePos()):
            return
        super().mouseReleaseEvent(event)
        self.owner.on_scene_mouse_release()

    def mouseDoubleClickEvent(self, event) -> None:
        super().mouseDoubleClickEvent(event)
        self.owner.on_scene_mouse_double_click()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.doc: fitz.Document | None = None
        self.pdf_path: Path | None = None
        self.page_index = 0
        self.zoom = 1.5
        self.use_foxit_freetext = False
        self.freetext_font_size_min = 4
        self.freetext_font_size_max = 20
        self.default_freetext_font_size = 7
        self.default_highlight_color = (1, 1, 0)
        self.default_highlight_opacity = 0.45
        self.load_app_settings()
        self.is_dirty = False
        self.current_annotations: list[AnnotationModel] = []
        self.annotation_items: list[QGraphicsItem] = []
        self.annotation_item_map: dict[str, list[QGraphicsItem]] = {}
        self.annotation_model_map: dict[str, AnnotationModel] = {}
        self.selection_items: list[QGraphicsItem] = []
        self.selected_annotation_id: str | None = None
        self.annotations_dock: QDockWidget | None = None
        self.annotations_tabs: QTabWidget | None = None
        self.annotations_table: QTableWidget | None = None
        self.properties_page: QWidget | None = None
        self.properties_layout: QVBoxLayout | None = None
        self.updating_page_spin = False
        self.updating_table_selection = False
        self.updating_scene_selection = False
        self.updating_properties_panel = False
        self.applying_property_change = False
        self.active_tool: str | None = None
        self.tool_start_scene_pos: QPointF | None = None
        self.tool_preview_item: QGraphicsItem | None = None
        self.tool_preview_items: list[QGraphicsItem] = []
        self.text_lines_cache_page_index: int | None = None
        self.text_lines_cache: list[list[dict]] | None = None

        self.scene = AnnotationScene(self)
        self.scene.selectionChanged.connect(self.on_scene_selection_changed)
        self.view = PdfCanvasView()
        self.view.setScene(self.scene)
        self.page_item = QGraphicsPixmapItem()
        self.page_item.setZValue(0)
        self.scene.addItem(self.page_item)
        self.setCentralWidget(self.view)

        self.update_window_title()
        self.resize(1200, 850)
        self.create_actions()
        self.create_menus()
        self.create_toolbar()
        self.show_current_page_annotations()
        self.update_actions()

    def create_actions(self) -> None:
        self.open_action = QAction("Open", self)
        self.open_action.triggered.connect(self.open_pdf)

        self.close_action = QAction("Close", self)
        self.close_action.triggered.connect(self.close_pdf)

        self.save_action = QAction("Save", self)
        self.save_action.triggered.connect(self.save)

        self.save_as_action = QAction("Save As", self)
        self.save_as_action.triggered.connect(self.save_as)

        self.settings_action = QAction("Settings...", self)
        self.settings_action.triggered.connect(self.open_settings)

        self.delete_annotation_action = QAction("Delete Annotation", self)
        self.delete_annotation_action.setShortcut("Delete")
        self.delete_annotation_action.triggered.connect(self.delete_selected_annotation)

        self.edit_annotation_action = QAction("Annotation Properties", self)
        self.edit_annotation_action.triggered.connect(self.show_annotation_properties)

        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)

        self.prev_action = QAction("Prev", self)
        self.prev_action.triggered.connect(self.prev_page)

        self.next_action = QAction("Next", self)
        self.next_action.triggered.connect(self.next_page)

        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setEnabled(False)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(self.go_to_page)

        self.page_count_label = QLabel("/ 0")

        self.zoom_out_action = QAction("Zoom -", self)
        self.zoom_out_action.triggered.connect(self.zoom_out)

        self.zoom_in_action = QAction("Zoom +", self)
        self.zoom_in_action.triggered.connect(self.zoom_in)

        self.add_typewriter_action = QAction("Add FreeText", self)
        self.add_typewriter_action.setCheckable(True)
        self.add_typewriter_action.triggered.connect(self.add_typewriter)

        self.add_rectangle_action = QAction("Add Square", self)
        self.add_rectangle_action.setCheckable(True)
        self.add_rectangle_action.triggered.connect(self.add_rectangle)

        self.add_highlight_action = QAction("Add Highlight", self)
        self.add_highlight_action.setCheckable(True)
        self.add_highlight_action.triggered.connect(self.add_highlight)

        self.add_arrow_action = QAction("Add Arrow", self)
        self.add_arrow_action.setCheckable(True)
        self.add_arrow_action.triggered.connect(self.add_arrow)

        self.show_annotations_action = QAction("Current Page Annotations", self)
        self.show_annotations_action.triggered.connect(self.show_current_page_annotations)

    def create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.close_action)
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(self.settings_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction(self.edit_annotation_action)
        edit_menu.addAction(self.delete_annotation_action)

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction(self.show_annotations_action)

    def create_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.prev_action)
        toolbar.addAction(self.next_action)
        toolbar.addWidget(QLabel("Page"))
        toolbar.addWidget(self.page_spin)
        toolbar.addWidget(self.page_count_label)

        toolbar.addSeparator()

        toolbar.addAction(self.zoom_out_action)
        toolbar.addAction(self.zoom_in_action)

        toolbar.addSeparator()

        toolbar.addAction(self.add_typewriter_action)
        toolbar.addAction(self.add_rectangle_action)
        toolbar.addAction(self.add_highlight_action)
        toolbar.addAction(self.add_arrow_action)

    def update_actions(self) -> None:
        has_doc = self.doc is not None
        for action in (
            self.close_action,
            self.save_action,
            self.save_as_action,
            self.prev_action,
            self.next_action,
            self.zoom_out_action,
            self.zoom_in_action,
            self.add_typewriter_action,
            self.add_rectangle_action,
            self.add_highlight_action,
            self.add_arrow_action,
            self.show_annotations_action,
        ):
            action.setEnabled(has_doc)
        self.delete_annotation_action.setEnabled(has_doc and self.selected_annotation_id is not None)
        self.edit_annotation_action.setEnabled(has_doc and self.selected_annotation_id is not None)

        if not has_doc or self.doc is None:
            self.close_action.setEnabled(False)
            self.delete_annotation_action.setEnabled(False)
            self.edit_annotation_action.setEnabled(False)
            self.page_spin.setEnabled(False)
            self.page_spin.setMaximum(1)
            self.page_count_label.setText("/ 0")
            return

        self.prev_action.setEnabled(self.page_index > 0)
        self.next_action.setEnabled(self.page_index < len(self.doc) - 1)
        self.page_spin.setEnabled(True)
        self.page_spin.setMaximum(len(self.doc))
        self.page_count_label.setText(f"/ {len(self.doc)}")

    def open_pdf(self) -> None:
        if not self.confirm_unsaved_changes("open another PDF"):
            return

        file_name, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if not file_name:
            return

        try:
            if self.doc is not None:
                self.doc.close()
            self.doc = fitz.open(file_name)
            self.pdf_path = Path(file_name)
            self.page_index = 0
            self.clear_dirty()
            self.current_annotations = []
            self.render_page()
            self.update_actions()
        except Exception as exc:
            self.show_error("Open failed", exc)

    def close_pdf(self) -> None:
        if not self.confirm_unsaved_changes("close this PDF"):
            return

        self.cancel_add_tool()
        if self.doc is not None:
            self.doc.close()

        self.doc = None
        self.pdf_path = None
        self.page_index = 0
        self.clear_dirty()
        self.current_annotations = []
        self.clear_annotation_items()
        self.page_item.setPixmap(QPixmap())
        self.scene.setSceneRect(0, 0, 0, 0)
        self.sync_page_spin()
        self.update_window_title()
        self.statusBar().showMessage("No PDF open")
        self.refresh_annotations_table()
        self.refresh_properties_panel()
        self.sync_page_spin()
        self.update_actions()

    def sync_page_spin(self) -> None:
        self.updating_page_spin = True
        try:
            if self.doc is None:
                self.page_spin.setValue(1)
            else:
                self.page_spin.setMaximum(len(self.doc))
                self.page_spin.setValue(self.page_index + 1)
        finally:
            self.updating_page_spin = False

    def mark_dirty(self) -> None:
        self.is_dirty = True
        self.update_window_title()

    def clear_dirty(self) -> None:
        self.is_dirty = False
        self.update_window_title()

    def update_window_title(self) -> None:
        dirty_marker = " *" if self.is_dirty else ""
        if self.doc is None:
            self.setWindowTitle(f"PDF Note Reader{dirty_marker}")
            return
        name = self.pdf_path.name if self.pdf_path else "PDF"
        self.setWindowTitle(f"{name} - Page {self.page_index + 1}/{len(self.doc)} - {self.zoom:.0%}{dirty_marker}")

    def confirm_unsaved_changes(self, action_text: str) -> bool:
        if self.doc is None or not self.is_dirty:
            return True

        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            f"Save changes before you {action_text}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Discard:
            return True
        return self.save()

    def go_to_page(self, page_number: int) -> None:
        if self.updating_page_spin or self.doc is None:
            return

        target_index = max(0, min(page_number - 1, len(self.doc) - 1))
        if target_index == self.page_index:
            return

        self.page_index = target_index
        self.cancel_add_tool()
        self.render_page()

    def current_page(self) -> fitz.Page:
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        return self.doc[self.page_index]

    def settings_path(self) -> Path:
        return Path(__file__).with_name("PDFReaderSetting.json")

    def load_app_settings(self) -> None:
        path = self.settings_path()
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return

        self.use_foxit_freetext = bool(data.get("use_foxit_freetext", self.use_foxit_freetext))
        color = data.get("default_highlight_color")
        if isinstance(color, list) and len(color) >= 3:
            try:
                self.default_highlight_color = tuple(max(0.0, min(1.0, float(value))) for value in color[:3])
            except (TypeError, ValueError):
                pass
        try:
            self.freetext_font_size_min = max(1, int(data.get("freetext_font_size_min", self.freetext_font_size_min)))
            self.freetext_font_size_max = max(
                self.freetext_font_size_min,
                int(data.get("freetext_font_size_max", self.freetext_font_size_max)),
            )
            font_size = int(data.get("default_freetext_font_size", self.default_freetext_font_size))
            self.default_freetext_font_size = self.clamp_freetext_font_size(font_size)
            opacity = float(data.get("default_highlight_opacity", self.default_highlight_opacity))
            self.default_highlight_opacity = max(0.05, min(1.0, opacity))
        except (TypeError, ValueError):
            pass

    def save_app_settings(self) -> None:
        data = {
            "default_freetext_font_size": self.default_freetext_font_size,
            "default_highlight_color": list(self.default_highlight_color),
            "default_highlight_opacity": self.default_highlight_opacity,
            "freetext_font_size_min": self.freetext_font_size_min,
            "freetext_font_size_max": self.freetext_font_size_max,
            "use_foxit_freetext": self.use_foxit_freetext,
        }
        self.settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")

    def clamp_freetext_font_size(self, value: int) -> int:
        return max(self.freetext_font_size_min, min(self.freetext_font_size_max, int(value)))

    def render_page(self, preserve_selection: bool = False) -> None:
        if self.doc is None:
            return

        self.text_lines_cache_page_index = None
        self.text_lines_cache = None
        selected_annotation_id = self.selected_annotation_id if preserve_selection else None
        page = self.current_page()
        self.current_annotations = self.load_page_annotations(self.page_index)
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.page_item.setPixmap(QPixmap.fromImage(image))
        self.scene.setSceneRect(self.page_item.boundingRect())
        self.render_annotation_overlay()
        self.view.centerOn(self.page_item)

        supported_count = sum(1 for annot in self.current_annotations if annot.is_supported)
        unsupported_count = len(self.current_annotations) - supported_count
        self.update_window_title()
        self.statusBar().showMessage(
            f"Page {self.page_index + 1}/{len(self.doc)} | "
            f"Annotations: {len(self.current_annotations)} | "
            f"Supported: {supported_count} | Unsupported: {unsupported_count}"
        )
        self.refresh_annotations_table()
        self.sync_page_spin()
        self.update_actions()
        if selected_annotation_id in self.annotation_model_map:
            self.select_annotation(selected_annotation_id)

    def render_annotation_overlay(self) -> None:
        self.clear_annotation_items()
        self.annotation_model_map = {model.id: model for model in self.current_annotations}
        self.selected_annotation_id = None
        for model in self.current_annotations:
            if not model.is_supported:
                continue

            if model.app_type == "highlight":
                self.add_highlight_item(model)
            elif model.app_type == "freetext":
                self.add_freetext_item(model)
            elif model.app_type == "square":
                self.add_square_item(model)
            elif model.app_type == "arrow":
                self.add_arrow_item(model)

    def clear_annotation_items(self) -> None:
        self.clear_selection_items()
        for item in self.annotation_items:
            self.scene.removeItem(item)
        self.annotation_items.clear()
        self.annotation_item_map.clear()
        self.annotation_model_map.clear()
        self.selected_annotation_id = None

    def add_annotation_item(self, item: QGraphicsItem, model: AnnotationModel) -> None:
        item.setZValue(10)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if self.is_draggable_model(model):
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setCursor(Qt.CursorShape.SizeAllCursor)
        item.setData(0, model.id)
        item.setData(1, item.pos())
        self.annotation_items.append(item)
        self.annotation_item_map.setdefault(model.id, []).append(item)

    def add_hit_item(self, item: QGraphicsItem, model: AnnotationModel) -> None:
        item.setZValue(11)
        item.setOpacity(0.01)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if self.is_draggable_model(model):
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setCursor(Qt.CursorShape.SizeAllCursor)
        item.setData(0, model.id)
        item.setData(1, item.pos())
        self.annotation_items.append(item)
        self.annotation_item_map.setdefault(model.id, []).append(item)

    def is_draggable_model(self, model: AnnotationModel) -> bool:
        return model.app_type in DRAGGABLE_APP_TYPES

    def add_highlight_item(self, model: AnnotationModel) -> None:
        color = self.pdf_color(model.color, QColor(255, 235, 59))
        opacity = model.opacity if model.opacity is not None else self.default_highlight_opacity
        color.setAlpha(max(20, min(255, int(255 * opacity))))

        quad_polygons = self.highlight_polygons(model)
        if quad_polygons:
            for polygon in quad_polygons:
                item = self.scene.addPolygon(polygon, QPen(Qt.PenStyle.NoPen), QBrush(color))
                self.add_annotation_item(item, model)
                self.add_highlight_hit_item(polygon, model)
            return

        rect = self.scene_rect(model.rect)
        item = self.scene.addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(color))
        self.add_annotation_item(item, model)
        self.add_rect_hit_item(rect, model)

    def add_freetext_item(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        text_item = self.scene.addText(model.text)
        text_item.setDefaultTextColor(self.pdf_color(model.color, QColor(255, 0, 0)))
        font_size = model.font_size or self.default_freetext_font_size
        text_item.setFont(QFont("Arial", max(1, int(font_size * self.zoom))))
        text_item.setTextWidth(rect.width())
        text_item.setPos(rect.topLeft())
        self.add_annotation_item(text_item, model)
        self.add_rect_hit_item(rect, model)

    def add_square_item(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        color = self.pdf_color(model.color, QColor(255, 0, 0))
        width = max(1.0, (model.border_width or 1.0) * self.zoom)
        item = self.scene.addRect(rect, QPen(color, width), QBrush(Qt.BrushStyle.NoBrush))
        self.add_annotation_item(item, model)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.add_square_hit_item(rect, model)

    def add_arrow_item(self, model: AnnotationModel) -> None:
        start, end = self.arrow_points(model)
        color = self.pdf_color(model.color, QColor(255, 0, 0))
        width = max(1.0, (model.border_width or 1.0) * self.zoom)
        pen = QPen(color, width)
        line_item = self.scene.addLine(QLineF(start, end), pen)
        self.add_annotation_item(line_item, model)
        self.add_line_hit_item(start, end, model)

        start_arrow, end_arrow = self.arrow_head_flags(model.line_ending)
        size = max(8.0, 9.0 * self.zoom)
        if start_arrow:
            self.add_arrow_head_lines(end, start, size, pen, model)
        if end_arrow:
            self.add_arrow_head_lines(start, end, size, pen, model)

    def add_rect_hit_item(self, rect: QRectF, model: AnnotationModel) -> None:
        margin = max(4.0, 4.0 * self.zoom)
        hit_rect = rect.adjusted(-margin, -margin, margin, margin)
        item = self.scene.addRect(hit_rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0)))
        self.add_hit_item(item, model)

    def add_square_hit_item(self, rect: QRectF, model: AnnotationModel) -> None:
        width = max(8.0, ((model.border_width or 1.0) * self.zoom) + 6.0)
        pen = QPen(QColor(0, 0, 0), width)
        lines = (
            QLineF(rect.topLeft(), rect.topRight()),
            QLineF(rect.topRight(), rect.bottomRight()),
            QLineF(rect.bottomRight(), rect.bottomLeft()),
            QLineF(rect.bottomLeft(), rect.topLeft()),
        )
        for line in lines:
            item = self.scene.addLine(line, pen)
            self.add_hit_item(item, model)

    def add_highlight_hit_item(self, polygon: QPolygonF, model: AnnotationModel) -> None:
        margin = max(5.0, 5.0 * self.zoom)
        hit_rect = polygon.boundingRect().adjusted(-margin, -margin, margin, margin)
        item = self.scene.addRect(hit_rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0)))
        self.add_hit_item(item, model)

    def add_line_hit_item(self, start: QPointF, end: QPointF, model: AnnotationModel) -> None:
        width = max(12.0, 10.0 * self.zoom)
        item = self.scene.addLine(QLineF(start, end), QPen(QColor(0, 0, 0), width))
        self.add_hit_item(item, model)

    def scene_rect(self, rect: fitz.Rect) -> QRectF:
        return QRectF(
            rect.x0 * self.zoom,
            rect.y0 * self.zoom,
            rect.width * self.zoom,
            rect.height * self.zoom,
        )

    def scene_point(self, point: tuple[float, float]) -> QPointF:
        return QPointF(point[0] * self.zoom, point[1] * self.zoom)

    def pdf_color(self, color: tuple | None, fallback: QColor) -> QColor:
        if not color:
            return QColor(fallback)
        channels = [max(0, min(255, int(value * 255))) for value in color[:3]]
        if len(channels) < 3:
            return QColor(fallback)
        return QColor(channels[0], channels[1], channels[2])

    def arrow_points(self, model: AnnotationModel) -> tuple[QPointF, QPointF]:
        if model.line_start and model.line_end:
            return self.scene_point(model.line_start), self.scene_point(model.line_end)

        rect = model.rect
        return (
            QPointF(rect.x0 * self.zoom, rect.y0 * self.zoom),
            QPointF(rect.x1 * self.zoom, rect.y1 * self.zoom),
        )

    def highlight_polygons(self, model: AnnotationModel) -> list[QPolygonF]:
        polygons: list[QPolygonF] = []
        points = model.quad_points
        for index in range(0, len(points) - 3, 4):
            quad = points[index : index + 4]
            ordered_quad = (quad[0], quad[1], quad[3], quad[2])
            polygons.append(QPolygonF([self.scene_point(point) for point in ordered_quad]))
        return polygons

    def add_arrow_head_lines(
        self, start: QPointF, end: QPointF, size: float, pen: QPen, model: AnnotationModel
    ) -> None:
        left, right = self.arrow_head_points(start, end, size)
        for point in (left, right):
            item = self.scene.addLine(QLineF(end, point), pen)
            self.add_annotation_item(item, model)

    def arrow_head_points(self, start: QPointF, end: QPointF, size: float) -> tuple[QPointF, QPointF]:
        angle = atan2(end.y() - start.y(), end.x() - start.x())
        half_angle = pi / 12
        left = QPointF(
            end.x() - size * cos(angle - half_angle),
            end.y() - size * sin(angle - half_angle),
        )
        right = QPointF(
            end.x() - size * cos(angle + half_angle),
            end.y() - size * sin(angle + half_angle),
        )
        return left, right

    def arrow_head_flags(self, line_ending: str) -> tuple[bool, bool]:
        endings = re.findall(r"/?([A-Za-z]*Arrow|None)", line_ending)
        if len(endings) >= 2:
            return "Arrow" in endings[0], "Arrow" in endings[1]
        if "Arrow" in line_ending:
            return False, True
        return False, True

    def on_scene_selection_changed(self) -> None:
        if self.updating_scene_selection:
            return

        selected_ids = [item.data(0) for item in self.scene.selectedItems() if item.data(0)]
        if not selected_ids:
            if self.selected_annotation_id is not None:
                self.select_annotation(None)
            return

        annotation_id = str(selected_ids[0])
        if annotation_id != self.selected_annotation_id:
            self.select_annotation(annotation_id)

    def on_scene_mouse_release(self) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None:
            return

        if self.apply_arrow_endpoint_move_if_needed(model):
            return

        if self.apply_rect_resize_if_needed(model):
            return

        if not self.is_draggable_model(model):
            return

        delta = self.annotation_drag_delta(model.id)
        if delta is None:
            return

        dx_pdf = delta.x() / self.zoom
        dy_pdf = delta.y() / self.zoom
        if abs(dx_pdf) < 0.1 and abs(dy_pdf) < 0.1:
            return

        try:
            self.move_pdf_annotation(model, dx_pdf, dy_pdf)
            self.mark_dirty()
            self.render_page(preserve_selection=True)
            self.statusBar().showMessage(f"Moved {model.pdf_type} xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            self.render_page(preserve_selection=True)
            self.show_error("Move annotation failed", exc)

    def apply_rect_resize_if_needed(self, model: AnnotationModel) -> bool:
        if model.app_type not in {"square", "freetext"}:
            return False

        for item in self.selection_items:
            if item.data(2) != "resize-handle" or item.data(3) != model.id:
                continue

            start_pos = item.data(5)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            delta = item.pos() - start_pos
            if abs(delta.x()) < 0.1 and abs(delta.y()) < 0.1:
                continue

            corner = item.data(4)
            dx_pdf = delta.x() / self.zoom
            dy_pdf = delta.y() / self.zoom
            try:
                self.resize_rect_annotation(model, str(corner), dx_pdf, dy_pdf)
                self.mark_dirty()
                self.render_page(preserve_selection=True)
                self.statusBar().showMessage(f"Resized {model.pdf_type} xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.render_page(preserve_selection=True)
                self.show_error("Resize annotation failed", exc)
            return True

        return False

    def apply_arrow_endpoint_move_if_needed(self, model: AnnotationModel) -> bool:
        if model.app_type != "arrow":
            return False

        for item in self.selection_items:
            if item.data(2) != "arrow-endpoint-handle" or item.data(3) != model.id:
                continue

            start_pos = item.data(5)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            delta = item.pos() - start_pos
            if abs(delta.x()) < 0.1 and abs(delta.y()) < 0.1:
                continue

            endpoint = str(item.data(4))
            dx_pdf = delta.x() / self.zoom
            dy_pdf = delta.y() / self.zoom
            try:
                self.move_arrow_endpoint(model, endpoint, dx_pdf, dy_pdf)
                self.mark_dirty()
                self.render_page(preserve_selection=True)
                self.statusBar().showMessage(f"Moved Arrow endpoint xref={model.xref}. Use Save to persist.")
            except Exception as exc:
                self.render_page(preserve_selection=True)
                self.show_error("Move arrow endpoint failed", exc)
            return True

        return False

    def on_scene_mouse_double_click(self) -> None:
        if self.selected_annotation_id is not None:
            self.show_annotation_properties()

    def annotation_drag_delta(self, annotation_id: str) -> QPointF | None:
        for item in self.annotation_item_map.get(annotation_id, []):
            start_pos = item.data(1)
            if start_pos is None:
                start_pos = QPointF(0, 0)
            delta = item.pos() - start_pos
            if abs(delta.x()) >= 0.1 or abs(delta.y()) >= 0.1:
                return delta
        return None

    def move_pdf_annotation(self, model: AnnotationModel, dx: float, dy: float) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        if model.app_type == "freetext":
            new_rect = fitz.Rect(
                model.rect.x0 + dx,
                model.rect.y0 + dy,
                model.rect.x1 + dx,
                model.rect.y1 + dy,
            )
            annot.set_rect(new_rect)
            annot.update()
            return

        if model.app_type == "square":
            new_rect = fitz.Rect(
                model.rect.x0 + dx,
                model.rect.y0 + dy,
                model.rect.x1 + dx,
                model.rect.y1 + dy,
            )
            self.set_square_rect(annot, model, new_rect)
            return

        if model.app_type == "arrow":
            if model.line_start is None or model.line_end is None:
                raise RuntimeError("The selected arrow annotation has no line endpoints.")
            start = (model.line_start[0] + dx, model.line_start[1] + dy)
            end = (model.line_end[0] + dx, model.line_end[1] + dy)
            self.set_line_annotation_points(page, annot, start, end)
            return

        raise RuntimeError(f"Annotation type cannot be moved: {model.app_type}")

    def resize_rect_annotation(self, model: AnnotationModel, corner: str, dx: float, dy: float) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        min_width = 20.0 if model.app_type == "freetext" else 10.0
        min_height = 12.0 if model.app_type == "freetext" else 10.0
        rect = fitz.Rect(model.rect)
        if corner == "top-left":
            rect.x0 = min(rect.x0 + dx, rect.x1 - min_width)
            rect.y0 = min(rect.y0 + dy, rect.y1 - min_height)
        elif corner == "top-right":
            rect.x1 = max(rect.x1 + dx, rect.x0 + min_width)
            rect.y0 = min(rect.y0 + dy, rect.y1 - min_height)
        elif corner == "bottom-right":
            rect.x1 = max(rect.x1 + dx, rect.x0 + min_width)
            rect.y1 = max(rect.y1 + dy, rect.y0 + min_height)
        elif corner == "bottom-left":
            rect.x0 = min(rect.x0 + dx, rect.x1 - min_width)
            rect.y1 = max(rect.y1 + dy, rect.y0 + min_height)
        elif corner == "top":
            rect.y0 = min(rect.y0 + dy, rect.y1 - min_height)
        elif corner == "right":
            rect.x1 = max(rect.x1 + dx, rect.x0 + min_width)
        elif corner == "bottom":
            rect.y1 = max(rect.y1 + dy, rect.y0 + min_height)
        elif corner == "left":
            rect.x0 = min(rect.x0 + dx, rect.x1 - min_width)
        else:
            raise RuntimeError(f"Unknown resize handle: {corner}")

        if model.app_type == "square":
            self.set_square_rect(annot, model, rect)
        elif model.app_type == "freetext":
            self.set_freetext_rect(annot, model, rect)
        else:
            raise RuntimeError(f"Annotation type cannot be resized: {model.app_type}")

    def set_square_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        inset = max(0.0, (model.border_width or 0.0) / 2)
        if rect.width <= inset * 2 or rect.height <= inset * 2:
            annot.set_rect(rect)
        else:
            annot.set_rect(fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset))
        annot.update()

    def set_freetext_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        annot.set_rect(rect)
        annot.update(
            fontsize=model.font_size or self.default_freetext_font_size,
            fontname="helv",
            text_color=(1, 0, 0),
            fill_color=None,
            border_color=None,
        )

    def set_line_annotation_points(
        self,
        page: fitz.Page,
        annot: fitz.Annot,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> None:
        page_height = page.rect.height
        raw = f"[ {start[0]:.4f} {page_height - start[1]:.4f} {end[0]:.4f} {page_height - end[1]:.4f} ]"
        if self.doc is None:
            raise RuntimeError("No PDF is open.")
        self.doc.xref_set_key(annot.xref, "L", raw)
        annot.update()

    def move_arrow_endpoint(self, model: AnnotationModel, endpoint: str, dx: float, dy: float) -> None:
        if model.line_start is None or model.line_end is None:
            raise RuntimeError("The selected arrow annotation has no line endpoints.")

        start = model.line_start
        end = model.line_end
        if endpoint == "start":
            start = (start[0] + dx, start[1] + dy)
        elif endpoint == "end":
            end = (end[0] + dx, end[1] + dy)
        else:
            raise RuntimeError(f"Unknown arrow endpoint: {endpoint}")

        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")
        self.set_line_annotation_points(page, annot, start, end)

    def selected_model_is_editable(self) -> bool:
        if self.selected_annotation_id is None:
            return False
        model = self.annotation_model_map.get(self.selected_annotation_id)
        return model is not None and model.app_type in EDITABLE_APP_TYPES

    def select_annotation(self, annotation_id: str | None, center_on: bool = False) -> None:
        if annotation_id is not None and annotation_id not in self.annotation_model_map:
            return

        self.selected_annotation_id = annotation_id
        self.clear_selection_items()
        self.sync_scene_selection()
        self.sync_table_selection()

        if annotation_id is None:
            self.show_page_status()
            self.update_actions()
            if not self.applying_property_change:
                self.refresh_properties_panel()
            return

        model = self.annotation_model_map[annotation_id]
        if model.is_supported:
            self.draw_selection_for_model(model)
            if center_on:
                self.center_on_annotation(model)

        summary = model.text.strip().replace("\n", " ")
        if len(summary) > 40:
            summary = summary[:40] + "..."
        hint = "Drag to move. Use Save to persist." if self.is_draggable_model(model) else "Highlight cannot be moved."
        if summary:
            self.statusBar().showMessage(f"Selected: {model.pdf_type} xref={model.xref} | {summary} | {hint}")
        else:
            self.statusBar().showMessage(f"Selected: {model.pdf_type} xref={model.xref} | {hint}")
        self.update_actions()
        if not self.applying_property_change:
            self.refresh_properties_panel()

    def clear_selection_items(self) -> None:
        for item in self.selection_items:
            self.scene.removeItem(item)
        self.selection_items.clear()

    def add_selection_item(self, item: QGraphicsItem) -> None:
        item.setZValue(20)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.selection_items.append(item)

    def sync_scene_selection(self) -> None:
        self.updating_scene_selection = True
        try:
            for item in self.annotation_items:
                should_select = bool(self.selected_annotation_id and item.data(0) == self.selected_annotation_id)
                if item.isSelected() != should_select:
                    item.setSelected(should_select)
        finally:
            self.updating_scene_selection = False

    def sync_table_selection(self) -> None:
        if self.annotations_table is None:
            return

        self.updating_table_selection = True
        try:
            self.annotations_table.clearSelection()
            if self.selected_annotation_id is None:
                return
            for row, model in enumerate(self.current_annotations):
                if model.id == self.selected_annotation_id:
                    self.annotations_table.selectRow(row)
                    break
        finally:
            self.updating_table_selection = False

    def on_annotations_table_selection_changed(self) -> None:
        if self.updating_table_selection or self.annotations_table is None:
            return

        rows = self.annotations_table.selectionModel().selectedRows()
        if not rows:
            self.select_annotation(None)
            return

        row = rows[0].row()
        if row < 0 or row >= len(self.current_annotations):
            self.select_annotation(None)
            return

        model = self.current_annotations[row]
        if not model.is_supported:
            self.select_annotation(None)
            self.statusBar().showMessage(f"Unsupported: {model.pdf_type} xref={model.xref}")
            return

        self.select_annotation(model.id, center_on=True)

    def delete_selected_annotation(self) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or not model.is_supported:
            return

        summary = model.text.strip().replace("\n", " ")
        if len(summary) > 60:
            summary = summary[:60] + "..."
        message = f"Delete selected annotation?\n\n{model.pdf_type} xref={model.xref}"
        if summary:
            message += f"\n{summary}"

        reply = QMessageBox.question(
            self,
            "Delete Annotation",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            page = self.current_page()
            annot = self.find_page_annotation_by_xref(page, model.xref)
            if annot is None:
                QMessageBox.warning(self, "Delete Annotation", "The selected annotation was not found on this page.")
                self.render_page()
                return

            page.delete_annot(annot)
            self.mark_dirty()
            self.selected_annotation_id = None
            self.render_page()
            self.statusBar().showMessage(f"Deleted annotation xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            self.show_error("Delete annotation failed", exc)

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        annot = page.first_annot
        while annot is not None:
            if annot.xref == xref:
                return annot
            annot = annot.next
        return None

    def edit_selected_annotation(self) -> None:
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or model.app_type not in EDITABLE_APP_TYPES:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {model.pdf_type} Annotation")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        text_edit: QPlainTextEdit | None = None
        font_size_spin: QSpinBox | None = None
        color_combo: QComboBox | None = None
        width_spin: QSpinBox | None = None

        if model.app_type == "freetext":
            text_edit = QPlainTextEdit()
            text_edit.setPlainText(model.text)
            text_edit.setMinimumSize(360, 160)
            form.addRow("Text", text_edit)

            font_size_spin = QSpinBox()
            font_size_spin.setRange(self.freetext_font_size_min, self.freetext_font_size_max)
            font_size_spin.setValue(self.clamp_freetext_font_size(round(model.font_size or self.default_freetext_font_size)))
            form.addRow("Font size", font_size_spin)
        else:
            color_combo = QComboBox()
            color_combo.addItems(ANNOTATION_COLORS.keys())
            color_combo.setCurrentText(self.color_name_for_tuple(model.color))
            form.addRow("Stroke color", color_combo)

            width_spin = QSpinBox()
            width_spin.setRange(1, 10)
            width_spin.setValue(max(1, min(10, int(round(model.border_width or 1)))))
            label = "Border width" if model.app_type == "square" else "Line width"
            form.addRow(label, width_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            if model.app_type == "freetext":
                if text_edit is None or font_size_spin is None:
                    return
                text = text_edit.toPlainText().strip()
                if not text:
                    QMessageBox.warning(self, "Edit Annotation", "FreeText annotation text cannot be empty.")
                    return
                self.update_freetext_annotation(model, text, font_size_spin.value())
                status_type = "FreeText"
            else:
                if color_combo is None or width_spin is None:
                    return
                self.update_stroked_annotation(model, ANNOTATION_COLORS[color_combo.currentText()], width_spin.value())
                status_type = model.pdf_type
            self.mark_dirty()
            self.render_page(preserve_selection=True)
            self.statusBar().showMessage(f"Edited {status_type} xref={model.xref}. Use Save to persist.")
        except Exception as exc:
            self.render_page(preserve_selection=True)
            self.show_error("Edit annotation failed", exc)

    def update_freetext_annotation(
        self, model: AnnotationModel, text: str, font_size: int, color: tuple[float, float, float] = (1, 0, 0)
    ) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_info(title="PDF Note Reader", content=text)
        annot.set_border(width=0)
        annot.update(fontsize=font_size, fontname="helv", text_color=color, fill_color=None, border_color=None)
        self.normalize_freetext_annotation(annot, font_size, color)

    def normalize_freetext_annotation(
        self, annot: fitz.Annot, font_size: int, color: tuple[float, float, float]
    ) -> None:
        if self.doc is None:
            return

        xref = annot.xref
        annot.set_border(width=0)
        annot.update(fontsize=font_size, fontname="helv", text_color=color, fill_color=None, border_color=None)

        self.doc.xref_set_key(xref, "DS", fitz.get_pdf_str(self.freetext_default_style(font_size, color)))
        self.doc.xref_set_key(xref, "Q", "0")
        self.doc.xref_set_key(xref, "BS", "<</Type/Border/W 0>>")
        self.remove_annotation_keys(xref, ("RC", "BE", "RD"))

    def freetext_default_style(self, font_size: int, color: tuple[float, float, float]) -> str:
        red, green, blue = (max(0, min(255, int(round(channel * 255)))) for channel in color[:3])
        return f"font: 'Helv' ,sans-serif {font_size:.2f}pt;color:#{red:02X}{green:02X}{blue:02X}"

    def update_highlight_annotation(
        self, model: AnnotationModel, color: tuple[float, float, float], opacity: float
    ) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_colors(stroke=color)
        annot.update(opacity=max(0.05, min(1.0, opacity)))

    def update_stroked_annotation(self, model: AnnotationModel, color: tuple[float, float, float], width: int) -> None:
        page = self.current_page()
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.update()

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

    def draw_selection_for_model(self, model: AnnotationModel) -> None:
        if model.app_type == "freetext":
            self.draw_freetext_selection(model)
        elif model.app_type == "square":
            self.draw_square_selection(model)
        elif model.app_type == "highlight":
            self.draw_highlight_selection(model)
        elif model.app_type == "arrow":
            self.draw_arrow_selection(model)

    def draw_rect_selection(self, rect: QRectF, color: QColor, width: float, handles: bool) -> None:
        item = self.scene.addRect(rect, QPen(color, width), QBrush(Qt.BrushStyle.NoBrush))
        self.add_selection_item(item)
        if handles:
            self.add_corner_handles(rect, color)

    def draw_square_selection(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        self.draw_rect_selection(rect, QColor(0, 0, 0), 3.0, handles=False)
        self.add_rect_resize_handles(rect, model)

    def draw_freetext_selection(self, model: AnnotationModel) -> None:
        rect = self.scene_rect(model.rect)
        self.draw_rect_selection(rect, QColor(0, 0, 0), 1.2, handles=False)
        self.add_rect_resize_handles(rect, model)

    def add_corner_handles(self, rect: QRectF, color: QColor) -> None:
        size = max(5.0, 5.0 * self.zoom)
        points = (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())
        for point in points:
            handle_rect = QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            item = self.scene.addRect(handle_rect, QPen(color, 1), QBrush(QColor(255, 255, 255)))
            self.add_selection_item(item)

    def add_rect_resize_handles(self, rect: QRectF, model: AnnotationModel) -> None:
        size = max(6.0, 5.0 * self.zoom)
        handles = (
            ("top-left", rect.topLeft(), Qt.CursorShape.SizeFDiagCursor),
            ("top", QPointF(rect.center().x(), rect.top()), Qt.CursorShape.SizeVerCursor),
            ("top-right", rect.topRight(), Qt.CursorShape.SizeBDiagCursor),
            ("right", QPointF(rect.right(), rect.center().y()), Qt.CursorShape.SizeHorCursor),
            ("bottom-right", rect.bottomRight(), Qt.CursorShape.SizeFDiagCursor),
            ("bottom", QPointF(rect.center().x(), rect.bottom()), Qt.CursorShape.SizeVerCursor),
            ("bottom-left", rect.bottomLeft(), Qt.CursorShape.SizeBDiagCursor),
            ("left", QPointF(rect.left(), rect.center().y()), Qt.CursorShape.SizeHorCursor),
        )
        for name, point, cursor in handles:
            handle_rect = QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            item = self.scene.addRect(handle_rect, QPen(QColor(0, 0, 0), 1), QBrush(QColor(255, 255, 255)))
            item.setZValue(25)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setCursor(cursor)
            item.setData(0, model.id)
            item.setData(2, "resize-handle")
            item.setData(3, model.id)
            item.setData(4, name)
            item.setData(5, item.pos())
            self.selection_items.append(item)

    def draw_highlight_selection(self, model: AnnotationModel) -> None:
        fill = QColor(92, 54, 24, 145)
        border = QPen(QColor(220, 0, 0), 1.0)
        border.setStyle(Qt.PenStyle.DashLine)
        polygons = self.highlight_polygons(model)
        if polygons:
            for polygon in polygons:
                item = self.scene.addPolygon(polygon, border, QBrush(fill))
                self.add_selection_item(item)
            return

        item = self.scene.addRect(self.scene_rect(model.rect), border, QBrush(fill))
        self.add_selection_item(item)

    def draw_arrow_selection(self, model: AnnotationModel) -> None:
        start, end = self.arrow_points(model)
        color = self.pdf_color(model.color, QColor(255, 0, 0))
        base_width = max(1.0, (model.border_width or 1.0) * self.zoom)
        pen = QPen(color, base_width + max(1.0, 1.0 * self.zoom))
        item = self.scene.addLine(QLineF(start, end), pen)
        self.add_selection_item(item)

        start_arrow, end_arrow = self.arrow_head_flags(model.line_ending)
        size = max(8.0, 9.0 * self.zoom)
        if start_arrow:
            self.add_selection_arrow_head_lines(end, start, size, pen)
        if end_arrow:
            self.add_selection_arrow_head_lines(start, end, size, pen)
        self.add_endpoint_handles(start, end, QColor(0, 0, 0))

    def add_selection_arrow_head_lines(self, start: QPointF, end: QPointF, size: float, pen: QPen) -> None:
        left, right = self.arrow_head_points(start, end, size)
        for point in (left, right):
            item = self.scene.addLine(QLineF(end, point), pen)
            self.add_selection_item(item)

    def add_endpoint_handles(self, start: QPointF, end: QPointF, color: QColor) -> None:
        size = max(5.0, 5.0 * self.zoom)
        if self.selected_annotation_id is None:
            return

        for name, point in (("start", start), ("end", end)):
            rect = QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            item = self.scene.addRect(rect, QPen(color, 1), QBrush(QColor(255, 255, 255)))
            item.setZValue(25)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setCursor(Qt.CursorShape.SizeAllCursor)
            item.setData(0, self.selected_annotation_id)
            item.setData(2, "arrow-endpoint-handle")
            item.setData(3, self.selected_annotation_id)
            item.setData(4, name)
            item.setData(5, item.pos())
            self.selection_items.append(item)

    def center_on_annotation(self, model: AnnotationModel) -> None:
        if model.app_type == "arrow":
            start, end = self.arrow_points(model)
            self.view.centerOn((start + end) / 2)
            return
        self.view.centerOn(self.scene_rect(model.rect).center())

    def show_page_status(self) -> None:
        if self.doc is None:
            self.statusBar().showMessage("No PDF open")
            return

        supported_count = sum(1 for annot in self.current_annotations if annot.is_supported)
        unsupported_count = len(self.current_annotations) - supported_count
        self.statusBar().showMessage(
            f"Page {self.page_index + 1}/{len(self.doc)} | "
            f"Annotations: {len(self.current_annotations)} | "
            f"Supported: {supported_count} | Unsupported: {unsupported_count}"
        )

    def load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        if self.doc is None:
            return []

        page = self.doc[page_index]
        models: list[AnnotationModel] = []
        annot = page.first_annot
        while annot is not None:
            models.append(self.annotation_to_model(page_index, annot))
            annot = annot.next
        return models

    def annotation_to_model(self, page_index: int, annot: fitz.Annot) -> AnnotationModel:
        pdf_type = annot.type[1] if annot.type and len(annot.type) > 1 else str(annot.type[0])
        app_type = self.classify_annotation(annot, pdf_type)
        color = self.annotation_color(annot)
        border_width = self.annotation_border_width(annot)
        font_size = self.annotation_font_size(annot)
        opacity = self.annotation_opacity(annot)
        quad_points = self.annotation_quad_points(annot)
        line_start, line_end = self.annotation_line_points(annot)
        line_ending = self.annotation_line_ending(annot)
        return AnnotationModel(
            id=f"p{page_index + 1}-xref{annot.xref}",
            xref=annot.xref,
            page_index=page_index,
            pdf_type=pdf_type,
            app_type=app_type,
            rect=fitz.Rect(annot.rect),
            text=self.annotation_text(annot),
            color=color,
            border_width=border_width,
            font_size=font_size,
            opacity=opacity,
            quad_points=quad_points,
            line_start=line_start,
            line_end=line_end,
            line_ending=line_ending,
            is_supported=app_type in SUPPORTED_APP_TYPES,
        )

    def classify_annotation(self, annot: fitz.Annot, pdf_type: str) -> str:
        if pdf_type == "Highlight":
            return "highlight"
        if pdf_type == "FreeText":
            return "freetext"
        if pdf_type == "Square":
            return "square"
        if pdf_type == "Line" and "Arrow" in self.annotation_line_ending(annot):
            return "arrow"
        return "unsupported"

    def annotation_text(self, annot: fitz.Annot) -> str:
        info = annot.info or {}
        return info.get("content") or info.get("subject") or ""

    def annotation_color(self, annot: fitz.Annot) -> tuple | None:
        colors = annot.colors or {}
        return colors.get("stroke") or colors.get("fill")

    def annotation_border_width(self, annot: fitz.Annot) -> float | None:
        border = annot.border or {}
        width = border.get("width")
        return float(width) if width is not None else None

    def annotation_font_size(self, annot: fitz.Annot) -> float | None:
        if self.doc is None or not annot.xref:
            return None
        try:
            key_type, value = self.doc.xref_get_key(annot.xref, "DA")
        except Exception:
            return None
        if key_type == "null" or not value:
            return None
        match = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)\s+Tf(?:\s|$)", value)
        if not match:
            return None
        return float(match.group(1))

    def annotation_opacity(self, annot: fitz.Annot) -> float | None:
        try:
            opacity = getattr(annot, "opacity", None)
        except Exception:
            return None
        if opacity is None or opacity < 0:
            return None
        return max(0.0, min(1.0, float(opacity)))

    def annotation_quad_points(self, annot: fitz.Annot) -> list[tuple[float, float]]:
        vertices = getattr(annot, "vertices", None)
        if not vertices:
            return []
        return [self.point_xy(point) for point in vertices]

    def annotation_line_points(self, annot: fitz.Annot) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
        vertices = getattr(annot, "vertices", None)
        if not vertices or len(vertices) < 2:
            return None, None
        start = vertices[0]
        end = vertices[-1]
        return self.point_xy(start), self.point_xy(end)

    def point_xy(self, point) -> tuple[float, float]:
        if hasattr(point, "x") and hasattr(point, "y"):
            return float(point.x), float(point.y)
        return float(point[0]), float(point[1])

    def annotation_line_ending(self, annot: fitz.Annot) -> str:
        if self.doc is None or not annot.xref:
            return ""
        try:
            value = self.doc.xref_get_key(annot.xref, "LE")
        except Exception:
            return ""
        if not value or len(value) < 2:
            return ""
        return str(value[1])

    def show_current_page_annotations(self) -> None:
        if self.annotations_dock is None:
            self.annotations_dock = QDockWidget("Current Page Annotations", self)
            self.annotations_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.annotations_dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetClosable
                | QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )

            self.annotations_tabs = QTabWidget()
            self.annotations_tabs.setTabPosition(QTabWidget.TabPosition.East)

            self.annotations_table = QTableWidget(0, 6)
            self.annotations_table.setHorizontalHeaderLabels(("Status", "Type", "xref", "Content", "Rect", "Note"))
            self.annotations_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.annotations_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.annotations_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self.annotations_table.setStyleSheet(
                "QTableWidget::item:selected { background-color: #2f6fed; color: white; }"
            )
            self.annotations_table.itemSelectionChanged.connect(self.on_annotations_table_selection_changed)

            self.properties_page = QWidget()
            self.properties_layout = QVBoxLayout(self.properties_page)
            self.properties_layout.addWidget(QLabel("Select an annotation to edit its properties."))
            self.properties_layout.addStretch()

            self.annotations_tabs.addTab(self.annotations_table, "Annotation List")
            self.annotations_tabs.addTab(self.properties_page, "Properties")
            self.annotations_dock.setWidget(self.annotations_tabs)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.annotations_dock)

        self.annotations_dock.show()
        self.annotations_dock.raise_()
        self.refresh_annotations_table()
        self.refresh_properties_panel()

    def show_annotation_properties(self) -> None:
        self.show_current_page_annotations()
        if self.annotations_tabs is not None:
            self.annotations_tabs.setCurrentIndex(1)

    def clear_properties_layout(self) -> None:
        if self.properties_layout is None:
            return
        self.clear_layout_items(self.properties_layout)

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
                continue

            spacer = item.spacerItem()
            if spacer is not None:
                continue

    def refresh_properties_panel(self) -> None:
        if self.properties_layout is None:
            return

        self.updating_properties_panel = True
        try:
            self.clear_properties_layout()
            if self.selected_annotation_id is None:
                self.properties_layout.addWidget(QLabel("Select an annotation to edit its properties."))
                self.properties_layout.addStretch()
                return

            model = self.annotation_model_map.get(self.selected_annotation_id)
            if model is None:
                self.properties_layout.addWidget(QLabel("Selected annotation is not available."))
                self.properties_layout.addStretch()
                return

            self.properties_layout.addWidget(QLabel(f"{model.pdf_type} xref={model.xref}"))
            if not model.is_supported:
                self.properties_layout.addWidget(QLabel("Unsupported annotation type."))
                self.properties_layout.addStretch()
                return

            form = QFormLayout()
            self.properties_layout.addLayout(form)
            if model.app_type == "highlight":
                self.populate_highlight_properties(form, model)
            elif model.app_type == "freetext":
                self.populate_freetext_properties(form, model)
            elif model.app_type in {"square", "arrow"}:
                self.populate_stroked_properties(form, model)
            self.properties_layout.addStretch()
        finally:
            self.updating_properties_panel = False

    def populate_highlight_properties(self, form: QFormLayout, model: AnnotationModel) -> None:
        color_combo = self.create_color_combo(model.color, "Yellow")
        opacity_spin = QSpinBox()
        opacity_spin.setRange(5, 100)
        opacity_spin.setSuffix("%")
        opacity_spin.setValue(round((model.opacity if model.opacity is not None else self.default_highlight_opacity) * 100))
        default_check = QCheckBox("Use these values as Highlight default")
        default_check.setChecked(
            self.color_name_for_tuple(model.color) == self.color_name_for_tuple(self.default_highlight_color)
            and abs((model.opacity or self.default_highlight_opacity) - self.default_highlight_opacity) < 0.01
        )

        form.addRow("Color", color_combo)
        form.addRow("Opacity", opacity_spin)
        form.addRow("", default_check)

        def apply() -> None:
            color = ANNOTATION_COLORS[color_combo.currentText()]
            opacity = opacity_spin.value() / 100
            self.apply_property_change(
                lambda selected: self.update_highlight_annotation(selected, color, opacity),
                f"Edited Highlight xref={model.xref}. Use Save to persist.",
            )
            if default_check.isChecked():
                self.default_highlight_color = color
                self.default_highlight_opacity = opacity
                self.save_app_settings()

        color_combo.currentTextChanged.connect(lambda _text: apply())
        opacity_spin.valueChanged.connect(lambda _value: apply())
        default_check.toggled.connect(lambda checked: apply() if checked else None)

    def populate_freetext_properties(self, form: QFormLayout, model: AnnotationModel) -> None:
        text_edit = QPlainTextEdit()
        text_edit.setPlainText(model.text)
        text_edit.setMinimumHeight(120)

        font_size_spin = QSpinBox()
        font_size_spin.setRange(self.freetext_font_size_min, self.freetext_font_size_max)
        font_size_spin.setValue(self.clamp_freetext_font_size(round(model.font_size or self.default_freetext_font_size)))

        color_combo = self.create_color_combo(model.color, "Red")
        form.addRow("Text", text_edit)
        form.addRow("Font size", font_size_spin)
        form.addRow("Color", color_combo)

        def apply() -> None:
            text = text_edit.toPlainText()
            if not text.strip():
                return
            color = ANNOTATION_COLORS[color_combo.currentText()]
            self.apply_property_change(
                lambda selected: self.update_freetext_annotation(selected, text, font_size_spin.value(), color),
                f"Edited FreeText xref={model.xref}. Use Save to persist.",
            )

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
            color = ANNOTATION_COLORS[color_combo.currentText()]
            self.apply_property_change(
                lambda selected: self.update_stroked_annotation(selected, color, width_spin.value()),
                f"Edited {model.pdf_type} xref={model.xref}. Use Save to persist.",
            )

        color_combo.currentTextChanged.connect(lambda _text: apply())
        width_spin.valueChanged.connect(lambda _value: apply())

    def create_color_combo(self, color: tuple | None, fallback_name: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(ANNOTATION_COLORS.keys())
        combo.setCurrentText(self.color_name_for_tuple(color) if color else fallback_name)
        return combo

    def apply_property_change(self, callback, status: str) -> None:
        if self.updating_properties_panel or self.applying_property_change:
            return
        if self.doc is None or self.selected_annotation_id is None:
            return

        model = self.annotation_model_map.get(self.selected_annotation_id)
        if model is None or not model.is_supported:
            return

        try:
            self.applying_property_change = True
            callback(model)
            self.mark_dirty()
            self.render_page(preserve_selection=True)
            self.statusBar().showMessage(status)
        except Exception as exc:
            self.render_page(preserve_selection=True)
            self.show_error("Edit annotation failed", exc)
        finally:
            self.applying_property_change = False

    def refresh_annotations_table(self) -> None:
        if self.annotations_table is None:
            return

        table = self.annotations_table
        self.updating_table_selection = True
        table.setRowCount(len(self.current_annotations))
        try:
            for row, model in enumerate(self.current_annotations):
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
                    table.setItem(row, column, item)
        finally:
            self.updating_table_selection = False

        table.resizeColumnsToContents()
        self.sync_table_selection()

    def rect_text(self, rect: fitz.Rect) -> str:
        return f"({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f})"

    def annotation_note(self, model: AnnotationModel) -> str:
        if model.app_type == "arrow":
            return f"start={model.line_start}, end={model.line_end}, LE={model.line_ending}"
        if not model.is_supported:
            return "unsupported annotation type"
        return ""

    def select_annotation_by_xref(self, xref: int) -> None:
        self.render_page()
        for model in self.current_annotations:
            if model.xref == xref:
                self.select_annotation(model.id, center_on=True)
                return

    def begin_add_tool(self, tool: str) -> None:
        if self.doc is None:
            return
        if self.active_tool == tool:
            self.cancel_add_tool()
            self.show_page_status()
            return

        self.set_active_tool(tool)

    def set_active_tool(self, tool: str | None) -> None:
        self.remove_tool_preview()
        self.active_tool = tool
        self.tool_start_scene_pos = None
        self.add_typewriter_action.setChecked(tool == "freetext")
        self.add_rectangle_action.setChecked(tool == "square")
        self.add_highlight_action.setChecked(tool == "highlight")
        self.add_arrow_action.setChecked(tool == "arrow")

        if tool is None:
            self.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        if tool == "freetext":
            self.statusBar().showMessage("Click on the page to add FreeText. Press Esc to cancel.")
        elif tool == "highlight":
            self.statusBar().showMessage("Drag over text to add Highlight. Press Esc to cancel.")
        else:
            self.statusBar().showMessage(f"Drag on the page to add {tool}. Press Esc to cancel.")

    def cancel_add_tool(self) -> None:
        self.remove_tool_preview()
        self.set_active_tool(None)

    def on_tool_mouse_press(self, scene_pos: QPointF) -> bool:
        if self.active_tool is None or self.doc is None:
            return False
        self.clear_selection_items()
        start = self.clamp_scene_pos_to_page(scene_pos)
        if self.active_tool == "freetext":
            try:
                self.create_freetext_annotation_at_point(self.pdf_point_from_scene_point(start))
                self.set_active_tool("freetext")
            except Exception as exc:
                self.show_error("Add FreeText failed", exc)
            return True

        self.tool_start_scene_pos = start
        self.update_tool_preview(self.tool_start_scene_pos)
        return True

    def on_tool_mouse_move(self, scene_pos: QPointF) -> bool:
        if self.active_tool is None or self.tool_start_scene_pos is None:
            return False
        self.update_tool_preview(self.clamp_scene_pos_to_page(scene_pos))
        return True

    def on_tool_mouse_release(self, scene_pos: QPointF) -> bool:
        if self.active_tool is None or self.tool_start_scene_pos is None or self.doc is None:
            return False

        tool = self.active_tool
        start = self.tool_start_scene_pos
        end = self.clamp_scene_pos_to_page(scene_pos)
        self.remove_tool_preview()
        self.tool_start_scene_pos = None

        try:
            if tool == "square":
                rect = self.pdf_rect_from_scene_points(start, end)
                min_width = 10.0
                min_height = 10.0
                if rect.width < min_width or rect.height < min_height:
                    self.statusBar().showMessage(f"{tool} area is too small.")
                    return True
                self.create_square_annotation(rect)
            elif tool == "highlight":
                start_pdf = self.pdf_point_from_scene_point(start)
                end_pdf = self.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 1 and abs(end_pdf[1] - start_pdf[1]) < 1:
                    self.statusBar().showMessage("Highlight area is too small.")
                    return True
                self.create_highlight_annotation_from_text_flow(start_pdf, end_pdf)
            elif tool == "arrow":
                start_pdf = self.pdf_point_from_scene_point(start)
                end_pdf = self.pdf_point_from_scene_point(end)
                if abs(end_pdf[0] - start_pdf[0]) < 10 and abs(end_pdf[1] - start_pdf[1]) < 10:
                    self.statusBar().showMessage("Arrow is too short.")
                    return True
                self.create_arrow_annotation(start_pdf, end_pdf)
            self.set_active_tool(tool)
        except Exception as exc:
            self.show_error(f"Add {tool} failed", exc)
        return True

    def update_tool_preview(self, scene_pos: QPointF) -> None:
        if self.active_tool is None or self.tool_start_scene_pos is None:
            return

        self.remove_tool_preview()
        if self.active_tool == "highlight":
            self.update_highlight_tool_preview(scene_pos)
            return

        pen = QPen(QColor(0, 0, 0), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        if self.active_tool == "square":
            rect = QRectF(self.tool_start_scene_pos, scene_pos).normalized()
            self.tool_preview_item = self.scene.addRect(rect, pen, QBrush(Qt.BrushStyle.NoBrush))
        elif self.active_tool == "arrow":
            self.tool_preview_item = self.scene.addLine(QLineF(self.tool_start_scene_pos, scene_pos), pen)
        if self.tool_preview_item is not None:
            self.tool_preview_item.setZValue(30)

    def update_highlight_tool_preview(self, scene_pos: QPointF) -> None:
        if self.tool_start_scene_pos is None or self.doc is None:
            return

        start_pdf = self.pdf_point_from_scene_point(self.tool_start_scene_pos)
        end_pdf = self.pdf_point_from_scene_point(scene_pos)
        rects = self.highlight_rects_from_text_flow(self.current_page(), start_pdf, end_pdf)
        if not rects:
            return

        brush = QBrush(QColor(255, 235, 59, 85))
        for rect in rects:
            item = self.scene.addRect(self.scene_rect(rect), QPen(Qt.PenStyle.NoPen), brush)
            item.setZValue(30)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            self.tool_preview_items.append(item)

    def remove_tool_preview(self) -> None:
        if self.tool_preview_item is not None:
            self.scene.removeItem(self.tool_preview_item)
            self.tool_preview_item = None
        for item in self.tool_preview_items:
            self.scene.removeItem(item)
        self.tool_preview_items.clear()

    def clamp_scene_pos_to_page(self, scene_pos: QPointF) -> QPointF:
        rect = self.page_item.boundingRect()
        return QPointF(
            max(rect.left(), min(scene_pos.x(), rect.right())),
            max(rect.top(), min(scene_pos.y(), rect.bottom())),
        )

    def pdf_point_from_scene_point(self, scene_pos: QPointF) -> tuple[float, float]:
        return scene_pos.x() / self.zoom, scene_pos.y() / self.zoom

    def pdf_rect_from_scene_points(self, start: QPointF, end: QPointF) -> fitz.Rect:
        x0, y0 = self.pdf_point_from_scene_point(start)
        x1, y1 = self.pdf_point_from_scene_point(end)
        return fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def create_freetext_annotation_at_point(self, point: tuple[float, float]) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Add FreeText", "Text:")
        if not ok or not text.strip():
            self.show_page_status()
            return

        rect = self.default_freetext_rect(point, text.strip(), self.default_freetext_font_size)
        self.create_freetext_annotation(rect, text.strip())

    def default_freetext_rect(self, point: tuple[float, float], text: str, font_size: int) -> fitz.Rect:
        page_rect = self.current_page().rect
        width = min(260.0, max(20.0, page_rect.width))
        line_count = max(1, len(text.splitlines()))
        height = max(40.0, line_count * font_size * 1.6 + 16.0)
        height = min(height, max(12.0, page_rect.height))
        x0 = min(max(page_rect.x0, point[0]), page_rect.x1 - width)
        y0 = min(max(page_rect.y0, point[1]), page_rect.y1 - height)
        return fitz.Rect(x0, y0, x0 + width, y0 + height)

    def create_freetext_annotation(self, rect: fitz.Rect, text: str) -> None:
        page = self.current_page()
        annot = page.add_freetext_annot(
            rect,
            text,
            fontsize=self.default_freetext_font_size,
            fontname="helv",
            text_color=(1, 0, 0),
            fill_color=None,
            border_color=None,
        )
        annot.set_info(title="PDF Note Reader", content=text)
        annot.update()
        if self.use_foxit_freetext:
            self.apply_foxit_freetext_keys(annot)
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)

    def create_square_annotation(self, rect: fitz.Rect) -> None:
        page = self.current_page()
        annot = page.add_rect_annot(rect)
        annot.set_colors(stroke=(1, 0, 0))
        annot.set_border(width=2)
        annot.set_info(title="PDF Note Reader", content="Rectangle annotation")
        annot.update()
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)

    def create_arrow_annotation(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        page = self.current_page()
        annot = page.add_line_annot(start, end)
        annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        annot.set_colors(stroke=(1, 0, 0))
        annot.set_border(width=2)
        annot.set_info(title="PDF Note Reader", content="Arrow annotation")
        annot.update()
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)

    def create_highlight_annotation_from_text_flow(
        self, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> None:
        page = self.current_page()
        rects = self.highlight_rects_from_text_flow(page, start_point, end_point)
        if not rects:
            self.statusBar().showMessage("No text found in highlight area.")
            return

        annot = page.add_highlight_annot(rects)
        annot.set_colors(stroke=self.default_highlight_color)
        annot.set_info(title="PDF Note Reader", content="Highlight annotation")
        annot.update(opacity=self.default_highlight_opacity)
        self.mark_dirty()
        self.select_annotation_by_xref(annot.xref)

    def highlight_rects_from_text_flow(
        self, page: fitz.Page, start_point: tuple[float, float], end_point: tuple[float, float]
    ) -> list[fitz.Rect]:
        lines = self.current_page_text_lines(page)
        if not lines:
            return []

        start_position = self.text_position_from_point(lines, start_point)
        end_position = self.text_position_from_point(lines, end_point)
        if start_position is None or end_position is None:
            return []

        start_line, start_offset = start_position
        end_line, end_offset = end_position
        if (end_line, end_offset) < (start_line, start_offset):
            start_line, start_offset, end_line, end_offset = end_line, end_offset, start_line, start_offset

        rects: list[fitz.Rect] = []
        for line_index in range(start_line, end_line + 1):
            chars = lines[line_index]
            if not chars:
                continue

            begin = start_offset if line_index == start_line else 0
            finish = end_offset if line_index == end_line else len(chars)
            begin = max(0, min(begin, len(chars)))
            finish = max(0, min(finish, len(chars)))
            if finish <= begin:
                continue

            selected_boxes = [char["bbox"] for char in chars[begin:finish]]
            selected_chars = chars[begin:finish]
            rects.append(self.highlight_rect_from_selected_chars(selected_chars, selected_boxes))
        return rects

    def current_page_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        if self.text_lines_cache_page_index != self.page_index or self.text_lines_cache is None:
            self.text_lines_cache = self.extract_text_lines(page)
            self.text_lines_cache_page_index = self.page_index
        return self.text_lines_cache

    def highlight_rect_from_selected_chars(self, selected_chars: list[dict], selected_boxes: list[fitz.Rect]) -> fitz.Rect:
        line_rect = fitz.Rect(selected_boxes[0])
        for box in selected_boxes[1:]:
            line_rect |= box

        metric_rect = self.highlight_metric_rect(selected_chars, line_rect)
        if metric_rect is not None:
            return metric_rect
        return self.tighten_highlight_rect(line_rect)

    def highlight_metric_rect(self, selected_chars: list[dict], line_rect: fitz.Rect) -> fitz.Rect | None:
        metric_chars = [
            char
            for char in selected_chars
            if char.get("origin") is not None
            and char.get("size") is not None
            and char.get("ascender") is not None
            and char.get("descender") is not None
        ]
        if not metric_chars:
            return None

        top_values: list[float] = []
        bottom_values: list[float] = []
        for char in metric_chars:
            _, baseline_y = char["origin"]
            size = float(char["size"])
            ascender = float(char["ascender"])
            descender = abs(float(char["descender"]))
            top_values.append(baseline_y - size * ascender * 0.62)
            bottom_values.append(baseline_y + size * descender * 0.75)

        top = sum(top_values) / len(top_values)
        bottom = sum(bottom_values) / len(bottom_values)
        if bottom <= top:
            return None
        return fitz.Rect(line_rect.x0, top, line_rect.x1, bottom)

    def tighten_highlight_rect(self, rect: fitz.Rect) -> fitz.Rect:
        height = rect.height
        top = rect.y0 + height * 0.18
        bottom = rect.y1 - height * 0.14
        if bottom <= top:
            return rect
        return fitz.Rect(rect.x0, top, rect.x1, bottom)

    def extract_text_lines(self, page: fitz.Page) -> list[list[dict]]:
        raw = page.get_text("rawdict")
        lines: list[list[dict]] = []
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                chars: list[dict] = []
                for span in line.get("spans", []):
                    span_size = span.get("size")
                    span_origin = span.get("origin")
                    span_ascender = span.get("ascender")
                    span_descender = span.get("descender")
                    for char in span.get("chars", []):
                        bbox = fitz.Rect(char.get("bbox"))
                        chars.append(
                            {
                                "text": char.get("c", ""),
                                "bbox": bbox,
                                "origin": char.get("origin", span_origin),
                                "size": span_size,
                                "ascender": span_ascender,
                                "descender": span_descender,
                            }
                        )
                if chars:
                    chars.sort(key=lambda item: item["bbox"].x0)
                    lines.append(chars)
        return lines

    def text_position_from_point(
        self, lines: list[list[dict]], point: tuple[float, float]
    ) -> tuple[int, int] | None:
        if not lines:
            return None

        px, py = point
        best_line_index = 0
        best_distance = float("inf")
        for index, chars in enumerate(lines):
            line_rect = fitz.Rect(chars[0]["bbox"])
            for char in chars[1:]:
                line_rect |= char["bbox"]
            if line_rect.y0 <= py <= line_rect.y1:
                distance = 0.0
            elif py < line_rect.y0:
                distance = line_rect.y0 - py
            else:
                distance = py - line_rect.y1
            if distance < best_distance:
                best_line_index = index
                best_distance = distance

        chars = lines[best_line_index]
        if px <= chars[0]["bbox"].x0:
            return best_line_index, 0
        if px >= chars[-1]["bbox"].x1:
            return best_line_index, len(chars)

        best_offset = 0
        best_x_distance = float("inf")
        for index, char in enumerate(chars):
            bbox = char["bbox"]
            center_x = (bbox.x0 + bbox.x1) / 2
            offset = index if px < center_x else index + 1
            if bbox.x0 <= px <= bbox.x1:
                return best_line_index, offset
            distance = min(abs(px - bbox.x0), abs(px - bbox.x1), abs(px - center_x))
            if distance < best_x_distance:
                best_offset = offset
                best_x_distance = distance
        return best_line_index, best_offset

    def page_center_rect(self, width: float, height: float) -> fitz.Rect:
        page = self.current_page()
        rect = page.rect
        x0 = rect.x0 + (rect.width - width) / 2
        y0 = rect.y0 + (rect.height - height) / 2
        return fitz.Rect(x0, y0, x0 + width, y0 + height)

    def set_foxit_freetext(self, checked: bool) -> None:
        self.use_foxit_freetext = checked

    def open_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")

        layout = QVBoxLayout(dialog)
        foxit_checkbox = QCheckBox("Experimental Foxit Typewriter compatibility")
        foxit_checkbox.setChecked(self.use_foxit_freetext)
        layout.addWidget(foxit_checkbox)

        form = QFormLayout()
        font_min_spin = QSpinBox()
        font_min_spin.setRange(1, 72)
        font_min_spin.setValue(self.freetext_font_size_min)
        form.addRow("FreeText font size min", font_min_spin)

        font_max_spin = QSpinBox()
        font_max_spin.setRange(1, 72)
        font_max_spin.setValue(self.freetext_font_size_max)
        form.addRow("FreeText font size max", font_max_spin)

        font_size_spin = QSpinBox()
        font_size_spin.setRange(self.freetext_font_size_min, self.freetext_font_size_max)
        font_size_spin.setValue(self.default_freetext_font_size)
        form.addRow("Default FreeText font size", font_size_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_foxit_freetext(foxit_checkbox.isChecked())
            self.freetext_font_size_min = max(1, font_min_spin.value())
            self.freetext_font_size_max = max(self.freetext_font_size_min, font_max_spin.value())
            self.default_freetext_font_size = self.clamp_freetext_font_size(font_size_spin.value())
            try:
                self.save_app_settings()
                if self.doc is not None:
                    self.render_page(preserve_selection=True)
            except Exception as exc:
                self.show_error("Save settings failed", exc)

    def pdf_date_now(self) -> str:
        now = datetime.now().astimezone()
        offset = now.strftime("%z")
        pdf_offset = "Z"
        if offset:
            pdf_offset = f"{offset[:3]}'{offset[3:]}'"
        return now.strftime("D:%Y%m%d%H%M%S") + pdf_offset

    def apply_foxit_freetext_keys(self, annot: fitz.Annot) -> None:
        if self.doc is None:
            return

        xref = annot.xref
        self.doc.xref_set_key(xref, "IT", "/FreeTextTypewriter")
        self.doc.xref_set_key(xref, "Subj", fitz.get_pdf_str("打字机"))
        self.doc.xref_set_key(xref, "CreationDate", fitz.get_pdf_str(self.pdf_date_now()))
        self.remove_annotation_keys(xref, ("CL", "RD"))

    def remove_annotation_keys(self, xref: int, keys: tuple[str, ...]) -> None:
        if self.doc is None:
            return

        source = self.doc.xref_object(xref, compressed=False)
        for key in keys:
            source = re.sub(
                rf"\n\s*/{re.escape(key)}\s+"
                r"(?:"
                r"<<.*?>>"
                r"|\[[^\]]*\]"
                r"|<[^<][^>]*>"
                r"|\([^)]*\)"
                r"|/[^\s<>\[\]()/]+"
                r"|-?\d+(?:\.\d+)?"
                r"|null"
                r")\s*",
                "\n",
                source,
                flags=re.DOTALL,
            )
        self.doc.update_object(xref, source)

    def add_typewriter(self) -> None:
        self.begin_add_tool("freetext")

    def add_rectangle(self) -> None:
        self.begin_add_tool("square")

    def add_highlight(self) -> None:
        self.begin_add_tool("highlight")

    def add_arrow(self) -> None:
        self.begin_add_tool("arrow")

    def save(self) -> bool:
        if self.doc is None:
            return True

        if self.pdf_path is None:
            return self.save_as()

        try:
            self.doc.saveIncr()
            self.clear_dirty()
            QMessageBox.information(self, "Saved", f"Saved:\n{self.pdf_path}")
            return True
        except Exception as exc:
            self.show_error("Save failed", exc)
            return False

    def save_as(self) -> bool:
        if self.doc is None:
            return True

        default_name = "annotated.pdf"
        if self.pdf_path:
            default_name = f"{self.pdf_path.stem}_annotated.pdf"

        file_name, _ = QFileDialog.getSaveFileName(self, "Save PDF As", default_name, "PDF files (*.pdf)")
        if not file_name:
            return False

        try:
            self.doc.save(file_name, garbage=4, deflate=True)
            page_index = self.page_index
            self.doc.close()
            self.doc = fitz.open(file_name)
            self.pdf_path = Path(file_name)
            self.page_index = max(0, min(page_index, len(self.doc) - 1))
            self.clear_dirty()
            self.render_page()
            QMessageBox.information(self, "Saved", f"Saved to:\n{file_name}")
            return True
        except Exception as exc:
            self.show_error("Save failed", exc)
            return False

    def prev_page(self) -> None:
        if self.doc is None or self.page_index <= 0:
            return
        self.cancel_add_tool()
        self.page_index -= 1
        self.render_page()

    def next_page(self) -> None:
        if self.doc is None or self.page_index >= len(self.doc) - 1:
            return
        self.cancel_add_tool()
        self.page_index += 1
        self.render_page()

    def zoom_in(self) -> None:
        self.zoom = min(self.zoom + 0.25, 4.0)
        self.render_page(preserve_selection=True)

    def zoom_out(self) -> None:
        self.zoom = max(self.zoom - 0.25, 0.5)
        self.render_page(preserve_selection=True)

    def show_error(self, title: str, exc: Exception) -> None:
        QMessageBox.critical(self, title, str(exc))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.active_tool is not None:
            self.cancel_add_tool()
            self.show_page_status()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if not self.confirm_unsaved_changes("exit"):
            event.ignore()
            return
        self.cancel_add_tool()
        if self.doc is not None:
            self.doc.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
