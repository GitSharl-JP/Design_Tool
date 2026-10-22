"""Tests for register_context_menu.py's pure winreg logic, using a fake
winreg backend so no real registry key is ever touched in CI."""

import pytest

import register_context_menu as rcm


class _FakeKeyHandle:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinReg:
    """Minimal in-memory stand-in for the winreg module surface used by
    register_context_menu.py: CreateKey/OpenKey/SetValue/QueryValue/
    DeleteKey against a single HKEY_CURRENT_USER namespace."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = "REG_SZ"

    def __init__(self):
        self.keys: dict[str, str] = {}  # path -> default value

    def CreateKey(self, root, path):
        self.keys.setdefault(path, "")
        return _FakeKeyHandle(self, path)

    def OpenKey(self, root, path):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return _FakeKeyHandle(self, path)

    def SetValue(self, key_handle, sub, type_, value):
        key_handle._store.keys[key_handle._path] = value

    def QueryValue(self, key_handle, sub):
        return key_handle._store.keys[key_handle._path]

    def DeleteKey(self, root, path):
        if path not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[path]


@pytest.fixture
def fake_reg():
    return FakeWinReg()


def test_is_registered_false_when_key_absent(fake_reg):
    assert rcm.is_registered(reg=fake_reg) is False


def test_register_creates_verb_and_command_keys(fake_reg):
    rcm.register(reg=fake_reg)

    assert rcm._VERB_KEY in fake_reg.keys
    assert fake_reg.keys[rcm._VERB_KEY] == rcm._MENU_LABEL
    assert rcm._COMMAND_KEY in fake_reg.keys
    assert "--rename-macro" in fake_reg.keys[rcm._COMMAND_KEY]
    assert "%*" in fake_reg.keys[rcm._COMMAND_KEY]


def test_is_registered_true_after_register(fake_reg):
    rcm.register(reg=fake_reg)

    assert rcm.is_registered(reg=fake_reg) is True


def test_unregister_removes_both_keys(fake_reg):
    rcm.register(reg=fake_reg)

    rcm.unregister(reg=fake_reg)

    assert rcm._VERB_KEY not in fake_reg.keys
    assert rcm._COMMAND_KEY not in fake_reg.keys
    assert rcm.is_registered(reg=fake_reg) is False


def test_unregister_is_idempotent_when_never_registered(fake_reg):
    # must not raise even though neither key exists
    rcm.unregister(reg=fake_reg)

    assert rcm.is_registered(reg=fake_reg) is False


def test_register_is_idempotent_when_called_twice(fake_reg):
    rcm.register(reg=fake_reg)
    rcm.register(reg=fake_reg)

    assert rcm.is_registered(reg=fake_reg) is True
    assert len(fake_reg.keys) == 2
