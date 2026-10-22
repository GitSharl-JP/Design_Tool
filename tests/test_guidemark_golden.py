"""Pixel non-regression for GuideMark's layout + tile rendering.

GuideMark output is printed and sent to museums/clients, so the mm/px
engine must never change silently. The goldens in tests/golden/guidemark/
were captured from the ORIGINAL monolith engine before it was extracted to
app/engines/. Regenerate them (only on purpose) with:

    .venv\\Scripts\\python.exe -m tests.test_guidemark_golden --regen

Goldens depend on Pillow/FreeType rasterisation; if a Pillow upgrade makes
every scenario differ by antialiasing only, regenerate after eyeballing.
"""

import os
import sys

import pytest
from PIL import Image, ImageChops

from tests import gm_harness as h

pytestmark = pytest.mark.skipif(
    not os.path.isfile(h.FONT_PATH), reason="arial.ttf not available")


def _render(name, tmp_dir):
    tpl = h.make_template(os.path.join(tmp_dir, "tpl.png"))
    w = h.make_wizard(tpl, **h.SCENARIOS[name])
    return w._generate_layout()


def _golden_path(name, i):
    return os.path.join(h.GOLDEN_DIR, f"{name}_p{i + 1}.png")


@pytest.mark.parametrize("name", list(h.SCENARIOS))
def test_layout_matches_golden(name, tmp_path):
    pages = _render(name, str(tmp_path))
    for i, page in enumerate(pages):
        gp = _golden_path(name, i)
        assert os.path.isfile(gp), f"missing golden {gp} (run --regen)"
        ref = Image.open(gp).convert("RGB")
        assert page.size == ref.size
        assert ImageChops.difference(page, ref).getbbox() is None, (
            f"{name} page {i + 1} differs from golden")
    # no extra golden page => same page count
    assert not os.path.isfile(_golden_path(name, len(pages)))


def _regen():
    import tempfile
    os.makedirs(h.GOLDEN_DIR, exist_ok=True)
    for name in h.SCENARIOS:
        with tempfile.TemporaryDirectory() as td:
            for i, page in enumerate(_render(name, td)):
                page.save(_golden_path(name, i), optimize=True)
                print("wrote", _golden_path(name, i))


if __name__ == "__main__" and "--regen" in sys.argv:
    _regen()
