"""Register/unregister the "Renommer avec Image Tool" right-click entry
in the Windows Explorer context menu.

Uses HKEY_CURRENT_USER\\Software\\Classes\\*\\shell\\ImageToolRename --
per-user, no admin rights required, fully reversible by a normal user.
This module is winreg-only logic (importable/testable without a GUI);
run it directly to get a small Tk toggle window.
"""

import sys

try:
    import winreg
except ImportError:
    winreg = None  # allows import on non-Windows for testing the pure logic paths

_VERB_KEY = r"Software\Classes\*\shell\ImageToolRename"
_COMMAND_KEY = _VERB_KEY + r"\command"
_MENU_LABEL = "Renommer avec Image Tool"


def _exe_command() -> str:
    """The command line to store in the registry. Points at the frozen
    .exe when packaged (sys.frozen), otherwise at the current Python
    interpreter + this script's sibling ImageTool.py (dev-only path,
    not meant to be handed to a colleague without a Python environment)."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}" --rename-macro %*'
    import os
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ImageTool.py")
    return f'"{sys.executable}" "{script}" --rename-macro %*'


def is_registered(reg=winreg) -> bool:
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, _COMMAND_KEY) as key:
            reg.QueryValue(key, None)
        return True
    except FileNotFoundError:
        return False


def register(reg=winreg) -> None:
    with reg.CreateKey(reg.HKEY_CURRENT_USER, _VERB_KEY) as key:
        reg.SetValue(key, "", reg.REG_SZ, _MENU_LABEL)
    with reg.CreateKey(reg.HKEY_CURRENT_USER, _COMMAND_KEY) as key:
        reg.SetValue(key, "", reg.REG_SZ, _exe_command())


def unregister(reg=winreg) -> None:
    # DeleteKey does not recurse: the command sub-key must go first.
    try:
        reg.DeleteKey(reg.HKEY_CURRENT_USER, _COMMAND_KEY)
    except FileNotFoundError:
        pass
    try:
        reg.DeleteKey(reg.HKEY_CURRENT_USER, _VERB_KEY)
    except FileNotFoundError:
        pass


def _run_toggle_window():
    import tkinter as tk
    from app.theme import BG, BG2, FG, FONT_H2, FONT_BODY
    from app.ui_kit import styled_frame, label, btn

    root = tk.Tk()
    root.title("Image Tool — Menu contextuel")
    root.configure(bg=BG)

    pad = styled_frame(root, bg=BG)
    pad.pack(padx=24, pady=20)

    label(pad, "Clic-droit « Renommer avec Image Tool »",
          font=FONT_H2, bg=BG).pack(anchor="w", pady=(0, 12))

    status_lbl = label(pad, "", font=FONT_BODY, bg=BG)
    status_lbl.pack(anchor="w", pady=(0, 12))

    def _refresh():
        if is_registered():
            status_lbl.config(text="État actuel : activé")
            action_btn.config(text="Désactiver", command=_do_unregister)
        else:
            status_lbl.config(text="État actuel : désactivé")
            action_btn.config(text="Activer", command=_do_register)

    def _do_register():
        register()
        _refresh()

    def _do_unregister():
        unregister()
        _refresh()

    action_btn = btn(pad, "", None, primary=True)
    action_btn.pack(anchor="w")

    _refresh()
    root.mainloop()


if __name__ == "__main__":
    if winreg is None:
        print("register_context_menu.py requires Windows (winreg unavailable).")
        sys.exit(1)
    _run_toggle_window()
