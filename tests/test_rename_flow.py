from pathlib import Path

import pytest

from app.engines.rename_flow import RenameJob


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


# ── preview() ─────────────────────────────────────────────────────────────────

def test_preview_basic_rows(tmp_path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    _touch(f1); _touch(f2)
    job = RenameJob([f1, f2], {"num_mode": "1", "prefix": "IMG_"})

    rows = job.preview()

    assert rows == [("a.jpg", "→", "IMG_a.jpg"), ("b.jpg", "→", "IMG_b.jpg")]


def test_preview_applies_skip_filter(tmp_path):
    f1 = tmp_path / "keep.jpg"
    f2 = tmp_path / "draft_skip.jpg"
    _touch(f1); _touch(f2)
    job = RenameJob([f1, f2], {"num_mode": "1", "skip": "draft"})

    rows = job.preview()

    assert [r[0] for r in rows] == ["keep.jpg"]


def test_preview_flags_name_collisions(tmp_path):
    f1 = tmp_path / "photo_1.jpg"
    f2 = tmp_path / "photo_1_copy.jpg"
    _touch(f1); _touch(f2)
    # mode "2" extracts first digit run -> both compute to "1.jpg"
    job = RenameJob([f1, f2], {"num_mode": "2"})

    rows = job.preview()

    assert rows[0][2] == rows[1][2] == "1.jpg"
    assert all("collision" in r[1] for r in rows)


def test_preview_no_collision_when_names_differ(tmp_path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    _touch(f1); _touch(f2)
    job = RenameJob([f1, f2], {"num_mode": "1"})

    rows = job.preview()

    assert all(r[1] == "→" for r in rows)


# ── execute() ─────────────────────────────────────────────────────────────────

def test_execute_renames_files_on_disk(tmp_path):
    f = tmp_path / "photo.jpg"
    _touch(f)
    job = RenameJob([f], {"num_mode": "1", "prefix": "IMG_"})

    success, failed, errors = job.execute()

    assert success == 1
    assert failed == 0
    assert errors == []
    assert not f.exists()
    assert (tmp_path / "IMG_photo.jpg").exists()


def test_execute_reports_success_and_failure_counts(tmp_path):
    ok = tmp_path / "ok.jpg"
    _touch(ok)
    missing = tmp_path / "missing.jpg"  # never created -> rename fails
    job = RenameJob([ok, missing], {"num_mode": "1"})

    success, failed, errors = job.execute()

    assert success == 1
    assert failed == 1
    assert len(errors) == 1
    assert errors[0][0] == missing
    assert isinstance(errors[0][1], OSError)


def test_execute_calls_log_cb_for_each_file(tmp_path):
    f = tmp_path / "a.jpg"
    _touch(f)
    job = RenameJob([f], {"num_mode": "1", "prefix": "X_"})
    logs = []

    job.execute(log_cb=lambda msg, tag: logs.append((msg, tag)))

    assert len(logs) == 1
    assert logs[0][1] == "ok"
    assert "X_a.jpg" in logs[0][0]


def test_execute_respects_skip_filter(tmp_path):
    keep = tmp_path / "keep.jpg"
    skip = tmp_path / "draft_skip.jpg"
    _touch(keep); _touch(skip)
    job = RenameJob([keep, skip], {"num_mode": "1", "skip": "draft", "prefix": "X_"})

    success, failed, errors = job.execute()

    assert success == 1
    assert (tmp_path / "X_keep.jpg").exists()
    assert skip.exists()  # untouched, still original name


def test_execute_friendly_error_message_on_permission_denied(tmp_path, monkeypatch):
    f = tmp_path / "locked.jpg"
    _touch(f)
    job = RenameJob([f], {"num_mode": "1"})
    logs = []

    def _raise_permission_error(self, target):
        raise PermissionError("WinError 32: file in use")

    monkeypatch.setattr(Path, "rename", _raise_permission_error)

    success, failed, errors = job.execute(log_cb=lambda msg, tag: logs.append((msg, tag)))

    assert success == 0
    assert failed == 1
    assert "verrouillé ou accès refusé" in logs[0][0]
