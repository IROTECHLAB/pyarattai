"""Tests for the adaptive E2EE routing logic."""
from __future__ import annotations

from pyarattai.client import ArattaiClient
from pyarattai.session import Session


def _make_client():
    """Minimal client for testing routing logic (no network)."""
    s = Session()
    blob = {"uid": "100", "device_id": "1", "registration_id": "2"}
    return ArattaiClient(s, blob)


def test_e2ee_cache_starts_empty():
    c = _make_client()
    assert c._e2ee_chats == {}


def test_remember_e2ee():
    c = _make_client()
    c._remember_e2ee("X-GC", True)
    assert c._e2ee_chats["X-GC"] is True
    c._remember_e2ee("X-GC", False)
    assert c._e2ee_chats["X-GC"] is False


def test_is_e2ee_chat_cached():
    c = _make_client()
    c._remember_e2ee("A", True)
    assert c._is_e2ee_chat("A") is True
    assert c._is_e2ee_chat("B") is False  # unknown → False
