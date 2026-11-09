"""Line breaks in the ImageGen ("Generate") tool, e.g. a 1054x250 banner."""

import os

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.engines.guidemark_render import draw_tracked
from app.engines.imagegen_text import (
    autofit_font_size, block_size, draw_text_block, line_pitch, split_lines)

FONT_PATH = r"C:\Windows\Fonts\arial.ttf"
pytestmark = pytest.mark.skipif(not os.path.isfile(FONT_PATH),
                                reason="arial.ttf not available")


def _font(size):
    return ImageFont.truetype(FONT_PATH, size)


def _ink_rows(img):
    """Row ranges that contain ink, as [(top, bottom), ...]."""
    g = img.convert("L").point(lambda p: 255 if p < 128 else 0)
    rows = [y for y in range(g.height)
            if g.crop((0, y, g.width, y + 1)).getbbox()]
    out, start = [], None
    for y in range(g.height + 1):
        has = y in set(rows) if y < g.height else False
        if has and start is None:
            start = y
        if not has and start is not None:
            out.append((start, y - 1))
            start = None
    return out


def test_split_lines_normalises_and_trims():
    assert split_lines("a\r\nb\rc") == ["a", "b", "c"]
    assert split_lines("\n\nhello\n\n") == ["hello"]
    assert split_lines("a\n\nb") == ["a", "", "b"]       # inner blank kept
    assert split_lines("") == [""]
    assert split_lines(None) == [""]


def test_single_line_is_identical_to_the_old_single_call():
    f = _font(60)
    old = Image.new("RGB", (400, 200), "white")
    draw_tracked(ImageDraw.Draw(old), "Hello", f, 200, 100, (0, 0, 0, 255), 3)
    new = Image.new("RGB", (400, 200), "white")
    draw_text_block(ImageDraw.Draw(new), ["Hello"], f, 200, 100,
                    (0, 0, 0, 255), 3, 1.2)
    assert ImageChops.difference(old, new).getbbox() is None


def test_two_lines_are_stacked_on_the_pitch_and_centred():
    f = _font(60)
    img = Image.new("RGB", (400, 400), "white")
    draw_text_block(ImageDraw.Draw(img), ["HH", "HH"], f, 200, 200,
                    (0, 0, 0, 255), 0, 1.5)
    rows = _ink_rows(img)
    assert len(rows) == 2
    pitch = line_pitch(f, 1.5)
    # same glyphs -> the two blobs are exactly one pitch apart
    assert rows[1][0] - rows[0][0] == pitch
    # block centred vertically on cy (ink centre within a few px)
    centre = (rows[0][0] + rows[1][1]) / 2
    assert abs(centre - 200) <= 6


def test_each_line_is_centred_horizontally():
    f = _font(50)
    img = Image.new("RGB", (600, 300), "white")
    draw_text_block(ImageDraw.Draw(img), ["WWWWWW", "ii"], f, 300, 150,
                    (0, 0, 0, 255), 0, 1.2)
    g = img.convert("L").point(lambda p: 255 if p < 128 else 0)
    rows = _ink_rows(img)
    for top, bottom in rows:
        bb = g.crop((0, top, 600, bottom + 1)).getbbox()
        assert abs((bb[0] + bb[2]) / 2 - 300) <= 6


def test_line_spacing_changes_the_gap():
    f = _font(50)

    def gap(spacing):
        img = Image.new("RGB", (400, 400), "white")
        draw_text_block(ImageDraw.Draw(img), ["HH", "HH"], f, 200, 200,
                        (0, 0, 0, 255), 0, spacing)
        rows = _ink_rows(img)
        return rows[1][0] - rows[0][1]
    assert gap(2.0) > gap(1.2) > gap(1.0)


def test_empty_line_leaves_a_gap():
    f = _font(40)
    img = Image.new("RGB", (400, 400), "white")
    draw_text_block(ImageDraw.Draw(img), ["HH", "", "HH"], f, 200, 200,
                    (0, 0, 0, 255), 0, 1.2)
    rows = _ink_rows(img)
    assert len(rows) == 2
    assert rows[1][0] - rows[0][0] == 2 * line_pitch(f, 1.2)


def test_autofit_shrinks_to_fit_a_banner_height():
    """3 lines at 150 px cannot fit in 250 px; auto-fit must shrink them
    until the whole block fits the 88% safe area of a 1054x250 banner."""
    lines = ["Line one", "Line two", "Line three"]
    max_w, max_h = int(1054 * 0.88), int(250 * 0.88)
    size = autofit_font_size(lines, _font, 150, max_w, max_h, 0, 1.2)
    assert size < 150
    w, h = block_size(lines, _font(size), 0, 1.2)
    assert w <= max_w and h <= max_h
    # ...and one step larger would not fit
    w2, h2 = block_size(lines, _font(size + 2), 0, 1.2)
    assert w2 > max_w or h2 > max_h


def test_single_line_autofit_matches_legacy_rule():
    """One line: same fit test as the original loop (bbox + tracking)."""
    f = _font(100)
    w, h = block_size(["Banner"], f, 4, 1.2)
    bb = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), "Banner", font=f)
    assert (w, h) == (bb[2] - bb[0] + 4 * 5, bb[3] - bb[1])
