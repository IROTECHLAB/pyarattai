"""Tests for pyarattai.session."""
from __future__ import annotations

import pytest

from pyarattai.session import Session


def test_install_and_find_cookie():
    s = Session()
    s._install_cookies({"iamcsr": "abc", "CT_CSRF_TOKEN": "xyz"})
    assert s._find_cookie("iamcsr") == "abc"
    assert s._find_cookie("CT_CSRF_TOKEN") == "xyz"
    assert s._find_cookie("nope") is None


def test_find_cookie_with_duplicates():
    """Duplicate names must not raise CookieConflictError."""
    s = Session()
    s._install_cookies({"iamcsr": "first"})
    s._install_cookies({"iamcsr": "second"})
    val = s._find_cookie("iamcsr")
    assert val in {"first", "second"}


def test_find_cookie_preserves_domain():
    """Cookies keep their original domain (needed for accounts.arattai.in)."""
    s = Session()
    s._install_cookies([
        {"name": "iamcsr", "value": "A",
         "domain": "accounts.arattai.in", "path": "/"},
        {"name": "CT_CSRF_TOKEN", "value": "B",
         "domain": "web.arattai.in", "path": "/"},
    ])
    domains = {c.name: c.domain for c in s.http.cookies}
    assert domains["iamcsr"] == "accounts.arattai.in"
    assert domains["CT_CSRF_TOKEN"] == "web.arattai.in"


def test_csrf_headers():
    s = Session()
    s._install_cookies({"iamcsr": "A", "CT_CSRF_TOKEN": "B"})
    assert s._csrf_header()["X-ZCSRF-TOKEN"].startswith("iamcsrcoo=")
    assert s._zcsrf_header()["X-ZCSRF-TOKEN"].startswith("zchat_csrparam=")


def test_csrf_headers_missing():
    s = Session()
    assert "iamcsrcoo=" in s._csrf_header()["X-ZCSRF-TOKEN"]
    assert "zchat_csrparam=" in s._zcsrf_header()["X-ZCSRF-TOKEN"]


def test_request_unknown_base():
    s = Session()
    with pytest.raises(ValueError):
        s.request("GET", "/x", base="nope")
