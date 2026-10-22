"""Parse a pasted list of tile labels (Excel column, mail, free text).

    "1, 5 14-1\n55\t100"  ->  [["1"], ["5"], ["14", "-1"], ["55"], ["100"]]

Entries are separated by newlines, tabs, spaces, commas, semicolons or
"、". A number followed by a suffix (``14-1``, ``14a``, ``14-A``) becomes a
two-run label (big number + small suffix) so it can be drawn with two
sizes; anything else is kept as a single run. Duplicates are kept on
purpose (the same tile can be printed several times). Ranges ("1-15") are
NOT expanded here — the Numbers field does that — so "14-1" stays a label.
"""

import re
import unicodedata

_SEPARATORS = re.compile(r"[\s,;、]+")
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})
_SUFFIXED = re.compile(r"^(\d+)(-[0-9A-Za-z]+|[A-Za-z]+)$")


def parse_labels(text: str) -> list:
    """List of runs-lists, one per pasted entry (see module docstring)."""
    text = unicodedata.normalize("NFKC", text or "").translate(_DASHES)
    out = []
    for token in _SEPARATORS.split(text):
        if not token:
            continue
        m = _SUFFIXED.match(token)
        out.append([m.group(1), m.group(2)] if m else [token])
    return out
