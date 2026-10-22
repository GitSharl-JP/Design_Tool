"""Template loading: raster images, plus vector SVG / PDF / AI backgrounds.

* Raster (png/jpg/tif/bmp) -> Pillow.
* ``.svg``  -> rasterised with resvg for previews/PNG/PDF; the SVG exporter
  inlines the original markup so it stays fully vector in Illustrator.
* ``.pdf`` / ``.ai`` (PDF-compatible) -> page 1 rendered with pypdfium2.
  Embedded as a high-resolution image in the SVG export (no PDF->SVG
  converter without an AGPL dependency).
"""

import logging
import os
from typing import Optional

from PIL import Image

log = logging.getLogger(__name__)

RASTER_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
PDF_EXT = {".pdf", ".ai"}
VECTOR_EXT = PDF_EXT | {".svg"}
FILE_PATTERNS = "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.svg *.pdf *.ai"


def kind(path: str) -> str:
    """'raster' | 'svg' | 'pdf' | '' (unknown/missing)."""
    ext = os.path.splitext(path or "")[1].lower()
    if not path or not os.path.isfile(path):
        return ""
    if ext == ".svg":
        return "svg"
    if ext in PDF_EXT:
        return "pdf"
    return "raster"


def _render_svg(path: str, tw: int, th: int) -> Image.Image:
    import io

    import resvg_py
    png = resvg_py.svg_to_bytes(svg_path=path, width=tw, height=th,
                                background="#ffffff")
    img = Image.open(io.BytesIO(bytes(png))).convert("RGB")
    return img if img.size == (tw, th) else img.resize((tw, th),
                                                       Image.LANCZOS)


def _render_pdf(path: str, tw: int, th: int) -> Image.Image:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        page = pdf[0]
        w_pt, h_pt = page.get_size()
        scale = max(tw / w_pt, th / h_pt)
        img = page.render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()
    return img.resize((tw, th), Image.LANCZOS)


def load_template_image(path: str, tw: int, th: int) -> Optional[Image.Image]:
    """The template stretched to (tw, th) px as RGB, or None if unusable."""
    k = kind(path)
    try:
        if k == "raster":
            return Image.open(path).convert("RGB").resize((tw, th),
                                                          Image.LANCZOS)
        if k == "svg":
            return _render_svg(path, tw, th)
        if k == "pdf":
            return _render_pdf(path, tw, th)
    except Exception:
        log.exception("cannot load template %r", path)
    return None


def natural_aspect(path: str) -> float:
    """width / height of the template (1.0 when unknown)."""
    k = kind(path)
    try:
        if k == "raster":
            with Image.open(path) as im:
                return im.width / im.height
        if k == "pdf":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(path)
            try:
                w, h = pdf[0].get_size()
            finally:
                pdf.close()
            return w / h
        if k == "svg":
            import xml.etree.ElementTree as ET
            root = ET.parse(path).getroot()
            vb = root.get("viewBox")
            if vb:
                _x, _y, w, h = (float(v) for v in vb.replace(",", " ").split())
                return w / h

            def num(v):
                return float("".join(c for c in v if c in "0123456789."))
            return num(root.get("width")) / num(root.get("height"))
    except Exception:
        log.exception("cannot read size of %r", path)
    return 1.0


def load_thumbnail(path: str, max_px: int) -> Optional[Image.Image]:
    """RGB thumbnail that fits in max_px x max_px, aspect preserved."""
    if not kind(path):
        return None
    ar = natural_aspect(path)
    w, h = ((max_px, max(1, round(max_px / ar))) if ar >= 1
            else (max(1, round(max_px * ar)), max_px))
    return load_template_image(path, w, h)
