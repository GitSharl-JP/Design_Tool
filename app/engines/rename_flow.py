"""Shared rename preview/execution logic, used by both the classic
RenameWizard (folder-scanned files) and the right-click macro (files
handed in directly by Explorer). The only difference between the two
callers is how they obtain their `files: list[Path]` -- this module owns
everything from there on.
"""

from pathlib import Path

from app.engines.rename_engine import compute_new_name


def _friendly_error(exc: Exception) -> str:
    """Map a raw OSError/rename failure to a message a non-programmer
    can act on, instead of Python's raw WinError text."""
    if isinstance(exc, PermissionError):
        return "fichier verrouillé ou accès refusé"
    if isinstance(exc, FileExistsError):
        return "un fichier porte déjà ce nom"
    if isinstance(exc, OSError):
        return str(exc)
    return str(exc)


class RenameJob:
    """cfg -> preview() -> execute(), decoupled from any Tkinter widget."""

    def __init__(self, files: list[Path], cfg: dict):
        self.files = files
        self.cfg = cfg

    def _filtered_files(self) -> list[Path]:
        skip = self.cfg.get("skip", "")
        if not skip:
            return list(self.files)
        return [f for f in self.files if skip not in f.name]

    def preview(self) -> list[tuple[str, str, str]]:
        """Return [(original_name, arrow, new_name), ...] and flag
        collisions: if two source files would land on the same target
        name, the arrow becomes a warning marker instead of "→" so the
        caller (PreviewList) can render it distinctly."""
        files = self._filtered_files()
        counter = [self.cfg.get("start_num", 1)]
        computed = [(f, compute_new_name(f, self.cfg, counter)) for f in files]

        seen: dict[str, int] = {}
        for _, new_name in computed:
            seen[new_name] = seen.get(new_name, 0) + 1

        rows = []
        for f, new_name in computed:
            arrow = "→" if seen[new_name] == 1 else "⚠ collision →"
            rows.append((f.name, arrow, new_name))
        return rows

    def execute(self, log_cb=None) -> tuple[int, int, list[tuple[Path, Exception]]]:
        """Rename every filtered file. log_cb(msg, tag) is called for each
        line if provided (tag in "ok"/"err", matching LogPanel). Returns
        (success_count, failure_count, [(path, exception), ...])."""
        files = self._filtered_files()
        counter = [self.cfg.get("start_num", 1)]
        success = 0
        failures: list[tuple[Path, Exception]] = []

        for f in files:
            try:
                new_name = compute_new_name(f, self.cfg, counter)
                f.rename(f.parent / new_name)
                if log_cb:
                    log_cb(f"  {f.name}  →  {new_name}", "ok")
                success += 1
            except Exception as e:
                if log_cb:
                    log_cb(f"  FAILED: {f.name} — {_friendly_error(e)}", "err")
                failures.append((f, e))

        return success, len(failures), failures
