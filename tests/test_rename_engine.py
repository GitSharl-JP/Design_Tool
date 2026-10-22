from pathlib import Path

import pytest

from app.engines.rename_engine import collect_files, first_digits, compute_new_name


# ── collect_files ────────────────────────────────────────────────────────────

def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_collect_files_filters_by_extension(tmp_path):
    _touch(tmp_path / "a.jpg")
    _touch(tmp_path / "b.png")
    _touch(tmp_path / "c.txt")

    result = collect_files(tmp_path, ["*.jpg"], recurse=False)

    assert [p.name for p in result] == ["a.jpg"]


def test_collect_files_empty_exts_matches_everything(tmp_path):
    _touch(tmp_path / "a.jpg")
    _touch(tmp_path / "b.png")

    result = collect_files(tmp_path, [], recurse=False)

    assert {p.name for p in result} == {"a.jpg", "b.png"}


def test_collect_files_non_recursive_ignores_subfolders(tmp_path):
    _touch(tmp_path / "top.jpg")
    _touch(tmp_path / "sub" / "nested.jpg")

    result = collect_files(tmp_path, ["*.jpg"], recurse=False)

    assert [p.name for p in result] == ["top.jpg"]


def test_collect_files_recursive_includes_subfolders(tmp_path):
    _touch(tmp_path / "top.jpg")
    _touch(tmp_path / "sub" / "nested.jpg")

    result = collect_files(tmp_path, ["*.jpg"], recurse=True)

    assert {p.name for p in result} == {"top.jpg", "nested.jpg"}


def test_collect_files_deduplicates_when_extensions_overlap(tmp_path):
    _touch(tmp_path / "a.jpg")

    # "*.jpg" and "*.JPG" both match the same file on a case-insensitive
    # filesystem (Windows) -- must not appear twice.
    result = collect_files(tmp_path, ["*.jpg", "*.jpg"], recurse=False)

    assert len(result) == 1


def test_collect_files_sorted_case_insensitively(tmp_path):
    _touch(tmp_path / "Banana.jpg")
    _touch(tmp_path / "apple.jpg")

    result = collect_files(tmp_path, ["*.jpg"], recurse=False)

    assert [p.name for p in result] == ["apple.jpg", "Banana.jpg"]


def test_collect_files_ignores_directories_matching_glob(tmp_path):
    _touch(tmp_path / "real.jpg")
    (tmp_path / "fake.jpg").mkdir()  # a directory that happens to match the glob

    result = collect_files(tmp_path, ["*.jpg"], recurse=False)

    assert [p.name for p in result] == ["real.jpg"]


# ── first_digits ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("photo123.jpg", "123"),
    ("001_intro", "001"),
    ("no_digits_here", None),
    ("a1b2", "1"),  # first run of digits only
])
def test_first_digits(name, expected):
    assert first_digits(name) == expected


# ── compute_new_name ──────────────────────────────────────────────────────────

def test_compute_new_name_mode1_keeps_original_stem():
    path = Path("photo.jpg")
    cfg = {"num_mode": "1", "prefix": "IMG_", "suffix": "_v2"}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "IMG_photo_v2.jpg"


def test_compute_new_name_mode2_extracts_digits():
    path = Path("scan_042_final.jpg")
    cfg = {"num_mode": "2"}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "042.jpg"


def test_compute_new_name_mode2_falls_back_when_no_digits():
    path = Path("cover.jpg")
    cfg = {"num_mode": "2"}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "cover.jpg"


def test_compute_new_name_mode3_counter_increments_across_calls():
    cfg = {"num_mode": "3", "pad_width": 3}
    counter = [1]

    first  = compute_new_name(Path("a.jpg"), cfg, counter)
    second = compute_new_name(Path("b.jpg"), cfg, counter)

    assert first == "001.jpg"
    assert second == "002.jpg"
    assert counter == [3]


def test_compute_new_name_mode4_maps_digit_to_name():
    path = Path("item_2.jpg")
    cfg = {"num_mode": "4", "map_start": 1, "name_map": ["first", "second", "third"]}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "second.jpg"


def test_compute_new_name_mode4_out_of_range_keeps_base():
    path = Path("item_99.jpg")
    cfg = {"num_mode": "4", "map_start": 1, "name_map": ["first"]}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "item_99.jpg"


def test_compute_new_name_find_replace_applied_to_base():
    path = Path("draft_final.jpg")
    cfg = {"num_mode": "1", "find_text": "draft", "replace_text": "release"}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "release_final.jpg"


def test_compute_new_name_preserves_extension_case():
    path = Path("photo.JPG")
    cfg = {"num_mode": "1"}

    result = compute_new_name(path, cfg, counter_ref=[1])

    assert result == "photo.JPG"
