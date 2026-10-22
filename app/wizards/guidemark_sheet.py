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
from app.i18n import _t
from app.theme import (
    BG, BG2, BG3, FG, FG2, FG3, FONT_BODY, FONT_HINT, FONT_SM,
    ACCENT, FG_ON_ACCENT)
from app.ui_kit import btn, scrollbar

_PREVIEW_PX = 220


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
    = ``(segments, copies)`` or ``None`` when cancelled."""

    def __init__(self, parent, item: SheetItem, families: list,
                 render_preview=None, with_copies=False):
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

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        for col, (key, w) in enumerate((
                ("gmw.seg_text", 10), ("gmw.seg_font", 16), ("gmw.seg_pt", 6),
                ("gmw.seg_dy", 7), ("gmw.seg_trk", 6))):
            tk.Label(head, text=_t(key), bg=BG, fg=FG2, font=FONT_HINT,
                     width=w, anchor="w").grid(row=0, column=col, padx=2)
        self._rows_frame = tk.Frame(body, bg=BG)
        self._rows_frame.pack(fill="x", pady=(2, 6))
        for seg in item.segments:
            self._add_row(seg)

        btn(body, _t("gmw.seg_add"), lambda: self._add_row(Segment("")),
            primary=False).pack(anchor="w", pady=(0, 8))
        tk.Label(body, text=_t("gmw.seg_hint"), bg=BG, fg=FG3,
                 font=FONT_HINT, justify="left").pack(anchor="w")

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
            "color": seg.color, "style": seg.font_style,
        }
        f = tk.Frame(self._rows_frame, bg=BG)
        f.grid(row=r, column=0, sticky="w", pady=2)
        tk.Entry(f, textvariable=v["text"], width=10, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat",
                 font=FONT_BODY).grid(row=0, column=0, padx=2)
        ttk.Combobox(f, textvariable=v["font"], values=[""] + self._families,
                     width=16, style="Custom.TCombobox",
                     font=FONT_SM).grid(row=0, column=1, padx=2)
        _spin(f, v["pt"], 4, 999, w=5).grid(row=0, column=2, padx=2)
        _spin(f, v["dy"], -50, 50, inc=0.5, w=6,
              fmt="%.1f").grid(row=0, column=3, padx=2)
        _spin(f, v["trk"], -50, 200, w=5).grid(row=0, column=4, padx=2)
        tk.Button(f, text="✕", bg=BG3, fg=FG, relief="flat", cursor="hand2",
                  command=lambda fr=f: self._del_row(fr)).grid(
                      row=0, column=5, padx=(6, 0))
        # A Spinbox snaps an empty textvariable to its minimum on creation;
        # empty means "inherit", so restore the intended values.
        v["pt"].set("" if seg.pt is None else f"{seg.pt:g}")
        v["trk"].set("" if seg.tracking is None else str(seg.tracking))
        for key in ("text", "font", "pt", "dy", "trk"):
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
        item = SheetItem(self._item.design, segs, self._item.design_name)
        img = self._render_preview(item)
        img.thumbnail((_PREVIEW_PX, _PREVIEW_PX))
        self._preview_tk = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        self._canvas.create_image(_PREVIEW_PX // 2, _PREVIEW_PX // 2,
                                  image=self._preview_tk)

    def _ok(self):
        segs = self._segments()
        if segs:
            self.result = (segs, max(1, self._copies.get()))
        self.destroy()


class SheetPanel(tk.Frame):
    """List of tiles making up the sheet + add/edit/reorder controls."""

    def __init__(self, parent, *, make_range_items, make_seed_item,
                 families, render_preview, on_change, bg=BG2):
        super().__init__(parent, bg=bg)
        self.items: list = []
        self._make_range_items = make_range_items
        self._make_seed_item = make_seed_item
        self._families = families
        self._render_preview = render_preview
        self._on_change = on_change

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x")
        btn(bar, _t("gmw.sheet_add_range"), self._add_range,
            primary=False).pack(fill="x", pady=(0, 4))
        btn(bar, _t("gmw.sheet_add_composite"), self._add_composite,
            primary=False).pack(fill="x")

        box = tk.Frame(self, bg=bg)
        box.pack(fill="both", expand=True, pady=(8, 0))
        self._lb = tk.Listbox(box, height=7, bg=BG3, fg=FG, relief="flat",
                              selectbackground=ACCENT,
                              selectforeground=FG_ON_ACCENT,
                              activestyle="none", exportselection=False,
                              font=FONT_SM)
        sb = scrollbar(box, orient="vertical", command=self._lb.yview)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._lb.bind("<Double-Button-1>", lambda e: self._edit())

        acts = tk.Frame(self, bg=bg)
        acts.pack(fill="x", pady=(6, 0))
        for text, cmd in (("✎", self._edit), ("▲", lambda: self._move(-1)),
                          ("▼", lambda: self._move(1)),
                          ("✕", self._remove), (_t("gmw.sheet_clear"),
                                                self._clear)):
            tk.Button(acts, text=text, bg=BG3, fg=FG, relief="flat",
                      cursor="hand2", font=FONT_SM, padx=8,
                      command=cmd).pack(side="left", padx=(0, 4))
        self._summary = tk.Label(self, bg=bg, fg=FG2, font=FONT_HINT,
                                 anchor="w", justify="left", wraplength=320)
        self._summary.pack(fill="x", pady=(6, 0))
        self._refresh()

    # ── state ─────────────────────────────────────────────────────────────
    def _refresh(self, select=None):
        self._lb.delete(0, "end")
        for i, it in enumerate(self.items):
            self._lb.insert(
                "end", f"{i + 1:>3}  {it.design_name[:16]:<16}  {describe(it)}")
        if select is not None and self.items:
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

    # ── actions ───────────────────────────────────────────────────────────
    def _add_range(self):
        new = self._make_range_items()
        self.items.extend(new)
        self._refresh(select=len(self.items) - 1)

    def _add_composite(self):
        dlg = SegmentEditor(self.winfo_toplevel(), self._make_seed_item(),
                            self._families, self._render_preview,
                            with_copies=True)
        self.wait_window(dlg)
        if not dlg.result:
            return
        segs, copies = dlg.result
        seed = self._make_seed_item()
        for _ in range(copies):
            self.items.append(SheetItem(seed.design, copy.deepcopy(segs),
                                        seed.design_name))
        self._refresh(select=len(self.items) - 1)

    def _edit(self):
        i = self._sel()
        if i is None:
            return
        dlg = SegmentEditor(self.winfo_toplevel(), self.items[i],
                            self._families, self._render_preview)
        self.wait_window(dlg)
        if dlg.result:
            self.items[i].segments = dlg.result[0]
            self._refresh(select=i)

    def _move(self, d):
        i = self._sel()
        if i is None or not 0 <= i + d < len(self.items):
            return
        self.items[i], self.items[i + d] = self.items[i + d], self.items[i]
        self._refresh(select=i + d)

    def _remove(self):
        i = self._sel()
        if i is None:
            return
        del self.items[i]
        self._refresh(select=i)

    def _clear(self):
        self.items.clear()
        self._refresh()
