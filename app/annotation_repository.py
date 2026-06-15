import re
from dataclasses import dataclass

import pymupdf as fitz

from app.models import SUPPORTED_APP_TYPES, AnnotationModel


@dataclass
class AnnotationReadWarning:
    page_index: int
    stage: str
    message: str
    xref: int | None = None

    def format(self) -> str:
        xref_text = f" xref={self.xref}" if self.xref is not None else ""
        return f"page={self.page_index + 1}{xref_text} stage={self.stage} error={self.message}"


class AnnotationRepository:
    def __init__(self, doc: fitz.Document) -> None:
        self.doc = doc
        self.warnings: list[AnnotationReadWarning] = []

    def load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        models: list[AnnotationModel] = []
        for annot in self.iter_page_annotations(page_index):
            try:
                models.append(self.annotation_to_model(page_index, annot))
            except Exception as exc:
                self.add_warning(page_index, "annotation_to_model", exc, annot)
        return models

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        for annot in self.iter_page_annotations_by_page(page, -1):
            try:
                if annot.xref == xref:
                    return annot
            except Exception as exc:
                self.add_warning(-1, "xref", exc, annot)
        return None

    def iter_page_annotations(self, page_index: int):
        try:
            page = self.doc[page_index]
        except Exception as exc:
            self.add_warning(page_index, "load_page", exc)
            return
        yield from self.iter_page_annotations_by_page(page, page_index)

    def iter_page_annotations_by_page(self, page: fitz.Page, page_index: int):
        try:
            annot = page.first_annot
        except Exception as exc:
            self.add_warning(page_index, "first_annot", exc)
            return

        while annot is not None:
            current = annot
            yield current
            try:
                annot = current.next
            except Exception as exc:
                self.add_warning(page_index, "next", exc, current)
                return

    def add_warning(self, page_index: int, stage: str, exc: Exception, annot=None) -> None:
        self.warnings.append(
            AnnotationReadWarning(
                page_index=page_index,
                xref=self.safe_xref(annot),
                stage=stage,
                message=str(exc),
            )
        )

    def safe_xref(self, annot) -> int | None:
        if annot is None:
            return None
        try:
            return int(annot.xref)
        except Exception:
            return None

    def annotation_to_model(self, page_index: int, annot: fitz.Annot) -> AnnotationModel:
        pdf_type = annot.type[1] if annot.type and len(annot.type) > 1 else str(annot.type[0])
        app_type = self.classify_annotation(annot, pdf_type)
        color = self.annotation_color(annot, pdf_type)
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

    def annotation_color(self, annot: fitz.Annot, pdf_type: str = "") -> tuple | None:
        if pdf_type == "FreeText":
            text_color = self.freetext_text_color(annot)
            if text_color is not None:
                return text_color
        colors = annot.colors or {}
        return colors.get("stroke") or colors.get("fill")

    def freetext_text_color(self, annot: fitz.Annot) -> tuple | None:
        if not annot.xref:
            return None

        for key in ("RC", "DA", "DS"):
            try:
                key_type, value = self.doc.xref_get_key(annot.xref, key)
            except Exception:
                continue
            if key_type == "null" or not value:
                continue
            color = self.parse_freetext_color_value(value)
            if color is not None:
                return color
        return None

    def parse_freetext_color_value(self, value: str) -> tuple | None:
        # FreeText text color is commonly stored either in /DA as "r g b rg"
        # or in /DS as CSS-like "color:#RRGGBB".
        rgb_match = re.search(
            r"(?<!\S)(\d*\.?\d+)\s+(\d*\.?\d+)\s+(\d*\.?\d+)\s+rg(?:\s|$)",
            value,
        )
        if rgb_match:
            channels = tuple(max(0.0, min(1.0, float(rgb_match.group(index)))) for index in range(1, 4))
            return channels

        hex_match = re.search(r"color\s*:\s*#([0-9a-fA-F]{6})", value)
        if hex_match:
            hex_value = hex_match.group(1)
            return tuple(int(hex_value[index : index + 2], 16) / 255 for index in (0, 2, 4))
        return None

    def annotation_border_width(self, annot: fitz.Annot) -> float | None:
        border = annot.border or {}
        width = border.get("width")
        return float(width) if width is not None else None

    def annotation_font_size(self, annot: fitz.Annot) -> float | None:
        if not annot.xref:
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
        return self.points_xy(vertices)

    def annotation_line_points(self, annot: fitz.Annot) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
        vertices = getattr(annot, "vertices", None)
        points = self.points_xy(vertices)
        if len(points) < 2:
            return None, None
        return points[0], points[-1]

    def points_xy(self, value) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        self.collect_points_xy(value, points)
        return points

    def collect_points_xy(self, value, points: list[tuple[float, float]]) -> None:
        if value is None:
            return
        point = self.try_point_xy(value)
        if point is not None:
            points.append(point)
            return
        if isinstance(value, (str, bytes)):
            return
        try:
            iterator = iter(value)
        except TypeError:
            return
        for item in iterator:
            self.collect_points_xy(item, points)

    def try_point_xy(self, point) -> tuple[float, float] | None:
        try:
            return self.point_xy(point)
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    def point_xy(self, point) -> tuple[float, float]:
        if hasattr(point, "x") and hasattr(point, "y"):
            return float(point.x), float(point.y)
        return float(point[0]), float(point[1])

    def annotation_line_ending(self, annot: fitz.Annot) -> str:
        if not annot.xref:
            return ""
        try:
            value = self.doc.xref_get_key(annot.xref, "LE")
        except Exception:
            return ""
        if not value or len(value) < 2:
            return ""
        return str(value[1])
