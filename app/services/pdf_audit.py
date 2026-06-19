from collections import Counter
from dataclasses import dataclass, field

import pymupdf as fitz


@dataclass
class PdfAuditIssue:
    level: str
    page_number: int | None
    message: str


@dataclass
class PdfAuditReport:
    title: str
    page_count: int
    pages_scanned: int = 0
    annotations_found: int = 0
    type_counts: Counter = field(default_factory=Counter)
    page_annotation_counts: list[tuple[int, int]] = field(default_factory=list)
    issues: list[PdfAuditIssue] = field(default_factory=list)


@dataclass
class QuickAuditReport:
    page_number: int | None
    detailed: bool = False
    page_rect: str = ""
    cropbox: str = ""
    annotation_count: int = 0
    supported_count: int = 0
    unsupported_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def quick_audit_current_page_annotations(
    doc: fitz.Document | None,
    page_index: int,
    detailed: bool = False,
) -> QuickAuditReport:
    if doc is None:
        return QuickAuditReport(None, detailed=detailed, errors=["No PDF open."])

    page_count = safe_page_count(doc)
    if page_count <= 0:
        return QuickAuditReport(None, detailed=detailed, errors=["Document has no readable pages."])
    if page_index < 0 or page_index >= page_count:
        return QuickAuditReport(None, detailed=detailed, errors=[f"Page index out of range: {page_index}."])

    page_number = page_index + 1
    report = QuickAuditReport(page_number, detailed=detailed)
    try:
        page = doc[page_index]
    except Exception as exc:
        report.errors.append(f"Page cannot be loaded: {exc}")
        return report

    try:
        page_rect = fitz.Rect(page.rect)
    except Exception as exc:
        report.errors.append(f"Page rect cannot be read: {exc}")
        return report

    try:
        cropbox = fitz.Rect(page.cropbox)
    except Exception:
        cropbox = page_rect
    report.page_rect = compact_rect(page_rect)
    report.cropbox = compact_rect(cropbox)

    try:
        annot = page.first_annot
        while annot is not None:
            report.annotation_count += 1
            quick_audit_annotation(annot, page_rect, cropbox, report, detailed)
            annot = annot.next
    except Exception as exc:
        report.errors.append(f"Annotation traversal failed: {exc}")

    return report


def quick_audit_annotation(
    annot: fitz.Annot,
    page_rect: fitz.Rect,
    cropbox: fitz.Rect,
    report: QuickAuditReport,
    detailed: bool,
) -> None:
    try:
        xref = annot.xref
    except Exception:
        xref = None

    try:
        pdf_type = annot.type[1] if annot.type and len(annot.type) > 1 else str(annot.type[0])
    except Exception:
        pdf_type = "Unknown"

    if pdf_type in {"Highlight", "FreeText", "Square", "Line"}:
        report.supported_count += 1
    else:
        report.unsupported_count += 1

    try:
        rect = fitz.Rect(annot.rect)
    except Exception as exc:
        report.errors.append(f"{label(xref, pdf_type)} rect cannot be read: {exc}")
        return

    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        report.warnings.append(f"{label(xref, pdf_type)} has invalid rect: {compact_rect(rect)}")
        return

    if rect_outside_rect(rect, page_rect):
        report.warnings.append(
            f"{label(xref, pdf_type)} rect outside page: rect={compact_rect(rect)} page={compact_rect(page_rect)}"
        )
    elif detailed and cropbox and not rect_inside_rect(rect, cropbox):
        report.warnings.append(
            f"{label(xref, pdf_type)} rect outside cropbox: rect={compact_rect(rect)} cropbox={compact_rect(cropbox)}"
        )

    if pdf_type == "Highlight":
        quick_audit_vertices(annot, page_rect, report, xref, pdf_type, expected_multiple=4, detailed=detailed)
    elif pdf_type == "Line":
        quick_audit_vertices(annot, page_rect, report, xref, pdf_type, expected_minimum=2, detailed=detailed)


def quick_audit_vertices(
    annot: fitz.Annot,
    page_rect: fitz.Rect,
    report: QuickAuditReport,
    xref: int | None,
    pdf_type: str,
    expected_multiple: int | None = None,
    expected_minimum: int | None = None,
    detailed: bool = False,
) -> None:
    try:
        vertices = getattr(annot, "vertices", None)
    except Exception as exc:
        report.warnings.append(f"{label(xref, pdf_type)} vertices cannot be read: {exc}")
        return
    if not vertices:
        report.warnings.append(f"{label(xref, pdf_type)} has no vertices.")
        return
    if expected_minimum is not None and len(vertices) < expected_minimum:
        report.warnings.append(f"{label(xref, pdf_type)} has fewer than {expected_minimum} vertices.")
    if expected_multiple is not None and len(vertices) % expected_multiple != 0:
        report.warnings.append(f"{label(xref, pdf_type)} vertex count is not a multiple of {expected_multiple}: {len(vertices)}.")

    for point in vertices:
        if not point_in_rect(point, page_rect):
            report.warnings.append(f"{label(xref, pdf_type)} vertex outside page: point={compact_point(point)}")
            return
    if detailed:
        report.warnings.extend(
            f"{label(xref, pdf_type)} vertex {index + 1}: {compact_point(point)}"
            for index, point in enumerate(vertices)
            if not point_in_rect(point, page_rect, tolerance=0.0)
        )


def format_quick_audit_report(report: QuickAuditReport) -> str:
    title_page = f"Page {report.page_number}" if report.page_number is not None else "No page"
    lines = [
        f"Quick Audit - {title_page}",
        f"Mode: {'Detailed' if report.detailed else 'Standard'}",
        f"Annotations: {report.annotation_count}",
        f"Supported: {report.supported_count}",
        f"Unsupported: {report.unsupported_count}",
    ]
    if report.detailed:
        lines.append(f"Page rect: {report.page_rect or 'unknown'}")
        lines.append(f"CropBox: {report.cropbox or 'unknown'}")
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {message}" for message in report.errors)
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {message}" for message in report.warnings[:20])
        if len(report.warnings) > 20:
            lines.append(f"- ... {len(report.warnings) - 20} more warnings")
    if not report.errors and not report.warnings:
        lines.append("")
        lines.append("No bounds warnings.")
    return "\n".join(lines)


def audit_current_page(doc: fitz.Document, page_index: int) -> PdfAuditReport:
    report = PdfAuditReport("PDF Current Page Audit", safe_page_count(doc))
    if report.page_count <= 0:
        report.issues.append(PdfAuditIssue("ERROR", None, "Document has no readable pages."))
        return report

    if page_index < 0 or page_index >= report.page_count:
        report.issues.append(PdfAuditIssue("ERROR", None, f"Page index out of range: {page_index}."))
        return report

    audit_page(doc, page_index, report, detailed=True)
    return report


def audit_document_summary(doc: fitz.Document) -> PdfAuditReport:
    report = PdfAuditReport("PDF Document Audit Summary", safe_page_count(doc))
    if report.page_count <= 0:
        report.issues.append(PdfAuditIssue("ERROR", None, "Document has no readable pages."))
        return report

    for page_index in range(report.page_count):
        audit_page(doc, page_index, report, detailed=False)
    return report


def safe_page_count(doc: fitz.Document) -> int:
    try:
        return len(doc)
    except Exception:
        return 0


def audit_page(doc: fitz.Document, page_index: int, report: PdfAuditReport, detailed: bool) -> None:
    page_number = page_index + 1
    try:
        page = doc[page_index]
    except Exception as exc:
        report.issues.append(PdfAuditIssue("ERROR", page_number, f"Page cannot be loaded: {exc}"))
        return

    report.pages_scanned += 1
    count = 0
    try:
        annot = page.first_annot
        while annot is not None:
            count += 1
            audit_annotation(annot, page.rect, page_number, report, detailed)
            annot = annot.next
    except Exception as exc:
        report.issues.append(PdfAuditIssue("ERROR", page_number, f"Annotation traversal failed: {exc}"))

    report.annotations_found += count
    report.page_annotation_counts.append((page_number, count))


def audit_annotation(
    annot: fitz.Annot,
    page_rect: fitz.Rect,
    page_number: int,
    report: PdfAuditReport,
    detailed: bool,
) -> None:
    try:
        xref = annot.xref
    except Exception as exc:
        report.issues.append(PdfAuditIssue("ERROR", page_number, f"Annotation xref cannot be read: {exc}"))
        xref = None

    try:
        pdf_type = annot.type[1] if annot.type and len(annot.type) > 1 else str(annot.type[0])
    except Exception as exc:
        report.issues.append(PdfAuditIssue("ERROR", page_number, f"Annotation type cannot be read: {exc}"))
        pdf_type = "Unknown"
    report.type_counts[pdf_type] += 1

    try:
        rect = fitz.Rect(annot.rect)
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            report.issues.append(PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} has invalid rect: {rect}."))
        elif detailed and rect_outside_page(rect, page_rect):
            report.issues.append(
                PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} rect is far outside page bounds: {rect}.")
            )
    except Exception as exc:
        report.issues.append(PdfAuditIssue("ERROR", page_number, f"{label(xref, pdf_type)} rect cannot be read: {exc}"))

    if not detailed:
        return

    if pdf_type == "Highlight":
        audit_highlight_vertices(annot, page_number, report, xref, pdf_type)
    elif pdf_type == "Line":
        audit_line_vertices(annot, page_number, report, xref, pdf_type)
    elif pdf_type == "FreeText":
        try:
            _ = annot.info or {}
        except Exception as exc:
            report.issues.append(PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} info cannot be read: {exc}"))


def audit_highlight_vertices(
    annot: fitz.Annot, page_number: int, report: PdfAuditReport, xref: int | None, pdf_type: str
) -> None:
    try:
        vertices = getattr(annot, "vertices", None)
    except Exception as exc:
        report.issues.append(PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} vertices cannot be read: {exc}"))
        return
    if not vertices:
        report.issues.append(PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} has no quad vertices."))
    elif len(vertices) % 4 != 0:
        report.issues.append(
            PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} vertex count is not a multiple of 4: {len(vertices)}.")
        )


def audit_line_vertices(
    annot: fitz.Annot, page_number: int, report: PdfAuditReport, xref: int | None, pdf_type: str
) -> None:
    try:
        vertices = getattr(annot, "vertices", None)
    except Exception as exc:
        report.issues.append(PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} vertices cannot be read: {exc}"))
        return
    if not vertices or len(vertices) < 2:
        report.issues.append(PdfAuditIssue("WARN", page_number, f"{label(xref, pdf_type)} has fewer than 2 vertices."))


def rect_outside_page(rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    margin = max(page_rect.width, page_rect.height) * 0.25
    expanded = fitz.Rect(
        page_rect.x0 - margin,
        page_rect.y0 - margin,
        page_rect.x1 + margin,
        page_rect.y1 + margin,
    )
    return not expanded.intersects(rect)


def rect_inside_rect(rect: fitz.Rect, outer: fitz.Rect, tolerance: float = 0.5) -> bool:
    return (
        rect.x0 >= outer.x0 - tolerance
        and rect.y0 >= outer.y0 - tolerance
        and rect.x1 <= outer.x1 + tolerance
        and rect.y1 <= outer.y1 + tolerance
    )


def rect_outside_rect(rect: fitz.Rect, outer: fitz.Rect, tolerance: float = 0.5) -> bool:
    return not rect_inside_rect(rect, outer, tolerance)


def point_in_rect(point, rect: fitz.Rect, tolerance: float = 0.5) -> bool:
    x = point.x if hasattr(point, "x") else point[0]
    y = point.y if hasattr(point, "y") else point[1]
    return rect.x0 - tolerance <= x <= rect.x1 + tolerance and rect.y0 - tolerance <= y <= rect.y1 + tolerance


def compact_rect(rect: fitz.Rect) -> str:
    return f"({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f})"


def compact_point(point) -> str:
    x = point.x if hasattr(point, "x") else point[0]
    y = point.y if hasattr(point, "y") else point[1]
    return f"({x:.1f}, {y:.1f})"


def label(xref: int | None, pdf_type: str) -> str:
    if xref is None:
        return pdf_type
    return f"{pdf_type} xref={xref}"


def format_audit_report(report: PdfAuditReport) -> str:
    lines = [
        report.title,
        "",
        f"Pages: {report.page_count}",
        f"Pages scanned: {report.pages_scanned}",
        f"Annotations found: {report.annotations_found}",
        f"Issues: {len(report.issues)}",
        "",
        "Annotation Types:",
    ]
    if report.type_counts:
        for name, count in report.type_counts.most_common():
            lines.append(f"  {name}: {count}")
    else:
        lines.append("  none")

    if report.page_annotation_counts:
        lines.extend(["", "Pages With Annotations:"])
        for page_number, count in report.page_annotation_counts:
            if count:
                lines.append(f"  Page {page_number}: {count}")

    lines.extend(["", "Issues:"])
    if report.issues:
        for issue in report.issues:
            page = f"Page {issue.page_number}" if issue.page_number is not None else "Document"
            lines.append(f"  [{issue.level}] {page}: {issue.message}")
    else:
        lines.append("  none")

    return "\n".join(lines)


def report_has_errors(report: PdfAuditReport) -> bool:
    return any(issue.level == "ERROR" for issue in report.issues)
