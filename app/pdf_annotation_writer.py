from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

import pymupdf as fitz

from app.models import AnnotationModel


class PdfAnnotationWriter:
    def __init__(self, doc: fitz.Document) -> None:
        self.doc = doc

    def pdf_date_now(self) -> str:
        now = datetime.now().astimezone()
        offset = now.strftime("%z")
        pdf_offset = "Z"
        if offset:
            pdf_offset = f"{offset[:3]}'{offset[3:]}'"
        return now.strftime("D:%Y%m%d%H%M%S") + pdf_offset

    def apply_foxit_freetext_keys(self, annot: fitz.Annot) -> None:
        xref = annot.xref
        self.doc.xref_set_key(xref, "IT", "/FreeTextTypewriter")
        self.doc.xref_set_key(xref, "Subj", fitz.get_pdf_str("打字机"))
        self.doc.xref_set_key(xref, "CreationDate", fitz.get_pdf_str(self.pdf_date_now()))
        self.remove_annotation_keys(xref, ("CL", "RD"))

    def remove_annotation_keys(self, xref: int, keys: tuple[str, ...]) -> None:
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

    def find_page_annotation_by_xref(self, page: fitz.Page, xref: int) -> fitz.Annot | None:
        annot = page.first_annot
        while annot is not None:
            if annot.xref == xref:
                return annot
            annot = annot.next
        return None

    def add_freetext_annotation(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        text: str,
        font_size: int,
        color: tuple[float, float, float] = (1, 0, 0),
        use_foxit_freetext: bool = False,
    ) -> fitz.Annot:
        annot = page.add_freetext_annot(
            rect,
            text,
            fontsize=font_size,
            fontname="helv",
            text_color=color,
            fill_color=None,
            border_color=None,
        )
        annot.set_info(title="PDF Note Reader", content=text)
        annot.update()
        if use_foxit_freetext:
            self.apply_foxit_freetext_keys(annot)
        return annot

    def add_square_annotation(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        color: tuple[float, float, float] = (1, 0, 0),
        border_width: float = 2,
        text: str = "Rectangle annotation",
    ) -> fitz.Annot:
        annot = page.add_rect_annot(rect)
        annot.set_colors(stroke=color)
        annot.set_border(width=border_width)
        annot.set_info(title="PDF Note Reader", content=text)
        annot.update()
        return annot

    def add_arrow_annotation(
        self,
        page: fitz.Page,
        start: tuple[float, float],
        end: tuple[float, float],
        color: tuple[float, float, float] = (1, 0, 0),
        border_width: float = 2,
        text: str = "Arrow annotation",
    ) -> fitz.Annot:
        annot = page.add_line_annot(start, end)
        annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        annot.set_colors(stroke=color)
        annot.set_border(width=border_width)
        annot.set_info(title="PDF Note Reader", content=text)
        annot.update()
        return annot

    def add_highlight_annotation(
        self,
        page: fitz.Page,
        rects: list[fitz.Rect],
        color: tuple[float, float, float],
        opacity: float,
        text: str = "Highlight annotation",
    ) -> fitz.Annot:
        annot = page.add_highlight_annot(rects)
        annot.set_colors(stroke=color)
        annot.set_info(title="PDF Note Reader", content=text)
        annot.update(opacity=opacity)
        return annot

    def delete_annotation(self, page: fitz.Page, xref: int) -> None:
        annot = self.find_page_annotation_by_xref(page, xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")
        page.delete_annot(annot)

    def resize_rect_annotation(
        self,
        page: fitz.Page,
        model: AnnotationModel,
        corner: str,
        dx: float,
        dy: float,
        default_freetext_font_size: int,
    ) -> None:
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
            self.set_freetext_rect(annot, model, rect, default_freetext_font_size)
        else:
            raise RuntimeError(f"Annotation type cannot be resized: {model.app_type}")

    def set_square_rect(self, annot: fitz.Annot, model: AnnotationModel, rect: fitz.Rect) -> None:
        inset = max(0.0, (model.border_width or 0.0) / 2)
        if rect.width <= inset * 2 or rect.height <= inset * 2:
            annot.set_rect(rect)
        else:
            annot.set_rect(fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset))
        annot.update()

    def set_freetext_rect(
        self,
        annot: fitz.Annot,
        model: AnnotationModel,
        rect: fitz.Rect,
        default_freetext_font_size: int,
    ) -> None:
        annot.set_rect(rect)
        annot.update(
            fontsize=model.font_size or default_freetext_font_size,
            fontname="helv",
            text_color=model.color or (1, 0, 0),
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
        padding = max(12.0, float((annot.border or {}).get("width") or 1.0) * 6.0)
        x0 = max(page.rect.x0, min(start[0], end[0]) - padding)
        y0 = max(page.rect.y0, min(start[1], end[1]) - padding)
        x1 = min(page.rect.x1, max(start[0], end[0]) + padding)
        y1 = min(page.rect.y1, max(start[1], end[1]) + padding)
        pdf_matrix = ~page.transformation_matrix
        pdf_start = fitz.Point(start) * pdf_matrix
        pdf_end = fitz.Point(end) * pdf_matrix
        pdf_rect = fitz.Rect(x0, y0, x1, y1) * pdf_matrix
        raw = f"[ {pdf_start.x:.4f} {pdf_start.y:.4f} {pdf_end.x:.4f} {pdf_end.y:.4f} ]"
        raw_rect = f"[ {pdf_rect.x0:.4f} {pdf_rect.y0:.4f} {pdf_rect.x1:.4f} {pdf_rect.y1:.4f} ]"
        self.doc.xref_set_key(annot.xref, "Rect", raw_rect)
        self.doc.xref_set_key(annot.xref, "L", raw)
        annot.update()

    def move_annotation(self, page: fitz.Page, model: AnnotationModel, dx: float, dy: float) -> None:
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

    def restore_annotation_geometry(
        self,
        page: fitz.Page,
        xref: int,
        app_type: str,
        rect: fitz.Rect | None,
        line_start: tuple[float, float] | None,
        line_end: tuple[float, float] | None,
        model: AnnotationModel | None,
        default_freetext_font_size: int,
    ) -> None:
        annot = self.find_page_annotation_by_xref(page, xref)
        if annot is None:
            raise RuntimeError("The annotation to undo was not found.")

        if app_type == "arrow":
            if line_start is None or line_end is None:
                raise RuntimeError("The arrow undo action has no saved endpoints.")
            self.set_line_annotation_points(page, annot, line_start, line_end)
            return

        if rect is None:
            raise RuntimeError("The undo action has no saved rectangle.")
        if app_type == "square" and model is not None:
            self.set_square_rect(annot, model, rect)
            return
        if app_type == "freetext" and model is not None:
            self.set_freetext_rect(annot, model, rect, default_freetext_font_size)
            return

        annot.set_rect(rect)
        annot.update()

    def update_freetext_annotation(
        self,
        page: fitz.Page,
        model: AnnotationModel,
        text: str,
        font_size: int,
        color: tuple[float, float, float],
        estimate_size: Callable[[str, int], tuple[float, float]],
    ) -> None:
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        new_width, new_height = estimate_size(text, font_size)
        page_rect = page.rect
        x0 = max(page_rect.x0, min(model.rect.x0, page_rect.x1 - new_width))
        y0 = max(page_rect.y0, min(model.rect.y0, page_rect.y1 - new_height))
        annot.set_rect(fitz.Rect(x0, y0, x0 + new_width, y0 + new_height))
        annot.set_info(title="PDF Note Reader", content=text)
        annot.set_border(width=0)
        annot.update(fontsize=font_size, fontname="helv", text_color=color, fill_color=None, border_color=None)
        self.normalize_freetext_annotation(annot, font_size, color)

    def normalize_freetext_annotation(
        self, annot: fitz.Annot, font_size: int, color: tuple[float, float, float]
    ) -> None:
        xref = annot.xref
        annot.set_border(width=0)
        annot.update(fontsize=font_size, fontname="helv", text_color=color, fill_color=None, border_color=None)

        self.doc.xref_set_key(xref, "DS", fitz.get_pdf_str(self.freetext_default_style(font_size, color)))
        self.doc.xref_set_key(xref, "Q", "0")
        self.doc.xref_set_key(xref, "BS", "<</Type/Border/W 0>>")
        self.remove_annotation_keys(xref, ("RC", "BE", "RD"))

    @staticmethod
    def freetext_default_style(font_size: int, color: tuple[float, float, float]) -> str:
        red, green, blue = (max(0, min(255, int(round(channel * 255)))) for channel in color[:3])
        return f"font: 'Helv' ,sans-serif {font_size:.2f}pt;color:#{red:02X}{green:02X}{blue:02X}"

    def update_highlight_annotation(
        self, page: fitz.Page, model: AnnotationModel, color: tuple[float, float, float], opacity: float
    ) -> None:
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_colors(stroke=color)
        annot.update(opacity=max(0.05, min(1.0, opacity)))

    def update_stroked_annotation(
        self, page: fitz.Page, model: AnnotationModel, color: tuple[float, float, float], width: int
    ) -> None:
        annot = self.find_page_annotation_by_xref(page, model.xref)
        if annot is None:
            raise RuntimeError("The selected annotation was not found on this page.")

        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.update()
