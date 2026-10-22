# =============================================================================
#  IMAGE TOOL  —  Rename / Resize / Compress
#  Python 3 + tkinter + Pillow
# =============================================================================

import os, re, sys, threading, json, subprocess
import logging
import concurrent.futures as _futures
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

try:
    from PIL import Image
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

DESKTOP = Path(os.path.expanduser("~")) / "Desktop"
log = logging.getLogger(__name__)

# ── Windows drag-and-drop via WM_DROPFILES (no extra package needed) ─────────
def _enable_drop(widget, callback):
    """
    Register `widget` as a drop target on Windows.
    `callback(paths: list[str])` is called with the list of dropped paths.
    Falls back silently on non-Windows platforms.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes, ctypes.wintypes
        user32   = ctypes.windll.user32
        shell32  = ctypes.windll.shell32
        WM_DROPFILES = 0x0233
        GWL_WNDPROC  = -4

        # On 64-bit Windows all pointer-sized returns MUST use c_ssize_t,
        # otherwise ctypes truncates to 32 bits → corrupted function pointer → crash.
        WNDPROCTYPE = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.wintypes.HWND,
            ctypes.c_uint,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )
        user32.SetWindowLongPtrW.restype  = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.CallWindowProcW.restype    = ctypes.c_ssize_t
        user32.GetParent.restype          = ctypes.wintypes.HWND

        _orig_proc = [ctypes.c_ssize_t(0)]
        _proc_ref  = [None]   # keep alive to prevent GC

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                try:
                    count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                    paths = []
                    for i in range(count):
                        buf = ctypes.create_unicode_buffer(260)
                        shell32.DragQueryFileW(wparam, i, buf, 260)
                        paths.append(buf.value)
                    shell32.DragFinish(wparam)
                    widget.after(0, lambda: callback(paths))
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(
                _orig_proc[0], hwnd, msg, wparam, lparam)

        def _attach():
            try:
                hwnd = widget.winfo_toplevel().winfo_id()
                parent = user32.GetParent(hwnd)
                if parent:
                    hwnd = parent
                shell32.DragAcceptFiles(hwnd, True)
                new_proc      = WNDPROCTYPE(_wnd_proc)
                _proc_ref[0]  = new_proc
                old           = user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC,
                                    ctypes.cast(new_proc, ctypes.c_ssize_t))
                _orig_proc[0] = ctypes.c_ssize_t(old)
            except Exception:
                pass   # drag-drop unavailable — not fatal

        widget.after(200, _attach)
    except Exception:
        pass

# -- Theme, i18n, fonts -- migrated to app/theme.py and app/i18n.py --
from app.theme import (
    BG, BG2, BG3, BG4, BORDER, ACCENT, ACCENT2, ACCENT_LIGHT, ACCENT_SOFT,
    FG, FG2, FG3, FG_ON_ACCENT, FG_GREEN, FG_RED, FG_YELLOW, RADIUS, PREVIEW_BG,
    FONT_H1, FONT_H2, FONT_LABEL, FONT_BODY, FONT_SM, FONT_HINT, FONT_MONO,
    _ui_font, _update_font_constants,
)
from app.i18n import _STRINGS, _t


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_size(b):
    if b >= 1_048_576: return f"{b/1_048_576:.2f} MB"
    return f"{b/1024:.1f} KB"

# ── Rename engine -- migrated to app/engines/rename_engine.py ────────────────
from app.engines.rename_engine import collect_files, first_digits, compute_new_name
from app.engines.rename_flow import RenameJob
from app.engines.guidemark_model import (
    Design, Segment, SheetItem, TRACKING_REF_DPI)
from app.engines.guidemark_render import (
    scale_tracking as _scale_tracking, draw_tracked as _draw_tracked,
    apply_k_convert, render_item)
from app.engines.guidemark_layout import PAPER_MM, make_grid, render_pages
from app.engines.guidemark_svg import SvgOptions, export_svg_pages
from app.engines.guidemark_paste import parse_labels
from app.engines.vector_template import (
    FILE_PATTERNS as _TPL_PATTERNS, load_thumbnail)
from app.engines.font_jp import (
    load_jp_support_cache as _load_jp_support_cache,
    save_jp_support_cache as _save_jp_support_cache,
    get_jp_support as _get_jp_support,
)

# ── Compress engine ───────────────────────────────────────────────────────────
def compress_jpeg_pillow(src: Path, dst: Path, quality: int):
    img = Image.open(src)
    if img.mode in ("RGBA","P","LA"):
        bg = Image.new("RGB", img.size, (255,255,255))
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA","LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)

def find_quality_for_target(src: Path, target_kb: int, min_q: int) -> tuple[int,int]:
    import io
    target_bytes = target_kb * 1024
    img = Image.open(src)
    if img.mode in ("RGBA","P","LA"):
        bg = Image.new("RGB", img.size, (255,255,255))
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA","LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    lo, hi, best_q, best_size = min_q, 95, -1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=mid, optimize=True)
        size = buf.tell()
        if size <= target_bytes:
            best_q, best_size, lo = mid, size, mid + 1
        else:
            hi = mid - 1
    if best_q == -1:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=min_q, optimize=True)
        best_q, best_size = min_q, buf.tell()
    return best_q, best_size

# ── Resize engine ─────────────────────────────────────────────────────────────
def compute_new_size(w, h, cfg):
    mode = cfg.get("resize_mode","1")
    tw   = cfg.get("target_w", 0)
    th   = cfg.get("target_h", 0)
    pct  = cfg.get("pct", 100)
    if mode == "1":
        r = tw / w;  return int(tw), int(h * r)
    elif mode == "2":
        r = th / h;  return int(w * r), int(th)
    elif mode in ("3", "6"):
        r = min(tw/w, th/h); return int(w*r), int(h*r)
    elif mode == "4":
        return tw, th
    else:
        r = pct / 100.0; return int(w*r), int(h*r)


def compute_canvas_fit(img: "Image.Image", cfg: dict):
    """Mode 6: fit `img` inside (target_w, target_h) keeping aspect ratio,
    then paste it centred onto a canvas of exactly that size filled with
    `canvas_color` (default black) — the letterbox/pillarbox bars seen in
    museum credit-panel exports. Optionally burns in a credit text line in
    one of the bars. Returns the finished canvas image (RGB)."""
    tw = int(cfg.get("target_w", 0))
    th = int(cfg.get("target_h", 0))
    nw, nh = compute_new_size(img.width, img.height, {**cfg, "resize_mode": "3"})
    nw, nh = max(1, nw), max(1, nh)
    fitted = img.convert("RGB").resize((nw, nh), Image.LANCZOS)

    color = cfg.get("canvas_color", "#000000")
    canvas = Image.new("RGB", (tw, th), _hex_to_rgb(color))
    ox = (tw - nw) // 2
    oy = (th - nh) // 2
    canvas.paste(fitted, (ox, oy))

    credit = (cfg.get("credit_text") or "").strip()
    if credit:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(canvas)
        font_name = cfg.get("credit_font", "Arial")
        font_size = int(cfg.get("credit_size", max(12, th // 40)))
        font = _load_pil_font(font_name, font_size)
        fg = _hex_to_rgb(cfg.get("credit_color", "#ffffff"))
        margin = max(6, font_size // 2)
        pos = cfg.get("credit_pos", "bottom-right")
        bbox = draw.textbbox((0, 0), credit, font=font)
        tw_px, th_px = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if "bottom" in pos:
            y = th - margin - th_px
        else:
            y = margin
        if "right" in pos:
            x = tw - margin - tw_px
        elif "center" in pos:
            x = (tw - tw_px) // 2
        else:
            x = margin
        draw.text((x - bbox[0], y - bbox[1]), credit, font=font, fill=fg)

    return canvas


def do_resize(src: Path, dst: Path, cfg: dict):
    img = Image.open(src)
    mode = cfg.get("resize_mode", "1")
    if mode == "6":
        result = compute_canvas_fit(img, cfg)
    else:
        nw, nh = compute_new_size(img.width, img.height, cfg)
        result = img.resize((nw, nh), Image.LANCZOS)
    q    = cfg.get("jpeg_quality", 90)
    ext  = src.suffix.lower()
    if mode == "6":
        dst = dst.with_suffix(".jpg")
        result.save(dst, "JPEG", quality=q, optimize=True)
    elif ext in (".jpg",".jpeg"):
        result.save(dst, "JPEG", quality=q, optimize=True)
    else:
        result.save(dst)

# =============================================================================
#  GUI helpers -- migrated to app/ui_kit.py
# =============================================================================
from app.ui_kit import (
    styled_frame, label, btn, entry, _blend_hex, _center_win, _arrow_scale,
    separator, _enable_mousewheel_scroll, combo, make_scrolled_list,
    section_title, property_row, card, scrollbar,
    scaled_geometry, scaled_minsize, _dpi_scale, grid_row, collapsible_section,
)
from app.widgets.wizard_widgets import PreviewList, LogPanel, StepBar, WizardPanel
from app.wizards.rename_settings_panel import RenameSettingsPanel
from app.wizards.guidemark_sheet import SheetPanel

# =============================================================================
#  Folder browser widget (reusable)
# =============================================================================
class FolderBrowser(tk.Frame):
    def __init__(self, parent, on_select=None, next_cb=None, **kw):
        """on_select(path) called when folder chosen. next_cb() optional — called after select."""
        super().__init__(parent, bg=BG, **kw)
        self.on_select   = on_select
        self.next_cb     = next_cb
        self._history    = []
        self._current    = DESKTOP
        self._build()
        self._refresh()

    def _build(self):
        top = styled_frame(self)
        top.pack(fill="x", pady=(0,6))
        self._path_var = tk.StringVar()
        label(top, "📂", font=("Segoe UI",11), bg=BG).pack(side="left")
        self._path_lbl = label(top, "", font=FONT_SM, fg=FG2, bg=BG)
        self._path_lbl.pack(side="left", padx=4)
        self._back_btn = btn(top, "← Back", self._go_back, primary=False)
        self._back_btn.pack(side="right")

        frm, self._lb = make_scrolled_list(self, height=10)
        frm.pack(fill="both", expand=True)
        self._lb.bind("<Double-Button-1>", self._enter_folder)

        bot = styled_frame(self)
        bot.pack(fill="x", pady=(6,0))
        btn(bot, "✔  Select this folder", self._select, primary=True).pack(side="left")
        btn(bot, "Type path…", self._custom_path, primary=False).pack(side="left", padx=6)

        # Windows drag-and-drop — dropping a folder navigates to it
        def _on_drop(paths):
            p = Path(paths[0])
            target = p if p.is_dir() else p.parent
            self._current = target
            self._refresh()
        _enable_drop(self._lb, _on_drop)

    def _refresh(self):
        self._path_lbl.config(text=str(self._current))
        self._back_btn.config(state="normal" if self._history else "disabled")
        self._lb.delete(0, "end")
        try:
            entries = sorted(self._current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            subs  = [d for d in entries if d.is_dir()  and not d.name.startswith(".")]
            files = [f for f in entries if f.is_file() and not f.name.startswith(".")]
        except PermissionError:
            subs = []; files = []
        for d in subs:
            self._lb.insert("end", "  📁  " + d.name)
        for f in files:
            self._lb.insert("end", "  📄  " + f.name)
        self._subs  = subs
        self._files_shown = files
        # colour the file entries dimmer so folders stand out
        base = len(subs)
        for i in range(len(files)):
            self._lb.itemconfig(base + i, fg=FG2)

    def _enter_folder(self, _=None):
        sel = self._lb.curselection()
        if not sel: return
        idx = sel[0]
        if idx < len(self._subs):
            self._history.append(self._current)
            self._current = self._subs[idx]
            self._refresh()
        # clicking a file does nothing (it's display-only)

    def _go_back(self):
        if self._history:
            self._current = self._history.pop()
            self._refresh()

    def _select(self):
        if self.on_select:
            self.on_select(self._current)
        if self.next_cb:
            self.next_cb()

    def _custom_path(self):
        p = filedialog.askdirectory(initialdir=str(self._current))
        if p:
            self._current = Path(p)
            self._refresh()
            if self.on_select:
                self.on_select(self._current)
            if self.next_cb:
                self.next_cb()

    def get(self):
        return self._current

# =============================================================================
#  Preview list widget
# =============================================================================
# PreviewList, LogPanel -- migrated to app/widgets/wizard_widgets.py

# =============================================================================
#  Image preview pane (live thumbnail + info)
# =============================================================================
class ImagePreviewPane(tk.Frame):
    """Right-side live preview for Compress/Resize settings pages.
    Call load_image(path) to set source, then update_preview(render_fn)
    where render_fn(img) -> PIL.Image of the result.
    Debounces rapid calls with 250 ms delay."""

    THUMB_W = 340
    THUMB_H = 280

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG2, **kw)
        self._src_path  = None
        self._src_img   = None
        self._tk_img    = None
        self._after_id  = None
        self._build()

    def _build(self):
        label(self, "Image preview", font=FONT_H2, bg=BG2).pack(pady=(10,4))
        separator(self).pack(fill="x", padx=10)

        # File picker row
        pick_row = styled_frame(self, bg=BG2)
        pick_row.pack(fill="x", padx=10, pady=(6,0))
        self._file_lbl = label(pick_row, "No image selected", fg=FG2, font=FONT_SM, bg=BG2)
        self._file_lbl.pack(side="left", fill="x", expand=True)
        btn(pick_row, "Pick image", self._pick, primary=False).pack(side="right")

        # Canvas
        self._canvas = tk.Canvas(self, width=self.THUMB_W, height=self.THUMB_H,
                                  bg=BG3, highlightthickness=0)
        self._canvas.pack(padx=10, pady=8)
        self._canvas.create_text(self.THUMB_W//2, self.THUMB_H//2,
                                  text="Select an image to preview",
                                  fill=FG2, font=FONT_SM, tags="placeholder")

        # Info labels
        self._info_orig = label(self, "", fg=FG2, font=FONT_SM, bg=BG2)
        self._info_orig.pack()
        self._info_new  = label(self, "", fg=FG_GREEN, font=FONT_SM, bg=BG2)
        self._info_new.pack(pady=(0,8))

    def _pick(self):
        p = filedialog.askopenfilename(
            title="Pick a preview image",
            initialdir=str(DESKTOP),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("All", "*.*")])
        if p:
            self.load_image(Path(p))

    def load_image(self, path: Path):
        try:
            img = Image.open(path)
            img.load()   # force decode
            self._src_path = path
            self._src_img  = img
            self._file_lbl.config(text=path.name)
            self._info_orig.config(
                text=f"Original: {img.width}×{img.height}  {fmt_size(path.stat().st_size)}")
            self._info_new.config(text="")
            self._show_image(img)
        except Exception:
            self._src_img = None

    def _show_image(self, img: Image.Image):
        """Fit image into canvas and display."""
        self._canvas.delete("placeholder")
        display = img.copy()
        display.thumbnail((self.THUMB_W, self.THUMB_H), Image.LANCZOS)
        try:
            from PIL import ImageTk
            self._tk_img = ImageTk.PhotoImage(display)
            self._canvas.delete("all")
            cw, ch = self.THUMB_W, self.THUMB_H
            self._canvas.create_image(cw//2, ch//2, anchor="center", image=self._tk_img)
        except Exception:
            pass

    def schedule_update(self, render_fn, delay_ms=280):
        """Call render_fn() in a background thread after debounce delay."""
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(delay_ms, lambda: self._do_update(render_fn))

    def _do_update(self, render_fn):
        self._after_id = None
        if self._src_img is None:
            return
        import threading
        def worker():
            try:
                result_img, info_str = render_fn(self._src_img.copy(), self._src_path)
                self.after(0, lambda: self._apply(result_img, info_str))
            except Exception as e:
                self.after(0, lambda _e=e: self._info_new.config(
                    text=f"Error: {_e}", fg=FG_RED))
        threading.Thread(target=worker, daemon=True).start()

    def _apply(self, result_img, info_str):
        self._show_image(result_img)
        self._info_new.config(text=info_str, fg=FG_GREEN)

    def clear(self):
        self._src_img  = None
        self._src_path = None
        self._file_lbl.config(text="No image selected")
        self._info_orig.config(text="")
        self._info_new.config(text="")
        self._canvas.delete("all")
        self._canvas.create_text(self.THUMB_W//2, self.THUMB_H//2,
                                  text="Select an image to preview",
                                  fill=FG2, font=FONT_SM, tags="placeholder")

# StepBar, WizardPanel -- migrated to app/widgets/wizard_widgets.py

# =============================================================================
#  TOOL: RENAME
# =============================================================================
class RenameWizard(tk.Toplevel):
    STEPS = ["1 · Folder", "2 · Filter", "3 · Settings", "4 · Preview", "5 · Done"]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Rename")
        self.configure(bg=BG)
        scaled_geometry(self, 860, 680)
        self.resizable(True, True)
        self._step   = 0
        self._folder = None
        self._files  = []
        self._cfg    = {}
        self._build()
        self._goto(0)

    def _build(self):
        # Header
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16,0))
        label(hdr, "✏  Rename", font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")

        separator(self).pack(fill="x", padx=20, pady=10)

        # Content area
        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)

        # Nav bar
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0,16))
        self._back_btn = btn(nav, "← Back", self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, "Next →", self._next, primary=True)
        self._next_btn.pack(side="right")

        # Build step frames
        self._s0 = self._build_s0()
        self._s1 = self._build_s1()
        self._s2 = self._build_s2()
        self._s3 = self._build_s3()
        self._s4 = self._build_s4()
        self._panels = [self._s0, self._s1, self._s2, self._s3, self._s4]

    # ── Step 0: folder ────────────────────────────────────────────────────────
    def _build_s0(self):
        p = WizardPanel(self._content)
        label(p, "Select a folder", font=FONT_H2).pack(anchor="w", pady=(0,8))
        self._browser = FolderBrowser(p, next_cb=self._next)
        self._browser.pack(fill="both", expand=True)
        return p

    # ── Step 1: filter ────────────────────────────────────────────────────────
    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, "File filter", font=FONT_H2).pack(anchor="w", pady=(0,10))

        self._ext_var = tk.StringVar(value="*.jpg")
        exts = ["*.jpg", "*.png", "*.jpg + *.png", "*.*", "custom…"]
        for e in exts:
            tk.Radiobutton(p, text=e, variable=self._ext_var, value=e,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        frm = styled_frame(p)
        frm.pack(anchor="w", fill="x", pady=(4,0))
        label(frm, "Custom ext:").pack(side="left")
        self._custom_ext = entry(frm, width=12)
        self._custom_ext.pack(side="left", padx=6)

        separator(p).pack(fill="x", pady=10)
        self._recurse_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subfolders (recursive)",
                       variable=self._recurse_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        self._perfolder_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Process each subfolder independently",
                       variable=self._perfolder_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=4)
        return p

    # ── Step 2: rename settings ───────────────────────────────────────────────
    def _build_s2(self):
        p = WizardPanel(self._content)
        self._settings_panel = RenameSettingsPanel(p)
        self._settings_panel.pack(fill="both", expand=True)
        return p

    # ── Step 3: preview ───────────────────────────────────────────────────────
    def _build_s3(self):
        p = WizardPanel(self._content)

        hdr_row = styled_frame(p)
        hdr_row.pack(fill="x", pady=(0, 8))
        label(hdr_row, "Preview  —  review before renaming", font=FONT_H2).pack(side="left")
        self._preview_count_lbl = label(hdr_row, "", fg=FG2, font=FONT_SM)
        self._preview_count_lbl.pack(side="right")

        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)

        confirm_row = styled_frame(p)
        confirm_row.pack(fill="x", pady=(10, 0))
        label(confirm_row,
              "⚠  Renaming is permanent. Review the list above, then click  ✔ Execute  to proceed.",
              fg=FG_YELLOW, font=FONT_SM).pack(side="left")
        return p

    # ── Step 4: done ──────────────────────────────────────────────────────────
    def _build_s4(self):
        p = WizardPanel(self._content)
        self._log = LogPanel(p)
        self._log.pack(fill="both", expand=True)
        return p

    # ── Navigation ────────────────────────────────────────────────────────────
    def _goto(self, step):
        for pan in self._panels: pan.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text="↩  New rename", command=lambda: self._goto(0))
        elif step == last - 1:
            self._next_btn.config(text="✔  Execute", command=self._execute)
        else:
            self._next_btn.config(text="Next →", command=self._next)

    def _back(self): self._goto(max(0, self._step - 1))

    def _next(self):
        step = self._step
        if step == 0:
            self._folder = self._browser.get()
            self._goto(1)
        elif step == 1:
            if not self._load_files(): return
            self._goto(2)
        elif step == 2:
            self._build_cfg()
            self._build_preview()
            self._goto(3)
        elif step == 3:
            self._goto(4)

    def _load_files(self):
        ext_choice = self._ext_var.get()
        if ext_choice == "*.jpg + *.png":
            exts = ["*.jpg","*.png"]
        elif ext_choice == "custom…":
            c = self._custom_ext.get().strip().lstrip("*.")
            exts = [f"*.{c}"] if c else ["*.*"]
        elif ext_choice == "*.*":
            exts = ["*.*"]
        else:
            exts = [ext_choice]

        recurse = self._recurse_var.get()
        self._files = collect_files(self._folder, exts, recurse)
        if not self._files:
            messagebox.showwarning("No files", f"No matching files found in:\n{self._folder}")
            return False
        return True

    def _build_cfg(self):
        self._cfg = self._settings_panel.get_cfg()

    def _build_preview(self):
        rows = RenameJob(self._files, self._cfg).preview()
        self._preview.load(rows)
        if hasattr(self, "_preview_count_lbl"):
            self._preview_count_lbl.config(text=f"{len(rows)} file(s)")

    def _execute(self):
        self._goto(4)
        self._log.clear()
        job = RenameJob(self._files, self._cfg)
        success, failures, _errors = job.execute(log_cb=self._log.log)
        self._log.log(f"\n  Done — {success} file(s) renamed.", "ok")
        if failures:
            self._log.log(f"  {failures} file(s) failed.", "err")
        self._next_btn.config(text="↩  New rename", command=lambda: self._goto(0))

# =============================================================================
#  TOOL: COMPRESS
# =============================================================================
class CompressWizard(tk.Toplevel):
    STEPS = ["1 · Folder", "2 · Filter", "3 · Settings", "4 · Output", "5 · Preview", "6 · Done"]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Compress")
        self.configure(bg=BG)
        scaled_geometry(self, 1100, 720)
        self.resizable(True, True)
        self._step   = 0
        self._folder = None
        self._files  = []
        self._build()
        self._goto(0)

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16,0))
        label(hdr, "🗜  Compress", font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)
        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0,16))
        self._back_btn = btn(nav, "← Back", self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, "Next →", self._next, primary=True)
        self._next_btn.pack(side="right")

        self._s0 = self._build_s0()
        self._s1 = self._build_s1()
        self._s2 = self._build_s2()
        self._s3 = self._build_s3()
        self._s4 = self._build_s4()
        self._s5 = self._build_s5()
        self._panels = [self._s0,self._s1,self._s2,self._s3,self._s4,self._s5]

    def _build_s0(self):  # Compress wizard
        p = WizardPanel(self._content)
        label(p, "Select a folder", font=FONT_H2).pack(anchor="w", pady=(0,8))
        self._browser = FolderBrowser(p, next_cb=self._next)
        self._browser.pack(fill="both", expand=True)
        return p

    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, "File filter", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._ext_var = tk.StringVar(value="*.jpg")
        for e in ["*.jpg","*.png","*.jpg + *.png","custom…"]:
            tk.Radiobutton(p, text=e, variable=self._ext_var, value=e,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=4)
        label(frm, "Custom ext:").pack(side="left")
        self._custom_ext = entry(frm, width=12)
        self._custom_ext.pack(side="left", padx=6)
        separator(p).pack(fill="x", pady=10)
        self._recurse_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subfolders (recursive)",
                       variable=self._recurse_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        return p

    def _build_s2(self):  # Compress: settings + live image preview split
        p = WizardPanel(self._content)
        split = styled_frame(p)
        split.pack(fill="both", expand=True)
        left  = styled_frame(split)
        left.pack(side="left", fill="both", expand=True, padx=(0,8))
        right = ImagePreviewPane(split, width=370)
        right.pack(side="right", fill="y")
        self._comp_preview_pane = right

        label(left, "Compression settings", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._comp_mode = tk.StringVar(value="1")
        tk.Radiobutton(left, text="Fixed quality  — set quality once for all files",
                       variable=self._comp_mode, value="1",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY,
                       command=self._refresh_comp).pack(anchor="w")
        tk.Radiobutton(left, text="Target file size  — quality auto-adjusted per file",
                       variable=self._comp_mode, value="2",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY,
                       command=self._refresh_comp).pack(anchor="w", pady=2)

        self._comp_opts = styled_frame(left)
        self._comp_opts.pack(fill="x", pady=(6,0), padx=20)

        # fixed quality
        self._fq_frame = styled_frame(self._comp_opts)
        r = styled_frame(self._fq_frame); r.pack(fill="x")
        label(r, "JPEG quality  (1–100):").pack(side="left")
        self._quality_var = tk.IntVar(value=85)
        sc = tk.Scale(r, from_=1, to=100, orient="horizontal",
                      variable=self._quality_var,
                      bg=BG, fg=FG, troughcolor=BG3,
                      highlightthickness=0, length=180)
        sc.pack(side="left", padx=8)
        _arrow_scale(sc)
        self._q_lbl = label(r, "85", fg=FG_GREEN)
        self._q_lbl.pack(side="left")
        def _on_q(*_):
            self._q_lbl.config(text=str(self._quality_var.get()))
            self._trigger_comp_preview()
        self._quality_var.trace_add("write", _on_q)

        # target size
        self._ts_frame = styled_frame(self._comp_opts)
        r2 = styled_frame(self._ts_frame); r2.pack(fill="x")
        label(r2, "Max file size (KB):").pack(side="left")
        self._target_kb = entry(r2, width=8); self._target_kb.insert(0,"500")
        self._target_kb.pack(side="left", padx=6)
        r3 = styled_frame(self._ts_frame); r3.pack(fill="x", pady=4)
        label(r3, "Min quality floor:").pack(side="left")
        self._min_q_var = tk.IntVar(value=20)
        _sc_minq = tk.Scale(r3, from_=1, to=95, orient="horizontal",
                 variable=self._min_q_var,
                 bg=BG, fg=FG, troughcolor=BG3,
                 highlightthickness=0, length=160)
        _sc_minq.pack(side="left", padx=8)
        _arrow_scale(_sc_minq)

        separator(left).pack(fill="x", pady=10)
        label(left, "PNG handling", font=FONT_H2).pack(anchor="w", pady=(0,4))
        label(left, "PNG is lossless — compression has limited effect.",
              fg=FG2, font=FONT_SM).pack(anchor="w")
        self._png_mode = tk.StringVar(value="skip")
        tk.Radiobutton(left, text="Skip PNG files",
                       variable=self._png_mode, value="skip",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        tk.Radiobutton(left, text="Keep as PNG  (lossless optimise)",
                       variable=self._png_mode, value="keepng",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        tk.Radiobutton(left, text="Convert PNG → JPEG  (loses transparency)",
                       variable=self._png_mode, value="convert",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")

        self._refresh_comp()
        return p

    def _refresh_comp(self):
        for w in self._comp_opts.winfo_children(): w.pack_forget()
        if self._comp_mode.get() == "1": self._fq_frame.pack(fill="x")
        else: self._ts_frame.pack(fill="x")
        self._trigger_comp_preview()

    def _trigger_comp_preview(self):
        import io
        pane = getattr(self, '_comp_preview_pane', None)
        if pane is None or pane._src_img is None: return
        mode = self._comp_mode.get()
        q    = self._quality_var.get()
        try: tkb = int(re.sub(r'[^0-9]','', self._target_kb.get()))
        except: tkb = 500
        minq = self._min_q_var.get()
        def render(img: Image.Image, src_path: Path):
            orig_size = src_path.stat().st_size if src_path and src_path.exists() else 0
            work = img.copy()
            if work.mode not in ("RGB","L"):
                bg2 = Image.new("RGB", work.size, (255,255,255))
                try: bg2.paste(work, mask=work.split()[-1])
                except: bg2.paste(work)
                work = bg2
            elif work.mode != "RGB":
                work = work.convert("RGB")
            if mode == "1":
                use_q = q
            else:
                if orig_size and orig_size <= tkb*1024:
                    use_q = 95
                elif src_path and src_path.exists():
                    use_q, _ = find_quality_for_target(src_path, tkb, minq)
                else:
                    use_q = minq
            buf = io.BytesIO()
            work.save(buf, "JPEG", quality=use_q, optimize=True)
            new_size = buf.tell()
            result = Image.open(io.BytesIO(buf.getvalue()))
            saving = round((1 - new_size/max(orig_size,1))*100) if orig_size else 0
            info = f"Q{use_q}  →  {fmt_size(new_size)}  (-{saving}%)"
            return result, info
        pane.schedule_update(render)

    def _build_s3(self):
        p = WizardPanel(self._content)
        label(p, "Output", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._out_mode = tk.StringVar(value="subfolder")
        tk.Radiobutton(p, text="Save copies to a subfolder  (safe)",
                       variable=self._out_mode, value="subfolder",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        tk.Radiobutton(p, text="Overwrite original files",
                       variable=self._out_mode, value="overwrite",
                       bg=BG, fg=FG_YELLOW, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=2)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=6)
        label(frm, "Subfolder name:").pack(side="left")
        self._subfolder_name = entry(frm, width=20)
        self._subfolder_name.insert(0,"compressed")
        self._subfolder_name.pack(side="left", padx=6)
        return p

    def _build_s4(self):
        p = WizardPanel(self._content)
        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)
        return p

    def _build_s5(self):
        p = WizardPanel(self._content)
        self._log = LogPanel(p)
        self._log.pack(fill="both", expand=True)
        return p

    def _goto(self, step):
        for pan in self._panels: pan.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text="↩  New compression", command=lambda: self._goto(0))
        elif step == last - 1:
            self._next_btn.config(text="✔  Execute", command=self._execute)
        else:
            self._next_btn.config(text="Next →", command=self._next)

    def _back(self): self._goto(max(0, self._step - 1))

    def _next(self):
        s = self._step
        if s == 0:   self._folder = self._browser.get(); self._goto(1)
        elif s == 1:
            if not self._load_files(): return
            self._goto(2)
        elif s == 2: self._goto(3)
        elif s == 3:
            self._build_preview(); self._goto(4)
        elif s == 4: self._goto(5)

    def _load_files(self):
        ext_choice = self._ext_var.get()
        if ext_choice == "*.jpg + *.png": exts = ["*.jpg","*.png"]
        elif ext_choice == "custom…":
            c = self._custom_ext.get().strip().lstrip("*.")
            exts = [f"*.{c}"] if c else ["*.*"]
        else: exts = [ext_choice]
        self._files = collect_files(self._folder, exts, self._recurse_var.get())
        if not self._files:
            messagebox.showwarning("No files", f"No matching files in:\n{self._folder}")
            return False
        return True

    def _build_preview(self):
        import io
        rows = []
        mode     = self._comp_mode.get()
        png_mode = self._png_mode.get()
        for f in self._files[:20]:
            orig = f.stat().st_size
            ext  = f.suffix.lower()
            is_png = (ext == ".png")
            if is_png and png_mode == "skip":
                rows.append((f.name, "—", "SKIPPED (PNG)"))
                continue
            if is_png and png_mode == "keepng":
                rows.append((f.name, "→", f"{fmt_size(orig)}  (PNG kept, lossless optimise)"))
                continue
            try:
                if mode == "1":
                    q = self._quality_var.get()
                    buf = io.BytesIO()
                    img = Image.open(f)
                    if img.mode not in ("RGB","L"):
                        bg = Image.new("RGB", img.size, (255,255,255))
                        try: bg.paste(img, mask=img.split()[-1])
                        except: bg.paste(img)
                        img = bg
                    img.save(buf, "JPEG", quality=q, optimize=True)
                    new_size  = buf.tell()
                    saving    = round((1 - new_size/orig)*100)
                    rows.append((f.name, "→", f"{fmt_size(orig)}  →  {fmt_size(new_size)}  (Q{q}, -{saving}%)"))
                else:
                    try: tkb = int(re.sub(r'[^0-9]','', self._target_kb.get()))
                    except: tkb = 500
                    minq = self._min_q_var.get()
                    if orig <= tkb*1024:
                        rows.append((f.name, "—", f"{fmt_size(orig)}  (already under target)"))
                    else:
                        q, new_size = find_quality_for_target(f, tkb, minq)
                        saving = round((1 - new_size/orig)*100)
                        rows.append((f.name, "→", f"{fmt_size(orig)}  →  {fmt_size(new_size)}  (Q{q}, -{saving}%)"))
            except Exception as e:
                rows.append((f.name, "!", str(e)))
        if len(self._files) > 20:
            rows.append(("…", "", f"and {len(self._files)-20} more file(s)"))
        self._preview.load(rows)

    def _execute(self):
        self._goto(5)
        self._log.clear()
        mode     = self._comp_mode.get()
        overwrite= self._out_mode.get() == "overwrite"
        subname  = self._subfolder_name.get().strip() or "compressed"
        q_fixed  = self._quality_var.get()
        try: tkb = int(re.sub(r'[^0-9]','', self._target_kb.get()))
        except: tkb = 500
        minq      = self._min_q_var.get()
        png_mode  = self._png_mode.get()   # "skip" | "keepng" | "convert"
        success = 0; failures = 0; skipped = 0; saved = 0

        for f in self._files:
            ext    = f.suffix.lower()
            is_png = (ext == ".png")
            if is_png and png_mode == "skip":
                skipped += 1; continue
            orig = f.stat().st_size
            try:
                if is_png and png_mode == "keepng":
                    # Save as optimised PNG — lossless
                    out_name = f.stem + ".png"
                    if overwrite:
                        dst = f
                    else:
                        d = f.parent / subname; d.mkdir(exist_ok=True)
                        dst = d / out_name
                    img = Image.open(f)
                    img.save(dst, "PNG", optimize=True)
                else:
                    out_name = f.stem + ".jpg"
                    if overwrite:
                        dst = f.parent / out_name if is_png else f
                    else:
                        d = f.parent / subname; d.mkdir(exist_ok=True)
                        dst = d / out_name

                    if mode == "1":
                        compress_jpeg_pillow(f, dst, q_fixed)
                    else:
                        if orig <= tkb*1024: skipped += 1; continue
                        q, _ = find_quality_for_target(f, tkb, minq)
                        compress_jpeg_pillow(f, dst, q)

                new_size = dst.stat().st_size
                saved   += max(orig - new_size, 0)
                if overwrite and is_png and png_mode == "convert" and dst.exists() and dst != f:
                    f.unlink()
                self._log.log(f"  {f.name}  →  {fmt_size(orig)} → {fmt_size(new_size)}", "ok")
                success += 1
            except Exception as e:
                self._log.log(f"  FAILED: {f.name} — {e}", "err")
                failures += 1

        total = sum(f.stat().st_size for f in self._files if f.exists())
        pct   = round(saved / max(total,1) * 100)
        self._log.log(f"\n  Done — {success} file(s) compressed.  Saved {fmt_size(max(saved,0))} (-{pct}%)", "ok")
        if skipped:  self._log.log(f"  {skipped} file(s) skipped.", "dim")
        if failures: self._log.log(f"  {failures} file(s) failed.", "err")
        self._next_btn.config(text="↩  New compression", command=lambda: self._goto(0))

# =============================================================================
#  TOOL: RESIZE
# =============================================================================
class ResizeWizard(tk.Toplevel):
    STEPS = ["1 · Folder", "2 · Filter", "3 · Settings", "4 · Output", "5 · Preview", "6 · Done"]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Resize")
        self.configure(bg=BG)
        scaled_geometry(self, 1100, 700)
        self.resizable(True, True)
        self._step   = 0
        self._folder = None
        self._files  = []
        self._build()
        self._goto(0)

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16,0))
        label(hdr, "⤡  Resize", font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)
        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0,16))
        self._back_btn = btn(nav, "← Back", self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, "Next →", self._next, primary=True)
        self._next_btn.pack(side="right")

        self._s0 = self._build_s0()
        self._s1 = self._build_s1()
        self._s2 = self._build_s2()
        self._s3 = self._build_s3()
        self._s4 = self._build_s4()
        self._s5 = self._build_s5()
        self._panels = [self._s0,self._s1,self._s2,self._s3,self._s4,self._s5]

    def _build_s0(self):  # Resize wizard
        p = WizardPanel(self._content)
        label(p, "Select a folder", font=FONT_H2).pack(anchor="w", pady=(0,8))
        self._browser = FolderBrowser(p, next_cb=self._next)
        self._browser.pack(fill="both", expand=True)
        return p

    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, "File filter", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._ext_var = tk.StringVar(value="*.jpg")
        for e in ["*.jpg","*.png","*.jpg + *.png","custom…"]:
            tk.Radiobutton(p, text=e, variable=self._ext_var, value=e,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=1)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=4)
        label(frm, "Custom ext:").pack(side="left")
        self._custom_ext = entry(frm, width=12)
        self._custom_ext.pack(side="left", padx=6)
        separator(p).pack(fill="x", pady=10)
        self._recurse_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subfolders (recursive)",
                       variable=self._recurse_var,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        return p

    def _build_s2(self):  # Resize: settings + live image preview split
        p = WizardPanel(self._content)
        split = styled_frame(p)
        split.pack(fill="both", expand=True)
        left  = styled_frame(split)
        left.pack(side="left", fill="both", expand=True, padx=(0,8))
        right = ImagePreviewPane(split, width=370)
        right.pack(side="right", fill="y")
        self._resize_preview_pane = right

        label(left, "Resize mode", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._resize_mode = tk.StringVar(value="1")
        modes = [
            ("1","Fixed width  (keep aspect ratio)"),
            ("2","Fixed height  (keep aspect ratio)"),
            ("3","Fit inside a box  (keep aspect ratio)"),
            ("4","Exact size  (may stretch)"),
            ("5","Scale by percentage"),
            ("6","Fit on canvas  (black bars + credit text)"),
        ]
        for val, txt in modes:
            tk.Radiobutton(left, text=txt, variable=self._resize_mode, value=val,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY,
                           command=self._refresh_resize).pack(anchor="w", pady=1)

        self._resize_opts = styled_frame(left)
        self._resize_opts.pack(fill="x", pady=(8,0), padx=20)

        def dim_row(parent, label_txt):
            r = styled_frame(parent); r.pack(fill="x", pady=2)
            label(r, label_txt).pack(side="left")
            e = entry(r, width=8); e.pack(side="left", padx=6)
            label(r, "px", fg=FG2, font=FONT_SM).pack(side="left")
            # trigger preview when user finishes typing
            e.bind("<FocusOut>", lambda _: self._trigger_resize_preview())
            e.bind("<Return>",   lambda _: self._trigger_resize_preview())
            return e

        self._tw = dim_row(self._resize_opts, "Width:")
        self._th = dim_row(self._resize_opts, "Height:")
        self._pct_entry = entry(self._resize_opts, width=6)
        self._pct_entry.bind("<FocusOut>", lambda _: self._trigger_resize_preview())
        self._pct_entry.bind("<Return>",   lambda _: self._trigger_resize_preview())

        # ── Mode 6 options: canvas colour + credit text ─────────────────────
        self._canvas_opts = styled_frame(left)

        self._canvas_color = tk.StringVar(value="#000000")
        crow = styled_frame(self._canvas_opts); crow.pack(fill="x", pady=2)
        label(crow, "Canvas colour:").pack(side="left")
        self._canvas_sw = tk.Frame(crow, width=20, height=20, bg=self._canvas_color.get(), relief="flat")
        self._canvas_sw.pack_propagate(False)
        self._canvas_sw.pack(side="left", padx=6)
        def _pick_canvas_color():
            dlg = ColorPickerDialog(self, initial=self._canvas_color.get())
            self.wait_window(dlg)
            if dlg.result:
                self._canvas_color.set(dlg.result)
                self._canvas_sw.config(bg=dlg.result)
                self._trigger_resize_preview()
        btn(crow, "Choose…", _pick_canvas_color, primary=False).pack(side="left")

        crow2 = styled_frame(self._canvas_opts); crow2.pack(fill="x", pady=(8,2))
        label(crow2, "Credit text:").pack(side="left")
        self._credit_text = entry(crow2, width=22)
        self._credit_text.pack(side="left", padx=6)
        self._credit_text.bind("<FocusOut>", lambda _: self._trigger_resize_preview())
        self._credit_text.bind("<Return>",   lambda _: self._trigger_resize_preview())

        crow3 = styled_frame(self._canvas_opts); crow3.pack(fill="x", pady=2)
        label(crow3, "Position:").pack(side="left")
        self._credit_pos = tk.StringVar(value="bottom-right")
        pos_combo = combo(crow3, self._credit_pos,
                           ["bottom-right","bottom-left","bottom-center","top-right","top-left"],
                           width=14)
        pos_combo.pack(side="left", padx=6)
        pos_combo.bind("<<ComboboxSelected>>", lambda _: self._trigger_resize_preview())

        crow4 = styled_frame(self._canvas_opts); crow4.pack(fill="x", pady=2)
        label(crow4, "Text colour:").pack(side="left")
        self._credit_color = tk.StringVar(value="#ffffff")
        self._credit_sw = tk.Frame(crow4, width=20, height=20, bg=self._credit_color.get(), relief="flat")
        self._credit_sw.pack_propagate(False)
        self._credit_sw.pack(side="left", padx=6)
        def _pick_credit_color():
            dlg = ColorPickerDialog(self, initial=self._credit_color.get())
            self.wait_window(dlg)
            if dlg.result:
                self._credit_color.set(dlg.result)
                self._credit_sw.config(bg=dlg.result)
                self._trigger_resize_preview()
        btn(crow4, "Choose…", _pick_credit_color, primary=False).pack(side="left")

        separator(left).pack(fill="x", pady=10)
        label(left, "JPEG quality for saved files", font=FONT_H2).pack(anchor="w", pady=(0,6))
        rq = styled_frame(left); rq.pack(fill="x")
        self._jpeg_q = tk.IntVar(value=90)
        sc = tk.Scale(rq, from_=1, to=100, orient="horizontal",
                      variable=self._jpeg_q,
                      bg=BG, fg=FG, troughcolor=BG3,
                      highlightthickness=0, length=180)
        sc.pack(side="left", padx=(0,8))
        _arrow_scale(sc)
        ql = label(rq, "90", fg=FG_GREEN); ql.pack(side="left")
        def _on_rq(*_):
            ql.config(text=str(self._jpeg_q.get()))
            self._trigger_resize_preview()
        self._jpeg_q.trace_add("write", _on_rq)

        self._refresh_resize()
        return p

    def _refresh_resize(self):
        m = self._resize_mode.get()
        self._tw.master.pack_forget()
        self._th.master.pack_forget()
        self._pct_entry.pack_forget()
        self._canvas_opts.pack_forget()
        if m == "1":
            self._tw.master.pack(fill="x", pady=2)
        elif m == "2":
            self._th.master.pack(fill="x", pady=2)
        elif m in ("3","4"):
            self._tw.master.pack(fill="x", pady=2)
            self._th.master.pack(fill="x", pady=2)
        elif m == "6":
            self._tw.master.pack(fill="x", pady=2)
            self._th.master.pack(fill="x", pady=2)
            self._canvas_opts.pack(fill="x", pady=(8,0))
        else:
            r = styled_frame(self._resize_opts); r.pack(fill="x", pady=2)
            label(r, "Scale %:").pack(side="left")
            self._pct_entry.pack(side="left", padx=6)
            label(r, "%", fg=FG2, font=FONT_SM).pack(side="left")
        self._trigger_resize_preview()

    def _trigger_resize_preview(self):
        pane = getattr(self, '_resize_preview_pane', None)
        if pane is None or pane._src_img is None: return
        cfg = self._get_resize_cfg()
        def render(img: Image.Image, src_path: Path):
            w, h = img.width, img.height
            if cfg.get("resize_mode") == "6":
                try:
                    result = compute_canvas_fit(img, cfg)
                except Exception:
                    return img, "Enter valid dimensions"
                info = f"{w}×{h}  →  {result.width}×{result.height}  (canvas)"
                return result, info
            try:
                nw, nh = compute_new_size(w, h, cfg)
                if nw <= 0 or nh <= 0:
                    return img, "Invalid dimensions"
            except Exception:
                return img, "Enter valid dimensions"
            result = img.resize((nw, nh), Image.LANCZOS)
            info = f"{w}×{h}  →  {nw}×{nh}"
            return result, info
        pane.schedule_update(render)

    def _build_s3(self):
        p = WizardPanel(self._content)
        label(p, "Output", font=FONT_H2).pack(anchor="w", pady=(0,10))
        self._out_mode = tk.StringVar(value="subfolder")
        tk.Radiobutton(p, text="Save copies to a subfolder  (safe)",
                       variable=self._out_mode, value="subfolder",
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w")
        tk.Radiobutton(p, text="Overwrite original files",
                       variable=self._out_mode, value="overwrite",
                       bg=BG, fg=FG_YELLOW, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=2)
        frm = styled_frame(p); frm.pack(anchor="w", fill="x", pady=6)
        label(frm, "Subfolder name:").pack(side="left")
        self._subfolder_name = entry(frm, width=20)
        self._subfolder_name.insert(0,"resized")
        self._subfolder_name.pack(side="left", padx=6)
        return p

    def _build_s4(self):
        p = WizardPanel(self._content)
        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)
        return p

    def _build_s5(self):
        p = WizardPanel(self._content)
        self._log = LogPanel(p)
        self._log.pack(fill="both", expand=True)
        return p

    def _goto(self, step):
        for pan in self._panels: pan.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text="↩  New resize", command=lambda: self._goto(0))
        elif step == last - 1:
            self._next_btn.config(text="✔  Execute", command=self._execute)
        else:
            self._next_btn.config(text="Next →", command=self._next)

    def _back(self): self._goto(max(0, self._step - 1))

    def _next(self):
        s = self._step
        if s == 0:   self._folder = self._browser.get(); self._goto(1)
        elif s == 1:
            if not self._load_files(): return
            self._goto(2)
        elif s == 2: self._goto(3)
        elif s == 3:
            self._build_preview(); self._goto(4)
        elif s == 4: self._goto(5)

    def _load_files(self):
        ext_choice = self._ext_var.get()
        if ext_choice == "*.jpg + *.png": exts = ["*.jpg","*.png"]
        elif ext_choice == "custom…":
            c = self._custom_ext.get().strip().lstrip("*.")
            exts = [f"*.{c}"] if c else ["*.*"]
        else: exts = [ext_choice]
        self._files = collect_files(self._folder, exts, self._recurse_var.get())
        if not self._files:
            messagebox.showwarning("No files", f"No matching files in:\n{self._folder}")
            return False
        return True

    def _get_resize_cfg(self):
        m = self._resize_mode.get()
        try: tw = int(self._tw.get())
        except: tw = 0
        try: th = int(self._th.get())
        except: th = 0
        try: pct = float(self._pct_entry.get())
        except: pct = 100.0
        return {"resize_mode": m, "target_w": tw, "target_h": th,
                "pct": pct, "jpeg_quality": self._jpeg_q.get(),
                "canvas_color": self._canvas_color.get(),
                "credit_text": self._credit_text.get(),
                "credit_pos": self._credit_pos.get(),
                "credit_color": self._credit_color.get(),
                "credit_font": "Arial"}

    def _build_preview(self):
        cfg  = self._get_resize_cfg()
        rows = []
        for f in self._files[:20]:
            try:
                img = Image.open(f)
                w, h = img.width, img.height
                if cfg.get("resize_mode") == "6":
                    nw, nh = int(cfg.get("target_w", 0)), int(cfg.get("target_h", 0))
                else:
                    nw, nh = compute_new_size(w, h, cfg)
                rows.append((f.name, "→", f"{w}×{h}  →  {nw}×{nh}"))
            except Exception as e:
                rows.append((f.name, "!", str(e)))
        if len(self._files) > 20:
            rows.append(("…","",f"and {len(self._files)-20} more"))
        self._preview.load(rows)

    def _execute(self):
        self._goto(5)
        self._log.clear()
        cfg       = self._get_resize_cfg()
        overwrite = self._out_mode.get() == "overwrite"
        subname   = self._subfolder_name.get().strip() or "resized"
        success   = 0; failures = 0

        for f in self._files:
            try:
                if overwrite:
                    dst = f
                else:
                    d = f.parent / subname; d.mkdir(exist_ok=True)
                    dst = d / f.name
                img    = Image.open(f)
                w, h   = img.width, img.height
                if cfg.get("resize_mode") == "6":
                    nw, nh = int(cfg.get("target_w", 0)), int(cfg.get("target_h", 0))
                else:
                    nw, nh = compute_new_size(w, h, cfg)
                do_resize(f, dst, cfg)
                self._log.log(f"  {f.name}  →  {nw}×{nh}", "ok")
                success += 1
            except Exception as e:
                self._log.log(f"  FAILED: {f.name} — {e}", "err")
                failures += 1

        self._log.log(f"\n  Done — {success} file(s) resized.", "ok")
        if failures: self._log.log(f"  {failures} file(s) failed.", "err")
        self._next_btn.config(text="↩  New resize", command=lambda: self._goto(0))

# =============================================================================
#  PIPELINE: chain multiple tools
# =============================================================================
class PipelineWizard(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Pipeline")
        self.configure(bg=BG)
        scaled_geometry(self, 500, 340)
        self.resizable(False, False)
        self._parent = parent
        self._build()

    def _build(self):
        label(self, "Pipeline  —  chain operations", font=FONT_H1).pack(padx=20, pady=(16,4), anchor="w")
        label(self, "Choose which tools to run in sequence.\nEach tool will open as its own wizard — complete them one by one.",
              fg=FG2, font=FONT_SM).pack(padx=20, anchor="w")
        separator(self).pack(fill="x", padx=20, pady=12)

        self._do_resize    = tk.BooleanVar(value=False)
        self._do_compress  = tk.BooleanVar(value=False)
        self._do_rename    = tk.BooleanVar(value=False)

        for var, text in [
            (self._do_resize,   "⤡  Resize first"),
            (self._do_compress, "🗜  Compress"),
            (self._do_rename,   "✏  Rename"),
        ]:
            tk.Checkbutton(self, text=text, variable=var,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY).pack(padx=40, anchor="w", pady=3)

        separator(self).pack(fill="x", padx=20, pady=12)
        label(self, "Tools will launch in the order shown above.", fg=FG2, font=FONT_SM).pack(padx=20, anchor="w")
        btn(self, "▶  Start pipeline", self._start, primary=True).pack(pady=16)

    def _start(self):
        order = []
        if self._do_resize.get():   order.append(ResizeWizard)
        if self._do_compress.get(): order.append(CompressWizard)
        if self._do_rename.get():   order.append(RenameWizard)
        if not order:
            messagebox.showwarning("Nothing selected", "Select at least one operation.")
            return
        self.destroy()
        self._launch_chain(order, 0)

    def _launch_chain(self, order, idx):
        if idx >= len(order): return
        w = order[idx](self._parent)
        def on_close():
            w.destroy()
            self._launch_chain(order, idx+1)
        w.protocol("WM_DELETE_WINDOW", on_close)

# =============================================================================
#  COLOUR PICKER  —  HSV square + hue bar (Illustrator-style)
# =============================================================================
class ColorPickerDialog(tk.Toplevel):
    """Full colour picker: SV square, hue bar, hex field, RGB sliders.
    Usage:
        dlg = ColorPickerDialog(parent, initial="#ff0000")
        parent.wait_window(dlg)
        result = dlg.result   # "#rrggbb" or None if cancelled
    """
    SQ = 220      # size of the SV square
    HB = 20       # width of the hue bar
    PAD = 8

    def __init__(self, parent, initial="#ffffff", title=None):
        super().__init__(parent)
        self.title(title or _t("cpd.title"))
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        # Parse initial colour to HSV
        r, g, b = self._hex_to_rgb(initial)
        import colorsys
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h = h   # 0-1
        self._s = s   # 0-1
        self._v = v   # 0-1
        self._dragging_sq  = False
        self._dragging_hue = False

        self._build()
        self._redraw_hue_bar()
        self._redraw_sq()
        self._update_fields_from_hsv()

        self.update_idletasks()
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        _center_win(self)

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build(self):
        SQ, HB, PAD = self.SQ, self.HB, self.PAD

        main = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        main.pack()

        # ── Row 1: SV square + hue bar + current/new swatches ─────────────────
        top = tk.Frame(main, bg=BG)
        top.pack(fill="x")

        self._sq_canvas = tk.Canvas(top, width=SQ, height=SQ,
                                     cursor="crosshair", highlightthickness=1,
                                     highlightbackground=BG3)
        self._sq_canvas.pack(side="left")
        self._sq_canvas.bind("<ButtonPress-1>",   self._sq_press)
        self._sq_canvas.bind("<B1-Motion>",        self._sq_drag)
        self._sq_canvas.bind("<ButtonRelease-1>",  lambda e: setattr(self, "_dragging_sq", False))

        gap = tk.Frame(top, bg=BG, width=PAD)
        gap.pack(side="left")

        self._hue_canvas = tk.Canvas(top, width=HB, height=SQ,
                                      cursor="sb_v_double_arrow", highlightthickness=1,
                                      highlightbackground=BG3)
        self._hue_canvas.pack(side="left")
        self._hue_canvas.bind("<ButtonPress-1>",  self._hue_press)
        self._hue_canvas.bind("<B1-Motion>",       self._hue_drag)
        self._hue_canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging_hue", False))

        swatch_col = tk.Frame(top, bg=BG, padx=PAD)
        swatch_col.pack(side="left", fill="y")
        label(swatch_col, "Nouveau", fg=FG2, font=FONT_SM, bg=BG).pack(anchor="w")
        self._new_swatch = tk.Frame(swatch_col, width=52, height=52, bg="#ffffff",
                                    highlightbackground=BORDER, highlightthickness=1)
        self._new_swatch.pack(pady=(0, 4))
        label(swatch_col, "Initial", fg=FG2, font=FONT_SM, bg=BG).pack(anchor="w")
        r0, g0, b0 = self._hex_to_rgb("#ffffff")
        self._orig_swatch = tk.Frame(swatch_col, width=52, height=52,
                                     bg=self._rgb_to_hex(r0, g0, b0),
                                     highlightbackground=BORDER, highlightthickness=1)
        self._orig_swatch.pack()

        # ── Row 2: fields ─────────────────────────────────────────────────────
        sep = tk.Frame(main, bg=BG3, height=1)
        sep.pack(fill="x", pady=(PAD, 0))

        fields = tk.Frame(main, bg=BG, pady=PAD)
        fields.pack(fill="x")

        # Hex
        hex_row = tk.Frame(fields, bg=BG)
        hex_row.pack(fill="x", pady=2)
        label(hex_row, "Hex :", bg=BG, width=4).pack(side="left")
        self._hex_var = tk.StringVar()
        self._hex_entry = tk.Entry(hex_row, textvariable=self._hex_var,
                                    bg=BG3, fg=FG, insertbackground=FG,
                                    relief="flat", font=FONT_MONO, width=10)
        self._hex_entry.pack(side="left", padx=6)
        self._hex_entry.bind("<Return>",     self._on_hex_enter)
        self._hex_entry.bind("<FocusOut>",   self._on_hex_enter)
        self._live_swatch = tk.Frame(hex_row, width=28, height=22, bg="#ffffff",
                                     highlightbackground=BORDER, highlightthickness=1)
        self._live_swatch.pack(side="left", padx=4)

        # RGB sliders
        self._r_var = tk.IntVar(value=255)
        self._g_var = tk.IntVar(value=255)
        self._b_var = tk.IntVar(value=255)
        for lbl_txt, var, col in [("R", self._r_var, "#e05555"),
                                   ("G", self._g_var, "#55c055"),
                                   ("B", self._b_var, "#5588e0")]:
            row = tk.Frame(fields, bg=BG)
            row.pack(fill="x", pady=1)
            label(row, lbl_txt, bg=BG, fg=col, font=("Segoe UI", 9, "bold"),
                  width=2).pack(side="left")
            sl = tk.Scale(row, from_=0, to=255, variable=var, orient="horizontal",
                          bg=BG2, fg=FG, troughcolor=BG3,
                          activebackground=ACCENT, highlightthickness=0, length=180,
                          showvalue=True, resolution=1,
                          command=lambda v, _v=var: self._on_rgb_slide())
            sl.pack(side="left", padx=4)
            _arrow_scale(sl)
            tk.Entry(row, textvariable=var, bg=BG3, fg=FG, insertbackground=FG,
                     relief="flat", font=FONT_BODY, width=4).pack(side="left")

        # ── Buttons ───────────────────────────────────────────────────────────
        sep2 = tk.Frame(main, bg=BG3, height=1)
        sep2.pack(fill="x", pady=(PAD, 0))
        btn_row = tk.Frame(main, bg=BG, pady=8)
        btn_row.pack(fill="x")
        btn(btn_row, _t("cpd.cancel"), self._cancel, primary=False).pack(side="right", padx=(6, 0))
        btn(btn_row, "✔  OK", self._ok).pack(side="right")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _redraw_sq(self):
        import colorsys
        SQ = self.SQ
        # Build the SV gradient image: top-right=pure hue, top-left=white, bottom=black
        from PIL import Image as _Img, ImageTk as _ITk
        img = _Img.new("RGB", (SQ, SQ))
        r_h, g_h, b_h = [int(c * 255) for c in colorsys.hsv_to_rgb(self._h, 1.0, 1.0)]
        px = img.load()
        for x in range(SQ):
            s = x / (SQ - 1)
            for y in range(SQ):
                v = 1.0 - y / (SQ - 1)
                r = int((1 - s) * 255 + s * r_h)
                g = int((1 - s) * 255 + s * g_h)
                b = int((1 - s) * 255 + s * b_h)
                r = int(r * v)
                g = int(g * v)
                b = int(b * v)
                px[x, y] = (r, g, b)
        self._sq_photo = _ITk.PhotoImage(img)
        self._sq_canvas.delete("all")
        self._sq_canvas.create_image(0, 0, anchor="nw", image=self._sq_photo)
        # Draw crosshair
        cx = int(self._s * (SQ - 1))
        cy = int((1 - self._v) * (SQ - 1))
        r_c = 6
        self._sq_canvas.create_oval(cx - r_c, cy - r_c, cx + r_c, cy + r_c,
                                     outline="white", width=2)
        self._sq_canvas.create_oval(cx - r_c - 1, cy - r_c - 1,
                                     cx + r_c + 1, cy + r_c + 1,
                                     outline="black", width=1)

    def _redraw_hue_bar(self):
        import colorsys
        from PIL import Image as _Img, ImageTk as _ITk
        img = _Img.new("RGB", (self.HB, self.SQ))
        px = img.load()
        for y in range(self.SQ):
            h = y / (self.SQ - 1)
            r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(h, 1.0, 1.0)]
            for x in range(self.HB):
                px[x, y] = (r, g, b)
        self._hue_photo = _ITk.PhotoImage(img)
        self._hue_canvas.delete("all")
        self._hue_canvas.create_image(0, 0, anchor="nw", image=self._hue_photo)
        self._draw_hue_marker()

    def _draw_hue_marker(self):
        self._hue_canvas.delete("hue_mark")
        y = int(self._h * (self.SQ - 1))
        self._hue_canvas.create_line(0, y, self.HB, y, fill="white",
                                      width=2, tags="hue_mark")
        self._hue_canvas.create_line(0, y + 1, self.HB, y + 1, fill="black",
                                      width=1, tags="hue_mark")

    # ── Interaction ───────────────────────────────────────────────────────────
    def _sq_press(self, event):
        self._dragging_sq = True
        self._sq_pick(event.x, event.y)

    def _sq_drag(self, event):
        if self._dragging_sq:
            self._sq_pick(event.x, event.y)

    def _sq_pick(self, x, y):
        SQ = self.SQ
        self._s = max(0.0, min(1.0, x / (SQ - 1)))
        self._v = max(0.0, min(1.0, 1.0 - y / (SQ - 1)))
        self._redraw_sq()
        self._update_fields_from_hsv()

    def _hue_press(self, event):
        self._dragging_hue = True
        self._hue_pick(event.y)

    def _hue_drag(self, event):
        if self._dragging_hue:
            self._hue_pick(event.y)

    def _hue_pick(self, y):
        self._h = max(0.0, min(1.0, y / (self.SQ - 1)))
        self._redraw_hue_bar()
        self._redraw_sq()
        self._update_fields_from_hsv()

    def _on_rgb_slide(self):
        import colorsys
        r, g, b = self._r_var.get(), self._g_var.get(), self._b_var.get()
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h, self._s, self._v = h, s, v
        self._redraw_hue_bar()
        self._redraw_sq()
        hex_val = self._rgb_to_hex(r, g, b)
        self._hex_var.set(hex_val)
        try:
            self._new_swatch.config(bg=hex_val)
            self._live_swatch.config(bg=hex_val)
        except Exception:
            pass

    def _on_hex_enter(self, _event=None):
        raw = self._hex_var.get().strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        try:
            r, g, b = self._hex_to_rgb(raw)
        except Exception:
            return
        import colorsys
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h, self._s, self._v = h, s, v
        self._r_var.set(r)
        self._g_var.set(g)
        self._b_var.set(b)
        self._redraw_hue_bar()
        self._redraw_sq()
        hex_val = self._rgb_to_hex(r, g, b)
        self._hex_var.set(hex_val)
        try:
            self._new_swatch.config(bg=hex_val)
            self._live_swatch.config(bg=hex_val)
        except Exception:
            pass

    def _update_fields_from_hsv(self):
        import colorsys
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(self._h, self._s, self._v)]
        hex_val = self._rgb_to_hex(r, g, b)
        self._hex_var.set(hex_val)
        self._r_var.set(r)
        self._g_var.set(g)
        self._b_var.set(b)
        try:
            self._new_swatch.config(bg=hex_val)
            self._live_swatch.config(bg=hex_val)
        except Exception:
            pass

    def _ok(self):
        import colorsys
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(self._h, self._s, self._v)]
        self.result = self._rgb_to_hex(r, g, b)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    # ── Colour utils ──────────────────────────────────────────────────────────
    @staticmethod
    def _hex_to_rgb(h: str):
        h = h.lstrip("#")
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        if len(h) != 6:
            return (255, 255, 255)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(r, g, b):
        return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
#  IMAGE GENERATOR  —  numbered image batch creator
# =============================================================================
_FONTS_DIR      = Path("C:/Windows/Fonts")
_FONTS_DIR_USER = Path(os.path.expanduser("~")) / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
_FONT_CACHE_FILE = Path(os.path.abspath(__file__)).parent / "_font_cache.json"

def _count_font_files() -> int:
    """Fast count of all TTF/OTF files in all scanned font dirs."""
    total = 0
    for d in [_FONTS_DIR, _FONTS_DIR_USER]:
        if d.exists():
            for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
                total += sum(1 for _ in d.glob(ext))
    return total

def _clear_font_cache():
    """Delete the on-disk cache and reset in-memory globals so the next call
    to _get_font_families() does a full rediscovery."""
    global _FONT_FAMILIES, _INSTALLED_FONTS
    _FONT_FAMILIES  = {}
    _INSTALLED_FONTS = {}
    try:
        if _FONT_CACHE_FILE.exists():
            _FONT_CACHE_FILE.unlink()
    except Exception:
        pass


def _read_font_name(path: str):
    """Parse the 'name' table of a TTF/OTF file and return (family, style).
    Falls back to ('', 'Regular') on any error."""
    import struct as _st
    try:
        with open(path, 'rb') as fh:
            sig = fh.read(4)
            font_start = 0
            if sig == b'ttcf':        # TrueType Collection
                fh.seek(8)
                n = _st.unpack('>I', fh.read(4))[0]
                if n < 1:
                    return '', 'Regular'
                font_start = _st.unpack('>I', fh.read(4))[0]
            fh.seek(font_start + 4)   # skip sfVersion
            num_tables = _st.unpack('>H', fh.read(2))[0]
            fh.seek(font_start + 12)  # jump to table records
            name_off = None
            for _ in range(num_tables):
                rec = fh.read(16)
                if len(rec) < 16:
                    break
                if rec[:4] == b'name':
                    name_off = _st.unpack('>I', rec[8:12])[0]
                    break
            if name_off is None:
                return '', 'Regular'
            fh.seek(name_off)
            _, count, str_off = _st.unpack('>HHH', fh.read(6))
            recs  = fh.read(count * 12)
            fh.seek(name_off + str_off)
            store = fh.read(32768)   # name strings (max 32 KB)
            best: dict = {}
            for i in range(count):
                b    = i * 12
                plat = _st.unpack('>H', recs[b:b+2])[0]
                nid  = _st.unpack('>H', recs[b+6:b+8])[0]
                nlen = _st.unpack('>H', recs[b+8:b+10])[0]
                noff = _st.unpack('>H', recs[b+10:b+12])[0]
                if nid not in (1, 2, 16, 17):
                    continue
                pri = 2 if plat == 3 else 1   # prefer Windows Unicode platform
                if nid in best and best[nid][0] >= pri:
                    continue
                raw = store[noff:noff + nlen]
                try:
                    s = raw.decode('utf-16-be' if plat == 3 else 'latin-1').strip()
                    if s:
                        best[nid] = (pri, s)
                except Exception:
                    pass
            # Prefer the typographic family/subfamily (16/17) over the legacy
            # 1/2 records: Adobe "Std" OTFs group weights into 4-style legacy
            # families (e.g. Heavy reported as "Futura Std Medium"/"Bold") for
            # old RIP compatibility, which hides real weights under the wrong name.
            family = best.get(16, best.get(1, (0, '')))[1]
            style  = best.get(17, best.get(2, (0, 'Regular')))[1] or 'Regular'
            return family.strip(), style.strip()
    except Exception:
        return '', 'Regular'


_FONT_FAMILIES:  dict = {}   # { family: { style: path } }
_INSTALLED_FONTS: dict = {}  # { "Family Style": path }  (flat, for compat)


def _discover_font_families() -> dict:
    """Read every installed TTF/OTF and group by (family, style)."""
    _style_kws = ('Bold Italic', 'Bold', 'Italic', 'Light Italic', 'Light',
                  'Medium Italic', 'Medium', 'Black Italic', 'Black', 'Thin',
                  'Heavy', 'Condensed', 'Expanded', 'Oblique', 'Regular',
                  'Semibold', 'SemiBold', 'ExtraBold', 'ExtraLight', 'Narrow')
    families: dict = {}
    font_dirs = [d for d in [_FONTS_DIR, _FONTS_DIR_USER] if d.exists()]
    for font_dir in font_dirs:
        for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
            for p in sorted(font_dir.glob(ext)):
                fam, sty = _read_font_name(str(p))
                if not fam:                   # fallback: parse filename
                    raw = re.sub(r'([a-z])([A-Z])', r'\1 \2',
                                 re.sub(r'[-_]', ' ', p.stem)).strip()
                    sty, fam = 'Regular', raw
                    for kw in _style_kws:
                        if raw.lower().endswith(' ' + kw.lower()):
                            sty = kw
                            fam = raw[:-(len(kw) + 1)].strip()
                            break
                fam = (fam or p.stem).strip()
                sty = (sty or 'Regular').strip()
                families.setdefault(fam, {})
                if sty not in families[fam]:   # first file wins
                    families[fam][sty] = str(p)
    return dict(sorted(families.items()))


def _get_font_families() -> dict:
    global _FONT_FAMILIES, _INSTALLED_FONTS
    if _FONT_FAMILIES:
        return _FONT_FAMILIES
    # ── Try cache first ───────────────────────────────────────────────────────
    if _FONT_CACHE_FILE.exists():
        try:
            cache_mtime = _FONT_CACHE_FILE.stat().st_mtime
            dirs_ok = all(
                not d.exists() or d.stat().st_mtime <= cache_mtime
                for d in [_FONTS_DIR, _FONTS_DIR_USER]
            )
            with open(_FONT_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Also check file count — catches installs that don't bump dir mtime
            count_ok = (data.get("file_count", -1) == _count_font_files())
            if dirs_ok and count_ok:
                _FONT_FAMILIES  = data["families"]
                _INSTALLED_FONTS = data["flat"]
                return _FONT_FAMILIES
        except Exception:
            pass
    # ── Rebuild from disk ─────────────────────────────────────────────────────
    _FONT_FAMILIES = _discover_font_families() or {"Default": {"Regular": ""}}
    flat = {}
    for fam, styles in _FONT_FAMILIES.items():
        for sty, path in sorted(styles.items()):
            key = fam if sty == 'Regular' else f"{fam} {sty}"
            if key not in flat:
                flat[key] = path
    _INSTALLED_FONTS = dict(sorted(flat.items()))
    # ── Save cache (includes file count for fast change detection) ──────────
    # Preserve any existing jp_support scan results — a font-list rebuild
    # (e.g. new font installed) shouldn't throw away prior JP analysis.
    jp_support = {}
    if _FONT_CACHE_FILE.exists():
        try:
            with open(_FONT_CACHE_FILE, "r", encoding="utf-8") as f:
                jp_support = json.load(f).get("jp_support", {})
        except Exception:
            jp_support = {}
    try:
        with open(_FONT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"families": _FONT_FAMILIES, "flat": _INSTALLED_FONTS,
                       "file_count": _count_font_files(),
                       "jp_support": jp_support},
                      f, ensure_ascii=False)
    except Exception:
        pass
    return _FONT_FAMILIES


def _get_installed_fonts() -> dict:
    _get_font_families()          # ensure both caches populated
    return _INSTALLED_FONTS


def _load_pil_font(font_name: str, size: int):
    from PIL import ImageFont
    fonts = _get_installed_fonts()
    path = fonts.get(font_name, "")
    if path and os.path.isfile(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # fall-back: treat font_name itself as a path or filename
    if os.path.isfile(font_name):
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            pass
    candidate = _FONTS_DIR / font_name
    if candidate.exists():
        try:
            return ImageFont.truetype(str(candidate), size)
        except Exception:
            pass
    candidate_user = _FONTS_DIR_USER / font_name
    if candidate_user.exists():
        try:
            return ImageFont.truetype(str(candidate_user), size)
        except Exception:
            pass
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) != 6:
        return (255, 255, 255)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def _apply_rounded_mask(img, radius: int):
    from PIL import Image as _Img, ImageDraw as _IDrw
    img = img.convert("RGBA")
    mask = _Img.new("L", img.size, 0)
    draw = _IDrw.Draw(mask)
    try:
        draw.rounded_rectangle([0, 0, img.width, img.height], radius=radius, fill=255)
    except TypeError:
        r, w, h = radius, img.width, img.height
        draw.rectangle([r, 0, w - r, h], fill=255)
        draw.rectangle([0, r, w, h - r], fill=255)
        draw.ellipse([0, 0, 2*r, 2*r], fill=255)
        draw.ellipse([w - 2*r, 0, w, 2*r], fill=255)
        draw.ellipse([0, h - 2*r, 2*r, h], fill=255)
        draw.ellipse([w - 2*r, h - 2*r, w, h], fill=255)
    img.putalpha(mask)
    return img


def _parse_num_list(s: str) -> list:
    """Parse '1-30, 55, 50, 60, 100, 200' → ordered list of unique ints."""
    result = []
    seen: set = set()
    for part in s.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and not part.lstrip().startswith("-"):
            halves = part.split("-", 1)
            try:
                a, b = int(halves[0].strip()), int(halves[1].strip())
                for n in (range(a, b + 1) if a <= b else range(a, b - 1, -1)):
                    if n not in seen:
                        seen.add(n)
                        result.append(n)
            except ValueError:
                pass
        else:
            try:
                n = int(part)
                if n not in seen:
                    seen.add(n)
                    result.append(n)
            except ValueError:
                pass
    return result


def _gen_image(number, width, height, bg_mode, bg_color, bg_image_path,
               text_color, font_name, font_size, corner_mode, corner_radius,
               shadow_on=False, shadow_color="#000000", shadow_offset_x=4, shadow_offset_y=4,
               shadow_blur=3,
               pos_manual=False, pos_x=None, pos_y=None,
               auto_fit=False, letter_spacing=0, custom_text=""):
    from PIL import Image as _Img, ImageDraw as _IDrw, ImageFilter as _IFilt
    if bg_mode == "color":
        rgb = _hex_to_rgb(bg_color)
        img = _Img.new("RGBA", (width, height), rgb + (255,))
    else:
        bg = _Img.open(bg_image_path).convert("RGBA")
        img = bg.resize((width, height), _Img.LANCZOS)
    draw = _IDrw.Draw(img)
    text = custom_text if custom_text else str(number)
    # Auto-fit: shrink font_size so text stays within 88% of canvas
    if auto_fit:
        max_tw, max_th = int(width * 0.88), int(height * 0.88)
        while font_size > 8:
            _f = _load_pil_font(font_name, font_size)
            try:
                _bb = draw.textbbox((0, 0), text, font=_f)
                _tw, _th = _bb[2] - _bb[0], _bb[3] - _bb[1]
            except AttributeError:
                _tw, _th = draw.textsize(text, font=_f)
            # Account for tracking in width check (always applied)
            _tw += letter_spacing * max(0, len(text) - 1)
            if _tw <= max_tw and _th <= max_th:
                break
            font_size -= 2
    font = _load_pil_font(font_name, font_size)
    # Centre point
    if pos_manual and pos_x is not None and pos_y is not None:
        cx, cy = int(pos_x), int(pos_y)
    else:
        cx, cy = width // 2, height // 2
    # Drop-shadow
    if shadow_on:
        sh_rgba  = _hex_to_rgb(shadow_color) + (200,)
        sh_layer = _Img.new("RGBA", img.size, (0, 0, 0, 0))
        sh_draw  = _IDrw.Draw(sh_layer)
        _draw_tracked(sh_draw, text, font,
                      cx + int(shadow_offset_x), cy + int(shadow_offset_y),
                      sh_rgba, letter_spacing)
        if shadow_blur > 0:
            # Blur only the alpha channel and keep RGB flat/uniform.
            # GaussianBlur on a straight (non-premultiplied) RGBA image blurs
            # R/G/B/A independently, so the soft edge blends the shadow colour
            # with the transparent backdrop's black RGB(0,0,0) — producing a
            # visible dark fringe/outline distinct from the shadow colour.
            # Since the shadow is a single flat colour, there's nothing to
            # premultiply/un-premultiply: just blur alpha and re-flood the RGB.
            alpha = sh_layer.split()[3].filter(
                _IFilt.GaussianBlur(radius=max(1, int(shadow_blur))))
            sh_layer = _Img.new("RGBA", img.size, sh_rgba[:3] + (0,))
            sh_layer.putalpha(alpha)
        img = _Img.alpha_composite(img, sh_layer)
        draw = _IDrw.Draw(img)
    text_rgba = _hex_to_rgb(text_color) + (255,)
    _draw_tracked(draw, text, font, cx, cy, text_rgba, letter_spacing)
    if corner_mode == "rounded" and corner_radius > 0:
        img = _apply_rounded_mask(img, corner_radius)
    return img  # always RGBA


# =============================================================================
#  FONT PREVIEW DIALOG
# =============================================================================
class FontPreviewDialog(tk.Toplevel):
    """Scrollable font picker that renders digit samples for every installed font."""
    SAMPLE = "0123456789"
    # Max concurrent font-render threads (keeps UI responsive)
    _MAX_WORKERS = 5

    def __init__(self, parent, current_font="Impact"):
        super().__init__(parent)
        self.title(_t("fpd.title"))
        self.configure(bg=BG)
        self.resizable(True, True)
        self.grab_set()
        self.result = None
        self._all_fonts   = list(_get_installed_fonts().keys())
        self._filtered    = list(self._all_fonts)
        self._photos: dict = {}
        self._render_gen  = 0   # incremented on each rebuild to discard stale renders
        self._executor    = _futures.ThreadPoolExecutor(max_workers=self._MAX_WORKERS)
        self._jp_only     = tk.BooleanVar(value=False)
        self._jp_scan_gen = 0   # incremented to cancel a stale background JP scan
        self._jp_scanning = False
        self._build()
        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw2, ph2 = parent.winfo_width(), parent.winfo_height()
        scaled_geometry(self, 560, 640)
        _center_win(self)
        self._rebuild_rows()

    def _build(self):
        top = styled_frame(self)
        top.pack(fill="x", padx=12, pady=(10, 6))
        label(top, _t("fpd.search")).pack(side="left")
        self._search_var = tk.StringVar()
        e = tk.Entry(top, textvariable=self._search_var, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat", font=FONT_BODY, width=26)
        e.pack(side="left", padx=6)
        self._count_lbl = label(top, "", fg=FG2, font=FONT_SM)
        self._count_lbl.pack(side="left", padx=8)
        self._filter_after = None
        self._search_var.trace_add("write", lambda *_: self._schedule_filter())
        e.focus_set()

        tk.Checkbutton(top, text=_t("fpd.jp_only"), variable=self._jp_only,
                       bg=BG, fg=FG, selectcolor=BG3, activebackground=BG,
                       font=FONT_SM, command=self._toggle_jp_only
                       ).pack(side="right", padx=(8, 0))

        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self._cv = tk.Canvas(container, bg=BG2, highlightthickness=0)
        sb = tk.Scrollbar(container, orient="vertical", command=self._cv.yview, bg=BG2)
        self._cv.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._cv.pack(side="left", fill="both", expand=True)
        self._cv.bind("<MouseWheel>",
                      lambda e: self._cv.yview_scroll(-1 * (e.delta // 120), "units"))
        self._inner = tk.Frame(self._cv, bg=BG2)
        self._wid = self._cv.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: self._cv.config(scrollregion=self._cv.bbox("all")))
        self._cv.bind("<Configure>",
                      lambda e: self._cv.itemconfig(self._wid, width=e.width))
        _enable_mousewheel_scroll(self._cv)

        bot = styled_frame(self)
        bot.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(bot, text="↺", command=self._refresh_fonts,
                  bg=BG3, fg=FG, relief="flat", font=FONT_SM,
                  cursor="hand2", padx=6).pack(side="left", padx=(0, 4))
        tk.Label(bot, text="Rafraîchir les fonts", bg=BG, fg=FG2,
                 font=FONT_SM).pack(side="left")
        btn(bot, _t("fpd.cancel"), self.destroy, primary=False).pack(side="right", padx=4)

    def destroy(self):
        self._render_gen = -1   # sentinel: discard all pending render callbacks
        self._jp_scan_gen += 1  # cancel any in-flight JP scan
        if self._filter_after is not None:
            try:
                self.after_cancel(self._filter_after)
            except Exception:
                pass
        self._executor.shutdown(wait=False)
        super().destroy()

    def _schedule_filter(self):
        """Debounce: wait 250 ms after last keypress before filtering."""
        if self._filter_after is not None:
            try:
                self.after_cancel(self._filter_after)
            except Exception:
                pass
        self._filter_after = self.after(250, self._filter)

    def _filter(self):
        self._filter_after = None
        q = self._search_var.get().lower()
        base = [f for f in self._all_fonts if q in f.lower()] if q else list(self._all_fonts)
        if self._jp_only.get():
            installed = _get_installed_fonts()
            cache = _load_jp_support_cache(_FONT_CACHE_FILE)
            base = [f for f in base if cache.get(installed.get(f, ""))]
        self._filtered = base
        self._rebuild_rows()

    def _toggle_jp_only(self):
        if self._jp_only.get():
            self._start_jp_scan()
        else:
            self._filter()

    def _start_jp_scan(self):
        """Ensure every currently-listed font has a cached JP-support result,
        computing missing ones in a background thread so the UI stays
        responsive, then re-applies the filter."""
        self._jp_scan_gen += 1
        gen = self._jp_scan_gen
        installed = _get_installed_fonts()
        cache = _load_jp_support_cache(_FONT_CACHE_FILE)
        missing = [p for p in installed.values() if p and p not in cache]
        if not missing:
            self._filter()
            return
        self._jp_scanning = True
        total = len(missing)

        def _worker():
            for i, path in enumerate(missing, 1):
                if self._jp_scan_gen != gen:
                    return  # cancelled: dialog closed or toggled again
                _get_jp_support(_FONT_CACHE_FILE, path, persist=False)
                if i % 25 == 0 or i == total:
                    self.after(0, lambda i=i: self._on_jp_scan_progress(i, total, gen))
            _save_jp_support_cache(_FONT_CACHE_FILE)
            self.after(0, lambda: self._on_jp_scan_done(gen))

        try:
            self._executor.submit(_worker)
        except RuntimeError:
            pass  # executor already shut down

    def _on_jp_scan_progress(self, done: int, total: int, gen: int):
        if self._jp_scan_gen != gen:
            return
        self._count_lbl.config(text=_t("fpd.jp_scanning", n=done, total=total))

    def _on_jp_scan_done(self, gen: int):
        if self._jp_scan_gen != gen:
            return
        self._jp_scanning = False
        self._filter()

    def _refresh_fonts(self):
        """Force-rebuild the font cache (picks up any newly installed fonts)."""
        _clear_font_cache()
        _get_font_families()
        self._all_fonts  = list(_get_installed_fonts().keys())
        self._photos     = {}          # discard cached preview images
        self._filter()                 # re-apply current search

    def _rebuild_rows(self):
        self._render_gen += 1   # invalidate all in-flight renders
        gen = self._render_gen
        for w in self._inner.winfo_children():
            w.destroy()
        self._count_lbl.config(text=_t("fpd.count", n=len(self._filtered)))
        for fname in self._filtered:
            row = tk.Frame(self._inner, bg=BG2, cursor="hand2")
            row.pack(fill="x", pady=1)
            name_lbl = tk.Label(row, text=fname, bg=BG2, fg=FG, font=FONT_SM,
                                anchor="w", width=28)
            name_lbl.pack(side="left", padx=(8, 4), pady=3)
            img_lbl = tk.Label(row, bg=BG2, bd=0)
            img_lbl.pack(side="left", padx=4)
            for widget in (row, name_lbl, img_lbl):
                widget.bind("<Button-1>", lambda e, f=fname: self._select(f))
                widget.bind("<Enter>", lambda e, r=row: r.config(bg=BG3))
                widget.bind("<Leave>", lambda e, r=row: r.config(bg=BG2))
            if fname in self._photos:
                img_lbl.config(image=self._photos[fname])
                img_lbl._photo_ref = self._photos[fname]
            else:
                self._render_async(fname, img_lbl, gen)
        self._cv.yview_moveto(0)
        # Re-bind wheel to newly added rows
        if hasattr(self._cv, '_mw_bind_tree'):
            self._cv._mw_bind_tree(self._inner)

    def _render_async(self, fname, lbl, gen: int):
        def _worker():
            if self._render_gen != gen:
                return   # stale — a newer rebuild already started
            try:
                from PIL import Image as _Img, ImageDraw as _IDrw, ImageTk as _ITk
                font = _load_pil_font(fname, 20)
                dummy = _Img.new("RGBA", (1, 1))
                dd    = _IDrw.Draw(dummy)
                try:
                    bb = dd.textbbox((0, 0), self.SAMPLE, font=font)
                    tw, th = bb[2] - bb[0], bb[3] - bb[1]
                    ox, oy = -bb[0], -bb[1]
                except AttributeError:
                    tw, th = dd.textsize(self.SAMPLE, font=font)
                    ox, oy = 0, 0
                img = _Img.new("RGBA", (max(tw + 16, 1), max(th + 8, 1)), (49, 49, 69, 255))
                _IDrw.Draw(img).text((8 + ox, 4 + oy), self.SAMPLE,
                                     fill=(220, 220, 220, 255), font=font)
                photo = _ITk.PhotoImage(img.convert("RGB"))
                if self._render_gen == gen:   # still current?
                    self.after(0, lambda p=photo, f=fname, l=lbl, g=gen:
                               self._set(f, p, l, g))
            except Exception:
                pass
        try:
            self._executor.submit(_worker)
        except RuntimeError:
            pass   # executor already shut down

    def _set(self, fname, photo, lbl, gen: int):
        if self._render_gen != gen:
            return   # stale
        self._photos[fname] = photo
        try:
            lbl.config(image=photo)
            lbl._photo_ref = photo
        except Exception:
            pass

    def _select(self, fname):
        self.result = fname
        self.destroy()


# =============================================================================
#  IMAGE GENERATOR WIZARD
# =============================================================================
class ImageGenWizard(tk.Toplevel):
    WIN_W, WIN_H = 1260, 700
    PREV_W = 300   # width of the persistent live-preview panel

    def __init__(self, parent):
        super().__init__(parent)
        self.title(_t("igw.title"))
        self.STEPS = _t("igw.steps")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._preview_after_id = None   # debounce timer for live preview

        # ── State vars ────────────────────────────────────────────────────────
        self._syncing  = False
        self._w_var    = tk.StringVar(value="500")
        self._h_var    = tk.StringVar(value="500")
        self._link_wh  = tk.BooleanVar(value=True)

        self._bg_mode  = tk.StringVar(value="color")
        self._bg_color = tk.StringVar(value="#3a86ff")
        self._bg_img   = tk.StringVar(value="")
        self._bg_pattern: list = []   # list of paths for cycling pattern mode

        self._num_list   = tk.StringVar(value="1-10")
        self._custom_text_var = tk.StringVar(value="")
        self._auto_fsize = tk.BooleanVar(value=True)
        self._font_family = tk.StringVar(value="Impact")
        self._font_style  = tk.StringVar(value="Regular")
        self._font_path   = tk.StringVar(value="")  # resolved TTF/OTF path
        self._font_var    = tk.StringVar(value="Impact")  # display name (compat)
        self._fsize       = tk.StringVar(value="120")
        self._tcol        = tk.StringVar(value="#ffffff")
        self._tracking    = tk.IntVar(value=0)   # letter spacing in pixels

        # Drop-shadow
        self._shadow_on      = tk.BooleanVar(value=False)
        self._shadow_col     = tk.StringVar(value="#000000")
        self._shadow_ox      = tk.StringVar(value="4")
        self._shadow_oy      = tk.StringVar(value="4")
        self._shadow_blur    = tk.StringVar(value="3")

        # Manual position
        self._pos_manual     = tk.BooleanVar(value=False)
        self._pos_x          = tk.StringVar(value="0")
        self._pos_y          = tk.StringVar(value="0")

        self._corner   = tk.StringVar(value="sharp")
        self._radius   = tk.StringVar(value="40")

        self._fmt      = tk.StringVar(value="PNG")
        self._quality  = tk.IntVar(value=92)
        self._prefix   = tk.StringVar(value="image_")
        self._suffix   = tk.StringVar(value="")
        self._out_folder = tk.StringVar(value=str(DESKTOP))

        # Guide mark
        self._guide_on     = tk.BooleanVar(value=False)
        self._guide_margin = tk.IntVar(value=40)

        # Thumb grid photos (kept alive after generation)
        self._thumb_photos: list = []
        # Undo — list of Path objects from the last generation
        self._last_gen_files: list = []

        self._step = 0
        self._panels: list = []
        self._build()
        self._wire_preview_vars()
        self._goto(0)

        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        scaled_geometry(self, self.WIN_W, self.WIN_H)
        _center_win(self)

    # ── Scaffold ──────────────────────────────────────────────────────────────
    def _build(self):
        # Header (full width)
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        label(hdr, _t("igw.hdr"), font=FONT_H1).pack(side="left")
        self._step_bar = StepBar(hdr, self.STEPS)
        self._step_bar.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)

        # Body: left = wizard steps, right = persistent live preview
        body = styled_frame(self)
        body.pack(fill="both", expand=True, padx=20)

        # Right panel — live preview (always visible)
        right_panel = tk.Frame(body, bg=BG2, width=self.PREV_W)
        right_panel.pack(side="right", fill="y", padx=(12, 0))
        right_panel.pack_propagate(False)
        self._build_side_preview(right_panel)

        # Left panel — step content
        self._content = styled_frame(body)
        self._content.pack(side="left", fill="both", expand=True)

        # Nav bar (full width)
        separator(self).pack(fill="x", padx=20, pady=10)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0, 16))
        self._back_btn = btn(nav, _t("igw.back"), self._back, primary=False)
        self._back_btn.pack(side="left")
        self._next_btn = btn(nav, _t("igw.next"), self._next, primary=True)
        self._next_btn.pack(side="right")

        self._panels = [
            self._build_s0(),
            self._build_s1(),
            self._build_s2(),
            self._build_s3(),
            self._build_s4(),
            self._build_s5(),
        ]

    def _build_side_preview(self, parent):
        """Persistent live-preview panel visible on every step."""
        label(parent, _t("igw.prev_title"), font=FONT_H2, bg=BG2).pack(
            anchor="w", padx=10, pady=(10, 4))
        sep = tk.Frame(parent, bg=BG3, height=1)
        sep.pack(fill="x", padx=10)
        CW = self.PREV_W - 20
        CH = CW   # square canvas
        self._side_canvas = tk.Canvas(parent, width=CW, height=CH,
                                       bg=BG3, highlightthickness=0)
        self._side_canvas.pack(padx=10, pady=8)
        self._side_canvas.create_text(CW // 2, CH // 2,
                                       text=_t("igw.prev_ph"), fill=FG2,
                                       font=FONT_SM, tags="ph")
        self._side_info = label(parent, "", fg=FG2, font=FONT_SM, bg=BG2,
                                 wraplength=self.PREV_W - 20, justify="left")
        self._side_info.pack(padx=10, fill="x")
        btn(parent, _t("igw.refresh"), self._schedule_preview,
            primary=False).pack(padx=10, pady=(6, 0), fill="x")

    def _wire_preview_vars(self):
        """Attach traces to all relevant vars so any change triggers a debounced preview."""
        watch = [
            self._w_var, self._h_var,
            self._bg_color, self._bg_img, self._bg_mode,
            self._num_list, self._custom_text_var, self._font_path, self._fsize, self._tcol,
            self._shadow_col, self._shadow_ox, self._shadow_oy, self._shadow_blur,
            self._pos_x, self._pos_y,
            self._corner, self._radius, self._guide_margin, self._tracking,
        ]
        for var in watch:
            var.trace_add("write", lambda *_: self._schedule_preview())
        for bvar in [self._shadow_on, self._pos_manual, self._link_wh,
                     self._auto_fsize, self._guide_on]:
            bvar.trace_add("write", lambda *_: self._schedule_preview())

    def _schedule_preview(self, delay_ms=350):
        """Debounced: cancel any pending refresh then start a new one."""
        if self._preview_after_id:
            self.after_cancel(self._preview_after_id)
        self._preview_after_id = self.after(delay_ms, self._run_side_preview)

    def _run_side_preview(self):
        self._preview_after_id = None
        try:
            nums = _parse_num_list(self._num_list.get())
            nf  = nums[0] if nums else 1
            w   = int(self._w_var.get())
            h   = int(self._h_var.get())
            fsz = int(self._fsize.get())
            rad = int(self._radius.get())
        except Exception:
            return
        CW = self.PREV_W - 20
        self._side_canvas.delete("all")
        self._side_canvas.create_text(CW // 2, CW // 2,
                                       text="…", fill=FG2, font=FONT_SM)

        kw = dict(
            number=nf, width=w, height=h,
            bg_mode="image" if self._bg_mode.get() == "pattern" and self._bg_pattern
                    else self._bg_mode.get(),
            bg_color=self._bg_color.get(),
            bg_image_path=(self._bg_pattern[0]
                           if self._bg_mode.get() == "pattern" and self._bg_pattern
                           else self._bg_img.get()),
            text_color=self._tcol.get(),
            font_name=self._font_path.get() or self._font_var.get(),
            font_size=fsz,
            corner_mode=self._corner.get(),
            corner_radius=rad,
            shadow_on=self._shadow_on.get(),
            shadow_color=self._shadow_col.get(),
            shadow_offset_x=int(self._shadow_ox.get() or 0),
            shadow_offset_y=int(self._shadow_oy.get() or 0),
            shadow_blur=int(self._shadow_blur.get() or 0),
            pos_manual=self._pos_manual.get(),
            pos_x=int(self._pos_x.get() or 0),
            pos_y=int(self._pos_y.get() or 0),
            auto_fit=self._auto_fsize.get(),
            letter_spacing=self._tracking.get(),
            custom_text=self._custom_text_var.get().strip(),
        )
        guide_on     = self._guide_on.get()
        guide_margin = self._guide_margin.get()

        def _worker():
            try:
                from PIL import Image as _Img, ImageTk as _ITk
                img = _gen_image(**kw)
                thumb = img.copy()
                thumb.thumbnail((CW, CW), _Img.LANCZOS)
                tw, th = thumb.size
                bg_disp = _Img.new("RGBA", thumb.size, (49, 49, 69, 255))
                if thumb.mode == "RGBA":
                    bg_disp.paste(thumb, mask=thumb.split()[3])
                else:
                    bg_disp.paste(thumb)
                photo = _ITk.PhotoImage(bg_disp.convert("RGB"))
                info = f"{img.width}×{img.height}  #{nf}"
                self.after(0, lambda p=photo, i=info, rw=img.width, rh=img.height,
                           tw_=tw, th_=th: self._apply_side_preview(
                               p, i, rw, rh, tw_, th_, guide_on, guide_margin))
            except Exception as exc:
                self.after(0, lambda e=str(exc):
                           self._side_info.config(text=f"⚠ {e}", fg=FG_RED))
        threading.Thread(target=_worker, daemon=True).start()

    def _apply_side_preview(self, photo, info,
                             real_w=0, real_h=0, thumb_w=0, thumb_h=0,
                             guide_on=False, guide_margin=40):
        CW = self.PREV_W - 20
        self._side_canvas.delete("all")
        # Centre offset of the thumbnail on the square canvas
        ox = (CW - thumb_w) // 2
        oy = (CW - thumb_h) // 2
        self._side_canvas.create_image(CW // 2, CW // 2, anchor="center", image=photo)
        self._side_canvas._photo_ref = photo
        # Guide mark overlay
        if guide_on and real_w > 0 and real_h > 0:
            sx = thumb_w / real_w
            sy = thumb_h / real_h
            gx1 = ox + int(guide_margin * sx)
            gy1 = oy + int(guide_margin * sy)
            gx2 = ox + thumb_w - int(guide_margin * sx)
            gy2 = oy + thumb_h - int(guide_margin * sy)
            # Dashed yellow guide rectangle
            self._side_canvas.create_rectangle(
                gx1, gy1, gx2, gy2,
                outline="#f2c94c", width=1, dash=(4, 3), tags="guide")
            # Corner ticks
            tk_len = 6
            for cx_, cy_ in [(gx1, gy1), (gx2, gy1), (gx1, gy2), (gx2, gy2)]:
                self._side_canvas.create_line(cx_-tk_len, cy_, cx_+tk_len, cy_,
                                              fill="#f2c94c", width=1, tags="guide")
                self._side_canvas.create_line(cx_, cy_-tk_len, cx_, cy_+tk_len,
                                              fill="#f2c94c", width=1, tags="guide")
        self._side_info.config(text=info, fg=FG_GREEN)

    def _goto(self, step):
        for p in self._panels:
            p.hide()
        self._step = step
        self._panels[step].show()
        self._step_bar.set_step(step)
        self._back_btn.config(state="normal" if step > 0 else "disabled")
        last = len(self._panels) - 1
        if step == last:
            self._next_btn.config(text=_t("igw.restart"), command=lambda: self._goto(0))
        else:
            self._next_btn.config(text=_t("igw.next"), command=self._next)
        if step == 0:
            if hasattr(self, "_gen_log"):
                self._gen_log.clear()
            if hasattr(self, "_gen_prog"):
                self._gen_prog["value"] = 0
        # Always refresh side preview when switching steps
        self._schedule_preview()

    def _back(self): self._goto(max(0, self._step - 1))
    def _next(self): self._goto(min(len(self._panels) - 1, self._step + 1))

    # ── Colour helpers ────────────────────────────────────────────────────────
    def _swatch(self, parent, color_var):
        """Small coloured square that tracks a StringVar."""
        sw = tk.Frame(parent, width=26, height=26, relief="flat")
        sw.pack_propagate(False)
        try:
            sw.config(bg=color_var.get())
        except Exception:
            sw.config(bg="#888888")

        def _upd(*_):
            c = color_var.get()
            if c.startswith("#") and len(c) in (4, 7):
                try:
                    sw.config(bg=c)
                except Exception:
                    pass
        color_var.trace_add("write", _upd)
        return sw

    def _color_btn(self, parent, color_var, label_txt="🎨"):
        """Button that opens ColorPickerDialog and writes result into color_var."""
        sw = self._swatch(parent, color_var)

        def _open():
            dlg = ColorPickerDialog(self, initial=color_var.get())
            self.wait_window(dlg)
            if dlg.result:
                color_var.set(dlg.result)

        b = btn(parent, label_txt, _open, primary=False)
        return sw, b

    # ── Step 0 · Canvas ───────────────────────────────────────────────────────
    def _build_s0(self):
        p = WizardPanel(self._content)
        label(p, _t("s0.title"), font=FONT_H2).pack(anchor="w", pady=(0, 12))

        dim = styled_frame(p)
        dim.pack(anchor="w", pady=4)
        label(dim, _t("s0.width")).pack(side="left")
        tk.Entry(dim, textvariable=self._w_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=7
                 ).pack(side="left", padx=4)
        label(dim, _t("s0.height_px")).pack(side="left")
        tk.Entry(dim, textvariable=self._h_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=7
                 ).pack(side="left", padx=4)
        label(dim, "px").pack(side="left")

        def _sync_w(*_):
            if self._link_wh.get() and not self._syncing:
                self._syncing = True
                self._h_var.set(self._w_var.get())
                self._syncing = False

        def _sync_h(*_):
            if self._link_wh.get() and not self._syncing:
                self._syncing = True
                self._w_var.set(self._h_var.get())
                self._syncing = False

        self._w_var.trace_add("write", _sync_w)
        self._h_var.trace_add("write", _sync_h)

        tk.Checkbutton(p, text=_t("s0.link_wh"),
                       variable=self._link_wh,
                       bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, font=FONT_BODY).pack(anchor="w", pady=(6, 12))

        separator(p).pack(fill="x", pady=(0, 10))
        label(p, _t("s0.presets"), fg=FG2, font=FONT_SM).pack(anchor="w")

        # ── Custom presets row
        presets_custom = [
            ("OPUS\n320×210",          320,  210),
            ("MIKRO\n96×128",           96,  128),
            ("KIKUBI\n1024×672",      1024,  672),
            ("KIKUBI List\n300×300",   300,  300),
        ]
        row1 = styled_frame(p)
        row1.pack(anchor="w", pady=(6, 2))
        label(row1, _t("s0.proj"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 8))
        for lbl_txt, w, h in presets_custom:
            display = lbl_txt.replace("\n", "  ")
            def _apply_c(ww=w, hh=h):
                self._link_wh.set(False)
                self._w_var.set(str(ww))
                self._h_var.set(str(hh))
            btn(row1, display, _apply_c, primary=True).pack(side="left", padx=3)

        # ── Banner presets row
        row_banners = styled_frame(p)
        row_banners.pack(anchor="w", pady=(2, 2))
        label(row_banners, _t("s0.banners"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 8))
        def _apply_bandeau():
            self._link_wh.set(False)
            self._w_var.set("1054")
            self._h_var.set("250")
            self._custom_text_var.set("展覧会公式サイトはこちら")
            self._auto_fsize.set(False)
            self._fsize.set("70")
        btn(row_banners, "展覧会公式サイトはこちら  1054×250",
            _apply_bandeau, primary=True).pack(side="left", padx=3)

        # ── Generic presets row
        presets_generic = [
            ("100×100", 100, 100), ("200×200", 200, 200), ("400×400", 400, 400),
            ("500×500", 500, 500), ("512×512", 512, 512), ("1024×1024", 1024, 1024),
            ("1280×720", 1280, 720), ("1920×1080", 1920, 1080),
        ]
        row2 = styled_frame(p)
        row2.pack(anchor="w", pady=2)
        label(row2, _t("s0.std"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 4))
        for lbl_txt, w, h in presets_generic:
            def _apply(ww=w, hh=h):
                self._link_wh.set(False)
                self._w_var.set(str(ww))
                self._h_var.set(str(hh))
            btn(row2, lbl_txt, _apply, primary=False).pack(side="left", padx=3)

        separator(p).pack(fill="x", pady=(14, 10))
        anal_row = styled_frame(p)
        anal_row.pack(anchor="w", fill="x")
        btn(anal_row, _t("s0.analyze"), self._analyse_ref_image,
            primary=False).pack(side="left")
        label(anal_row, _t("s0.analyze_hint"),
              fg=FG2, font=FONT_SM).pack(side="left", padx=(10, 0))
        return p

    def _analyse_ref_image(self):
        """Load a reference image and extract canvas size, text colour,
        approximate font size, position, and drop-shadow into the wizard.
        Uses PIL-native operations (no pixel loops) for speed."""
        path = filedialog.askopenfilename(
            parent=self, title=_t("anal.dlg"),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        if not path:
            return
        try:
            from PIL import Image as _Img, ImageFilter as _IFlt
            img_rgb = _Img.open(path).convert("RGB")
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return

        W, H = img_rgb.size
        self._link_wh.set(False)
        self._w_var.set(str(W))
        self._h_var.set(str(H))

        # ── Background colour: dominant colour in a 5% corner crop ──────────
        border = max(4, int(min(W, H) * 0.05))
        corner = img_rgb.crop((0, 0, border, border))
        corner_colors = corner.getcolors(maxcolors=border * border * 4)
        if corner_colors:
            bg_r, bg_g, bg_b = max(corner_colors, key=lambda x: x[0])[1]
        else:
            bg_r, bg_g, bg_b = 255, 255, 255
        bg_gray = int(0.299 * bg_r + 0.587 * bg_g + 0.114 * bg_b)
        dark_bg = bg_gray <= 128          # True → light text on dark bg

        # ── Binary mask: find non-background pixels via point() — no loops ──
        gray = img_rgb.convert("L")
        THRESH = 40
        if dark_bg:
            mask = gray.point(lambda p: 255 if p > bg_gray + THRESH else 0)
        else:
            mask = gray.point(lambda p: 255 if p < bg_gray - THRESH else 0)

        # Denoise (removes single-pixel noise)
        mask = mask.filter(_IFlt.MaxFilter(3)).filter(_IFlt.MinFilter(3))

        bbox = mask.getbbox()             # (left, top, right, bottom) | None
        if not bbox:
            messagebox.showinfo(_t("anal.title"), _t("anal.none"), parent=self)
            return

        left, top, right, bottom = bbox
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        font_est = max(8, round((bottom - top) * 0.80))

        # ── Dominant text colour from the bounding-box crop ─────────────────
        crop = img_rgb.crop((left, top, right, bottom))
        crop_colors = crop.getcolors(maxcolors=W * H)
        text_color = "#ffffff" if dark_bg else "#000000"
        if crop_colors:
            non_bg = [
                (cnt, (r, g, b)) for cnt, (r, g, b) in crop_colors
                if abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b) > 50
            ]
            if non_bg:
                r, g, b = max(non_bg, key=lambda x: x[0])[1]
                text_color = f"#{r:02x}{g:02x}{b:02x}"

        # ── Shadow detection: look for a dark cluster offset from main bbox ─
        shadow_on, sh_ox, sh_oy, sh_col = False, 4, 4, "#000000"
        if dark_bg:
            # On dark backgrounds the "shadow" would be darker than the bg —
            # skip (shadows rarely visible on dark canvas)
            pass
        else:
            # Light bg: shadow pixels are darker than bg, separate from text
            shadow_mask = gray.point(lambda p: 255 if p < bg_gray - THRESH - 20 else 0)
            shadow_mask = shadow_mask.filter(_IFlt.MaxFilter(3)).filter(_IFlt.MinFilter(3))
            sbbox = shadow_mask.getbbox()
            if sbbox:
                sc_x = (sbbox[0] + sbbox[2]) // 2
                sc_y = (sbbox[1] + sbbox[3]) // 2
                off_x, off_y = sc_x - cx, sc_y - cy
                if abs(off_x) >= 2 or abs(off_y) >= 2:
                    shadow_on = True
                    sh_ox, sh_oy = off_x, off_y
                    scrop = img_rgb.crop((sbbox[0], sbbox[1], sbbox[2], sbbox[3]))
                    sc_cols = scrop.getcolors(maxcolors=W * H)
                    if sc_cols:
                        sr, sg, sb_ = max(sc_cols, key=lambda x: x[0])[1]
                        sh_col = f"#{sr:02x}{sg:02x}{sb_:02x}"

        # ── Apply to wizard ──────────────────────────────────────────────────
        self._pos_manual.set(True)
        self._pos_x.set(str(cx))
        self._pos_y.set(str(cy))
        self._fsize.set(str(font_est))
        self._auto_fsize.set(False)
        self._tcol.set(text_color)
        if shadow_on:
            self._shadow_on.set(True)
            self._shadow_ox.set(str(sh_ox))
            self._shadow_oy.set(str(sh_oy))
            self._shadow_col.set(sh_col)

        self._schedule_preview()

        lines = [
            f"Canvas     :  {W} × {H} px",
            f"Position   :  ({cx}, {cy})",
            f"Font size  :  ~{font_est} px",
            f"Text colour:  {text_color}",
        ]
        if shadow_on:
            lines.append(f"Shadow     :  offset ({sh_ox}, {sh_oy})  colour {sh_col}")
        messagebox.showinfo(_t("anal.title"), "\n".join(lines), parent=self)

    # ── Step 1 · Background ───────────────────────────────────────────────────
    # ── Preset helpers ────────────────────────────────────────────────────────
    def _preset_state(self) -> dict:
        """Collect all wizard settings into a serialisable dict."""
        return {
            "w": self._w_var.get(), "h": self._h_var.get(),
            "bg_mode": self._bg_mode.get(), "bg_color": self._bg_color.get(),
            "bg_img": self._bg_img.get(),
            "bg_pattern": list(self._bg_pattern),
            "num_list": self._num_list.get(),
            "font_family": self._font_family.get(), "font_style": self._font_style.get(),
            "fsize": self._fsize.get(), "tcol": self._tcol.get(),
            "auto_fsize": self._auto_fsize.get(),
            "shadow_on": self._shadow_on.get(), "shadow_col": self._shadow_col.get(),
            "shadow_ox": self._shadow_ox.get(), "shadow_oy": self._shadow_oy.get(),
            "shadow_blur": self._shadow_blur.get(),
            "pos_manual": self._pos_manual.get(),
            "pos_x": self._pos_x.get(), "pos_y": self._pos_y.get(),
            "corner": self._corner.get(), "radius": self._radius.get(),
            "fmt": self._fmt.get(), "quality": self._quality.get(),
            "prefix": self._prefix.get(), "suffix": self._suffix.get(),
            "out_folder": self._out_folder.get(),
            "guide_on": self._guide_on.get(), "guide_margin": self._guide_margin.get(),
            "tracking": self._tracking.get(),
            "custom_text": self._custom_text_var.get(),
        }

    def _apply_preset(self, state: dict):
        """Restore wizard settings from a dict."""
        def sv(var, key):
            if key in state: var.set(state[key])
        sv(self._w_var, "w");                sv(self._h_var, "h")
        sv(self._bg_mode, "bg_mode");        sv(self._bg_color, "bg_color")
        sv(self._bg_img, "bg_img")
        if "bg_pattern" in state:
            self._bg_pattern = list(state["bg_pattern"])
            self._rebuild_pat_rows()
        sv(self._num_list, "num_list")
        sv(self._font_family, "font_family"); sv(self._font_style, "font_style")
        sv(self._fsize, "fsize");             sv(self._tcol, "tcol")
        sv(self._shadow_col, "shadow_col")
        sv(self._shadow_ox, "shadow_ox");     sv(self._shadow_oy, "shadow_oy")
        sv(self._shadow_blur, "shadow_blur")
        sv(self._pos_x, "pos_x");            sv(self._pos_y, "pos_y")
        sv(self._corner, "corner");           sv(self._radius, "radius")
        sv(self._fmt, "fmt");                 sv(self._quality, "quality")
        sv(self._prefix, "prefix");           sv(self._suffix, "suffix")
        sv(self._out_folder, "out_folder")
        for bvar, key in [(self._auto_fsize, "auto_fsize"), (self._shadow_on, "shadow_on"),
                          (self._pos_manual, "pos_manual"), (self._guide_on, "guide_on"),
                          (self._link_wh, "link_wh")]:
            if key in state: bvar.set(bool(state[key]))
        if "guide_margin" in state:
            self._guide_margin.set(int(state["guide_margin"]))
        if "tracking" in state:
            self._tracking.set(int(state["tracking"]))
        sv(self._custom_text_var, "custom_text")
        self._update_font_path()
        self._update_preview()

    def _build_s1(self):
        p = WizardPanel(self._content)
        label(p, _t("s1.title"), font=FONT_H2).pack(anchor="w", pady=(0, 10))

        # Option 1 — solid colour
        opt1 = styled_frame(p)
        opt1.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt1, text=_t("s1.color"), variable=self._bg_mode, value="color",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_bg).pack(anchor="w")
        self._color_panel = styled_frame(opt1)
        self._color_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 6))
        cf = styled_frame(self._color_panel)
        cf.pack(anchor="w")
        label(cf, _t("s1.hex")).pack(side="left")
        tk.Entry(cf, textvariable=self._bg_color, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=10
                 ).pack(side="left", padx=6)
        sw, cb = self._color_btn(cf, self._bg_color)
        sw.pack(side="left", padx=(0, 2))
        cb.pack(side="left")

        pal_row = styled_frame(self._color_panel)
        pal_row.pack(anchor="w", pady=(6, 0))
        label(pal_row, _t("s1.quick"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 6))
        palette = [("#1e1e2e","Noir bleuté"), ("#ffffff","Blanc"), ("#000000","Noir"),
                   ("#3a86ff","Bleu"), ("#ff6b6b","Rouge"), ("#06d6a0","Vert"),
                   ("#ffd166","Jaune"), ("#8338ec","Violet"), ("#f72585","Rose")]
        for hc, _ in palette:
            f = tk.Frame(pal_row, bg=hc, width=22, height=22, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, c=hc: self._bg_color.set(c))

        # Option 2 — image file
        opt2 = styled_frame(p)
        opt2.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt2, text=_t("s1.image"),
                       variable=self._bg_mode, value="image",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_bg).pack(anchor="w")
        self._img_panel = styled_frame(opt2)
        self._img_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        ipf = styled_frame(self._img_panel)
        ipf.pack(anchor="w")
        btn(ipf, _t("s1.browse"), self._pick_bg_image, primary=False).pack(side="left")
        btn(ipf, _t("s1.paste_path"), self._paste_bg_path, primary=False).pack(side="left", padx=(6, 0))
        self._bg_img_lbl = label(ipf, _t("s1.no_file"), fg=FG2, font=FONT_SM)
        self._bg_img_lbl.pack(side="left")
        label(self._img_panel,
              _t("s1.hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(4, 0))

        # Drag-and-drop an image file directly onto this panel
        def _on_img_drop(paths):
            raw = paths[0]
            if Path(raw).is_file():
                self._bg_img.set(raw)
                self._bg_img_lbl.config(text=f"  {Path(raw).name}", fg=FG)
                self._bg_mode.set("image")
                self._toggle_bg()
        _enable_drop(self._img_panel, _on_img_drop)

        # Option 3 — pattern (multiple images that cycle)
        opt3 = styled_frame(p)
        opt3.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt3, text=_t("s1.pattern"),
                       variable=self._bg_mode, value="pattern",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_bg).pack(anchor="w")
        self._pat_panel = styled_frame(opt3)
        self._pat_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        self._pat_rows_frame = styled_frame(self._pat_panel)
        self._pat_rows_frame.pack(anchor="w", fill="x")
        self._pat_empty_lbl = label(self._pat_rows_frame, _t("s1.pat_empty"),
                                     fg=FG2, font=FONT_SM)
        self._pat_empty_lbl.pack(anchor="w", pady=2)
        pat_btns = styled_frame(self._pat_panel)
        pat_btns.pack(anchor="w", pady=(6, 0))
        btn(pat_btns, _t("s1.pat_add"), self._pick_pattern_images,
            primary=False).pack(side="left")
        self._pat_count_lbl = label(pat_btns, "", fg=FG2, font=FONT_SM)
        self._pat_count_lbl.pack(side="left", padx=(10, 0))
        label(self._pat_panel, _t("s1.pat_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(6, 0))

        def _on_pat_drop(paths):
            for raw in paths:
                if Path(raw).is_file():
                    self._add_pattern_image(raw)
            self._bg_mode.set("pattern")
            self._toggle_bg()
        _enable_drop(self._pat_panel, _on_pat_drop)

        self._toggle_bg()
        return p

    def _toggle_bg(self):
        mode = self._bg_mode.get()
        if mode == "color":
            self._color_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 6))
            self._img_panel.pack_forget()
            self._pat_panel.pack_forget()
        elif mode == "image":
            self._color_panel.pack_forget()
            self._img_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
            self._pat_panel.pack_forget()
        else:  # pattern
            self._color_panel.pack_forget()
            self._img_panel.pack_forget()
            self._pat_panel.pack(anchor="w", fill="x", padx=24, pady=(4, 0))

    def _pick_bg_image(self):
        path = filedialog.askopenfilename(
            parent=self, title=_t("s1.bg_dlg"),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        if path:
            self._bg_img.set(path)
            self._bg_img_lbl.config(text=f"  {Path(path).name}", fg=FG)

    def _paste_bg_path(self):
        try:
            raw = self.clipboard_get().strip().strip('"').strip("'")
            if raw and Path(raw).is_file():
                self._bg_img.set(raw)
                self._bg_img_lbl.config(text=f"  {Path(raw).name}", fg=FG)
                self._bg_mode.set("image")
                self._toggle_bg()
        except Exception:
            pass

    # ── Pattern helpers ───────────────────────────────────────────────────────
    def _pick_pattern_images(self):
        """Open a multi-file picker and add selected images to the pattern."""
        paths = filedialog.askopenfilenames(
            parent=self, title=_t("s1.pat_add"),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        for p in paths:
            self._add_pattern_image(p)

    def _add_pattern_image(self, path: str):
        if path and path not in self._bg_pattern:
            self._bg_pattern.append(path)
        self._rebuild_pat_rows()
        self._schedule_preview()

    def _remove_pattern_image(self, idx: int):
        if 0 <= idx < len(self._bg_pattern):
            self._bg_pattern.pop(idx)
        self._rebuild_pat_rows()
        self._schedule_preview()

    def _move_pattern_image(self, idx: int, direction: int):
        lst = self._bg_pattern
        new_idx = idx + direction
        if 0 <= new_idx < len(lst):
            lst[idx], lst[new_idx] = lst[new_idx], lst[idx]
        self._rebuild_pat_rows()

    def _rebuild_pat_rows(self):
        """Rebuild the pattern image list display."""
        for w in self._pat_rows_frame.winfo_children():
            w.destroy()
        if not self._bg_pattern:
            lbl = label(self._pat_rows_frame, _t("s1.pat_empty"), fg=FG2, font=FONT_SM)
            lbl.pack(anchor="w", pady=2)
        else:
            for i, path in enumerate(self._bg_pattern):
                row = styled_frame(self._pat_rows_frame)
                row.pack(anchor="w", fill="x", pady=1)
                label(row, f"{i+1}.", fg=FG2, font=FONT_SM, width=3).pack(side="left")
                name = Path(path).name
                disp = name if len(name) <= 32 else name[:29] + "…"
                label(row, f"🖼  {disp}", font=FONT_SM).pack(side="left", padx=(2, 6))
                btn(row, "↑", lambda ii=i: self._move_pattern_image(ii, -1),
                    primary=False).pack(side="left", padx=1)
                btn(row, "↓", lambda ii=i: self._move_pattern_image(ii, +1),
                    primary=False).pack(side="left", padx=1)
                btn(row, "✕", lambda ii=i: self._remove_pattern_image(ii),
                    primary=False).pack(side="left", padx=(1, 0))
        n = len(self._bg_pattern)
        self._pat_count_lbl.config(
            text=f"  {n} image{'s' if n != 1 else ''}" if n else "")

    # ── Step 2 · Numbers ──────────────────────────────────────────────────────
    def _build_s2(self):
        p = WizardPanel(self._content)

        # Scrollable inner canvas so step 2 never clips
        _canvas = tk.Canvas(p, bg=BG, highlightthickness=0)
        _sb = tk.Scrollbar(p, orient="vertical", command=_canvas.yview, bg=BG2)
        _canvas.config(yscrollcommand=_sb.set)
        inner = styled_frame(_canvas)
        inner.bind("<Configure>",
                   lambda e: _canvas.config(scrollregion=_canvas.bbox("all")))
        _canvas.create_window((0, 0), window=inner, anchor="nw")
        _canvas.pack(side="left", fill="both", expand=True)
        _sb.pack(side="right", fill="y")
        # Enable mouse-wheel on canvas + all children (bound after content is built)
        p.bind("<Map>", lambda e, c=_canvas: _enable_mousewheel_scroll(c), add="+")

        label(inner, _t("s2.style_title"), font=FONT_H2).pack(anchor="w", pady=(0, 8))

        # ── Number list ────────────────────────────────────────────────────────
        label(inner, _t("s2.nums_title"), font=FONT_H2).pack(anchor="w", pady=(0, 4))
        label(inner,
              _t("s2.nums_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 4))
        rng = styled_frame(inner)
        rng.pack(anchor="w", fill="x", pady=2)
        tk.Entry(rng, textvariable=self._num_list, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=44
                 ).pack(side="left", padx=(0, 8))
        self._range_lbl = label(rng, "", fg=FG_GREEN, font=FONT_SM)
        self._range_lbl.pack(side="left")

        def _upd_count(*_):
            nums = _parse_num_list(self._num_list.get())
            n = len(nums)
            suffix = _t("s2.count_range", a=nums[0], b=nums[-1]) if n > 1 else ""
            self._range_lbl.config(
                text=_t("s2.count", n=n) + suffix,
                fg=FG_GREEN if n > 0 else FG_RED)
        self._num_list.trace_add("write", _upd_count)
        _upd_count()

        # ── Custom text (optional — overrides number) ─────────────────────────
        ct_row = styled_frame(inner)
        ct_row.pack(anchor="w", fill="x", pady=(10, 0))
        label(ct_row, _t("s2.custom_text")).pack(side="left")
        tk.Entry(ct_row, textvariable=self._custom_text_var,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                 font=FONT_BODY, width=36).pack(side="left", padx=6)
        btn(ct_row, "✕", lambda: self._custom_text_var.set(""),
            primary=False).pack(side="left")
        label(inner, _t("s2.custom_text_hint"), fg=FG2, font=FONT_SM).pack(
            anchor="w", pady=(2, 0))

        separator(inner).pack(fill="x", pady=8)

        # ── Font ───────────────────────────────────────────────────────────────
        label(inner, _t("s2.font_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))

        fam_dict    = _get_font_families()
        all_families = list(fam_dict.keys())
        init_fam = "Impact" if "Impact" in fam_dict else (all_families[0] if all_families else "")
        self._font_family.set(init_fam)

        # — Family row —
        row_fam = styled_frame(inner)
        row_fam.pack(anchor="w", pady=2)
        label(row_fam, _t("s2.family")).pack(side="left")
        self._font_fam_cb = ttk.Combobox(row_fam, textvariable=self._font_family,
                                          values=all_families, state="normal", width=26,
                                          style="Custom.TCombobox", font=FONT_BODY)
        self._font_fam_cb.pack(side="left", padx=6)
        btn(row_fam, _t("s2.preview_btn"), self._open_font_preview,
            primary=False).pack(side="left", padx=4)

        def _filter_families(*_):
            q = self._font_family.get().lower()
            filtered = [n for n in all_families if q in n.lower()]
            self._font_fam_cb["values"] = filtered if filtered else all_families
            try:
                self._font_fam_cb.event_generate("<Down>")
            except Exception:
                pass
        self._font_fam_cb.bind("<KeyRelease>", lambda e: _filter_families())
        self._font_family.trace_add("write", lambda *_: self._refresh_style_cb())

        # — Style + Size row —
        row_sty = styled_frame(inner)
        row_sty.pack(anchor="w", pady=2)
        label(row_sty, _t("s2.style")).pack(side="left")
        self._font_sty_cb = ttk.Combobox(row_sty, textvariable=self._font_style,
                                          values=[], state="readonly", width=18,
                                          style="Custom.TCombobox", font=FONT_BODY)
        self._font_sty_cb.pack(side="left", padx=6)
        self._font_style.trace_add("write", lambda *_: self._update_font_path())
        label(row_sty, _t("s2.size")).pack(side="left")
        tk.Entry(row_sty, textvariable=self._fsize, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=6
                 ).pack(side="left", padx=4)
        label(row_sty, "px", fg=FG2, font=FONT_SM).pack(side="left")
        label(row_sty, _t("s2.tracking")).pack(side="left", padx=(16, 0))
        tk.Spinbox(row_sty, textvariable=self._tracking,
                   from_=-500, to=500, increment=1, width=5,
                   bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                   buttonbackground=BG3, font=FONT_BODY).pack(side="left", padx=4)
        label(row_sty, "px", fg=FG2, font=FONT_SM).pack(side="left")
        label(inner, _t("s2.tracking_hint"), fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 2))

        # Initialise style list for default family
        self._refresh_style_cb()

        # — Taille mode toggle —
        mode_frm = tk.Frame(inner, bg=BG3, padx=6, pady=6)
        mode_frm.pack(anchor="w", pady=(10, 2), fill="x")
        label(mode_frm, _t("s2.mode_lbl"), bg=BG3).pack(side="left", padx=(0, 10))
        tk.Radiobutton(mode_frm, text=_t("s2.fixed"),
                       variable=self._auto_fsize, value=False,
                       bg=BG3, fg=FG, selectcolor=ACCENT, activebackground=BG3,
                       font=FONT_BODY).pack(side="left")
        tk.Radiobutton(mode_frm, text=_t("s2.auto"),
                       variable=self._auto_fsize, value=True,
                       bg=BG3, fg=FG, selectcolor=ACCENT, activebackground=BG3,
                       font=FONT_BODY).pack(side="left", padx=(12, 0))
        label(inner,
              _t("s2.auto_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(2, 4))
        label(inner, _t("s2.tip"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 4))

        separator(inner).pack(fill="x", pady=8)

        # ── Text colour ────────────────────────────────────────────────────────
        label(inner, _t("s2.color_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        row_c = styled_frame(inner)
        row_c.pack(anchor="w", pady=2)
        label(row_c, _t("s2.hex_lbl")).pack(side="left")
        tk.Entry(row_c, textvariable=self._tcol, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=10
                 ).pack(side="left", padx=6)
        sw_tc, btn_tc = self._color_btn(row_c, self._tcol)
        sw_tc.pack(side="left", padx=(0, 2))
        btn_tc.pack(side="left")
        tc_row = styled_frame(inner)
        tc_row.pack(anchor="w", pady=(4, 0))
        label(tc_row, _t("s2.quick_lbl"), fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 6))
        for hc in ["#ffffff", "#000000", "#ffff00", "#ff4444", "#44ff99", "#66aaff", "#ff9900"]:
            f = tk.Frame(tc_row, bg=hc, width=22, height=22, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, c=hc: self._tcol.set(c))

        separator(inner).pack(fill="x", pady=8)

        # ── Drop shadow ────────────────────────────────────────────────────────
        label(inner, _t("s2.shadow_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        sh_hdr = styled_frame(inner)
        sh_hdr.pack(anchor="w")
        tk.Checkbutton(sh_hdr, text=_t("s2.shadow_on"),
                       variable=self._shadow_on,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_shadow).pack(side="left")
        self._shadow_panel = styled_frame(inner)
        self._shadow_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))

        sh_r1 = styled_frame(self._shadow_panel)
        sh_r1.pack(anchor="w", pady=2)
        label(sh_r1, _t("s2.shadow_col")).pack(side="left")
        tk.Entry(sh_r1, textvariable=self._shadow_col, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=10
                 ).pack(side="left", padx=6)
        sw_sh, btn_sh = self._color_btn(sh_r1, self._shadow_col)
        sw_sh.pack(side="left", padx=(0, 2))
        btn_sh.pack(side="left")
        for hc in ["#000000", "#222222", "#444444", "#001133", "#330000"]:
            f = tk.Frame(sh_r1, bg=hc, width=18, height=18, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, c=hc: self._shadow_col.set(c))

        sh_r2 = styled_frame(self._shadow_panel)
        sh_r2.pack(anchor="w", pady=2)
        label(sh_r2, _t("s2.shadow_x")).pack(side="left")
        self._sh_ox_slider = tk.Scale(sh_r2, from_=-40, to=40, orient="horizontal",
                                       bg=BG2, fg=FG, highlightthickness=0,
                                       troughcolor=BG3, activebackground=ACCENT, length=140,
                                       command=lambda v: self._shadow_ox.set(str(int(float(v)))))
        self._sh_ox_slider.set(4)
        self._sh_ox_slider.pack(side="left", padx=6)
        _arrow_scale(self._sh_ox_slider)
        tk.Entry(sh_r2, textvariable=self._shadow_ox, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=5
                 ).pack(side="left")
        label(sh_r2, _t("s2.shadow_y")).pack(side="left", padx=(8, 0))
        self._sh_oy_slider = tk.Scale(sh_r2, from_=-40, to=40, orient="horizontal",
                                       bg=BG2, fg=FG, highlightthickness=0,
                                       troughcolor=BG3, activebackground=ACCENT, length=140,
                                       command=lambda v: self._shadow_oy.set(str(int(float(v)))))
        self._sh_oy_slider.set(4)
        self._sh_oy_slider.pack(side="left", padx=6)
        _arrow_scale(self._sh_oy_slider)
        tk.Entry(sh_r2, textvariable=self._shadow_oy, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=5
                 ).pack(side="left")
        label(sh_r2, "px", fg=FG2, font=FONT_SM).pack(side="left", padx=4)

        sh_r3 = styled_frame(self._shadow_panel)
        sh_r3.pack(anchor="w", pady=2)
        label(sh_r3, _t("s2.shadow_blur")).pack(side="left")
        self._sh_blur_slider = tk.Scale(sh_r3, from_=0, to=20, orient="horizontal",
                                         bg=BG2, fg=FG, highlightthickness=0,
                                         troughcolor=BG3, activebackground=ACCENT, length=160,
                                         command=lambda v: self._shadow_blur.set(str(int(float(v)))))
        self._sh_blur_slider.set(3)
        self._sh_blur_slider.pack(side="left", padx=6)
        _arrow_scale(self._sh_blur_slider)
        tk.Entry(sh_r3, textvariable=self._shadow_blur, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=5
                 ).pack(side="left")
        label(sh_r3, "px", fg=FG2, font=FONT_SM).pack(side="left", padx=4)

        # Sync sliders from entries
        def _sync_sh_ox(*_):
            try: self._sh_ox_slider.set(int(self._shadow_ox.get()))
            except Exception: pass
        def _sync_sh_oy(*_):
            try: self._sh_oy_slider.set(int(self._shadow_oy.get()))
            except Exception: pass
        def _sync_sh_bl(*_):
            try: self._sh_blur_slider.set(int(self._shadow_blur.get()))
            except Exception: pass
        self._shadow_ox.trace_add("write", _sync_sh_ox)
        self._shadow_oy.trace_add("write", _sync_sh_oy)
        self._shadow_blur.trace_add("write", _sync_sh_bl)

        self._toggle_shadow()

        separator(inner).pack(fill="x", pady=8)

        # ── Manual position ────────────────────────────────────────────────────
        label(inner, _t("s2.pos_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        pos_hdr = styled_frame(inner)
        pos_hdr.pack(anchor="w")
        tk.Checkbutton(pos_hdr, text=_t("s2.pos_manual"),
                       variable=self._pos_manual,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_pos).pack(side="left")
        self._pos_panel = styled_frame(inner)
        self._pos_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        label(self._pos_panel,
              _t("s2.pos_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(0, 4))

        pos_r = styled_frame(self._pos_panel)
        pos_r.pack(anchor="w")
        label(pos_r, "X :").pack(side="left")
        # Guard flags prevent the slider command writing back while we're
        # syncing the slider from the entry (would cause clamping to slider max).
        _px_busy = [False]
        _py_busy = [False]

        def _on_px_slider(v):
            if not _px_busy[0]:
                self._pos_x.set(str(int(float(v))))

        def _on_py_slider(v):
            if not _py_busy[0]:
                self._pos_y.set(str(int(float(v))))

        self._px_slider = tk.Scale(pos_r, from_=0, to=2000, orient="horizontal",
                                    bg=BG2, fg=FG, highlightthickness=0,
                                    troughcolor=BG3, activebackground=ACCENT, length=180,
                                    command=_on_px_slider)
        self._px_slider.set(0)
        self._px_slider.pack(side="left", padx=6)
        _arrow_scale(self._px_slider)

        # Entry: digits only; slider follows on Enter/FocusOut (no mid-typing clamping)
        def _pos_vcmd(new_val):
            return new_val == "" or (new_val.lstrip("-").isdigit())

        _px_vcmd = (self.register(lambda P: _pos_vcmd(P)), "%P")
        _py_vcmd = (self.register(lambda P: _pos_vcmd(P)), "%P")

        def _commit_px(e=None):
            raw = self._pos_x.get()
            v = int(raw) if raw.lstrip("-").isdigit() and raw not in ("", "-") else 0
            self._pos_x.set(str(v))
            _px_busy[0] = True
            try: self._px_slider.set(v)
            finally: _px_busy[0] = False

        def _commit_py(e=None):
            raw = self._pos_y.get()
            v = int(raw) if raw.lstrip("-").isdigit() and raw not in ("", "-") else 0
            self._pos_y.set(str(v))
            _py_busy[0] = True
            try: self._py_slider.set(v)
            finally: _py_busy[0] = False

        px_entry = tk.Entry(pos_r, textvariable=self._pos_x,
                            validate="key", validatecommand=_px_vcmd,
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            font=FONT_BODY, width=6)
        px_entry.pack(side="left")
        px_entry.bind("<Return>",   _commit_px)
        px_entry.bind("<FocusOut>", _commit_px)

        label(pos_r, _t("s2.pos_xy")).pack(side="left", padx=(8, 0))

        self._py_slider = tk.Scale(pos_r, from_=0, to=2000, orient="horizontal",
                                    bg=BG2, fg=FG, highlightthickness=0,
                                    troughcolor=BG3, activebackground=ACCENT, length=180,
                                    command=_on_py_slider)
        self._py_slider.set(0)
        self._py_slider.pack(side="left", padx=6)
        _arrow_scale(self._py_slider)

        py_entry = tk.Entry(pos_r, textvariable=self._pos_y,
                            validate="key", validatecommand=_py_vcmd,
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            font=FONT_BODY, width=6)
        py_entry.pack(side="left")
        py_entry.bind("<Return>",   _commit_py)
        py_entry.bind("<FocusOut>", _commit_py)

        label(pos_r, "px", fg=FG2, font=FONT_SM).pack(side="left", padx=4)

        # Adapt slider max to canvas size live, clamping to guide zone if active
        def _adapt_sliders(*_):
            try:
                mx = max(10, int(self._w_var.get()))
                my = max(10, int(self._h_var.get()))
                if self._guide_on.get():
                    gm = self._guide_margin.get()
                    self._px_slider.config(from_=gm, to=max(gm+1, mx - gm))
                    self._py_slider.config(from_=gm, to=max(gm+1, my - gm))
                else:
                    self._px_slider.config(from_=0, to=mx)
                    self._py_slider.config(from_=0, to=my)
            except Exception:
                pass
        self._w_var.trace_add("write", _adapt_sliders)
        self._h_var.trace_add("write", _adapt_sliders)
        self._guide_on.trace_add("write", _adapt_sliders)
        self._guide_margin.trace_add("write", _adapt_sliders)

        self._toggle_pos()

        separator(inner).pack(fill="x", pady=8)

        # ── Guide mark ────────────────────────────────────────────────────────
        label(inner, _t("s2.guide_title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        guide_hdr = styled_frame(inner)
        guide_hdr.pack(anchor="w", fill="x")
        tk.Checkbutton(guide_hdr, text=_t("s2.guide_on"),
                       variable=self._guide_on,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_guide).pack(side="left")
        self._guide_panel = styled_frame(inner)
        self._guide_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        gm_row = styled_frame(self._guide_panel)
        gm_row.pack(anchor="w", pady=2)
        label(gm_row, _t("s2.guide_margin")).pack(side="left")
        self._guide_slider = tk.Scale(gm_row, from_=1, to=200,
                                      variable=self._guide_margin,
                                      orient="horizontal", bg=BG2, fg=FG,
                                      highlightthickness=0, troughcolor=BG3,
                                      activebackground=ACCENT, length=180)
        self._guide_slider.pack(side="left", padx=6)
        _arrow_scale(self._guide_slider)

        # Entry: digits-only, clamps to [1..200] on Return / focus-out
        _gm_str = tk.StringVar(value=str(self._guide_margin.get()))

        def _gm_str_to_int(*_):
            """StringVar → IntVar (fires when user types)."""
            raw = _gm_str.get()
            if raw.isdigit() and raw != "":
                v = max(1, min(200, int(raw)))
                if self._guide_margin.get() != v:
                    self._guide_margin.set(v)

        def _gm_int_to_str(*_):
            """IntVar → StringVar (fires when slider moves or OPUS btn clicked)."""
            new_val = str(self._guide_margin.get())
            if _gm_str.get() != new_val:
                _gm_str.set(new_val)

        def _gm_clamp_entry(e=None):
            """On Enter / focus-out: clamp and normalise the entry display."""
            raw = _gm_str.get()
            v   = max(1, min(200, int(raw))) if raw.isdigit() and raw != "" else self._guide_margin.get()
            self._guide_margin.set(v)
            _gm_str.set(str(v))

        def _gm_validate(action, new_val):
            """Allow only digit strings (or empty during editing)."""
            return new_val == "" or new_val.isdigit()

        vcmd = (self.register(_gm_validate), "%d", "%P")
        _gm_str.trace_add("write", _gm_str_to_int)
        self._guide_margin.trace_add("write", _gm_int_to_str)

        gm_entry = tk.Entry(gm_row, textvariable=_gm_str,
                            validate="key", validatecommand=vcmd,
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            font=FONT_BODY, width=5)
        gm_entry.pack(side="left", padx=4)
        gm_entry.bind("<Return>",    _gm_clamp_entry)
        gm_entry.bind("<FocusOut>",  _gm_clamp_entry)

        label(gm_row, "px", fg=FG2, font=FONT_SM).pack(side="left")
        btn(gm_row, _t("s2.guide_opus"),
            lambda: self._guide_margin.set(40),
            primary=False).pack(side="left", padx=(10, 0))
        label(self._guide_panel, _t("s2.guide_hint"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(4, 0))
        self._toggle_guide()
        return p

    def _toggle_shadow(self):
        if self._shadow_on.get():
            self._shadow_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        else:
            self._shadow_panel.pack_forget()

    def _toggle_pos(self):
        if self._pos_manual.get():
            self._pos_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        else:
            self._pos_panel.pack_forget()

    def _toggle_guide(self):
        if self._guide_on.get():
            self._guide_panel.pack(anchor="w", fill="x", padx=20, pady=(4, 0))
        else:
            self._guide_panel.pack_forget()

    # ── Step 3 · Corners ──────────────────────────────────────────────────────
    def _build_s3(self):
        p = WizardPanel(self._content)
        label(p, _t("s3.title"), font=FONT_H2).pack(anchor="w", pady=(0, 12))

        # Both options in their own container so toggle doesn't reorder rows
        opt1 = styled_frame(p)
        opt1.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt1, text=_t("s3.sharp"),
                       variable=self._corner, value="sharp",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_corners).pack(anchor="w")

        opt2 = styled_frame(p)
        opt2.pack(anchor="w", fill="x", pady=2)
        tk.Radiobutton(opt2, text=_t("s3.rounded"),
                       variable=self._corner, value="rounded",
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY, command=self._toggle_corners).pack(anchor="w")
        self._radius_frm = styled_frame(opt2)
        self._radius_frm.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        label(self._radius_frm, _t("s3.radius")).pack(side="left")
        self._radius_slider = tk.Scale(self._radius_frm, from_=2, to=250,
                                       orient="horizontal", bg=BG2, fg=FG,
                                       highlightthickness=0, troughcolor=BG3,
                                       activebackground=ACCENT, length=260,
                                       command=lambda v: self._radius.set(str(int(float(v)))))
        self._radius_slider.set(40)
        self._radius_slider.pack(side="left", padx=8)
        _arrow_scale(self._radius_slider)
        tk.Entry(self._radius_frm, textvariable=self._radius,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                 font=FONT_BODY, width=5).pack(side="left", padx=4)
        label(self._radius_frm, "px", fg=FG2, font=FONT_SM).pack(side="left")

        def _sync_slider(*_):
            try:
                self._radius_slider.set(int(self._radius.get()))
            except Exception:
                pass
        self._radius.trace_add("write", _sync_slider)

        separator(p).pack(fill="x", pady=12)
        label(p, _t("s3.warn_jpeg"),
              fg=FG_YELLOW, font=FONT_SM).pack(anchor="w")
        label(p, _t("s3.hint_png"),
              fg=FG2, font=FONT_SM).pack(anchor="w", pady=(2, 0))

        self._toggle_corners()
        return p

    def _toggle_corners(self):
        if self._corner.get() == "rounded":
            self._radius_frm.pack(anchor="w", fill="x", padx=24, pady=(4, 0))
        else:
            self._radius_frm.pack_forget()

    # ── Step 4 · Output ───────────────────────────────────────────────────────
    def _build_s4(self):
        p = WizardPanel(self._content)
        label(p, _t("s4.title"), font=FONT_H2).pack(anchor="w", pady=(0, 10))

        fmt_row = styled_frame(p)
        fmt_row.pack(anchor="w")
        for fmt in ("PNG", "JPEG"):
            tk.Radiobutton(fmt_row, text=fmt, variable=self._fmt, value=fmt,
                           bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                           font=FONT_BODY, command=self._toggle_fmt
                           ).pack(side="left", padx=(0, 16))

        self._qual_frm = styled_frame(p)
        self._qual_frm.pack(anchor="w", fill="x", padx=24, pady=(6, 0))
        label(self._qual_frm, _t("s4.quality")).pack(side="left")
        _sc_qual_cvw = tk.Scale(self._qual_frm, from_=60, to=100, variable=self._quality,
                 orient="horizontal", bg=BG2, fg=FG, highlightthickness=0,
                 troughcolor=BG3, activebackground=ACCENT, length=200)
        _sc_qual_cvw.pack(side="left", padx=8)
        _arrow_scale(_sc_qual_cvw)
        label(self._qual_frm, "%", fg=FG2, font=FONT_SM).pack(side="left")

        separator(p).pack(fill="x", pady=12)
        label(p, _t("s4.naming"), font=FONT_H2).pack(anchor="w", pady=(0, 8))
        row_pfx = styled_frame(p)
        row_pfx.pack(anchor="w", pady=4)
        label(row_pfx, _t("s4.prefix")).pack(side="left")
        tk.Entry(row_pfx, textvariable=self._prefix, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=16
                 ).pack(side="left", padx=6)
        label(row_pfx, _t("s4.suffix")).pack(side="left", padx=(12, 0))
        tk.Entry(row_pfx, textvariable=self._suffix, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_BODY, width=14
                 ).pack(side="left", padx=6)
        self._naming_ex = label(row_pfx, "", fg=FG2, font=FONT_SM)
        self._naming_ex.pack(side="left", padx=6)

        def _upd_ex(*_):
            ext = ".png" if self._fmt.get() == "PNG" else ".jpg"
            nums = _parse_num_list(self._num_list.get())
            n = nums[0] if nums else 1
            sfx = self._suffix.get()
            self._naming_ex.config(text=f"ex: {self._prefix.get()}{n}{sfx}{ext}")
        self._prefix.trace_add("write", _upd_ex)
        self._suffix.trace_add("write", _upd_ex)
        self._fmt.trace_add("write", _upd_ex)
        self._num_list.trace_add("write", _upd_ex)
        _upd_ex()

        separator(p).pack(fill="x", pady=12)
        label(p, _t("s4.folder"), font=FONT_H2).pack(anchor="w", pady=(0, 8))
        frow = styled_frame(p)
        frow.pack(anchor="w", fill="x")
        btn(frow, _t("s4.choose"), self._pick_out_folder, primary=False).pack(side="left")
        self._out_lbl = label(frow, f"  {self._out_folder.get()}", fg=FG2, font=FONT_SM,
                              wraplength=540, justify="left")
        self._out_lbl.pack(side="left", padx=6, fill="x")

        self._toggle_fmt()

        separator(p).pack(fill="x", pady=12)

        # ── Presets ───────────────────────────────────────────────────────────
        label(p, _t("preset.title"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        preset_row = styled_frame(p)
        preset_row.pack(anchor="w", fill="x")

        # Combobox listing saved presets
        self._preset_var = tk.StringVar()
        self._preset_cb  = ttk.Combobox(preset_row, textvariable=self._preset_var,
                                         state="readonly", width=22,
                                         style="Custom.TCombobox", font=FONT_BODY)
        self._preset_cb.pack(side="left", padx=(0, 8))
        self._refresh_preset_list()

        btn(preset_row, _t("preset.load"),
            self._load_preset, primary=False).pack(side="left", padx=(0, 6))
        btn(preset_row, _t("preset.save"),
            self._save_preset, primary=True).pack(side="left", padx=(0, 6))
        btn(preset_row, _t("preset.delete"),
            self._delete_preset, primary=False).pack(side="left")

        return p

    def _toggle_fmt(self):
        if self._fmt.get() == "JPEG":
            self._qual_frm.pack(anchor="w", fill="x", padx=24, pady=(6, 0))
        else:
            self._qual_frm.pack_forget()

    def _pick_out_folder(self):
        path = filedialog.askdirectory(parent=self, title=_t("s4.dlg_title"),
                                       initialdir=self._out_folder.get())
        if path:
            self._out_folder.set(path)
            self._out_lbl.config(text=f"  {path}", fg=FG)

    def _refresh_preset_list(self):
        presets = _load_presets()
        names = list(presets.keys()) or [_t("preset.none")]
        self._preset_cb["values"] = names
        if names:
            self._preset_cb.set(names[0])

    def _save_preset(self):
        # Ask for a name
        name = self._ask_simple_text(_t("preset.name_dlg"), default=self._preset_var.get())
        if not name or name == _t("preset.none"):
            return
        presets = _load_presets()
        presets[name] = self._preset_state()
        _save_presets(presets)
        self._refresh_preset_list()
        self._preset_var.set(name)
        self._gen_log.log(_t("preset.saved", name=name), "ok") if hasattr(self, "_gen_log") else None

    def _load_preset(self):
        name = self._preset_var.get()
        presets = _load_presets()
        if name not in presets:
            return
        self._apply_preset(presets[name])
        self._gen_log.log(_t("preset.loaded", name=name), "ok") if hasattr(self, "_gen_log") else None

    def _delete_preset(self):
        name = self._preset_var.get()
        presets = _load_presets()
        if name not in presets:
            return
        if not messagebox.askyesno(_t("preset.delete"),
                                   _t("preset.confirm_del", name=name), parent=self):
            return
        del presets[name]
        _save_presets(presets)
        self._refresh_preset_list()

    def _ask_simple_text(self, prompt: str, default: str = "") -> str:
        """Small inline dialog that returns a string (or '' if cancelled)."""
        out = [""]
        dlg = tk.Toplevel(self)
        dlg.title(prompt)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        label(dlg, prompt, font=FONT_BODY).pack(padx=20, pady=(16, 4), anchor="w")
        var = tk.StringVar(value=default)
        e   = tk.Entry(dlg, textvariable=var, bg=BG3, fg=FG, insertbackground=FG,
                       relief="flat", font=FONT_BODY, width=28)
        e.pack(padx=20, pady=(0, 8))
        e.select_range(0, "end")
        e.focus_set()
        row = styled_frame(dlg)
        row.pack(fill="x", padx=20, pady=(0, 12))
        def _ok(e=None):
            out[0] = var.get().strip()
            dlg.destroy()
        def _cancel(e=None):
            dlg.destroy()
        btn(row, "OK", _ok, primary=True).pack(side="right", padx=(6, 0))
        btn(row, "Cancel", _cancel, primary=False).pack(side="right")
        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)
        dlg.update_idletasks()
        pw, py = self.winfo_rootx(), self.winfo_rooty()
        dlg.geometry(f"360x130+{pw+80}+{py+80}")
        self.wait_window(dlg)
        return out[0]

    # ── Step 5 · Preview & Generate ───────────────────────────────────────────
    def _build_s5(self):
        p = WizardPanel(self._content)
        outer = styled_frame(p)
        outer.pack(fill="both", expand=True)

        # The right side preview panel is always visible — no separate left preview needed here
        right = styled_frame(outer)
        right.pack(fill="both", expand=True)

        label(right, _t("s5.summary"), font=FONT_H2).pack(anchor="w", pady=(0, 6))
        self._summary_lbl = label(right, "", fg=FG2, font=FONT_SM, justify="left")
        self._summary_lbl.pack(anchor="w")

        separator(right).pack(fill="x", pady=8)

        btn_row = styled_frame(right)
        btn_row.pack(anchor="w", pady=(0, 6))
        btn(btn_row, _t("s5.generate"), self._execute, primary=True).pack(side="left")
        btn(btn_row, _t("s5.refresh"),
            self._schedule_preview, primary=False).pack(side="left", padx=8)
        self._open_btn = btn(btn_row, _t("s5.open_folder"),
                             lambda: os.startfile(str(self._out_folder.get())),
                             primary=False)
        self._open_btn.config(state="disabled")
        self._open_btn.pack(side="left", padx=8)
        self._undo_btn = btn(btn_row, _t("undo.btn"),
                             self._undo_generation, primary=False)
        self._undo_btn.config(state="disabled")
        self._undo_btn.pack(side="left", padx=8)

        self._gen_prog = ttk.Progressbar(right, orient="horizontal",
                                          length=360, mode="determinate")
        self._gen_prog.pack(anchor="w", pady=(0, 6))
        self._gen_log = LogPanel(right)
        self._gen_log.pack(fill="both", expand=True)

        # Thumbnail grid — shown after generation
        self._thumb_frame = styled_frame(right)
        label(self._thumb_frame, _t("s5.grid_title"), font=FONT_H2).pack(anchor="w", pady=(0, 4))
        self._thumb_inner = styled_frame(self._thumb_frame)
        self._thumb_inner.pack(anchor="w")
        self._thumb_frame.pack_forget()   # hidden until generation finishes
        return p

    def _refresh_style_cb(self, *_):
        """Update the style combobox when the family changes."""
        fams   = _get_font_families()
        fam    = self._font_family.get()
        styles = sorted(fams.get(fam, {}).keys())
        if not styles:
            styles = ["Regular"]
        if hasattr(self, '_font_sty_cb'):
            self._font_sty_cb["values"] = styles
        cur = self._font_style.get()
        if cur not in styles:
            self._font_style.set("Regular" if "Regular" in styles else styles[0])
        self._update_font_path()

    def _update_font_path(self, *_):
        """Resolve (family, style) → absolute font path and store in _font_path."""
        fams  = _get_font_families()
        fam   = self._font_family.get()
        sty   = self._font_style.get()
        styles = fams.get(fam, {})
        path  = styles.get(sty) or (next(iter(styles.values())) if styles else "")
        self._font_path.set(path)
        display = fam if sty in ('Regular', '') else f"{fam} {sty}"
        self._font_var.set(display)   # keep display name in sync

    def _open_font_preview(self):
        """Open FontPreviewDialog and apply the selected font (family + style)."""
        dlg = FontPreviewDialog(self, current_font=self._font_family.get())
        self.wait_window(dlg)
        if not dlg.result:
            return
        flat = dlg.result   # e.g. "Arial Bold" or "Arial"
        fams = _get_font_families()
        found = False
        for fam, styles in fams.items():
            for sty, _ in styles.items():
                key = fam if sty == 'Regular' else f"{fam} {sty}"
                if key == flat:
                    self._font_family.set(fam)
                    self._refresh_style_cb()
                    self._font_style.set(sty)
                    self._update_font_path()
                    found = True
                    break
            if found:
                break
        if not found and flat in fams:   # family name typed directly
            self._font_family.set(flat)
            self._refresh_style_cb()

    def _update_preview(self):
        """Update the step-6 summary text and trigger the side preview."""
        nums = _parse_num_list(self._num_list.get())
        n  = len(nums)
        corners_txt = (_t("s5.rounded_r", r=self._radius.get()) if self._corner.get() == "rounded"
                       else _t("s5.sharp"))
        bg_txt = ("Image" if self._bg_mode.get() == "image"
                  else self._bg_color.get())
        auto_txt = _t("s5.auto_tag") if self._auto_fsize.get() else ""
        if hasattr(self, "_summary_lbl"):
            self._summary_lbl.config(
                text="\n".join([
                    _t("s5.sum_canvas", w=self._w_var.get(), h=self._h_var.get()),
                    _t("s5.sum_nums", nums=self._num_list.get(), n=n),
                    _t("s5.sum_font", fam=self._font_family.get(),
                       sty=self._font_style.get(), sz=self._fsize.get(), auto=auto_txt),
                    _t("s5.sum_bg", bg=bg_txt),
                    _t("s5.sum_corners", c=corners_txt),
                    _t("s5.sum_fmt", fmt=self._fmt.get(), pfx=self._prefix.get(),
                       sfx=self._suffix.get()),
                    _t("s5.sum_out", out=self._out_folder.get()),
                ]))
        self._schedule_preview(delay_ms=0)

    # ── Execute ───────────────────────────────────────────────────────────────
    def _execute(self):
        ctext = self._custom_text_var.get().strip()
        if ctext:
            nums = ["banner"]
        else:
            nums = _parse_num_list(self._num_list.get())
            if not nums:
                messagebox.showerror(_t("err.empty_list"), _t("err.empty_msg"), parent=self)
                return
        try:
            w   = int(self._w_var.get())
            h   = int(self._h_var.get())
            fsz = int(self._fsize.get())
            rad = int(self._radius.get())
        except ValueError as exc:
            messagebox.showerror(_t("err.bad_value"), str(exc), parent=self)
            return
        if self._bg_mode.get() == "image" and not self._bg_img.get():
            messagebox.showerror(_t("err.no_bg"), _t("err.no_bg_msg"), parent=self)
            return
        if self._bg_mode.get() == "pattern" and not self._bg_pattern:
            messagebox.showerror(_t("err.no_pat"), _t("err.no_pat_msg"), parent=self)
            return

        out_dir = Path(self._out_folder.get())
        out_dir.mkdir(parents=True, exist_ok=True)

        total = len(nums)
        self._gen_prog["maximum"] = total
        self._gen_prog["value"]   = 0
        self._gen_log.clear()
        self._next_btn.config(state="disabled")
        self._back_btn.config(state="disabled")

        fmt      = self._fmt.get()
        ext      = ".png" if fmt == "PNG" else ".jpg"
        prefix   = self._prefix.get()
        suffix   = self._suffix.get()
        quality  = self._quality.get()
        bg_mode  = self._bg_mode.get()
        bg_col   = self._bg_color.get()
        bg_img   = self._bg_img.get()
        tcol     = self._tcol.get()
        fname    = self._font_path.get() or self._font_var.get()
        corner   = self._corner.get()
        sh_on    = self._shadow_on.get()
        sh_col   = self._shadow_col.get()
        sh_ox    = int(self._shadow_ox.get() or 0)
        sh_oy    = int(self._shadow_oy.get() or 0)
        sh_bl    = int(self._shadow_blur.get() or 0)
        p_man    = self._pos_manual.get()
        p_x      = int(self._pos_x.get() or 0)
        p_y      = int(self._pos_y.get() or 0)
        auto_fit = self._auto_fsize.get()
        tracking = self._tracking.get()
        ctext_snap = ctext  # snapshot for thread

        bg_pattern = list(self._bg_pattern)  # snapshot for thread

        def _worker():
            ok = 0
            generated = []
            for idx, number in enumerate(nums, 1):
                # Resolve bg for this specific image
                cur_bg_mode = bg_mode
                cur_bg_img  = bg_img
                if bg_mode == "pattern" and bg_pattern:
                    cur_bg_mode = "image"
                    cur_bg_img  = bg_pattern[(idx - 1) % len(bg_pattern)]
                try:
                    img = _gen_image(number=number, width=w, height=h,
                                     bg_mode=cur_bg_mode, bg_color=bg_col,
                                     bg_image_path=cur_bg_img, text_color=tcol,
                                     font_name=fname, font_size=fsz,
                                     corner_mode=corner, corner_radius=rad,
                                     shadow_on=sh_on, shadow_color=sh_col,
                                     shadow_offset_x=sh_ox, shadow_offset_y=sh_oy,
                                     shadow_blur=sh_bl,
                                     pos_manual=p_man, pos_x=p_x, pos_y=p_y,
                                     auto_fit=auto_fit, letter_spacing=tracking,
                                     custom_text=ctext_snap)
                    dest = out_dir / f"{prefix}{'banner' if ctext_snap else number}{suffix}{ext}"
                    if fmt == "JPEG":
                        from PIL import Image as _Img
                        flat = _Img.new("RGBA", img.size, (255, 255, 255, 255))
                        if img.mode == "RGBA":
                            flat.paste(img, mask=img.split()[3])
                        else:
                            flat.paste(img)
                        flat.convert("RGB").save(str(dest), "JPEG", quality=quality)
                    else:
                        img.save(str(dest), "PNG")
                    generated.append(dest)
                    ok += 1
                    done = idx
                    self.after(0, lambda d=done, m=f"✔  {dest.name}": (
                        self._gen_log.log(m, "ok"),
                        self._gen_prog.__setitem__("value", d)
                    ))
                except Exception as exc:
                    self.after(0, lambda n=number, e=str(exc):
                               self._gen_log.log(f"✘  #{n}  {e}", "err"))
            self.after(0, lambda: self._on_done(ok, total, out_dir, generated))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, ok, total, out_dir, generated=None):
        self._last_gen_files = generated or []
        self._next_btn.config(state="normal")
        self._back_btn.config(state="normal")
        self._gen_log.log(_t("err.log_sum", ok=ok, total=total, out=out_dir), "dim")
        self._open_btn.config(state="normal")
        self._undo_btn.config(state="normal" if self._last_gen_files else "disabled")
        nums = _parse_num_list(self._num_list.get())
        if nums and ok > 0:
            threading.Thread(
                target=lambda: self._show_thumb_grid(nums, out_dir),
                daemon=True).start()
        if ok == total:
            messagebox.showinfo(_t("err.done"), _t("err.done_msg", ok=ok, out=out_dir), parent=self)

    def _undo_generation(self):
        if not self._last_gen_files:
            messagebox.showinfo(_t("undo.btn"), _t("undo.empty"), parent=self)
            return
        n = len(self._last_gen_files)
        if not messagebox.askyesno(_t("undo.btn"),
                                   _t("undo.confirm", n=n), parent=self):
            return
        deleted = 0
        for p in self._last_gen_files:
            try:
                if p.exists():
                    p.unlink()
                    deleted += 1
            except Exception:
                pass
        self._last_gen_files.clear()
        self._undo_btn.config(state="disabled")
        self._gen_log.log(_t("undo.done", n=deleted), "dim")
        # Also hide the thumbnail grid
        self._thumb_frame.pack_forget()

    def _show_thumb_grid(self, nums, out_dir):
        """Load up to 9 evenly-spaced generated images and build the thumbnail grid."""
        from PIL import Image as _PilImg
        from PIL.ImageTk import PhotoImage as _PILPhoto
        ext     = ".png" if self._fmt.get() == "PNG" else ".jpg"
        pfx     = self._prefix.get()
        sfx     = self._suffix.get()
        THUMB   = 72

        # Pick ≤9 indices evenly spread
        step    = max(1, len(nums) // 9)
        samples = nums[::step][:9]

        photos = []
        for n in samples:
            path = out_dir / f"{pfx}{n}{sfx}{ext}"
            if path.exists():
                try:
                    img = _PilImg.open(str(path)).convert("RGBA")
                    img.thumbnail((THUMB, THUMB), _PilImg.LANCZOS)
                    photos.append((n, _PILPhoto(img)))
                except Exception:
                    pass

        self.after(0, lambda: self._place_thumb_grid(photos))

    def _place_thumb_grid(self, photos):
        """Render the thumbnail grid onto _thumb_inner and reveal _thumb_frame."""
        for w in self._thumb_inner.winfo_children():
            w.destroy()
        self._thumb_photos = [ph for _, ph in photos]   # keep refs alive
        COLS = 3
        for i, (n, ph) in enumerate(photos):
            r, c = divmod(i, COLS)
            cell = styled_frame(self._thumb_inner)
            cell.grid(row=r, column=c, padx=3, pady=3)
            tk.Label(cell, image=ph, bg=BG, relief="flat").pack()
            label(cell, str(n), fg=FG2, font=FONT_SM).pack()
        if photos:
            self._thumb_frame.pack(fill="x", pady=(8, 0))


# =============================================================================
#  CLIPBOARD MANAGER  —  persistent smart clipboard
# =============================================================================
CLIPBOARD_FILE = Path(os.path.abspath(__file__)).parent / "_clipboard_data.json"
PRESETS_FILE   = Path(os.path.abspath(__file__)).parent / "_igw_presets.json"

def _load_presets() -> dict:
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_presets(data: dict):
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_clipboard_data() -> dict:
    if CLIPBOARD_FILE.exists():
        try:
            with open(CLIPBOARD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"categories": [{"name": "Général", "items": []}]}

def save_clipboard_data(data: dict):
    with open(CLIPBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ItemDialog(tk.Toplevel):
    """Small dialog to create or edit a clipboard snippet (label + body text)."""

    def __init__(self, parent, title="Nouvel élément", label_val="", text_val=""):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None  # (label, text) or None if cancelled

        self._build(label_val, text_val)
        self.update_idletasks()
        # Centre on screen
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        scaled_geometry(self, 460, 340)
        _center_win(self)

    def _build(self, label_val, text_val):
        pad = dict(padx=20, pady=8)

        label(self, "Étiquette (nom court)", font=FONT_SM, fg=FG2).pack(anchor="w", **pad)
        self._lbl_var = tk.StringVar(value=label_val)
        lbl_entry = tk.Entry(self, textvariable=self._lbl_var,
                             bg=BG3, fg=FG, insertbackground=FG,
                             relief="flat", font=FONT_BODY)
        lbl_entry.pack(fill="x", padx=20)
        lbl_entry.focus_set()

        label(self, "Contenu du snippet", font=FONT_SM, fg=FG2).pack(anchor="w", padx=20, pady=(12, 4))
        frm = styled_frame(self, bg=BG3)
        frm.pack(fill="both", expand=True, padx=20)
        self._txt = tk.Text(frm, bg=BG3, fg=FG, insertbackground=FG,
                            relief="flat", font=FONT_MONO, wrap="word",
                            width=50, height=8)
        sb = ttk.Scrollbar(frm, orient="vertical", command=self._txt.yview)
        self._txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._txt.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self._txt.insert("1.0", text_val)

        sep = tk.Frame(self, bg=BG3, height=1)
        sep.pack(fill="x", padx=20, pady=(10, 0))

        row = styled_frame(self)
        row.pack(fill="x", padx=20, pady=10)
        btn(row, "Cancel", self._cancel, primary=False).pack(side="right", padx=(6, 0))
        btn(row, "✔  Save", self._save).pack(side="right")

        self.bind("<Escape>", lambda e: self._cancel())

    def _save(self):
        lbl = self._lbl_var.get().strip()
        txt = self._txt.get("1.0", "end-1c")
        if not lbl:
            messagebox.showwarning("Empty field", "Please enter a label.", parent=self)
            return
        if not txt:
            messagebox.showwarning("Empty field", "Content cannot be empty.", parent=self)
            return
        self.result = (lbl, txt)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ClipboardManager(tk.Toplevel):
    """Persistent smart clipboard — categories + snippets, copy in one click."""

    WIN_W, WIN_H = 820, 560

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📋  Presse-papier intelligent")
        self.configure(bg=BG)
        self.resizable(True, True)
        scaled_minsize(self, 660, 420)

        self._data = load_clipboard_data()
        self._cat_idx = 0       # currently selected category index
        self._item_idx = None   # currently selected item index (or None)
        self._flash_id = None   # after() id for status flash

        self._build()
        self._refresh_cats()
        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        scaled_geometry(self, self.WIN_W, self.WIN_H)
        _center_win(self)

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # Header
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        label(hdr, "📋  Presse-papier intelligent", font=FONT_H1).pack(side="left")

        # Search bar
        sf = styled_frame(self)
        sf.pack(fill="x", padx=20, pady=(0, 8))
        label(sf, "🔍", font=FONT_BODY, fg=FG2).pack(side="left", padx=(0, 6))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._on_search())
        self._search_entry = tk.Entry(sf, textvariable=self._search_var,
                                      bg=BG3, fg=FG, insertbackground=FG,
                                      relief="flat", font=FONT_BODY)
        self._search_entry.pack(fill="x", expand=True, ipady=4)
        btn(sf, "✕", lambda: self._search_var.set(""),
            primary=False).pack(side="right", padx=(6, 0))

        separator(self).pack(fill="x", padx=20, pady=(0, 10))

        # Main split panel
        main = styled_frame(self)
        main.pack(fill="both", expand=True, padx=20, pady=(0, 0))

        # ── Left panel: categories ────────────────────────────────────────────
        left = styled_frame(main, bg=BG2)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        left.config(width=190)

        label(left, "Catégories", font=FONT_H2, bg=BG2).pack(anchor="w", padx=10, pady=(10, 4))

        cat_frm = tk.Frame(left, bg=BG2)
        cat_frm.pack(fill="both", expand=True, padx=6)
        sb_c = ttk.Scrollbar(cat_frm, orient="vertical")
        self._cat_lb = tk.Listbox(cat_frm, bg=BG3, fg=FG, selectbackground=ACCENT,
                                  selectforeground=FG, relief="flat",
                                  activestyle="none", font=FONT_BODY,
                                  yscrollcommand=sb_c.set, exportselection=False)
        sb_c.config(command=self._cat_lb.yview)
        sb_c.pack(side="right", fill="y")
        self._cat_lb.pack(fill="both", expand=True)
        self._cat_lb.bind("<<ListboxSelect>>", self._on_cat_select)

        cat_btns = styled_frame(left, bg=BG2)
        cat_btns.pack(fill="x", padx=6, pady=6)
        btn(cat_btns, "+ Nouvelle",  self._cat_new,    primary=True ).pack(side="left", padx=(0,3))
        btn(cat_btns, "✏",           self._cat_rename, primary=False).pack(side="left", padx=3)
        btn(cat_btns, "🗑",           self._cat_delete, primary=False).pack(side="left", padx=3)

        # ── Right panel: items + preview ──────────────────────────────────────
        right = styled_frame(main)
        right.pack(side="left", fill="both", expand=True)

        # Items listbox (top half)
        items_lbl = styled_frame(right)
        items_lbl.pack(fill="x")
        label(items_lbl, "Éléments", font=FONT_H2).pack(side="left", pady=(0, 4))

        items_frm = tk.Frame(right, bg=BG2, height=160)
        items_frm.pack(fill="x")
        items_frm.pack_propagate(False)
        isb = ttk.Scrollbar(items_frm, orient="vertical")
        self._item_lb = tk.Listbox(items_frm, bg=BG3, fg=FG, selectbackground=ACCENT,
                                   selectforeground=FG, relief="flat",
                                   activestyle="none", font=FONT_BODY,
                                   yscrollcommand=isb.set, exportselection=False)
        isb.config(command=self._item_lb.yview)
        isb.pack(side="right", fill="y")
        self._item_lb.pack(fill="both", expand=True)
        self._item_lb.bind("<<ListboxSelect>>", self._on_item_select)
        self._item_lb.bind("<Double-Button-1>", lambda e: self._copy_item())
        self._item_lb.bind("<Return>",           lambda e: self._copy_item())

        # Item action buttons
        ibtns = styled_frame(right)
        ibtns.pack(fill="x", pady=(4, 0))
        btn(ibtns, "📋  Copier",     self._copy_item,   primary=True ).pack(side="left", padx=(0, 4))
        btn(ibtns, "+ Nouveau",       self._item_new,    primary=False).pack(side="left", padx=4)
        btn(ibtns, "✏  Modifier",    self._item_edit,   primary=False).pack(side="left", padx=4)
        btn(ibtns, "↑",              self._item_up,     primary=False).pack(side="left", padx=2)
        btn(ibtns, "↓",              self._item_down,   primary=False).pack(side="left", padx=2)
        btn(ibtns, "🗑",              self._item_delete, primary=False).pack(side="left", padx=4)

        separator(right).pack(fill="x", pady=8)

        # Content preview
        label(right, "Content preview", font=FONT_SM, fg=FG2).pack(anchor="w")
        preview_frm = tk.Frame(right, bg=BG3)
        preview_frm.pack(fill="both", expand=True, pady=(4, 0))
        psb = ttk.Scrollbar(preview_frm, orient="vertical")
        self._preview = tk.Text(preview_frm, bg=BG3, fg=FG, font=FONT_MONO,
                                relief="flat", state="disabled", wrap="word",
                                yscrollcommand=psb.set)
        psb.config(command=self._preview.yview)
        psb.pack(side="right", fill="y")
        self._preview.pack(fill="both", expand=True, padx=4, pady=4)

        # Status bar
        self._status_var = tk.StringVar(value="")
        self._status = label(self, textvariable=self._status_var,
                             fg=FG_GREEN, font=FONT_SM, anchor="w")
        self._status.pack(fill="x", padx=20, pady=(6, 10))

    # ── Data helpers ──────────────────────────────────────────────────────────
    @property
    def _cats(self):
        return self._data["categories"]

    def _current_cat(self):
        if 0 <= self._cat_idx < len(self._cats):
            return self._cats[self._cat_idx]
        return None

    def _current_items(self):
        cat = self._current_cat()
        return cat["items"] if cat else []

    def _save(self):
        save_clipboard_data(self._data)

    # ── Refresh helpers ───────────────────────────────────────────────────────
    def _refresh_cats(self, keep_sel=True):
        prev = self._cat_idx
        self._cat_lb.delete(0, "end")
        for c in self._cats:
            n = len(c["items"])
            self._cat_lb.insert("end", f"  {c['name']}  ({n})")
        if self._cats:
            idx = min(prev, len(self._cats) - 1)
            self._cat_idx = idx
            self._cat_lb.selection_set(idx)
            self._cat_lb.see(idx)
        self._refresh_items()

    def _refresh_items(self, keep_sel=True):
        prev = self._item_idx
        self._item_lb.delete(0, "end")
        for it in self._current_items():
            self._item_lb.insert("end", f"  {it['label']}")
        if self._current_items():
            idx = 0 if prev is None else min(prev, len(self._current_items()) - 1)
            self._item_idx = idx
            self._item_lb.selection_set(idx)
            self._item_lb.see(idx)
        else:
            self._item_idx = None
        self._refresh_preview()

    def _refresh_preview(self):
        self._preview.config(state="normal")
        self._preview.delete("1.0", "end")
        items = self._current_items()
        if self._item_idx is not None and 0 <= self._item_idx < len(items):
            self._preview.insert("1.0", items[self._item_idx]["text"])
        self._preview.config(state="disabled")

    # ── Event handlers ────────────────────────────────────────────────────────
    def _on_cat_select(self, _event=None):
        sel = self._cat_lb.curselection()
        if sel:
            self._cat_idx = sel[0]
            self._item_idx = None
            self._refresh_items()

    def _on_item_select(self, _event=None):
        sel = self._item_lb.curselection()
        if sel:
            self._item_idx = sel[0]
            self._refresh_preview()

    def _on_search(self):
        query = self._search_var.get().strip().lower()
        self._item_lb.delete(0, "end")
        if not query:
            self._refresh_items()
            return
        # Search across all categories, show flat results
        results = []
        for cat in self._cats:
            for it in cat["items"]:
                if query in it["label"].lower() or query in it["text"].lower():
                    results.append((cat["name"], it))
        for cat_name, it in results:
            self._item_lb.insert("end", f"  [{cat_name}]  {it['label']}")
        # Store results for copy
        self._search_results = results

    def _flash(self, msg, color=FG_GREEN):
        if self._flash_id:
            self.after_cancel(self._flash_id)
        self._status_var.set(msg)
        self._status.config(fg=color)
        self._flash_id = self.after(2500, lambda: self._status_var.set(""))

    # ── Copy ──────────────────────────────────────────────────────────────────
    def _copy_item(self):
        query = self._search_var.get().strip()
        sel = self._item_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if query:
            if not hasattr(self, "_search_results") or idx >= len(self._search_results):
                return
            _, it = self._search_results[idx]
        else:
            items = self._current_items()
            if idx >= len(items):
                return
            it = items[idx]
        self.clipboard_clear()
        self.clipboard_append(it["text"])
        self._flash(f"✔  Copié : {it['label']}")

    # ── Category CRUD ─────────────────────────────────────────────────────────
    def _cat_new(self):
        name = self._ask_string("Nouvelle catégorie", "Nom de la catégorie :")
        if not name:
            return
        self._cats.append({"name": name, "items": []})
        self._cat_idx = len(self._cats) - 1
        self._save()
        self._refresh_cats()

    def _cat_rename(self):
        cat = self._current_cat()
        if not cat:
            return
        name = self._ask_string("Renommer", "Nouveau nom :", default=cat["name"])
        if not name:
            return
        cat["name"] = name
        self._save()
        self._refresh_cats()

    def _cat_delete(self):
        cat = self._current_cat()
        if not cat:
            return
        if not messagebox.askyesno("Supprimer",
                                   f"Supprimer la catégorie « {cat['name']} » et tous ses éléments ?",
                                   parent=self):
            return
        self._cats.pop(self._cat_idx)
        if not self._cats:
            self._cats.append({"name": "Général", "items": []})
        self._cat_idx = max(0, self._cat_idx - 1)
        self._save()
        self._refresh_cats()

    # ── Item CRUD ─────────────────────────────────────────────────────────────
    def _item_new(self):
        if not self._current_cat():
            return
        dlg = ItemDialog(self, title="Nouvel élément")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        lbl, txt = dlg.result
        self._current_items().append({"label": lbl, "text": txt})
        self._item_idx = len(self._current_items()) - 1
        self._save()
        self._refresh_cats()

    def _item_edit(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx >= len(items):
            return
        it = items[self._item_idx]
        dlg = ItemDialog(self, title="Modifier l'élément",
                         label_val=it["label"], text_val=it["text"])
        self.wait_window(dlg)
        if dlg.result is None:
            return
        lbl, txt = dlg.result
        it["label"] = lbl
        it["text"] = txt
        self._save()
        self._refresh_items()

    def _item_delete(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx >= len(items):
            return
        it = items[self._item_idx]
        if not messagebox.askyesno("Supprimer",
                                   f"Supprimer « {it['label']} » ?", parent=self):
            return
        items.pop(self._item_idx)
        self._item_idx = max(0, self._item_idx - 1) if items else None
        self._save()
        self._refresh_cats()

    def _item_up(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx <= 0:
            return
        i = self._item_idx
        items[i], items[i - 1] = items[i - 1], items[i]
        self._item_idx -= 1
        self._save()
        self._refresh_items()

    def _item_down(self):
        items = self._current_items()
        if self._item_idx is None or self._item_idx >= len(items) - 1:
            return
        i = self._item_idx
        items[i], items[i + 1] = items[i + 1], items[i]
        self._item_idx += 1
        self._save()
        self._refresh_items()

    # ── Simple ask-string helper (avoids simpledialog import) ─────────────────
    def _ask_string(self, title, prompt, default=""):
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        result = tk.StringVar(value=default)

        label(dlg, prompt, font=FONT_BODY).pack(padx=20, pady=(16, 4), anchor="w")
        e = tk.Entry(dlg, textvariable=result, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat", font=FONT_BODY, width=36)
        e.pack(padx=20, fill="x")
        e.select_range(0, "end")
        e.focus_set()

        out = [None]

        def confirm():
            v = result.get().strip()
            if v:
                out[0] = v
            dlg.destroy()

        row = styled_frame(dlg)
        row.pack(fill="x", padx=20, pady=12)
        btn(row, "Annuler", dlg.destroy, primary=False).pack(side="right", padx=(6, 0))
        btn(row, "OK", confirm).pack(side="right")
        dlg.bind("<Return>", lambda e: confirm())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        pw, py = self.winfo_rootx(), self.winfo_rooty()
        w, h = 360, 140
        dlg.geometry(f"{w}x{h}+{pw+80}+{py+80}")
        self.wait_window(dlg)
        return out[0]


# =============================================================================
#  TOOL: CONVERT
# =============================================================================
class ConvertWizard(tk.Toplevel):
    _FMT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WebP": ".webp", "BMP": ".bmp"}

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Image Tool  —  Convert")
        self.configure(bg=BG)
        scaled_geometry(self, 920, 680)
        self.resizable(True, True)
        self._folder = None
        self._files  = []
        self._build()

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        label(hdr, "🔄  " + _t("conv.title"), font=FONT_H1).pack(side="left")
        separator(self).pack(fill="x", padx=20, pady=10)

        body = styled_frame(self)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # ── Folder browser ────────────────────────────────────────────────────
        label(body, "Source folder", font=FONT_H2).pack(anchor="w", pady=(0, 6))
        self._browser = FolderBrowser(body, next_cb=self._load_files_ui)
        self._browser.pack(anchor="w", fill="x")

        separator(body).pack(fill="x", pady=10)

        # ── Format selectors ─────────────────────────────────────────────────
        fmt_row = styled_frame(body)
        fmt_row.pack(anchor="w", fill="x", pady=4)

        label(fmt_row, _t("conv.src_fmt")).pack(side="left")
        self._src_fmt = ttk.Combobox(fmt_row, values=["ALL", "JPEG", "PNG", "WebP", "BMP"],
                                      state="readonly", width=10,
                                      style="Custom.TCombobox", font=FONT_BODY)
        self._src_fmt.set("ALL")
        self._src_fmt.pack(side="left", padx=(6, 20))
        self._src_fmt.bind("<<ComboboxSelected>>", lambda e: self._load_files_ui())

        label(fmt_row, _t("conv.dst_fmt")).pack(side="left")
        self._dst_fmt = ttk.Combobox(fmt_row, values=["JPEG", "PNG", "WebP", "BMP"],
                                      state="readonly", width=10,
                                      style="Custom.TCombobox", font=FONT_BODY)
        self._dst_fmt.set("PNG")
        self._dst_fmt.pack(side="left", padx=6)
        self._dst_fmt.bind("<<ComboboxSelected>>", self._toggle_quality)

        # JPEG quality (shown only when target is JPEG)
        self._qual_row = styled_frame(body)
        self._qual_row.pack(anchor="w", fill="x", pady=2)
        label(self._qual_row, _t("conv.quality")).pack(side="left")
        self._quality = tk.IntVar(value=92)
        _sc_qual_conv = tk.Scale(self._qual_row, from_=60, to=100, variable=self._quality,
                 orient="horizontal", bg=BG2, fg=FG, highlightthickness=0,
                 troughcolor=BG3, activebackground=ACCENT, length=180)
        _sc_qual_conv.pack(side="left", padx=6)
        _arrow_scale(_sc_qual_conv)
        label(self._qual_row, "%", fg=FG2, font=FONT_SM).pack(side="left")
        self._toggle_quality()

        separator(body).pack(fill="x", pady=10)

        # ── Output options ────────────────────────────────────────────────────
        self._overwrite = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text=_t("conv.overwrite"),
                       variable=self._overwrite,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY).pack(anchor="w")
        self._sub_folder = tk.BooleanVar(value=True)
        tk.Checkbutton(body, text=_t("conv.out_sub"),
                       variable=self._sub_folder,
                       bg=BG, fg=FG, selectcolor=ACCENT, activebackground=BG,
                       font=FONT_BODY).pack(anchor="w", pady=(4, 0))

        separator(body).pack(fill="x", pady=10)

        # ── File list + progress ──────────────────────────────────────────────
        self._file_count_lbl = label(body, "", fg=FG2, font=FONT_SM)
        self._file_count_lbl.pack(anchor="w", pady=(0, 4))
        self._prog = ttk.Progressbar(body, orient="horizontal",
                                     length=460, mode="determinate")
        self._prog.pack(anchor="w", pady=(0, 6))
        self._log  = LogPanel(body)
        self._log.pack(fill="both", expand=True)

        separator(self).pack(fill="x", padx=20, pady=6)
        nav = styled_frame(self)
        nav.pack(fill="x", padx=24, pady=(0, 16))
        self._go_btn = btn(nav, _t("conv.go"), self._execute, primary=True)
        self._go_btn.pack(side="right")

    def _toggle_quality(self, e=None):
        if self._dst_fmt.get() == "JPEG":
            self._qual_row.pack(anchor="w", fill="x", pady=2)
        else:
            self._qual_row.pack_forget()

    def _load_files_ui(self):
        folder = self._browser.get()
        if not folder:
            return
        self._folder = Path(folder)
        src = self._src_fmt.get()
        if src == "ALL":
            exts = ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp"]
        else:
            ext_map = {"JPEG": ["*.jpg", "*.jpeg"], "PNG": ["*.png"],
                       "WebP": ["*.webp"], "BMP": ["*.bmp"]}
            exts = ext_map.get(src, ["*.*"])
        self._files = collect_files(self._folder, exts, recurse=False)
        n = len(self._files)
        self._file_count_lbl.config(text=f"{n} file(s) found")

    def _execute(self):
        if not self._files:
            self._load_files_ui()
        if not self._files:
            messagebox.showwarning("No files", "No matching files found.", parent=self)
            return

        dst_name = self._dst_fmt.get()
        dst_ext  = self._FMT_EXT[dst_name]
        pil_fmt  = dst_name if dst_name != "JPEG" else "JPEG"
        quality  = self._quality.get()
        overwrite = self._overwrite.get()
        use_sub   = self._sub_folder.get()

        files = list(self._files)
        total = len(files)
        self._prog["maximum"] = total
        self._prog["value"]   = 0
        self._log.clear()
        self._go_btn.config(state="disabled")

        def _worker():
            ok = 0
            for idx, src_path in enumerate(files, 1):
                try:
                    from PIL import Image as _Img
                    img = _Img.open(str(src_path))

                    if pil_fmt == "JPEG":
                        if img.mode in ("RGBA", "P"):
                            bg = _Img.new("RGB", img.size, (255, 255, 255))
                            if img.mode == "RGBA":
                                bg.paste(img, mask=img.split()[3])
                            else:
                                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
                            img = bg
                        elif img.mode != "RGB":
                            img = img.convert("RGB")

                    if overwrite:
                        out_path = src_path.with_suffix(dst_ext)
                    elif use_sub:
                        out_dir  = src_path.parent / "converted"
                        out_dir.mkdir(exist_ok=True)
                        out_path = out_dir / src_path.with_suffix(dst_ext).name
                    else:
                        out_path = src_path.with_suffix(dst_ext)

                    save_kw = {"quality": quality, "optimize": True} if pil_fmt == "JPEG" else {}
                    img.save(str(out_path), pil_fmt, **save_kw)
                    ok += 1
                    self.after(0, lambda d=idx, m=f"✔  {src_path.name}  →  {out_path.name}": (
                        self._log.log(m, "ok"),
                        self._prog.__setitem__("value", d)))
                except Exception as exc:
                    self.after(0, lambda n=src_path.name, e=str(exc):
                               self._log.log(f"✘  {n}  {e}", "err"))
            self.after(0, lambda: self._on_done(ok, total))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, ok, total):
        self._go_btn.config(state="normal")
        self._log.log(_t("conv.done", ok=ok, total=total), "dim")
        if ok == total:
            messagebox.showinfo("Done ✔", _t("conv.done", ok=ok, total=total), parent=self)


# =============================================================================
#  GUIDE MARK WIZARD
# =============================================================================
class GuideMarkWizard(tk.Toplevel):
    """3-step print wizard.

    Step 0 · Design   — pick template image(s) from disk
    Step 1 · Configure — number range, font settings, live tile preview
    Step 2 · Print     — paper / layout preview, export / print

    Tiles = your template image resized to tile dimensions + number overlaid.
    No PIL drawing — just your own files used as backgrounds.
    """

    _TPL_CFG = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_guidemark_templates.json")
    _PRESET_CFG = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_guidemark_presets.json")

    PAPER_MM = PAPER_MM

    # Reference DPI for stored tracking values (see _render_tile) — chosen
    # as 150 because Step 1's tile preview already renders at 150 DPI and
    # was already visually faithful to a real export; tracking is scaled
    # from this reference to whatever DPI a given render actually uses.
    TRACKING_REF_DPI = TRACKING_REF_DPI

    def __init__(self, master):
        super().__init__(master)
        self.title(_t("gmw.title"))
        self.configure(bg=BG)
        scaled_geometry(self, 980, 660)
        scaled_minsize(self, 780, 520)
        self.resizable(True, True)

        self._step = 0
        self._selected_tpl: dict = {}
        self._selected_tpl3: dict = {}   # override template for 3-digit (100+) cards
        self._templates: list = self._load_templates()

        # ── State vars ────────────────────────────────────────────────────
        self._num_range_str = tk.StringVar(value="1-15")
        self._paper         = tk.StringVar(value=list(self.PAPER_MM)[0])
        self._orient        = tk.StringVar(value="landscape")
        self._tile_w        = tk.DoubleVar(value=100.0)
        self._tile_h        = tk.DoubleVar(value=100.0)
        self._margin        = tk.DoubleVar(value=10.0)
        self._gap           = tk.DoubleVar(value=5.0)
        self._dpi           = tk.IntVar(value=300)
        self._out_folder    = tk.StringVar()
        self._export_name   = tk.StringVar(value="guidemark_layout")
        self._printer       = tk.StringVar(value="")
        self._printers: list = []
        self._monochrome    = tk.BooleanVar(value=True)
        self._k_pct         = tk.IntVar(value=70)
        self._num_font_fam  = tk.StringVar(value="Futura Std")
        self._num_font_sty  = tk.StringVar(value="Medium")
        self._num_font_display = tk.StringVar(value="Futura Std Medium")
        self._num_auto      = tk.BooleanVar(value=True)
        self._num_fsize_pt  = tk.IntVar(value=120)   # 1-2 digit manual pt
        self._num_fsize3_pt = tk.IntVar(value=90)    # 3+ digit manual/auto cap pt
        self._num_color     = tk.StringVar(value="#000000")
        self._num_pos_x     = tk.IntVar(value=50)   # % of tile width  (1-2 digit)
        self._num_pos_y     = tk.IntVar(value=42)   # % of tile height (1-2 digit)
        self._num_tracking  = tk.IntVar(value=0)    # letter spacing in px (1-2 digit)
        self._num_kern_mode = tk.StringVar(value="メトリクス")  # メトリクス / オプティカル / 等幅 (1-2 digit)
        # ── 3-digit-specific overrides ─────────────────────────────────────
        self._num3_pos_x     = tk.IntVar(value=50)
        self._num3_pos_y     = tk.IntVar(value=42)
        self._num3_tracking  = tk.IntVar(value=0)
        self._num3_kern_mode = tk.StringVar(value="メトリクス")
        self._num_lock      = tk.BooleanVar(value=False)  # lock ratio on tile resize
        self._tile_w_prev   = 100.0   # previous tile_w for lock scaling
        self._tile_h_prev   = 100.0   # previous tile_h for lock scaling
        self._preview_num   = tk.StringVar(value="1")   # label, e.g. 14 or 14-1
        # ── Display helpers (driven by traces after _build) ────────────────
        self._pos_x_pct_str  = tk.StringVar(value="50%")
        self._pos_y_pct_str  = tk.StringVar(value="42%")
        self._pos_x_px_str   = tk.StringVar(value="")
        self._pos_y_px_str   = tk.StringVar(value="")
        self._pos_x_mm_str   = tk.StringVar(value="")
        self._pos_y_mm_str   = tk.StringVar(value="")
        self._pos3_x_pct_str = tk.StringVar(value="50%")
        self._pos3_y_pct_str = tk.StringVar(value="42%")
        self._pos3_x_mm_str  = tk.StringVar(value="")
        self._pos3_y_mm_str  = tk.StringVar(value="")
        self._tile_px_str    = tk.StringVar(value="")

        self._sched_tile_id   = None
        self._sched_layout_id = None
        self._tile_tk   = None
        self._layout_tk = None
        self._layout_info     = tk.StringVar(value="—")
        self._layout_pages:   list = []
        self._layout_page_idx = tk.IntVar(value=0)
        self._layout_page_str = tk.StringVar(value="—")
        self._cut_line_show   = tk.BooleanVar(value=True)
        self._cut_line_pt     = tk.DoubleVar(value=0.2)
        # Illustrator (SVG) export
        self._svg_marks       = tk.StringVar(value="tombo")
        self._svg_bleed       = tk.DoubleVar(value=3.0)
        self._svg_mark_len    = tk.DoubleVar(value=10.0)
        self._svg_center      = tk.BooleanVar(value=True)
        self._svg_outline     = tk.BooleanVar(value=False)
        self._svg_single      = tk.BooleanVar(value=True)   # one file for all pages
        self._svg_artboards   = tk.BooleanVar(value=True)   # .jsx -> artboard per page
        self._svg_border_crop = tk.DoubleVar(value=0.8)     # hide baked-in frame
        self._preset_name     = tk.StringVar(value="")
        self._presets: list   = self._load_presets()
        self._thumb_cache: dict = {}

        self._build()
        self._refresh_cards()
        self._load_printers_async()

        for v in (self._monochrome, self._k_pct,
                  self._num_font_fam, self._num_font_sty, self._num_auto,
                  self._num_fsize_pt, self._num_fsize3_pt, self._num_color,
                  self._num_pos_x, self._num_pos_y, self._num_tracking,
                  self._num_kern_mode,
                  self._num3_pos_x, self._num3_pos_y, self._num3_tracking,
                  self._num3_kern_mode,
                  self._tile_w, self._tile_h, self._preview_num):
            v.trace_add("write", lambda *_: self._schedule_tile_preview())

        for v in (self._num_range_str, self._paper, self._orient,
                  self._margin, self._gap, self._dpi, self._k_pct,
                  self._num_tracking, self._num_kern_mode,
                  self._num3_tracking, self._num3_kern_mode,
                  self._cut_line_show, self._cut_line_pt):
            v.trace_add("write", lambda *_: self._schedule_layout_preview())

        def _upd_px_displays(*_):
            if not self.winfo_exists():
                return
            dpi   = max(1, self._dpi.get())
            tw_px = max(1, round(self._tile_w.get() / 25.4 * dpi))
            th_px = max(1, round(self._tile_h.get() / 25.4 * dpi))
            self._tile_px_str.set(f"= {tw_px} × {th_px} px")
            self._pos_x_pct_str.set(f"{self._num_pos_x.get()}%")
            self._pos_y_pct_str.set(f"{self._num_pos_y.get()}%")
            self._pos_x_px_str.set(
                f"≈ {round(self._num_pos_x.get() / 100 * tw_px)} px")
            self._pos_y_px_str.set(
                f"≈ {round(self._num_pos_y.get() / 100 * th_px)} px")
            self._pos_x_mm_str.set(
                f"= {self._num_pos_x.get() / 100 * self._tile_w.get():.1f} mm")
            self._pos_y_mm_str.set(
                f"= {self._num_pos_y.get() / 100 * self._tile_h.get():.1f} mm")
            self._pos3_x_pct_str.set(f"{self._num3_pos_x.get()}%")
            self._pos3_y_pct_str.set(f"{self._num3_pos_y.get()}%")
            self._pos3_x_mm_str.set(
                f"= {self._num3_pos_x.get() / 100 * self._tile_w.get():.1f} mm")
            self._pos3_y_mm_str.set(
                f"= {self._num3_pos_y.get() / 100 * self._tile_h.get():.1f} mm")
        for v in (self._tile_w, self._tile_h, self._dpi,
                  self._num_pos_x, self._num_pos_y,
                  self._num3_pos_x, self._num3_pos_y):
            v.trace_add("write", _upd_px_displays)
        _upd_px_displays()

        def _on_tile_size_change(*_):
            """When lock is on, scale font sizes proportionally to tile area."""
            if not self._num_lock.get():
                self._tile_w_prev = self._tile_w.get()
                self._tile_h_prev = self._tile_h.get()
                return
            new_w = self._tile_w.get()
            new_h = self._tile_h.get()
            prev_w = self._tile_w_prev
            prev_h = self._tile_h_prev
            if prev_w <= 0 or prev_h <= 0:
                self._tile_w_prev = new_w
                self._tile_h_prev = new_h
                return
            # Scale by the geometric mean of the two dimension ratios
            ratio = ((new_w / prev_w) * (new_h / prev_h)) ** 0.5
            if abs(ratio - 1.0) > 0.001:
                self._num_fsize_pt.set(
                    max(4, round(self._num_fsize_pt.get() * ratio)))
                self._num_fsize3_pt.set(
                    max(4, round(self._num_fsize3_pt.get() * ratio)))
            self._tile_w_prev = new_w
            self._tile_h_prev = new_h
        self._tile_w.trace_add("write", _on_tile_size_change)
        self._tile_h.trace_add("write", _on_tile_size_change)

    # ═════════════════════════════════════════════════════════════════════
    # Template persistence
    # ═════════════════════════════════════════════════════════════════════
    def _load_templates(self) -> list:
        try:
            with open(self._TPL_CFG, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [t for t in data
                    if isinstance(t, dict) and "path" in t
                    and os.path.isfile(t["path"])]
        except Exception:
            return []

    def _save_templates(self):
        try:
            with open(self._TPL_CFG, "w", encoding="utf-8") as f:
                json.dump(self._templates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════
    # Preset persistence
    # ═════════════════════════════════════════════════════════════════════
    def _load_presets(self) -> list:
        try:
            with open(self._PRESET_CFG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_presets(self):
        try:
            with open(self._PRESET_CFG, "w", encoding="utf-8") as f:
                json.dump(self._presets, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _preset_data(self) -> dict:
        return {
            "name":          self._preset_name.get(),
            "num_range":     self._num_range_str.get(),
            "tile_w":        self._tile_w.get(),
            "tile_h":        self._tile_h.get(),
            "margin":        self._margin.get(),
            "gap":           self._gap.get(),
            "dpi":           self._dpi.get(),
            "paper":         self._paper.get(),
            "orient":        self._orient.get(),
            "monochrome":    self._monochrome.get(),
            "k_pct":         self._k_pct.get(),
            "num_font_fam":  self._num_font_fam.get(),
            "num_font_sty":  self._num_font_sty.get(),
            "num_auto":      self._num_auto.get(),
            "num_fsize_pt":  self._num_fsize_pt.get(),
            "num_fsize3_pt": self._num_fsize3_pt.get(),
            "num_color":     self._num_color.get(),
            "num_pos_x":     self._num_pos_x.get(),
            "num_pos_y":       self._num_pos_y.get(),
            "num_tracking":    self._num_tracking.get(),
            "num_kern_mode":   self._num_kern_mode.get(),
            "num3_pos_x":      self._num3_pos_x.get(),
            "num3_pos_y":      self._num3_pos_y.get(),
            "num3_tracking":   self._num3_tracking.get(),
            "num3_kern_mode":  self._num3_kern_mode.get(),
            "cut_line_show":   self._cut_line_show.get(),
            "cut_line_pt":   self._cut_line_pt.get(),
            "svg_marks":     self._svg_marks.get(),
            "svg_bleed":     self._svg_bleed.get(),
            "svg_mark_len":  self._svg_mark_len.get(),
            "svg_center":    self._svg_center.get(),
            "svg_outline":   self._svg_outline.get(),
            "svg_single":    self._svg_single.get(),
            "svg_artboards": self._svg_artboards.get(),
            "svg_border_crop": self._svg_border_crop.get(),
            "template_path": self._selected_tpl.get("path", ""),
            "template3_path": self._selected_tpl3.get("path", ""),
        }

    def _apply_preset(self, d: dict):
        def _s(var, key): var.set(d[key]) if key in d else None
        if "name" in d:
            self._preset_name.set(d["name"])
        _s(self._num_range_str, "num_range")
        _s(self._tile_w,        "tile_w")
        _s(self._tile_h,        "tile_h")
        _s(self._margin,        "margin")
        _s(self._gap,           "gap")
        _s(self._dpi,           "dpi")
        _s(self._paper,         "paper")
        _s(self._orient,        "orient")
        _s(self._monochrome,    "monochrome")
        _s(self._k_pct,          "k_pct")
        _s(self._num_font_fam,  "num_font_fam")
        _s(self._num_font_sty,  "num_font_sty")
        _s(self._num_auto,      "num_auto")
        _s(self._num_fsize_pt,  "num_fsize_pt")
        _s(self._num_fsize3_pt, "num_fsize3_pt")
        _s(self._num_color,     "num_color")
        _s(self._num_pos_x,      "num_pos_x")
        _s(self._num_pos_y,      "num_pos_y")
        _s(self._num_tracking,   "num_tracking")
        _s(self._num_kern_mode,  "num_kern_mode")
        _s(self._num3_pos_x,     "num3_pos_x")
        _s(self._num3_pos_y,     "num3_pos_y")
        _s(self._num3_tracking,  "num3_tracking")
        _s(self._num3_kern_mode, "num3_kern_mode")
        _s(self._cut_line_show,  "cut_line_show")
        _s(self._cut_line_pt,   "cut_line_pt")
        _s(self._svg_marks,     "svg_marks")
        _s(self._svg_bleed,     "svg_bleed")
        _s(self._svg_mark_len,  "svg_mark_len")
        _s(self._svg_center,    "svg_center")
        _s(self._svg_outline,   "svg_outline")
        _s(self._svg_single,    "svg_single")
        _s(self._svg_artboards, "svg_artboards")
        _s(self._svg_border_crop, "svg_border_crop")
        tp = d.get("template_path", "")
        if tp and os.path.isfile(tp):
            self._selected_tpl = {
                "path": tp,
                "name": os.path.splitext(os.path.basename(tp))[0]
            }
        tp3 = d.get("template3_path", "")
        if tp3 and os.path.isfile(tp3):
            self._selected_tpl3 = {
                "path": tp3,
                "name": os.path.splitext(os.path.basename(tp3))[0]
            }
        else:
            self._selected_tpl3 = {}
        self._refresh_cards()
        self._update_step1_template_display()
        self._update_step1_tpl3_display()
        self._toggle_num_spin()
        self._update_font_display()

    def _save_preset_as(self):
        name = self._preset_name.get().strip()
        if not name:
            messagebox.showwarning("Preset",
                "Enter a preset name in the field first.", parent=self)
            return
        d = self._preset_data()
        d["name"] = name
        for i, p in enumerate(self._presets):
            if p.get("name") == name:
                self._presets[i] = d
                break
        else:
            self._presets.append(d)
        self._save_presets()
        messagebox.showinfo("Preset", f'Preset "{name}" saved.', parent=self)

    # ═════════════════════════════════════════════════════════════════════
    # Build
    # ═════════════════════════════════════════════════════════════════════
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Step nav bar ──────────────────────────────────────────────────
        self._step_bar = StepBar(self, [_t("gmw.step_design"),
                                         _t("gmw.step_configure"),
                                         _t("gmw.step_print")])
        self._step_bar.grid(row=0, column=0, sticky="ew")

        # ── Content frames ────────────────────────────────────────────────
        content = tk.Frame(self, bg=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self._frames: list = []
        for _ in range(3):
            f = tk.Frame(content, bg=BG)
            f.grid(row=0, column=0, sticky="nsew")
            self._frames.append(f)

        self._build_step0()
        self._build_step1()
        self._build_step2()
        # Propagate mousewheel to step-1 and step-2 scroll canvases for all children
        def _bind_scroll(cv_ref, w):
            try:
                w.bind("<MouseWheel>",
                       lambda e, cv=cv_ref: cv.yview_scroll(
                           int(-1*(e.delta/120)), "units"))
            except Exception:
                pass
            for c in w.winfo_children():
                _bind_scroll(cv_ref, c)
        self.after(200, lambda: _bind_scroll(self._lp1_cv_ref, self._lp1_cv_ref))
        self.after(200, lambda: _bind_scroll(self._lp_cv_ref, self._lp_cv_ref))

        # ── Bottom nav ────────────────────────────────────────────────────
        bot = tk.Frame(self, bg=BG2)
        bot.grid(row=2, column=0, sticky="ew")
        bot.columnconfigure(1, weight=1)
        self._btn_back = btn(bot, "← " + _t("gmw.back"), self._go_back,
                              primary=False)
        self._btn_back.grid(row=0, column=0, padx=12, pady=8)
        # Plain tk.Button here, not ui_kit.btn(): its bg/text/state are
        # driven directly by _show_step() (disabled + blank on the last
        # step) which would fight btn()'s hover bindings (they always
        # restore the colours captured at creation time).
        self._btn_next = tk.Button(
            bot, text=_t("gmw.next") + " →", bg=ACCENT, fg=FG_ON_ACCENT,
            relief="flat", font=FONT_BODY,
            cursor="hand2", padx=16, pady=8, command=self._go_next)
        self._btn_next.grid(row=0, column=2, padx=12, pady=8)

        self._show_step(0)

    # ── Step 0 : Design picker (image cards) ──────────────────────────────
    def _build_step0(self):
        f = self._frames[0]
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        tk.Label(f, text=_t("gmw.pick_design"), bg=BG, fg=FG,
                 font=FONT_H2).grid(row=0, column=0, pady=(28, 14))

        # Horizontally scrollable card row
        outer = tk.Frame(f, bg=BG)
        outer.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 10))
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = scrollbar(outer, orient="horizontal", command=cv.xview)
        cv.configure(xscrollcommand=sb.set)
        sb.grid(row=1, column=0, sticky="ew")
        cv.grid(row=0, column=0, sticky="nsew")

        self._cards_inner = tk.Frame(cv, bg=BG)
        _win = cv.create_window((0, 0), window=self._cards_inner, anchor="nw")
        self._cards_inner.bind("<Configure>", lambda e: (
            cv.configure(scrollregion=cv.bbox("all")),
            cv.itemconfig(_win, height=cv.winfo_height())))
        cv.bind("<Configure>", lambda e: cv.itemconfig(_win, height=e.height))
        self._cards_cv = cv

    def _refresh_cards(self):
        for w in self._cards_inner.winfo_children():
            w.destroy()
        self._thumb_cache.clear()
        for preset in self._presets:
            self._make_card(preset)
        # "+" new-preset card
        add, inner = card(self._cards_inner, selected=False, width=220, height=280)
        add.pack(side="left", padx=14, pady=14)
        plus_lbl = tk.Label(inner, text="+", bg=BG3, fg=FG2,
                             font=("Segoe UI", 52))
        plus_lbl.place(relx=0.5, rely=0.42, anchor="center")
        hint_lbl = tk.Label(inner, text="New preset", bg=BG3, fg=FG2,
                             font=FONT_SM)
        hint_lbl.place(relx=0.5, rely=0.75, anchor="center")
        for w in (add, inner, plus_lbl, hint_lbl):
            w.bind("<Button-1>", lambda e: self._new_preset())

    def _make_card(self, preset: dict):
        from PIL import Image as _I, ImageTk as _ITk
        path = preset.get("template_path", "")
        name = preset.get("name", "Unnamed")
        selected = (name == self._preset_name.get())

        card_outer, card_inner = card(self._cards_inner, selected=selected,
                                      width=220, height=280)
        card_outer.pack(side="left", padx=14, pady=14)

        # Thumbnail canvas — sized generously so a non-square template
        # (e.g. a 70x105mm card gabarit) still reads clearly instead of
        # shrinking to a sliver inside a mostly-empty square.
        TH = 220
        tc = tk.Canvas(card_inner, width=TH, height=TH, bg=PREVIEW_BG,
                       highlightthickness=0)
        tc.pack(pady=(12, 0), padx=12)
        try:
            img = load_thumbnail(path, TH)
            w_, h_ = img.size
            thumb = _ITk.PhotoImage(img)
            self._thumb_cache[path] = thumb
            tc.create_image((TH - w_) // 2, (TH - h_) // 2,
                            anchor="nw", image=thumb)
        except Exception:
            tc.create_text(TH // 2, TH // 2, text="?", fill=FG2,
                           font=("Segoe UI", 24))

        # Name
        name_lbl = tk.Label(card_inner, text=name, bg=BG3, fg=FG, font=FONT_SM,
                             wraplength=160, justify="center")
        name_lbl.pack(padx=10, pady=(6, 2))

        # Remove button
        del_lbl = tk.Label(card_inner, text="✕  remove", bg=BG3, fg=FG3,
                           font=FONT_HINT, cursor="hand2")
        del_lbl.pack(pady=(0, 8))

        def _select(p=preset):
            self._apply_preset(p)

        def _remove(p=preset):
            self._presets = [x for x in self._presets
                             if x.get("name") != p.get("name")]
            self._save_presets()
            if self._preset_name.get() == p.get("name"):
                self._preset_name.set("")
                self._selected_tpl = {}
            self._refresh_cards()

        for w in (card_outer, card_inner, tc, name_lbl):
            w.bind("<Button-1>", lambda e, fn=_select: fn())
        del_lbl.bind("<Button-1>", lambda e, fn=_remove: fn())

    def _new_preset(self):
        """Start fresh: clear preset/template, go directly to Configure."""
        self._preset_name.set("")
        self._selected_tpl = {}
        self._update_step1_template_display()
        self._show_step(1)

    def _change_template(self):
        """Pick a new template image from disk (used inside step 1)."""
        path = filedialog.askopenfilename(
            parent=self,
            title="Select template image",
            filetypes=[("Images / vector", _TPL_PATTERNS),
                       ("All files", "*.*")])
        if not path:
            return
        path = os.path.normpath(path)
        name = os.path.splitext(os.path.basename(path))[0]
        self._selected_tpl = {"path": path, "name": name}
        self._update_step1_template_display()
        self._schedule_tile_preview()

    def _update_step1_template_display(self):
        """Refresh the template thumbnail + label inside step 1."""
        if not hasattr(self, "_step1_thumb_cv"):
            return
        path = self._selected_tpl.get("path", "")
        name = self._selected_tpl.get("name", "")
        self._step1_tpl_lbl.configure(text=name if name else _t("gmw.no_template"))
        self._step1_thumb_cv.delete("all")
        if path and os.path.isfile(path):
            try:
                from PIL import Image as _I, ImageTk as _ITk
                img = load_thumbnail(path, 44)
                w_, h_ = img.size
                thumb = _ITk.PhotoImage(img)
                self._step1_thumb_ref = thumb
                self._step1_thumb_cv.create_image(
                    (44 - w_) // 2, (44 - h_) // 2, anchor="nw", image=thumb)
            except Exception:
                self._step1_thumb_cv.create_text(22, 22, text="?", fill=FG2)

    def _change_template3(self):
        """Pick a separate background image for 3-digit (100+) cards."""
        path = filedialog.askopenfilename(
            parent=self,
            title="Select template image (100+)",
            filetypes=[("Images / vector", _TPL_PATTERNS),
                       ("All files", "*.*")])
        if not path:
            return
        path = os.path.normpath(path)
        name = os.path.splitext(os.path.basename(path))[0]
        self._selected_tpl3 = {"path": path, "name": name}
        self._update_step1_tpl3_display()
        self._schedule_tile_preview()

    def _clear_template3(self):
        """Remove the 3-digit template override (fall back to main template)."""
        self._selected_tpl3 = {}
        self._update_step1_tpl3_display()
        self._schedule_tile_preview()

    def _update_step1_tpl3_display(self):
        """Refresh the 3-digit template thumbnail + label inside step 1."""
        if not hasattr(self, "_step1_tpl3_thumb_cv"):
            return
        path = self._selected_tpl3.get("path", "")
        name = self._selected_tpl3.get("name", "")
        self._step1_tpl3_lbl.configure(
            text=name if name else _t("gmw.same_template"))
        self._step1_tpl3_thumb_cv.delete("all")
        if path and os.path.isfile(path):
            try:
                from PIL import Image as _I, ImageTk as _ITk
                img = load_thumbnail(path, 44)
                w_, h_ = img.size
                thumb = _ITk.PhotoImage(img)
                self._step1_tpl3_thumb_ref = thumb
                self._step1_tpl3_thumb_cv.create_image(
                    (44 - w_) // 2, (44 - h_) // 2, anchor="nw", image=thumb)
            except Exception:
                self._step1_tpl3_thumb_cv.create_text(22, 22, text="?",
                                                       fill=FG2)

    # ── Step 1 : Configure ────────────────────────────────────────────────
    def _build_step1(self):
        f = self._frames[1]
        f.columnconfigure(0, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        # Scrollable left panel. Width is DPI-scaled (see app/ui_kit._dpi_scale)
        # since it's a hand-tuned logical-pixel constant, same reasoning as
        # the window geometry fixes done for every wizard's Toplevel — on a
        # >100%-scaled display an unscaled fixed width renders visibly
        # narrower than intended, clipping labels/values/units.
        panel_w = round(380 * _dpi_scale(self))
        lp_outer = tk.Frame(f, bg=BG2, width=panel_w)
        lp_outer.grid(row=0, column=0, sticky="nsew")
        lp_outer.grid_propagate(False)
        lp_outer.rowconfigure(0, weight=1)
        lp_outer.columnconfigure(0, weight=1)
        _lp1_cv = tk.Canvas(lp_outer, bg=BG2, highlightthickness=0,
                            width=panel_w - 16)
        self._lp1_cv_ref = _lp1_cv
        _lp1_sb = scrollbar(lp_outer, orient="vertical", command=_lp1_cv.yview)
        _lp1_cv.configure(yscrollcommand=_lp1_sb.set)
        _lp1_sb.grid(row=0, column=1, sticky="ns")
        _lp1_cv.grid(row=0, column=0, sticky="nsew")
        lp = tk.Frame(_lp1_cv, bg=BG2)
        _lp1_win = _lp1_cv.create_window((0, 0), window=lp, anchor="nw")
        def _lp1_conf(e):
            _lp1_cv.configure(scrollregion=_lp1_cv.bbox("all"))
            _lp1_cv.itemconfig(_lp1_win, width=_lp1_cv.winfo_width())
        lp.bind("<Configure>", _lp1_conf)
        _lp1_cv.bind("<Configure>",
                     lambda e: _lp1_cv.itemconfig(_lp1_win, width=e.width))
        def _lp1_scroll(e):
            _lp1_cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
        for _w in (_lp1_cv, lp):
            _w.bind("<MouseWheel>", _lp1_scroll)

        def sec(txt):
            section_title(lp, txt).pack(anchor="w", padx=18, pady=(18, 6))

        def row(lbl, fn, lw=15):
            r = grid_row(lp, lbl, fn, label_width=lw)
            r.pack(fill="x", padx=18, pady=5)
            return r

        def spin(p, var, lo, hi, inc=1, w=7, fmt=None):
            kw = {"format": fmt} if fmt else {}
            tk.Spinbox(p, textvariable=var, from_=lo, to=hi, increment=inc,
                       width=w, bg=BG3, fg=FG, insertbackground=FG,
                       relief="flat", buttonbackground=BG3,
                       **kw).pack(side="left")

        def entry(p, var, w=18):
            tk.Entry(p, textvariable=var, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     width=w).pack(side="left", fill="x", expand=True)

        def swatch(p, var):
            ref = [None]
            def _pick():
                try:
                    ColorPickerDialog(
                        parent=self, init_color=var.get(),
                        callback=lambda c: (var.set(c),
                            ref[0].configure(bg=c) if ref[0] else None))
                except Exception:
                    pass
            # Wider than a bare colour dot + a visible border, so it reads
            # as "click me to change the colour" rather than a static swatch.
            b = tk.Button(p, bg=var.get(), width=4, relief="flat",
                          cursor="hand2", command=_pick,
                          highlightbackground=BORDER, highlightthickness=1)
            b.pack(side="left", padx=(6, 0))
            ref[0] = b
            var.trace_add("write", lambda *_: (
                b.configure(bg=var.get()) if b.winfo_exists() else None))

        # ── Primary settings: Numbers + Number style ─────────────────────
        # Most-used controls come first (range, auto-scale, size, colour,
        # kerning, tracking) — template/preset management moves below,
        # output/advanced move to the bottom, per the panel reorganisation.
        sec(_t("gmw.num_range"))
        nr = tk.Frame(lp, bg=BG2)
        nr.pack(fill="x", padx=18, pady=4)
        tk.Entry(nr, textvariable=self._num_range_str,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                 font=("Segoe UI", 11)).pack(side="left", fill="x", expand=True)
        tk.Label(lp, text="ex: 1-15, 77, 100 or 1-5, 1, 1, 55, 55",
                 bg=BG2, fg=FG3, font=FONT_HINT).pack(anchor="w", padx=18, pady=(2, 0))

        sec(_t("gmw.sec_sheet"))
        self._sheet_panel = SheetPanel(
            lp,
            make_range_items=self._make_range_items,
            make_seed_item=self._make_seed_item,
            make_label_items=self._make_label_items,
            get_design=self._sheet_design,
            families=sorted(_get_font_families().keys()),
            render_preview=self._render_item_preview,
            on_change=lambda: self._schedule_layout_preview(100))
        self._sheet_panel.pack(fill="x", padx=18, pady=(0, 4))

        sec(_t("gmw.sec_number"))

        def _nfont(p):
            all_fonts = sorted(_get_installed_fonts().keys())
            cb = ttk.Combobox(p, textvariable=self._num_font_display,
                              values=all_fonts, state="normal",
                              style="Custom.TCombobox", font=FONT_SM)
            cb.pack(side="left", fill="x", expand=True)

            def _on_font_select(e=None):
                flat = self._num_font_display.get()
                fams = _get_font_families()
                found = False
                for fam, styles in fams.items():
                    for sty in styles:
                        key = fam if sty in ('Regular', '') else f"{fam} {sty}"
                        if key == flat:
                            self._num_font_fam.set(fam)
                            self._num_font_sty.set(sty)
                            found = True
                            break
                    if found:
                        break
                if not found and flat in fams:
                    self._num_font_fam.set(flat)
                    self._num_font_sty.set("Regular")

            cb.bind("<<ComboboxSelected>>", _on_font_select)
            cb.bind("<Return>", _on_font_select)
            cb.bind("<FocusOut>", _on_font_select)

            def _filter_fonts(*_):
                q = self._num_font_display.get().lower()
                filtered = [n for n in all_fonts if q in n.lower()]
                cb["values"] = filtered if filtered else all_fonts
            cb.bind("<KeyRelease>", _filter_fonts)

            tk.Button(p, text="🔍", bg=BG3, fg=FG, relief="flat",
                      font=FONT_SM, cursor="hand2", width=3,
                      command=self._open_gmw_font).pack(side="left", padx=(4, 0))
        row(_t("gmw.num_font"), _nfont)

        def _nauto(p):
            tk.Checkbutton(p, text=_t("gmw.num_auto"),
                           variable=self._num_auto, bg=BG2, fg=FG,
                           selectcolor=BG3, activebackground=BG2,
                           font=FONT_SM,
                           command=self._toggle_num_spin).pack(side="left")
        row("", _nauto)

        def _nfsize(p):
            self._num_fsize_spin = tk.Spinbox(
                p, textvariable=self._num_fsize_pt, from_=4, to=999,
                width=6, bg=BG3, fg=FG, insertbackground=FG,
                relief="flat", buttonbackground=BG3, state="disabled")
            self._num_fsize_spin.pack(side="left")
            tk.Label(p, text="pt (1-2 digit)", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(6, 0))
        row(_t("gmw.num_fsize"), _nfsize)

        def _ncol(p):
            entry(p, self._num_color, 9)
            swatch(p, self._num_color)
        row(_t("gmw.num_color"), _ncol)

        def _nkern(p):
            cb = ttk.Combobox(p, textvariable=self._num_kern_mode,
                              values=["メトリクス", "オプティカル", "等幅"],
                              state="readonly", width=16,
                              style="Custom.TCombobox", font=FONT_SM)
            cb.pack(side="left")
        row(_t("gmw.kerning"), _nkern)

        def _ntrack(p):
            sp = tk.Spinbox(p, textvariable=self._num_tracking, from_=-200, to=200,
                            increment=1, width=7, bg=BG3, fg=FG,
                            insertbackground=FG, relief="flat",
                            buttonbackground=BG3)
            sp.pack(side="left")
            tk.Label(p, text="px", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(6, 0))
        row(_t("gmw.sub_tracking"), _ntrack)

        # ── Card size ──────────────────────────────────────────────────
        sec(_t("gmw.sec_card_size"))
        def _tsize(p):
            spin(p, self._tile_w, 1, 500, inc=0.5, w=7, fmt="%.1f")
            tk.Label(p, text="×", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=6)
            spin(p, self._tile_h, 1, 500, inc=0.5, w=7, fmt="%.1f")
            tk.Label(p, text="mm", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(6, 0))
            tk.Label(p, textvariable=self._tile_px_str, bg=BG2, fg=FG3,
                     font=FONT_HINT).pack(side="left", padx=(10, 0))
        row(_t("gmw.tile_size"), _tsize)

        # Lock checkbox: when on, font sizes scale proportionally on resize
        lock_row = tk.Frame(lp, bg=BG2)
        lock_row.pack(fill="x", padx=18, pady=(2, 4))
        tk.Checkbutton(lock_row, text=_t("gmw.lock_ratio"),
                       variable=self._num_lock, bg=BG2, fg=FG2,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_HINT).pack(side="left")

        def _pos_row(p, pct_var, pct_str, mm_str, mm_dim_var):
            sc = tk.Scale(p, variable=pct_var, from_=0, to=100,
                          orient="horizontal", bg=BG2, fg=FG, troughcolor=BG3,
                          highlightthickness=0, showvalue=False)
            sc.pack(side="left", fill="x", expand=True)
            _arrow_scale(sc)
            tk.Label(p, textvariable=pct_str, bg=BG2, fg=FG2,
                     font=FONT_SM, width=4, anchor="w").pack(
                         side="left", padx=(6, 0))
            # mm entry (editable)
            mm_ent = tk.Entry(p, bg=BG3, fg=FG, insertbackground=FG,
                              relief="flat", width=6, justify="right")
            mm_ent.pack(side="left", padx=(6, 0))
            tk.Label(p, text="mm", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(2, 0))
            # keep entry in sync when slider moves
            def _sync_entry(*_, _e=mm_ent, _v=pct_var, _d=mm_dim_var):
                val = round(_v.get() / 100 * _d.get(), 1)
                if not _e.focus_get() == _e:
                    _e.delete(0, "end")
                    _e.insert(0, str(val))
            pct_var.trace_add("write", _sync_entry)
            mm_dim_var.trace_add("write", _sync_entry)
            _sync_entry()
            # apply mm entry on Return / FocusOut
            def _apply(e=None, _v=pct_var, _e=mm_ent, _d=mm_dim_var):
                try:
                    mm_val = float(_e.get())
                    dim = _d.get()
                    if dim > 0:
                        _v.set(max(0, min(100, round(mm_val / dim * 100))))
                except ValueError:
                    pass
            mm_ent.bind("<Return>",   _apply)
            mm_ent.bind("<FocusOut>", _apply)

        row(_t("gmw.pos_x"),
            lambda p: _pos_row(p, self._num_pos_x, self._pos_x_pct_str,
                               self._pos_x_mm_str, self._tile_w))
        row(_t("gmw.pos_y"),
            lambda p: _pos_row(p, self._num_pos_y, self._pos_y_pct_str,
                               self._pos_y_mm_str, self._tile_h))

        # ── Template + Preset: compact secondary cards ────────────────
        sec(_t("gmw.sec_template"))
        tpl_card = tk.Frame(lp, bg=BG3, highlightbackground=BORDER,
                            highlightthickness=1)
        tpl_card.pack(fill="x", padx=18, pady=(2, 4))
        tpl_row = tk.Frame(tpl_card, bg=BG3)
        tpl_row.pack(fill="x", padx=10, pady=10)
        self._step1_thumb_cv = tk.Canvas(tpl_row, width=44, height=44,
                                          bg=PREVIEW_BG, highlightthickness=0)
        self._step1_thumb_cv.pack(side="left")
        self._step1_tpl_lbl = tk.Label(tpl_row, text=_t("gmw.no_template"),
                                        bg=BG3, fg=FG, font=FONT_SM,
                                        anchor="w", justify="left")
        self._step1_tpl_lbl.pack(side="left", fill="x", expand=True, padx=10)
        tk.Button(tpl_row, text=_t("gmw.change"), bg=BG2, fg=FG, relief="flat",
                  font=FONT_SM, cursor="hand2", padx=10,
                  highlightbackground=BORDER, highlightthickness=1,
                  command=self._change_template).pack(side="left")

        sec(_t("gmw.sec_preset"))
        pr_row = tk.Frame(lp, bg=BG2)
        pr_row.pack(fill="x", padx=18, pady=4)
        tk.Entry(pr_row, textvariable=self._preset_name, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_SM
                 ).pack(side="left", fill="x", expand=True, ipady=3)
        btn(pr_row, _t("gmw.save_preset"), self._save_preset_as,
            primary=True).pack(side="left", padx=(6, 0))
        change_lbl = tk.Label(lp, text=_t("gmw.change_preset"), bg=BG2, fg=ACCENT,
                              font=FONT_HINT, cursor="hand2")
        change_lbl.pack(anchor="w", padx=18, pady=(2, 4))
        change_lbl.bind("<Button-1>", lambda e: self._show_step(0))

        # ── 3-digit (100+) independent settings: secondary card ────────
        sec(_t("gmw.sec_3digit"))
        tpl3_card = tk.Frame(lp, bg=BG3, highlightbackground=BORDER,
                             highlightthickness=1)
        tpl3_card.pack(fill="x", padx=18, pady=(2, 8))

        tpl3_row = tk.Frame(tpl3_card, bg=BG3)
        tpl3_row.pack(fill="x", padx=10, pady=10)
        self._step1_tpl3_thumb_cv = tk.Canvas(tpl3_row, width=44, height=44,
                                               bg=PREVIEW_BG,
                                               highlightthickness=0)
        self._step1_tpl3_thumb_cv.pack(side="left")
        self._step1_tpl3_lbl = tk.Label(tpl3_row, text=_t("gmw.same_template"),
                                         bg=BG3, fg=FG, font=FONT_SM,
                                         anchor="w", justify="left")
        self._step1_tpl3_lbl.pack(side="left", fill="x", expand=True, padx=10)
        tk.Button(tpl3_row, text=_t("gmw.change"), bg=BG2, fg=FG, relief="flat",
                  font=FONT_SM, cursor="hand2", padx=10,
                  highlightbackground=BORDER, highlightthickness=1,
                  command=self._change_template3).pack(side="left")
        tk.Button(tpl3_row, text="✕", bg=BG2, fg=FG_RED, relief="flat",
                  font=FONT_SM, cursor="hand2", width=3,
                  highlightbackground=BORDER, highlightthickness=1,
                  command=self._clear_template3).pack(side="left", padx=(4, 0))

        tpl3_settings = tk.Frame(tpl3_card, bg=BG3)
        tpl3_settings.pack(fill="x", padx=10, pady=(0, 10))

        def row3(lbl, fn, lw=15):
            grid_row(tpl3_settings, lbl, fn, bg=BG3, label_width=lw).pack(
                fill="x", pady=3)

        def _nfsize3b(p):
            self._num_fsize3_spin = tk.Spinbox(
                p, textvariable=self._num_fsize3_pt, from_=4, to=999,
                width=6, bg=BG2, fg=FG, insertbackground=FG,
                relief="flat", buttonbackground=BG2, state="disabled")
            self._num_fsize3_spin.pack(side="left")
            tk.Label(p, text="pt", bg=BG3, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(6, 0))
        row3(_t("gmw.num_fsize"), _nfsize3b)

        def _nkern3(p):
            cb = ttk.Combobox(p, textvariable=self._num3_kern_mode,
                              values=["メトリクス", "オプティカル", "等幅"],
                              state="readonly", width=16,
                              style="Custom.TCombobox", font=FONT_SM)
            cb.pack(side="left")
        row3(_t("gmw.kerning"), _nkern3)

        def _ntrack3(p):
            sp = tk.Spinbox(p, textvariable=self._num3_tracking, from_=-200, to=200,
                            increment=1, width=7, bg=BG2, fg=FG,
                            insertbackground=FG, relief="flat",
                            buttonbackground=BG2)
            sp.pack(side="left")
            tk.Label(p, text="px", bg=BG3, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=(6, 0))
        row3(_t("gmw.sub_tracking"), _ntrack3)

        row3(_t("gmw.pos_x"),
             lambda p: _pos_row(p, self._num3_pos_x, self._pos3_x_pct_str,
                                self._pos3_x_mm_str, self._tile_w))
        row3(_t("gmw.pos_y"),
             lambda p: _pos_row(p, self._num3_pos_y, self._pos3_y_pct_str,
                                self._pos3_y_mm_str, self._tile_h))

        # ── Output ───────────────────────────────────────────────────────
        sec(_t("gmw.sec_output"))
        mn_row = tk.Frame(lp, bg=BG2)
        mn_row.pack(fill="x", padx=18, pady=4)
        tk.Checkbutton(mn_row, text=_t("gmw.monochrome"),
                       variable=self._monochrome, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")

        # ── Advanced (collapsible, closed by default) ──────────────────
        separator(lp).pack(fill="x", padx=18, pady=(14, 0))
        adv_header, adv_content, _adv_toggle = collapsible_section(
            lp, _t("gmw.advanced"), bg=BG2, start_open=False)
        adv_header.pack(fill="x", padx=16, pady=6)
        adv_content.pack(fill="x")

        def adv_row(lbl, fn, lw=15):
            grid_row(adv_content, lbl, fn, label_width=lw).pack(
                fill="x", padx=18, pady=4)

        adv_row(_t("gmw.dpi"),
                lambda p: spin(p, self._dpi, 72, 600, w=6))

        # ── Right panel : tile preview ────────────────────────────────
        rp = tk.Frame(f, bg=BG)
        rp.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        rp.rowconfigure(0, weight=1)
        rp.columnconfigure(0, weight=1)

        self._tile_canvas = tk.Canvas(rp, bg=BG3, highlightthickness=0)
        self._tile_canvas.grid(row=0, column=0, sticky="nsew")
        self._tile_canvas.bind(
            "<Configure>", lambda e: self._schedule_tile_preview(300))

        pn_row = tk.Frame(rp, bg=BG)
        pn_row.grid(row=1, column=0, pady=(8, 0), sticky="e")
        tk.Label(pn_row, text=_t("gmw.tile_preview_num"),
                 bg=BG, fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 6))
        tk.Spinbox(pn_row, textvariable=self._preview_num,
                   from_=1, to=9999, width=6, bg=BG3, fg=FG,
                   insertbackground=FG, relief="flat",
                   buttonbackground=BG3).pack(side="left")

    # ── Step 2 : Print ────────────────────────────────────────────────────
    def _build_step2(self):
        f = self._frames[2]
        f.columnconfigure(0, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        # Scrollable left panel so all controls are reachable at any height.
        # Width is DPI-scaled (see app/ui_kit._dpi_scale) — same reasoning
        # as Step 1's panel: an unscaled fixed width renders too narrow on a
        # >100%-scaled display, clipping labels/values/units.
        panel_w = round(340 * _dpi_scale(self))
        lp_outer = tk.Frame(f, bg=BG2, width=panel_w)
        lp_outer.grid(row=0, column=0, sticky="nsew")
        lp_outer.grid_propagate(False)
        lp_outer.rowconfigure(0, weight=1)
        lp_outer.columnconfigure(0, weight=1)
        _lp_cv = tk.Canvas(lp_outer, bg=BG2, highlightthickness=0,
                            width=panel_w - 16)
        self._lp_cv_ref = _lp_cv
        _lp_sb = scrollbar(lp_outer, orient="vertical",
                            command=_lp_cv.yview)
        _lp_cv.configure(yscrollcommand=_lp_sb.set)
        _lp_sb.grid(row=0, column=1, sticky="ns")
        _lp_cv.grid(row=0, column=0, sticky="nsew")
        lp = tk.Frame(_lp_cv, bg=BG2)
        _lp_win = _lp_cv.create_window((0, 0), window=lp, anchor="nw")
        def _lp_conf(e):
            _lp_cv.configure(scrollregion=_lp_cv.bbox("all"))
            _lp_cv.itemconfig(_lp_win, width=_lp_cv.winfo_width())
        lp.bind("<Configure>", _lp_conf)
        _lp_cv.bind("<Configure>",
                    lambda e: _lp_cv.itemconfig(_lp_win, width=e.width))
        def _lp_scroll(e):
            _lp_cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
        for _w in (_lp_cv, lp):
            _w.bind("<MouseWheel>", _lp_scroll)

        P = {"padx": 16, "pady": 5}

        def sec(txt):
            section_title(lp, txt).pack(anchor="w", padx=16, pady=(16, 5))

        def row(lbl, fn, lw=14):
            grid_row(lp, lbl, fn, label_width=lw).pack(fill="x", **P)

        def spin(p, var, lo, hi, inc=1, w=7, fmt=None):
            kw = {"format": fmt} if fmt else {}
            tk.Spinbox(p, textvariable=var, from_=lo, to=hi, increment=inc,
                       width=w, bg=BG3, fg=FG, insertbackground=FG,
                       relief="flat", buttonbackground=BG3,
                       **kw).pack(side="left")

        sec(_t("gmw.paper"))
        fmt_row = tk.Frame(lp, bg=BG2)
        fmt_row.pack(fill="x", padx=16, pady=(2, 4))
        for fk, fl in (
            (list(self.PAPER_MM)[0], "A3ノビ"),
            (list(self.PAPER_MM)[1], "A3"),
            (list(self.PAPER_MM)[2], "A4"),
        ):
            tk.Radiobutton(
                fmt_row, text=fl, variable=self._paper, value=fk,
                bg=BG3, fg=FG, selectcolor=ACCENT,
                font=("Segoe UI", 9, "bold"),
                indicatoron=0, padx=12, pady=5, cursor="hand2",
                relief="flat", overrelief="groove",
                activebackground=BG3, activeforeground=FG
            ).pack(side="left", padx=(0, 4))
        # mm dimension label — updates on paper/orient change
        self._paper_mm_lbl = tk.Label(lp, bg=BG2, fg=FG3, font=FONT_HINT)
        self._paper_mm_lbl.pack(anchor="w", padx=16, pady=(0, 4))
        def _upd_mm_lbl(*_):
            if not self.winfo_exists(): return
            pw, ph = self.PAPER_MM.get(self._paper.get(), (0, 0))
            if self._orient.get() == "portrait":
                pw, ph = min(pw, ph), max(pw, ph)
            else:
                pw, ph = max(pw, ph), min(pw, ph)
            self._paper_mm_lbl.configure(text=f"=> {pw} x {ph} mm")
        self._paper.trace_add("write", _upd_mm_lbl)
        self._orient.trace_add("write", _upd_mm_lbl)
        _upd_mm_lbl()

        def _orient_w(p):
            for val, key in (("landscape", "gmw.landscape"),
                              ("portrait",  "gmw.portrait")):
                tk.Radiobutton(p, text=_t(key), variable=self._orient,
                               value=val, bg=BG2, fg=FG, selectcolor=BG3,
                               activebackground=BG2,
                               font=FONT_SM).pack(side="left", padx=3)
        row(_t("gmw.orient"), _orient_w)

        def _mm(p, var):
            spin(p, var, 0, 100, inc=0.5, w=6, fmt="%.1f")
            tk.Label(p, text="mm", bg=BG2, fg=FG2,
                     font=FONT_SM).pack(side="left", padx=2)
        row(_t("gmw.margin"), lambda p: _mm(p, self._margin))
        row(_t("gmw.gap"),    lambda p: _mm(p, self._gap))
        row(_t("gmw.dpi"),    lambda p: spin(p, self._dpi, 72, 600, w=6))

        sec(_t("gmw.sec_cut_lines"))
        cut_row = tk.Frame(lp, bg=BG2)
        cut_row.pack(fill="x", padx=16, pady=4)
        tk.Checkbutton(cut_row, text=_t("gmw.cut_show"), variable=self._cut_line_show,
                       bg=BG2, fg=FG, selectcolor=BG3,
                       activebackground=BG2, font=FONT_SM,
                       command=lambda: self._schedule_layout_preview(100)
                       ).pack(side="left")
        tk.Spinbox(cut_row, textvariable=self._cut_line_pt,
                   from_=0.1, to=5.0, increment=0.1, width=5,
                   bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                   buttonbackground=BG3,
                   format="%.1f",
                   command=lambda: self._schedule_layout_preview(100)
                   ).pack(side="left", padx=(8, 0))
        tk.Label(cut_row, text="pt", bg=BG2, fg=FG2,
                 font=FONT_SM).pack(side="left", padx=(6, 0))

        sec(_t("gmw.out_folder"))

        def _out_w(p):
            tk.Entry(p, textvariable=self._out_folder, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat").pack(
                         side="left", fill="x", expand=True)
            tk.Button(p, text="📂", bg=BG3, fg=FG, relief="flat",
                      cursor="hand2",
                      command=self._pick_out_folder).pack(side="left",
                                                          padx=(6, 0))
        row(_t("gmw.out_folder"), _out_w)

        def _name_w(p):
            tk.Entry(p, textvariable=self._export_name, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat").pack(
                         side="left", fill="x", expand=True)
            tk.Label(p, text=".png / .pdf", bg=BG2, fg=FG3,
                     font=FONT_HINT).pack(side="left", padx=(6, 0))
        row(_t("gmw.file_name"), _name_w)

        def _pr_w(p):
            self._printer_cb = ttk.Combobox(
                p, textvariable=self._printer, state="readonly",
                style="Custom.TCombobox")
            self._printer_cb.pack(side="left", fill="x", expand=True)
        row(_t("gmw.printers"), _pr_w)

        mn = tk.Frame(lp, bg=BG2)
        mn.pack(fill="x", **P)
        tk.Checkbutton(mn, text=_t("gmw.monochrome"),
                       variable=self._monochrome, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")

        sec(_t("gmw.sec_svg"))
        def _svg_marks_w(p):
            for val, key in (("tombo", "gmw.svg_tombo"),
                             ("rect", "gmw.svg_rect"),
                             ("none", "gmw.svg_none")):
                tk.Radiobutton(p, text=_t(key), variable=self._svg_marks,
                               value=val, bg=BG2, fg=FG, selectcolor=BG3,
                               activebackground=BG2,
                               font=FONT_SM).pack(side="left", padx=3)
        row(_t("gmw.svg_marks"), _svg_marks_w)
        row(_t("gmw.svg_bleed"), lambda p: _mm(p, self._svg_bleed))
        row(_t("gmw.svg_arm"),   lambda p: _mm(p, self._svg_mark_len))
        cm = tk.Frame(lp, bg=BG2)
        cm.pack(fill="x", **P)
        tk.Checkbutton(cm, text=_t("gmw.svg_center"),
                       variable=self._svg_center, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")
        row(_t("gmw.svg_crop"),  lambda p: _mm(p, self._svg_border_crop))
        tk.Label(lp, text=_t("gmw.svg_crop_hint"), bg=BG2, fg=FG3,
                 font=FONT_HINT, wraplength=300, justify="left").pack(
                     anchor="w", padx=16)
        ol = tk.Frame(lp, bg=BG2)
        ol.pack(fill="x", **P)
        tk.Checkbutton(ol, text=_t("gmw.svg_outline"),
                       variable=self._svg_outline, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")
        one = tk.Frame(lp, bg=BG2)
        one.pack(fill="x", **P)
        tk.Checkbutton(one, text=_t("gmw.svg_single"),
                       variable=self._svg_single, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")
        ab = tk.Frame(lp, bg=BG2)
        ab.pack(fill="x", **P)
        tk.Checkbutton(ab, text=_t("gmw.svg_artboards"),
                       variable=self._svg_artboards, bg=BG2, fg=FG,
                       selectcolor=BG3, activebackground=BG2,
                       font=FONT_SM).pack(side="left")

        sec(_t("gmw.sec_export"))
        for txt, cmd in (
            (_t("gmw.export_png"), self._export_png),
            (_t("gmw.export_pdf"), self._export_pdf),
            (_t("gmw.export_svg"), self._export_svg),
            (_t("gmw.print"),      self._print_layout),
        ):
            tk.Button(lp, text=txt, bg=ACCENT, fg=FG_ON_ACCENT,
                      relief="flat", font=FONT_SM, cursor="hand2",
                      padx=10, pady=7, anchor="w",
                      command=cmd).pack(fill="x", padx=16, pady=4)

        # Right panel : layout preview
        rp = tk.Frame(f, bg=BG)
        rp.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        rp.rowconfigure(0, weight=1)
        rp.columnconfigure(0, weight=1)

        self._layout_canvas = tk.Canvas(rp, bg=BG3, highlightthickness=0)
        self._layout_canvas.grid(row=0, column=0, sticky="nsew")
        self._layout_canvas.bind(
            "<Configure>", lambda e: self._schedule_layout_preview(300))

        page_nav = tk.Frame(rp, bg=BG)
        page_nav.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self._page_prev_btn = tk.Button(
            page_nav, text="◄", bg=BG3, fg=FG, relief="flat",
            cursor="hand2", state="disabled", command=self._page_prev)
        self._page_prev_btn.pack(side="left")
        tk.Label(page_nav, textvariable=self._layout_page_str,
                 bg=BG, fg=FG, font=("Segoe UI", 9, "bold")).pack(
                     side="left")
        self._page_next_btn = tk.Button(
            page_nav, text="►", bg=BG3, fg=FG, relief="flat",
            cursor="hand2", state="disabled", command=self._page_next)
        self._page_next_btn.pack(side="left")
        tk.Label(page_nav, textvariable=self._layout_info,
                 bg=BG, fg=FG2, font=FONT_SM).pack(side="right")

    # ═════════════════════════════════════════════════════════════════════
    # Navigation
    # ═════════════════════════════════════════════════════════════════════
    def _show_step(self, n: int):
        self._step = n
        for i, f in enumerate(self._frames):
            if i == n:
                f.tkraise()
        self._step_bar.set_step(n)
        self._btn_back.configure(
            state="normal" if n > 0 else "disabled")
        if n < 2:
            self._btn_next.configure(
                text=_t("gmw.next") + " →", state="normal", bg=ACCENT)
        else:
            self._btn_next.configure(text="", state="disabled", bg=BG2)
        if n == 1:
            self._sheet_panel.set_designs(
                [p.get("name", "") for p in self._presets if p.get("name")])
            self._update_step1_template_display()
            self._schedule_tile_preview(300)
        elif n == 2:
            self._schedule_layout_preview(300)

    def _go_next(self):
        if self._step < 2:
            self._show_step(self._step + 1)

    def _go_back(self):
        if self._step > 0:
            self._show_step(self._step - 1)

    def _page_prev(self):
        idx = max(0, self._layout_page_idx.get() - 1)
        self._layout_page_idx.set(idx)
        self._show_layout_page()

    def _page_next(self):
        idx = min(len(self._layout_pages) - 1,
                  self._layout_page_idx.get() + 1)
        self._layout_page_idx.set(idx)
        self._show_layout_page()

    def _show_layout_page(self):
        if not self._layout_pages or not self.winfo_exists():
            return
        idx = max(0, min(self._layout_page_idx.get(),
                         len(self._layout_pages) - 1))
        cw = self._layout_canvas.winfo_width()
        ch = self._layout_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        from PIL import ImageTk as _ITk
        sheet = self._layout_pages[idx]
        sw, sh = sheet.size
        scale = min(cw / sw, ch / sh, 1.0)
        nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
        self._layout_tk = _ITk.PhotoImage(sheet.resize((nw, nh)))
        self._layout_canvas.delete("all")
        self._layout_canvas.create_image(
            (cw - nw) // 2, (ch - nh) // 2,
            anchor="nw", image=self._layout_tk)
        total = len(self._layout_pages)
        self._layout_page_str.set(f"  Page  {idx + 1} / {total}  ")
        if hasattr(self, "_page_prev_btn"):
            self._page_prev_btn.configure(
                state="normal" if idx > 0 else "disabled")
        if hasattr(self, "_page_next_btn"):
            self._page_next_btn.configure(
                state="normal" if idx < total - 1 else "disabled")

    # ═════════════════════════════════════════════════════════════════════
    # Toggles
    # ═════════════════════════════════════════════════════════════════════
    def _toggle_num_spin(self):
        if hasattr(self, "_num_fsize_spin"):
            st = "disabled" if self._num_auto.get() else "normal"
            self._num_fsize_spin.configure(state=st)
        if hasattr(self, "_num_fsize3_spin"):
            st = "disabled" if self._num_auto.get() else "normal"
            self._num_fsize3_spin.configure(state=st)
        self._schedule_tile_preview()

    # ═════════════════════════════════════════════════════════════════════
    # Pickers / printers
    # ═════════════════════════════════════════════════════════════════════
    def _pick_out_folder(self):
        d = filedialog.askdirectory(parent=self)
        if d:
            self._out_folder.set(d)

    def _load_printers_async(self):
        def _load():
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-Printer | Select-Object -ExpandProperty Name"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=(subprocess.CREATE_NO_WINDOW
                                   if hasattr(subprocess, "CREATE_NO_WINDOW")
                                   else 0))
                names = [ln.strip() for ln in result.stdout.splitlines()
                         if ln.strip()]
                self._printers = names
                if self.winfo_exists():
                    self.after(0, self._update_printer_cb)
            except Exception:
                pass
        threading.Thread(target=_load, daemon=True).start()

    def _update_printer_cb(self):
        if hasattr(self, "_printer_cb") and self._printers:
            self._printer_cb["values"] = self._printers
            if not self._printer.get():
                self._printer_cb.set(self._printers[0])

    # ═════════════════════════════════════════════════════════════════════
    # Font helpers
    # ═════════════════════════════════════════════════════════════════════
    def _open_gmw_font(self):
        """Open the FontPreviewDialog and apply the selected font."""
        dlg = FontPreviewDialog(self, current_font=self._num_font_fam.get())
        self.wait_window(dlg)
        if not dlg.result:
            return
        flat = dlg.result
        fams = _get_font_families()
        found = False
        for fam, styles in fams.items():
            for sty in styles:
                key = fam if sty in ('Regular', '') else f"{fam} {sty}"
                if key == flat:
                    self._num_font_fam.set(fam)
                    self._num_font_sty.set(sty)
                    found = True
                    break
            if found:
                break
        if not found and flat in fams:
            self._num_font_fam.set(flat)
            self._num_font_sty.set("Regular")
        self._update_font_display()

    def _update_font_display(self):
        fam = self._num_font_fam.get()
        sty = self._num_font_sty.get()
        display = fam if sty in ('Regular', '') else f"{fam} {sty}"
        self._num_font_display.set(display)

    def _font_path(self, family: str, style: str) -> str:
        _get_font_families()
        key = f"{family} {style}".strip()
        return _INSTALLED_FONTS.get(key) or _INSTALLED_FONTS.get(family, "")

    def _apply_k_convert(self, img):
        """Convert img to monochrome at the selected K% density."""
        return apply_k_convert(img, self._k_pct.get())

    def _render_tile(self, n: int, tw: int, th: int, dpi: int):
        return render_item(SheetItem(self._design(), [Segment(str(n))]),
                           tw, th, dpi, self._font_path)

    # ── Mixed sheet (several designs / composite labels on one sheet) ────
    def _design_name(self) -> str:
        return (self._preset_name.get().strip()
                or self._selected_tpl.get("name", "") or "design")

    def _sheet_design(self):
        """(Design, name) for NEW tiles: the preset picked in the sheet's
        selector, else the design currently being edited."""
        panel = getattr(self, "_sheet_panel", None)
        wanted = panel.chosen_design() if panel is not None else ""
        for preset in self._presets:
            if wanted and preset.get("name") == wanted:
                return Design.from_preset(preset), wanted
        return self._design(), self._design_name()

    def _make_range_items(self) -> list:
        """One sheet item per number of the Numbers field."""
        design, name = self._sheet_design()
        return [SheetItem(design, [Segment(str(n))], name)
                for n in self._parse_range()]

    def _make_seed_item(self) -> SheetItem:
        """Starting point for the composite editor: 'N' + small '-1'."""
        design, name = self._sheet_design()
        sub_pt = max(4, round(design.fsize_pt * 0.4))
        return SheetItem(design,
                         [Segment(str(self._parse_range()[0])),
                          Segment("-1", pt=sub_pt)], name)

    def _make_label_items(self, labels: list, current: bool = False,
                          main_pt=None, sub_pt=None) -> list:
        """Tiles for pasted labels; extra runs (e.g. '-1') get a smaller
        size (40% of the main one, same as the composite editor's seed).
        `current=True` forces the design being edited (step 1 preview)."""
        design, name = ((self._design(), self._design_name()) if current
                        else self._sheet_design())
        auto_sub = max(4, round((main_pt or design.fsize_pt) * 0.4))
        items = []
        for runs in labels:
            composite = len(runs) > 1
            items.append(SheetItem(
                design,
                [Segment(run, pt=(sub_pt or auto_sub) if i
                         else (main_pt if composite else None))
                 for i, run in enumerate(runs)], name))
        return items

    def _render_item_preview(self, item: SheetItem):
        tw = max(10, round(self._tile_w.get() / 25.4 * 150))
        th = max(10, round(self._tile_h.get() / 25.4 * 150))
        tile = render_item(item, tw, th, 150, self._font_path)
        if self._monochrome.get():
            tile = apply_k_convert(tile, self._k_pct.get())
        return tile

    # ═════════════════════════════════════════════════════════════════════
    # Number range parsing  — "1-15, 77, 100"  or  "1-5, 1, 1, 55, 55"
    # Numbers are kept IN ORDER and duplicates are allowed (same tile can
    # appear multiple times in the print layout).
    # ═════════════════════════════════════════════════════════════════════
    def _parse_range(self) -> list:
        result: list = []
        for part in self._num_range_str.get().replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part and not part.lstrip().startswith("-"):
                try:
                    a, b = part.split("-", 1)
                    a, b = int(a.strip()), int(b.strip())
                    result.extend(range(a, b + 1) if a <= b
                                  else range(a, b - 1, -1))
                except ValueError:
                    pass
            else:
                try:
                    result.append(int(part))
                except ValueError:
                    pass
        return result or [1]

    # ═════════════════════════════════════════════════════════════════════
    # Preview
    # ═════════════════════════════════════════════════════════════════════
    def _schedule_tile_preview(self, delay=450, *_):
        if self._sched_tile_id:
            self.after_cancel(self._sched_tile_id)
        self._sched_tile_id = self.after(delay, self._render_tile_preview)

    def _render_tile_preview(self):
        self._sched_tile_id = None
        if not self.winfo_exists() or self._step != 1:
            return
        cw = self._tile_canvas.winfo_width()
        ch = self._tile_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        labels = parse_labels(self._preview_num.get()) or [["1"]]
        item = self._make_label_items(labels[:1], current=True)[0]
        tw = max(10, round(self._tile_w.get() / 25.4 * 150))
        th = max(10, round(self._tile_h.get() / 25.4 * 150))
        tile = render_item(item, tw, th, 150, self._font_path)
        if self._monochrome.get():
            tile = self._apply_k_convert(tile)
        scale = min(cw / tw, ch / th, 1.0)
        nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
        from PIL import ImageTk as _ITk
        self._tile_tk = _ITk.PhotoImage(tile.resize((nw, nh)))
        self._tile_canvas.delete("all")
        self._tile_canvas.create_image(
            (cw - nw) // 2, (ch - nh) // 2,
            anchor="nw", image=self._tile_tk)

    def _schedule_layout_preview(self, delay=700, *_):
        if self._sched_layout_id:
            self.after_cancel(self._sched_layout_id)
        self._sched_layout_id = self.after(delay, self._render_layout_preview)

    def _render_layout_preview(self):
        self._sched_layout_id = None
        if not self.winfo_exists() or self._step != 2:
            return
        cw = self._layout_canvas.winfo_width()
        ch = self._layout_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        prev_dpi = max(36, min(72, self._dpi.get() // 4))
        pages = self._generate_layout(dpi_override=prev_dpi)
        self._layout_pages = pages
        idx = max(0, min(self._layout_page_idx.get(), len(pages) - 1))
        self._layout_page_idx.set(idx)
        self._layout_info.set(
            _t("gmw.layout_sum",
               placed=getattr(self, "_last_placed", 0),
               total=getattr(self,  "_last_total",  0),
               cols=getattr(self,   "_last_cols",   0),
               rows=getattr(self,   "_last_rows",   0)))
        self._show_layout_page()

    # ═════════════════════════════════════════════════════════════════════
    # Layout engine
    # ═════════════════════════════════════════════════════════════════════
    def _design(self) -> Design:
        """Current wizard settings as an engine Design."""
        return Design(
            template_path=self._selected_tpl.get("path", "")
            if self._selected_tpl else "",
            template3_path=self._selected_tpl3.get("path", "")
            if self._selected_tpl3 else "",
            font_family=self._num_font_fam.get(),
            font_style=self._num_font_sty.get(),
            auto_size=self._num_auto.get(),
            fsize_pt=self._num_fsize_pt.get(),
            fsize3_pt=self._num_fsize3_pt.get(),
            color=self._num_color.get(),
            pos_x=self._num_pos_x.get(), pos_y=self._num_pos_y.get(),
            tracking=self._num_tracking.get(),
            kern_mode=self._num_kern_mode.get(),
            pos3_x=self._num3_pos_x.get(), pos3_y=self._num3_pos_y.get(),
            tracking3=self._num3_tracking.get(),
            kern3_mode=self._num3_kern_mode.get())

    def _sheet_items(self) -> list:
        """Tiles to print: the mixed sheet if any, else the Numbers field."""
        panel = getattr(self, "_sheet_panel", None)
        if panel is not None and panel.items:
            return list(panel.items)
        return [SheetItem(self._design(), [Segment(str(n))])
                for n in self._parse_range()]

    def _generate_layout(self, dpi_override=None) -> list:
        dpi = dpi_override or max(72, self._dpi.get())
        grid = make_grid(self._paper.get(), self._orient.get(),
                         self._tile_w.get(), self._tile_h.get(),
                         self._margin.get(), self._gap.get())
        items = self._sheet_items()
        self._last_total  = len(items)
        self._last_placed = len(items)
        self._last_cols   = grid.cols
        self._last_rows   = grid.rows

        cut_lw = 0
        if self._cut_line_show.get():
            cut_lw = max(1, round(self._cut_line_pt.get() * dpi / 72))
            if dpi_override is not None:
                # Preview renders at low DPI — force minimum visible line
                # width so Show/Hide and thickness are clearly perceivable.
                cut_lw = max(3, cut_lw)

        mono = self._monochrome.get()
        k = self._k_pct.get()
        return render_pages(
            items, grid, dpi,
            lambda it, tw, th, d: render_item(
                it, tw, th, d, self._font_path),
            cut_lw=cut_lw,
            post=(lambda sh: apply_k_convert(sh, k)) if mono else None)

    # ═════════════════════════════════════════════════════════════════════
    # Export / Print
    # ═════════════════════════════════════════════════════════════════════
    def _check_ready(self) -> bool:
        panel = getattr(self, "_sheet_panel", None)
        if panel is not None and panel.items:
            return True   # every sheet item carries its own design
        if not self._selected_tpl or not os.path.isfile(
                self._selected_tpl.get("path", "")):
            messagebox.showwarning(
                _t("gmw.title"),
                "No template selected. Go back to step 1 and pick a template.",
                parent=self)
            return False
        return True

    def _get_out_dir(self) -> str:
        base = self._out_folder.get()
        if not base:
            base = filedialog.askdirectory(parent=self,
                                           title=_t("gmw.out_folder"))
        if base:
            os.makedirs(base, exist_ok=True)
        return base

    def _export_png(self):
        if not self._check_ready():
            return
        out_dir = self._get_out_dir()
        if not out_dir:
            return
        pages = self._generate_layout()
        if not pages:
            return
        base = os.path.splitext(self._export_name.get().strip() or "guidemark_layout")[0]
        dpi_val = max(72, self._dpi.get())
        for i, sheet in enumerate(pages):
            suffix = f"_p{i + 1}" if len(pages) > 1 else ""
            out_path = os.path.join(out_dir, f"{base}{suffix}.png")
            sheet.save(out_path, "PNG", dpi=(dpi_val, dpi_val))
        messagebox.showinfo(
            _t("gmw.title"),
            _t("gmw.done_png",
               path=os.path.join(out_dir, f"{base}.png")),
            parent=self)
        try:
            os.startfile(out_dir)
        except Exception:
            pass

    def _export_svg(self):
        """Illustrator-ready SVG: live text + cut marks on their own layers."""
        if not self._check_ready():
            return
        marks = self._svg_marks.get()
        bleed, arm = self._svg_bleed.get(), self._svg_mark_len.get()
        if marks == "tombo" and self._gap.get() < bleed + arm:
            if not messagebox.askyesno(
                    _t("gmw.title"),
                    _t("gmw.svg_gap_warn", gap=self._gap.get(),
                       need=bleed + arm), parent=self):
                return
        out_dir = self._get_out_dir()
        if not out_dir:
            return
        grid = make_grid(self._paper.get(), self._orient.get(),
                         self._tile_w.get(), self._tile_h.get(),
                         self._margin.get(), self._gap.get())
        dpi = max(72, self._dpi.get())
        opts = SvgOptions(
            marks=marks, bleed_mm=bleed, mark_len_mm=arm,
            center_marks=self._svg_center.get(),
            outline_text=self._svg_outline.get(),
            monochrome=self._monochrome.get(), k_pct=self._k_pct.get(),
            raster_dpi=dpi, layout_dpi=dpi,
            # the frame baked into a design is only hidden once cut marks
            # replace it
            border_crop_mm=(self._svg_border_crop.get()
                            if marks != "none" else 0.0))
        base = os.path.splitext(
            self._export_name.get().strip() or "guidemark_layout")[0]
        try:
            paths = export_svg_pages(self._sheet_items(), grid,
                                     self._font_path, opts,
                                     os.path.join(out_dir, base),
                                     single_file=self._svg_single.get(),
                                     with_artboard_script=(
                                         self._svg_artboards.get()))
        except Exception as e:
            log.exception("SVG export failed")
            messagebox.showerror(_t("gmw.title"), str(e), parent=self)
            return
        scripts = [p for p in paths if p.endswith(".jsx")]
        svgs = [p for p in paths if p.endswith(".svg")]
        msg = _t("gmw.done_svg", path=svgs[0], n=len(svgs))
        if scripts:
            msg += "\n\n" + _t("gmw.done_jsx",
                                 name=os.path.basename(scripts[0]))
        messagebox.showinfo(_t("gmw.title"), msg, parent=self)
        try:
            os.startfile(out_dir)
        except OSError:
            log.warning("cannot open %s", out_dir)

    def _export_pdf(self):
        if not self._check_ready():
            return
        out_dir = self._get_out_dir()
        if not out_dir:
            return
        pages = self._generate_layout()
        if not pages:
            return
        base = os.path.splitext(self._export_name.get().strip() or "guidemark_layout")[0]
        out_path = filedialog.asksaveasfilename(
            parent=self,
            title="Save PDF as",
            initialdir=out_dir,
            initialfile=f"{base}.pdf",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if not out_path:
            return
        # resolution=DPI encodes the physical page size in the PDF:
        # page_width_in = px_width / dpi  =>  mm = in * 25.4
        # Our pages are already generated at the correct DPI so the
        # resulting PDF page has the exact mm dimensions set in step 2.
        pages[0].save(
            out_path, "PDF",
            resolution=max(72, self._dpi.get()),
            save_all=True,
            append_images=pages[1:])
        messagebox.showinfo(_t("gmw.title"),
                            _t("gmw.done_pdf", path=out_path), parent=self)
        try:
            os.startfile(out_path)
        except Exception:
            pass

    def _print_layout(self):
        if not self._check_ready():
            return
        pages = self._generate_layout()
        if not pages:
            return
        import tempfile
        # Save as multi-page TIFF — Windows "Print Pictures" wizard
        # opens automatically with all print settings (printer, paper, orientation…)
        tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
        tmp.close()
        pages[0].save(tmp.name, "TIFF", save_all=True,
                      append_images=pages[1:], compression="lzw")
        try:
            os.startfile(tmp.name, "print")
        except Exception as e:
            messagebox.showerror("Print error", str(e), parent=self)

# =============================================================================
#  PDF TEXT EXTRACTOR  — helpers
# =============================================================================
def _ensure_pdf_deps() -> tuple:
    """Return (ok, message). Installs pdfplumber + python-docx if missing."""
    missing = []
    try:
        import pdfplumber as _p  # noqa: F401
    except ImportError:
        missing.append("pdfplumber")
    try:
        import docx as _d  # noqa: F401
    except ImportError:
        missing.append("python-docx")
    if not missing:
        return True, ""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + missing + ["-q"])
        return True, ""
    except Exception as e:
        return False, str(e)


def _pdf_extract_file(path: str, col_detect: bool,
                      page_nums: list | None) -> list:
    """Extract text from a PDF file. Returns list[list[dict]], one list of blocks per page."""
    import pdfplumber
    result = []
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        indices = ([i - 1 for i in page_nums if 1 <= i <= total]
                   if page_nums else list(range(total)))
        for idx in indices:
            page = pdf.pages[idx]
            result.append(_pdf_page_text(page, col_detect))
    return result


# CJK Unicode ranges: CJK Unified, Hangul, Hiragana, Katakana, CJK Compat, Kangxi…
_CJK_RANGES = (
    (0x1100, 0x11FF),   # Hangul Jamo
    (0x3000, 0x9FFF),   # CJK / Hiragana / Katakana / Bopomofo / Kangxi…
    (0xA000, 0xA4CF),   # Yi
    (0xAC00, 0xD7AF),   # Hangul syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFE30, 0xFE4F),   # CJK Compatibility Forms
    (0xFF00, 0xFFEF),   # Halfwidth / Fullwidth Forms
    (0x20000, 0x2A6DF), # CJK Extension B
)


def _is_cjk_char(c: str) -> bool:
    o = ord(c)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def _cjk_ratio(text: str) -> float:
    """Fraction of non-whitespace chars that are CJK."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _is_cjk_char(c)) / len(chars)


def _is_hangul_char(c: str) -> bool:
    o = ord(c)
    return (0xAC00 <= o <= 0xD7AF) or (0x1100 <= o <= 0x11FF)


def _hangul_ratio(text: str) -> float:
    """Fraction of non-whitespace chars that are Hangul (Korean)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _is_hangul_char(c)) / len(chars)


def _pick_preview_font() -> str:
    """Best available monospace font that covers CJK + Latin."""
    try:
        import tkinter.font as tkf
        avail = set(tkf.families())
    except Exception:
        avail = set()
    # Prefer fonts that cover CJK + monospace; fall back gracefully
    candidates = [
        "Noto Sans Mono",
        "Yu Gothic",        # Windows 10+ Japanese/CJK
        "Meiryo",           # Windows Vista+ Japanese
        "MS Gothic",        # Legacy Japanese
        "Malgun Gothic",    # Korean
        "SimSun",           # Chinese Simplified
        "MingLiU",          # Chinese Traditional
        "Courier New",
    ]
    for f in candidates:
        if f in avail:
            return f
    return "Courier New"


# ── Block helpers (used by extraction layer and exporters) ───────────────────

_LI_STARTERS = frozenset("\u30fb\u25cf\u25cb\u2666\u25a1\u25b2\u25ba\u2192\u2022\u203b\u25c7\u25b6\u25b8\xb7")


def _blocks_to_plain(blocks: list) -> str:
    """Flatten list[dict] blocks to a plain string (for QC / word count)."""
    parts = []
    for b in blocks:
        if b.get("type") == "sep":
            parts.append("\u2500\u2500 \u2500\u2500 \u2500\u2500")
        elif b.get("text", "").strip():
            parts.append(b["text"])
    return "\n\n".join(parts)


def _classify_block(text: str, is_cjk: bool) -> str:
    """
    Classify a reflowed paragraph as  h1 / h2 / li / body.
    Input is an already-isolated paragraph; short = heading is a safe rule.
    """
    import re
    stripped = text.strip()
    if not stripped:
        return "body"

    # ── List items (language-agnostic) ────────────────────────────────────────
    if stripped[0] in _LI_STARTERS:
        return "li"
    if re.match(r'^[-\*]\s', stripped):
        return "li"
    if re.match(r'^\d+[.\)]\s', stripped):
        return "li"
    if re.match(r'^\([0-9]+\)', stripped):
        return "li"
    if re.match(r'^[\u2460-\u2473]', stripped):   # ①-⑳
        return "li"

    if is_cjk:
        CJK_END = set("\u3002\uff1f\uff01\u2026\u300d\u300f\uff09)")
        # Explicit chapter / section markers ─────────────────────────────
        if re.match(r'^\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\d]+[\u7ae0\u90e8\u7bc0\u7de8\u7bc7\u8ab2]', stripped):
            return "h1"
        if re.match(r'^\uc81c\s*\d+\s*[\uc7a5\uc808\ud3b8]', stripped, re.IGNORECASE):
            return "h1"
        # Numbered section: １、 or 10、 (fullwidth or ASCII digits + 、)
        if re.match(r'^[\uff10-\uff19\d]+[\u3001\uff0c]', stripped):
            return "h2"
        if re.match(r'^[\uff08\(][\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+[\uff09\)]', stripped):
            return "h2"
        if re.match(r'^[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341][\u3001\.]', stripped):
            return "h2"
        if re.match(r'^[\uac00\ub098\ub2e4\ub77c\ub9c8\ubc14\uc0ac\uc544\uc790][.\uff09]', stripped):
            return "h2"
        # Short isolated CJK without sentence-end punctuation ─────────────
        cjk_c  = sum(1 for c in stripped if _is_cjk_char(c))
        if cjk_c >= len(stripped) * 0.35:
            has_end = bool(stripped) and stripped[-1] in CJK_END
            n = len(stripped)
            if n <= 8  and not has_end: return "h1"
            if n <= 22 and not has_end: return "h2"
    else:
        words_list = stripped.split()
        has_punct  = stripped[-1] in ".;,!?" if stripped else False
        if stripped.isupper() and 3 < len(stripped) < 80:
            return "h1"
        cap_w = sum(1 for w in words_list if w and w[0].isupper())
        if (len(words_list) <= 10 and len(stripped) < 80
                and not has_punct
                and cap_w >= len(words_list) * 0.6):
            return "h2"

    return "body"


def _block(text: str, is_cjk: bool) -> dict:
    return {"type": _classify_block(text, is_cjk), "text": text}


def _pdf_page_text(page, col_detect: bool) -> list:
    """Extract text from one pdfplumber page. Returns list[dict] of blocks."""
    words = page.extract_words(x_tolerance=3, y_tolerance=3,
                                keep_blank_chars=False)
    if words:
        sample = " ".join(w["text"] for w in words[:200])
        if _cjk_ratio(sample) >= 0.20:
            return _pdf_page_text_cjk(page)
        return _reflow_words(words, float(page.width), col_detect)

    try:
        raw = (page.extract_text() or "").strip()
        if _cjk_ratio(raw) >= 0.20:
            return _reflow_cjk_raw(raw)
        paragraphs, cur = [], []
        for ln in raw.splitlines():
            if ln.strip():
                cur.append(ln.strip())
            else:
                if cur:
                    paragraphs.append(" ".join(cur))
                    cur = []
        if cur:
            paragraphs.append(" ".join(cur))
        return [_block(p, False) for p in paragraphs if p.strip()]
    except Exception:
        return []


def _pdf_page_text_cjk(page) -> str:
    """
    CJK-aware extraction via character positions.
    Groups chars into lines by Y-proximity, then detects paragraph breaks.
    """
    try:
        chars = page.extract_chars(x_tolerance=1, y_tolerance=3)
    except Exception:
        chars = []
    if not chars:
        raw = page.extract_text() or ""
        return _reflow_cjk_raw(raw)

    # Group chars into line buckets by vertical proximity, then sort by x0.
    # Using a fixed bucket key avoids the drifting-average chaining bug
    # that would otherwise merge physically separate lines.
    Y_TOL = 4
    _buckets: dict = {}
    for ch in chars:
        bk = round(ch["top"] / Y_TOL)
        if bk not in _buckets:
            _buckets[bk] = []
        _buckets[bk].append(ch)
    lines: list = []   # list of (avg_top, text_str, x0_of_first_char)
    for _bk in sorted(_buckets):
        _bchars = sorted(_buckets[_bk], key=lambda c: c["x0"])
        txt = "".join(c["text"] for c in _bchars).strip()
        avg_top = sum(c["top"] for c in _bchars) / len(_bchars)
        if txt:
            lines.append((avg_top, txt, _bchars[0]["x0"]))

    return _reflow_cjk_lines(lines, float(page.width))


def _reflow_cjk_lines(lines: list, page_width: float) -> str:
    """
    Paragraph detection for CJK line tuples: (top, text, x0).
    Rules:
      1. Vertical gap > 1.5 × median → new paragraph
      2. Line starts with full-width space 　 or ASCII indent ≥10pt from left_base
      3. Previous line ends with CJK sentence-end punctuation (。？！…」』)
    """
    if not lines:
        return ""

    # Detect script: Korean needs ASCII sentence-end punctuation and space-joining.
    # Japanese/Chinese use full-width punctuation and need no inter-word space.
    import re as _re
    _sample = " ".join(txt for _, txt, _ in lines[:40])
    _is_korean = _hangul_ratio(_sample) >= 0.35

    # Korean sentences end with ASCII .?!  Japanese/Chinese with 。？！…
    # Parenthetical ) / ） are NOT sentence enders (already excluded).
    if _is_korean:
        CJK_ENDS = set("。？！…」』.?!")
        JOIN_SEP  = " "   # Korean words are space-separated
    else:
        CJK_ENDS = set("。？！…」』")
        JOIN_SEP  = ""    # Japanese/Chinese: no inter-character space

    # Full-width / ASCII numbered section starters: １、 or 1、 — always begin a new block.
    _SECTION_RE = _re.compile(r'^[\uff10-\uff19\d]+[\u3001\uff0c]')

    tops   = [t for t, _, _ in lines]
    # 5th-percentile left margin
    x0s = sorted(x for _, _, x in lines)
    left_base = x0s[max(0, len(x0s) // 20)]
    INDENT_MIN = 8.0

    raw_gaps = sorted(
        tops[i + 1] - tops[i]
        for i in range(len(tops) - 1)
        if tops[i + 1] > tops[i]
    )
    median_gap  = raw_gaps[len(raw_gaps) // 2] if raw_gaps else 14.0
    PARA_THRESH = median_gap * 1.50

    paragraphs: list = []
    cur:        list = []
    prev_top:   float | None = None
    prev_text   = ""

    for top, text, x0 in lines:
        if not text:
            continue
        is_break = False
        if prev_top is not None:
            gap = top - prev_top
            if gap > PARA_THRESH:
                is_break = True
            elif text.startswith("\u3000") or x0 > left_base + INDENT_MIN:
                # full-width space indent OR positional indent
                is_break = True
            elif prev_text and prev_text[-1] in CJK_ENDS:
                # previous line ended a sentence
                is_break = True
            elif _SECTION_RE.match(text):
                # current line starts a numbered section (１、 etc.) → new block
                is_break = True
        if is_break and cur:
            paragraphs.append(JOIN_SEP.join(cur))
            cur = []
        cur.append(text)
        # Section title line → flush it immediately as its own block
        # so the next line always starts a fresh paragraph.
        if _SECTION_RE.match(text):
            paragraphs.append(JOIN_SEP.join(cur))
            cur = []
        prev_top  = top
        prev_text = text

    if cur:
        paragraphs.append(JOIN_SEP.join(cur))

    return [_block(p, True) for p in paragraphs]


def _reflow_cjk_raw(raw: str) -> list:
    """
    Paragraph reflow for CJK text from plain extract_text().
    Consecutive non-empty lines with no indent/sentence-end → joined (no space).
    """
    import re as _re
    _is_korean = _hangul_ratio(raw) >= 0.35
    if _is_korean:
        CJK_ENDS = set("。？！…」』.?!")  # Korean uses ASCII punctuation
        JOIN_SEP  = " "
    else:
        CJK_ENDS = set("。？！…」』")      # parenthetical ) / ） excluded
        JOIN_SEP  = ""

    _SECTION_RE = _re.compile(r'^[\uff10-\uff19\d]+[\u3001\uff0c]')
    lines = [ln.rstrip() for ln in raw.splitlines()]

    paragraphs: list = []
    cur:        list = []
    prev = ""

    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if cur:
                paragraphs.append(JOIN_SEP.join(cur))
                cur = []
            prev = ""
            continue
        # Indent → new paragraph
        indent = len(ln) - len(ln.lstrip())
        if indent >= 2 and cur:
            paragraphs.append(JOIN_SEP.join(cur))
            cur = []
        # Sentence-end on previous line → flush before current line.
        elif prev and prev[-1] in CJK_ENDS and cur:
            paragraphs.append(JOIN_SEP.join(cur))
            cur = []
        # Numbered section start → new block
        elif _SECTION_RE.match(stripped) and cur:
            paragraphs.append(JOIN_SEP.join(cur))
            cur = []
        cur.append(stripped)
        # Flush section title immediately so next line starts fresh
        if _SECTION_RE.match(stripped):
            paragraphs.append(JOIN_SEP.join(cur))
            cur = []
        prev = stripped

    if cur:
        paragraphs.append(JOIN_SEP.join(cur))

    return [_block(p, True) for p in paragraphs if p]


def _reflow_words(words: list, page_width: float, col_detect: bool) -> list:
    """
    Reconstruct readable paragraphs from PDF word positions.

    1. Group words into visual lines by Y proximity.
    2. Compute median inter-line gap → derive paragraph-break threshold.
    3. Lines separated by > threshold → separate paragraph.
    4. Lines in the same paragraph → joined with a space (sentence reflow).
    5. Optional: detect two-column layout, process each side separately.
    """
    if not words:
        return []

    Y_TOL = 5           # pt — vertical tolerance for same-line grouping

    # ── Optional two-column detection ────────────────────────────────────────
    mid = None
    if col_detect and len(words) > 20:
        COL_GAP_MIN = page_width * 0.10
        xs = sorted(w["x1"] for w in words)
        max_gap, gap_mid = 0.0, 0.0
        for i in range(len(xs) - 1):
            g = xs[i + 1] - xs[i]
            if g > max_gap:
                max_gap = g
                gap_mid = (xs[i] + xs[i + 1]) / 2
        if max_gap >= COL_GAP_MIN:
            mid = gap_mid

    if mid is not None:
        left_bl  = _reflow_column([w for w in words if w["x1"] <= mid],  Y_TOL)
        right_bl = _reflow_column([w for w in words if w["x0"] >= mid], Y_TOL)
        if right_bl:
            return left_bl + [{"type": "sep", "text": "── ── ── ──"}] + right_bl
        return left_bl
    return _reflow_column(words, Y_TOL)


def _group_into_lines(words: list, y_tol: float) -> list:
    """Cluster words into visual lines. Returns list of (avg_top, [word,...])."""
    if not words:
        return []
    sorted_w = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list = []
    cur_top = sorted_w[0]["top"]
    cur: list = []
    for w in sorted_w:
        if abs(w["top"] - cur_top) <= y_tol:
            cur.append(w)
            # Keep a running average so a tall word doesn't drift the bucket
            cur_top = sum(x["top"] for x in cur) / len(cur)
        else:
            lines.append((cur_top, cur))
            cur = [w]
            cur_top = w["top"]
    if cur:
        lines.append((cur_top, cur))
    return lines


def _reflow_column(words: list, y_tol: float) -> str:
    """
    Group words → visual lines → reflow into paragraphs.

    Three independent signals each trigger a paragraph break:
      1. Vertical gap > 1.45 × median line spacing  (explicit spacing between paras)
      2. This line's x0 > left_base + INDENT_MIN    (first-line indent = new para)
      3. Previous line width < 65 % of typical width (previous line ended short = para ended)
    """
    lines = _group_into_lines(words, y_tol)
    if not lines:
        return ""

    # ── Page-level statistics ─────────────────────────────────────────────────
    all_x0 = [min(w["x0"] for w in ws) for _, ws in lines]
    all_x1 = [max(w["x1"] for w in ws) for _, ws in lines]

    sorted_x0 = sorted(all_x0)
    # Base left margin: 5th-percentile x0 (robust even if a few lines are flush-left)
    p5_idx    = max(0, len(sorted_x0) // 20)
    left_base = sorted_x0[p5_idx]
    INDENT_MIN = 10.0   # pt — x0 must exceed left_base+10 to be "indented"

    # Typical full-line width: 75th-percentile (ignores deliberately short last lines)
    widths    = sorted(x1 - x0 for x0, x1 in zip(all_x0, all_x1))
    typical_w = widths[int(len(widths) * 0.75)] if widths else 0.0
    SHORT_RATIO = 0.65  # previous line shorter than this → it ended a paragraph

    # ── Median inter-line gap ─────────────────────────────────────────────────
    tops = [t for t, _ in lines]
    raw_gaps = sorted(
        tops[i + 1] - tops[i]
        for i in range(len(tops) - 1)
        if tops[i + 1] > tops[i]
    )
    median_gap  = raw_gaps[len(raw_gaps) // 2] if raw_gaps else 14.0
    PARA_THRESH = median_gap * 1.45   # gap > this → paragraph break

    # ── Reflow ────────────────────────────────────────────────────────────────
    paragraphs: list = []
    cur_lines:  list = []
    prev_top:   float | None = None
    prev_width: float | None = None

    for top, ws in lines:
        line_text = " ".join(
            w["text"] for w in sorted(ws, key=lambda w: w["x0"])
        ).strip()
        if not line_text:
            continue

        x0_line = min(w["x0"] for w in ws)
        x1_line = max(w["x1"] for w in ws)
        lw      = x1_line - x0_line

        is_break = False
        if prev_top is not None:
            gap = top - prev_top
            if gap > PARA_THRESH:
                # Signal 1: large vertical gap
                is_break = True
            elif x0_line > left_base + INDENT_MIN:
                # Signal 2: first-line indent → new paragraph
                is_break = True
            elif (typical_w > 0 and prev_width is not None
                  and prev_width < typical_w * SHORT_RATIO):
                # Signal 3: previous line was short → paragraph ended there
                is_break = True

        if is_break and cur_lines:
            paragraphs.append(" ".join(cur_lines))
            cur_lines = []

        cur_lines.append(line_text)
        prev_top   = top
        prev_width = lw

    if cur_lines:
        paragraphs.append(" ".join(cur_lines))

    return [_block(p, False) for p in paragraphs]


_SEP_ENDS = frozenset("。？！…」』.?!")  # chars that mark a sentence end

import re as _re_nav
# Matches page-navigation artifacts embedded in the PDF:
#   （次ページへ） p. 3   /   (次ページへ)p.5   /   次ページへ   etc.
_NAV_RE = _re_nav.compile(
    r"^\s*[（(]?\s*次ページへ\s*[）)]?\s*(p\s*[.\uff0e]\s*\d+)?\s*$",
    _re_nav.IGNORECASE,
)


def _is_nav_block(b: dict) -> bool:
    """Return True for page-turn navigation markers that should be discarded."""
    return bool(_NAV_RE.match(b.get("text", "")))


def _flatten_pages_for_export(pages: list) -> list:
    """
    Flatten list-of-pages into a single ordered block stream.

    Paragraphs split across page boundaries are silently merged.
    No page-separator markers are emitted — the output flows naturally.
    """
    flat:  list       = []
    carry: dict | None = None   # incomplete body block from previous page

    for raw_blocks in pages:
        blocks = [b for b in raw_blocks if not _is_nav_block(b)]

        # ── Merge carry into first body block of this page ──────────────────
        if carry is not None:
            fb_idx = next(
                (j for j in range(len(blocks))
                 if blocks[j].get("type") == "body"),
                None)
            if fb_idx is not None:
                lt      = carry["text"].rstrip()
                ft      = blocks[fb_idx]["text"].lstrip()
                sample  = lt + ft
                is_cjk_ = _cjk_ratio(sample) >= 0.20
                joiner  = " " if _hangul_ratio(sample) >= 0.20 else ("" if is_cjk_ else " ")
                blocks[fb_idx] = _block(lt + joiner + ft, is_cjk_)
            # carry is consumed regardless (either merged or discarded if no body)
            carry = None

        # ── Detect incomplete last body block → carry to next page ───────────
        lb_idx = next(
            (j for j in range(len(blocks) - 1, -1, -1)
             if blocks[j].get("type") == "body"),
            None)
        if lb_idx is not None:
            last_text = blocks[lb_idx]["text"].rstrip()
            if last_text and last_text[-1] not in _SEP_ENDS:
                carry  = blocks[lb_idx]
                blocks = blocks[:lb_idx] + blocks[lb_idx + 1:]

        flat.extend(blocks)

    # Flush any remaining carry (document ends mid-sentence)
    if carry is not None:
        flat.append(carry)

    return flat


def _pdf_export_txt(pages: list, out_path: str):
    """
    pages: list[list[dict]]  (one list of blocks per page)
    Exports to UTF-8 BOM text with visual structure markers.
    """
    flat = _flatten_pages_for_export(pages)
    parts = []
    for b in flat:
        t    = b.get("type", "body")
        text = b.get("text", "").strip()
        if not text:
            continue
        if t == "h1":
            bar = "═" * min(max(len(text) + 4, 24), 64)
            parts.append(f"\n{bar}\n  {text}\n{bar}\n")
        elif t == "h2":
            parts.append(f"\n▌ {text}\n")
        elif t == "li":
            if not text or text[0] not in _LI_STARTERS:
                text = "• " + text
            parts.append("  " + text)
        elif t == "sep":
            parts.append(f"\n\n{text}\n\n")
        else:
            parts.append(text)
    with open(out_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(p if p.startswith("\n") else p for p in parts))


def _pdf_qc_check(pages: list) -> list:
    """
    Quality check — returns a list of dicts, one per page.
    pages: list[list[dict]]  (blocks format)
    """
    import unicodedata
    results = []
    for i, blocks in enumerate(pages):
        text   = (_blocks_to_plain(blocks)
                  if (blocks and isinstance(blocks[0], dict))
                  else (blocks if isinstance(blocks, str) else ""))
        issues = []
        non_ws = [c for c in text if not c.isspace()]
        chars  = len(non_ws)

        # Replacement / unknown characters
        bad = sum(1 for c in non_ws
                  if c in ("\ufffd", "\u25a1", "\u25a0")
                  or unicodedata.category(c) == "Cn")   # unassigned
        if bad:
            issues.append(f"{bad} car. non reconnu(s)")

        # Very low char count on a non-empty page
        if chars > 0 and chars < 20:
            issues.append("page quasi vide")

        # Repeated character runs (likely garbled OCR/encoding)
        import re as _re
        if _re.search(r"(.)\1{6,}", text):
            issues.append("répétitions suspectes")

        cjk_pct = _cjk_ratio(text) * 100
        results.append({
            "page":    i + 1,
            "chars":   chars,
            "bad":     bad,
            "cjk_pct": cjk_pct,
            "issues":  issues,
            "ok":      len(issues) == 0 and chars > 0,
        })
    return results


def _docx_set_fonts(run, latin: str, cjk: str, size_pt: float):
    """Set both Latin + East-Asian fonts on a run (python-docx only exposes Latin)."""
    from docx.oxml.ns import qn
    run.font.name = latin
    run.font.size_pt = size_pt  # will be set by caller; this is a no-op placeholder
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:eastAsia"), cjk)
    rFonts.set(qn("w:cs"),       cjk)


def _pdf_export_docx(pages: list, out_path: str, source_name: str = ""):
    """
    pages: list[list[dict]]  (one list of classified blocks per page)
    Exports a DOCX using Word Heading 1/2 styles for h1/h2,
    List Paragraph for li, Normal for body.  CJK fonts patched on every run.
    """
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_LINE_SPACING  # noqa: F401
    from docx.oxml.ns import qn

    flat     = _flatten_pages_for_export(pages)
    all_text = " ".join(b.get("text", "") for b in flat if b.get("type") not in ("page_sep", "sep"))
    is_cjk     = _cjk_ratio(all_text) >= 0.15
    LATIN_FONT = "Calibri"
    CJK_FONT   = "Yu Gothic" if is_cjk else "Calibri"
    BODY_PT    = 11.0

    doc = Document()
    sec = doc.sections[0]
    sec.top_margin    = Cm(2.5)
    sec.bottom_margin = Cm(2.5)
    sec.left_margin   = Cm(2.5)
    sec.right_margin  = Cm(2.5)

    GREY = RGBColor(0x99, 0x99, 0xbb)

    def _patch_cjk(run, size=BODY_PT, bold=False, color=None):
        run.font.name = LATIN_FONT
        run.font.size = Pt(size)
        run.bold      = bold
        if color:
            run.font.color.rgb = color
        rPr = run._r.get_or_add_rPr()
        rf  = rPr.get_or_add_rFonts()
        rf.set(qn("w:eastAsia"), CJK_FONT)
        rf.set(qn("w:cs"),       CJK_FONT)

    def _patch_style_cjk(style_name):
        try:
            st  = doc.styles[style_name]
            rPr = st.element.get_or_add_rPr()
            rf  = rPr.get_or_add_rFonts()
            rf.set(qn("w:eastAsia"), CJK_FONT)
            rf.set(qn("w:cs"),       CJK_FONT)
        except Exception:
            pass

    for sn in ("Normal", "Heading 1", "Heading 2", "List Paragraph"):
        _patch_style_cjk(sn)
    normal = doc.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal.font.size = Pt(BODY_PT)
    normal.paragraph_format.space_after  = Pt(8)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    normal.paragraph_format.line_spacing = 1.15

    if source_name:
        ph  = doc.add_paragraph()
        run = ph.add_run(source_name)
        _patch_cjk(run, size=8.5, bold=True, color=GREY)
        ph.paragraph_format.space_after = Pt(16)

    for b in flat:
        btype = b.get("type", "body")
        text  = b.get("text", "").strip()
        if not text:
            continue

        if btype == "h1":
            p   = doc.add_heading("", level=1)
            run = p.add_run(text)
            _patch_cjk(run, size=16.0, bold=True)
        elif btype == "h2":
            p   = doc.add_heading("", level=2)
            run = p.add_run(text)
            _patch_cjk(run, size=13.0, bold=True)
        elif btype == "li":
            p      = doc.add_paragraph(style="List Paragraph")
            prefix = "" if (text and text[0] in _LI_STARTERS) else "• "
            run    = p.add_run(prefix + text)
            _patch_cjk(run)
        elif btype == "sep":
            sp  = doc.add_paragraph()
            run = sp.add_run(text)
            _patch_cjk(run, size=8.0, color=GREY)
            sp.paragraph_format.space_before = Pt(6)
            sp.paragraph_format.space_after  = Pt(6)
        else:
            p   = doc.add_paragraph()
            run = p.add_run(text)
            _patch_cjk(run)

    doc.save(out_path)


def _parse_page_range_str(raw: str, total: int) -> list | None:
    """Parse '1-5, 8' → [1,3,4,5,8]. Returns None if raw is empty (=all)."""
    raw = raw.strip()
    if not raw:
        return None
    pages = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                pages.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                pages.add(int(part))
            except ValueError:
                pass
    return sorted(p for p in pages if 1 <= p <= total) or None


# =============================================================================
#  LINK REDIRECTOR  — wizard UI
# =============================================================================
_WORKER_JS = r'''/**
 * LINK REDIRECTOR — Cloudflare Worker
 * Routes:
 *   GET  /<code>             → redirect or "expired" page
 *   GET  /admin              → admin UI  (protected)
 *   POST /admin/create       → create link  (JSON)
 *   GET  /admin/links        → list links   (JSON)
 *   DELETE /admin/delete/<c> → delete link  (JSON)
 *
 * Env vars to set in Cloudflare dashboard:
 *   ADMIN_TOKEN   — secret token for the admin UI
 *   LINKS         — KV namespace binding (see wrangler.toml)
 */

export default {
  async fetch(request, env, ctx) {
    const url  = new URL(request.url);
    const path = url.pathname.replace(/\/$/, "") || "/";
    const method = request.method;

    if (path === "/admin") {
      const deny = checkAuth(request, env); if (deny) return deny;
      return serveAdminPage(request);
    }
    if (path === "/admin/create" && method === "POST") {
      const deny = checkAuth(request, env); if (deny) return deny;
      return handleCreate(request, env);
    }
    if (path === "/admin/links" && method === "GET") {
      const deny = checkAuth(request, env); if (deny) return deny;
      return handleList(env);
    }
    if (path.startsWith("/admin/delete/") && method === "DELETE") {
      const deny = checkAuth(request, env); if (deny) return deny;
      return handleDelete(path.slice("/admin/delete/".length), env);
    }

    const code = path.slice(1);
    if (!code || code.includes("/")) return new Response("Not found", { status: 404 });
    return handleRedirect(code, env, ctx);
  },
};

function checkAuth(request, env) {
  const token = env.ADMIN_TOKEN;
  if (!token) return null;
  const h = request.headers.get("Authorization") || "";
  if (h === `Bearer ${token}`) return null;
  const cookies = parseCookies(request.headers.get("Cookie") || "");
  if (cookies["admin_token"] === token) return null;
  if (new URL(request.url).searchParams.get("token") === token) return null;
  return new Response(loginPage(), { status: 401, headers: { "Content-Type": "text/html;charset=UTF-8" } });
}

async function handleRedirect(code, env, ctx) {
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(code)) return expiredPage("リンクが見つかりません。");
  const raw = await env.LINKS.get(code);
  if (!raw) return expiredPage("リンクが見つかりません。");
  let link; try { link = JSON.parse(raw); } catch { return expiredPage(); }
  if (link.expires && new Date(link.expires) < new Date()) return expiredPage("このリンクは有効期限切れです。");
  if (link.maxClicks != null && link.clicks >= link.maxClicks) return expiredPage("クリック数の上限に達しました。");
  link.clicks = (link.clicks || 0) + 1;
  ctx.waitUntil(env.LINKS.put(code, JSON.stringify(link)));
  return Response.redirect(link.url, 302);
}

async function handleCreate(request, env) {
  let body; try { body = await request.json(); } catch { return jsonError("JSON invalide.", 400); }
  const { url, expires, maxClicks, customCode } = body;
  if (!url || !isValidUrl(url)) return jsonError("URL invalide.", 400);
  const code = (customCode || "").trim() || nanoid(8);
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(code)) return jsonError("Code invalide.", 400);
  if (expires && isNaN(Date.parse(expires))) return jsonError("Date invalide.", 400);
  const mc = maxClicks ? parseInt(maxClicks, 10) : null;
  if (mc !== null && (isNaN(mc) || mc < 1)) return jsonError("Clics max invalide.", 400);
  const link = { url, created: new Date().toISOString(), expires: expires || null, maxClicks: mc, clicks: 0 };
  await env.LINKS.put(code, JSON.stringify(link));
  const origin = new URL(request.url).origin;
  return jsonOk({ code, shortUrl: `${origin}/${code}`, link });
}

async function handleList(env) {
  const list = await env.LINKS.list();
  const items = await Promise.all(list.keys.map(async ({ name }) => {
    const raw = await env.LINKS.get(name);
    try { return { code: name, ...JSON.parse(raw) }; } catch { return { code: name }; }
  }));
  return jsonOk(items);
}

async function handleDelete(code, env) {
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(code)) return jsonError("Code invalide.", 400);
  await env.LINKS.delete(code); return jsonOk({ deleted: code });
}

function isValidUrl(s) { try { const u = new URL(s); return u.protocol==="https:"||u.protocol==="http:"; } catch { return false; } }
function nanoid(len=8) { const c="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"; const b=new Uint8Array(len); crypto.getRandomValues(b); return Array.from(b,x=>c[x%c.length]).join(""); }
function parseCookies(h) { const o={}; for(const p of h.split(";")){ const[k,...vs]=p.trim().split("="); if(k) o[decodeURIComponent(k.trim())]=decodeURIComponent(vs.join("=").trim()); } return o; }
function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function jsonOk(d) { return new Response(JSON.stringify(d,null,2),{headers:{"Content-Type":"application/json"}}); }
function jsonError(m,s=400) { return new Response(JSON.stringify({error:m}),{status:s,headers:{"Content-Type":"application/json"}}); }

function expiredPage(msg="このリンクは無効です。") {
  return new Response(`<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><title>アクセス不可</title>
<style>body{min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0d0d0d;color:#c9c9c9;font-family:'Segoe UI',system-ui,sans-serif}
.card{text-align:center;padding:3rem 2.5rem;background:#171717;border-radius:18px;border:1px solid #262626;max-width:420px;width:90%}
.icon{font-size:3.5rem;margin-bottom:1.2rem}h1{font-size:1.4rem;color:#fff;margin-bottom:.7rem}p{color:#777;font-size:.93rem}</style>
</head><body><div class="card"><div class="icon">🔒</div><h1>アクセス拒否</h1><p>${esc(msg)}</p></div></body></html>`,
  {status:410,headers:{"Content-Type":"text/html;charset=UTF-8"}});
}

function loginPage() {
  return `<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"><title>Admin</title>
<style>body{min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0d0d0d;font-family:'Segoe UI',system-ui,sans-serif}
.card{background:#171717;border:1px solid #262626;border-radius:16px;padding:2.5rem;width:360px}
h1{color:#fff;font-size:1.3rem;margin-bottom:1.5rem;text-align:center}
input{width:100%;padding:.75rem 1rem;background:#0d0d0d;border:1px solid #2e2e2e;border-radius:8px;color:#e0e0e0;font-size:.95rem;margin-bottom:1rem;outline:none}
input:focus{border-color:#f77f00}
button{width:100%;padding:.8rem;background:#f77f00;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer;font-weight:600}</style>
</head><body><div class="card"><h1>🔗 Link Redirector</h1>
<form id="f"><input id="tok" type="password" placeholder="Token admin" required>
<button type="submit">Connexion</button></form>
<script>document.getElementById('f').addEventListener('submit',e=>{e.preventDefault();window.location.href='/admin?token='+encodeURIComponent(document.getElementById('tok').value);});</script>
</div></body></html>`;
}

function serveAdminPage(request) {
  const url=new URL(request.url); const qt=url.searchParams.get("token");
  const headers={"Content-Type":"text/html;charset=UTF-8"};
  if(qt) headers["Set-Cookie"]=`admin_token=${encodeURIComponent(qt)};Path=/;SameSite=Strict;Secure`;
  return new Response(adminPage(url.origin),{headers});
}

function adminPage(origin) { return `<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Link Redirector</title>
<style>*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0d0d;--bg2:#171717;--bg3:#1f1f1f;--bd:#2a2a2a;--acc:#f77f00;--fg:#e2e2e2;--fg2:#777;--red:#ff6b6b;--grn:#51cf66}
body{background:var(--bg);color:var(--fg);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:var(--bg2);border-bottom:1px solid var(--bd);padding:1rem 2rem;display:flex;align-items:center;gap:.75rem}
header h1{font-size:1.15rem;color:#fff}main{max-width:900px;margin:0 auto;padding:2rem 1.5rem}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:14px;padding:1.75rem;margin-bottom:2rem}
h2{font-size:1rem;color:#fff;margin-bottom:1.2rem}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:.85rem}.full{grid-column:1/-1}
label{font-size:.8rem;color:var(--fg2);display:block;margin-bottom:.35rem}
input{width:100%;padding:.65rem .9rem;background:var(--bg3);border:1px solid var(--bd);border-radius:8px;color:var(--fg);font-size:.9rem;outline:none}
input:focus{border-color:var(--acc)}
.btn{padding:.65rem 1.4rem;border:none;border-radius:8px;font-size:.9rem;font-weight:600;cursor:pointer}
.btn-primary{background:var(--acc);color:#fff}.btn-danger{background:transparent;color:var(--red);border:1px solid var(--red)}
#result{margin-top:1rem;padding:.9rem 1.1rem;border-radius:8px;font-size:.88rem;display:none}
#result.ok{background:#1a2e1a;border:1px solid #2d5a2d;color:var(--grn)}
#result.err{background:#2e1a1a;border:1px solid #5a2d2d;color:var(--red)}
.link{color:#fff;font-weight:600;cursor:pointer;text-decoration:underline}
table{width:100%;border-collapse:collapse;font-size:.85rem;margin-top:.5rem}
th{color:var(--fg2);text-align:left;padding:.5rem .75rem;font-weight:500;border-bottom:1px solid var(--bd)}
td{padding:.6rem .75rem;border-bottom:1px solid #1a1a1a;vertical-align:middle}
.short{color:var(--acc);cursor:pointer;font-weight:500}.dest{color:var(--fg2);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{display:inline-block;padding:.2rem .55rem;border-radius:20px;font-size:.75rem;font-weight:600}
.badge.ok{background:#1a2e1a;color:var(--grn)}.badge.exp{background:#2e1a1a;color:var(--red)}
.empty{color:var(--fg2);text-align:center;padding:2rem;font-size:.9rem}
.refresh{float:right;background:none;border:none;color:var(--fg2);cursor:pointer;font-size:.85rem}
</style></head><body>
<header><span>🔗</span><h1>Link Redirector</h1><span>— Admin</span></header>
<main>
<div class="card"><h2>✚ Créer un lien</h2><div class="grid">
<div class="full"><label>URL de destination *</label><input id="url" type="url" placeholder="https://ma-pwa.vercel.app"></div>
<div><label>Expiration (date &amp; heure)</label><input id="expires" type="datetime-local"></div>
<div><label>Clics max</label><input id="maxClicks" type="number" min="1" placeholder="illimité"></div>
<div><label>Code personnalisé</label><input id="customCode" type="text" placeholder="mon-projet-v2"></div>
<div style="display:flex;align-items:flex-end"><button class="btn btn-primary" onclick="createLink()">▶ Générer</button></div>
</div><div id="result"></div></div>
<div class="card"><h2>Liens existants<button class="refresh" onclick="loadLinks()">↺</button></h2>
<table><thead><tr><th>Code</th><th>Destination</th><th>Expiration</th><th>Clics</th><th>Statut</th><th></th></tr></thead>
<tbody id="links-body"><tr><td colspan="6" class="empty">Chargement…</td></tr></tbody></table></div>
</main>
<script>
const ORIGIN=${JSON.stringify(origin)};
function getToken(){const m=document.cookie.match(/(?:^|;)\\s*admin_token=([^;]+)/);return m?decodeURIComponent(m[1]):"";}
function ah(){return{"Authorization":"Bearer "+getToken(),"Content-Type":"application/json"};}
async function createLink(){
  const url=document.getElementById('url').value.trim();
  const expires=document.getElementById('expires').value;
  const maxClicks=document.getElementById('maxClicks').value;
  const customCode=document.getElementById('customCode').value.trim();
  const res=document.getElementById('result');res.style.display='none';
  if(!url){showResult('err','URL requise.');return;}
  try{
    const r=await fetch('/admin/create',{method:'POST',headers:ah(),body:JSON.stringify({url,expires:expires||null,maxClicks:maxClicks?parseInt(maxClicks):null,customCode:customCode||null})});
    const d=await r.json();
    if(!r.ok){showResult('err',d.error||'Erreur.');return;}
    showResult('ok',\`Lien : <span class="link" onclick="copy('\${d.shortUrl}')">\${d.shortUrl}</span> (cliquer pour copier)\`);
    loadLinks();
  }catch(e){showResult('err','Erreur réseau : '+e.message);}
}
async function loadLinks(){
  const tb=document.getElementById('links-body');
  tb.innerHTML='<tr><td colspan="6" class="empty">Chargement…</td></tr>';
  try{
    const r=await fetch('/admin/links',{headers:ah()});const d=await r.json();
    if(!r.ok||!Array.isArray(d)){tb.innerHTML='<tr><td colspan="6" class="empty">Erreur.</td></tr>';return;}
    if(!d.length){tb.innerHTML='<tr><td colspan="6" class="empty">Aucun lien.</td></tr>';return;}
    tb.innerHTML=d.map(l=>{
      const short=ORIGIN+'/'+esc(l.code);
      const exp=l.expires?new Date(l.expires).toLocaleString('fr-FR'):'∞';
      const expired=l.expires&&new Date(l.expires)<new Date();
      const maxed=l.maxClicks!=null&&l.clicks>=l.maxClicks;
      const ok=!expired&&!maxed;
      const cl=l.maxClicks!=null?\`\${l.clicks||0}/\${l.maxClicks}\`:\`\${l.clicks||0}\`;
      return \`<tr><td><span class="short" onclick="copy('\${short}')">\${esc(l.code)}</span></td>
      <td><span class="dest" title="\${esc(l.url||'')}">\${esc(l.url||'—')}</span></td>
      <td>\${exp}</td><td>\${cl}</td>
      <td><span class="badge \${ok?'ok':'exp'}">\${ok?'Actif':'Expiré'}</span></td>
      <td><button class="btn btn-danger" style="padding:.3rem .7rem;font-size:.78rem" onclick="del('\${esc(l.code)}',this)">✕</button></td></tr>\`;
    }).join('');
  }catch(e){tb.innerHTML='<tr><td colspan="6" class="empty">'+e.message+'</td></tr>';}
}
async function del(code,btn){
  if(!confirm(\`Supprimer "\${code}" ?\`))return;
  const row=btn.closest('tr');row.style.opacity='.4';
  const r=await fetch('/admin/delete/'+encodeURIComponent(code),{method:'DELETE',headers:ah()});
  if(r.ok)row.remove();else{row.style.opacity='1';alert('Erreur.');}
}
function showResult(t,h){const el=document.getElementById('result');el.className=t;el.innerHTML=h;el.style.display='block';}
function copy(t){navigator.clipboard.writeText(t).then(()=>alert('Copié : '+t));}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
loadLinks();
</script></body></html>`;
}
'''

_WRANGLER_TOML = '''name = "link-redirector"
main = "worker.js"
compatibility_date = "2024-01-01"

[[kv_namespaces]]
binding = "LINKS"
id      = "YOUR_KV_NAMESPACE_ID"

[vars]
# Set ADMIN_TOKEN as a secret instead:
#   wrangler secret put ADMIN_TOKEN
'''

_DEPLOY_STEPS = """How to deploy the Cloudflare Worker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Install Wrangler CLI
   npm install -g wrangler
   wrangler login

2. Create a KV namespace
   wrangler kv:namespace create LINKS
   → Copy the 'id' value into wrangler.toml

3. Set your admin token (never stored in plain text)
   wrangler secret put ADMIN_TOKEN
   → Enter your secret when prompted

4. Save worker.js + wrangler.toml in the same folder
   (use the "Save worker.js" button above)

5. Deploy
   wrangler deploy

6. Open the admin panel at:
   https://<your-worker>.workers.dev/admin?token=<your-token>
"""


class _DateTimePicker(tk.Toplevel):
    """Modal calendar + time picker. Read .result ('YYYY-MM-DDTHH:MM') after close."""

    def __init__(self, parent, initial=""):
        super().__init__(parent)
        self.title("Date & Time")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        import datetime as _dt, calendar as _cal
        now = _dt.datetime.now()
        try:
            dt = _dt.datetime.fromisoformat(initial) if initial else now
        except Exception:
            dt = now

        self._year   = tk.IntVar(value=dt.year)
        self._month  = tk.IntVar(value=dt.month)
        self._day    = tk.IntVar(value=dt.day)
        self._hour   = tk.IntVar(value=dt.hour)
        self._minute = tk.IntVar(value=dt.minute)
        self._sel_day = dt.day

        self._build()
        self._render_days()
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        _center_win(self)

    def _build(self):
        # ── Month / Year nav ─────────────────────────────────────────────────
        nav = tk.Frame(self, bg=BG2)
        nav.pack(fill="x")
        tk.Button(nav, text="◀", command=self._prev_month,
                  bg=BG2, fg=FG, relief="flat", font=FONT_SM,
                  cursor="hand2", activebackground=BG3, activeforeground=FG
                  ).pack(side="left", padx=8, pady=6)
        self._month_lbl = tk.Label(nav, text="", bg=BG2, fg=FG,
                                    font=FONT_BODY, width=18)
        self._month_lbl.pack(side="left", expand=True)
        tk.Button(nav, text="▶", command=self._next_month,
                  bg=BG2, fg=FG, relief="flat", font=FONT_SM,
                  cursor="hand2", activebackground=BG3, activeforeground=FG
                  ).pack(side="right", padx=8, pady=6)

        # ── Day-of-week headers ───────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(padx=10, pady=(4, 0))
        for d in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]:
            tk.Label(hdr, text=d, bg=BG, fg=FG2,
                     font=FONT_SM, width=3).pack(side="left", padx=2)

        # ── Day grid ──────────────────────────────────────────────────────────
        self._day_frame = tk.Frame(self, bg=BG)
        self._day_frame.pack(padx=10, pady=4)

        # ── Time ─────────────────────────────────────────────────────────────
        tf = tk.Frame(self, bg=BG2)
        tf.pack(fill="x")
        inner = tk.Frame(tf, bg=BG2)
        inner.pack(pady=8)
        tk.Label(inner, text="Heure :", bg=BG2, fg=FG2,
                 font=FONT_SM).pack(side="left", padx=(0, 6))
        tk.Spinbox(inner, textvariable=self._hour, from_=0, to=23,
                   width=3, format="%02.0f", bg=BG3, fg=FG,
                   buttonbackground=BG3, relief="flat",
                   font=FONT_SM).pack(side="left")
        tk.Label(inner, text=" : ", bg=BG2, fg=FG,
                 font=FONT_BODY).pack(side="left")
        tk.Spinbox(inner, textvariable=self._minute, from_=0, to=59,
                   width=3, format="%02.0f", bg=BG3, fg=FG,
                   buttonbackground=BG3, relief="flat",
                   font=FONT_SM).pack(side="left")

        # ── Buttons ───────────────────────────────────────────────────────────
        bf = tk.Frame(self, bg=BG)
        bf.pack(fill="x", padx=12, pady=10)
        btn(bf, "Annuler", self.destroy, primary=False).pack(side="right", padx=(4, 0))
        btn(bf, "OK", self._ok, primary=True).pack(side="right")

    def _render_days(self):
        import calendar as cal, datetime as dt
        for w in self._day_frame.winfo_children():
            w.destroy()
        y, m = self._year.get(), self._month.get()
        self._month_lbl.config(text=dt.date(y, m, 1).strftime("%B %Y"))
        for week in cal.monthcalendar(y, m):
            row = tk.Frame(self._day_frame, bg=BG)
            row.pack()
            for day in week:
                if day == 0:
                    tk.Label(row, text="", bg=BG, width=3,
                             font=FONT_SM).pack(side="left", padx=2, pady=2)
                else:
                    is_sel = (day == self._sel_day)
                    b = tk.Button(row, text=str(day), width=3,
                                  bg=ACCENT if is_sel else BG3, fg=FG,
                                  relief="flat", font=FONT_SM, cursor="hand2",
                                  activebackground=ACCENT2, activeforeground=FG,
                                  command=lambda d=day: self._pick(d))
                    b.pack(side="left", padx=2, pady=2)

    def _pick(self, day):
        self._sel_day = day
        self._day.set(day)
        self._render_days()

    def _prev_month(self):
        m, y = self._month.get(), self._year.get()
        m -= 1
        if m < 1:
            m, y = 12, y - 1
        self._month.set(m); self._year.set(y)
        self._render_days()

    def _next_month(self):
        m, y = self._month.get(), self._year.get()
        m += 1
        if m > 12:
            m, y = 1, y + 1
        self._month.set(m); self._year.set(y)
        self._render_days()

    def _ok(self):
        import calendar as cal
        y, m = self._year.get(), self._month.get()
        d = min(self._sel_day, cal.monthrange(y, m)[1])
        h, mn = self._hour.get(), self._minute.get()
        self.result = f"{y:04d}-{m:02d}-{d:02d}T{h:02d}:{mn:02d}"
        self.destroy()


class LinkRedirectorWizard(tk.Toplevel):
    """Manage expiring short links backed by a Cloudflare Worker."""
    WIN_W, WIN_H = 860, 580

    def __init__(self, parent):
        super().__init__(parent)
        self.title(_t("lrw.title"))
        self.configure(bg=BG)
        self.resizable(True, True)
        scaled_minsize(self, 700, 460)

        # Persisted settings in a small JSON sidecar
        self._cfg_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_lrw_config.json")
        self._cfg = self._load_cfg()

        self._worker_url = tk.StringVar(value=self._cfg.get("worker_url", ""))
        self._token      = tk.StringVar(value=self._cfg.get("token", ""))

        self._build()
        self.update_idletasks()
        pw = parent.winfo_rootx(); py = parent.winfo_rooty()
        pw2 = parent.winfo_width(); ph2 = parent.winfo_height()
        scaled_geometry(self, self.WIN_W, self.WIN_H)
        _center_win(self)

    # ── Config persistence ────────────────────────────────────────────────────
    def _load_cfg(self):
        try:
            with open(self._cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self, _btn=None):
        url = self._worker_url.get().strip().rstrip("/")
        if url and not url.startswith("http"):
            url = "https://" + url
        self._worker_url.set(url)
        self._cfg["worker_url"] = url
        self._cfg["token"]      = self._token.get().strip()
        try:
            with open(self._cfg_path, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, indent=2)
            if _btn:
                _btn.config(text="✔")
                self.after(1500, lambda: _btn.config(text="💾"))
        except Exception as e:
            if _btn:
                _btn.config(text="✗")
                self.after(1500, lambda: _btn.config(text="💾"))

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build(self):
        # ── Top: Worker URL + Token ───────────────────────────────────────────
        bar = tk.Frame(self, bg=BG2)
        bar.pack(fill="x", padx=0, pady=0)
        inner = tk.Frame(bar, bg=BG2)
        inner.pack(fill="x", padx=16, pady=10)

        tk.Label(inner, text=_t("lrw.worker_url"), bg=BG2, fg=FG2,
                 font=FONT_SM, width=14, anchor="e").pack(side="left")
        tk.Entry(inner, textvariable=self._worker_url, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=FONT_SM,
                 width=34).pack(side="left", padx=(4, 12))

        tk.Label(inner, text=_t("lrw.token"), bg=BG2, fg=FG2,
                 font=FONT_SM, width=10, anchor="e").pack(side="left")
        tk.Entry(inner, textvariable=self._token, bg=BG3, fg=FG,
                 show="•", insertbackground=FG, relief="flat",
                 font=FONT_SM, width=22).pack(side="left", padx=(4, 12))

        _sb = btn(inner, "💾", None, primary=False)
        _sb.config(command=lambda b=_sb: self._save_cfg(b))
        _sb.pack(side="left")

        tk.Frame(self, bg=BG3, height=1).pack(fill="x")

        # ── Notebook tabs ─────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=0, pady=0)

        tab_create = tk.Frame(nb, bg=BG)
        tab_links  = tk.Frame(nb, bg=BG)
        tab_deploy = tk.Frame(nb, bg=BG)

        nb.add(tab_create, text=f"  {_t('lrw.tab_create')}  ")
        nb.add(tab_links,  text=f"  {_t('lrw.tab_links')}  ")
        nb.add(tab_deploy, text=f"  {_t('lrw.tab_deploy')}  ")

        nb.bind("<<NotebookTabChanged>>",
                lambda e: self._on_tab(nb.index("current")))

        self._build_create(tab_create)
        self._build_links(tab_links)
        self._build_deploy(tab_deploy)

    def _on_tab(self, idx):
        if idx == 1:
            self._refresh_links()

    # ── Tab: Create ───────────────────────────────────────────────────────────
    def _build_create(self, p):
        def row(lbl, widget_fn, lw=16):
            r = tk.Frame(p, bg=BG)
            r.pack(fill="x", padx=24, pady=5)
            tk.Label(r, text=lbl, bg=BG, fg=FG2, font=FONT_SM,
                     width=lw, anchor="e").pack(side="left", padx=(0, 8))
            widget_fn(r)

        self._c_url = tk.StringVar()
        self._c_exp = tk.StringVar()
        self._c_max = tk.StringVar()
        self._c_cod = tk.StringVar()

        tk.Frame(p, bg=BG, height=16).pack()

        row(_t("lrw.url"), lambda r: tk.Entry(
            r, textvariable=self._c_url, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=FONT_SM, width=44
        ).pack(side="left"))

        def _expiry_widget(r):
            tk.Entry(r, textvariable=self._c_exp, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat", font=FONT_SM,
                     width=20).pack(side="left")
            def _open_picker():
                p = _DateTimePicker(self, self._c_exp.get())
                self.wait_window(p)
                if p.result:
                    self._c_exp.set(p.result)
            tk.Button(r, text="📅", command=_open_picker,
                      bg=BG3, fg=FG, relief="flat", font=FONT_SM,
                      cursor="hand2", activebackground=BG2,
                      activeforeground=FG, padx=6).pack(side="left", padx=(4, 0))
            tk.Button(r, text="✕", command=lambda: self._c_exp.set(""),
                      bg=BG3, fg=FG2, relief="flat", font=FONT_SM,
                      cursor="hand2", activebackground=BG2,
                      activeforeground=FG, padx=4).pack(side="left", padx=(2, 0))

        row(_t("lrw.expires"), _expiry_widget)

        row(_t("lrw.max_clicks"), lambda r: tk.Entry(
            r, textvariable=self._c_max, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=FONT_SM, width=10
        ).pack(side="left"))

        row(_t("lrw.custom"), lambda r: tk.Entry(
            r, textvariable=self._c_cod, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=FONT_SM, width=22
        ).pack(side="left"))

        tk.Frame(p, bg=BG, height=10).pack()
        af = tk.Frame(p, bg=BG)
        af.pack(fill="x", padx=24)
        btn(af, _t("lrw.generate"), self._create_link, primary=True).pack(side="left")

        # Result box
        self._c_result_var = tk.StringVar()
        self._c_result_frame = tk.Frame(p, bg=BG)
        self._c_result_frame.pack(fill="x", padx=24, pady=(16, 0))
        self._c_result_lbl = tk.Label(
            self._c_result_frame, textvariable=self._c_result_var,
            bg=_blend_hex(BG, FG_GREEN, 0.12), fg=FG_GREEN, relief="flat", font=FONT_SM,
            anchor="w", padx=12, pady=8, wraplength=700, justify="left")
        self._c_copy_btn = btn(self._c_result_frame, _t("lrw.copy"),
                               self._copy_result, primary=False)
        self._c_result_frame.pack_forget()

    def _create_link(self):
        url     = self._c_url.get().strip()
        expires = self._c_exp.get().strip()
        max_cl  = self._c_max.get().strip()
        custom  = self._c_cod.get().strip()

        if not url:
            messagebox.showwarning(_t("lrw.title"), "URL requise.", parent=self)
            return

        base  = self._worker_url.get().strip().rstrip("/")
        token = self._token.get().strip()
        if not base:
            messagebox.showwarning(_t("lrw.title"),
                                   "Worker URL non configurée.", parent=self)
            return

        payload = {"url": url}
        if expires:
            # Treat input as JST (UTC+9) → convert to UTC for the Worker
            import datetime as _dt
            try:
                naive = _dt.datetime.fromisoformat(expires)
                jst   = _dt.timezone(_dt.timedelta(hours=9))
                utc_str = naive.replace(tzinfo=jst).astimezone(
                    _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                payload["expires"] = utc_str
            except Exception:
                payload["expires"] = expires
        if max_cl:  payload["maxClicks"]  = int(max_cl)
        if custom:  payload["customCode"] = custom

        def _worker():
            try:
                import urllib.request, urllib.error, json as _json
                data = _json.dumps(payload).encode()
                req  = urllib.request.Request(
                    f"{base}/admin/create",
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                    },
                    method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        resp = _json.loads(r.read().decode())
                    self.after(0, lambda: self._on_create_ok(resp))
                except urllib.error.HTTPError as e:
                    body = e.read().decode(errors="replace")
                    self.after(0, lambda: self._on_create_err(
                        f"HTTP {e.code}: {body[:300]}"))
            except Exception as ex:
                self.after(0, lambda: self._on_create_err(str(ex)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_create_ok(self, resp):
        short = resp.get("shortUrl", "")
        self._last_short = short
        self._c_result_var.set(f"✓  {short}")
        self._c_result_lbl.config(bg=_blend_hex(BG, FG_GREEN, 0.12), fg=FG_GREEN)
        self._c_result_lbl.pack(side="left", fill="x", expand=True)
        self._c_copy_btn.pack(side="left", padx=(8, 0))
        self._c_result_frame.pack(fill="x", padx=24, pady=(16, 0))

    def _on_create_err(self, msg):
        self._last_short = ""
        self._c_result_var.set(f"✗  {msg}")
        self._c_result_lbl.config(bg=_blend_hex(BG, FG_RED, 0.12), fg=FG_RED)
        self._c_result_lbl.pack(side="left", fill="x", expand=True)
        self._c_copy_btn.pack_forget()
        self._c_result_frame.pack(fill="x", padx=24, pady=(16, 0))
        messagebox.showerror(_t("lrw.title"), msg, parent=self)

    def _copy_result(self):
        short = getattr(self, "_last_short", "")
        if short:
            self.clipboard_clear()
            self.clipboard_append(short)
            self._c_copy_btn.config(text=_t("lrw.copied"))
            self.after(2000, lambda: self._c_copy_btn.config(text=_t("lrw.copy")))

    # ── Tab: Links ────────────────────────────────────────────────────────────
    def _build_links(self, p):
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 6))
        btn(top, _t("lrw.refresh"), self._refresh_links, primary=False).pack(side="left")
        btn(top, _t("lrw.open_admin"), self._open_admin, primary=False).pack(side="left", padx=(8, 0))
        self._links_status = tk.Label(top, text="", bg=BG, fg=FG2, font=FONT_SM)
        self._links_status.pack(side="left", padx=12)

        # Table header
        hdr = tk.Frame(p, bg=BG2)
        hdr.pack(fill="x", padx=16, pady=(0, 2))
        for txt, w in [("Code", 12), ("Destination", 36), ("Expiration", 16),
                       ("Clics", 8), ("Statut", 8)]:
            tk.Label(hdr, text=txt, bg=BG2, fg=FG2, font=FONT_SM,
                     width=w, anchor="w").pack(side="left", padx=4, pady=4)

        # Scrollable rows
        sf = tk.Frame(p, bg=BG)
        sf.pack(fill="both", expand=True, padx=16)
        ysb = tk.Scrollbar(sf, orient="vertical", bg=BG2)
        self._links_cv = tk.Canvas(sf, bg=BG, highlightthickness=0,
                                   yscrollcommand=ysb.set)
        ysb.config(command=self._links_cv.yview)
        ysb.pack(side="right", fill="y")
        self._links_cv.pack(side="left", fill="both", expand=True)
        self._links_inner = tk.Frame(self._links_cv, bg=BG)
        self._links_win   = self._links_cv.create_window(
            (0, 0), window=self._links_inner, anchor="nw")
        self._links_inner.bind("<Configure>", lambda e: (
            self._links_cv.configure(
                scrollregion=self._links_cv.bbox("all")),
            self._links_cv.itemconfig(self._links_win,
                                      width=self._links_cv.winfo_width())))
        self._links_cv.bind("<Configure>", lambda e: self._links_cv.itemconfig(
            self._links_win, width=e.width))

    def _refresh_links(self):
        base  = self._worker_url.get().strip().rstrip("/")
        token = self._token.get().strip()
        if not base:
            self._links_status.config(text="Worker URL non configurée.", fg=FG_RED)
            return
        self._links_status.config(text="Chargement…", fg=FG2)

        def _worker():
            try:
                import urllib.request, json as _json
                req = urllib.request.Request(
                    f"{base}/admin/links",
                    headers={"Authorization": f"Bearer {token}",
                             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"},
                    method="GET")
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = _json.loads(r.read().decode())
                self.after(0, lambda d=data: self._populate_links(d))
            except Exception as ex:
                self.after(0, lambda: self._links_status.config(
                    text=f"Erreur : {ex}", fg=FG_RED))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate_links(self, data):
        for w in self._links_inner.winfo_children():
            w.destroy()

        if not data:
            tk.Label(self._links_inner, text=_t("lrw.no_links"),
                     bg=BG, fg=FG2, font=FONT_SM).pack(pady=20)
            self._links_status.config(text="", fg=FG2)
            return

        import datetime as _dt
        for link in data:
            code    = link.get("code", "?")
            url     = link.get("url", "—")
            expires = link.get("expires")
            clicks  = link.get("clicks", 0)
            max_cl  = link.get("maxClicks")
            expired = bool(expires and _dt.datetime.fromisoformat(
                expires.replace("Z", "+00:00")) < _dt.datetime.now(
                    _dt.timezone.utc))
            maxed   = max_cl is not None and clicks >= max_cl
            ok      = not expired and not maxed

            row = tk.Frame(self._links_inner, bg=BG3)
            row.pack(fill="x", pady=1)

            base_url = self._worker_url.get().strip().rstrip("/")
            short    = f"{base_url}/{code}"

            tk.Label(row, text=code, bg=BG3, fg=ACCENT, font=FONT_SM,
                     width=12, anchor="w", cursor="hand2").pack(
                side="left", padx=4, pady=5)
            tk.Label(row, text=url[:52] + ("…" if len(url) > 52 else ""),
                     bg=BG3, fg=FG2, font=FONT_SM,
                     width=36, anchor="w").pack(side="left", padx=4)
            exp_str = (expires[:16] if expires else "∞").replace("T", " ")
            tk.Label(row, text=exp_str, bg=BG3, fg=FG, font=FONT_SM,
                     width=16, anchor="w").pack(side="left", padx=4)
            cl_str = f"{clicks}/{max_cl}" if max_cl else str(clicks)
            tk.Label(row, text=cl_str, bg=BG3, fg=FG, font=FONT_SM,
                     width=8, anchor="w").pack(side="left", padx=4)
            status_fg = FG_GREEN if ok else FG_RED
            tk.Label(row, text="Actif" if ok else "Expiré",
                     bg=BG3, fg=status_fg, font=FONT_SM,
                     width=8, anchor="w").pack(side="left", padx=4)

            # Copy + Delete buttons
            def _copy(s=short):
                self.clipboard_clear(); self.clipboard_append(s)
            def _del(c=code):
                self._delete_link(c)

            btn(row, _t("lrw.copy"), _copy, primary=False).pack(
                side="right", padx=4, pady=3)
            btn(row, _t("lrw.delete"), _del, primary=False).pack(
                side="right", padx=(0, 4), pady=3)

        self._links_status.config(
            text=f"{len(data)} lien(s)", fg=FG2)
        self._links_cv.update_idletasks()
        self._links_cv.configure(
            scrollregion=self._links_cv.bbox("all"))

    def _delete_link(self, code):
        if not messagebox.askyesno(_t("lrw.title"),
                                   f"Supprimer « {code} » ?", parent=self):
            return
        base  = self._worker_url.get().strip().rstrip("/")
        token = self._token.get().strip()

        def _worker():
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{base}/admin/delete/{code}",
                    headers={"Authorization": f"Bearer {token}",
                             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"},
                    method="DELETE")
                urllib.request.urlopen(req, timeout=10)
                self.after(0, self._refresh_links)
            except Exception as ex:
                self.after(0, lambda: messagebox.showerror(
                    _t("lrw.title"), str(ex), parent=self))

        threading.Thread(target=_worker, daemon=True).start()

    def _open_admin(self):
        base  = self._worker_url.get().strip().rstrip("/")
        token = self._token.get().strip()
        if not base:
            messagebox.showwarning(_t("lrw.title"),
                                   "Worker URL non configurée.", parent=self)
            return
        import webbrowser
        url = f"{base}/admin"
        if token:
            import urllib.parse
            url += f"?token={urllib.parse.quote(token, safe='')}"
        webbrowser.open(url)

    # ── Tab: Deploy ───────────────────────────────────────────────────────────
    def _build_deploy(self, p):
        tk.Label(p, text=_t("lrw.deploy_title"), bg=BG, fg=FG,
                 font=FONT_H2).pack(anchor="w", padx=20, pady=(16, 6))

        # Steps text
        tf = tk.Frame(p, bg=BG)
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        ysb = tk.Scrollbar(tf, orient="vertical", bg=BG2)
        txt = tk.Text(tf, bg=BG3, fg=FG, font=FONT_MONO, relief="flat",
                      bd=6, wrap="word", yscrollcommand=ysb.set, state="normal")
        ysb.config(command=txt.yview)
        ysb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", _DEPLOY_STEPS)
        txt.config(state="disabled")

        # Buttons
        bf = tk.Frame(p, bg=BG)
        bf.pack(fill="x", padx=20, pady=(0, 14))
        btn(bf, _t("lrw.save_worker"), self._save_worker, primary=True).pack(side="left")

    def _save_worker(self):
        out_dir = filedialog.askdirectory(
            parent=self, title="Choisir le dossier de destination")
        if not out_dir:
            return
        worker_path   = os.path.join(out_dir, "worker.js")
        wrangler_path = os.path.join(out_dir, "wrangler.toml")
        try:
            with open(worker_path,   "w", encoding="utf-8") as f:
                f.write(_WORKER_JS)
            with open(wrangler_path, "w", encoding="utf-8") as f:
                f.write(_WRANGLER_TOML)
            messagebox.showinfo(_t("lrw.title"),
                                f"Fichiers enregistrés dans :\n{out_dir}",
                                parent=self)
            try: os.startfile(out_dir)
            except Exception: pass
        except Exception as ex:
            messagebox.showerror(_t("lrw.title"), str(ex), parent=self)


# =============================================================================
#  PDF TEXT EXTRACTOR  — wizard UI
# =============================================================================
class PDFExtractorWizard(tk.Toplevel):
    """Extract and export text from PDF files with layout preservation."""
    WIN_W, WIN_H = 1000, 680

    def __init__(self, parent):
        super().__init__(parent)
        self.title(_t("pdf.title"))
        self.configure(bg=BG)
        self.resizable(True, True)
        scaled_minsize(self, 760, 500)

        self._pdf_paths:  list = []
        self._extracted:  dict = {}   # path → list[str]  (one str per page)
        self._current_path   = ""
        self._total_pages    = 0
        self._cur_page       = tk.IntVar(value=1)
        self._out_dir        = tk.StringVar(value=str(DESKTOP))
        self._out_fmt        = tk.StringVar(value="docx")
        self._col_detect     = tk.BooleanVar(value=True)
        self._page_range_str = tk.StringVar(value="")

        self._build()
        self.update_idletasks()
        pw = parent.winfo_rootx(); py = parent.winfo_rooty()
        pw2 = parent.winfo_width();  ph2 = parent.winfo_height()
        scaled_geometry(self, self.WIN_W, self.WIN_H)
        _center_win(self)

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=5, sashrelief="flat", handlesize=0)
        pw.pack(fill="both", expand=True)

        left = tk.Frame(pw, bg=BG2, width=290)
        left.pack_propagate(False)
        pw.add(left, minsize=230)

        right = tk.Frame(pw, bg=BG)
        pw.add(right, minsize=420)

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, p):
        def sec(txt):
            tk.Label(p, text=txt, bg=BG2, fg=FG2,
                     font=("Segoe UI", 8, "bold")).pack(
                anchor="w", padx=14, pady=(12, 3))

        def row(lbl, fn, lw=13):
            r = tk.Frame(p, bg=BG2)
            r.pack(fill="x", padx=14, pady=3)
            if lbl:
                tk.Label(r, text=lbl, bg=BG2, fg=FG, font=FONT_SM,
                         width=lw, anchor="e").pack(side="left", padx=(0, 6))
            fn(r)

        # ── Files ────────────────────────────────────────────────────────
        sec(_t("pdf.files"))

        drop = tk.Frame(p, bg=BG3, cursor="hand2")
        drop.pack(fill="x", padx=12, pady=(0, 4))
        drop_lbl = tk.Label(drop, text=_t("pdf.add"), bg=BG3, fg=FG2,
                             font=FONT_SM, pady=12)
        drop_lbl.pack()
        for w in (drop, drop_lbl):
            w.bind("<Button-1>", lambda e: self._add_files())

        lf = tk.Frame(p, bg=BG2)
        lf.pack(fill="x", padx=12, pady=(0, 2))
        self._file_lb = tk.Listbox(lf, bg=BG3, fg=FG, font=FONT_SM,
                                   selectbackground=ACCENT, selectforeground=FG,
                                   relief="flat", bd=0, height=5,
                                   activestyle="none")
        self._file_lb.pack(fill="x")
        self._file_lb.bind("<<ListboxSelect>>", self._on_file_select)

        br = tk.Frame(p, bg=BG2)
        br.pack(fill="x", padx=12, pady=(0, 4))
        tk.Button(br, text=_t("pdf.remove"), command=self._remove_file,
                  bg=BG3, fg=FG2, relief="flat", font=FONT_SM,
                  cursor="hand2").pack(side="left")

        # ── Options ──────────────────────────────────────────────────────
        sec(_t("pdf.options"))

        def _fmt_row(r):
            for val, lbl in (("docx", "Word (.docx)"), ("txt", "Texte (.txt)")):
                tk.Radiobutton(r, text=lbl, variable=self._out_fmt, value=val,
                               bg=BG2, fg=FG, selectcolor=BG3,
                               activebackground=BG2,
                               font=FONT_SM).pack(side="left", padx=(0, 8))
        row(_t("pdf.format"), _fmt_row, lw=9)

        def _col_row(r):
            tk.Checkbutton(r, text=_t("pdf.cols"), variable=self._col_detect,
                           bg=BG2, fg=FG, selectcolor=BG3,
                           activebackground=BG2,
                           font=FONT_SM).pack(side="left")
        row("", _col_row, lw=0)

        def _pg_row(r):
            tk.Entry(r, textvariable=self._page_range_str, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     font=FONT_SM, width=14).pack(side="left")
            tk.Label(r, text=_t("pdf.pages_hint"), bg=BG2, fg=FG2,
                     font=("Segoe UI", 7)).pack(side="left", padx=4)
        row(_t("pdf.pages"), _pg_row, lw=9)

        def _dir_row(r):
            tk.Entry(r, textvariable=self._out_dir, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat",
                     font=FONT_SM, width=14).pack(side="left", fill="x",
                                                  expand=True)
            tk.Button(r, text="…", command=self._pick_dir,
                      bg=BG3, fg=FG, relief="flat", font=FONT_SM,
                      cursor="hand2").pack(side="left", padx=(4, 0))
        row(_t("pdf.outdir"), _dir_row, lw=9)

        # ── Actions ──────────────────────────────────────────────────────
        tk.Frame(p, bg=BG3, height=1).pack(fill="x", padx=12, pady=(14, 0))
        af = tk.Frame(p, bg=BG2)
        af.pack(fill="x", padx=12, pady=10)
        btn(af, _t("pdf.extract"), self._run_extract,
            primary=True).pack(fill="x", pady=(0, 6))
        btn(af, _t("pdf.export"), self._export,
            primary=False).pack(fill="x")

        self._status_lbl = tk.Label(p, text="", bg=BG2, fg=FG2,
                                    font=FONT_SM, wraplength=248,
                                    justify="left")
        self._status_lbl.pack(padx=14, pady=(6, 0), anchor="w")

    def _build_right(self, p):
        self._right_frame = p   # keep ref for QC panel
        nav = tk.Frame(p, bg=BG)
        nav.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(nav, text=_t("pdf.preview"), bg=BG, fg=FG2,
                 font=FONT_SM).pack(side="left")
        self._btn_prev = tk.Button(nav, text="◀", command=self._prev_page,
                                   bg=BG3, fg=FG, relief="flat",
                                   font=FONT_SM, cursor="hand2", padx=6)
        self._btn_prev.pack(side="left", padx=(8, 2))
        self._page_lbl = tk.Label(nav, text="—", bg=BG, fg=FG,
                                   font=FONT_SM, width=9)
        self._page_lbl.pack(side="left")
        self._btn_next = tk.Button(nav, text="▶", command=self._next_page,
                                   bg=BG3, fg=FG, relief="flat",
                                   font=FONT_SM, cursor="hand2", padx=6)
        self._btn_next.pack(side="left", padx=(2, 8))
        self._word_lbl = tk.Label(nav, text="", bg=BG, fg=FG2, font=FONT_SM)
        self._word_lbl.pack(side="right")

        tf = tk.Frame(p, bg=BG)
        tf.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        ysb = tk.Scrollbar(tf, orient="vertical",   bg=BG2)
        xsb = tk.Scrollbar(tf, orient="horizontal", bg=BG2)
        _preview_font = (_pick_preview_font(), 10)
        self._txt = tk.Text(tf, bg=BG3, fg=FG, insertbackground=FG,
                            font=_preview_font, relief="flat", bd=6,
                            wrap="none",
                            yscrollcommand=ysb.set,
                            xscrollcommand=xsb.set)
        ysb.config(command=self._txt.yview)
        xsb.config(command=self._txt.xview)
        ysb.pack(side="right",  fill="y")
        xsb.pack(side="bottom", fill="x")
        self._txt.pack(side="left", fill="both", expand=True)

    # ── File management ───────────────────────────────────────────────────────
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            parent=self, title="Sélectionner des PDF",
            filetypes=[("PDF", "*.pdf *.PDF"), ("All files", "*.*")])
        for p in paths:
            p = os.path.normpath(p)
            if p not in self._pdf_paths:
                self._pdf_paths.append(p)
                self._file_lb.insert("end", os.path.basename(p))

    def _remove_file(self):
        sel = self._file_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        path = self._pdf_paths.pop(idx)
        self._file_lb.delete(idx)
        self._extracted.pop(path, None)
        if self._current_path == path:
            self._current_path = ""
            self._clear_preview()

    def _on_file_select(self, _=None):
        sel = self._file_lb.curselection()
        if not sel:
            return
        self._current_path = self._pdf_paths[sel[0]]
        if self._current_path in self._extracted:
            self._cur_page.set(1)
            self._show_page()
        else:
            self._clear_preview()

    def _pick_dir(self):
        d = filedialog.askdirectory(parent=self,
                                    initialdir=self._out_dir.get())
        if d:
            self._out_dir.set(d)

    # ── Extraction ────────────────────────────────────────────────────────────
    def _run_extract(self):
        if not self._pdf_paths:
            messagebox.showwarning(_t("pdf.title"),
                                   "Aucun PDF sélectionné.", parent=self)
            return
        self._status_lbl.config(text=_t("pdf.installing"), fg=FG2)
        self.update_idletasks()

        def _worker():
            ok, err = _ensure_pdf_deps()
            if not ok:
                self.after(0, lambda: self._status_lbl.config(
                    text=f"Erreur install: {err}", fg=FG_RED))
                return
            results = {}
            self.after(0, lambda: self._status_lbl.config(
                text=_t("pdf.extracting"), fg=FG2))
            for path in self._pdf_paths:
                try:
                    results[path] = _pdf_extract_file(
                        path,
                        col_detect=self._col_detect.get(),
                        page_nums=None)
                except Exception as ex:
                    results[path] = [f"[ERREUR: {ex}]"]
            self.after(0, lambda r=results: self._on_done(r))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, results: dict):
        self._extracted = results
        if not self._current_path and self._pdf_paths:
            self._current_path = self._pdf_paths[0]
            self._file_lb.selection_clear(0, "end")
            self._file_lb.selection_set(0)
        self._cur_page.set(1)
        self._show_page()
        total_p = sum(len(v) for v in results.values())
        self._status_lbl.config(
            text=_t("pdf.done", n=len(results), p=total_p), fg=FG_GREEN)
        self._show_qc(results)

    # ── QC panel ──────────────────────────────────────────────────────────────
    def _show_qc(self, results: dict):
        """Build / rebuild the QC strip below the text preview."""
        # Remove old QC frame if present
        if hasattr(self, "_qc_frame") and self._qc_frame.winfo_exists():
            self._qc_frame.destroy()

        qf = tk.Frame(self._right_frame, bg=BG2)
        qf.pack(fill="x", padx=12, pady=(0, 10))
        self._qc_frame = qf

        tk.Label(qf, text="QC", bg=BG2, fg=FG2,
                 font=("Segoe UI", 7, "bold")).pack(side="left", padx=(8, 4))

        has_issue = False
        for path, pages in results.items():
            report = _pdf_qc_check(pages)
            fname  = os.path.basename(path)
            bad_pages = [r for r in report if not r["ok"]]
            ok_pages  = [r for r in report if r["ok"]]

            # Compact summary dot
            dot_color = FG_GREEN if not bad_pages else FG_RED
            dot_tip    = fname
            if bad_pages:
                has_issue = True
                dot_tip  += "  ⚠ p." + ", ".join(
                    str(r["page"]) for r in bad_pages[:8])

            dot = tk.Label(qf, text="●", bg=BG2, fg=dot_color,
                           font=("Segoe UI", 9), cursor="hand2")
            dot.pack(side="left", padx=2)

            # Hover tooltip
            def _enter(e, tip=dot_tip, lbl=dot):
                lbl.config(text=f"● {tip}")
            def _leave(e, lbl=dot):
                lbl.config(text="●")
            dot.bind("<Enter>", _enter)
            dot.bind("<Leave>", _leave)

            # Detailed issues: show as compact text
            if bad_pages:
                issues_txt = "  ".join(
                    f"p{r['page']}:{''.join(r['issues'])}" for r in bad_pages[:5]
                )
                tk.Label(qf, text=issues_txt, bg=BG2, fg=FG_RED,
                         font=("Segoe UI", 7)).pack(side="left", padx=(2, 8))

        if not has_issue:
            tk.Label(qf, text="✓ Aucun problème détecté", bg=BG2, fg=FG_GREEN,
                     font=("Segoe UI", 7)).pack(side="left", padx=4)

    # ── Preview ───────────────────────────────────────────────────────────────
    def _show_page(self):
        if not self._current_path or self._current_path not in self._extracted:
            self._clear_preview()
            return
        pages = self._extracted[self._current_path]
        self._total_pages = len(pages)
        p = max(1, min(self._cur_page.get(), self._total_pages))
        self._cur_page.set(p)
        self._page_lbl.config(text=f"{p} / {self._total_pages}")
        raw = pages[p - 1]
        # _pdf_extract_file stores list[dict] blocks; convert to plain string
        # for the preview widget.
        if isinstance(raw, list):
            text = _blocks_to_plain(raw)
        else:
            text = str(raw)
        self._txt.config(state="normal")
        self._txt.delete("1.0", "end")
        self._txt.insert("1.0", text)
        words = len(text.split())
        chars = len(text.replace("\n", "").replace(" ", ""))
        self._word_lbl.config(text=_t("pdf.words", w=words, c=chars))

    def _clear_preview(self):
        self._txt.config(state="normal")
        self._txt.delete("1.0", "end")
        self._page_lbl.config(text="—")
        self._word_lbl.config(text="")
        self._total_pages = 0

    def _prev_page(self):
        p = self._cur_page.get() - 1
        if p >= 1:
            self._cur_page.set(p)
            self._show_page()

    def _next_page(self):
        p = self._cur_page.get() + 1
        if p <= self._total_pages:
            self._cur_page.set(p)
            self._show_page()

    # ── Export ────────────────────────────────────────────────────────────────
    def _export(self):
        if not self._extracted:
            messagebox.showwarning(_t("pdf.title"),
                                   "Rien à exporter — lancez d'abord l'extraction.",
                                   parent=self)
            return
        out_dir = self._out_dir.get().strip()
        if not os.path.isdir(out_dir):
            messagebox.showerror(_t("pdf.title"),
                                 f"Dossier invalide :\n{out_dir}", parent=self)
            return
        fmt        = self._out_fmt.get()
        exported, errors = [], []
        for path, pages in self._extracted.items():
            wanted = _parse_page_range_str(self._page_range_str.get(),
                                           len(pages))
            sel = [pages[i - 1] for i in wanted] if wanted else pages
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                if fmt == "txt":
                    out = os.path.join(out_dir, f"{stem}.txt")
                    _pdf_export_txt(sel, out)
                else:
                    out = os.path.join(out_dir, f"{stem}.docx")
                    _pdf_export_docx(sel, out,
                                     source_name=os.path.basename(path))
                exported.append(out)
            except Exception as ex:
                errors.append(f"{stem}: {ex}")

        msg = f"✓  {len(exported)} fichier(s) exporté(s)."
        if errors:
            msg += "\n⚠ " + "\n".join(errors)
        fg = FG_GREEN if not errors else FG_RED
        self._status_lbl.config(text=msg, fg=fg)
        if exported:
            try:
                os.startfile(out_dir)
            except Exception:
                pass


# =============================================================================
#  HTML TEXT FORMATTER
# =============================================================================
class HtmlFormatterWizard(tk.Toplevel):
    """Text → HTML formatter — rapid tagging, auto-format, apply-to-all."""

    WIN_W, WIN_H = 1120, 700

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📝  Text / HTML Formatter")
        self.configure(bg=BG)
        self.resizable(True, True)
        scaled_minsize(self, 800, 500)

        self._last_tag = tk.StringVar(value="b")
        self._tag_buttons: dict = {}   # tag_name → tk.Button
        self._wbka_var = tk.BooleanVar(value=False)  # word-break: keep-all toggle
        self._ag_styles = self._load_ag_styles()

        self._build()
        self.update_idletasks()
        _center_win(self)

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # Accent bar
        tk.Frame(self, bg=ACCENT, height=3).pack(fill="x")

        # Header
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        label(hdr, "📝  Text / HTML Formatter", font=FONT_H1).pack(side="left")

        # Word / char counter (right of header)
        self._counter_lbl = label(hdr, "", fg=FG2, font=FONT_SM)
        self._counter_lbl.pack(side="right")

        separator(self).pack(fill="x", padx=20, pady=(0, 8))

        # ── Auto-format bar ───────────────────────────────────────────────────
        fmt_bar = styled_frame(self)
        fmt_bar.pack(fill="x", padx=20, pady=(0, 8))

        label(fmt_bar, "Auto-format :", fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 10))
        self._fmt_var = tk.StringVar(value="paragraphes")
        for val, lbl_txt in [
            ("paragraphes", "Paragraphes <p>"),
            ("br",          "Sauts de ligne <br>"),
            ("clean",       "Nettoyer (Word)"),
            ("none",        "Aucun (brut)"),
        ]:
            tk.Radiobutton(
                fmt_bar, text=lbl_txt, variable=self._fmt_var, value=val,
                bg=BG, fg=FG, selectcolor=BG3, activebackground=BG,
                activeforeground=FG, font=FONT_SM
            ).pack(side="left", padx=(0, 12))

        btn(fmt_bar, "▶  Convertir", self._auto_convert, primary=True
            ).pack(side="left", padx=(14, 0))
        btn(fmt_bar, "↩  Annuler", self._undo_output, primary=False
            ).pack(side="left", padx=(6, 0))

        # ── Audio Guide preset row ──────────────────────────────────────────────────
        ag_row = styled_frame(self)
        ag_row.pack(fill="x", padx=20, pady=(0, 8))
        label(ag_row, "🎥  Presets :", fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 10))

        # Audio guide button (toggle-style)
        self._ag_active_var = tk.BooleanVar(value=False)
        self._ag_btn = tk.Button(
            ag_row, text="🎧  Audio Guide (JP)",
            command=self._toggle_ag_preset,
            bg=BG3, fg=FG2, font=FONT_SM,
            relief="flat", bd=0, padx=12, pady=5, cursor="hand2")
        self._ag_btn.pack(side="left", padx=(0, 6))
        self._ag_btn.bind("<Enter>",
            lambda e: self._ag_btn.config(bg=ACCENT_LIGHT, fg=FG)
            if not self._ag_active_var.get() else None)
        self._ag_btn.bind("<Leave>",
            lambda e: self._ag_btn.config(
                bg=ACCENT if self._ag_active_var.get() else BG3,
                fg=FG     if self._ag_active_var.get() else FG2))

        btn(ag_row, "▶  Générer HTML", self._ag_run, primary=True
            ).pack(side="left", padx=(0, 6))
        btn(ag_row, "⚙  Configurer styles", self._ag_configure, primary=False
            ).pack(side="left", padx=(0, 6))
        btn(ag_row, "📂  Importer Excel…", self._ag_import_excel, primary=False
            ).pack(side="left")

        # ── Main split: Raw | HTML ────────────────────────────────────────────
        paned = tk.Frame(self, bg=BG)
        paned.pack(fill="both", expand=True, padx=20, pady=(0, 6))

        # Left — raw input
        left = styled_frame(paned, bg=BG2)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        in_hdr = styled_frame(left, bg=BG2)
        in_hdr.pack(fill="x", padx=8, pady=(6, 2))
        label(in_hdr, "Texte brut  (coller ici)", fg=FG2, font=FONT_SM, bg=BG2).pack(side="left")
        btn(in_hdr, "🗑 Vider", lambda: self._input.delete("1.0", "end"),
            primary=False).pack(side="right")

        in_frm = tk.Frame(left, bg=BG3)
        in_frm.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        isb  = ttk.Scrollbar(in_frm, orient="vertical")
        isb2 = ttk.Scrollbar(in_frm, orient="horizontal")
        self._input = tk.Text(
            in_frm, bg=BG3, fg=FG, insertbackground=FG,
            relief="flat", font=FONT_MONO, wrap="none", undo=True,
            yscrollcommand=isb.set, xscrollcommand=isb2.set)
        isb.config(command=self._input.yview)
        isb2.config(command=self._input.xview)
        isb.pack(side="right", fill="y")
        isb2.pack(side="bottom", fill="x")
        self._input.pack(fill="both", expand=True, padx=4, pady=4)
        self._input.bind("<KeyRelease>", lambda e: self._update_counter())

        # Right — HTML output
        right = styled_frame(paned, bg=BG2)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        out_hdr = styled_frame(right, bg=BG2)
        out_hdr.pack(fill="x", padx=8, pady=(6, 2))
        label(out_hdr, "HTML (sortie)", fg=FG2, font=FONT_SM, bg=BG2).pack(side="left")
        btn(out_hdr, "🗑 Vider", lambda: self._output.delete("1.0", "end"),
            primary=False).pack(side="right")

        out_frm = tk.Frame(right, bg=BG3)
        out_frm.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        osb  = ttk.Scrollbar(out_frm, orient="vertical")
        osb2 = ttk.Scrollbar(out_frm, orient="horizontal")
        self._output = tk.Text(
            out_frm, bg=BG3, fg=FG, insertbackground=FG,
            relief="flat", font=FONT_MONO, wrap="none", undo=True,
            yscrollcommand=osb.set, xscrollcommand=osb2.set)
        osb.config(command=self._output.yview)
        osb2.config(command=self._output.xview)
        osb.pack(side="right", fill="y")
        osb2.pack(side="bottom", fill="x")
        self._output.pack(fill="both", expand=True, padx=4, pady=4)
        self._output.bind("<KeyRelease>", lambda e: self._highlight_html())

        # Syntax-highlight colour tags — light theme (VSCode Light+ palette),
        # calibrated for a white/near-white Text background.
        self._output.tag_configure("hl_tag",     foreground="#0000ff")
        self._output.tag_configure("hl_attr",    foreground="#0451a5")
        self._output.tag_configure("hl_val",     foreground="#a31515")
        self._output.tag_configure("hl_comment", foreground="#008000",
                                   font=("Consolas", 9, "italic"))

        # ── Tag toolbar ───────────────────────────────────────────────────────
        separator(self).pack(fill="x", padx=20, pady=(0, 6))

        tb = styled_frame(self)
        tb.pack(fill="x", padx=20, pady=(0, 6))

        # Row 1 — inline tags
        row1 = styled_frame(tb)
        row1.pack(anchor="w", pady=(0, 4))
        label(row1, "Inline :", fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 8))
        for txt, tname in [("<b>","b"), ("<i>","i"), ("<u>","u"),
                            ("<em>","em"), ("<strong>","strong"),
                            ("<span>","span"), ("<a>","a"), ("<code>","code")]:
            self._make_tag_btn(row1, txt, tname)

        # Row 2 — block tags
        row2 = styled_frame(tb)
        row2.pack(anchor="w", pady=(0, 4))
        label(row2, "Bloc :   ", fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 8))
        for txt, tname in [("<h1>","h1"), ("<h2>","h2"), ("<h3>","h3"),
                            ("<p>","p"), ("<div>","div"), ("<li>","li"),
                            ("<ul>","ul"), ("<ol>","ol"), ("<section>","section")]:
            self._make_tag_btn(row2, txt, tname)

        # Row 3 — CSS styles
        row3 = styled_frame(tb)
        row3.pack(anchor="w", pady=(0, 4))
        label(row3, "CSS :    ", fg=FG2, font=FONT_SM).pack(side="left", padx=(0, 8))
        self._wbka_btn = tk.Button(
            row3, text="word-break: keep-all",
            command=self._toggle_wbka,
            bg=BG3, fg=FG2, font=("Consolas", 9),
            relief="flat", bd=0, padx=9, pady=4, cursor="hand2")
        self._wbka_btn.pack(side="left", padx=2)
        self._wbka_btn.bind("<Enter>", lambda e: self._wbka_btn.config(
            bg=ACCENT_LIGHT if not self._wbka_var.get() else ACCENT2, fg=FG))
        self._wbka_btn.bind("<Leave>", lambda e: self._wbka_btn.config(
            bg=ACCENT if self._wbka_var.get() else BG3,
            fg=FG if self._wbka_var.get() else FG2))
        btn(row3, "🏷  wrap sél.",  self._wbka_wrap_sel,  primary=False
            ).pack(side="left", padx=(10, 2))
        btn(row3, "🔁  appliquer sur tout", self._wbka_apply_all, primary=False
            ).pack(side="left", padx=2)
        btn(row3, "🗑  retirer partout", self._wbka_strip_all, primary=False
            ).pack(side="left", padx=2)

        # Row 4 — actions
        row4 = styled_frame(tb)
        row4.pack(anchor="w", pady=(0, 4))
        btn(row4, "🏷  Baliser la sélection",
            self._wrap_selection, primary=True).pack(side="left", padx=(0, 6))
        btn(row4, "🔁  Appliquer sur tout",
            self._apply_to_all,  primary=True).pack(side="left", padx=(0, 6))
        btn(row4, "📋  Copier HTML",
            self._copy_html,     primary=False).pack(side="left", padx=(0, 6))
        btn(row4, "🌐  Aperçu navigateur",
            self._preview_browser, primary=False).pack(side="left", padx=(0, 6))
        btn(row4, "🗑  Tout effacer",
            self._clear_all,     primary=False).pack(side="left")

        # Status bar
        self._status = label(self, "", fg=FG2, font=FONT_SM)
        self._status.pack(anchor="w", padx=20, pady=(0, 10))

    # ── Tag button helpers ────────────────────────────────────────────────────
    def _make_tag_btn(self, parent: tk.Frame, display: str, tag_name: str):
        b = tk.Button(
            parent, text=display,
            command=lambda t=tag_name: self._select_tag(t),
            bg=BG3, fg=FG2, font=("Consolas", 9),
            relief="flat", bd=0, padx=9, pady=4,
            activebackground=ACCENT, activeforeground=FG,
            cursor="hand2")
        b.pack(side="left", padx=2)
        self._tag_buttons[tag_name] = b

        def _enter(e, _b=b): _b.config(bg=ACCENT_LIGHT, fg=FG)
        def _leave(e, _b=b, t=tag_name):
            if self._last_tag.get() == t:
                _b.config(bg=ACCENT, fg=FG)
            else:
                _b.config(bg=BG3, fg=FG2)

        b.bind("<Enter>", _enter)
        b.bind("<Leave>", _leave)

    def _select_tag(self, tag_name: str):
        # Deselect old
        old = self._last_tag.get()
        if old in self._tag_buttons:
            self._tag_buttons[old].config(bg=BG3, fg=FG2)
        # Select new
        self._last_tag.set(tag_name)
        if tag_name in self._tag_buttons:
            self._tag_buttons[tag_name].config(bg=ACCENT, fg=FG)

    # ── Auto-conversion ───────────────────────────────────────────────────────
    def _auto_convert(self):
        raw = self._input.get("1.0", "end-1c")
        if not raw.strip():
            self._flash("⚠  Collez du texte dans la zone de gauche.", FG_YELLOW)
            return
        mode = self._fmt_var.get()
        try:
            html = self._convert(raw, mode)
        except Exception as ex:
            import traceback
            messagebox.showerror("Erreur de conversion",
                                 traceback.format_exc(), parent=self)
            return
        self._output.delete("1.0", "end")
        self._output.insert("1.0", html)
        self._highlight_html()
        self._flash(f"✓  Converti ({mode}).", FG_GREEN)

    def _convert(self, raw: str, mode: str) -> str:
        if mode in ("clean", "paragraphes"):
            if mode == "clean":
                raw = self._clean_word(raw)
            paragraphs = re.split(r"\n\s*\n", raw)
            parts = []
            for p in paragraphs:
                p = p.strip()
                if p:
                    p = p.replace("\n", "<br>\n")
                    parts.append(f"<p>{p}</p>")
            return "\n".join(parts)
        elif mode == "br":
            return raw.replace("\n", "<br>\n")
        elif mode == "audio_guide":
            return self._convert_audio_guide(raw)
        else:  # none
            return raw

    @staticmethod
    def _clean_word(text: str) -> str:
        """Strip common Word/Office artefacts."""
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u2014", "--").replace("\u2013", "-")
        text = text.replace("\u2026", "...").replace("\u00a0", " ")
        text = re.sub(r"[^\S\n]+", " ", text)
        text = "\n".join(line.rstrip() for line in text.splitlines())
        return text

    def _undo_output(self):
        try:
            self._output.edit_undo()
        except tk.TclError:
            pass

    # ── Tag wrapping ──────────────────────────────────────────────────────────
    def _wrap_selection(self):
        """Wrap the current selection in the OUTPUT area with the active tag."""
        tag = self._last_tag.get()
        try:
            sel = self._output.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            self._flash("⚠  Sélectionnez du texte dans la zone HTML.", FG_YELLOW)
            return
        wrapped = self._build_tag(tag, sel)
        if wrapped is None:
            return   # user cancelled href dialog
        self._output.delete(tk.SEL_FIRST, tk.SEL_LAST)
        self._output.insert(tk.INSERT, wrapped)
        self._highlight_html()
        self._flash(f"✓  <{tag}> appliqué à la sélection.", FG_GREEN)

    def _apply_to_all(self):
        """Wrap EVERY plain occurrence of the selection with the active tag."""
        tag = self._last_tag.get()
        try:
            sel = self._output.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            self._flash("⚠  Sélectionnez le texte modèle dans la zone HTML.", FG_YELLOW)
            return
        if not sel.strip():
            self._flash("⚠  La sélection est vide.", FG_YELLOW)
            return

        wrapped = self._build_tag(tag, sel)
        if wrapped is None:
            return   # user cancelled

        html = self._output.get("1.0", "end-1c")

        # Protect already-wrapped occurrences with a placeholder
        already = f"<{tag}>{sel}</{tag}>"
        placeholder = f"\x00WRAPPED_{tag}\x00"
        html2 = html.replace(already, placeholder)
        count = html2.count(sel)
        if count == 0:
            html2 = html2.replace(placeholder, already)
            self._flash("⚠  Aucune occurrence non balisée trouvée.", FG_YELLOW)
            self._output.delete("1.0", "end")
            self._output.insert("1.0", html2)
            return
        html2 = html2.replace(sel, wrapped)
        html2 = html2.replace(placeholder, already)

        self._output.delete("1.0", "end")
        self._output.insert("1.0", html2)
        self._highlight_html()
        self._flash(f"✓  {count} occurrence(s) balisée(s) avec <{tag}>.", FG_GREEN)

    def _build_tag(self, tag: str, content: str):
        """Return the HTML string wrapping content with tag.  Returns None if cancelled."""
        if tag == "a":
            href = self._ask_href()
            if href is None:
                return None
            return f'<a href="{href}">{content}</a>'
        return f"<{tag}>{content}</{tag}>"

    def _ask_href(self):
        """Modal dialog — returns the entered URL, or None if cancelled."""
        dlg = tk.Toplevel(self)
        dlg.title("Lien <a>")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        label(dlg, "URL du lien :", font=FONT_BODY).pack(padx=24, pady=(18, 4))
        href_var = tk.StringVar(value="https://")
        e = entry(dlg, textvariable=href_var, width=52)
        e.pack(padx=24, pady=4)
        e.focus_set()
        e.select_range(0, "end")

        result = [None]   # None = cancelled

        def _ok(event=None):
            result[0] = href_var.get() or "#"
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        bf = styled_frame(dlg)
        bf.pack(pady=14)
        btn(bf, "OK",       _ok,     primary=True ).pack(side="left", padx=6)
        btn(bf, "Annuler",  _cancel, primary=False).pack(side="left", padx=6)
        e.bind("<Return>", _ok)
        dlg.bind("<Escape>", lambda e: _cancel())
        _center_win(dlg)
        dlg.wait_window()
        return result[0]

    # ── Copy / Preview ────────────────────────────────────────────────────────
    def _copy_html(self):
        html = self._output.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(html)
        self._flash("✓  HTML copié dans le presse-papier !", FG_GREEN)

    # ── Audio Guide preset ────────────────────────────────────────────────────
    _AG_CONFIG_FILE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_html_formatter_config.json")

    _AG_DEFAULTS = {
        "h2_style":      "font-size:22pt; font-weight:bold; margin:20px 0;",
        "p_style":       "",
        "trailing_empty": 2,
        "col_id":        "A",
        "col_title":     "B",
        "col_body":      "C",
        "skip_rows":     1,
    }

    def _load_ag_styles(self) -> dict:
        try:
            with open(self._AG_CONFIG_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            styles = dict(self._AG_DEFAULTS)
            styles.update(saved)
            return styles
        except Exception:
            return dict(self._AG_DEFAULTS)

    def _save_ag_styles(self):
        try:
            with open(self._AG_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._ag_styles, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _toggle_ag_preset(self):
        self._ag_active_var.set(not self._ag_active_var.get())
        on = self._ag_active_var.get()
        self._ag_btn.config(bg=ACCENT if on else BG3,
                            fg=FG     if on else FG2)

    def _ag_run(self):
        raw = self._input.get("1.0", "end-1c")
        if not raw.strip():
            self._flash("⚠  Collez le texte Excel dans la zone de gauche.", FG_YELLOW)
            return
        try:
            html = self._convert_audio_guide(raw)
        except Exception as ex:
            import traceback
            messagebox.showerror("Erreur de conversion",
                                 traceback.format_exc(), parent=self)
            return
        self._output.delete("1.0", "end")
        self._output.insert("1.0", html)
        self._highlight_html()
        self._flash("✓  HTML Audio Guide généré.", FG_GREEN)

    def _convert_audio_guide(self, raw: str) -> str:
        st   = self._ag_styles
        lines = [l.rstrip() for l in raw.splitlines()]
        # drop leading blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return ""

        result = []
        i = 0

        # ── Try to detect ARTIST《WORK》DATE on the first line ─────────────
        first = lines[0]
        m = re.match(
            r'^(.*?)(?:[《（<\(])(.*?)(?:[》）>\)])\s*(\d[\d\s\S]{0,20}?(?:年|月)[\d〜\-~]*(?:月|年)?[^♪\n]*)?\s*$',
            first.strip())
        if m:
            artist = m.group(1).strip()
            work   = m.group(2).strip()
            date   = (m.group(3) or "").strip()

            def _h2(txt, sty):
                a = f' style="{sty}"' if sty else ""
                return f"<h2{a}>{txt}</h2>"

            def _p(txt, sty):
                a = f' style="{sty}"' if sty else ""
                return f"<p{a}>{txt}</p>"

            if artist:
                result.append(_h2(artist, st["h2_artist_style"]))
            if work:
                wo = st["work_bracket_open"]
                wc = st["work_bracket_close"]
                result.append(_h2(f"{wo}{work}{wc}", st["h2_work_style"]))
            if date:
                result.append(_p(date, st["p_date_style"]))
            i += 1
        else:
            # Fallback: treat first line as a plain h2 if short, else p
            sty = st["h2_artist_style"]
            a   = f' style="{sty}"' if sty else ""
            result.append(f'<h2{a}>{first.strip()}</h2>')
            i += 1

        # ── Remaining lines ───────────────────────────────────────────────
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            # BGM block: collect all consecutive non-empty lines
            if re.match(r'^♪\s*BGM', stripped):
                bgm_block = []
                while i < len(lines) and lines[i].strip():
                    l = lines[i]
                    # append &nbsp; after ♪BGM marker
                    l = re.sub(r'^(♪\s*BGM)', r'\1&nbsp;', l, count=1)
                    if st["auto_link_urls"]:
                        l = re.sub(
                            r'(https?://[^\s\u3000<>]+)',
                            r'<a href="\1">\1</a>', l)
                    bgm_block.append(l)
                    i += 1
                bs = st["p_bgm_style"]
                ba = f' style="{bs}"' if bs else ""
                inner = '<br />\n'.join(bgm_block)
                result.append(f'<p{ba}>{inner}</p>')
            else:
                txt = stripped
                if st["auto_link_urls"]:
                    txt = re.sub(
                        r'(https?://[^\s\u3000<>]+)',
                        r'<a href="\1">\1</a>', txt)
                bs = st["p_body_style"]
                ba = f' style="{bs}"' if bs else ""
                result.append(f'<p{ba}>{txt}</p>')
                i += 1

        # ── Trailing empty paragraphs ─────────────────────────────────────
        for _ in range(int(st.get("trailing_empty", 2))):
            result.append('<p>&nbsp;</p>')

        return '\n'.join(result)

    def _ag_configure(self):
        """Config dialog — just h2 style, p style, trailing empty paragraphs."""
        dlg = tk.Toplevel(self)
        dlg.title("⚙  Styles HTML")
        dlg.configure(bg=BG)
        dlg.resizable(True, False)
        dlg.grab_set()

        st = self._ag_styles

        fields = [
            ("h2_style", "Style <h2>  (titre)"),
            ("p_style",  "Style <p>   (corps)"),
        ]
        vars_ = {}
        for row_i, (key, lbl_txt) in enumerate(fields):
            tk.Label(dlg, text=lbl_txt + " :", bg=BG, fg=FG2,
                     font=FONT_SM).grid(row=row_i, column=0, sticky="e",
                                        padx=(16, 8), pady=8)
            v = tk.StringVar(value=st.get(key, ""))
            vars_[key] = v
            tk.Entry(dlg, textvariable=v, bg=BG3, fg=FG,
                     insertbackground=FG, relief="flat", font=FONT_MONO,
                     width=56).grid(row=row_i, column=1, padx=(0, 16),
                                    pady=8, sticky="ew")
        dlg.columnconfigure(1, weight=1)

        # Trailing empty <p>&nbsp;</p>
        tk.Label(dlg, text="<p>&nbsp;</p> finaux :", bg=BG, fg=FG2,
                 font=FONT_SM).grid(row=2, column=0, sticky="e",
                                    padx=(16, 8), pady=8)
        trail_var = tk.IntVar(value=st.get("trailing_empty", 2))
        tk.Spinbox(dlg, from_=0, to=10, width=4, textvariable=trail_var,
                   bg=BG3, fg=FG, buttonbackground=BG3,
                   relief="flat").grid(row=2, column=1, sticky="w",
                                       padx=(0, 16), pady=8)

        # Live preview
        prev = tk.Label(dlg, text="", bg=BG3, fg=FG2, font=FONT_MONO,
                        justify="left", anchor="w", wraplength=520,
                        padx=8, pady=6)
        prev.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 4))

        def _refresh(*_):
            h2s = vars_["h2_style"].get()
            ps  = vars_["p_style"].get()
            h2a = f' style="{h2s}"' if h2s else ""
            pa  = f' style="{ps}"'  if ps  else ""
            prev.config(text=(
                f'<h2{h2a}>1、はじめに 〜ファン・ゴッホ・ファミリー</h2>\n'
                f'<p{pa}>♪BGM ユリウス・クレンゲル…</p>\n'
                f'<p>&nbsp;</p>  (×{trail_var.get()})'
            ))
        for v in vars_.values():
            v.trace_add("write", _refresh)
        trail_var.trace_add("write", _refresh)
        _refresh()

        def _ok():
            for key, v in vars_.items():
                st[key] = v.get()
            st["trailing_empty"] = trail_var.get()
            self._save_ag_styles()
            dlg.destroy()
            self._flash("✓  Styles sauvegardés.", FG_GREEN)

        def _reset():
            for key, v in vars_.items():
                v.set(self._AG_DEFAULTS.get(key, ""))
            trail_var.set(self._AG_DEFAULTS["trailing_empty"])

        bf = styled_frame(dlg)
        bf.grid(row=4, column=0, columnspan=2, pady=14)
        btn(bf, "💾  Sauvegarder",   _ok,        primary=True ).pack(side="left", padx=6)
        btn(bf, "↩  Réinitialiser", _reset,     primary=False).pack(side="left", padx=6)
        btn(bf, "✕  Annuler",       dlg.destroy, primary=False).pack(side="left", padx=6)

    def _ag_import_excel(self):
        """Batch-import an Excel file → generate full HTML for all rows."""
        try:
            import importlib
            openpyxl = importlib.import_module("openpyxl")
        except ImportError:
            if messagebox.askyesno(
                    "openpyxl requis",
                    "openpyxl n'est pas installé.\nInstaller maintenant ?",
                    parent=self):
                import subprocess as _sp
                _sp.check_call([sys.executable, "-m", "pip", "install",
                                "openpyxl", "-q"])
                import importlib
                openpyxl = importlib.import_module("openpyxl")
            else:
                return

        path = filedialog.askopenfilename(
            parent=self,
            title="Sélectionner le fichier Excel",
            filetypes=[("Excel", "*.xlsx *.xlsm *.xls"), ("Tous", "*.*")])
        if not path:
            return

        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except Exception as ex:
            messagebox.showerror("Erreur", str(ex), parent=self)
            return

        self._ag_excel_batch(wb, path)

    def _ag_excel_batch(self, wb, path: str):
        """Batch dialog: table of all rows → generate complete HTML in one click."""
        dlg = tk.Toplevel(self)
        dlg.title("📂  Import Excel — Batch HTML")
        dlg.configure(bg=BG)
        dlg.resizable(True, True)
        scaled_minsize(dlg, 900, 580)
        dlg.grab_set()

        st = self._ag_styles   # live config

        # ── Top bar ───────────────────────────────────────────────────────────
        top = styled_frame(dlg)
        top.pack(fill="x", padx=16, pady=(12, 4))
        label(top, f"📄  {os.path.basename(path)}", font=FONT_BODY).pack(side="left")

        # ── Config row 1 : sheet + first data row ─────────────────────────────
        cr1 = styled_frame(dlg)
        cr1.pack(fill="x", padx=16, pady=(0, 4))
        label(cr1, "Feuille :", font=FONT_SM, fg=FG2).pack(side="left", padx=(0, 6))
        sheet_var = tk.StringVar(value=wb.sheetnames[0])
        combo(cr1, sheet_var, wb.sheetnames, width=26).pack(side="left")
        label(cr1, "  Prem. ligne data :", font=FONT_SM, fg=FG2).pack(side="left", padx=(14, 6))
        skip_var = tk.IntVar(value=int(st.get("skip_rows", 1)) + 1)
        tk.Spinbox(cr1, from_=1, to=50, width=4, textvariable=skip_var,
                   bg=BG3, fg=FG, buttonbackground=BG3,
                   relief="flat").pack(side="left")
        label(cr1, "  (1 = pas d'en-tête)", font=FONT_SM, fg=FG2).pack(side="left", padx=(4,0))

        # ── Config row 2 : column mapping ─────────────────────────────────────
        cr2 = styled_frame(dlg)
        cr2.pack(fill="x", padx=16, pady=(0, 8))
        def _col_entry(parent, lbl_txt, default):
            label(parent, lbl_txt, font=FONT_SM, fg=FG2).pack(side="left", padx=(0, 4))
            v = tk.StringVar(value=default)
            entry(parent, textvariable=v, width=4).pack(side="left")
            label(parent, "  ", font=FONT_SM).pack(side="left")
            return v
        col_id_var    = _col_entry(cr2, "Col ガイドNo :", st.get("col_id",    "A"))
        col_title_var = _col_entry(cr2, "Col タイトル :",  st.get("col_title", "B"))
        col_body_var  = _col_entry(cr2, "Col 本文 :",       st.get("col_body",  "C"))

        # ── Treeview table ────────────────────────────────────────────────────
        tv_frame = tk.Frame(dlg, bg=BG3)
        tv_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))

        vsb = ttk.Scrollbar(tv_frame, orient="vertical")
        hsb = ttk.Scrollbar(tv_frame, orient="horizontal")
        style = ttk.Style(dlg)
        style.configure("Excel.Treeview",
                        background=BG3, fieldbackground=BG3,
                        foreground=FG, rowheight=22, font=FONT_SM)
        style.configure("Excel.Treeview.Heading",
                        background=BG2, foreground=FG2, font=FONT_SM)
        style.map("Excel.Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", FG)])

        tv = ttk.Treeview(tv_frame, style="Excel.Treeview",
                          columns=("id", "title", "body"),
                          show="headings", selectmode="extended",
                          yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tv.heading("id",    text="ガイドNo", anchor="w")
        tv.heading("title", text="タイトル（略記）", anchor="w")
        tv.heading("body",  text="本文（作品タイトル等詳細）", anchor="w")
        tv.column("id",    width=80,  stretch=False)
        tv.column("title", width=220, stretch=False)
        tv.column("body",  width=500, stretch=True)
        vsb.config(command=tv.yview)
        hsb.config(command=tv.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        tv.pack(fill="both", expand=True)

        # Row data cache  { iid: (id_val, title_val, body_val) }
        _row_data: dict = {}

        def _load(*_):
            tv.delete(*tv.get_children())
            _row_data.clear()
            try:
                ws      = wb[sheet_var.get()]
                ci_id   = self._col_letter_to_idx(col_id_var.get().strip().upper()   or "A")
                ci_ttl  = self._col_letter_to_idx(col_title_var.get().strip().upper() or "B")
                ci_body = self._col_letter_to_idx(col_body_var.get().strip().upper()  or "C")
                first   = max(1, skip_var.get())
                for r in ws.iter_rows(min_row=first):
                    max_ci = max(ci_id, ci_ttl, ci_body)
                    if len(r) <= max_ci:
                        continue
                    id_val   = str(r[ci_id  ].value or "").strip()
                    ttl_val  = str(r[ci_ttl ].value or "").strip()
                    body_val = str(r[ci_body].value or "").strip()
                    if not ttl_val and not body_val:
                        continue
                    iid = str(r[0].row)
                    body_snip = body_val[:120].replace("\n", " ")
                    tv.insert("", "end", iid=iid,
                              values=(id_val, ttl_val, body_snip))
                    _row_data[iid] = (id_val, ttl_val, body_val)
            except Exception as ex:
                tv.insert("", "end", values=("ERR", str(ex), ""))

        # Preview
        prev_frm = tk.Frame(dlg, bg=BG3, height=90)
        prev_frm.pack(fill="x", padx=16, pady=(0, 4))
        prev_frm.pack_propagate(False)
        prev_text = tk.Text(prev_frm, bg=BG3, fg=FG2, font=FONT_MONO,
                            relief="flat", state="disabled", wrap="word")
        prev_text.pack(fill="both", expand=True, padx=4, pady=4)

        def _on_select(event=None):
            sel = tv.selection()
            if not sel:
                return
            iid = sel[-1]
            if iid in _row_data:
                _, ttl, body = _row_data[iid]
                prev_text.config(state="normal")
                prev_text.delete("1.0", "end")
                prev_text.insert("1.0", f"TITRE : {ttl}\n\n{body[:400]}")
                prev_text.config(state="disabled")

        tv.bind("<<TreeviewSelect>>", _on_select)

        # Trace column changes to reload
        for v in (sheet_var, col_id_var, col_title_var, col_body_var, skip_var):
            v.trace_add("write", _load)
        _load()

        # ── Bottom toolbar ────────────────────────────────────────────────────
        bot = styled_frame(dlg)
        bot.pack(fill="x", padx=16, pady=(0, 10))

        def _select_all():
            tv.selection_set(tv.get_children())
        def _deselect_all():
            tv.selection_remove(tv.get_children())

        btn(bot, "☑  Tout sélectionner",   _select_all,   primary=False).pack(side="left", padx=(0, 4))
        btn(bot, "☐  Tout désélectionner", _deselect_all, primary=False).pack(side="left", padx=(0, 20))

        # word-break: keep-all toggle
        wbka_var = tk.BooleanVar(value=st.get("wbka", False))
        def _update_wbka_btn():
            on = wbka_var.get()
            wbka_btn.config(
                bg=ACCENT if on else BG3,
                fg=FG if on else FG2,
                relief="flat")
        wbka_btn = tk.Button(
            bot, text="word-break: keep-all",
            font=FONT_SM,
            relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
            command=lambda: [wbka_var.set(not wbka_var.get()), _update_wbka_btn()])
        _update_wbka_btn()
        wbka_btn.pack(side="right", padx=(8, 0))

        def _generate(only_sel=True):
            items = tv.selection() if only_sel else tv.get_children()
            if not items:
                self._flash("⚠  Aucune ligne sélectionnée.", FG_YELLOW)
                return
            # Save col config
            st["col_id"]    = col_id_var.get().strip().upper()    or "A"
            st["col_title"] = col_title_var.get().strip().upper() or "B"
            st["col_body"]  = col_body_var.get().strip().upper()  or "C"
            st["skip_rows"] = skip_var.get() - 1
            st["wbka"]      = wbka_var.get()
            self._save_ag_styles()

            h2_sty = st.get("h2_style", "font-size:22pt; font-weight:bold; margin:20px 0;")
            p_sty  = st.get("p_style", "")
            trail  = int(st.get("trailing_empty", 2))
            # Inject word-break: keep-all if toggle is ON
            if wbka_var.get():
                wbka_css = "word-break: keep-all;"
                h2_sty = (h2_sty.rstrip("; ") + "; " + wbka_css).lstrip("; ")
                p_sty  = (p_sty.rstrip("; ")  + "; " + wbka_css).lstrip("; ")
            h2a    = f' style="{h2_sty}"' if h2_sty else ""
            pa     = f' style="{p_sty}"'  if p_sty  else ""

            parts = []
            for iid in items:
                if iid not in _row_data:
                    continue
                id_val, ttl_val, body_val = _row_data[iid]
                block = []
                # Guide number as HTML comment — visible in code, invisible in browser
                num_ref = id_val or re.match(r"^(\d+)", ttl_val or "")
                if num_ref:
                    num_str = num_ref if isinstance(num_ref, str) else num_ref.group(1)
                    block.append(f"<!-- {num_str} -->")
                if ttl_val:
                    # Strip leading number prefix: "1、" "55、" "1." "1 " etc.
                    ttl_clean = re.sub(r"^\d+[、。．.,\s]+", "", ttl_val).strip()
                    block.append(f"<h2{h2a}>{ttl_clean}</h2>")
                if body_val:
                    # Normalise Windows/Mac line endings from Excel
                    body_norm = body_val.replace("\r\n", "\n").replace("\r", "\n")
                    # Split on blank lines → separate <p> blocks
                    for para in re.split(r"\n{2,}", body_norm):
                        # Keep each line exactly as-is; only trim the block edges
                        para = para.strip("\n")
                        if not para:
                            continue
                        # Single line breaks within a block → <br />
                        inner = para.replace("\n", "<br />\n")
                        block.append(f"<p{pa}>{inner}</p>")
                for _ in range(trail):
                    block.append("<p>&nbsp;</p>")
                if block:
                    parts.append("\n".join(block))

            full_html = "\n\n".join(parts)
            self._output.delete("1.0", "end")
            self._output.insert("1.0", full_html)
            self._highlight_html()
            dlg.destroy()
            self._flash(f"✓  HTML généré pour {len(parts)} entrée(s).", FG_GREEN)

        btn(bot, "▶  Générer HTML (sélection)", lambda: _generate(True),
            primary=True).pack(side="left", padx=(0, 6))
        btn(bot, "▶▶  Générer HTML (tout)",     lambda: _generate(False),
            primary=True).pack(side="left", padx=(0, 14))
        btn(bot, "✕  Fermer", dlg.destroy, primary=False).pack(side="right")

        _center_win(dlg)

    @staticmethod
    def _col_letter_to_idx(col: str) -> int:
        """Convert column letter(s) like 'C' or 'AA' to 0-based index."""
        col = col.upper()
        idx = 0
        for c in col:
            idx = idx * 26 + (ord(c) - ord('A') + 1)
        return idx - 1

    # ── word-break: keep-all helpers ─────────────────────────────────────────
    _WBKA_STYLE = 'style="word-break: keep-all;"'
    _WBKA_OPEN  = '<span style="word-break: keep-all;">'
    _WBKA_CLOSE = '</span>'

    def _toggle_wbka(self):
        self._wbka_var.set(not self._wbka_var.get())
        on = self._wbka_var.get()
        self._wbka_btn.config(bg=ACCENT if on else BG3,
                              fg=FG     if on else FG2)
        state = "activé (aperçu)" if on else "désactivé"
        self._flash(f"word-break: keep-all  {state}.",
                    FG_GREEN if on else FG2)

    def _wbka_wrap_sel(self):
        """Wrap the current HTML selection in a keep-all span."""
        try:
            sel = self._output.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            self._flash("⚠  Sélectionnez du texte dans la zone HTML.", FG_YELLOW)
            return
        wrapped = self._WBKA_OPEN + sel + self._WBKA_CLOSE
        self._output.delete(tk.SEL_FIRST, tk.SEL_LAST)
        self._output.insert(tk.INSERT, wrapped)
        self._highlight_html()
        self._flash("✓  word-break: keep-all appliqué à la sélection.", FG_GREEN)

    def _wbka_apply_all(self):
        """Wrap every <p>, <li>, <h1-6> block that doesn't already have keep-all."""
        html = self._output.get("1.0", "end-1c")
        if not html.strip():
            self._flash("⚠  Zone HTML vide.", FG_YELLOW)
            return
        count = [0]

        def _replace(m):
            tag, attrs, content = m.group(1), m.group(2), m.group(3)
            if "word-break" in attrs:
                return m.group(0)   # already tagged
            count[0] += 1
            # Inject into existing style attr if present, else add new attr
            if 'style="' in attrs:
                attrs2 = attrs.replace('style="', 'style="word-break: keep-all; ', 1)
            else:
                attrs2 = attrs + ' style="word-break: keep-all;"'
            return f"<{tag}{attrs2}>{content}</{tag}>"

        pattern = r"<(p|li|h[1-6])(\s[^>]*)?>([\s\S]*?)</\1>"
        html2 = re.sub(pattern, _replace, html, flags=re.IGNORECASE)
        self._output.delete("1.0", "end")
        self._output.insert("1.0", html2)
        self._highlight_html()
        self._flash(f"✓  word-break: keep-all injecté sur {count[0]} bloc(s).", FG_GREEN)

    def _wbka_strip_all(self):
        """Remove all word-break: keep-all inline styles from the output."""
        html = self._output.get("1.0", "end-1c")
        # Remove standalone attribute: style="word-break: keep-all;"
        html2 = re.sub(r'\s*style="word-break:\s*keep-all;?"', "", html)
        # Remove when it was injected at the start of a multi-property style
        html2 = re.sub(r'word-break:\s*keep-all;\s*', "", html2)
        # Remove empty style attributes left over
        html2 = re.sub(r'\s*style="\s*"', "", html2)
        # Remove bare keep-all <span> wrappers
        html2 = re.sub(
            r'<span style="word-break:\s*keep-all;?">([\s\S]*?)</span>',
            r'\1', html2)
        self._output.delete("1.0", "end")
        self._output.insert("1.0", html2)
        self._highlight_html()
        self._flash("✓  word-break: keep-all retiré de tout le document.", FG_GREEN)

    def _preview_browser(self):
        import tempfile, webbrowser
        html = self._output.get("1.0", "end-1c")
        wbka_css = "word-break: keep-all; " if self._wbka_var.get() else ""
        page = (
            "<!DOCTYPE html>\n<html lang='fr'>\n<head>"
            "<meta charset='UTF-8'><title>Aperçu HTML</title>"
            f"<style>body{{font-family:Segoe UI,sans-serif;padding:40px;"
            f"max-width:860px;margin:auto;line-height:1.6;{wbka_css}}}</style>"
            "</head><body>" + html + "</body>\n</html>"
        )
        with tempfile.NamedTemporaryFile(
                "w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(page)
            tmp = f.name
        import webbrowser
        webbrowser.open(f"file:///{tmp}")
        self._flash("✓  Aperçu ouvert dans le navigateur.", FG_GREEN)

    def _clear_all(self):
        self._input.delete("1.0", "end")
        self._output.delete("1.0", "end")
        self._counter_lbl.config(text="")
        self._flash("Effacé.", FG2)

    # ── Live stats ────────────────────────────────────────────────────────────
    def _update_counter(self):
        txt = self._input.get("1.0", "end-1c")
        words = len(txt.split()) if txt.strip() else 0
        chars = len(txt)
        self._counter_lbl.config(text=f"{words} mots  ·  {chars} car.")

    # ── Syntax highlighting ───────────────────────────────────────────────────
    def _highlight_html(self):
        try:
            self._do_highlight_html()
        except Exception:
            pass   # never block output over a syntax-highlight error

    def _do_highlight_html(self):
        widget = self._output
        content = widget.get("1.0", "end")
        for t in ("hl_tag", "hl_attr", "hl_val", "hl_comment"):
            widget.tag_remove(t, "1.0", "end")

        for m in re.finditer(r"(</?[A-Za-z][^>]*?>|<!--.*?-->)", content, re.DOTALL):
            s = f"1.0+{m.start()}c"
            e = f"1.0+{m.end()}c"
            tok = m.group(0)
            if tok.startswith("<!--"):
                widget.tag_add("hl_comment", s, e)
            else:
                widget.tag_add("hl_tag", s, e)
                # Attribute names & values inside the tag
                for am in re.finditer(r'(\s+[A-Za-z\-]+)(="[^"]*")?', tok):
                    as_ = f"1.0+{m.start() + am.start()}c"
                    ae  = f"1.0+{m.start() + am.end(1)}c"
                    widget.tag_add("hl_attr", as_, ae)
                    if am.group(2):
                        vs = f"1.0+{m.start() + am.end(1)}c"
                        ve = f"1.0+{m.start() + am.end(2)}c"
                        widget.tag_add("hl_val", vs, ve)

    # ── Status flash ──────────────────────────────────────────────────────────
    def _flash(self, msg: str, color: str = FG):
        self._status.config(text=msg, fg=color)
        self.after(4500, lambda: self._status.config(text=""))


# =============================================================================
#  HOME SCREEN
# =============================================================================
class HomeScreen(tk.Tk):
    _CARD_SIZE = 200
    _CARD_GAP  = 16
    _COLUMNS   = 3

    def __init__(self):
        super().__init__()
        self.title("Image Tool")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._apply_style()
        self._build()
        # ── Size to fit the actual grid content (up to the available
        #    desktop area), so all tool cards are visible without an initial
        #    scroll on a normal-height display; falls back to scrolling
        #    (already wired on the canvas) only if the screen is smaller
        #    than the content. ─────────────────────────────────────────────
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        content_h = self._tools_inner.winfo_reqheight()
        # header (hero + section label) + grid content + bottom margin
        wanted_h = self.winfo_reqheight() - self._tools_cv.winfo_reqheight() + content_h + 20
        w = min(int(sw * 0.8), 1200)
        h = min(max(wanted_h, 600), int(sh * 0.9))
        x = (sw - w) // 2
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        scaled_minsize(self, 640, 480)

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Custom.TCombobox",
                        fieldbackground=BG3, background=BG3,
                        foreground=FG, selectbackground=ACCENT,
                        arrowcolor=FG)
        for orient in ("Vertical", "Horizontal"):
            name = f"Custom.{orient}.TScrollbar"
            style.configure(name,
                            background=BG3, troughcolor=BG2,
                            bordercolor=BG2, arrowcolor=FG2,
                            lightcolor=BG3, darkcolor=BG3)
            style.map(name, background=[("active", BG4)])

    def _build(self):
        # ── Accent top bar ────────────────────────────────────────────────────
        tk.Frame(self, bg=ACCENT, height=3).pack(fill="x")
        # ── Hero banner ───────────────────────────────────────────────────────
        hero = tk.Frame(self, bg=BG)
        hero.pack(fill="x")
        hpad = styled_frame(hero, bg=BG)
        hpad.pack(fill="x", padx=28, pady=(24, 20))

        row1 = styled_frame(hpad, bg=BG)
        row1.pack(anchor="w")
        icon_box = tk.Frame(row1, bg=ACCENT, width=46, height=46)
        icon_box.pack_propagate(False)
        icon_box.pack(side="left", padx=(0, 14))
        tk.Label(icon_box, text="🖼", bg=ACCENT, fg="#ffffff",
                 font=("Segoe UI", 20)).pack(expand=True)
        col = styled_frame(row1, bg=BG)
        col.pack(side="left")
        label(col, "Image Tool", font=("Segoe UI", 22, "bold"),
              fg=FG, bg=BG).pack(anchor="w")
        label(col, _t("home.sub"),
              fg=FG2, font=FONT_SM, bg=BG).pack(anchor="w")

        pills = styled_frame(hpad, bg=BG)
        pills.pack(anchor="w", pady=(14, 0))
        for txt in _t("home.pills"):
            p = tk.Frame(pills, bg=BG2, highlightbackground=BORDER,
                        highlightthickness=1)
            p.pack(side="left", padx=(0, 8))
            tk.Label(p, text=f"  {txt}  ", bg=BG2, fg=FG2,
                     font=("Segoe UI", 8)).pack(padx=2, pady=4)

        # ── Section label ─────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")  # hero bottom border
        sec = styled_frame(self, bg=BG)
        sec.pack(fill="x", padx=28, pady=(20, 10))
        label(sec, _t("home.tools"), fg=FG2, font=("Segoe UI", 8, "bold"),
              bg=BG).pack(side="left")
        tk.Frame(sec, bg=ACCENT, height=1).pack(side="left", fill="x",
                                                       expand=True, padx=(10, 0))

        # ── Tool cards in a scrollable area ───────────────────────────────────
        tools_outer = tk.Frame(self, bg=BG)
        tools_outer.pack(fill="both", expand=True, padx=28)

        tools_cv = tk.Canvas(tools_outer, bg=BG, highlightthickness=0)
        tools_sb = tk.Scrollbar(tools_outer, orient="vertical",
                                command=tools_cv.yview)
        tools_cv.configure(yscrollcommand=tools_sb.set)
        tools_cv.pack(side="left", fill="both", expand=True)
        # scrollbar only shows when content overflows; always bind wheel
        self._tools_cv = tools_cv

        def _scroll(e):
            tools_cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

        def _bind_scroll(widget):
            widget.bind("<MouseWheel>", _scroll)
            for child in widget.winfo_children():
                _bind_scroll(child)

        self._tools_inner = tk.Frame(tools_cv, bg=BG)
        _tw = tools_cv.create_window((0, 0), window=self._tools_inner,
                                     anchor="nw")
        self._tools_inner.bind("<Configure>", lambda e: (
            tools_cv.configure(scrollregion=tools_cv.bbox("all")),
            tools_cv.itemconfig(_tw, width=tools_cv.winfo_width())))
        tools_cv.bind("<Configure>",
                      lambda e: tools_cv.itemconfig(_tw, width=e.width))
        tools_cv.bind("<MouseWheel>", _scroll)
        # Re-bind scroll on every inner layout change (new children added)
        self._tools_inner.bind("<Configure>",
                               lambda e: _bind_scroll(self._tools_inner),
                               add="+")

        tools = [
            ("✏",  "Rename",      _t("home.rename_d"),     RenameWizard,      "#4cc9f0"),
            ("⤡",  "Resize",      _t("home.resize_d"),     ResizeWizard,      "#4895ef"),
            ("🗜", "Compress",    _t("home.comp_d"),       CompressWizard,    "#ff6b6b"),
            ("🔄", "Convert",     _t("conv.desc"),         ConvertWizard,     "#f4a261"),
            ("🎨", "Generate",    _t("home.gen_d"),        ImageGenWizard,    "#7c6af7"),
            ("📐", "Guide Mark",  _t("home.gmw_d"),        GuideMarkWizard,   "#2ec4b6"),
            ("📋", "Clipboard",   _t("home.clip_d"),       ClipboardManager,  "#f72585"),
            ("📄", "PDF Extractor", _t("home.pdf_d"),      PDFExtractorWizard,    "#f77f00"),
            ("🔗", "Link Redirector", _t("home.lrw_d"),    LinkRedirectorWizard,  "#9b5de5"),
            ("🏷", "HTML Formatter",  _t("home.html_d"),   HtmlFormatterWizard,   "#43aa8b"),
        ]
        grid_frm = styled_frame(self._tools_inner, bg=BG)
        grid_frm.pack(fill="x")
        for col_i in range(self._COLUMNS):
            grid_frm.columnconfigure(col_i, weight=1, uniform="cards")
        for i, (ico, title, desc, cls, color) in enumerate(tools):
            r, c = divmod(i, self._COLUMNS)
            self._tool_card_grid(grid_frm, ico, title, desc, cls, color, r, c)

        # ── Pipeline ──────────────────────────────────────────────────────────
        sep2 = styled_frame(self._tools_inner, bg=BG)
        sep2.pack(fill="x", pady=(18, 12))
        label(sep2, _t("home.adv"), fg=FG2, font=("Segoe UI", 8, "bold"),
              bg=BG).pack(side="left")
        tk.Frame(sep2, bg=BORDER, height=1).pack(side="left", fill="x",
                                              expand=True, padx=(10, 0))

        pipe_frm = styled_frame(self._tools_inner, bg=BG)
        pipe_frm.pack(fill="x", pady=(0, 16))
        pipe_btn = tk.Button(
            pipe_frm, text=_t("home.pipeline"), command=lambda: PipelineWizard(self),
            bg=BG2, fg=FG, font=FONT_BODY,
            relief="flat", bd=0, padx=14, pady=7, cursor="hand2",
            highlightbackground=BORDER, highlightthickness=1,
            activebackground=BORDER, activeforeground=FG)
        pipe_btn.pack(side="left")
        label(pipe_frm, _t("home.pipe_desc"),
              fg=FG2, font=FONT_SM, bg=BG).pack(side="left", padx=(10, 0))

    def _tool_card_grid(self, parent, ico, title, desc, cls, accent_col, row, col):
        outer = tk.Frame(parent, bg=BG2, cursor="hand2",
                         width=self._CARD_SIZE, height=self._CARD_SIZE,
                         highlightbackground=BORDER, highlightthickness=1)
        outer.grid(row=row, column=col, padx=self._CARD_GAP // 2,
                  pady=self._CARD_GAP // 2, sticky="nsew")
        outer.grid_propagate(False)

        inner = styled_frame(outer, bg=BG2)
        inner.place(relx=0.5, rely=0.5, anchor="center")

        icon_bg = _blend_hex(BG2, accent_col, 0.15)
        icon_frm = tk.Frame(inner, bg=icon_bg, width=56, height=56)
        icon_frm.pack_propagate(False)
        icon_frm.pack(pady=(0, 12))
        ico_lbl = tk.Label(icon_frm, text=ico, bg=icon_bg, fg=accent_col,
                           font=("Segoe UI", 22))
        ico_lbl.pack(expand=True)

        t_lbl = label(inner, title, font=FONT_H2, fg=FG, bg=BG2)
        t_lbl.pack()
        d_lbl = label(inner, desc, fg=FG2, font=FONT_HINT, bg=BG2,
                     wraplength=self._CARD_SIZE - 32, justify="center")
        d_lbl.pack(pady=(4, 0))

        def open_tool(): cls(self)

        def on_enter(e):
            outer.config(highlightbackground=accent_col)
            t_lbl.config(fg=accent_col)

        def on_leave(e):
            outer.config(highlightbackground=BORDER)
            t_lbl.config(fg=FG)

        for w in (outer, inner, icon_frm, ico_lbl, t_lbl, d_lbl):
            w.bind("<Button-1>", lambda e: open_tool())
            w.bind("<Enter>",    on_enter)
            w.bind("<Leave>",    on_leave)

# =============================================================================
#  Entry point
# =============================================================================
def _parse_macro_args(argv):
    """Positive detection of macro mode: an explicit --rename-macro flag
    plus at least one path. A bare double-click (argv=[]) or any
    unexpected argument Explorer might add must still fall through to the
    normal HomeScreen launch -- never trigger the macro by accident."""
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--rename-macro", action="store_true")
    parser.add_argument("paths", nargs="*")
    ns, _unknown = parser.parse_known_args(argv)
    if ns.rename_macro and ns.paths:
        return [Path(p) for p in ns.paths]
    return None


if __name__ == "__main__":
    if sys.platform == "win32":
        # Without this, Windows bitmap-scales the whole Tk UI on any display
        # at >100% scaling, producing a blurry/low-res look. Must run before
        # any Tk window is created.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # older Windows fallback
            except Exception:
                pass

    if not PILLOW_OK:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
        from PIL import Image

    macro_paths = _parse_macro_args(sys.argv[1:])
    if macro_paths is not None:
        from app.wizards.rename_macro import launch_macro
        launch_macro(macro_paths)
    else:
        app = HomeScreen()
        app.mainloop()
