"""GuideMark mixed-sheet UI: the tile list + the composite-label editor.

``SheetPanel`` owns the list of ``SheetItem`` (one per printed tile, each
carrying its own Design snapshot) so tiles from several designs can share
one sheet. ``SegmentEditor`` edits a label made of several runs, e.g.
"14" (big) + "-1" (small).
"""

import copy
import tkinter as tk
from tkinter import ttk

from app.engines.guidemark_model import Segment, SheetItem
from app.engines.guidemark_paste import parse_labels
from app.i18n import _t
from app.theme import (
    BG, BG2, BG3, FG, FG2, FG3, FONT_BODY, FONT_HINT, FONT_SM,
    ACCENT, FG_ON_ACCENT)
from app.ui_kit import btn, scrollbar

_PREVIEW_PX = 220
_CURRENT = _t("gmw.sheet_design_cur")


def _spin(parent, var, lo, hi, inc=1, w=6, fmt=None):
    kw = {"format": fmt} if fmt else {}
    return tk.Spinbox(parent, textvariable=var, from_=lo, to=hi,
                      increment=inc, width=w, bg=BG3, fg=FG,
                      insertbackground=FG, relief="flat",
                      buttonbackground=BG3, **kw)


def describe(item: SheetItem) -> str:
    """One-line label for the list (composite runs are separated by ' | ')."""
    return " | ".join(s.text for s in item.segments)


class SegmentEditor(tk.Toplevel):
    """Modal editor for the runs of a label. Result: ``self.result``
    = ``(segments, copies, group)`` or ``None`` when cancelled, where
    ``group`` = ``{"dx_mm", "dy_mm", "center_main"}``.

    ``bulk=True`` edits the STYLE of several tiles at once: texts are
    read-only and runs cannot be added/removed (the caller copies each
    run's style onto the same-index run of every selected tile)."""

    def __init__(self, parent, item: SheetItem, families: list,
                 render_preview=None, with_copies=False, bulk=False):
        super().__init__(parent)
        self.title(_t("gmw.seg_title"))
        self.configure(bg=BG)
        self.transient(parent)
        self.result = None
        self._item = item
        self._families = families
        self._render_preview = render_preview
        self._preview_tk = None
        self._after_id = None
        self._rows: list = []
        self._copies = tk.IntVar(value=1)
        self._bulk = bulk
        self._dx = tk.StringVar(value=f"{item.dx_mm:g}")
        self._dy = tk.StringVar(value=f"{item.dy_mm:g}")
        self._center_main = tk.BooleanVar(value=item.center_main)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        for col, (key, w) in enumerate((
                ("gmw.seg_text", 10), ("gmw.seg_font", 16), ("gmw.seg_pt", 6),
                ("gmw.seg_dy", 7), ("gmw.seg_trk", 6),
                ("gmw.seg_gap", 7))):
            tk.Label(head, text=_t(key), bg=BG, fg=FG2, font=FONT_HINT,
                     width=w, anchor="w").grid(row=0, column=col, padx=2)
        self._rows_frame = tk.Frame(body, bg=BG)
        self._rows_frame.pack(fill="x", pady=(2, 6))
        for seg in item.segments:
            self._add_row(seg)

        if not bulk:
            btn(body, _t("gmw.seg_add"), lambda: self._add_row(Segment("")),
                primary=False).pack(anchor="w", pady=(0, 8))
        else:
            tk.Label(body, text=_t("gmw.seg_bulk"), bg=BG, fg=ACCENT,
                     font=FONT_SM, justify="left").pack(anchor="w",
                                                        pady=(0, 6))
        tk.Label(body, text=_t("gmw.seg_hint"), bg=BG, fg=FG3,
                 font=FONT_HINT, justify="left").pack(anchor="w")

        grp = tk.Frame(body, bg=BG)
        grp.pack(fill="x", pady=(8, 0))
        tk.Label(grp, text=_t("gmw.seg_group"), bg=BG, fg=FG,
                 font=FONT_SM).pack(side="left")
        tk.Label(grp, text="X", bg=BG, fg=FG2,
                 font=FONT_SM).pack(side="left", padx=(8, 2))
        _spin(grp, self._dx, -50, 50, inc=0.5, w=6,
              fmt="%.1f").pack(side="left")
        tk.Label(grp, text="Y", bg=BG, fg=FG2,
                 font=FONT_SM).pack(side="left", padx=(8, 2))
        _spin(grp, self._dy, -50, 50, inc=0.5, w=6,
              fmt="%.1f").pack(side="left")
        tk.Label(grp, text="mm", bg=BG, fg=FG2,
                 font=FONT_SM).pack(side="left", padx=(4, 0))
        tk.Checkbutton(body, text=_t("gmw.seg_center_main"),
                       variable=self._center_main, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG,
                       font=FONT_SM).pack(anchor="w", pady=(4, 0))
        for var in (self._dx, self._dy, self._center_main):
            var.trace_add("write", lambda *_: self._schedule_preview())

        if render_preview:
            self._canvas = tk.Canvas(body, width=_PREVIEW_PX,
                                     height=_PREVIEW_PX, bg=BG3,
                                     highlightthickness=0)
            self._canvas.pack(pady=10)

        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x", pady=(6, 0))
        if with_copies:
            tk.Label(foot, text=_t("gmw.seg_copies"), bg=BG, fg=FG,
                     font=FONT_SM).pack(side="left")
            _spin(foot, self._copies, 1, 999, w=5).pack(side="left", padx=6)
        btn(foot, _t("gmw.seg_cancel"), self.destroy,
            primary=False).pack(side="right")
        btn(foot, _t("gmw.seg_ok"), self._ok).pack(side="right", padx=8)

        self._schedule_preview()
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    # ── rows ──────────────────────────────────────────────────────────────
    def _add_row(self, seg: Segment):
        r = len(self._rows)
        v = {
            "text": tk.StringVar(value=seg.text),
            "font": tk.StringVar(value=seg.font_family or ""),
            "pt":   tk.StringVar(value="" if seg.pt is None else f"{seg.pt:g}"),
            "dy":   tk.StringVar(value=f"{seg.dy_mm:g}"),
            "trk":  tk.StringVar(value="" if seg.tracking is None
                                 else str(seg.tracking)),
            "gap":  tk.StringVar(value=f"{seg.gap_mm:g}"),
            "color": seg.color, "style": seg.font_style,
        }
        f = tk.Frame(self._rows_frame, bg=BG)
        f.grid(row=r, column=0, sticky="w", pady=2)
        tk.Entry(f, textvariable=v["text"], width=10, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat",
                 state="readonly" if self._bulk else "normal",
                 readonlybackground=BG2,
                 font=FONT_BODY).grid(row=0, column=0, padx=2)
        ttk.Combobox(f, textvariable=v["font"], values=[""] + self._families,
                     width=16, style="Custom.TCombobox",
                     font=FONT_SM).grid(row=0, column=1, padx=2)
        _spin(f, v["pt"], 4, 999, w=5).grid(row=0, column=2, padx=2)
        _spin(f, v["dy"], -50, 50, inc=0.5, w=6,
              fmt="%.1f").grid(row=0, column=3, padx=2)
        _spin(f, v["trk"], -50, 200, w=5).grid(row=0, column=4, padx=2)
        _spin(f, v["gap"], -20, 20, inc=0.5, w=6,
              fmt="%.1f").grid(row=0, column=5, padx=2)
        if not self._bulk:
            tk.Button(f, text="✕", bg=BG3, fg=FG, relief="flat",
                      cursor="hand2",
                      command=lambda fr=f: self._del_row(fr)).grid(
                          row=0, column=6, padx=(6, 0))
        # A Spinbox snaps an empty textvariable to its minimum on creation;
        # empty means "inherit", so restore the intended values.
        v["pt"].set("" if seg.pt is None else f"{seg.pt:g}")
        v["trk"].set("" if seg.tracking is None else str(seg.tracking))
        v["gap"].set(f"{seg.gap_mm:g}")
        for key in ("text", "font", "pt", "dy", "trk", "gap"):
            v[key].trace_add("write", lambda *_: self._schedule_preview())
        self._rows.append((f, v))

    def _del_row(self, frame):
        if len(self._rows) <= 1:
            return
        self._rows = [(f, v) for f, v in self._rows if f is not frame]
        frame.destroy()
        self._schedule_preview()

    def _segments(self) -> list:
        out = []
        for _f, v in self._rows:
            text = v["text"].get()
            if not text:
                continue

            def num(key, cast):
                raw = v[key].get().strip()
                if not raw:
                    return None
                try:
                    return cast(raw)
                except ValueError:
                    return None
            out.append(Segment(
                text=text, pt=num("pt", float),
                dy_mm=num("dy", float) or 0.0,
                gap_mm=num("gap", float) or 0.0,
                tracking=num("trk", int),
                color=v["color"],
                font_family=v["font"].get().strip() or None,
                font_style=v["style"] if v["font"].get().strip() else None))
        return out

    # ── preview / result ──────────────────────────────────────────────────
    def _schedule_preview(self):
        if not self._render_preview:
            return
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(250, self._draw_preview)

    def _draw_preview(self):
        self._after_id = None
        segs = self._segments()
        if not segs or not self.winfo_exists():
            return
        from PIL import ImageTk
        item = SheetItem(self._item.design, segs, self._item.design_name,
                         **self._group())
        img = self._render_preview(item)
        img.thumbnail((_PREVIEW_PX, _PREVIEW_PX))
        self._preview_tk = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        self._canvas.create_image(_PREVIEW_PX // 2, _PREVIEW_PX // 2,
                                  image=self._preview_tk)

    def _group(self) -> dict:
        def mm(var):
            try:
                return float(var.get())
            except (ValueError, tk.TclError):
                return 0.0
        return {"dx_mm": mm(self._dx), "dy_mm": mm(self._dy),
                "center_main": bool(self._center_main.get())}

    def _ok(self):
        segs = self._segments()
        if segs:
            self.result = (segs, max(1, self._copies.get()), self._group())
        self.destroy()


class PasteDialog(tk.Toplevel):
    """Paste a list of labels; shows live how it will be read.
    Result: ``self.result`` = list of runs-lists, or ``None``."""

    def __init__(self, parent, initial_text: str = ""):
        super().__init__(parent)
        self.title(_t("gmw.paste_title"))
        self.configure(bg=BG)
        self.transient(parent)
        self.result = None

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)
        tk.Label(body, text=_t("gmw.paste_hint"), bg=BG, fg=FG2,
                 font=FONT_HINT, justify="left").pack(anchor="w")
        self._text = tk.Text(body, width=44, height=10, bg=BG3, fg=FG,
                             insertbackground=FG, relief="flat",
                             font=FONT_BODY)
        self._text.pack(fill="both", expand=True, pady=8)
        self._text.insert("1.0", initial_text)
        self._text.bind("<<Modified>>", self._on_modified)
        sizes = tk.Frame(body, bg=BG)
        sizes.pack(fill="x", pady=(0, 6))
        self._main_pt = tk.StringVar()
        self._sub_pt = tk.StringVar()
        tk.Label(sizes, text=_t("gmw.paste_main_pt"), bg=BG, fg=FG,
                 font=FONT_SM).pack(side="left")
        tk.Entry(sizes, textvariable=self._main_pt, width=5, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat").pack(side="left",
                                                          padx=(4, 12))
        tk.Label(sizes, text=_t("gmw.paste_sub_pt"), bg=BG, fg=FG,
                 font=FONT_SM).pack(side="left")
        tk.Entry(sizes, textvariable=self._sub_pt, width=5, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat").pack(side="left",
                                                          padx=(4, 0))
        self._summary = tk.Label(body, bg=BG, fg=FG, font=FONT_SM,
                                 anchor="w", justify="left", wraplength=380)
        self._summary.pack(fill="x")

        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x", pady=(10, 0))
        btn(foot, _t("gmw.seg_cancel"), self.destroy,
            primary=False).pack(side="right")
        self._ok_btn = btn(foot, _t("gmw.seg_ok"), self._ok)
        self._ok_btn.pack(side="right", padx=8)

        self._update_summary()
        self.grab_set()
        self.wait_visibility()
        self._text.focus_set()

    def _labels(self) -> list:
        return parse_labels(self._text.get("1.0", "end"))

    def _on_modified(self, _e=None):
        if self._text.edit_modified():
            self._text.edit_modified(False)
            self._update_summary()

    def _update_summary(self):
        labels = self._labels()
        comp = sum(1 for r in labels if len(r) > 1)
        preview = ", ".join(" | ".join(r) for r in labels[:12])
        if len(labels) > 12:
            preview += " …"
        self._summary.configure(
            text=_t("gmw.paste_summary", n=len(labels), c=comp)
            + ("\n" + preview if labels else ""))
        self._ok_btn.configure(state="normal" if labels else "disabled")

    @staticmethod
    def _pt(var):
        try:
            v = float(var.get().replace(",", "."))
        except ValueError:
            return None
        return v if v > 0 else None

    def _ok(self):
        labels = self._labels()
        if labels:
            self.result = (labels, self._pt(self._main_pt),
                           self._pt(self._sub_pt))
        self.destroy()


class SheetPanel(tk.Frame):
    """List of tiles making up the sheet + add/edit/reorder controls."""

    def __init__(self, parent, *, make_range_items, make_seed_item,
                 make_label_items, get_design, families, render_preview,
                 on_change, bg=BG2):
        super().__init__(parent, bg=bg)
        self.items: list = []
        self._make_range_items = make_range_items
        self._make_seed_item = make_seed_item
        self._make_label_items = make_label_items
        self._get_design = get_design          # () -> (Design, name)
        self._design_var = tk.StringVar(value=_CURRENT)
        self._families = families
        self._render_preview = render_preview
        self._on_change = on_change

        dsg = tk.Frame(self, bg=bg)
        dsg.pack(fill="x", pady=(0, 6))
        tk.Label(dsg, text=_t("gmw.sheet_design"), bg=bg, fg=FG2,
                 font=FONT_HINT).pack(anchor="w")
        self._design_cb = ttk.Combobox(dsg, textvariable=self._design_var,
                                       values=[_CURRENT], state="readonly",
                                       style="Custom.TCombobox")
        self._design_cb.pack(fill="x")

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x")
        btn(bar, _t("gmw.sheet_add_range"), self._add_range,
            primary=False).pack(fill="x", pady=(0, 4))
        btn(bar, _t("gmw.sheet_add_composite"), self._add_composite,
            primary=False).pack(fill="x", pady=(0, 4))
        btn(bar, _t("gmw.sheet_paste"), self._paste,
            primary=False).pack(fill="x")

        box = tk.Frame(self, bg=bg)
        box.pack(fill="both", expand=True, pady=(8, 0))
        self._lb = tk.Listbox(box, height=7, bg=BG3, fg=FG, relief="flat",
                              selectmode="extended",
                              selectbackground=ACCENT,
                              selectforeground=FG_ON_ACCENT,
                              activestyle="none", exportselection=False,
                              font=FONT_SM)
        sb = scrollbar(box, orient="vertical", command=self._lb.yview)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._lb.bind("<Double-Button-1>", lambda e: self._edit())
        self._lb.bind("<<ListboxSelect>>", lambda e: self._show_size())
        self._lb.bind("<Control-v>", lambda e: self._paste())
        self._lb.bind("<Control-a>", lambda e: (
            self._lb.selection_set(0, "end"), "break")[1])

        acts = tk.Frame(self, bg=bg)
        acts.pack(fill="x", pady=(6, 0))
        for text, cmd in (("✎", self._edit), ("▲", lambda: self._move(-1)),
                          ("▼", lambda: self._move(1)),
                          ("✕", self._remove), (_t("gmw.sheet_clear"),
                                                self._clear)):
            tk.Button(acts, text=text, bg=BG3, fg=FG, relief="flat",
                      cursor="hand2", font=FONT_SM, padx=8,
                      command=cmd).pack(side="left", padx=(0, 4))
        sz = tk.Frame(self, bg=bg)
        sz.pack(fill="x", pady=(6, 0))
        tk.Label(sz, text=_t("gmw.sheet_size"), bg=bg, fg=FG,
                 font=FONT_SM).pack(side="left")
        self._size_var = tk.StringVar()
        tk.Entry(sz, textvariable=self._size_var, width=6, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat").pack(side="left",
                                                          padx=(6, 4))
        tk.Label(sz, text="pt", bg=bg, fg=FG2,
                 font=FONT_SM).pack(side="left")
        tk.Button(sz, text=_t("gmw.sheet_size_apply"), bg=BG3, fg=FG,
                  relief="flat", cursor="hand2", font=FONT_SM, padx=8,
                  command=self._apply_size).pack(side="left", padx=(8, 0))
        btn(self, _t("gmw.sheet_apply_design"), self._apply_design,
            primary=False).pack(fill="x", pady=(6, 0))
        self._summary = tk.Label(self, bg=bg, fg=FG2, font=FONT_HINT,
                                 anchor="w", justify="left", wraplength=320)
        self._summary.pack(fill="x", pady=(6, 0))
        self._refresh()

    # ── main-number size ──────────────────────────────────────────────────
    @staticmethod
    def _effective_pt(item: SheetItem):
        """Size the first run is drawn at (None when auto-fitted)."""
        pt = item.segments[0].pt
        if pt is not None:
            return pt
        d = item.design
        if d.auto_size:
            return None
        return d.fsize3_pt if len(item.segments[0].text) >= 3 else d.fsize_pt

    def _show_size(self):
        sel = self._sels()
        pt = self._effective_pt(self.items[sel[0]]) if sel else None
        self._size_var.set("" if pt is None else f"{pt:g}")

    def _apply_size(self):
        """Set the main number's font size on the selected tiles
        (empty field = back to the design's size)."""
        sel = self._sels()
        if not sel:
            return
        raw = self._size_var.get().strip().replace(",", ".")
        try:
            pt = float(raw) if raw else None
        except ValueError:
            return
        if pt is not None and pt <= 0:
            return
        for i in sel:
            self.items[i].segments[0].pt = pt
        self._refresh(select_range=(sel[0], sel[-1]))
        self._show_size()

    # ── designs ───────────────────────────────────────────────────────────
    def set_designs(self, names: list):
        """Preset names offered for new tiles (called when presets change)."""
        self._design_cb.configure(values=[_CURRENT] + list(names))
        if self._design_var.get() not in [_CURRENT] + list(names):
            self._design_var.set(_CURRENT)

    def chosen_design(self) -> str:
        """Preset name picked for NEW tiles ('' = the design being edited)."""
        v = self._design_var.get()
        return "" if v == _CURRENT else v

    def _apply_design(self):
        """Give the selected tiles the design picked in the selector."""
        sel = self._sels()
        if not sel:
            return
        design, name = self._get_design()
        for i in sel:
            self.items[i].design = copy.deepcopy(design)
            self.items[i].design_name = name
        self._refresh(select_range=(sel[0], sel[-1]))

    # ── state ─────────────────────────────────────────────────────────────
    def _refresh(self, select=None, select_range=None):
        self._lb.delete(0, "end")
        for i, it in enumerate(self.items):
            self._lb.insert(
                "end", f"{i + 1:>3}  {it.design_name[:16]:<16}  {describe(it)}")
        if select_range is not None and self.items:
            a, b = select_range
            self._lb.selection_set(a, b)
            self._lb.see(a)
        elif select is not None and self.items:
            idx = max(0, min(select, len(self.items) - 1))
            self._lb.selection_set(idx)
            self._lb.see(idx)
        designs = {(it.design_name, it.design.template_path)
                   for it in self.items}
        self._summary.configure(
            text=(_t("gmw.sheet_summary", n=len(self.items), d=len(designs))
                  if self.items else _t("gmw.sheet_empty")))
        self._on_change()

    def _sel(self):
        s = self._lb.curselection()
        return s[0] if s else None

    def _sels(self) -> list:
        return list(self._lb.curselection())

    @staticmethod
    def _apply_group(item: SheetItem, grp: dict):
        item.dx_mm = grp["dx_mm"]
        item.dy_mm = grp["dy_mm"]
        item.center_main = grp["center_main"]

    # ── actions ───────────────────────────────────────────────────────────
    def _add_range(self):
        new = self._make_range_items()
        self.items.extend(new)
        self._refresh(select=len(self.items) - 1)

    def _paste(self):
        """Create one tile per entry of a pasted list (clipboard prefilled)."""
        try:
            clip = self.clipboard_get()
        except tk.TclError:          # empty / non-text clipboard
            clip = ""
        dlg = PasteDialog(self.winfo_toplevel(), clip)
        self.wait_window(dlg)
        if not dlg.result:
            return
        labels, main_pt, sub_pt = dlg.result
        first = len(self.items)
        self.items.extend(self._make_label_items(
            labels, main_pt=main_pt, sub_pt=sub_pt))
        # leave the new tiles selected: one click on ✎ restyles them all
        self._refresh(select_range=(first, len(self.items) - 1))

    def _add_composite(self):
        dlg = SegmentEditor(self.winfo_toplevel(), self._make_seed_item(),
                            self._families, self._render_preview,
                            with_copies=True)
        self.wait_window(dlg)
        if not dlg.result:
            return
        segs, copies, grp = dlg.result
        seed = self._make_seed_item()
        for _ in range(copies):
            new = SheetItem(seed.design, copy.deepcopy(segs),
                            seed.design_name)
            self._apply_group(new, grp)
            self.items.append(new)
        self._refresh(select=len(self.items) - 1)

    def _edit(self):
        """Edit the selected tile; with several selected, restyle them all
        (texts untouched; run N's style is copied onto every tile's run N)."""
        sel = self._sels()
        if not sel:
            return
        first = self.items[sel[0]]
        dlg = SegmentEditor(self.winfo_toplevel(), first, self._families,
                            self._render_preview, bulk=len(sel) > 1)
        self.wait_window(dlg)
        if not dlg.result:
            return
        segs, _copies, grp = dlg.result
        if len(sel) == 1:
            first.segments = segs
            self._apply_group(first, grp)
        else:
            for idx in sel:
                item = self.items[idx]
                for seg, new in zip(item.segments, segs):
                    seg.pt, seg.dy_mm = new.pt, new.dy_mm
                    seg.tracking, seg.gap_mm = new.tracking, new.gap_mm
                    seg.color = new.color
                    seg.font_family = new.font_family
                    seg.font_style = new.font_style
                self._apply_group(item, grp)
        self._refresh(select_range=(sel[0], sel[-1]) if len(sel) > 1
                      else None, select=sel[0])

    def _move(self, d):
        i = self._sel()
        if i is None or not 0 <= i + d < len(self.items):
            return
        self.items[i], self.items[i + d] = self.items[i + d], self.items[i]
        self._refresh(select=i + d)

    def _remove(self):
        sel = self._sels()
        if not sel:
            return
        for i in reversed(sel):
            del self.items[i]
        self._refresh(select=sel[0])

    def _clear(self):
        self.items.clear()
        self._refresh()
