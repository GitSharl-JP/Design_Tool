"""Pure, UI-free logic for file collection and rename-name computation.

No Tkinter/Pillow imports here on purpose — these functions must stay
testable without spinning up a GUI.
"""

import re
from pathlib import Path


def collect_files(folder, exts, recurse):
    """Return sorted, de-duplicated files under `folder` matching `exts`.

    `exts` is a list of glob patterns (e.g. ["*.jpg", "*.png"]); an empty
    list means "match everything". Used by Rename/Compress/Resize/Clipboard.
    """
    folder = Path(folder)
    files  = []
    globs  = exts if exts else ["*.*"]
    for g in globs:
        if recurse:
            files += sorted(folder.rglob(g))
        else:
            files += sorted(folder.glob(g))
    seen = set(); unique = []
    for f in files:
        if f in seen or not f.is_file(): continue
        seen.add(f); unique.append(f)
    return sorted(unique, key=lambda p: str(p).lower())


def first_digits(name):
    m = re.search(r'\d+', name)
    return m.group() if m else None


def compute_new_name(path: Path, cfg: dict, counter_ref: list) -> str:
    """Compute the renamed filename for `path` given a rename config dict.

    `counter_ref` is a single-element list used as a mutable int counter
    across calls (mode "3" increments it in place).
    """
    base = path.stem
    ext  = path.suffix

    mode = cfg.get("num_mode", "1")
    if mode == "2":
        d = first_digits(base)
        base = d if d else base
    elif mode == "3":
        w = cfg.get("pad_width", 3)
        base = str(counter_ref[0]).zfill(w)
        counter_ref[0] += 1
    elif mode == "4":
        d = first_digits(base)
        if d:
            idx = int(d) - cfg.get("map_start", 1)
            nm  = cfg.get("name_map", [])
            base = nm[idx] if 0 <= idx < len(nm) else base
        # keep base as-is if no digit

    find = cfg.get("find_text", "")
    repl = cfg.get("replace_text", "")
    if find:
        base = base.replace(find, repl)

    return cfg.get("prefix","") + base + cfg.get("suffix","") + ext
