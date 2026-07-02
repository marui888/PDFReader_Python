from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QIcon

import qtawesome as qta


@lru_cache(maxsize=16)
def dirty_dot_icon() -> QIcon:
    return qta.icon(
        "fa5s.circle",
        color="#e00000",
        scale_factor=0.72,
    )
