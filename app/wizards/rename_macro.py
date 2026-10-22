"""Right-click macro entry point: a standalone 2-screen window
(Settings -> Preview/Confirm) that renames files handed in directly by
Explorer -- no folder browsing, no copy-to-local step. Shares its
settings UI and rename engine with the classic RenameWizard via
RenameSettingsPanel and RenameJob.
"""

import tkinter as tk
from pathlib import Path

from app.theme import BG, BG2, FG_YELLOW, FONT_H1, FONT_SM, FG2
from app.ui_kit import styled_frame, label, btn, separator
from app.widgets.wizard_widgets import WizardPanel, PreviewList, LogPanel
from app.wizards.rename_settings_panel import RenameSettingsPanel
from app.engines.rename_flow import RenameJob


class RenameMacroWizard(tk.Toplevel):
    def __init__(self, parent, files: list[Path]):
        super().__init__(parent)
        self.title("Image Tool — Renommer")
        self.configure(bg=BG)
        self.geometry("760x620")
        self._files = files
        self._cfg = None
        self._build()
        self._show_screen(0)

    def _build(self):
        hdr = styled_frame(self)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        label(hdr, f"Rename — {len(self._files)} fichier(s) sélectionné(s)",
              font=FONT_H1).pack(side="left")
        self._step_lbl = label(hdr, "", fg=FG2, font=FONT_SM)
        self._step_lbl.pack(side="right")
        separator(self).pack(fill="x", padx=20, pady=10)

        self._content = styled_frame(self)
        self._content.pack(fill="both", expand=True, padx=20)
        separator(self).pack(fill="x", padx=20, pady=10)

        nav = styled_frame(self)
        nav.pack(fill="x", padx=20, pady=(0, 16))
        self._back_btn = btn(nav, "← Retour", self._go_back, primary=False)
        self._next_btn = btn(nav, "Aperçu →", self._go_next, primary=True)
        self._next_btn.pack(side="right")

        self._screen0 = self._build_screen0()
        self._screen1 = self._build_screen1()
        self._screens = [self._screen0, self._screen1]

    # ── Screen 0: Settings ───────────────────────────────────────────────
    def _build_screen0(self):
        p = WizardPanel(self._content)
        self._settings_panel = RenameSettingsPanel(p)
        self._settings_panel.pack(fill="both", expand=True)
        return p

    # ── Screen 1: Preview / Confirmation ─────────────────────────────────
    def _build_screen1(self):
        p = WizardPanel(self._content)

        self._preview = PreviewList(p)
        self._preview.pack(fill="both", expand=True)

        self._warn_lbl = label(
            p, "⚠  Renommer est irréversible. Vérifiez la liste ci-dessus, "
               "puis cliquez sur Exécuter pour continuer.",
            fg=FG_YELLOW, font=FONT_SM)
        self._warn_lbl.pack(anchor="w", pady=(10, 0))

        self._log = LogPanel(p)
        # LogPanel is created but hidden until execution replaces the
        # preview with a result summary -- keeps this a 2-screen flow
        # (Done is a state of screen 1, not a 3rd screen).

        return p

    # ── Navigation ────────────────────────────────────────────────────────
    def _show_screen(self, idx):
        for s in self._screens:
            s.hide()
        self._screens[idx].show()
        self._step_lbl.config(text=f"Étape {idx + 1}/2")
        self._back_btn.pack_forget()
        if idx == 0:
            self._next_btn.config(text="Aperçu →", command=self._go_next, state="normal")
        else:
            self._back_btn.pack(side="left")
            self._next_btn.config(text="Exécuter", command=self._go_execute, state="normal")

    def _go_next(self):
        self._cfg = self._settings_panel.get_cfg()
        rows = RenameJob(self._files, self._cfg).preview()
        self._preview.pack(fill="both", expand=True)
        self._log.pack_forget()
        self._warn_lbl.pack(anchor="w", pady=(10, 0))
        self._preview.load(rows)
        self._show_screen(1)

    def _go_back(self):
        self._show_screen(0)

    def _go_execute(self):
        self._preview.pack_forget()
        self._warn_lbl.pack_forget()
        self._log.pack(fill="both", expand=True)
        self._log.clear()

        job = RenameJob(self._files, self._cfg)
        success, failures, _errors = job.execute(log_cb=self._log.log)
        self._log.log(f"\n  Terminé — {success} fichier(s) renommé(s).", "ok")
        if failures:
            self._log.log(f"  {failures} fichier(s) en échec.", "err")

        self._back_btn.pack_forget()
        self._next_btn.config(text="Fermer", command=self._close, state="normal")

    def _close(self):
        self.master.destroy()


def launch_macro(paths: list[Path]):
    """Standalone entry point for --rename-macro: opens the macro window
    alone, with no HomeScreen behind it."""
    root = tk.Tk()
    root.withdraw()
    wizard = RenameMacroWizard(root, paths)
    wizard.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
