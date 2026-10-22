"""SVG (Illustrator) export: structure, tombo geometry, and fidelity with the
raster renderer (the SVG is rasterised with resvg and compared to the PNG
pipeline's output)."""

import io
import os
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from app.engines import guidemark_svg as gs
from app.engines.guidemark_layout import make_grid, render_pages
from app.engines.guidemark_model import Design, Segment, SheetItem
from app.engines.guidemark_render import render_item
from tests import gm_harness as h

pytestmark = pytest.mark.skipif(
    not os.path.isfile(h.FONT_PATH), reason="arial.ttf not available")

FONT = lambda fam, sty: h.FONT_PATH  # noqa: E731
NS = {"s": gs.SVG_NS}


def _design(tpl, **kw):
    base = dict(template_path=tpl, font_family="Arial", font_style="Regular",
                auto_size=False, fsize_pt=70, fsize3_pt=50, tracking=0,
                kern_mode="メトリクス", pos_y=46, pos3_y=50)
    base.update(kw)
    return Design(**base)


def _white(tmp_path):
    p = tmp_path / "w.png"
    Image.new("RGB", (100, 100), "white").save(p)
    return str(p)


def _grid():
    return make_grid("A4  (297×210 mm)", "landscape", 60, 60, 10, 8)


def test_tombo_lines_geometry():
    lines = gs.tombo_lines(0, 0, 100, 100, bleed=3, length=2)
    assert len(lines) == 16                       # 4 corners x 4 arms
    # bottom-left corner (sx=-1, sy=+1): vertical trim arm x=0, y 103->105
    assert (0, 103, 0, 105) in lines
    # ... and the outer bleed arm x=-3, y 100->105
    assert (-3, 100, -3, 105) in lines
    # no arm ever enters the tile interior
    for xa, ya, xb, yb in lines:
        for x, y in ((xa, ya), (xb, yb)):
            assert not (0 < x < 100 and 0 < y < 100)


def test_svg_structure_and_layers(tmp_path):
    d = _design(_white(tmp_path))
    items = [SheetItem(d, [Segment("1")]),
             SheetItem(d, [Segment("14"), Segment("-1", pt=28)])]
    svg = gs.build_page_svg(items, _grid(), FONT, gs.SvgOptions())
    root = ET.fromstring(svg)
    assert float(root.get("width")) == pytest.approx(297 * 72 / 25.4, abs=0.01)
    assert float(root.get("viewBox").split()[2]) == pytest.approx(
        297 * 72 / 25.4, abs=0.01)
    layers = [g.get("id") for g in root.findall("s:g", NS)]
    assert layers == ["Fond", "Numeros", "Reperes"]
    texts = root.findall(".//s:g[@id='Numeros']//s:text", NS)
    assert len(texts) == 2                         # ONE block per tile
    assert "".join(texts[1].itertext()) == "14-1"  # composite = one text
    spans = texts[1].findall("s:tspan", NS)
    sizes = sorted(float(t.get("font-size", texts[1].get("font-size")))
                   for t in spans)
    assert sizes[0] == pytest.approx(28, abs=0.2)   # the small "-1" run
    assert sizes[-1] == pytest.approx(70, abs=0.2)
    assert "ArialMT" in texts[0].get("font-family")
    lines = root.findall(".//s:g[@id='Reperes']//s:line", NS)
    assert len(lines) == 2 * (16 + 8)     # corners + centre crosses
    # backgrounds are deduplicated: one def for two tiles
    assert len(root.findall("s:defs/*", NS)) == 1


def test_svg_outline_text_has_no_text_elements(tmp_path):
    d = _design(_white(tmp_path))
    svg = gs.build_page_svg([SheetItem(d, [Segment("55")])], _grid(), FONT,
                            gs.SvgOptions(outline_text=True, marks="none"))
    root = ET.fromstring(svg)
    assert not root.findall(".//s:text", NS)
    assert len(root.findall(".//s:g[@id='Numeros']//s:path", NS)) == 2
    assert not root.findall(".//s:g[@id='Reperes']/*", NS)


def test_marks_modes(tmp_path):
    d = _design(_white(tmp_path))
    it = [SheetItem(d, [Segment("1")])]
    rect = ET.fromstring(gs.build_page_svg(
        it, _grid(), FONT, gs.SvgOptions(marks="rect")))
    assert len(rect.findall(".//s:g[@id='Reperes']/s:rect", NS)) == 1


def _resvg(svg: str, width_px: int, height_px: int) -> Image.Image:
    import resvg_py
    png = resvg_py.svg_to_bytes(svg_string=svg, width=width_px, height=height_px,
                                background="#ffffff")
    return Image.open(io.BytesIO(bytes(png))).convert("L")


def _dark_bbox(img, box):
    return img.crop(box).point(lambda p: 255 if p < 128 else 0).getbbox()


@pytest.mark.parametrize("label,design_kw,segs", [
    ("plain", dict(), [Segment("1")]),
    ("tracked", dict(tracking=12), [Segment("55")]),
    ("mono", dict(kern_mode="等幅"), [Segment("17")]),
    ("optical", dict(kern_mode="オプティカル"), [Segment("77")]),
    ("three", dict(pos3_x=52, tracking3=5), [Segment("100")]),
    ("composite", dict(), [Segment("14"), Segment("-1", pt=28, dy_mm=1)]),
])
def test_svg_matches_raster_layout(tmp_path, label, design_kw, segs):
    d = _design(_white(tmp_path), **design_kw)
    item = SheetItem(d, segs)
    grid = _grid()
    dpi = 150
    raster = render_pages(
        [item], grid, dpi,
        lambda it, tw, th, dp: render_item(it, tw, th, dp, FONT))[0].convert("L")
    svg = gs.build_page_svg([item], grid, FONT, gs.SvgOptions(marks="none", layout_dpi=dpi))
    vec = _resvg(svg, raster.width, raster.height)
    assert vec.size == raster.size or abs(vec.height - raster.height) <= 1

    px = dpi / 25.4
    ox, oy = grid.origin_mm(0)
    box = (round(ox * px), round(oy * px),
           round((ox + 60) * px), round((oy + 60) * px))
    a, b = _dark_bbox(raster, box), _dark_bbox(vec, box)
    assert a and b
    # same ink box within a few px (hinting / sub-pixel positioning)
    for ea, eb in zip(a, b):
        assert abs(ea - eb) <= 4, (label, a, b)


def _items_many(tmp_path, n):
    d = _design(h.make_template(str(tmp_path / "t.png")))
    return [SheetItem(d, [Segment(str(i + 1))]) for i in range(n)]


def test_single_file_stacks_all_pages(tmp_path):
    grid = _grid()
    items = _items_many(tmp_path, grid.per_page + 2)       # -> 2 pages
    paths = gs.export_svg_pages(items, grid, FONT, gs.SvgOptions(),
                                str(tmp_path / "sheet"), single_file=True)
    assert [os.path.basename(p) for p in paths] == ["sheet.svg"]
    root = ET.parse(paths[0]).getroot()
    page_h = 210 * 72 / 25.4
    assert float(root.get("height")) == pytest.approx(
        2 * page_h + 36, abs=0.05)
    for layer in ("Fond", "Numeros", "Reperes"):
        pages = root.findall(f".//s:g[@id='{layer}']/s:g", NS)
        assert [g.get("id") for g in pages] == ["Page-1", "Page-2"]
    # second page's tiles sit below the first page
    fond2 = root.findall(".//s:g[@id='Page-2']/s:g", NS)[0]
    assert float(fond2.get("transform").split()[1].rstrip(")")) > page_h


def test_one_file_per_page_when_asked(tmp_path):
    grid = _grid()
    items = _items_many(tmp_path, grid.per_page + 1)
    paths = gs.export_svg_pages(items, grid, FONT, gs.SvgOptions(),
                                str(tmp_path / "sheet"), single_file=False)
    assert [os.path.basename(p) for p in paths] == [
        "sheet_p1.svg", "sheet_p2.svg"]


def test_single_page_has_no_page_wrapper(tmp_path):
    svg = gs.build_page_svg(_items_many(tmp_path, 2), _grid(), FONT,
                            gs.SvgOptions())
    assert "Page-1" not in svg


def test_border_crop_hides_baked_in_frame(tmp_path):
    """The template has a black frame at its edge; cropping the tile edge
    in the SVG must remove it, without touching the artwork inside."""
    items = _items_many(tmp_path, 1)
    grid = _grid()
    dpi = 300
    px = dpi / 25.4
    ox, oy = grid.origin_mm(0)

    def edge_dark(crop):
        svg = gs.build_page_svg(items, grid, FONT, gs.SvgOptions(
            marks="none", border_crop_mm=crop, layout_dpi=dpi))
        w = round(297 * px)
        img = _resvg(svg, w, round(210 * px))
        # a point on the left frame line (px 2-4 of 400 -> 0.3-0.75 mm)
        x = round((ox + 0.5) * px)
        y = round((oy + 30) * px)
        return img.getpixel((x, y)) < 100
    assert edge_dark(0.0) is True
    assert edge_dark(0.8) is False


def test_border_crop_clip_is_defined_once(tmp_path):
    svg = gs.build_page_svg(_items_many(tmp_path, 3), _grid(), FONT,
                            gs.SvgOptions(border_crop_mm=0.8))
    assert svg.count("<clipPath") == 1
    assert svg.count('clip-path="url(#tile-clip)"') == 3


def test_center_mark_lines_geometry():
    lines = gs.center_mark_lines(0, 0, 100, 100, bleed=3, length=10)
    assert len(lines) == 8                        # 4 sides x (line + tick)
    # top side: cross centred at x=50, y=-6.5 (half-way across the band)
    assert (50 - 12.5, -6.5, 50 + 12.5, -6.5) in lines
    assert (50, -6.5 - 5, 50, -6.5 + 5) in lines
    # left side: cross centred at y=50, x=-6.5
    assert (-6.5, 50 - 12.5, -6.5, 50 + 12.5) in lines
    assert (-6.5 - 5, 50, -6.5 + 5, 50) in lines
    # nothing enters the tile, and ticks stop `bleed` short of the trim edge
    for xa, ya, xb, yb in lines:
        for x, y in ((xa, ya), (xb, yb)):
            assert not (0 < x < 100 and 0 < y < 100)
    ticks_top = [l for l in lines if l[0] == l[2] == 50 and l[1] < 0]
    assert ticks_top and max(ticks_top[0][1], ticks_top[0][3]) == -1.5


def test_center_marks_can_be_turned_off(tmp_path):
    d = _design(_white(tmp_path))
    it = [SheetItem(d, [Segment("1")])]
    svg = gs.build_page_svg(it, _grid(), FONT,
                            gs.SvgOptions(center_marks=False))
    root = ET.fromstring(svg)
    assert len(root.findall(".//s:g[@id='Reperes']//s:line", NS)) == 16


@pytest.mark.parametrize("kw,segs,label", [
    (dict(), [Segment("55")], "55"),
    (dict(tracking=12), [Segment("55")], "55"),
    (dict(kern_mode="等幅"), [Segment("17")], "17"),
    (dict(kern_mode="オプティカル"), [Segment("77")], "77"),
    (dict(), [Segment("15"), Segment("-2", pt=28, dy_mm=1, gap_mm=0.5)],
     "15-2"),
])
def test_label_is_one_editable_text_block(tmp_path, kw, segs, label):
    """Illustrator turns per-glyph x lists into one object per character;
    the label must stay a single <text> with no x/dx lists."""
    d = _design(_white(tmp_path), **kw)
    svg = gs.build_page_svg([SheetItem(d, segs)], _grid(), FONT,
                            gs.SvgOptions(marks="none"))
    root = ET.fromstring(svg)
    texts = root.findall(".//s:g[@id='Numeros']//s:text", NS)
    assert len(texts) == 1
    t = texts[0]
    assert "".join(t.itertext()) == label
    assert " " not in t.get("x")                 # a single x, not a list
    assert all(" " not in (sp.get("x") or "") and sp.get("dx") is None
               for sp in t.iter(f"{{{gs.SVG_NS}}}tspan"))
    if len(segs) > 1:                             # raised suffix
        assert any(sp.get("baseline-shift") for sp in t.findall("s:tspan", NS))
