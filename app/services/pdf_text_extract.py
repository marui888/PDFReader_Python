from __future__ import annotations

import pymupdf as fitz


def highlight_selected_text(page: fitz.Page, model) -> str:
    if model.app_type != "highlight":
        return ""

    polygons = highlight_quad_polygons(model)
    fallback_rects = [] if polygons else [fitz.Rect(model.rect)]
    return selected_text_from_areas(page, polygons=polygons, fallback_rects=fallback_rects)


def selected_text_from_rects(page: fitz.Page, rects: list[fitz.Rect]) -> str:
    return selected_text_from_areas(page, polygons=[], fallback_rects=[fitz.Rect(rect) for rect in rects])


def selected_text_from_areas(
    page: fitz.Page,
    polygons: list[list[tuple[float, float]]],
    fallback_rects: list[fitz.Rect],
) -> str:
    freetext_rects = page_freetext_rects(page)
    lines = page_text_lines(page)

    selected_lines: list[str] = []
    for line in lines:
        chars: list[str] = []
        for char in line:
            center = rect_center(char["bbox"])
            if point_in_any_rect(center, freetext_rects):
                continue
            if point_in_selection_area(center, polygons, fallback_rects):
                chars.append(char["text"])
        text = "".join(chars).strip()
        if text:
            selected_lines.append(text)
    return normalize_selected_text(selected_lines)


def highlight_quad_polygons(model) -> list[list[tuple[float, float]]]:
    polygons: list[list[tuple[float, float]]] = []
    points = model.quad_points or []
    for index in range(0, len(points) - 3, 4):
        quad = points[index : index + 4]
        polygons.append([quad[0], quad[1], quad[3], quad[2]])
    return polygons


def page_text_lines(page: fitz.Page) -> list[list[dict]]:
    raw = page.get_text("rawdict")
    lines: list[list[dict]] = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            chars: list[dict] = []
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    text = char.get("c", "")
                    if not text:
                        continue
                    chars.append({"text": text, "bbox": fitz.Rect(char.get("bbox"))})
            if chars:
                chars.sort(key=lambda item: item["bbox"].x0)
                lines.append(chars)
    return lines


def page_freetext_rects(page: fitz.Page) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    annot = page.first_annot
    while annot is not None:
        try:
            pdf_type = annot.type[1] if annot.type and len(annot.type) > 1 else str(annot.type[0])
            if pdf_type == "FreeText":
                rects.append(fitz.Rect(annot.rect))
        except Exception:
            pass
        annot = annot.next
    return rects


def rect_center(rect: fitz.Rect) -> tuple[float, float]:
    return (float((rect.x0 + rect.x1) / 2), float((rect.y0 + rect.y1) / 2))


def point_in_selection_area(
    point: tuple[float, float],
    polygons: list[list[tuple[float, float]]],
    fallback_rects: list[fitz.Rect],
) -> bool:
    if polygons:
        return any(point_in_polygon(point, polygon) for polygon in polygons)
    return point_in_any_rect(point, fallback_rects)


def point_in_any_rect(point: tuple[float, float], rects: list[fitz.Rect]) -> bool:
    return any(point_in_rect(point, rect) for rect in rects)


def point_in_rect(point: tuple[float, float], rect: fitz.Rect) -> bool:
    x, y = point
    return rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def normalize_selected_text(lines: list[str]) -> str:
    return " ".join(" ".join(lines).split())
