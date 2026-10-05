from __future__ import annotations

from pyarattai.errors import APIError


def test_api_error_str_includes_status_and_url():
    e = APIError("boom", status_code=500, url="https://x/y")
    s = str(e)
    assert "boom" in s
    assert "500" in s
    assert "https://x/y" in s
