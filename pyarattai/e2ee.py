"""Bridge to the external Node.js E2EE daemon.

This module does NOT implement Signal / E2EE in Python. It proxies to a
local daemon (``arattai-daemon.cjs``) listening on 127.0.0.1:8766.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

import requests

from .constants import E2EE_DEFAULT_URL
from .errors import NetworkError

__all__ = ["E2EEBridge"]


class E2EEBridge:
    """HTTP bridge to the local Node.js E2EE daemon."""

    def __init__(self, base_url: str = E2EE_DEFAULT_URL, timeout: float = 30.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = requests.request(
                method, f"{self.base}{path}", timeout=self.timeout, **kw
            )
        except requests.RequestException as e:
            raise NetworkError(f"daemon unreachable: {e}") from e
        if not r.ok:
            raise NetworkError(f"daemon HTTP {r.status_code}: {r.text[:200]}")
        if r.headers.get("Content-Type", "").startswith("text/event-stream"):
            return r
        try:
            return r.json()
        except ValueError:
            return r.text

    def status(self) -> Dict[str, Any]:
        """Return daemon status (keys: ok, uid, connected, ...)."""
        return self._req("GET", "/status")

    def chats(self) -> List[Dict[str, Any]]:
        """List chats known to the daemon."""
        return self._req("GET", "/chats") or []

    def transcript(self, chid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch decrypted messages for a chat."""
        return self._req("GET", f"/transcript/{chid}?limit={limit}") or []

    def send(self, chid: str, recipient_uid: str, text: str) -> Dict[str, Any]:
        """Send an E2EE message (daemon shells out to e2ee-send.cjs)."""
        return self._req(
            "POST",
            "/send-e2ee",
            json={"chid": chid, "recipient": recipient_uid, "text": text},
        )

    def watch(self) -> Iterator[Dict[str, Any]]:
        """Yield new messages from the daemon SSE ``/stream`` endpoint."""
        r = self._req("GET", "/stream", stream=True)
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("data:"):
                raw = raw[5:].strip()
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue
