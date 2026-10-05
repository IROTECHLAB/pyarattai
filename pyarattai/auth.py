"""Authentication flow for Arattai accounts."""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .constants import AUTH_BASE, CHAT_BASE  # noqa: F401
from .errors import AuthError, SessionError
from .session import Session

__all__ = ["Auth"]


_LOOKUP_QUERY = (
    "mode=primary&cli_time={ts}&servicename=Arattai"
    "&hiderightpanel=true&serviceurl=https%3A%2F%2Fweb.arattai.in"
)
_OTP_QUERY = (
    "digest={digest}&cli_time={ts}&servicename=Arattai"
    "&hiderightpanel=true&serviceurl=https%3A%2F%2Fweb.arattai.in"
)


class Auth:
    """Interactive authentication helper.

    Usage::

        s = Session()
        a = Auth(s)
        a.start()
        a.lookup("91XXXXXXXXXX")
        a.send_otp()
        a.verify_otp(input("OTP: "))
        a.fetch_session_meta()
    """

    def __init__(self, session: Optional[Session] = None) -> None:
        self.s = session or Session()
        self.identifier: Optional[str] = None
        self.digest: Optional[str] = None
        self.e_mobile: Optional[str] = None
        self.r_mobile: Optional[str] = None
        self.uid: Optional[str] = None
        self.mobile: Optional[str] = None
        self.device_id: Optional[str] = None
        self.registration_id: Optional[str] = None
        self.session_id: Optional[str] = None

    # ------------------------------------------------------------------ step 1

    def start(self) -> None:
        """Reproduce the known-working iam_session() call verbatim."""
        import requests as _rq
        from .constants import AUTH_BASE as _IAM

        _UA = (
            "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/119.0.6045.193 Mobile Safari/537.36"
        )
        _REF = (
            f"{_IAM}/signin?servicename=Arattai"
            "&serviceurl=https%3A%2F%2Fweb.arattai.in&QRLogin=false"
        )

        # Build a completely fresh requests.Session inside Auth.start()
        # to bypass any header/cookie pollution in self.s.
        fresh = _rq.Session()
        fresh.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": _IAM,
            "Referer": _REF,
        })
        r = fresh.get(
            f"{_IAM}/signin",
            params={
                "servicename": "Arattai",
                "serviceurl": "https://web.arattai.in",
                "QRLogin": "false",
            },
            timeout=20,
        )
        print(f"[auth.start] raw GET -> {r.status_code}")
        if r.status_code != 200:
            print(f"[auth.start] body: {r.text[:400]}")
            raise AuthError(f"signin {r.status_code}")

        # Copy cookies + headers into our main session so the rest of the
        # flow keeps working.
        for c in fresh.cookies:
            self.s.http.cookies.set_cookie(c)
        for k, v in fresh.headers.items():
            self.s.http.headers[k] = v

        if not self.s._find_cookie("iamcsr"):
            raise AuthError("signin returned 200 but no iamcsr cookie")

    def lookup(self, phone: str) -> Dict[str, Any]:
        """Look up a phone number. Params go in the form body, not the URL.

        Args:
            phone: Raw identifier (with or without ``-`` / ``+``); it is
                passed through unchanged to the server.

        Returns:
            The decoded lookup response.
        """
        import json as _json
        import time as _t

        # ZCSRF header must be present and quoted.
        csrf = self.s._csrf_header()["X-ZCSRF-TOKEN"]

        body = {
            "mode": "primary",
            "cli_time": str(int(_t.time() * 1000)),
            "servicename": "Arattai",
            "hiderightpanel": "true",
            "serviceurl": "https://web.arattai.in",
        }

        # Post to the base path (no query string beyond what we control).
        resp = self.s.request(
            "POST",
            f"/signin/v2/lookup/{phone}",
            base="auth",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "X-ZCSRF-TOKEN": csrf,
            },
        )

        data = resp if isinstance(resp, dict) else _json.loads(resp or "{}")
        lk = data.get("lookup") or {}
        self.identifier = lk.get("identifier")
        self.digest = lk.get("digest")
        otp = ((lk.get("modes") or {}).get("otp") or {}).get("data") or []
        if otp:
            self.e_mobile = otp[0].get("e_mobile")
            self.r_mobile = otp[0].get("r_mobile")
        if not (self.identifier and self.digest and self.e_mobile):
            raise AuthError(f"Lookup failed: {data}")
        return data

    def send_otp(self) -> Any:
        """Trigger the OTP SMS."""
        if not (self.identifier and self.digest and self.e_mobile):
            raise AuthError("Call lookup() first")
        ts = int(time.time() * 1000)
        url = (
            f"/signin/v2/primary/{self.identifier}/arotp/{self.e_mobile}?"
            + _OTP_QUERY.format(digest=self.digest, ts=ts)
        )
        return self.s.request(
            "POST",
            url,
            base="auth",
            json={"arotpauth": {"mode": "MOBILE"}},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    # ------------------------------------------------------------------ step 4

    def verify_otp(self, code: str, is_resend: bool = False) -> Any:
        """Submit the OTP code (or request a resend)."""
        if not (self.identifier and self.digest and self.e_mobile):
            raise AuthError("Call lookup() first")
        ts = int(time.time() * 1000)
        url = (
            f"/signin/v2/primary/{self.identifier}/otp/{self.e_mobile}?"
            + _OTP_QUERY.format(digest=self.digest, ts=ts)
        )
        payload = {"otpauth": {"code": code, "is_resend": is_resend, "mode": "MOBILE"}}
        if is_resend:
            payload = {"otpauth": {"is_resend": True, "mode": "MOBILE"}}
            return self.s.request(
                "POST", url, base="auth", json=payload,
                headers={"Content-Type": "application/json;charset=UTF-8"},
            )
        return self.s.request(
            "PUT", url, base="auth", json=payload,
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    # ------------------------------------------------------------------ post-login

    def establish_chat_session(self) -> None:
        """Hand off IAM cookies to web.arattai.in and let it issue chat cookies.

        The accounts.arattai.in session is not sufficient for chat
        endpoints. This hits ``https://web.arattai.in/`` with the IAM
        cookies; the redirect chain sets ``CT_CSRF_TOKEN`` and friends
        on ``.arattai.in`` so that ``/webclientsync.do`` and the rest of
        the chat API will accept the request.
        """
        from .constants import CHAT_BASE as _CB
        # The Referer/Accept here mimic a browser navigations, not XHR.
        self.s.request(
            "GET",
            "/",
            base="chat",
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Referer": f"{_CB}/",
            },
            raw=True,  # we don't care about the body
        )

    def mint_x_tkp_token(self) -> Optional[str]:
        """Mint the ``x-tkp-token`` cookie required for WS auth.

        The web client fetches this from ``/_wms/pconnect.sas`` after
        login; without it, ``wss://in1-wms.arattai.in/pconnect``
        immediately closes with ``mtype=-17`` (auth expired).

        Returns:
            The token value, or None if the server didn't set one.
        """
        import secrets as _sec
        from .constants import CHAT_BASE as _CB

        uid = self.uid or "0"
        tabid = f"{uid}_CT_{int(time.time()*1000)}_{1000 + _sec.randbelow(8999)}"
        params = "&".join([
            "settings=true", "prd=CT", f"uname={uid}", "samedomain=false",
            f"nocache={int(time.time()*1000)}", "config=111", "wmscont=_wms",
            "nodomainchange=true", "retrycount=1", f"tabid={tabid}",
            "staticdomain=static.arattaicdn.com",
            "staticversion=Apr_29_2026_13276743", "tokenpair=true",
            "frameorigin=https%3A%2F%2Fweb.arattai.in", "hash=2f147ab0",
        ])
        try:
            r = self.s.request(
                "GET",
                f"/_wms/pconnect.sas?{params}",
                base="chat",
                raw=True,
                headers={
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Referer": f"{_CB}/",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "iframe",
                },
            )
        except Exception as e:  # noqa: BLE001
            print(f"[auth.mint] request failed: {e}")
            return None

        tok = r.cookies.get("x-tkp-token")
        if tok:
            self.session_id = self.session_id or tok
            print(f"[auth.mint] x-tkp-token = {tok[:40]}...")
        else:
            print(f"[auth.mint] no x-tkp-token in Set-Cookie; status={r.status_code}")
        return tok

    def fetch_session_meta(self) -> None:
        """Fetch uid/device/registration ids and X-SID session id."""
        # 1. Hand off to chat server BEFORE anything else, otherwise
        #    /webclientsync.do returns the QR fallback page.
        self.establish_chat_session()

        ts = int(time.time() * 1000)
        # Do NOT send empty X-Registration-Id / X-Device-Id here; the
        # chat server treats them as invalid identity and falls back to
        # the QR-login page.
        data = self.s.request(
            "GET",
            f"/webclientsync.do?nocache={ts}",
            base="chat",
        )
        if isinstance(data, list):
            for entry in data:
                obj = (entry or {}).get("objString") or {}
                if isinstance(obj, dict):
                    self.uid = self.uid or obj.get("uid") or obj.get("user_id")
                    self.device_id = self.device_id or obj.get("device_id")
                    self.registration_id = self.registration_id or obj.get("registration_id")

        # grab X-SID from the WebSocket handshake cookie
        try:
            r = self.s.request(
                "GET",
                "/_wms/pconnect.sas",
                base="chat",
                raw=True,
            )
            tok = r.cookies.get("x-tkp-token")
            if tok:
                self.session_id = tok
        except Exception:  # noqa: BLE001
            pass

        if not self.uid:
            self.uid = (self.s._find_cookie("zuid")
                        or self.s._find_cookie("uid"))

        # Mint WS token so wss:// connections are authorized.
        self.mint_x_tkp_token()

    # ------------------------------------------------------------------ persistence

    def save(self, path: str) -> None:
        """Persist cookies + metadata to ``path`` (mode 0600)."""
        p = Path(os.path.expanduser(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        cookies = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expires": c.expires,
            }
            for c in self.s.http.cookies
        ]
        blob = {
            "cookies": cookies,
            "uid": self.uid,
            "mobile": self.mobile,
            "device_id": self.device_id,
            "registration_id": self.registration_id,
            "session_id": self.session_id,
        }
        p.write_text(json.dumps(blob, indent=2))
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)

    @staticmethod
    def load_into(session: Session, path: str) -> Dict[str, Any]:
        """Load a saved session blob into ``session``."""
        p = Path(os.path.expanduser(path))
        if not p.exists():
            raise SessionError(f"Session file not found: {p}")
        try:
            blob = json.loads(p.read_text())
        except (ValueError, OSError) as e:
            raise SessionError(f"Corrupt session file: {e}") from e
        session._install_cookies(blob.get("cookies") or [])
        return blob
