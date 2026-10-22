"""Colour palette and font constants shared across the app.

FONT_* are rebuilt by _update_font_constants() after a language toggle.
Note: any function that captures FONT_* as a default parameter value only
sees the value at *module import* time (Python evaluates defaults once) —
this mirrors the original monolith's behaviour, callers that need the live
value must reference the FONT_* global explicitly in their call, not rely
on a default.

BG4/BORDER/ACCENT_SOFT/FG3/FG_ON_ACCENT/PREVIEW_BG and FONT_LABEL/FONT_HINT
were introduced for the GuideMarkWizard visual-design chantier but are
global constants — reuse them as-is for any future screen, do not recreate
local equivalents.
"""

from app.i18n import get_lang

# ── Colours ──────────────────────────────────────────────────────────────────
# Light Acoustiguide theme (fond blanc, accent rouge) — replaces the previous
# dark/orange palette across the whole app, not just HomeScreen.
BG           = "#ffffff"   # app background
BG2          = "#f7f7f8"   # panels / bars
BG3          = "#ececed"   # cards / inputs at rest
BG4          = "#e3e3e5"   # hover card / focused input
BORDER       = "#d5d5d8"   # neutral 1px border (unselected cards, visible separators)
ACCENT       = "#c9302c"   # Acoustiguide red — brand identity, do not change
ACCENT2      = "#a82824"   # pressed
ACCENT_LIGHT = "#e04a46"   # hover
ACCENT_SOFT  = "#fbe9e8"   # tinted-red background for a discreet highlight
FG           = "#1a1a1a"   # primary text (titles)
FG2          = "#6b6b6f"   # secondary text
FG3          = "#9a9a9e"   # tertiary text (hints, placeholders)
FG_ON_ACCENT = "#ffffff"   # text drawn on an ACCENT-coloured button
FG_GREEN     = "#2e7d46"
FG_RED       = "#c0392b"
FG_YELLOW    = "#a67c00"
RADIUS       = 8
PREVIEW_BG   = "#f0f0f0"   # neutral ground for image/print preview thumbnails —
                            # deliberately a shade darker than BG so the
                            # preview frame stays visually distinct from the
                            # window background, not a leftover default

# ── Fonts ────────────────────────────────────────────────────────────────────
_FONT_FACE_EN = "Segoe UI"
_FONT_FACE_JA = "Noto Sans JP"


def _ui_font():
    """Return the UI font face for the active language."""
    return _FONT_FACE_JA if get_lang() == "ja" else _FONT_FACE_EN


def _update_font_constants():
    """Rebuild FONT_* globals after a language change."""
    global FONT_H1, FONT_H2, FONT_LABEL, FONT_BODY, FONT_SM, FONT_HINT, FONT_MONO
    face = _ui_font()
    FONT_H1    = (face, 20, "bold")   # screen / wizard title
    FONT_H2    = (face, 14, "bold")   # major section title
    FONT_LABEL = (face, 9, "bold")    # sub-section title ("━ Title")
    FONT_BODY  = (face, 10)           # body text / field values
    FONT_SM    = (face, 9)            # row labels, secondary buttons
    FONT_HINT  = (face, 8)            # contextual help — floor: nothing below 8pt
    FONT_MONO  = ("Consolas", 9)      # keep monospace for code snippets


_update_font_constants()  # initialise with default language
