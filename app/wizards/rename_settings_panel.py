"""Rename settings panel: prefix/suffix, numbering mode, find & replace,
skip filter. Shared between the classic RenameWizard (folder-scanned files)
and the right-click macro -- both need the exact same settings UI, only
their file source differs.
"""

import re
import tkinter as tk

from app.theme import BG, BG2, BG3, ACCENT, FG, FG2, FONT_H2, FONT_BODY, FONT_SM, FONT_MONO
from app.ui_kit import styled_frame, label, entry, separator


class RenameSettingsPanel(tk.Frame):
    """A scrollable panel with all rename settings. Call get_cfg() to read
    the current values as the dict expected by RenameJob/compute_new_name."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb     = tk.Scrollbar(self, command=canvas.yview, bg=BG2)
        canvas.config(yscrollcommand=sb.set)
        inner  = styled_frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.config(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Prefix / suffix
        label(inner, "Prefix / Suffix", font=FONT_H2).pack(anchor="w", pady=(0, 6))
        r1 = styled_frame(inner); r1.pack(fill="x", pady=2)
        label(r1, "Prefix:").pack(side="left")
        self._prefix = entry(r1, width=20); self._prefix.pack(side="left", padx=6)
        label(r1, "Suffix:").pack(side="left", padx=(12, 0))
        self._suffix = entry(r1, width=20); self._suffix.pack(side="left", padx=6)

        separator(inner).pack(fill="x", pady=10)

        # Number mode
        label(inner, "Number handling", font=FONT_H2).pack(anchor="w", pady=(0, 6))
        self._num_mode = tk.StringVar(value="1")
        modes = [
            ("1", "Keep original filename as-is"),
            ("2", "Keep only the first digits  (e.g.  03_2nd  →  03)"),
            ("3", "Replace with counter  (001, 002, …)"),
            ("4", "Map file numbers to a custom name list"),
        ]
        for val, txt in modes:
            tk.Radiobutton(inner, text=txt, variable=self._num_mode, value=val,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, font=FONT_BODY,
                           command=self._refresh_num_opts).pack(anchor="w", pady=1)

        self._num_opts = styled_frame(inner)
        self._num_opts.pack(anchor="w", fill="x", pady=(4, 0), padx=20)

        # counter opts
        self._ctr_frame = styled_frame(self._num_opts)
        r = styled_frame(self._ctr_frame); r.pack(fill="x")
        label(r, "Start at:").pack(side="left")
        self._ctr_start = entry(r, width=6); self._ctr_start.insert(0, "1")
        self._ctr_start.pack(side="left", padx=4)
        label(r, "Zero-pad width:").pack(side="left", padx=(10, 0))
        self._ctr_pad = entry(r, width=4); self._ctr_pad.insert(0, "3")
        self._ctr_pad.pack(side="left", padx=4)

        # map opts
        self._map_frame = styled_frame(self._num_opts)
        label(self._map_frame, "Name list (comma-separated):", fg=FG2, font=FONT_SM).pack(anchor="w")
        self._name_map_txt = tk.Text(self._map_frame, bg=BG3, fg=FG, font=FONT_MONO,
                                     height=3, relief="flat", bd=4,
                                     insertbackground=FG)
        self._name_map_txt.pack(fill="x", pady=2)
        label(self._map_frame, "Example:  E1-1, E1-2, E2-1, E2-2", fg=FG2, font=FONT_SM).pack(anchor="w")
        r2 = styled_frame(self._map_frame); r2.pack(fill="x", pady=4)
        label(r2, "File numbering starts at:").pack(side="left")
        self._map_start = entry(r2, width=6); self._map_start.insert(0, "1")
        self._map_start.pack(side="left", padx=4)

        separator(inner).pack(fill="x", pady=10)

        # Find/replace
        label(inner, "Find & Replace  (optional)", font=FONT_H2).pack(anchor="w", pady=(0, 6))
        r3 = styled_frame(inner); r3.pack(fill="x", pady=2)
        label(r3, "Find:").pack(side="left")
        self._find = entry(r3, width=20); self._find.pack(side="left", padx=6)
        label(r3, "Replace with:").pack(side="left", padx=(12, 0))
        self._replace = entry(r3, width=20); self._replace.pack(side="left", padx=6)

        separator(inner).pack(fill="x", pady=10)

        # Skip
        label(inner, "Skip files  (optional)", font=FONT_H2).pack(anchor="w", pady=(0, 6))
        r4 = styled_frame(inner); r4.pack(fill="x", pady=2)
        label(r4, "Skip filenames containing:").pack(side="left")
        self._skip = entry(r4, width=24); self._skip.pack(side="left", padx=6)

        self._refresh_num_opts()

    def _refresh_num_opts(self):
        for w in self._num_opts.winfo_children():
            w.pack_forget()
        m = self._num_mode.get()
        if m == "3": self._ctr_frame.pack(fill="x")
        if m == "4": self._map_frame.pack(fill="x")

    def get_cfg(self) -> dict:
        nm = self._num_mode.get()
        name_map = [x.strip() for x in
                    re.split(r'[,、]', self._name_map_txt.get("1.0", "end"))
                    if x.strip()]
        try: start = int(self._ctr_start.get())
        except Exception: start = 1
        try: pad = int(self._ctr_pad.get())
        except Exception: pad = 3
        try: map_start = int(self._map_start.get())
        except Exception: map_start = 1
        return {
            "prefix":       self._prefix.get(),
            "suffix":       self._suffix.get(),
            "num_mode":     nm,
            "start_num":    start,
            "pad_width":    pad,
            "name_map":     name_map,
            "map_start":    map_start,
            "find_text":    self._find.get(),
            "replace_text": self._replace.get(),
            "skip":         self._skip.get().strip(),
        }
