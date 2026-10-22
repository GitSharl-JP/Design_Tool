"""GuideMark -> SVG export for Illustrator.

One SVG per sheet page, document size 1 user unit = 1 pt (unitless width/height, as
Illustrator itself exports, so it reports true point sizes). Top-level groups become Illustrator
layers: ``Fond`` (backgrounds), ``Numeros`` (live text, or outlines), and
``Reperes`` (cut marks, vector strokes — never baked into the artwork).

Glyph positions come from the same draw ops as the raster renderer
(guidemark_render.item_ops) computed at the PRINT dpi (`layout_dpi`), so
tracking, optical / monospaced kerning and composite labels match the
PNG/PDF export exactly (some kerning maths is pixel-quantised, so it is
not strictly dpi-independent).
"""

import base64
import functools
import io
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from PIL import Image

from app.engines import vector_template as vt
from app.engines.guidemark_layout import Grid
from app.engines.guidemark_model import SheetItem
from app.engines.guidemark_render import (
    _template_for, apply_k_convert, item_ops)

log = logging.getLogger(__name__)

PT_PER_MM = 72 / 25.4
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
MAX_EMBED_BYTES = 20 * 1024 * 1024


@dataclass
class SvgOptions:
    marks: str = "tombo"           # 'tombo' | 'rect' | 'none'
    bleed_mm: float = 3.0          # trim -> bleed distance (tombo)
    mark_len_mm: float = 10.0      # length of each tombo arm
    center_marks: bool = True      # centre crosses at the middle of each side
    mark_pt: float = 0.25          # stroke width of the marks
    outline_text: bool = False     # convert numbers to outlines
    monochrome: bool = False
    k_pct: int = 70
    raster_dpi: int = 300          # resolution of rasterised backgrounds
    layout_dpi: int = 300          # dpi the text layout is computed at (= print dpi)
    border_crop_mm: float = 0.0    # clip this much off each tile edge (hides
                                   # a frame baked into the artwork)
    page_gap_pt: float = 36.0      # space between stacked pages (one-file export)


# ── fonts ────────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=64)
def _open_ttfont(path: str, family: str, style: str):
    """The TTFont of `path` (picks the matching face inside a .ttc)."""
    from fontTools.ttLib import TTCollection, TTFont
    if path.lower().endswith(".ttc"):
        coll = TTCollection(path, lazy=True)
        for f in coll.fonts:
            names = f["name"]
            fam = names.getBestFamilyName()
            sty = names.getBestSubFamilyName()
            if fam == family and sty == style:
                return f
        return coll.fonts[0]
    return TTFont(path, lazy=True)


def _font_names(pil_font) -> tuple:
    try:
        return pil_font.getname()
    except Exception:
        return ("", "")


def postscript_name(pil_font) -> str:
    """Illustrator matches SVG font-family against PostScript names."""
    fam, sty = _font_names(pil_font)
    if not isinstance(pil_font.path, str):       # Pillow's built-in fallback
        log.warning("font not resolved; SVG text falls back to %r", fam)
        return fam
    try:
        return _open_ttfont(pil_font.path, fam, sty)["name"].getDebugName(6) \
            or fam
    except Exception:
        log.exception("no PostScript name for %r", getattr(pil_font, "path", ""))
        return fam


def _glyph_path(pil_font, ch: str):
    """(svg path d in font units, advance, unitsPerEm) for `ch`."""
    from fontTools.pens.svgPathPen import SVGPathPen
    fam, sty = _font_names(pil_font)
    if not isinstance(pil_font.path, str):
        return "", 0, 1000
    tt = _open_ttfont(pil_font.path, fam, sty)
    gname = tt.getBestCmap().get(ord(ch))
    if gname is None:
        return "", 0, tt["head"].unitsPerEm
    gs = tt.getGlyphSet()
    pen = SVGPathPen(gs)
    gs[gname].draw(pen)
    return pen.getCommands(), gs[gname].width, tt["head"].unitsPerEm


# ── small helpers ────────────────────────────────────────────────────────
def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _hex(fill) -> str:
    if isinstance(fill, str):
        return fill if fill.startswith("#") else f"#{fill}"
    r, g, b = fill[:3]
    return f"#{r:02x}{g:02x}{b:02x}"


def _mono_hex(color: str, k_pct: int) -> str:
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    lum = round(0.299 * r + 0.587 * g + 0.114 * b)
    g_ = int(255 - (255 - lum) * k_pct / 100) if k_pct < 100 else lum
    return f"#{g_:02x}{g_:02x}{g_:02x}"


def _png_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── backgrounds ──────────────────────────────────────────────────────────
def _background_defs(path: str, def_id: str, w_pt: float, h_pt: float,
                     tile_w_mm: float, tile_h_mm: float,
                     opts: SvgOptions) -> str:
    """<defs> fragment for one template (referenced by <use>)."""
    k = vt.kind(path)
    size = f'width="{_f(w_pt)}" height="{_f(h_pt)}"'
    if k == "svg" and not opts.monochrome:
        try:
            return _symbol_from_svg(path, def_id, w_pt, h_pt)
        except Exception:
            log.exception("cannot inline %r, rasterising instead", path)
    if k == "raster" and not opts.monochrome:
        ext = os.path.splitext(path)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg"}.get(ext)
        if mime and os.path.getsize(path) <= MAX_EMBED_BYTES:
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            return (f'<image id="{def_id}" {size} '
                    f'preserveAspectRatio="none" '
                    f'xlink:href="data:{mime};base64,{b64}"/>')
    tw = max(1, round(tile_w_mm / 25.4 * opts.raster_dpi))
    th = max(1, round(tile_h_mm / 25.4 * opts.raster_dpi))
    img = vt.load_template_image(path, tw, th)
    if img is None:
        return f'<rect id="{def_id}" {size} fill="#ffffff"/>'
    if opts.monochrome:
        img = apply_k_convert(img, opts.k_pct)
    return (f'<image id="{def_id}" {size} preserveAspectRatio="none" '
            f'xlink:href="{_png_data_uri(img)}"/>')


def _symbol_from_svg(path: str, def_id: str, w_pt: float, h_pt: float) -> str:
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    root = ET.parse(path).getroot()
    vb = root.get("viewBox")
    if not vb:
        def num(v):
            return float("".join(c for c in (v or "100") if c in "0123456789."))
        vb = f"0 0 {num(root.get('width'))} {num(root.get('height'))}"
    inner = "".join(ET.tostring(ch, encoding="unicode") for ch in root)
    return (f'<symbol id="{def_id}" viewBox="{vb}" '
            f'preserveAspectRatio="none">{inner}</symbol>')


# ── cut marks ────────────────────────────────────────────────────────────
def tombo_lines(x0, y0, x1, y1, bleed, length) -> list:
    """Japanese double corner marks around a trim box (all in the same unit).

    Per corner: two nested L's offset by `bleed` — the trim lines and the
    bleed lines — with arms of `length` pointing away from the tile.
    Returns [(xa, ya, xb, yb), ...].
    """
    out = []
    for cx, sx in ((x0, -1), (x1, 1)):
        for cy, sy in ((y0, -1), (y1, 1)):
            far = bleed + length
            # L1: corner (cx + sx*bleed, cy) — trim horizontal, bleed vertical
            out.append((cx + sx * bleed, cy, cx + sx * far, cy))
            out.append((cx + sx * bleed, cy, cx + sx * bleed, cy + sy * far))
            # L2: corner (cx, cy + sy*bleed) — bleed horizontal, trim vertical
            out.append((cx, cy + sy * bleed, cx + sx * far, cy + sy * bleed))
            out.append((cx, cy + sy * bleed, cx, cy + sy * far))
    return out


def center_mark_lines(x0, y0, x1, y1, bleed, length) -> list:
    """The four centre crosses (センタートンボ), one per side, outside the
    trim box. Each cross sits half-way across the corner-mark band
    (`bleed + length`); its line parallel to the side is 2.5x `length`
    long, the tick across it `length` long.
    Returns [(xa, ya, xb, yb), ...]."""
    d = (bleed + length) / 2
    along, tick = 1.25 * length, length / 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    out = []
    for y in (y0 - d, y1 + d):                       # top / bottom
        out.append((cx - along, y, cx + along, y))
        out.append((cx, y - tick, cx, y + tick))
    for x in (x0 - d, x1 + d):                       # left / right
        out.append((x, cy - along, x, cy + along))
        out.append((x - tick, cy, x + tick, cy))
    return out


# ── text ─────────────────────────────────────────────────────────────────
def _baseline_pt(op, dpi: int) -> float:
    asc, desc = op.font.getmetrics()
    if op.anchor == "ls":
        base = op.y
    elif op.anchor == "lm":
        base = op.y + (asc - desc) / 2
    else:                                   # default 'la'
        base = op.y + asc
    return base * 72 / dpi


def _style(op, opts: SvgOptions) -> tuple:
    """(font-family attr, size pt, fill) of an op."""
    ps = postscript_name(op.font)
    fam, _sty = _font_names(op.font)
    family = ps + ", " + fam if fam and fam != ps else ps
    fill = _hex(op.fill)
    if opts.monochrome:
        fill = _mono_hex(fill, opts.k_pct)
    return family, op.font.size * 72 / opts.layout_dpi, fill


def _tile_text(ops: list, ox_pt: float, oy_pt: float,
               opts: SvgOptions) -> str:
    """ONE <text> element for the whole label, so it is a single editable
    block in Illustrator (retype "15-2" in one go).

    Each style run (size / font / colour / baseline) is a <tspan>; glyph
    spacing is expressed with letter-spacing (tracking), and a raised run
    with baseline-shift — never per-glyph x lists, which Illustrator
    explodes into one text object per character. Spacings are derived from
    the real glyph positions, so tracking, optical and monospaced kerning
    reproduce the raster layout.
    """
    dpi, px = opts.layout_dpi, 72 / opts.layout_dpi
    base0 = _baseline_pt(ops[0], dpi)
    # spacing (px) after each op = distance to the next op's origin
    # beyond this op's own advance
    sp = [ops[i + 1].x - (ops[i].x + ops[i].font.getlength(ops[i].text))
          for i in range(len(ops) - 1)] + [0.0]

    # consecutive ops sharing font/colour/baseline form one style run
    runs, i = [], 0
    while i < len(ops):
        j = i
        while (j + 1 < len(ops) and ops[j + 1].font is ops[i].font
               and ops[j + 1].fill == ops[i].fill
               and abs(_baseline_pt(ops[j + 1], dpi)
                       - _baseline_pt(ops[i], dpi)) < 0.01):
            j += 1
        runs.append((i, j))
        i = j + 1

    text_style = _style(ops[0], opts)
    spans = []
    for i, j in runs:
        op0 = ops[i]
        fam, size, fill = _style(op0, opts)
        shift = base0 - _baseline_pt(op0, dpi)          # + = raised
        attrs = ""
        if (fam, size, fill) != text_style:
            attrs += (f' font-family={quoteattr(fam)} '
                      f'font-size="{_f(size)}" fill="{fill}"')
        if abs(shift) > 0.01:
            attrs += f' baseline-shift="{_f(shift)}"'

        def span(chunk, spacing_px):
            txt = escape("".join(o.text for o in chunk))
            ls = (f' letter-spacing="{_f(spacing_px * px)}"'
                  if abs(spacing_px) > 0.01 else "")
            return f"<tspan{attrs}{ls}>{txt}</tspan>"

        inner = sp[i:j]                  # spacings between this run's ops
        if i == j:
            spans.append(span(ops[i:j + 1], sp[j]))
        elif max(inner) - min(inner) <= 1.5:      # uniform tracking
            mean = sum(inner) / len(inner)
            if j == len(ops) - 1 or abs(sp[j] - mean) <= 1.5:
                # the spacing after the last glyph is irrelevant (end of
                # label) or equal to the tracking: keep the run whole
                spans.append(span(ops[i:j + 1], mean))
            else:
                spans.append(span(ops[i:j], mean))
                spans.append(span(ops[j:j + 1], sp[j]))
        else:                                     # optical / monospaced
            spans.extend(span(ops[k:k + 1], sp[k]) for k in range(i, j + 1))

    fam, size, fill = text_style
    return (f'<text x="{_f(ox_pt + ops[0].x * px)}" '
            f'y="{_f(oy_pt + base0)}" font-family={quoteattr(fam)} '
            f'font-size="{_f(size)}" fill="{fill}" '
            f'xml:space="preserve">{"".join(spans)}</text>')


def _text_group(ops: list, ox_pt: float, oy_pt: float,
                opts: SvgOptions) -> list:
    """SVG elements for one tile's label: a single live <text>, or
    per-glyph outlines when `opts.outline_text`."""
    if not ops:
        return []
    if opts.outline_text:
        out = []
        for op in ops:
            x = op.x
            for ch in op.text:
                d, _adv, upem = _glyph_path(op.font, ch)
                out.append(_outline(d, upem, op, x, ox_pt, oy_pt, opts))
                x += op.font.getlength(ch)
        return out
    return [_tile_text(ops, ox_pt, oy_pt, opts)]


def _outline(d, upem, op, x_px, ox_pt, oy_pt, opts) -> str:
    px = 72 / opts.layout_dpi
    s = op.font.size * px / upem
    fill = _hex(op.fill)
    if opts.monochrome:
        fill = _mono_hex(fill, opts.k_pct)
    return (f'<path d="{d}" fill="{fill}" transform="translate('
            f'{_f(ox_pt + x_px * px)} {_f(oy_pt + _baseline_pt(op, opts.layout_dpi))}) '
            f'scale({s:.6f} {-s:.6f})"/>')


# ── page / document ──────────────────────────────────────────────────────
def build_svg(pages: list, grid: Grid, font_path, opts: SvgOptions) -> str:
    """One SVG document holding every page (`pages` = list of item lists).

    Several pages are stacked vertically with `opts.page_gap_pt` between
    them; each page is a sub-group (``Page-1`` ...) inside the three
    layers, so Illustrator keeps one layer per kind of content.
    """
    pw_pt = grid.pw_mm * PT_PER_MM
    ph_pt = grid.ph_mm * PT_PER_MM
    tw_pt = grid.tile_w_mm * PT_PER_MM
    th_pt = grid.tile_h_mm * PT_PER_MM
    dpi = opts.layout_dpi
    tw_px = max(1, round(grid.tile_w_mm / 25.4 * dpi))
    th_px = max(1, round(grid.tile_h_mm / 25.4 * dpi))
    crop = max(0.0, opts.border_crop_mm) * PT_PER_MM
    stacked = len(pages) > 1

    defs, bg_ids = [], {}
    if crop > 0:
        defs.append(
            f'<clipPath id="tile-clip"><rect x="{_f(crop)}" y="{_f(crop)}" '
            f'width="{_f(tw_pt - 2 * crop)}" '
            f'height="{_f(th_pt - 2 * crop)}"/></clipPath>')
    fond_pages, nums_pages, rep_pages = [], [], []
    for pn, items in enumerate(pages):
        page_y = pn * (ph_pt + opts.page_gap_pt)
        fond, nums, reperes = [], [], []
        for i, item in enumerate(items):
            ox, oy = grid.origin_mm(i)
            ox, oy = ox * PT_PER_MM, oy * PT_PER_MM + page_y
            path = _template_for(item.design,
                                 len(item.segments[0].text) >= 3)
            if path not in bg_ids:
                bg_ids[path] = f"bg{len(bg_ids) + 1}"
                defs.append(_background_defs(
                    path, bg_ids[path], tw_pt, th_pt,
                    grid.tile_w_mm, grid.tile_h_mm, opts))
            tag = f"p{pn + 1}-tile-{i + 1:03d}" if stacked \
                else f"tile-{i + 1:03d}"
            use = f'<use xlink:href="#{bg_ids[path]}" x="0" y="0"'
            if path and vt.kind(path) == "svg" and not opts.monochrome:
                use += f' width="{_f(tw_pt)}" height="{_f(th_pt)}"'
            clip = ' clip-path="url(#tile-clip)"' if crop > 0 else ""
            fond.append(f'<g id="{tag}-fond" transform="translate('
                        f'{_f(ox)} {_f(oy)})"{clip}>{use}/></g>')

            ops = item_ops(item, tw_px, th_px, dpi, font_path)
            label = escape(item.text)
            nums.append(f'<g id="{tag}-{label}">'
                        + "".join(_text_group(ops, ox, oy, opts)) + "</g>")

            x0, y0, x1, y1 = ox, oy, ox + tw_pt, oy + th_pt
            sw = _f(opts.mark_pt)
            if opts.marks == "tombo":
                bl, ln = opts.bleed_mm * PT_PER_MM, opts.mark_len_mm * PT_PER_MM
                lines = tombo_lines(x0, y0, x1, y1, bl, ln)
                if opts.center_marks:
                    lines += center_mark_lines(x0, y0, x1, y1, bl, ln)
                body = "".join(
                    f'<line x1="{_f(a)}" y1="{_f(b)}" x2="{_f(c)}" '
                    f'y2="{_f(d)}"/>' for a, b, c, d in lines)
                reperes.append(
                    f'<g id="{tag}-tombo" fill="none" stroke="#000000" '
                    f'stroke-width="{sw}">{body}</g>')
            elif opts.marks == "rect":
                reperes.append(
                    f'<rect id="{tag}-cut" x="{_f(x0)}" y="{_f(y0)}" '
                    f'width="{_f(tw_pt)}" height="{_f(th_pt)}" fill="none" '
                    f'stroke="#000000" stroke-width="{sw}"/>')
        for bucket, acc in ((fond, fond_pages), (nums, nums_pages),
                            (reperes, rep_pages)):
            acc.append(f'<g id="Page-{pn + 1}">{"".join(bucket)}</g>'
                       if stacked else "".join(bucket))

    total_h = len(pages) * ph_pt + (len(pages) - 1) * opts.page_gap_pt
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}" version="1.1" '
        f'width="{_f(pw_pt)}" height="{_f(total_h)}" '
        f'viewBox="0 0 {_f(pw_pt)} {_f(total_h)}">\n'
        f'<defs>{"".join(defs)}</defs>\n'
        f'<g id="Fond">{"".join(fond_pages)}</g>\n'
        f'<g id="Numeros">{"".join(nums_pages)}</g>\n'
        f'<g id="Reperes">{"".join(rep_pages)}</g>\n'
        '</svg>\n')


def build_page_svg(items: list, grid: Grid, font_path,
                   opts: SvgOptions) -> str:
    """SVG markup for the tiles of ONE page (`items` = that page's items)."""
    return build_svg([items], grid, font_path, opts)


def split_pages(items: list, grid: Grid) -> list:
    per = grid.per_page
    return [items[i:i + per] for i in range(0, max(1, len(items)), per)]


def export_svg_pages(items: list, grid: Grid, font_path, opts: SvgOptions,
                     out_base: str, single_file: bool = True) -> list:
    """Write the sheet next to `out_base` (no extension): ONE svg with all
    pages stacked (`single_file`) or one svg per page. Returns the paths."""
    pages = split_pages(items, grid)
    if single_file or len(pages) == 1:
        groups = [(f"{out_base}.svg", pages)]
    else:
        groups = [(f"{out_base}_p{n + 1}.svg", [pg])
                  for n, pg in enumerate(pages)]
    paths = []
    for path, pgs in groups:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build_svg(pgs, grid, font_path, opts))
        paths.append(path)
    return paths
