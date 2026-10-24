"""Replace-All-style Highlight edits on the reader's existing in-memory Document.

This service never opens/closes a PDF, writes a file, or saves the document.
The caller marks edits dirty and refreshes every view of the shared document.
"""
import traceback

import pymupdf as fitz

from app.repositories.annotation_repository import AnnotationRepository
from app.services.pdf_annotation_writer import PdfAnnotationWriter


def _near(left, right, tolerance=0.0001):
    return len(left) == len(right) and all(abs(a - b) <= tolerance for a, b in zip(left, right))


class BulkHighlightEditError(RuntimeError):
    def __init__(self, message, *, changed, touched, details):
        super().__init__(message)
        self.changed = changed
        self.touched = touched
        self.details = details


def _collect_targets(doc, page_index, color, opacity, progress):
    """Preflight all target pages before making any changes; return plain data."""
    repository = AnnotationRepository(doc)
    indexes = range(len(doc)) if page_index is None else [page_index]
    targets, matching = [], 0
    for number, current_page in enumerate(indexes, 1):
        page = doc[current_page]
        for annot in repository.iter_page_annotations_by_page(page, current_page):
            if annot.type[0] != fitz.PDF_ANNOT_HIGHLIGHT:
                continue
            matching += 1
            old_color = tuple((annot.colors or {}).get("stroke") or ())
            old_opacity = float(annot.opacity)
            effective_opacity = old_opacity if old_opacity >= 0 else 1.0
            if ((color is not None and not _near(old_color, color))
                    or (opacity is not None and abs(effective_opacity - opacity) > 0.0001)):
                targets.append((current_page, annot.xref, old_color, old_opacity))
        if progress is not None:
            progress(f"检查内存文档：第 {current_page + 1} 页", number, len(indexes))
    if repository.warnings:
        raise RuntimeError("批注读取不完整，尚未开始修改：\n"
                           + "\n".join(warning.format() for warning in repository.warnings[:10]))
    return targets, matching


def edit_highlights(doc: fitz.Document, page_index: int | None, color, opacity,
                    confirm, progress=None, log=None):
    """Edit the live document. Report partial changes honestly, never auto-save."""
    changed = 0
    touched = False
    stage = "检查参数"
    failure = None
    page = annot = repository = writer = None

    def write_log(message):
        if log is not None:
            try:
                log(message)
            except Exception:
                pass

    try:
        if doc is None or doc.is_closed or not doc.is_pdf:
            raise ValueError("没有可编辑的已打开 PDF。")
        if not doc.permissions & fitz.PDF_PERM_ANNOTATE:
            raise ValueError("该 PDF 不允许修改批注。")
        if page_index is not None and not 0 <= page_index < len(doc):
            raise ValueError("原来选择的当前页已不存在。")
        if color is None and opacity is None:
            raise ValueError("请至少选择一个需要修改的属性。")
        if color is not None and (len(color) != 3 or any(not 0 <= value <= 1 for value in color)):
            raise ValueError("颜色必须为有效的 RGB 值。")
        if opacity is not None and not 0.05 <= opacity <= 1:
            raise ValueError("Opacity 必须在 5%–100% 之间。")
        stage = "检查内存中的 Highlight"
        targets, matching = _collect_targets(doc, page_index, color, opacity, progress)
        write_log(f"Bulk Highlight in-memory preflight: matching={matching} targets={len(targets)}")
        if not targets:
            return {"changed": 0, "matching": matching, "cancelled": False, "touched": False}
        stage = "等待用户确认"
        if not confirm(len(targets), matching):
            return {"changed": 0, "matching": matching, "cancelled": True, "touched": False}
        repository = AnnotationRepository(doc)
        writer = PdfAnnotationWriter(doc)
        for number, (current_page, xref, old_color, old_opacity) in enumerate(targets, 1):
            stage = f"修改第 {current_page + 1} 页 Highlight xref={xref}"
            page = doc[current_page]
            annot = repository.find_page_annotation_by_xref(page, xref)
            if annot is None or annot.type[0] != fitz.PDF_ANNOT_HIGHLIGHT:
                raise RuntimeError("目标 Highlight 已不存在或类型已改变。")
            current_color = tuple((annot.colors or {}).get("stroke") or ())
            if not _near(current_color, old_color) or abs(float(annot.opacity) - old_opacity) > 0.0001:
                raise RuntimeError("目标属性在检查后发生变化，已停止后续修改。")
            raw_rect = doc.xref_get_key(xref, "Rect")
            raw_opacity = doc.xref_get_key(xref, "CA")
            blend = repository.annotation_blend_mode(annot)
            # Mark before the first mutator: even a failed update can leave a
            # partially changed annotation and must not appear as a clean document.
            touched = True
            if color is not None:
                annot.set_colors(stroke=color)
            options = {}
            if opacity is not None:
                options["opacity"] = opacity
            if blend:
                options["blend_mode"] = blend
            annot.update(**options)
            doc.xref_set_key(xref, "Rect", raw_rect[1])
            if opacity is None:
                doc.xref_set_key(xref, "CA", raw_opacity[1])
            writer.touch_mod_date(annot)
            changed += 1
            if progress is not None:
                progress(f"修改内存中的 Highlight：第 {current_page + 1} 页", number, len(targets))
        if repository.warnings:
            raise RuntimeError("批注处理期间发生读取异常。\n"
                               + "\n".join(warning.format() for warning in repository.warnings[:10]))
        write_log(f"Bulk Highlight in-memory finished: changed={changed}; disk PDF not saved")
        return {"changed": changed, "matching": matching, "cancelled": False, "touched": touched}
    except Exception as exc:
        details = traceback.format_exc()
        message = (f"执行阶段：{stage}\n已完成 {changed} 个 Highlight 的修改。\n"
                   + ("内存中可能包含部分修改，需手动保存；磁盘 PDF 尚未写入。\n" if touched else
                      "尚未修改 Highlight；磁盘 PDF 未写入。\n")
                   + f"首次错误：{type(exc).__name__}: {exc}")
        failure = BulkHighlightEditError(message, changed=changed, touched=touched, details=details)
        write_log(message + "\n" + details)
        traceback.clear_frames(exc.__traceback__)
    finally:
        # Do not retain native Page/Annot wrappers in diagnostic exceptions.
        page = annot = repository = writer = None
    raise failure from None
