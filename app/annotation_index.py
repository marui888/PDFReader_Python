import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf as fitz

from app.annotation_repository import AnnotationRepository


@dataclass
class AnnotationSearchResult:
    document_path: str
    file_name: str
    page_index: int
    page_number: int
    xref: int
    pdf_type: str
    app_type: str
    text: str


@dataclass
class AnnotationSearchResponse:
    results: list[AnnotationSearchResult]
    sqlite_ms: float
    build_ms: float


@dataclass
class DocumentIndexStatus:
    path: str
    is_indexed: bool
    annotation_count: int = 0
    indexed_at: str = ""
    is_stale: bool = False
    message: str = ""


@dataclass
class IndexedDocumentInfo:
    path: str
    file_name: str
    file_size: int | None
    modified_time: float | None
    indexed_at: str
    annotation_count: int
    exists: bool
    is_stale: bool


class AnnotationIndex:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.last_warnings: list[str] = []
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    file_name TEXT NOT NULL,
                    file_size INTEGER,
                    modified_time REAL,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page_index INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    xref INTEGER NOT NULL,
                    pdf_type TEXT NOT NULL,
                    app_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_text TEXT,
                    subject_text TEXT,
                    selected_text TEXT,
                    rect_x0 REAL,
                    rect_y0 REAL,
                    rect_x1 REAL,
                    rect_y1 REAL,
                    color_r REAL,
                    color_g REAL,
                    color_b REAL,
                    color_a REAL,
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                )
                """
            )
            self.ensure_annotation_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_annotations_document_page ON annotations(document_id, page_index)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_annotations_type ON annotations(app_type, pdf_type)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_annotations_text ON annotations(text)")

    def ensure_annotation_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(annotations)").fetchall()
        }
        if "content_text" not in columns:
            connection.execute("ALTER TABLE annotations ADD COLUMN content_text TEXT")
        if "subject_text" not in columns:
            connection.execute("ALTER TABLE annotations ADD COLUMN subject_text TEXT")
        if "selected_text" not in columns:
            connection.execute("ALTER TABLE annotations ADD COLUMN selected_text TEXT")

    def reindex_document(
        self,
        doc: fitz.Document,
        path: Path,
        extract_highlight_text: bool = False,
        progress_callback: Callable[[int, int, int], None] | None = None,
    ) -> int:
        repository = AnnotationRepository(doc)
        self.last_warnings = []
        stat = path.stat() if path.exists() else None
        indexed_at = self.timestamp()
        page_count = len(doc)
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            document_id = self.upsert_document(connection, path, stat, indexed_at)
            connection.execute("DELETE FROM annotations WHERE document_id = ?", (document_id,))
            count = 0
            for page_index in range(page_count):
                page = doc[page_index]
                for annot in repository.iter_page_annotations_by_page(page, page_index):
                    try:
                        model = repository.annotation_to_model(page_index, annot)
                        content_text, subject_text = self.annotation_info_texts(annot)
                        selected_text = self.highlight_selected_text(page, model) if extract_highlight_text else ""
                        color = self.normalized_color(model.color)
                        connection.execute(
                            """
                            INSERT INTO annotations (
                                document_id, page_index, page_number, xref, pdf_type, app_type,
                                text, content_text, subject_text, selected_text,
                                rect_x0, rect_y0, rect_x1, rect_y1,
                                color_r, color_g, color_b, color_a, indexed_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                document_id,
                                model.page_index,
                                model.page_index + 1,
                                model.xref,
                                model.pdf_type,
                                model.app_type,
                                model.text or "",
                                content_text,
                                subject_text,
                                selected_text,
                                float(model.rect.x0),
                                float(model.rect.y0),
                                float(model.rect.x1),
                                float(model.rect.y1),
                                color[0],
                                color[1],
                                color[2],
                                model.opacity,
                                indexed_at,
                            ),
                        )
                        count += 1
                    except Exception as exc:
                        repository.add_warning(page_index, "index_annotation", exc, annot)
                if progress_callback is not None:
                    progress_callback(page_index + 1, page_count, count)
            self.last_warnings = [warning.format() for warning in repository.warnings]
            return count

    def upsert_document(self, connection: sqlite3.Connection, path: Path, stat, indexed_at: str) -> int:
        path_text = str(path)
        connection.execute(
            """
            INSERT INTO documents(path, file_name, file_size, modified_time, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                file_name = excluded.file_name,
                file_size = excluded.file_size,
                modified_time = excluded.modified_time,
                indexed_at = excluded.indexed_at
            """,
            (
                path_text,
                path.name,
                stat.st_size if stat is not None else None,
                stat.st_mtime if stat is not None else None,
                indexed_at,
            ),
        )
        row = connection.execute("SELECT id FROM documents WHERE path = ?", (path_text,)).fetchone()
        if row is None:
            raise RuntimeError(f"Document was not indexed: {path}")
        return int(row["id"])

    def search(
        self,
        keyword: str,
        app_type: str | None = None,
        limit: int | None = None,
        document_paths: list[str] | None = None,
    ) -> list[AnnotationSearchResult]:
        return self.search_with_timing(keyword, app_type, limit, document_paths).results

    def search_with_timing(
        self,
        keyword: str,
        app_type: str | None = None,
        limit: int | None = None,
        document_paths: list[str] | None = None,
    ) -> AnnotationSearchResponse:
        keyword = keyword.strip()
        parameters: list[object] = []
        where = []
        if keyword:
            where.append(
                "("
                "annotations.text LIKE ? "
                "OR annotations.content_text LIKE ? "
                "OR annotations.subject_text LIKE ? "
                "OR annotations.selected_text LIKE ?"
                ")"
            )
            like_keyword = f"%{keyword}%"
            parameters.extend((like_keyword, like_keyword, like_keyword, like_keyword))
        if app_type:
            where.append("annotations.app_type = ?")
            parameters.append(app_type)
        if document_paths:
            placeholders = ", ".join("?" for _ in document_paths)
            where.append(f"documents.path IN ({placeholders})")
            parameters.extend(document_paths)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ?"
            parameters.append(limit)
        sql = f"""
            SELECT
                documents.path AS document_path,
                documents.file_name AS file_name,
                annotations.page_index,
                annotations.page_number,
                annotations.xref,
                annotations.pdf_type,
                annotations.app_type,
                annotations.text,
                annotations.content_text,
                annotations.subject_text,
                annotations.selected_text
            FROM annotations
            JOIN documents ON documents.id = annotations.document_id
            {where_sql}
            ORDER BY documents.file_name COLLATE NOCASE, annotations.page_number, annotations.xref
            {limit_sql}
        """
        query_started = time.perf_counter()
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        sqlite_ms = (time.perf_counter() - query_started) * 1000

        build_started = time.perf_counter()
        results = [
            AnnotationSearchResult(
                document_path=str(row["document_path"]),
                file_name=str(row["file_name"]),
                page_index=int(row["page_index"]),
                page_number=int(row["page_number"]),
                xref=int(row["xref"]),
                pdf_type=str(row["pdf_type"]),
                app_type=str(row["app_type"]),
                text=self.display_text(
                    str(row["app_type"]),
                    str(row["text"] or ""),
                    str(row["content_text"] or ""),
                    str(row["subject_text"] or ""),
                    str(row["selected_text"] or ""),
                ),
            )
            for row in rows
        ]
        build_ms = (time.perf_counter() - build_started) * 1000
        return AnnotationSearchResponse(results=results, sqlite_ms=sqlite_ms, build_ms=build_ms)

    def document_status(self, path: Path) -> DocumentIndexStatus:
        path_text = str(path)
        if not path.exists():
            return DocumentIndexStatus(path=path_text, is_indexed=False, message="Current PDF file was not found.")

        stat = path.stat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, indexed_at, file_size, modified_time
                FROM documents
                WHERE path = ?
                """,
                (path_text,),
            ).fetchone()
            if row is None:
                return DocumentIndexStatus(
                    path=path_text,
                    is_indexed=False,
                    message="Current PDF is not indexed. Use Reindex Current PDF.",
                )
            annotation_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM annotations WHERE document_id = ?",
                    (int(row["id"]),),
                ).fetchone()["count"]
            )

        indexed_size = row["file_size"]
        indexed_mtime = row["modified_time"]
        is_stale = indexed_size != stat.st_size
        if indexed_mtime is not None:
            is_stale = is_stale or abs(float(indexed_mtime) - stat.st_mtime) > 1.0

        if is_stale:
            message = (
                f"Index may be stale: {annotation_count} annotations, "
                f"indexed at {row['indexed_at']}. Reindex recommended."
            )
        else:
            message = f"Current PDF indexed: {annotation_count} annotations, indexed at {row['indexed_at']}."
        return DocumentIndexStatus(
            path=path_text,
            is_indexed=True,
            annotation_count=annotation_count,
            indexed_at=str(row["indexed_at"]),
            is_stale=is_stale,
            message=message,
        )

    def database_info(self) -> list[IndexedDocumentInfo]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    documents.path,
                    documents.file_name,
                    documents.file_size,
                    documents.modified_time,
                    documents.indexed_at,
                    COUNT(annotations.id) AS annotation_count
                FROM documents
                LEFT JOIN annotations ON annotations.document_id = documents.id
                GROUP BY documents.id
                ORDER BY documents.file_name COLLATE NOCASE
                """
            ).fetchall()

        info: list[IndexedDocumentInfo] = []
        for row in rows:
            path = Path(str(row["path"]))
            exists = path.exists()
            indexed_size = row["file_size"]
            indexed_mtime = row["modified_time"]
            is_stale = False
            if not exists:
                is_stale = True
            else:
                stat = path.stat()
                is_stale = indexed_size != stat.st_size
                if indexed_mtime is not None:
                    is_stale = is_stale or abs(float(indexed_mtime) - stat.st_mtime) > 1.0
            info.append(
                IndexedDocumentInfo(
                    path=str(row["path"]),
                    file_name=str(row["file_name"]),
                    file_size=int(indexed_size) if indexed_size is not None else None,
                    modified_time=float(indexed_mtime) if indexed_mtime is not None else None,
                    indexed_at=str(row["indexed_at"]),
                    annotation_count=int(row["annotation_count"]),
                    exists=exists,
                    is_stale=is_stale,
                )
            )
        return info

    def clear_all(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM annotations")
            connection.execute("DELETE FROM documents")

    def annotation_info_texts(self, annot: fitz.Annot) -> tuple[str, str]:
        info = annot.info or {}
        return str(info.get("content") or ""), str(info.get("subject") or "")

    def display_text(
        self,
        app_type: str,
        text: str,
        content_text: str,
        subject_text: str,
        selected_text: str = "",
    ) -> str:
        if app_type != "highlight":
            return text

        parts = []
        if selected_text:
            parts.append(f"[selected] {selected_text}")
        if content_text:
            parts.append(f"[content] {content_text}")
        if subject_text:
            parts.append(f"[subject] {subject_text}")
        return " | ".join(parts) if parts else text

    def highlight_selected_text(self, page: fitz.Page, model) -> str:
        if model.app_type != "highlight":
            return ""

        polygons = self.highlight_quad_polygons(model)
        fallback_rect = fitz.Rect(model.rect) if not polygons else None
        freetext_rects = self.page_freetext_rects(page)
        lines = self.page_text_lines(page)

        selected_lines: list[str] = []
        for line in lines:
            chars: list[str] = []
            for char in line:
                center = self.rect_center(char["bbox"])
                if self.point_in_any_rect(center, freetext_rects):
                    continue
                if self.point_in_highlight_area(center, polygons, fallback_rect):
                    chars.append(char["text"])
            text = "".join(chars).strip()
            if text:
                selected_lines.append(text)
        return " ".join(" ".join(selected_lines).split())

    def highlight_quad_polygons(self, model) -> list[list[tuple[float, float]]]:
        polygons: list[list[tuple[float, float]]] = []
        points = model.quad_points or []
        for index in range(0, len(points) - 3, 4):
            quad = points[index : index + 4]
            polygons.append([quad[0], quad[1], quad[3], quad[2]])
        return polygons

    def page_text_lines(self, page: fitz.Page) -> list[list[dict]]:
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

    def page_freetext_rects(self, page: fitz.Page) -> list[fitz.Rect]:
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

    def rect_center(self, rect: fitz.Rect) -> tuple[float, float]:
        return (float((rect.x0 + rect.x1) / 2), float((rect.y0 + rect.y1) / 2))

    def point_in_highlight_area(
        self,
        point: tuple[float, float],
        polygons: list[list[tuple[float, float]]],
        fallback_rect: fitz.Rect | None,
    ) -> bool:
        if polygons:
            return any(self.point_in_polygon(point, polygon) for polygon in polygons)
        return bool(fallback_rect is not None and self.point_in_rect(point, fallback_rect))

    def point_in_any_rect(self, point: tuple[float, float], rects: list[fitz.Rect]) -> bool:
        return any(self.point_in_rect(point, rect) for rect in rects)

    def point_in_rect(self, point: tuple[float, float], rect: fitz.Rect) -> bool:
        x, y = point
        return rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1

    def point_in_polygon(self, point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
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

    def timestamp(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def normalized_color(self, color: tuple | None) -> tuple[float | None, float | None, float | None]:
        if not color or len(color) < 3:
            return None, None, None
        return (
            self.clamp_color(float(color[0])),
            self.clamp_color(float(color[1])),
            self.clamp_color(float(color[2])),
        )

    def clamp_color(self, value: float) -> float:
        return max(0.0, min(1.0, value))
