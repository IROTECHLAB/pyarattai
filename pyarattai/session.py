"""HTTP session wrapper: cookie management, CSRF headers, retries."""
from __future__ import annotations

import json
import os
import time
from http.cookiejar import Cookie, CookieJar
from typing import Any, Dict, Iterable, List, Optional, Union

import requests

from .constants import (
    AUTH_BASE,
    AUTH_HEADERS,
    CHAT_BASE,
    CHAT_HEADERS,
    FILES_BASE,
    FILES_HEADERS,
    MAX_RETRIES,
    RETRY_BACKOFF,
)
from .errors import APIError, NetworkError, RateLimitError

__all__ = ["Session"]


_BASE_MAP = {"auth": AUTH_BASE, "chat": CHAT_BASE, "files": FILES_BASE}
_HEADER_MAP = {"auth": AUTH_HEADERS, "chat": CHAT_HEADERS, "files": FILES_HEADERS}


class Session:
    """Thin wrapper around :class:`requests.Session`.

    Adds cookie helpers, CSRF header construction, retry/backoff, and
    uniform error mapping to :class:`APIError` / :class:`NetworkError`.
    """

    def __init__(self) -> None:
        self.http = requests.Session()
        self.http.cookies = CookieJar()
        # ensure duplicate cookie values don't confuse us
        self.http.cookies.clear()

    # ------------------------------------------------------------------ cookies

    def _install_cookies(
        self,
        cookies: Union[Dict[str, str], Iterable[Dict[str, Any]]],
        domain: str = ".arattai.in",
    ) -> None:
        """Install cookies into the jar.

        Args:
            cookies: Either a ``{name: value}`` mapping, or an iterable of
                dicts with at least ``name`` and ``value`` (plus optional
                ``domain``, ``path``, ``secure``).
            domain: Default domain applied when not set on the cookie.
        """
        if isinstance(cookies, dict):
            items: List[Dict[str, Any]] = [
                {"name": k, "value": v} for k, v in cookies.items()
            ]
        else:
            items = list(cookies)

        for item in items:
            name = item.get("name")
            value = item.get("value")
            if not name or value is None:
                continue
            # Preserve the domain that the server originally set.
            # Sending iamcsr to web.arattai.in/_wms/* causes the WMS
            # endpoint to reject the request and skip minting
            # x-tkp-token, so we must not flatten scoped cookies.
            raw_domain = item.get("domain") or domain
            domain_str = str(raw_domain)
            c = Cookie(
                version=0,
                name=str(name),
                value=str(value),
                port=None,
                port_specified=False,
                domain=domain_str,
                domain_specified=bool(domain_str),
                domain_initial_dot=domain_str.startswith("."),
                path=item.get("path") or "/",
                path_specified=True,
                secure=bool(item.get("secure", True)),
                expires=item.get("expires"),
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
            self.http.cookies.set_cookie(c)

    def _find_cookie(self, name: str) -> Optional[str]:
        """Return the value of the first cookie with ``name`` (or None).

        Never raises :class:`requests.cookies.CookieConflictError`.
        """
        jar: CookieJar = self.http.cookies
        try:
            return jar.get(name)
        except Exception:  # noqa: BLE001 - CookieConflictError & friends
            pass
        for c in jar:
            if c.name == name:
                return c.value
        return None

    def _all_cookies(self) -> Dict[str, str]:
        """Return a flat dict of ``name -> value`` (last wins)."""
        out: Dict[str, str] = {}
        for c in self.http.cookies:
            out[c.name] = c.value
        return out

    # ------------------------------------------------------------------ headers

    def _csrf_header(self) -> Dict[str, str]:
        """Header used for ``accounts.arattai.in`` endpoints."""
        from urllib.parse import quote as _q
        iamcsr = self._find_cookie("iamcsr") or ""
        return {"X-ZCSRF-TOKEN": f"iamcsrcoo={_q(iamcsr)}"}

    def _zcsrf_header(self) -> Dict[str, str]:
        """Header used for ``web.arattai.in`` chat endpoints."""
        token = self._find_cookie("CT_CSRF_TOKEN") or ""
        return {"X-ZCSRF-TOKEN": f"zchat_csrparam={token}"}

    # ------------------------------------------------------------------ request

    def request(
        self,
        method: str,
        path: str,
        *,
        base: str = "chat",
        raw: bool = False,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> Any:
        """Perform an HTTP request against one of the Arattai bases.

        Args:
            method: HTTP verb.
            path: Path (may be a full URL when ``base='files'`` and a
                different host is needed).
            base: One of ``"auth"``, ``"chat"``, ``"files"``.
            raw: When True, do not parse JSON; return the response object.
            timeout: Per-request timeout in seconds.
            **kwargs: Passed to :mod:`requests`.

        Returns:
            Parsed JSON (dict/list) or the :class:`requests.Response`
            when ``raw=True``.

        Raises:
            APIError: on 4xx/5xx (after retries).
            NetworkError: on transport failures.
        """
        if base not in _BASE_MAP:
            raise ValueError(f"Unknown base: {base!r}")

        url = path if path.startswith("http") else _BASE_MAP[base].rstrip("/") + path

        headers: Dict[str, str] = dict(_HEADER_MAP[base])
        headers.update(kwargs.pop("headers", {}) or {})
        headers["client-time"] = str(int(time.time() * 1000))

        # Only inject CSRF headers when we actually have the cookie.
        # ZGS returns 400 if X-ZCSRF-TOKEN is sent with an empty value
        # on the very first /signin GET.
        if base == "auth":
            if self._find_cookie("iamcsr"):
                headers.update(self._csrf_header())
        elif base == "chat":
            if self._find_cookie("CT_CSRF_TOKEN"):
                headers.update(self._zcsrf_header())
            # Arattai expects device identity headers on every chat call
            # (see arattai-all.py chat_session()).
            dev_id = self._find_cookie("device_id") or None
            # Read from a session-level attribute if present
            if getattr(self, "_device_id", None):
                headers["X-Device-Id"] = str(self._device_id)
            if getattr(self, "_registration_id", None):
                headers["X-Registration-Id"] = str(self._registration_id)

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.http.request(
                    method, url, headers=headers, timeout=timeout, **kwargs
                )
            except requests.RequestException as e:
                last_exc = e
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
                    continue
                raise NetworkError(str(e)) from e

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
                    continue

            if raw:
                if not resp.ok:
                    raise APIError(
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                        url=url,
                        payload=resp.text,
                    )
                return resp

            if not resp.ok:
                payload: Any
                try:
                    payload = resp.json()
                except ValueError:
                    payload = resp.text

                # Detect Arattai's application-level throttle which
                # returns 400 with {"code":"request_limit_exceeded"}.
                if isinstance(payload, dict) and (
                    payload.get("code") == "request_limit_exceeded"
                    or "throttle" in str(payload.get("message", "")).lower()
                ):
                    raise RateLimitError(
                        f"Arattai throttled this endpoint: {payload.get('message')}"
                    )

                raise APIError(
                    f"HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    url=url,
                    payload=payload,
                )

            if not resp.content:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "json" not in ctype.lower():
                return resp.text
            try:
                return resp.json()
            except json.JSONDecodeError:
                return resp.text

        raise NetworkError(str(last_exc) if last_exc else "unknown network error")
