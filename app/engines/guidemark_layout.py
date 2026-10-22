"""GuideMark sheet layout — grid geometry in mm, then pixels.

Pure functions (no Tk). Extracted from GuideMarkWizard._generate_layout so
the raster pages and the future vector (SVG) export share one geometry.
"""

import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

PAPER_MM = {
    "A3ノビ  (483×329 mm)": (483, 329),
    "A3  (420×297 mm)":     (420, 297),
    "A4  (297×210 mm)":     (297, 210),
}


def paper_size_mm(paper: str, orient: str) -> tuple:
    pw, ph = PAPER_MM[paper]
    if orient == "portrait":
        return min(pw, ph), max(pw, ph)
    return max(pw, ph), min(pw, ph)


@dataclass(frozen=True)
class Grid:
    cols: int
    rows: int
    pw_mm: float
    ph_mm: float
    tile_w_mm: float
    tile_h_mm: float
    gap_mm: float
    margin_mm: float

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    def origin_mm(self, index_on_page: int) -> tuple:
        """Top-left of a tile in mm, grid centred on the page."""
        r, c = divmod(index_on_page, self.cols)
        grid_w = self.cols * self.tile_w_mm + (self.cols - 1) * self.gap_mm
        grid_h = self.rows * self.tile_h_mm + (self.rows - 1) * self.gap_mm
        off_x = (self.pw_mm - grid_w) / 2
        off_y = (self.ph_mm - grid_h) / 2
        return (off_x + c * (self.tile_w_mm + self.gap_mm),
                off_y + r * (self.tile_h_mm + self.gap_mm))


def make_grid(paper: str, orient: str, tile_w_mm: float, tile_h_mm: float,
              margin_mm: float, gap_mm: float) -> Grid:
    pw_mm, ph_mm = paper_size_mm(paper, orient)
    # cols/rows are computed directly in mm (no intermediate pixel
    # rounding) so the count is independent of DPI.
    cols = max(1, int((pw_mm - 2 * margin_mm + gap_mm)
                      // (tile_w_mm + gap_mm)))
    rows = max(1, int((ph_mm - 2 * margin_mm + gap_mm)
                      // (tile_h_mm + gap_mm)))
    return Grid(cols, rows, pw_mm, ph_mm, tile_w_mm, tile_h_mm,
                gap_mm, margin_mm)


def render_pages(items: list, grid: Grid, dpi: int, render_item,
                 cut_lw: int = 0, post=None) -> list:
    """Raster pages. `render_item(item, tw_px, th_px, dpi) -> Image`.

    Pixel maths intentionally mirrors the original wizard (mm2px of each
    quantity, centred integer offset) — see tests/test_guidemark_golden.py.
    """
    def mm2px(mm):
        return round(mm / 25.4 * dpi)

    PW, PH = mm2px(grid.pw_mm), mm2px(grid.ph_mm)
    gap = mm2px(grid.gap_mm)
    tw = mm2px(grid.tile_w_mm)
    th = mm2px(grid.tile_h_mm)
    if tw <= 0 or th <= 0:
        return [Image.new("RGB", (max(1, PW), max(1, PH)), "white")]

    grid_w = grid.cols * tw + (grid.cols - 1) * gap
    grid_h = grid.rows * th + (grid.rows - 1) * gap
    off_x = (PW - grid_w) // 2
    off_y = (PH - grid_h) // 2

    pages = []
    per_page = grid.per_page
    for page_start in range(0, max(1, len(items)), per_page):
        chunk = items[page_start:page_start + per_page]
        sheet = Image.new("RGB", (PW, PH), "white")
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(chunk):
            r, c = divmod(i, grid.cols)
            x0 = off_x + c * (tw + gap)
            y0 = off_y + r * (th + gap)
            try:
                sheet.paste(render_item(item, tw, th, dpi), (x0, y0))
            except Exception:
                log.exception("tile %r failed to render", item)
            if cut_lw:
                draw.rectangle([x0, y0, x0 + tw - 1, y0 + th - 1],
                               outline="#888888", width=cut_lw)
        if post:
            sheet = post(sheet)
        pages.append(sheet)
    return pages or [Image.new("RGB", (PW, PH), "white")]
