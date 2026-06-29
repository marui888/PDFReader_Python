import json
from dataclasses import dataclass, field
from pathlib import Path

from app.services.shortcuts import merged_shortcuts


@dataclass
class AppSettings:
    use_foxit_freetext: bool = False
    use_popup_freetext_input: bool = False
    freetext_font_size_min: int = 4
    freetext_font_size_max: int = 20
    default_freetext_font_size: int = 7
    default_highlight_color: tuple[float, float, float] = (1, 1, 0)
    default_highlight_opacity: float = 0.45
    extract_highlight_text_on_reindex: bool = False
    quick_audit_detailed: bool = False
    qpdf_bin_dir: str = r"D:\tools\qpdf-12.3.2-msvc64\bin"
    save_incremental_safety_default: bool = True
    search_page_size: int = 500
    recent_files: list[dict] = field(default_factory=list)
    recent_search_rule_files: list[str] = field(default_factory=list)
    shortcuts: dict[str, str] = field(default_factory=lambda: merged_shortcuts({}))


def settings_path(base_file: str | Path) -> Path:
    return Path(base_file).with_name("PDFReaderSetting.json")


def load_settings(path: Path, max_recent_files: int = 10) -> AppSettings:
    settings = AppSettings()
    if not path.exists():
        return settings

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return settings

    settings.use_foxit_freetext = bool(data.get("use_foxit_freetext", settings.use_foxit_freetext))
    settings.use_popup_freetext_input = bool(
        data.get("use_popup_freetext_input", settings.use_popup_freetext_input)
    )
    settings.extract_highlight_text_on_reindex = bool(
        data.get("extract_highlight_text_on_reindex", settings.extract_highlight_text_on_reindex)
    )
    settings.quick_audit_detailed = bool(data.get("quick_audit_detailed", settings.quick_audit_detailed))
    settings.qpdf_bin_dir = str(data.get("qpdf_bin_dir", settings.qpdf_bin_dir))
    settings.save_incremental_safety_default = bool(
        data.get("save_incremental_safety_default", settings.save_incremental_safety_default)
    )
    settings.recent_files = normalize_recent_files(data.get("recent_files", []), max_recent_files)
    settings.recent_search_rule_files = normalize_recent_paths(
        data.get("recent_search_rule_files", []),
        max_recent_files,
    )
    settings.shortcuts = merged_shortcuts(data.get("shortcuts", {}))

    color = data.get("default_highlight_color")
    if isinstance(color, list) and len(color) >= 3:
        try:
            settings.default_highlight_color = tuple(max(0.0, min(1.0, float(value))) for value in color[:3])
        except (TypeError, ValueError):
            pass

    try:
        settings.freetext_font_size_min = max(
            1, int(data.get("freetext_font_size_min", settings.freetext_font_size_min))
        )
        settings.freetext_font_size_max = max(
            settings.freetext_font_size_min,
            int(data.get("freetext_font_size_max", settings.freetext_font_size_max)),
        )
        font_size = int(data.get("default_freetext_font_size", settings.default_freetext_font_size))
        settings.default_freetext_font_size = clamp_font_size(
            font_size, settings.freetext_font_size_min, settings.freetext_font_size_max
        )
        opacity = float(data.get("default_highlight_opacity", settings.default_highlight_opacity))
        settings.default_highlight_opacity = max(0.05, min(1.0, opacity))
        settings.search_page_size = max(1, min(10000, int(data.get("search_page_size", settings.search_page_size))))
    except (TypeError, ValueError):
        pass

    return settings


def save_settings(path: Path, settings: AppSettings) -> None:
    data = {
        "default_freetext_font_size": settings.default_freetext_font_size,
        "default_highlight_color": list(settings.default_highlight_color),
        "default_highlight_opacity": settings.default_highlight_opacity,
        "extract_highlight_text_on_reindex": settings.extract_highlight_text_on_reindex,
        "freetext_font_size_min": settings.freetext_font_size_min,
        "freetext_font_size_max": settings.freetext_font_size_max,
        "quick_audit_detailed": settings.quick_audit_detailed,
        "qpdf_bin_dir": settings.qpdf_bin_dir,
        "recent_files": settings.recent_files,
        "recent_search_rule_files": settings.recent_search_rule_files,
        "save_incremental_safety_default": settings.save_incremental_safety_default,
        "search_page_size": settings.search_page_size,
        "shortcuts": dict(settings.shortcuts),
        "use_foxit_freetext": settings.use_foxit_freetext,
        "use_popup_freetext_input": settings.use_popup_freetext_input,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def normalize_recent_files(value, max_recent_files: int) -> list[dict]:
    if not isinstance(value, list):
        return []

    records: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        path_text = item.get("path")
        if not path_text:
            continue
        try:
            path = str(Path(path_text))
            page_index = max(0, int(item.get("last_page_index", 0)))
        except (TypeError, ValueError):
            continue
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "path": path,
                "last_page_index": page_index,
                "last_opened_at": str(item.get("last_opened_at", "")),
            }
        )
        if len(records) >= max_recent_files:
            break
    return records


def normalize_recent_paths(value, max_recent_files: int) -> list[str]:
    if not isinstance(value, list):
        return []

    records: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not item:
            continue
        try:
            path = str(Path(str(item)))
        except (TypeError, ValueError):
            continue
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        records.append(path)
        if len(records) >= max_recent_files:
            break
    return records


def clamp_font_size(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
