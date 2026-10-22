"""Headless harness for GuideMark's layout/render engine.

Builds a GuideMarkWizard WITHOUT a Tk root (bypassing __init__) and gives
it plain-Python stand-ins for its tk variables, so the pixel output of
_generate_layout / _render_tile can be captured and compared in tests.
Used by the non-regression goldens (tests/golden/guidemark/).
"""

import os

from PIL import Image, ImageDraw

import ImageTool as it

FONT_PATH = r"C:\Windows\Fonts\arial.ttf"
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden", "guidemark")


class _Var:
    def __init__(self, value):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


def make_template(path, size=400):
    """Deterministic stand-in for a real design (ring + border)."""
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, size - 3, size - 3], outline="black", width=3)
    d.ellipse([size * 0.15, size * 0.12, size * 0.85, size * 0.82],
              outline="#707070", width=10)
    d.pieslice([size * 0.1, size * 0.62, size * 0.3, size * 0.82],
               0, 180, fill="#606060")
    img.save(path)
    return path


DEFAULTS = dict(
    num_range="1-6,55,100,100",
    paper="A4  (297×210 mm)", orient="landscape",
    tile_w=60.0, tile_h=60.0, margin=10.0, gap=4.0, dpi=150,
    monochrome=False, k_pct=70,
    num_font_fam="Arial", num_font_sty="Regular", num_auto=False,
    num_fsize_pt=70, num_fsize3_pt=50, num_color="#000000",
    num_pos_x=50, num_pos_y=46, num_tracking=7, num_kern_mode="メトリクス",
    num3_pos_x=52, num3_pos_y=50, num3_tracking=5,
    num3_kern_mode="オプティカル",
    cut_line_show=True, cut_line_pt=0.2,
)


def make_wizard(template_path, template3_path="", **over):
    cfg = {**DEFAULTS, **over}
    w = it.GuideMarkWizard.__new__(it.GuideMarkWizard)
    names = {
        "_num_range_str": "num_range", "_paper": "paper", "_orient": "orient",
        "_tile_w": "tile_w", "_tile_h": "tile_h", "_margin": "margin",
        "_gap": "gap", "_dpi": "dpi", "_monochrome": "monochrome",
        "_k_pct": "k_pct", "_num_font_fam": "num_font_fam",
        "_num_font_sty": "num_font_sty", "_num_auto": "num_auto",
        "_num_fsize_pt": "num_fsize_pt", "_num_fsize3_pt": "num_fsize3_pt",
        "_num_color": "num_color", "_num_pos_x": "num_pos_x",
        "_num_pos_y": "num_pos_y", "_num_tracking": "num_tracking",
        "_num_kern_mode": "num_kern_mode", "_num3_pos_x": "num3_pos_x",
        "_num3_pos_y": "num3_pos_y", "_num3_tracking": "num3_tracking",
        "_num3_kern_mode": "num3_kern_mode",
        "_cut_line_show": "cut_line_show", "_cut_line_pt": "cut_line_pt",
    }
    for attr, key in names.items():
        object.__setattr__(w, attr, _Var(cfg[key]))
    object.__setattr__(w, "_selected_tpl", {"path": template_path})
    object.__setattr__(
        w, "_selected_tpl3",
        {"path": template3_path} if template3_path else {})
    # Fixed font so the goldens do not depend on the user's installed fonts.
    object.__setattr__(w, "_font_path", lambda fam, sty: FONT_PATH)
    return w


SCENARIOS = {
    "base": {},
    "mono_k70": dict(monochrome=True, k_pct=70),
    "monospaced": dict(num_kern_mode="等幅", num3_kern_mode="等幅"),
    "a3_nobi_many": dict(paper="A3ノビ  (483×329 mm)",
                         num_range="1-22,100,200", dpi=100),
    "no_cutline": dict(cut_line_show=False, num_range="7,7,7"),
}
