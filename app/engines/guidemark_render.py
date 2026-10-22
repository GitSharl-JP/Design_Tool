"""GuideMark raster tile renderer — template image + number overlay.

Text is first resolved into a list of drawing ``Op``s (one glyph run each,
with its exact pixel position), then painted. The SVG exporter consumes the
very same ops, so the vector file reproduces the raster layout (tracking,
optical / monospaced kerning, composite labels). Pixel output is guarded by
tests/test_guidemark_golden.py.
"""

import logging
from typing import NamedTuple, Optional

from PIL import Image, ImageDraw, ImageFont

from app.engines.guidemark_model import (
    Design, SheetItem, TRACKING_REF_DPI)
from app.engines.vector_template import load_template_image

log = logging.getLogger(__name__)


class Op(NamedTuple):
    """One text draw call in tile pixel space."""
    text: str
    x: float
    y: float
    anchor: Optional[str]     # PIL anchor ('lm', 'ls', None == 'la')
    font: ImageFont.FreeTypeFont
    fill: object              # '#rrggbb' or RGBA tuple


def scale_tracking(tracking: int, dpi: int, ref_dpi: int) -> int:
    """Scale a tracking value (stored as a pixel count at `ref_dpi`) to the
    pixel count it must be at `dpi` for the same PHYSICAL (mm) spacing.

    Tracking is the one GuideMark setting authored in raw pixels rather
    than pt/mm/%. Without this scale, the same stored value produces
    wildly different physical spacing depending on which DPI a tile is
    rendered at (e.g. a low-res preview vs. the real print export).
    """
    return round(tracking * dpi / ref_dpi)


def pt2px(pt: float, dpi: int) -> int:
    return max(1, round(pt * dpi / 72))


_SCRATCH = ImageDraw.Draw(Image.new("RGB", (1, 1)))   # for text measuring


def paint(draw, ops) -> None:
    for op in ops:
        draw.text((op.x, op.y), op.text, fill=op.fill, font=op.font,
                  anchor=op.anchor)


def tracked_ops(text, fnt, cx, cy, fill_rgba, tracking) -> list:
    """Text centred at (cx,cy) with per-character letter spacing.
    Always glyph-by-glyph for total spacing control.
    tracking=0 uses natural advance widths (no inter-glyph kerning)."""
    if not text:
        return []
    advances = []
    for ch in text:
        try:
            adv = float(fnt.getlength(ch))
        except AttributeError:
            bb = _SCRATCH.textbbox((0, 0), ch, font=fnt)
            adv = float(bb[2] - bb[0])
        advances.append(adv)
    total_w = sum(advances) + tracking * (len(text) - 1)
    x = cx - total_w / 2
    ops = []
    for ch, adv in zip(text, advances):
        ops.append(Op(ch, round(x), cy, "lm", fnt, fill_rgba))
        x += adv + tracking
    return ops


def draw_tracked(drw, text, fnt, cx, cy, fill_rgba, tracking):
    """Paint tracked_ops (kept for ImageGen in the monolith)."""
    paint(drw, tracked_ops(text, fnt, cx, cy, fill_rgba, tracking))


def optical_ops(text, fnt, cx, cy, fill_rgba, tracking) -> list:
    """Optical kerning: space characters by their visual ink extents, not advance widths."""
    if not text:
        return []
    ink_widths = []
    ink_left_pads = []
    for ch in text:
        # Render char on tiny canvas, measure actual ink bounds
        bb = _SCRATCH.textbbox((0, 0), ch, font=fnt)
        ch_w = max(1, bb[2] - bb[0])
        ch_h = max(1, bb[3] - bb[1])
        tmp = Image.new("L", (ch_w + 20, ch_h + 20), 0)
        ImageDraw.Draw(tmp).text((10 - bb[0], 10 - bb[1]), ch,
                                 fill=255, font=fnt)
        data = list(tmp.getdata())
        col_sums = [sum(data[r * tmp.width + c] for r in range(tmp.height))
                    for c in range(tmp.width)]
        nonzero = [i for i, v in enumerate(col_sums) if v > 0]
        if nonzero:
            ink_l = nonzero[0]
            ink_r = nonzero[-1]
            ink_widths.append(ink_r - ink_l + 1)
            ink_left_pads.append(ink_l)
        else:
            # Whitespace char
            try:
                adv = int(fnt.getlength(ch))
            except AttributeError:
                adv = ch_w
            ink_widths.append(adv)
            ink_left_pads.append(0)
    # Visual gap between chars: ~20% of average ink width + tracking
    avg_ink = sum(ink_widths) / max(1, len(ink_widths))
    gap = max(0, int(avg_ink * 0.20)) + tracking
    total_w = sum(ink_widths) + gap * (len(text) - 1)
    x = cx - total_w // 2
    ops = []
    for ch, iw, il in zip(text, ink_widths, ink_left_pads):
        # Draw so the ink's left edge aligns to x
        bb = _SCRATCH.textbbox((0, 0), ch, font=fnt)
        ops.append(Op(ch, x - il - bb[0], cy, "lm", fnt, fill_rgba))
        x += iw + gap
    return ops


def _load_font(fp: str, px: int):
    try:
        return ImageFont.truetype(fp, px) if fp else ImageFont.load_default()
    except OSError:
        log.warning("cannot load font %r, falling back to default", fp)
        return ImageFont.load_default()


def auto_num_size(text: str, tw: int, th: int, fp: str, dpi: int,
                  cap3_pt: int) -> int:
    """Binary-search: largest pt where `text` fits in ~72% W / 50% H.
    For 3+ digit numbers `cap3_pt` is the upper bound."""
    max_w = int(tw * 0.72)
    max_h = int(th * 0.50)
    cap = cap3_pt if len(text) >= 3 else 400

    def fits(pt: int) -> bool:
        f = _load_font(fp, max(1, round(pt * dpi / 72)))
        bb = _SCRATCH.textbbox((0, 0), text, font=f)
        return (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h

    lo, hi, best = 4, cap, 8
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def apply_k_convert(img, k_pct: int):
    """Convert img to monochrome at the selected K% density."""
    gray = img.convert("L")
    if k_pct < 100:
        gray = gray.point(lambda p: int(255 - (255 - p) * k_pct / 100))
    return gray.convert("RGB")


def _template_for(design: Design, is3: bool) -> str:
    """Background file for a tile ('' = none); 3-digit cards may override."""
    import os
    if is3 and design.template3_path and os.path.isfile(design.template3_path):
        return design.template3_path
    return design.template_path


def _background(design: Design, is3: bool, tw: int, th: int) -> Image.Image:
    path = _template_for(design, is3)
    img = load_template_image(path, tw, th) if path else None
    return img if img is not None else Image.new("RGB", (tw, th), "#ffffff")


def _hex_rgba(col: str) -> tuple:
    col = col or "#000000"
    return tuple(int(col.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)) + (255,)


def text_ops(design: Design, text: str, tw: int, th: int, dpi: int,
             font_path) -> list:
    """Draw ops for a plain single-run label."""
    is3 = len(text) >= 3
    fp = font_path(design.font_family, design.font_style)

    if design.auto_size:
        num_pt = auto_num_size(text, tw, th, fp, dpi, design.fsize3_pt)
    else:
        num_pt = design.fsize3_pt if is3 else design.fsize_pt

    nf = _load_font(fp, pt2px(num_pt, dpi))
    col = design.color or "#000000"
    col_rgba = _hex_rgba(col)

    # Pick 1-2 digit vs 3+ digit independent settings
    if is3:
        cx, cy = int(tw * design.pos3_x / 100), int(th * design.pos3_y / 100)
        tracking, kern_mode = design.tracking3, design.kern3_mode
    else:
        cx, cy = int(tw * design.pos_x / 100), int(th * design.pos_y / 100)
        tracking, kern_mode = design.tracking, design.kern_mode

    # Tracking is stored in raw px at a fixed reference DPI, so it must be
    # rescaled to whatever DPI this render (preview or export) uses.
    tracking = scale_tracking(tracking, dpi, TRACKING_REF_DPI)

    if kern_mode == "等幅":
        try:
            slot_w = int(max(nf.getlength(ch) for ch in "0123456789"))
        except AttributeError:
            slot_w = int(max((_SCRATCH.textbbox((0, 0), ch, font=nf)[2]
                              - _SCRATCH.textbbox((0, 0), ch, font=nf)[0])
                             for ch in "0123456789"))
        slot_w = max(1, slot_w + tracking)
        total_w = slot_w * len(text)
        x = cx - total_w // 2
        ops = []
        for ch in text:
            try:
                ch_w = int(nf.getlength(ch))
            except AttributeError:
                bb_ = _SCRATCH.textbbox((0, 0), ch, font=nf)
                ch_w = bb_[2] - bb_[0]
            ops.append(Op(ch, x + (slot_w - ch_w) // 2, cy, "lm", nf,
                          col_rgba))
            x += slot_w
        return ops
    if kern_mode == "オプティカル":
        return optical_ops(text, nf, cx, cy, col_rgba, tracking)
    if tracking != 0:
        return tracked_ops(text, nf, cx, cy, col_rgba, tracking)
    bb = _SCRATCH.textbbox((0, 0), text, font=nf)
    nx = cx - (bb[2] - bb[0]) // 2 - bb[0]
    ny = cy - (bb[3] - bb[1]) // 2 - bb[1]
    return [Op(text, nx, ny, None, nf, col)]


def render_tile(design: Design, text: str, tw: int, th: int, dpi: int,
                font_path) -> Image.Image:
    """Render one tile. `font_path(family, style) -> str` resolves fonts."""
    tile = _background(design, len(text) >= 3, tw, th)
    paint(ImageDraw.Draw(tile),
          text_ops(design, text, tw, th, dpi, font_path))
    return tile


def composite_ops(item: SheetItem, tw: int, th: int, dpi: int,
                  font_path) -> list:
    """Several runs (own size/font/offset) on one shared baseline.

    The group is centred horizontally at the design's position and the
    FIRST run is centred vertically there; later runs sit on its baseline
    (plus their own dy). Kerning modes other than plain tracking only
    apply to single-run labels.
    """
    design = item.design
    segs = item.segments
    main = segs[0]
    is3 = len(main.text) >= 3

    def fp_of(seg):
        return font_path(seg.font_family or design.font_family,
                         seg.font_style or ("" if seg.font_family
                                            else design.font_style))

    main_fp = fp_of(main)
    if main.pt is not None:
        main_pt = main.pt
    elif design.auto_size:
        main_pt = auto_num_size(main.text, tw, th, main_fp, dpi,
                                design.fsize3_pt)
    else:
        main_pt = design.fsize3_pt if is3 else design.fsize_pt

    if is3:
        cx, cy = int(tw * design.pos3_x / 100), int(th * design.pos3_y / 100)
        base_trk = design.tracking3
    else:
        cx, cy = int(tw * design.pos_x / 100), int(th * design.pos_y / 100)
        base_trk = design.tracking

    runs = []   # (char, font, x_offset, rgba, dy_px)
    x = 0.0
    for i, seg in enumerate(segs):
        font = _load_font(fp_of(seg), pt2px(
            seg.pt if seg.pt is not None else main_pt, dpi))
        trk = scale_tracking(
            base_trk if seg.tracking is None else seg.tracking,
            dpi, TRACKING_REF_DPI)
        rgba = _hex_rgba(seg.color or design.color)
        dy = round(seg.dy_mm / 25.4 * dpi)
        for ch in seg.text:
            runs.append((ch, font, x, rgba, dy))
            x += float(font.getlength(ch)) + trk
        # last char of the last run: drop the trailing tracking
        if i == len(segs) - 1 and seg.text:
            x -= trk
    if not runs:
        return []
    total_w = x

    bb = _SCRATCH.textbbox((0, 0), main.text, font=runs[0][1], anchor="ls")
    baseline = cy - (bb[1] + bb[3]) / 2
    left = cx - total_w / 2
    return [Op(ch, round(left + ox), round(baseline - dy), "ls", font, rgba)
            for ch, font, ox, rgba, dy in runs]


def item_ops(item: SheetItem, tw: int, th: int, dpi: int, font_path) -> list:
    if item.is_simple:
        return text_ops(item.design, item.text, tw, th, dpi, font_path)
    return composite_ops(item, tw, th, dpi, font_path)


def render_item(item: SheetItem, tw: int, th: int, dpi: int,
                font_path) -> Image.Image:
    """Render a sheet item (plain labels and composite labels alike)."""
    tile = _background(item.design, len(item.segments[0].text) >= 3, tw, th)
    paint(ImageDraw.Draw(tile), item_ops(item, tw, th, dpi, font_path))
    return tile
