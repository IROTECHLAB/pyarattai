"""Tests for SignalBridge helpers (no crypto needed)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pyarattai.signal import (
    MARK_DECRYPT, MARK_SEND, MARK_KEYGEN,
    _extract_marked,
    VENDOR_DIR,
)


def test_markers():
    assert MARK_DECRYPT.startswith("PYA_")
    assert MARK_SEND.startswith("PYA_")
    assert MARK_KEYGEN.startswith("PYA_")


def test_extract_marked_plain():
    stdout = "noise\nmore noise\n" + MARK_SEND + '{"ok":true}'
    assert _extract_marked(stdout, MARK_SEND) == '{"ok":true}'


def test_extract_marked_missing():
    assert _extract_marked("no marker here", MARK_SEND) is None


def test_vendor_dir_exists():
    assert VENDOR_DIR.exists()
    # At least libsignal-protocol.js should be there
    assert (VENDOR_DIR / "libsignal-protocol.js").exists()


def test_vendor_has_all_scripts():
    """The package must ship every Node helper."""
    for name in ("keygen.cjs", "decrypt.cjs", "send.cjs"):
        assert (VENDOR_DIR / name).exists(), f"missing vendor/{name}"
