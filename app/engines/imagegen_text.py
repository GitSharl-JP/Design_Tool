"""Multi-line text for the ImageGen ("Generate") tool.

A label may contain line breaks. Lines are stacked on a fixed pitch
(`font size x line_spacing`), each centred horizontally at `cx`, and the
whole block is centred vertically at `cy`. A single line takes exactly the
same path as before multi-line support existed (same positions, same
auto-fit), so existing presets render unchanged.
"""

from PIL import ImageDraw

from app.engines.guidemark_render import draw_tracked

DEFAULT_LINE_SPACING = 1.2     # line pitch as a multiple of the font size


def split_lines(text: str) -> list:
    """Lines of `text` (CRLF/CR normalised). Blank lines inside the text are
    kept — they act as a gap — but leading/trailing blank lines are not."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines or [""]


def line_pitch(font, line_spacing: float) -> int:
    """Distance in px between the centres of two consecutive lines."""
    size = getattr(font, "size", 16)
    return max(1, round(size * line_spacing))


def block_size(lines: list, font, letter_spacing: int,
               line_spacing: float) -> tuple:
    """(width, height) in px of the stacked block, ink-based."""
    scratch = ImageDraw.Draw(_blank())
    widths, heights = [], []
    for ln in lines:
        bb = scratch.textbbox((0, 0), ln, font=font)
        widths.append(bb[2] - bb[0] + letter_spacing * max(0, len(ln) - 1))
        heights.append(bb[3] - bb[1])
    h = (len(lines) - 1) * line_pitch(font, line_spacing) + max(heights)
    return max(widths), h


def _blank():
    from PIL import Image
    return Image.new("RGB", (1, 1))


def autofit_font_size(lines: list, load_font, font_size: int, max_w: int,
                      max_h: int, letter_spacing: int, line_spacing: float,
                      min_size: int = 8, step: int = 2) -> int:
    """Largest size (stepping down by `step` from `font_size`) at which the
    block fits in max_w x max_h. `load_font(size) -> font`."""
    while font_size > min_size:
        w, h = block_size(lines, load_font(font_size), letter_spacing,
                          line_spacing)
        if w <= max_w and h <= max_h:
            break
        font_size -= step
    return font_size


def draw_text_block(draw, lines: list, font, cx: int, cy: int, fill,
                    letter_spacing: int, line_spacing: float) -> None:
    """Draw the lines centred on (cx, cy)."""
    pitch = line_pitch(font, line_spacing)
    n = len(lines)
    for i, ln in enumerate(lines):
        y = cy + round((i - (n - 1) / 2) * pitch)
        draw_tracked(draw, ln, font, cx, y, fill, letter_spacing)
