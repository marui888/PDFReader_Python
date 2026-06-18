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
