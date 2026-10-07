"""High-level ArattaiClient."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from .auth import Auth
from .constants import SESSION_FILE
from .errors import ArattaiError, AuthError
from .models import Chat, Message
from .signal import SignalBridge
from .session import Session

__all__ = ["ArattaiClient"]


class ArattaiClient:
    """Synchronous Arattai client.

    Prefer :meth:`from_session` or :meth:`login` instead of the raw
    constructor.
    """

    def __init__(self, session: Session, blob: Dict[str, Any]) -> None:
        self.s = session
        self.uid: Optional[str] = blob.get("uid")
        self.mobile: Optional[str] = blob.get("mobile")
        self.device_id: Optional[str] = blob.get("device_id")
        self.registration_id: Optional[str] = blob.get("registration_id")
        self.session_id: Optional[str] = blob.get("session_id")

        # Per-chat E2EE decision cache. Populated lazily: first send to
        # a chat probes the plaintext endpoint, and if the server
        # rejects it as E2EE we cache the answer and route E2EE from
        # then on. Key = chat_id, value = True (e2ee) / False (plain).
        self._e2ee_chats: Dict[str, bool] = {}

        # Expose device identity to Session.request so every chat call
        # carries X-Device-Id / X-Registration-Id (matches reference).
        self.s._device_id = self.device_id
        self.s._registration_id = self.registration_id

    # ------------------------------------------------------------------ ctors

    @classmethod
    def from_session(cls, path: str = SESSION_FILE) -> "ArattaiClient":
        """Restore an :class:`ArattaiClient` from a saved session file."""
        s = Session()
        blob = Auth.load_into(s, path)
        return cls(s, blob)

    @classmethod
    def login(cls, phone: str, session_file: str = SESSION_FILE,
              channel: str = "app") -> "ArattaiClient":
        """Interactive OTP login.

        After a successful OTP, this also:

        * establishes the chat session
        * mints the WMS token (x-tkp-token)
        * fetches uid / device info from /webclientsync.do
        * **auto-registers Signal E2EE keys** if none exist yet

        So callers get a fully working client from a single call::

            client = ArattaiClient.login("91-XXXXXXXXXX")

        Args:
            phone: phone number as registered (e.g. ``91-XXXXXXXXXX``).
            session_file: where to store the session JSON.
            channel: OTP delivery channel — ``"app"`` (default),
                ``"sms"``, or ``"auto"``.

        Commands at the OTP prompt: digits, ``resend``, ``sms``, ``app``.
        """
        s = Session()
        a = Auth(s)
        a.start()
        a.lookup(phone)

        def _send(ch: str) -> bool:
            try:
                a.send_otp(channel=ch)
                if ch == "sms":
                    print(f"SMS OTP sent to {a.r_mobile or a.e_mobile}")
                else:
                    print(f"App OTP sent to {a.r_mobile or a.e_mobile} "
                          f"(check the Arattai app)")
                return True
            except Exception as e:
                print(f"{ch} send failed: {e}")
                return False

        _send(channel)
        current_channel = channel

        for _ in range(50):
            try:
                line = input(
                    "Enter OTP  ('resend' = new code, "
                    "'sms'/'app' = switch channel): "
                ).strip()
            except EOFError:
                raise
            if not line:
                continue
            low = line.lower()
            if low == "resend":
                _send(current_channel)
                continue
            if low in ("sms", "app"):
                current_channel = low
                _send(current_channel)
                continue

            resp = a.verify_otp(line)
            code = ""
            if isinstance(resp, dict):
                code = str(resp.get("code") or
                           resp.get("status_code") or "")
            if code in ("SI200", "200"):
                break
            if code in ("IN105", "500"):
                print("Wrong code. Try again, or type 'resend'.")
                continue
            print(f"Unexpected response ({code}). Try again.")
        else:
            raise AuthError("too many OTP attempts")

        a.mobile = phone
        a.establish_chat_session()
        a.mint_x_tkp_token()
        a.fetch_session_meta()
        if not a.uid:
            a.uid = a.identifier
        a.save(session_file)
        blob = {
            "uid": a.uid, "mobile": a.mobile,
            "device_id": a.device_id,
            "registration_id": a.registration_id,
            "session_id": a.session_id,
        }
        client = cls(s, blob)

        # ─── Auto-register Signal E2EE keys on first login ───────────
        try:
            from .signal import SIGNAL_FILE, register_signal_keys
            import os as _os
            if not _os.path.exists(str(SIGNAL_FILE)):
                # Assign a device id if we don't have one
                if not client.device_id:
                    import secrets as _sec
                    client.device_id = str(_sec.randbelow(8_000_000)
                                           + 1_000_000)
                print("→ registering Signal E2EE keys (one-time setup)")
                try:
                    register_signal_keys(client, int(client.device_id))
                    # Persist the newly assigned device_id + cookies
                    client.save_session(session_file)
                    print("→ Signal keys registered")
                except Exception as e:
                    print(f"⚠ Signal registration failed: {e}")
                    print("  (you can retry with: pyarattai signal register)")
        except Exception as e:
            # Never break login just because Signal setup hiccupped
            print(f"⚠ auto Signal setup skipped: {e}")

        return client

    def _ts(self) -> int:
        return int(time.time() * 1000)

    def _sid(self) -> str:
        """Return X-SID session token, or "" if unavailable.

        Arattai's server accepts /sendofficechatmessage.do without
        X-SID (as the reference client does), so this is best-effort.
        """
        return self.session_id or ""

    def _dname(self) -> str:
        return self.mobile or "pyarattai"

    # ------------------------------------------------------------------ chats

    def attach_session(self) -> List[Dict[str, Any]]:
        """POST /attachsession.do — returns raw list."""
        return self.s.request("POST", "/attachsession.do", base="chat", json={}) or []

    def get_chats(self, limit: int = 2000) -> List[Chat]:
        """Fetch all chats (bulk)."""
        data = self.s.request(
            "GET",
            f"/v3/chats?limit={limit}&state=bulk&nocache={self._ts()}",
            base="chat",
            headers={"x-message-version": "legacy"},
        )
        rows = (data or {}).get("data") if isinstance(data, dict) else data
        return [Chat.from_api(r) for r in (rows or [])]

    def get_chat(self, chat_id: str) -> Chat:
        """Fetch metadata for a single chat."""
        data = self.s.request(
            "GET", f"/api/v1/chats/{chat_id}?nocache={self._ts()}", base="chat"
        )
        payload = (data or {}).get("data") if isinstance(data, dict) else data
        return Chat.from_api(payload or {})

    # ------------------------------------------------------------------ messages

    def get_messages(self, chat_id: str, n: int = 50) -> List[Message]:
        """Fetch the last ``n`` messages from a chat.

        Note: Arattai returns the transcript as a JSON *string* which
        sometimes contains a trailing comma before the closing ``]``
        (illegal JSON). We strip those before parsing.
        """
        import json as _json
        import re as _re

        device_id = self.device_id or "web"
        raw = self.s.request(
            "GET",
            f"/v2/chats/{chat_id}/transcript?lineslimit={n}"
            f"&device_id={device_id}&nocache={self._ts()}",
            base="chat",
        )

        def _parse(s: str):
            """Parse JSON, tolerating trailing commas before ] or }."""
            try:
                return _json.loads(s)
            except ValueError:
                pass
            cleaned = _re.sub(r",\s*([\]}])", r"\1", s)
            try:
                return _json.loads(cleaned)
            except ValueError:
                return None

        for _ in range(4):
            if isinstance(raw, str):
                parsed = _parse(raw)
                if parsed is None:
                    return []
                raw = parsed
            elif isinstance(raw, dict):
                raw = raw.get("data") if "data" in raw else raw.get("messages")
            elif isinstance(raw, list):
                break
            else:
                return []

        if not isinstance(raw, list):
            return []

        msgs = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            if m.get("msg") == "chat.more":
                continue
            msg = Message.from_api(m)
            if msg.is_encrypted and self.uid:
                try:
                    bridge = SignalBridge(uid=self.uid)
                    msg.text = bridge.decrypt(m)
                except Exception as e:
                    import logging
                    logging.getLogger("pyarattai").debug(
                        "auto-decrypt failed: %s", e)
            msgs.append(msg)
        return msgs
    def send(self, chat_id: str, text: str, *,
             force_plaintext: bool = False) -> Message:
        """Send a message, auto-detecting E2EE.

        Strategy (self-tuning):

        1. If we've cached a decision for this chat, use it.
        2. Otherwise try the plaintext endpoint first.
        3. If plaintext is rejected (any 4xx), retry via
           :class:`SignalBridge`. On success, cache ``True``.
        4. If plaintext succeeds, cache ``False``.

        The server is the source of truth — no guessing about chat
        types. Works for DMs, groups, channels, saved messages, and
        anything Arattai adds later.
        """
        if force_plaintext:
            return self._send_plaintext(chat_id, text)

        cached = self._e2ee_chats.get(chat_id)
        if cached is True:
            return self._send_e2ee(chat_id, text)
        if cached is False:
            return self._send_plaintext(chat_id, text)

        # Unknown → probe by trying plaintext first
        try:
            return self._send_plaintext(chat_id, text)
        except Exception as plain_err:
            try:
                msg = self._send_e2ee(chat_id, text)
                self._remember_e2ee(chat_id, True)
                return msg
            except Exception as e2ee_err:
                raise plain_err from e2ee_err


    def _send_plaintext(self, chat_id: str, text: str) -> Message:
        """Plaintext POST via /sendofficechatmessage.do."""
        sid = self._require_sid()
        msgid = str(self._ts())
        body = {
            "chid": chat_id,
            "msg": text,
            "msgid": msgid,
            "sid": sid,
            "dname": self._dname(),
            "unfurl": "false",
        }
        try:
            data = self.s.request(
                "POST", "/sendofficechatmessage.do", base="chat",
                data=body, headers={"X-SID": sid},
            )
        except Exception:
            # Let the caller decide
            raise
        entry = (data or [{}])[0] if isinstance(data, list) else {}
        obj = (entry or {}).get("objString") or {}
        obj.setdefault("msg", text)
        obj.setdefault("msgid", msgid)
        obj.setdefault("chid", chat_id)
        self._remember_e2ee(chat_id, False)
        return Message.from_api(obj)

    def _send_e2ee(self, chat_id: str, text: str) -> Message:
        """E2EE POST via SignalBridge."""
        # Group E2EE requires SenderKeys, which the vendored libsignal
        # build doesn't expose. Fail with a clear message rather than
        # a cryptic error from the JS layer.
        if str(chat_id).endswith("-GC"):
            try:
                ch = self.get_chat(chat_id)
                if getattr(ch, "is_e2ee", False):
                    raise ArattaiError(
                        "group E2EE is not supported in this version "
                        "(requires SenderKeys). Non-E2EE groups work as "
                        "plaintext. See the README for roadmap."
                    )
            except ArattaiError:
                raise
            except Exception:
                pass

        recipient = self._find_recipient(chat_id)
        if not recipient:
            raise ArattaiError(
                f"can't determine recipient for E2EE send to {chat_id}"
            )

        sid = self._require_sid()
        from .signal import SignalBridge
        bridge = SignalBridge(uid=self.uid or "0")
        bridge.send(self, chat_id, recipient, text, sid=sid)
        self._remember_e2ee(chat_id, True)
        return Message.from_api({
            "msg": text, "msgid": "e2ee-sent",
            "msguid": "e2ee-sent",
            "chid": chat_id, "sender": self.uid or "",
        })


    def _find_recipient(self, chat_id: str) -> Optional[str]:
        """Extract the peer uid for a DM / group chat.

        Arattai returns ``recipants`` in several shapes:
          * ``"20004081014,20031897759"`` — comma list
          * ``'[{"dname":"...","zuid":"..."}]'`` — JSON array of dicts
          * ``'["20004081014","20031897759"]'`` — JSON array of uids

        For DMs we return the single uid that isn't ours.
        """
        import json as _json

        try:
            ch = self.get_chat(chat_id)
        except Exception:
            return self.uid

        raw = ch.raw or {}
        recipants_raw = raw.get("recipants")

        uids = []

        def _collect(item):
            if isinstance(item, dict):
                for k in ("zuid", "id", "uid", "user_id"):
                    v = item.get(k)
                    if v:
                        uids.append(str(v))
                        return
            elif isinstance(item, (str, int)):
                uids.append(str(item))

        if isinstance(recipants_raw, list):
            for item in recipants_raw:
                _collect(item)
        elif recipants_raw is not None:
            tv = str(recipants_raw).strip()
            parsed = None
            if tv.startswith("[") or tv.startswith("{"):
                try:
                    parsed = _json.loads(tv)
                except Exception:
                    parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    _collect(item)
            elif isinstance(parsed, dict):
                _collect(parsed)
            else:
                for part in tv.split(","):
                    part = part.strip()
                    if part:
                        uids.append(part)

        # Fallback to the users field
        if not uids:
            users_raw = raw.get("users")
            if isinstance(users_raw, str) and users_raw.strip().startswith("["):
                try:
                    for item in _json.loads(users_raw):
                        _collect(item)
                except Exception:
                    pass
            elif isinstance(users_raw, list):
                for item in users_raw:
                    _collect(item)

        # Drop empties and ourselves
        uids = [u for u in uids if u and u != self.uid]

        if not uids:
            return self.uid
        return uids[0]


    def _is_e2ee_chat(self, chat_id: str) -> bool:
        """True if we've cached this chat as E2EE."""
        return bool(self._e2ee_chats.get(chat_id, False))

    def _remember_e2ee(self, chat_id: str, value: bool) -> None:
        """Cache the E2EE decision for a chat."""
        self._e2ee_chats[chat_id] = bool(value)

    @staticmethod
    def _looks_e2ee_only_error(payload) -> bool:
        """True if the server said "this chat requires E2EE".

        The plaintext send endpoint 400s with a body mentioning
        ``e2ee`` / ``encrypt`` when the chat only accepts encrypted
        sends.  This lets us auto-retry via SignalBridge.
        """
        if payload is None:
            return False
        text = str(payload).lower()
        if "e2ee" in text or "encrypt" in text:
            return True
        if isinstance(payload, dict):
            code = str(payload.get("code") or "").lower()
            if code in ("request_not_allowed", "e2ee_required",
                        "not_allowed"):
                return True
            msg = str(payload.get("message") or "").lower()
            if "e2ee" in msg or "encrypt" in msg:
                return True
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                inner = str(first).lower()
                if "e2ee" in inner or "encrypt" in inner:
                    return True
        return False

    def _require_sid(self) -> str:
        """Return a fresh WS-issued SID or raise.

        Use this in every send/status/edit endpoint body and header.
        """
        sid = self._fresh_ws_sid()
        if not sid:
            raise ArattaiError(
                "could not obtain X-SID from WebSocket; "
                "check network and try again"
            )
        return sid

    def _fresh_ws_sid(self, timeout: float = 15.0) -> Optional[str]:
        """Open a WebSocket, capture the WS-issued X-SID, close.

        Every send endpoint (``/sendofficechatmessage.do``,
        ``/e2ee/sendofficechatmessage.api``, ``/sendstatus.do``,
        ``/propagatemsgseen.do``) requires the SID the server issues
        in the ``mtype: 0`` frame of a WS handshake. The value stored
        in ``session.json`` is the x-tkp-token and is NOT accepted.

        Always returns a fresh SID — no caching — so it stays valid
        even if the WS session rotates.

        Returns:
            The SID string, or None if the WS handshake failed
            within ``timeout`` seconds.
        """
        import time as _t
        try:
            from .ws import WSClient
        except Exception as e:
            import logging
            logging.getLogger("pyarattai").warning(
                "WSClient unavailable: %s", e
            )
            return None

        box: dict = {}
        ws = WSClient(
            self.s,
            uid=self.uid or "0",
            dname=self._dname(),
        )
        ws.on_sid(lambda sid: box.update(sid=sid))
        ws.start(blocking=False)

        deadline = _t.time() + timeout
        while _t.time() < deadline:
            if box.get("sid"):
                break
            _t.sleep(0.25)

        ws.stop()
        sid = box.get("sid")
        if not sid:
            import logging
            logging.getLogger("pyarattai").warning(
                "no X-SID captured within %.1fs", timeout
            )
        return sid

    def edit(self, chat_id: str, msguid: str, text: str) -> Any:
        """Edit an existing message."""
        msgid = str(self._ts())
        body = {
            "chid": chat_id,
            "msg": text,
            "msgid": msgid,
            "msguid": msguid,
            "dname": self._dname(),
            "unfurl": "false",
            "notifyedit": "true",
        }
        return self.s.request(
            "POST",
            "/sendofficechatmessage.do",
            base="chat",
            data=body,
            headers={"X-SID": self._require_sid()},
        )

    def delete(self, chat_id: str, msguid: str) -> None:
        """Delete a message."""
        self.s.request(
            "DELETE", f"/v2/chats/{chat_id}/messages/{msguid}", base="chat"
        )

    def mark_read(self, chat_id: str, msguid: str) -> None:
        """Mark a message as read."""
        self.s.request(
            "POST",
            f"/v2/chats/{chat_id}/messages/{msguid}/markread",
            base="chat",
            json={},
        )

    # ------------------------------------------------------------------ reactions

    def react(self, chat_id: str, msguid: str, emoji: str) -> Any:
        """Add a reaction."""
        return self.s.request(
            "POST",
            f"/v2/chats/{chat_id}/messages/{msguid}/reactions",
            base="chat",
            json={"emoji_code": emoji},
        )

    def unreact(self, chat_id: str, msguid: str, emoji: str) -> Any:
        """Remove a reaction."""
        return self.s.request(
            "DELETE",
            f"/v2/chats/{chat_id}/messages/{msguid}/reactions",
            base="chat",
            json={"emoji_code": emoji},
        )

    def get_reactions(self, chat_id: str, msguid: str) -> Dict[str, Any]:
        """Fetch reactions + read status."""
        data = self.s.request(
            "GET",
            f"/v2/chats/{chat_id}/messages/{msguid}?fields=read_status,reactions",
            base="chat",
        )
        return (data or {}).get("reactions") or {}

    # ------------------------------------------------------------------ status

    def typing(self, chat_id: str, idle: bool = False) -> Any:
        """Send a typing (or idle) status to a chat.

        Always grabs a fresh WS-issued X-SID — the stored
        ``session_id`` from ``session.json`` is the x-tkp-token and is
        rejected by ``/sendstatus.do``.
        """
        from .constants import STATUS_IDLE, STATUS_TYPING

        sid = self._require_sid()
        body = {
            "userid": self.uid or "",
            "chid": chat_id,
            "status": STATUS_IDLE if idle else STATUS_TYPING,
            "sid": sid,
            "dname": self._dname(),
        }
        headers = {
            "Content-Type":
                "application/x-www-form-urlencoded; charset=UTF-8",
            "X-SID": sid,
        }
        return self.s.request(
            "POST", "/sendstatus.do", base="chat",
            data=body, headers=headers,
        )

    # ------------------------------------------------------------------ groups

    def get_participants(self, chat_id: str) -> List[Dict[str, Any]]:
        """List participants in a chat."""
        data = self.s.request(
            "GET", f"/v3/chats/{chat_id}/participants", base="chat"
        )
        return (data or {}).get("participants") if isinstance(data, dict) else (data or [])

    def add_member(self, chat_id: str, uids: List[str]) -> Any:
        """Add members by uid list."""
        return self.s.request(
            "POST",
            "/addmember.do",
            base="chat",
            json={"invited_users": uids},
            headers={"X-SID": self._require_sid()},
        )

    def remove_member(self, chat_id: str, uids: List[str]) -> Any:
        """Remove members by uid list."""
        return self.s.request(
            "POST",
            "/deletemember.do",
            base="chat",
            json={"removed_users": uids},
            headers={"X-SID": self._require_sid()},
        )

    def join_chat(self, chat_id: str) -> Any:
        """Join a chat."""
        return self.s.request("POST", "/joinchat.do", base="chat", json={})

    def quit_chat(self, chat_id: str) -> Any:
        """Quit a chat."""
        return self.s.request("POST", "/quitchat.do", base="chat", json={})

    def leave_chat(self, chat_id: str) -> None:
        """Leave a chat via v2 endpoint."""
        self.s.request("POST", f"/v2/chats/{chat_id}/leave", base="chat")

    def update_chat(self, chat_id: str, title: Optional[str] = None,
                    description: Optional[str] = None) -> Any:
        """Update title/description of a group."""
        body: Dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if description is not None:
            body["description"] = description
        return self.s.request(
            "PUT", f"/api/v1/chats/{chat_id}", base="chat", json=body
        )

    def set_role(self, chat_id: str, user_id: str, role: int) -> Any:
        """Set a participant's role (1=admin, 0=member)."""
        return self.s.request(
            "PUT",
            f"/api/v1/chats/{chat_id}/participants",
            base="chat",
            json={"role": role, "user_id": user_id},
        )

    # ------------------------------------------------------------------ invites

    def get_permalink(self, chat_id: str) -> Dict[str, Any]:
        """Get or create an invite link."""
        data = self.s.request(
            "GET", f"/api/v2/chats/{chat_id}/permalink", base="chat"
        )
        return (data or {}).get("data") or {}

    def revoke_permalink(self, chat_id: str) -> None:
        """Revoke an invite link."""
        self.s.request("DELETE", f"/api/v2/chats/{chat_id}/permalink", base="chat")

    def join_by_invite(self, invite_token: str) -> None:
        """Join a group via invite token."""
        self.s.request("POST", f"/groups/{invite_token}/join", base="chat")

    def invite_details(self, invite_token: str) -> Dict[str, Any]:
        """Fetch details of an invite."""
        return self.s.request(
            "GET",
            f"/groups/{invite_token}/details?nocache={self._ts()}",
            base="chat",
        )

    # ------------------------------------------------------------------ users

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Fetch a user's profile."""
        return self.s.request(
            "GET",
            f"/v2/users/{user_id}?fields=all,chat_id&nocache={self._ts()}",
            base="chat",
        )

    # ------------------------------------------------------------------ channels

    def get_channels(self, filter_: str = "popular", limit: int = 50) -> List[Dict[str, Any]]:
        """List channels."""
        data = self.s.request(
            "GET",
            f"/v2/channels?filter={filter_}&limit={limit}&nocache={self._ts()}",
            base="chat",
        )
        return (data or {}).get("data") if isinstance(data, dict) else (data or [])

    def get_channel(self, channel_id: str) -> Dict[str, Any]:
        """Fetch a channel's metadata."""
        return self.s.request(
            "GET",
            f"/api/v2/channels/{channel_id}?nocache={self._ts()}",
            base="chat",
        )

    def join_channel(self, username: str) -> None:
        """Join a channel by @username."""
        self.s.request("POST", f"/channel/@{username}/join", base="chat")

    def leave_channel(self, channel_id: str) -> None:
        """Leave a channel."""
        self.s.request("POST", f"/api/v2/chats/{channel_id}/leave", base="chat")

    # ------------------------------------------------------------------ saved

    def create_saved_messages(self) -> str:
        """Create (or fetch) the Saved Messages chat id."""
        data = self.s.request(
            "POST",
            "/api/v2/chats",
            base="chat",
            json={"type": "saved_messages"},
        )
        return (data or {}).get("chat_id", "")

    # ------------------------------------------------------------------ pinned

    def pin(self, chat_id: str, msguid: str, expiry_time: int = 0,
            notify: bool = False) -> Any:
        """Pin a message."""
        return self.s.request(
            "POST",
            f"/v2/chats/{chat_id}/stickymessage",
            base="chat",
            json={"id": msguid, "expiry_time": expiry_time, "notify": notify},
        )

    def unpin(self, chat_id: str) -> None:
        """Unpin the sticky message."""
        self.s.request(
            "DELETE", f"/v2/chats/{chat_id}/stickymessage", base="chat"
        )

    # ------------------------------------------------------------------ events

    def event_logs(self, sync_token: Optional[str] = None) -> Any:
        """Fetch event logs (optionally from a sync token)."""
        if sync_token:
            return self.s.request(
                "GET",
                f"/v3/eventlogs?sync_token={sync_token}&nocache={self._ts()}",
                base="chat",
            )
        return self.s.request(
            "GET", f"/v3/eventlogs?nocache={self._ts()}", base="chat"
        )

    # ------------------------------------------------------------------ paths

    def logout(self, delete_session_file: bool = True) -> None:
        """Log out server-side and (optionally) remove local state.

        Hits ``GET /logout.sas`` which returns a 302 chain ending on
        ``/login.jsp`` — after the chain, the IAM cookies
        (``__Secure-iamsdt``, ``_iamadt``, ``_iambdt``, ``x-tkp-token``)
        are cleared and the linked-device session is terminated on the
        server. The remaining CSRF scaffolding cookies
        (``CT_CSRF_TOKEN``, ``JSESSIONID``) are harmless.

        Args:
            delete_session_file: also remove ``~/.pyarattai/session.json``
                and ``~/.pyarattai/signal-storage.json`` so a subsequent
                login starts completely fresh.
        """
        try:
            r = self.s.request(
                "GET", "/logout.sas", base="chat",
                raw=True, allow_redirects=True, timeout=20,
            )
            ok = r.status_code in (200, 401) and "login" in r.url
            print(f"[logout] server {'OK' if ok else 'unexpected'} "
                  f"(HTTP {r.status_code})")
        except Exception as e:
            print(f"[logout] request failed: {e}")

        if delete_session_file:
            import os as _os
            from .constants import SESSION_FILE as _SF
            from .signal import SIGNAL_FILE as _SIG
            for f in (_os.path.expanduser(_SF), str(_SIG)):
                try:
                    _os.remove(f)
                    print(f"[logout] removed {f}")
                except FileNotFoundError:
                    pass

    @staticmethod
    def default_session_path() -> str:
        return os.path.expanduser(SESSION_FILE)
