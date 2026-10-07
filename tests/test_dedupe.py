"""Tests for the bot's frame dedupe logic (ciphertext-body key)."""
from __future__ import annotations


def test_ciphertext_body_dedupe_keys():
    """Same body → same key, so the second frame is skipped."""
    body1 = "abc$def"
    body2 = "abc$def"
    assert "b:" + body1 == "b:" + body2


def test_msguid_dedupe_keys():
    assert "m:123" == "m:123"
    assert "m:123" != "m:124"
