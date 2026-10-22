"""GuideMark data model — what a tile *is*, independent of any GUI.

A ``Design`` is everything the old single-design wizard kept in its tk
variables (template + number typography); it maps 1:1 onto a saved preset
dict so existing ``_guidemark_presets.json`` entries load unchanged.
"""

from dataclasses import dataclass, field
from typing import List, Optional

# Stored tracking values are raw pixels at this DPI (see scale_tracking).
TRACKING_REF_DPI = 150


@dataclass
class Design:
    template_path: str = ""
    template3_path: str = ""      # optional background override for 3+ digits
    font_family: str = "Futura Std"
    font_style: str = "Medium"
    auto_size: bool = True
    fsize_pt: int = 120           # 1-2 digit manual size
    fsize3_pt: int = 90           # 3+ digit manual size / auto cap
    color: str = "#000000"
    pos_x: int = 50               # % of tile width   (1-2 digit)
    pos_y: int = 42               # % of tile height  (1-2 digit)
    tracking: int = 0             # px at TRACKING_REF_DPI (1-2 digit)
    kern_mode: str = "メトリクス"
    pos3_x: int = 50
    pos3_y: int = 42
    tracking3: int = 0
    kern3_mode: str = "メトリクス"

    # preset-dict key  ->  Design field
    _PRESET_KEYS = {
        "template_path": "template_path", "template3_path": "template3_path",
        "num_font_fam": "font_family", "num_font_sty": "font_style",
        "num_auto": "auto_size", "num_fsize_pt": "fsize_pt",
        "num_fsize3_pt": "fsize3_pt", "num_color": "color",
        "num_pos_x": "pos_x", "num_pos_y": "pos_y",
        "num_tracking": "tracking", "num_kern_mode": "kern_mode",
        "num3_pos_x": "pos3_x", "num3_pos_y": "pos3_y",
        "num3_tracking": "tracking3", "num3_kern_mode": "kern3_mode",
    }

    @classmethod
    def from_preset(cls, d: dict) -> "Design":
        kw = {fld: d[key] for key, fld in cls._PRESET_KEYS.items()
              if key in d}
        return cls(**kw)

    def to_preset(self) -> dict:
        return {key: getattr(self, fld)
                for key, fld in self._PRESET_KEYS.items()}



@dataclass
class Segment:
    """One run of text inside a tile's label (e.g. "14" then "-1").

    Every field except ``text`` is an optional override of the Design's
    number style; ``None`` means "inherit".
    """
    text: str
    pt: Optional[float] = None            # font size
    dy_mm: float = 0.0                    # vertical offset, + = up
    tracking: Optional[int] = None        # px at TRACKING_REF_DPI
    color: Optional[str] = None
    font_family: Optional[str] = None
    font_style: Optional[str] = None
    gap_mm: float = 0.0                   # extra space BEFORE this run (+/-)

    @property
    def is_plain(self) -> bool:
        return (self.pt is None and not self.dy_mm and not self.gap_mm
                and self.tracking is None
                and self.color is None and self.font_family is None
                and self.font_style is None)


@dataclass
class SheetItem:
    """One tile on the sheet: a Design + the label drawn on it."""
    design: Design
    segments: List[Segment] = field(default_factory=list)
    design_name: str = ""
    # whole-label adjustments (composite labels)
    dx_mm: float = 0.0            # shift right
    dy_mm: float = 0.0            # shift up
    center_main: bool = False     # centre only the first run, not the group

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)

    @property
    def is_simple(self) -> bool:
        """Single run, at most a font-size override -> rendered by the
        legacy (golden-tested) path, which keeps optical / monospaced
        kerning."""
        if len(self.segments) != 1 or self.dx_mm or self.dy_mm:
            return False
        s = self.segments[0]
        return (not s.dy_mm and not s.gap_mm and s.tracking is None
                and s.color is None and s.font_family is None
                and s.font_style is None)
