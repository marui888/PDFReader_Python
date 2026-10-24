from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def path_key(path: str | Path) -> str:
    try:
        return str(Path(path).resolve()).casefold()
    except (OSError, RuntimeError):
        return str(Path(path).absolute()).casefold()


@dataclass(frozen=True)
class IndexSource:
    path: str
    kind: str = "file"
    recursive: bool = True
    indexed_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{path_key(self.path)}"

    def record(self) -> dict:
        return {"path": self.path, "kind": self.kind, "recursive": self.recursive}


@dataclass
class SourceDraft:
    source: IndexSource
    is_new: bool = False
    delete: bool = False
    update: bool = False
    check_modified: bool = True


@dataclass
class IndexSnapshot:
    sources: list[IndexSource] = field(default_factory=list)
    members: dict[str, set[str]] = field(default_factory=dict)
    documents: dict[str, dict] = field(default_factory=dict)

    @property
    def source_keys(self) -> set[str]:
        return {source.key for source in self.sources}

    @property
    def source_signature(self) -> set[tuple]:
        return {(source.key, source.recursive, source.indexed_at) for source in self.sources}


@dataclass
class IndexUpdatePlan:
    snapshot: IndexSnapshot
    sources: list[IndexSource]
    members: dict[str, set[str]]
    files: dict[str, str]
    index_files: set[str]
    delete_files: set[str]
    added_sources: set[str]
    deleted_sources: set[str]
    touched_sources: set[str]
    clear: bool = False

    @property
    def changes(self) -> bool:
        return bool(self.index_files or self.delete_files or self.added_sources
                    or self.deleted_sources or self.touched_sources)

    def summary(self) -> str:
        new_files = sum(key not in self.snapshot.documents for key in self.index_files)
        return (
            f"待建立来源 {len(self.added_sources)}；待删除来源 {len(self.deleted_sources)}\n"
            f"新增 PDF 索引 {new_files}；更新 PDF 索引 {len(self.index_files) - new_files}；"
            f"删除 PDF 索引 {len(self.delete_files)}\n"
            "重叠来源共用一份 PDF 索引，仍被保留来源使用的索引不会删除。"
        )
