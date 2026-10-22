"""Pure, UI-free logic for detecting real Japanese glyph coverage in a font.

No Tkinter/Pillow imports here on purpose — these functions must stay
testable without spinning up a GUI. The only external dependency is
fontTools (see requirements.txt).

The on-disk cache (jp_support) lives inside the same JSON file the
monolith's font-discovery cache uses (`_font_cache.json`), but this module
never reads/writes that file's "families"/"flat" keys — the cache_file path
is passed in by the caller, and this module only ever touches its own
"jp_support" sub-key, staying decoupled from the rest of the font-cache
system (still in ImageTool.py).
"""

import json
import logging
from pathlib import Path

# Sample used to probe real Japanese glyph coverage: base hiragana/katakana
# plus a handful of visually-distinct, very common Joyo kanji.
JP_PROBE_CHARS = "あいうえおんがざだアイウエオンガザダ日一国人年大十二本中長出三時行見月分後前生五間上東"
JP_KANA_CHARS  = "あいうえおんがざだアイウエオンガザダ"

_JP_SUPPORT_CACHE: dict = {}   # { file_path: bool } — in-memory mirror of on-disk cache


def font_supports_japanese(path: str) -> bool:
    """Best-effort check that a font file actually draws distinct Japanese
    glyphs, rather than just declaring them in its cmap.

    Some OTF/CID fonts (e.g. Adobe "trial"/subset builds like the Ryumin
    Std -KS/-KO variants) advertise full Adobe-Japan1 coverage in their
    cmap, but most of their kanji glyphs are placeholder squares sharing an
    identical bounding box — the cmap alone can't detect that. We draw a
    probe sample and flag fonts where too many glyphs share one bbox.
    """
    try:
        from fontTools.ttLib import TTFont
        from fontTools.pens.boundsPen import BoundsPen
    except Exception:
        logging.warning("fontTools unavailable, cannot check JP support for %s", path)
        return False
    try:
        font = TTFont(path, fontNumber=0, lazy=True)
        cmap = font.getBestCmap()
        if not cmap:
            return False
        kana_hits = sum(1 for c in JP_KANA_CHARS if ord(c) in cmap)
        if kana_hits < int(len(JP_KANA_CHARS) * 0.7):
            return False  # no real kana coverage: not a JP font
        glyphset = font.getGlyphSet()
        bboxes = []
        for c in JP_PROBE_CHARS:
            gid = cmap.get(ord(c))
            if gid is None:
                continue
            pen = BoundsPen(glyphset)
            glyphset[gid].draw(pen)
            bboxes.append(pen.bounds)
        if not bboxes:
            return False
        most_common = max(bboxes.count(b) for b in set(bboxes))
        if most_common > len(bboxes) * 0.35:
            return False  # too many identical bboxes: placeholder glyphs
        return True
    except Exception:
        logging.warning("JP support check failed for %s", path, exc_info=True)
        return False


def load_jp_support_cache(cache_file: Path) -> dict:
    """Load the on-disk jp_support map into memory (once per process)."""
    global _JP_SUPPORT_CACHE
    if _JP_SUPPORT_CACHE:
        return _JP_SUPPORT_CACHE
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _JP_SUPPORT_CACHE = json.load(f).get("jp_support", {})
        except Exception:
            _JP_SUPPORT_CACHE = {}
    return _JP_SUPPORT_CACHE


def save_jp_support_cache(cache_file: Path):
    """Persist the in-memory jp_support map into the shared font cache file,
    preserving whatever other keys (families/flat/file_count) are already
    on disk — this module only ever owns the "jp_support" key."""
    data = {}
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["jp_support"] = _JP_SUPPORT_CACHE
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def get_jp_support(cache_file: Path, path: str, persist: bool = True) -> bool:
    """Return whether a font file has real Japanese glyph coverage,
    checking the on-disk cache first and computing on miss.

    persist=False skips the disk write (caller is expected to batch many
    lookups and call save_jp_support_cache() once at the end — writing to
    disk after every single font would make a full library scan very slow)."""
    cache = load_jp_support_cache(cache_file)
    if path in cache:
        return cache[path]
    result = font_supports_japanese(path)
    cache[path] = result
    if persist:
        save_jp_support_cache(cache_file)
    return result
