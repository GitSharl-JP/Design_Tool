from pathlib import Path

import pytest

from app.engines import font_jp
from app.engines.font_jp import (
    font_supports_japanese,
    get_jp_support,
    load_jp_support_cache,
    save_jp_support_cache,
)

WINDOWS_FONTS = Path(r"C:\Windows\Fonts")
MEIRYO   = WINDOWS_FONTS / "meiryo.ttc"
ARIAL    = WINDOWS_FONTS / "arial.ttf"


# ── font_supports_japanese ───────────────────────────────────────────────────

def test_font_supports_japanese_true_for_real_jp_font():
    if not MEIRYO.exists():
        pytest.skip("meiryo.ttc not present on this machine")
    assert font_supports_japanese(str(MEIRYO)) is True


def test_font_supports_japanese_false_for_latin_only_font():
    if not ARIAL.exists():
        pytest.skip("arial.ttf not present on this machine")
    assert font_supports_japanese(str(ARIAL)) is False


def test_font_supports_japanese_false_for_missing_file(tmp_path):
    assert font_supports_japanese(str(tmp_path / "does_not_exist.ttf")) is False


# ── cache round-trip ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_in_memory_cache():
    """The module keeps an in-memory mirror of the on-disk cache — clear it
    between tests so each test's tmp_path cache file is read fresh."""
    font_jp._JP_SUPPORT_CACHE = {}
    yield
    font_jp._JP_SUPPORT_CACHE = {}


def test_save_then_load_round_trips(tmp_path):
    cache_file = tmp_path / "_font_cache.json"
    font_jp._JP_SUPPORT_CACHE = {"some/font.ttf": True}

    save_jp_support_cache(cache_file)
    font_jp._JP_SUPPORT_CACHE = {}  # force a fresh read from disk
    result = load_jp_support_cache(cache_file)

    assert result == {"some/font.ttf": True}


def test_save_preserves_other_keys_in_cache_file(tmp_path):
    import json
    cache_file = tmp_path / "_font_cache.json"
    cache_file.write_text(
        json.dumps({"families": {"Arial": {"Regular": "x"}}, "flat": {}, "file_count": 1}),
        encoding="utf-8",
    )

    font_jp._JP_SUPPORT_CACHE = {"a.ttf": False}
    save_jp_support_cache(cache_file)

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["families"] == {"Arial": {"Regular": "x"}}
    assert data["jp_support"] == {"a.ttf": False}


def test_get_jp_support_uses_cache_without_recomputing(tmp_path, monkeypatch):
    cache_file = tmp_path / "_font_cache.json"
    font_jp._JP_SUPPORT_CACHE = {"cached/font.ttf": True}

    calls = []
    monkeypatch.setattr(font_jp, "font_supports_japanese",
                        lambda path: calls.append(path) or False)

    result = get_jp_support(cache_file, "cached/font.ttf")

    assert result is True
    assert calls == []  # never recomputed — served straight from the cache


def test_get_jp_support_computes_and_caches_on_miss(tmp_path, monkeypatch):
    cache_file = tmp_path / "_font_cache.json"
    monkeypatch.setattr(font_jp, "font_supports_japanese", lambda path: True)

    result = get_jp_support(cache_file, "new/font.ttf")

    assert result is True
    assert font_jp._JP_SUPPORT_CACHE["new/font.ttf"] is True


def test_get_jp_support_persist_false_skips_disk_write(tmp_path, monkeypatch):
    cache_file = tmp_path / "_font_cache.json"
    monkeypatch.setattr(font_jp, "font_supports_japanese", lambda path: True)

    get_jp_support(cache_file, "new/font.ttf", persist=False)

    assert not cache_file.exists()
