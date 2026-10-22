from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QIcon

import qtawesome as qta


TOOLBAR_ICONS = {
    "zoom_in": ("fa5s.search-plus", 0),
    "zoom_out": ("fa5s.search-minus", 0),
    "freetext": ("fa5s.font", 0),
    "square": ("fa5.square", 0),
    "highlight": ("fa5s.highlighter", 0),
    "arrow": ("fa5s.long-arrow-alt-right", -45),
}


@lru_cache(maxsize=16)
def toolbar_icon(name: str) -> QIcon:
    glyph, rotation = TOOLBAR_ICONS[name]
    return qta.icon(
        glyph,
        color="#444444",
        color_active="#444444",
        color_selected="#444444",
        color_on="#ffffff",
        color_on_active="#ffffff",
        color_on_selected="#ffffff",
        color_disabled="#aaaaaa",
        scale_factor=0.8,
        rotated=rotation,
    )


@lru_cache(maxsize=16)
def dirty_dot_icon() -> QIcon:
    return qta.icon(
        "fa5s.circle",
        color="#be5000",
        scale_factor=0.66,
    )
