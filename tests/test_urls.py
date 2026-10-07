"""Tests for constants (URLs, status codes)."""
from __future__ import annotations

from pyarattai.constants import (
    AUTH_BASE, CHAT_BASE, FILES_BASE,
    STATUS_IDLE, STATUS_TYPING,
)


def test_bases_are_https():
    assert AUTH_BASE.startswith("https://")
    assert CHAT_BASE.startswith("https://")
    assert FILES_BASE.startswith("https://")


def test_status_codes():
    assert STATUS_TYPING == "104"
    assert STATUS_IDLE == "105"
