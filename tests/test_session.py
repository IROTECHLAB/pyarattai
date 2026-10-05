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
    s = Session()
    s._install_cookies({"iamcsr": "first"})
    s._install_cookies({"iamcsr": "second"})
    # should NOT raise CookieConflictError
    val = s._find_cookie("iamcsr")
    assert val in {"first", "second"}


def test_csrf_headers():
    s = Session()
    s._install_cookies({"iamcsr": "A", "CT_CSRF_TOKEN": "B"})
    assert s._csrf_header()["X-ZCSRF-TOKEN"] == "iamcsrcoo=A"
    assert s._zcsrf_header()["X-ZCSRF-TOKEN"] == "zchat_csrparam=B"


def test_csrf_headers_missing():
    s = Session()
    assert s._csrf_header()["X-ZCSRF-TOKEN"] == "iamcsrcoo="
    assert s._zcsrf_header()["X-ZCSRF-TOKEN"] == "zchat_csrparam="


def test_request_unknown_base():
    s = Session()
    with pytest.raises(ValueError):
        s.request("GET", "/x", base="nope")
