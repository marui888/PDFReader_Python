"""Read-only planning and atomic, staged updates of the annotation index."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Event

import pymupdf as fitz

from app.models.index_management import IndexSnapshot, IndexSource, IndexUpdatePlan, path_key
from app.services.annotation_index import AnnotationIndex


class IndexTaskCancelled(Exception):
    pass


SOURCE_TABLES = (
    "CREATE TABLE IF NOT EXISTS index_sources ("
    "source_key TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL, "
    "recursive INTEGER NOT NULL, indexed_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS index_source_documents ("
    "source_key TEXT NOT NULL, document_path TEXT NOT NULL, "
    "PRIMARY KEY(source_key, document_path))",
)


def check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise IndexTaskCancelled("任务已取消，数据库未改变。")


def read_snapshot(db_path: Path) -> IndexSnapshot:
    """Opening the manager never creates source tables or imports legacy rows."""
    if not db_path.exists():
        return IndexSnapshot()
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot = IndexSnapshot()
        if "documents" not in tables:
            return snapshot
        for row in connection.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM annotations a WHERE a.document_id=d.id) "
            "AS annotation_count FROM documents d ORDER BY d.path"
        ):
            key = path_key(row["path"])
            if key in snapshot.documents:
                raise RuntimeError("数据库包含同一路径的重复索引，请先整理重复路径。")
            snapshot.documents[key] = dict(row)
        if "index_sources" in tables:
            for row in connection.execute("SELECT * FROM index_sources ORDER BY path"):
                source = IndexSource(row["path"], row["kind"], bool(row["recursive"]), row["indexed_at"])
                snapshot.sources.append(source)
                snapshot.members[source.key] = set()
            if "index_source_documents" in tables:
                for row in connection.execute("SELECT * FROM index_source_documents"):
                    key = path_key(row["document_path"])
                    if row["source_key"] in snapshot.members and key in snapshot.documents:
                        snapshot.members[row["source_key"]].add(key)
        linked = set().union(*snapshot.members.values()) if snapshot.members else set()
        # Legacy indexes become virtual single-file sources, persisted only by Update.
        for key, document in snapshot.documents.items():
            if key not in linked:
                source = IndexSource(document["path"], "file", False, document["indexed_at"])
                if source.key not in snapshot.members:
                    snapshot.sources.append(source)
                    snapshot.members[source.key] = set()
                snapshot.members[source.key].add(key)
        return snapshot


def scan_source(source: IndexSource, cancel: Event) -> dict[str, str]:
    path = Path(source.path)
    check_cancel(cancel)
    if source.kind == "file":
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            raise RuntimeError(f"PDF 来源不存在或不可访问：{path}")
        return {path_key(path): str(path.resolve())}
    if not path.is_dir():
        raise RuntimeError(f"文件夹来源不存在或不可访问：{path}")
    files = {}
    def failed(error):
        raise RuntimeError(f"无法完整扫描来源 {path}：{error}")
    for root, directories, names in os.walk(path, onerror=failed, followlinks=False):
        check_cancel(cancel)
        # Avoid junction/symlink recursion and duplicate trees.
        directories[:] = [name for name in directories
                          if not Path(root, name).is_symlink() and not Path(root, name).is_junction()]
        for name in names:
            check_cancel(cancel)
            candidate = Path(root, name)
            if candidate.suffix.casefold() == ".pdf" and candidate.is_file():
                files[path_key(candidate)] = str(candidate.resolve())
        if not source.recursive:
            break
    return files


def make_plan(db_path: Path, drafts, clear: bool, expected_sources: set[tuple], cancel: Event) -> IndexUpdatePlan:
    snapshot = read_snapshot(db_path)
    if snapshot.source_signature != expected_sources:
        raise RuntimeError("数据库的来源已改变，请点击 Restore 后重新编辑。")
    if clear:
        return IndexUpdatePlan(snapshot, [], {}, {}, set(), set(snapshot.documents),
                               set(), snapshot.source_keys, set(), True)
    sources, members = [], {}
    files = {key: row["path"] for key, row in snapshot.documents.items()}
    index_files, touched = set(), set()
    for draft in drafts:
        check_cancel(cancel)
        source = draft.source
        if draft.delete:
            continue
        sources.append(source)
        old = snapshot.members.get(source.key, set())
        if not draft.is_new and not draft.update:
            members[source.key] = set(old)
            continue
        scanned = scan_source(source, cancel)
        if draft.is_new and not scanned:
            raise RuntimeError(f"新来源中没有 PDF，尚不能建立索引：{source.path}")
        members[source.key] = set(scanned)
        files.update(scanned)
        if set(scanned) != old:
            touched.add(source.key)
        for key, file_path in scanned.items():
            row = snapshot.documents.get(key)
            needs_index = draft.is_new or row is None
            if row is not None and draft.update:
                if not draft.check_modified:
                    needs_index = True
                else:
                    try:
                        indexed_time = datetime.fromisoformat(row["indexed_at"]).timestamp()
                        needs_index = Path(file_path).stat().st_mtime > indexed_time
                    except (ValueError, TypeError):
                        needs_index = True
            if needs_index:
                index_files.add(key)
    final_keys = {source.key for source in sources}
    retained = set().union(*members.values()) if members else set()
    return IndexUpdatePlan(
        snapshot, sources, members, files, index_files,
        set(snapshot.documents) - retained, final_keys - snapshot.source_keys,
        snapshot.source_keys - final_keys, touched,
    )


def find_duplicate_source_names(drafts, clear: bool, cancel: Event) -> dict[str, list[str]]:
    """Scan every retained source, independent of its update/mtime flags."""
    if clear:
        return {}
    files = {}
    for draft in drafts:
        check_cancel(cancel)
        if not draft.delete:
            files.update(scan_source(draft.source, cancel))
    names = {}
    for path in files.values():
        check_cancel(cancel)
        names.setdefault(Path(path).name.casefold(), []).append(path)
    return {name: sorted(paths) for name, paths in sorted(names.items()) if len(paths) > 1}


def duplicate_names_report(duplicates: dict[str, list[str]]) -> str:
    return "\n\n".join(name + "\n" + "\n".join(paths) for name, paths in duplicates.items())


def require_unique_source_names(drafts, clear, cancel):
    duplicates = find_duplicate_source_names(drafts, clear, cancel)
    if duplicates:
        raise RuntimeError("来源中存在同名 PDF，Update 未提交。请整理来源后重试。\n\n"
                           + duplicate_names_report(duplicates))


def database_digest(connection) -> bytes:
    # SQLite serialization includes committed WAL content, unlike hashing the .db file.
    return hashlib.sha256(connection.serialize()).digest()


def apply_plan_steps(db_path: Path, drafts, clear, expected_sources, extract_highlight_text,
                     cancel: Event, progress, committing):
    """Cooperative main-thread task. Yield between pages, never inside final commit."""
    with tempfile.TemporaryDirectory(prefix="pdfreader-index-", ignore_cleanup_errors=True) as directory:
        progress("正在准备临时索引数据库…", 0, 1)
        yield
        check_cancel(cancel)
        require_unique_source_names(drafts, clear, cancel)
        staged_path = Path(directory) / "index.sqlite3"
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("BEGIN")
            original_digest = database_digest(source)
            with closing(sqlite3.connect(staged_path)) as target:
                source.backup(target)
        yield
        check_cancel(cancel)
        plan = make_plan(staged_path, drafts, clear, expected_sources, cancel)
        if not plan.changes:
            return {"changed": False, "indexed": 0, "deleted": 0, "warnings": []}
        index = AnnotationIndex(staged_path)
        warnings = []
        signatures = {}
        total = len(plan.index_files)
        for number, key in enumerate(sorted(plan.index_files), 1):
            check_cancel(cancel)
            file_path = Path(plan.files[key])
            before = file_path.stat()
            progress(f"{number}/{total}  {file_path}", number - 1, total)
            yield
            check_cancel(cancel)
            with fitz.open(file_path) as doc:
                if not len(doc):
                    raise RuntimeError(f"PDF 没有可读取的页面：{file_path}")
                def page_progress(page_number, page_count, count):
                    check_cancel(cancel)
                    progress(f"{file_path.name}：第 {page_number}/{page_count} 页，批注 {count}", number - 1, total)
                # Preserve the existing raw path so cross-source updates cannot create duplicates.
                stored_path = Path(plan.snapshot.documents.get(key, {}).get("path", file_path))
                steps = index.reindex_document_steps(doc, stored_path, extract_highlight_text, page_progress)
                try:
                    for _ in steps:
                        yield
                        check_cancel(cancel)
                finally:
                    steps.close()
                if index.last_warnings:
                    raise RuntimeError(
                        f"PDF 批注读取不完整，整批任务未提交：{file_path}\n" + "\n".join(index.last_warnings[:10])
                    )
                warnings.extend(index.last_warnings)
            after = file_path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise RuntimeError(f"索引期间 PDF 发生变化，整批任务未提交：{file_path}")
            signatures[file_path] = (after.st_mtime_ns, after.st_size)
            progress(f"{number}/{total}  已完成 {file_path.name}", number, total)
            yield
        check_cancel(cancel)
        with closing(index.connect()) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            for sql in SOURCE_TABLES:
                connection.execute(sql)
            connection.execute("DELETE FROM index_source_documents")
            connection.execute("DELETE FROM index_sources")
            for key in plan.delete_files:
                connection.execute("DELETE FROM documents WHERE path=?", (plan.snapshot.documents[key]["path"],))
            now = datetime.now().isoformat(timespec="seconds")
            for item in plan.sources:
                changed = (item.key in plan.added_sources or item.key in plan.touched_sources
                           or bool(plan.members[item.key] & plan.index_files))
                connection.execute("INSERT INTO index_sources VALUES (?,?,?,?,?)", (
                    item.key, item.path, item.kind, int(item.recursive), now if changed else item.indexed_at,
                ))
                for key in plan.members[item.key]:
                    stored_path = plan.snapshot.documents.get(key, {}).get("path", plan.files.get(key))
                    connection.execute("INSERT INTO index_source_documents VALUES (?,?)", (item.key, stored_path))
        check_cancel(cancel)
        for file_path, expected in signatures.items():
            check_cancel(cancel)
            current = file_path.stat()
            if (current.st_mtime_ns, current.st_size) != expected:
                raise RuntimeError(f"准备提交时发现 PDF 已变化，整批任务未提交：{file_path}")
        # Sources can change on disk after preflight or while PDFs are being indexed.
        require_unique_source_names(drafts, clear, cancel)
        committing()
        # Let the GUI paint the committing state before entering the atomic transaction.
        yield
        check_cancel(cancel)
        with closing(sqlite3.connect(db_path, timeout=10)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("ATTACH DATABASE ? AS prepared", (str(staged_path),))
            connection.execute("BEGIN IMMEDIATE")
            try:
                if database_digest(connection) != original_digest:
                    raise RuntimeError("索引数据库在任务期间被其他操作修改，未覆盖现有数据；请 Restore 后重试。")
                check_cancel(cancel)
                for sql in SOURCE_TABLES:
                    connection.execute(sql)
                connection.set_progress_handler(lambda: int(cancel.is_set()), 2000)
                tables = ("index_source_documents", "index_sources", "annotations", "documents")
                for table in tables:
                    connection.execute(f'DELETE FROM "{table}"')
                for table in reversed(tables):
                    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                    names = ",".join(f'"{name}"' for name in columns)
                    connection.execute(f'INSERT INTO main."{table}" ({names}) SELECT {names} FROM prepared."{table}"')
                check_cancel(cancel)
                connection.commit()
            except BaseException:
                connection.set_progress_handler(None, 0)
                connection.rollback()
                raise
            finally:
                connection.set_progress_handler(None, 0)
        return {"changed": True, "indexed": total, "deleted": len(plan.delete_files), "warnings": warnings}


