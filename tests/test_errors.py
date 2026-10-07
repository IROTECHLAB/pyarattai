"""Tests for pyarattai.errors."""
from __future__ import annotations

import pytest

from pyarattai.errors import APIError, ArattaiError, NetworkError


def test_api_error_str_includes_status_and_url():
    e = APIError("boom", status_code=500, url="https://x/y")
    s = str(e)
    assert "boom" in s
    assert "500" in s
    assert "https://x/y" in s


def test_hierarchy():
    assert issubclass(APIError, ArattaiError)
    assert issubclass(NetworkError, ArattaiError)


def test_api_error_with_payload():
    e = APIError("bad", status_code=400, payload={"code": "X"})
    assert e.payload == {"code": "X"}
