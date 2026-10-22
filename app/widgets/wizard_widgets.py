"""Shared stateful widgets used across the wizard screens.

Unlike app/ui_kit.py (stateless layout primitives), these carry their own
state/behaviour (step navigation, list rendering, log buffer) and are
composed from ui_kit primitives.
"""

import tkinter as tk

from app.theme import (
    BG, BG2, BG3, BORDER, ACCENT, FG, FG2, FG_GREEN, FG_RED, FG_YELLOW,
    FG_ON_ACCENT, FONT_H2, FONT_LABEL, FONT_SM, FONT_MONO,
)
from app.ui_kit import styled_frame, label, separator, make_scrolled_list


class PreviewList(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        top = styled_frame(self)
        top.pack(fill="x")
        label(top, "Preview", font=FONT_H2).pack(side="left")
        self._count_lbl = label(top, "", font=FONT_SM, fg=FG2)
        self._count_lbl.pack(side="right")
        separator(self).pack(fill="x", pady=4)
        frm, self._lb = make_scrolled_list(self, height=14)
        frm.pack(fill="both", expand=True)

    def load(self, rows: list[tuple[str, str, str]]):
        """rows = [(original, arrow, new_name), ...]"""
        self._lb.delete(0, "end")
        for orig, arrow, new in rows:
            self._lb.insert("end", f"  {orig}   {arrow}   {new}")
        self._count_lbl.config(text=f"{len(rows)} file(s)")

    def clear(self):
        self._lb.delete(0, "end")
        self._count_lbl.config(text="")


class LogPanel(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        label(self, "Log", font=FONT_H2).pack(anchor="w")
        separator(self).pack(fill="x", pady=4)
        self._text = tk.Text(self, bg=BG2, fg=FG, font=FONT_MONO,
                             relief="flat", bd=0, state="disabled", height=12)
        sb = tk.Scrollbar(self, command=self._text.yview, bg=BG2)
        self._text.config(yscrollcommand=sb.set)
        self._text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._text.tag_config("ok",  foreground=FG_GREEN)
        self._text.tag_config("err", foreground=FG_RED)
        self._text.tag_config("warn",foreground=FG_YELLOW)
        self._text.tag_config("dim", foreground=FG2)

    def log(self, msg, tag=""):
        self._text.config(state="normal")
        self._text.insert("end", msg + "\n", tag)
        self._text.see("end")
        self._text.config(state="disabled")

    def clear(self):
        self._text.config(state="normal")
        self._text.delete("1.0","end")
        self._text.config(state="disabled")


class StepBar(tk.Frame):
    """Horizontal step indicator (e.g. "Design › Configure › Print").

    Each step reads clearly as done / active / upcoming: done steps get a
    checkmark, the active step gets a solid accent pill, upcoming steps stay
    muted — with generous padding so the bar doesn't read as cramped text.
    """
    def __init__(self, parent, steps, **kw):
        super().__init__(parent, bg=BG, padx=4, pady=10, **kw)
        self._steps = list(steps)
        self._labels = []
        for i, s in enumerate(steps):
            if i > 0:
                tk.Frame(self, bg=BORDER, width=28, height=1).pack(side="left")
            lbl = tk.Label(self, text=s, bg=BG, fg=FG2, font=FONT_SM,
                          padx=14, pady=6)
            lbl.pack(side="left")
            self._labels.append(lbl)

    def set_step(self, idx):
        for i, lbl in enumerate(self._labels):
            if i == idx:
                lbl.config(text=self._steps[i],
                          fg=FG_ON_ACCENT, bg=ACCENT, font=FONT_LABEL)
            elif i < idx:
                lbl.config(text="✓  " + self._steps[i], fg=FG_GREEN, bg=BG, font=FONT_SM)
            else:
                lbl.config(text=self._steps[i], fg=FG2, bg=BG, font=FONT_SM)


class WizardPanel(tk.Frame):
    """One step in a wizard. Call show()/hide()."""
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)

    def show(self): self.pack(fill="both", expand=True)
    def hide(self): self.pack_forget()
