import re

import pymupdf as fitz

from app.models import SUPPORTED_APP_TYPES, AnnotationModel


class AnnotationRepository:
    def __init__(self, doc: fitz.Document) -> None:
        self.doc = doc

    def load_page_annotations(self, page_index: int) -> list[AnnotationModel]:
        page = self.doc[page_index]
        models: list[AnnotationModel] = []
        annot = page.first_annot
        while annot is not None:
            models.append(self.annotation_to_model(page_index, annot))
            annot = annot.next
        return models

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        annot = page.first_annot
        while annot is not None:
            if annot.xref == xref:
                return annot
            annot = annot.next
        return None

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
        if not annot.xref:
            return ""
        try:
            value = self.doc.xref_get_key(annot.xref, "LE")
        except Exception:
            return ""
        if not value or len(value) < 2:
            return ""
        return str(value[1])
