"""Vector backgrounds (SVG / PDF / AI) for GuideMark tiles."""

import os
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from app.engines import guidemark_svg as gs
from app.engines import vector_template as vt
from app.engines.guidemark_layout import make_grid
from app.engines.guidemark_model import Design, Segment, SheetItem

RED_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
           '<rect id="bgrect" width="10" height="10" fill="#ff0000"/></svg>')
FONT = lambda fam, sty: r"C:\Windows\Fonts\arial.ttf"  # noqa: E731


def _design(path):
    return Design(template_path=path, font_family="Arial",
                  font_style="Regular", auto_size=False, fsize_pt=40)


def test_kind_detection(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_text(RED_SVG)
    assert vt.kind(str(svg)) == "svg"
    assert vt.kind(str(tmp_path / "missing.png")) == ""
    for ext, k in ((".ai", "pdf"), (".pdf", "pdf"), (".jpg", "raster")):
        f = tmp_path / f"x{ext}"
        f.write_bytes(b"x")
        assert vt.kind(str(f)) == k


def test_svg_template_rasterises(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_text(RED_SVG)
    img = vt.load_template_image(str(svg), 80, 60)
    assert img.size == (80, 60)
    assert img.getpixel((40, 30)) == (255, 0, 0)


def test_pdf_and_ai_templates_render(tmp_path):
    src = Image.new("RGB", (200, 100), (0, 0, 255))
    pdf = tmp_path / "a.pdf"
    src.save(pdf, "PDF", resolution=72)
    ai = tmp_path / "a.ai"                    # .ai is PDF-compatible
    ai.write_bytes(pdf.read_bytes())
    for p in (pdf, ai):
        img = vt.load_template_image(str(p), 100, 50)
        assert img.size == (100, 50)
        r, g, b = img.getpixel((50, 25))
        assert b > 200 and r < 60


def test_unreadable_template_returns_none(tmp_path):
    bad = tmp_path / "bad.svg"
    bad.write_text("not svg")
    assert vt.load_template_image(str(bad), 10, 10) is None


@pytest.mark.skipif(not os.path.isfile(FONT(0, 0)), reason="no arial")
def test_svg_export_inlines_vector_template(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_text(RED_SVG)
    grid = make_grid("A4  (297×210 mm)", "landscape", 60, 60, 10, 8)
    items = [SheetItem(_design(str(svg)), [Segment("1")]),
             SheetItem(_design(str(svg)), [Segment("2")])]
    out = gs.build_page_svg(items, grid, FONT, gs.SvgOptions(marks="none"))
    root = ET.fromstring(out)
    ns = {"s": gs.SVG_NS}
    symbols = root.findall(".//s:symbol", ns)
    assert len(symbols) == 1                       # shared by both tiles
    assert symbols[0].find("s:rect", ns) is not None   # still vector
    assert not root.findall(".//s:image", ns)
    uses = root.findall(".//s:g[@id='Fond']//s:use", ns)
    assert len(uses) == 2 and all(u.get("width") for u in uses)


@pytest.mark.skipif(not os.path.isfile(FONT(0, 0)), reason="no arial")
def test_svg_export_embeds_raster_template_once(tmp_path):
    png = tmp_path / "t.png"
    Image.new("RGB", (50, 50), "white").save(png)
    grid = make_grid("A4  (297×210 mm)", "landscape", 60, 60, 10, 8)
    items = [SheetItem(_design(str(png)), [Segment(str(n))]) for n in (1, 2, 3)]
    out = gs.build_page_svg(items, grid, FONT, gs.SvgOptions(marks="none"))
    assert out.count("data:image/png;base64,") == 1
