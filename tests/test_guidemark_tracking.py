"""Non-regression test for GuideMark's tracking-vs-DPI scaling.

Context: tracking (letter spacing between digits) was stored as a raw
pixel count applied directly during tile rendering, with no relation to
the render DPI — unlike every other GuideMark setting (font size, position,
margins), which is authored in physical units (pt/mm/%) and converted
proportionally to DPI. This made the same tracking value produce wildly
different PHYSICAL (mm) spacing depending on the resolution a tile was
rendered at: the Step 2 layout preview renders at ~36-72 DPI while a real
print export uses the configured DPI (300 by default), so the same stored
value looked several times wider in that preview than in the actual
printed output — see ImageTool.py's _render_tile / _scale_tracking.

_scale_tracking() fixes this by treating the stored value as pixels at a
fixed reference DPI (GuideMarkWizard.TRACKING_REF_DPI, 150 — chosen because
Step 1's tile preview already rendered at 150 DPI and was already visually
faithful to a real export) and rescaling it to whatever DPI a given render
actually uses, so the physical spacing stays constant regardless of DPI.
"""

import pytest

import ImageTool as it


def test_tracking_unchanged_at_reference_dpi():
    assert it._scale_tracking(10, dpi=150, ref_dpi=150) == 10


def test_tracking_scales_up_with_dpi():
    # Doubling the render DPI should double the pixel tracking, so the
    # physical (mm) spacing stays the same.
    assert it._scale_tracking(10, dpi=300, ref_dpi=150) == 20


def test_tracking_scales_down_with_dpi():
    assert it._scale_tracking(10, dpi=75, ref_dpi=150) == 5


def test_tracking_zero_stays_zero_at_any_dpi():
    for dpi in (36, 72, 150, 300, 600):
        assert it._scale_tracking(0, dpi=dpi, ref_dpi=150) == 0


def test_physical_spacing_is_stable_across_dpi():
    """The core guarantee: the same stored tracking must correspond to the
    same physical (mm) spacing at any render DPI, within rounding error."""
    ref_dpi = 150
    stored_tracking = 10  # as saved in a preset

    def physical_mm(dpi):
        px = it._scale_tracking(stored_tracking, dpi, ref_dpi)
        return px / dpi * 25.4

    reference_mm = physical_mm(ref_dpi)
    # GuideMark's DPI field ranges 72-600 (see the Advanced spinbox in
    # _build_step1) — that's the range this guarantee must hold tightly
    # over. 36 DPI only ever occurs as Step 2's absolute floor at very low
    # configured DPI and carries much coarser whole-pixel rounding (e.g.
    # 10px scaled to 36 DPI rounds to the nearest 0.68mm), so it gets a
    # looser tolerance instead of being excluded — the old unscaled
    # behaviour was off by a factor of several times, not a rounding
    # fraction, and this still catches that class of regression.
    for dpi, tol in ((72, 0.05), (150, 0.05), (300, 0.05), (600, 0.05), (36, 0.35)):
        assert physical_mm(dpi) == pytest.approx(reference_mm, rel=tol)


def test_negative_tracking_scales_consistently():
    assert it._scale_tracking(-10, dpi=300, ref_dpi=150) == -20
