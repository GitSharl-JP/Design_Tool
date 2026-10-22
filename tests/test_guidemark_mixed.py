"""Mixed-design sheets and composite (multi-size) labels like "14-1"."""

import os

import pytest
from PIL import Image, ImageChops

from app.engines.guidemark_layout import make_grid, render_pages
from app.engines.guidemark_model import Design, Segment, SheetItem
from app.engines.guidemark_render import render_item, render_tile
from tests import gm_harness as h

pytestmark = pytest.mark.skipif(
    not os.path.isfile(h.FONT_PATH), reason="arial.ttf not available")

FONT = lambda fam, sty: h.FONT_PATH  # noqa: E731


def _design(tpl, **kw):
    base = dict(template_path=tpl, font_family="Arial", font_style="Regular",
                auto_size=False, fsize_pt=70, fsize3_pt=50, tracking=0,
                kern_mode="メトリクス", pos_y=46, pos3_y=50)
    base.update(kw)
    return Design(**base)


def _solid(path, color, size=200):
    Image.new("RGB", (size, size), color).save(path)
    return str(path)


def test_plain_single_segment_uses_legacy_path(tmp_path):
    d = _design(h.make_template(str(tmp_path / "t.png")))
    item = SheetItem(d, [Segment("55")])
    a = render_item(item, 300, 300, 150, FONT)
    b = render_tile(d, "55", 300, 300, 150, FONT)
    assert ImageChops.difference(a, b).getbbox() is None


def _ink_boxes(tile):
    """(black bbox, red bbox) — the small run is coloured red in tests."""
    rgb = tile.convert("RGB")
    r, g, b = rgb.split()
    red = ImageChops.multiply(r.point(lambda p: 255 if p > 200 else 0),
                              g.point(lambda p: 255 if p < 60 else 0))
    blk = ImageChops.multiply(ImageChops.multiply(
        *[c.point(lambda p: 255 if p < 90 else 0) for c in (r, g)]),
        b.point(lambda p: 255 if p < 90 else 0))
    return blk.getbbox(), red.getbbox()


def test_composite_small_run_shares_baseline(tmp_path):
    tpl = _solid(tmp_path / "w.png", "white")
    d = _design(tpl, pos_y=50)
    item = SheetItem(d, [Segment("14"),
                         Segment("1", pt=28, color="#ff0000")])
    tile = render_item(item, 600, 600, 300, FONT)
    big, small = _ink_boxes(tile)
    assert big and small
    assert (small[3] - small[1]) < (big[3] - big[1]) * 0.6   # smaller
    assert abs(small[3] - big[3]) <= 2                       # same baseline
    assert small[0] > big[0]                                 # to the right


def test_composite_dy_moves_run_up(tmp_path):
    tpl = _solid(tmp_path / "w.png", "white")
    d = _design(tpl)

    def small_bottom(dy):
        item = SheetItem(d, [Segment("14"), Segment(
            "1", pt=28, dy_mm=dy, color="#ff0000")])
        return _ink_boxes(render_item(item, 600, 600, 300, FONT))[1][3]
    shift = small_bottom(0) - small_bottom(5)
    assert abs(shift - 5 / 25.4 * 300) <= 1.5   # whole-pixel rounding


def test_mixed_designs_on_one_sheet(tmp_path):
    red = _design(_solid(tmp_path / "r.png", "#ff0000"), auto_size=False)
    blue = _design(_solid(tmp_path / "b.png", "#0000ff"), auto_size=False)
    items = ([SheetItem(red, [Segment(str(n))]) for n in (1, 2, 3)]
             + [SheetItem(blue, [Segment("14"), Segment("1", pt=28)])]
             + [SheetItem(blue, [Segment("55")])])
    grid = make_grid("A4  (297×210 mm)", "landscape", 60, 60, 10, 4)
    pages = render_pages(
        items, grid, 100,
        lambda it, tw, th, d: render_item(it, tw, th, d, FONT))
    assert len(pages) == 1
    px = 100 / 25.4

    def tile_color(i):
        x, y = grid.origin_mm(i)
        return pages[0].getpixel((round((x + 2) * px), round((y + 2) * px)))
    assert [tile_color(i) for i in range(5)] == (
        [(255, 0, 0)] * 3 + [(0, 0, 255)] * 2)


def test_overflow_makes_second_page(tmp_path):
    d = _design(_solid(tmp_path / "r.png", "#ff0000"))
    grid = make_grid("A4  (297×210 mm)", "landscape", 60, 60, 10, 4)
    items = [SheetItem(d, [Segment(str(i))]) for i in range(grid.per_page + 1)]
    pages = render_pages(
        items, grid, 50,
        lambda it, tw, th, dpi: render_item(it, tw, th, dpi, FONT))
    assert len(pages) == 2
