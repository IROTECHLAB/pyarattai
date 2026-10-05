"""WebSocket transport for Arattai.

Opens ``wss://in1-wms.arattai.in/pconnect`` using the same cookies as
the HTTP client, handles the initial handshake (which yields the X-SID
session id), dispatches incoming messages to a callback, and reconnects
with exponential backoff on drops.

This module is synchronous — no asyncio. Suitable for use inside
:class:`pyarattai.bot.ArattaiBot`.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import websocket  # type: ignore[import]

from .constants import CHAT_BASE
from .errors import NetworkError
from .session import Session

__all__ = ["WSClient"]

log = logging.getLogger("pyarattai.ws")

WMS_URL = "wss://in1-wms.arattai.in/pconnect"
ORIGIN = "https://in1-wms.arattai.in"


class WSClient:
    """Synchronous Arattai WebSocket client.

    Usage::

        ws = WSClient(session, uid="20004081014")
        ws.on_message(lambda frame: ...)
        ws.on_sid(lambda sid: print("got X-SID", sid))
        ws.start()
        ...
        ws.stop()
    """

    def __init__(
        self,
        session: Session,
        uid: str,
        dname: str = "pyarattai",
        *,
        auto_reconnect: bool = True,
        ping_interval: float = 15.0,
    ) -> None:
        self.s = session
        self.uid = uid
        self.dname = dname
        self.auto_reconnect = auto_reconnect
        self.ping_interval = ping_interval

        self.sid: Optional[str] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._msg_handlers: List[Callable[[Dict[str, Any]], None]] = []
        self._sid_handlers: List[Callable[[str], None]] = []
        self._frame_handlers: List[Callable[[Dict[str, Any]], None]] = []

    # --------------------------------------------------------------- hooks

    def on_message(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback for ``mtype=12`` message frames."""
        self._msg_handlers.append(fn)

    def on_sid(self, fn: Callable[[str], None]) -> None:
        """Register a callback for when the initial handshake yields X-SID."""
        self._sid_handlers.append(fn)

    def on_frame(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback for every parsed frame (debug/inspection)."""
        self._frame_handlers.append(fn)

    # --------------------------------------------------------------- url

    def _build_url(self) -> str:
        """Build the pconnect URL with the params Arattai expects."""
        tabid = f"{self.uid}_CT_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        params = {
            "prd": "CT",
            "uname": self.uid,
            "wmsid": self.uid,
            "config": "111",
            "dname": self.dname,
            "authtype": "10",
            "useagent": "true",
            "page_visible": "true",
            "tid": tabid,
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{WMS_URL}?{qs}"

    def _cookie_header(self) -> str:
        parts = []
        for c in self.s.http.cookies:
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    # --------------------------------------------------------------- lifecycle

    def start(self, blocking: bool = False) -> None:
        """Start the WS in a background thread (or block)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if blocking:
            self._run()
        else:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Signal the loop to stop and close the socket."""
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # --------------------------------------------------------------- core

    def _run(self) -> None:
        """Reconnect loop."""
        backoff = 2.0
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 2.0
            except Exception as e:  # noqa: BLE001
                log.warning("ws error: %s", e)
            if not self.auto_reconnect or self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def _connect_once(self) -> None:
        url = self._build_url()
        cookie = self._cookie_header()
        headers = [
            f"Cookie: {cookie}",
            f"Origin: {ORIGIN}",
        ]

        def on_open(ws: websocket.WebSocketApp) -> None:
            log.info("ws open")
            # Start ping thread
            def ping_loop() -> None:
                while not self._stop.is_set():
                    time.sleep(self.ping_interval)
                    try:
                        ws.send("-")
                    except Exception:  # noqa: BLE001
                        return
            threading.Thread(target=ping_loop, daemon=True).start()

        def on_message(ws: websocket.WebSocketApp, raw: Any) -> None:
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    return
            self._handle_raw(raw, ws)

        def on_error(ws: websocket.WebSocketApp, err: Any) -> None:
            log.warning("ws error: %s", err)

        def on_close(ws: websocket.WebSocketApp, code: Any, msg: Any) -> None:
            log.info("ws close: %s %s", code, msg)

        self._ws = websocket.WebSocketApp(
            url,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws.run_forever(ping_interval=0)  # we do our own ping

    def _handle_raw(self, raw: str, ws: websocket.WebSocketApp) -> None:
        # Frames can be a single JSON object or an array of them.
        try:
            parsed = json.loads(raw)
        except ValueError:
            return

        frames = parsed if isinstance(parsed, list) else [parsed]
        for fr in frames:
            if not isinstance(fr, dict):
                continue
            try:
                self._handle_frame(fr, ws)
            except Exception as e:  # noqa: BLE001
                log.warning("frame handler error: %s", e)

    def _handle_frame(self, fr: Dict[str, Any], ws: websocket.WebSocketApp) -> None:
        for h in self._frame_handlers:
            try:
                h(fr)
            except Exception:  # noqa: BLE001
                pass

        mt = str(fr.get("mtype"))

        if mt == "0":
            # handshake — extract sid
            msg = fr.get("msg") or {}
            sid = msg.get("sid") or msg.get("xsid")
            if sid:
                self.sid = str(sid)
                log.info("sid captured (%d chars)", len(sid))
                for h in self._sid_handlers:
                    try:
                        h(self.sid)
                    except Exception:  # noqa: BLE001
                        pass
            try:
                ws.send("--1--")
            except Exception:  # noqa: BLE001
                pass

        elif mt == "-17":
            # auth expired — user needs to re-mint x-tkp-token (out of scope)
            log.warning("ws auth expired (mtype -17)")

        elif mt == "12":
            msg = fr.get("msg") or {}
            for h in self._msg_handlers:
                try:
                    h(msg)
                except Exception as e:  # noqa: BLE001
                    log.warning("message handler error: %s", e)

        elif mt in ("50", "57"):
            # chat sync events — not forwarded, but logged
            log.debug("sync event mtype=%s", mt)
