"""Reusable Tkinter layout primitives.

Note: several helpers below (label, btn, entry, combo, make_scrolled_list)
capture theme colours/fonts as default parameter values. Python evaluates
defaults once, at function-definition time — this mirrors the original
monolith's behaviour. Callers that need the *current* theme value after a
language/font change must pass it explicitly (e.g. font=theme.FONT_BODY)
rather than rely on the default.
"""

import tkinter as tk
from tkinter import ttk

from app.theme import (
    BG, BG2, BG3, BG4, BORDER, ACCENT, ACCENT2, FG, FG3, FG_ON_ACCENT,
    FONT_BODY, FONT_LABEL, FONT_SM, FONT_MONO,
)


def styled_frame(parent, bg=None, **kw):
    return tk.Frame(parent, bg=bg or BG, **kw)


def label(parent, text, font=FONT_BODY, fg=FG, bg=None, **kw):
    return tk.Label(parent, text=text, font=font, fg=fg, bg=bg or BG, **kw)


def btn(parent, text, cmd, primary=True, **kw):
    bg    = ACCENT if primary else BG3
    fg    = FG_ON_ACCENT if primary else FG
    hover = ACCENT2 if primary else "#333333"
    b = tk.Button(parent, text=text, command=cmd,
                  bg=bg, fg=fg, font=FONT_BODY,
                  relief="flat", bd=0, padx=14, pady=7,
                  activebackground=hover, activeforeground=fg,
                  cursor="hand2", **kw)
    b.bind("<Enter>", lambda e, _b=b, _h=hover: _b.config(bg=_h))
    b.bind("<Leave>", lambda e, _b=b, _bg=bg:   _b.config(bg=_bg))
    return b


def entry(parent, textvariable=None, width=30, **kw):
    return tk.Entry(parent, textvariable=textvariable, width=width,
                    bg=BG3, fg=FG, insertbackground=FG,
                    relief="flat", font=FONT_BODY, bd=4, **kw)


def _blend_hex(c1, c2, t=0.18):
    """Blend colour c2 into c1 by factor t (0=all c1, 1=all c2)."""
    r1,g1,b1 = int(c1[1:3],16), int(c1[3:5],16), int(c1[5:7],16)
    r2,g2,b2 = int(c2[1:3],16), int(c2[3:5],16), int(c2[5:7],16)
    r = int(r1*(1-t)+r2*t)
    g = int(g1*(1-t)+g2*t)
    b = int(b1*(1-t)+b2*t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _dpi_scale(win) -> float:
    """Return the display's DPI scale factor (1.0 = 96 DPI / 100%).

    Window sizes in this codebase are hand-tuned in logical pixels assuming
    a 96 DPI display. Now that the process is marked DPI-aware (see
    ImageTool.py entry point), Tk stops relying on Windows' own bitmap
    upscaling to make those windows look bigger — so on a >100%-scaled
    display they'd render undersized/clipped unless we scale them here.
    winfo_fpixels('1i') returns real pixels-per-inch regardless of DPI
    awareness, which is why it's used instead of winfo_screenwidth (a
    logical, not physical, pixel count)."""
    try:
        return win.winfo_fpixels("1i") / 96.0
    except Exception:
        return 1.0


def scaled_geometry(win, w: int, h: int, x: int = None, y: int = None):
    """Apply a DPI-scaled `w x h[+x+y]` geometry to a Toplevel/Tk window.

    Use this instead of a hand-written win.geometry(f"{w}x{h}") wherever a
    window size is a fixed logical-pixel constant — see _dpi_scale."""
    s = _dpi_scale(win)
    sw, sh = round(w * s), round(h * s)
    if x is None or y is None:
        win.geometry(f"{sw}x{sh}")
    else:
        win.geometry(f"{sw}x{sh}+{round(x*s)}+{round(y*s)}")


def scaled_minsize(win, w: int, h: int):
    """DPI-scaled equivalent of win.minsize(w, h) — see _dpi_scale."""
    s = _dpi_scale(win)
    win.minsize(round(w * s), round(h * s))


def _center_win(win):
    """Centre a Toplevel (or Tk) on the screen."""
    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    win.geometry(f"+{x}+{y}")


def _arrow_scale(sc: "tk.Scale"):
    """Allow arrow-key control on any Scale: click it, then ←/→/↑/↓ steps ±1."""
    sc.configure(takefocus=True)
    def _step(n):
        lo = float(sc.cget("from"))
        hi = float(sc.cget("to"))
        sc.set(max(lo, min(hi, sc.get() + n)))
        return "break"
    sc.bind("<Left>",  lambda e: _step(-1))
    sc.bind("<Right>", lambda e: _step(+1))
    sc.bind("<Up>",    lambda e: _step(+1))
    sc.bind("<Down>",  lambda e: _step(-1))


def separator(parent):
    return tk.Frame(parent, bg=BG3, height=1)


def _enable_mousewheel_scroll(canvas: tk.Canvas):
    """Make mouse-wheel scroll 'canvas' regardless of which child widget the
    pointer is over.  Call once after the canvas + its children exist."""
    def _scroll(event):
        # Linux sends Button-4/5, Windows sends <MouseWheel> with delta
        delta = -1 if event.num == 5 else (1 if event.num == 4
                else -1 * (event.delta // 120))
        canvas.yview_scroll(delta, "units")

    def _bind_tree(widget):
        widget.bind("<MouseWheel>",  _scroll, add="+")
        widget.bind("<Button-4>",    _scroll, add="+")
        widget.bind("<Button-5>",    _scroll, add="+")
        for child in widget.winfo_children():
            _bind_tree(child)

    _bind_tree(canvas)
    # Also watch for new children added later (e.g. FontPreview rows)
    canvas._mw_bind_tree = _bind_tree   # expose so callers can re-run after rebuild


def combo(parent, var, values, width=20):
    cb = ttk.Combobox(parent, textvariable=var, values=values,
                      width=width, state="readonly", font=FONT_BODY)
    cb.configure(style="Custom.TCombobox")
    return cb


def scrollbar(parent, orient="vertical", command=None):
    """Return a themed ttk.Scrollbar (Custom.Vertical/Horizontal.TScrollbar,
    configured once globally in ImageTool._apply_style) instead of a native
    tk.Scrollbar, which renders in the OS light theme and breaks visual
    consistency inside a dark UI."""
    style = "Custom.Vertical.TScrollbar" if orient == "vertical" \
        else "Custom.Horizontal.TScrollbar"
    return ttk.Scrollbar(parent, orient=orient, command=command, style=style)


def make_scrolled_list(parent, height=14):
    frame = styled_frame(parent)
    sb    = scrollbar(parent=frame, orient="vertical")
    lb    = tk.Listbox(frame, bg=BG2, fg=FG, font=FONT_MONO,
                       selectbackground=ACCENT, selectforeground=FG,
                       height=height, relief="flat", bd=0,
                       yscrollcommand=sb.set, activestyle="none")
    sb.config(command=lb.yview)
    lb.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return frame, lb


def section_title(parent, text, bg=BG2, **kw):
    """A "━ Title" sub-section heading inside a settings panel.

    Replaces the sec() helper duplicated locally in GuideMarkWizard's
    Step 1 and Step 2 builders — same look, single definition.
    """
    return tk.Label(parent, text=text, bg=bg, fg=FG3, font=FONT_LABEL, **kw)


def property_row(parent, label_text, build_fn, bg=BG2, label_width=14):
    """A "label : widget" settings row.

    Replaces the row() helper duplicated locally in GuideMarkWizard's
    Step 1 and Step 2 builders. `build_fn(row_frame)` packs the actual
    input widget(s) into the row — inputs stay wizard-specific closures,
    only the row scaffolding is a shared primitive.
    """
    r = tk.Frame(parent, bg=bg)
    if label_text:
        tk.Label(r, text=label_text, bg=bg, fg=FG, font=FONT_SM,
                 width=label_width, anchor="e").pack(side="left", padx=(0, 7))
    build_fn(r)
    return r


def grid_row(parent, label_text, build_fn, bg=None, label_width=16):
    """A "label | control" settings row laid out on a real 2-column grid,
    so every row in a section lines up regardless of how wide its control
    content is — unlike property_row, whose control side is free-packed and
    drifts row to row. `build_fn(control_frame)` fills the control column;
    pack widgets inside it with side="left" as usual, the column itself
    handles the stretch/alignment.

    Use column 1's `sticky="ew"` + the caller's own columnconfigure(1,
    weight=1) (set once per parent, see GuideMarkWizard._build_step1) to
    let controls grow when the panel widens instead of staying pinned to
    their initial pack width.
    """
    bg = bg or BG2
    r = tk.Frame(parent, bg=bg)
    r.columnconfigure(1, weight=1)
    if label_text:
        tk.Label(r, text=label_text, bg=bg, fg=FG, font=FONT_SM,
                 width=label_width, anchor="e").grid(row=0, column=0,
                                                      padx=(0, 7), sticky="e")
    ctrl = tk.Frame(r, bg=bg)
    ctrl.grid(row=0, column=1, sticky="ew")
    build_fn(ctrl)
    return r


def collapsible_section(parent, title, bg=None, start_open=False):
    """A "▸ Title" header that toggles a content frame open/closed.

    Replaces the _adv_btn/_adv_frame/_toggle_adv pattern duplicated locally
    in GuideMarkWizard. Returns (header_button, content_frame, toggle_fn) —
    pack content_frame's children as usual, call toggle_fn() to flip state
    programmatically (e.g. from a keyboard shortcut) if ever needed.
    """
    bg = bg or BG2
    state = {"open": start_open}
    header = tk.Button(parent, bg=bg, fg=FG3, relief="flat",
                       font=("Segoe UI", 8, "bold"), cursor="hand2", anchor="w")
    content = tk.Frame(parent, bg=bg)

    def _render():
        header.config(text=("▾  " if state["open"] else "▸  ") + title)
        if state["open"]:
            content.pack(fill="x")
        else:
            content.pack_forget()

    def toggle():
        state["open"] = not state["open"]
        _render()

    header.config(command=toggle)
    _render()
    return header, content, toggle


def card(parent, selected=False, width=170, height=220, bg=None):
    """A selectable "card" container (template/preset thumbnail tile).

    Border is BORDER at rest (visible outline, was invisible BG3-on-BG3
    before) and ACCENT when selected — a beginner must see a clickable
    tile before hovering it, not only after.
    """
    bg = bg or BG3
    outer = tk.Frame(parent, bg=bg, cursor="hand2", highlightthickness=3,
                     highlightbackground=ACCENT if selected else BORDER)
    inner = tk.Frame(outer, bg=bg, width=width, height=height)
    inner.pack_propagate(False)
    inner.pack()
    return outer, inner
