"""High-level ArattaiClient."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from .auth import Auth
from .constants import SESSION_FILE
from .errors import AuthError
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
    def login(cls, phone: str, session_file: str = SESSION_FILE) -> "ArattaiClient":
        """Perform interactive OTP login and persist the session."""
        s = Session()
        a = Auth(s)
        a.start()
        a.lookup(phone)
        a.send_otp()
        print(f"OTP sent to {a.r_mobile or a.e_mobile}")
        code = input("Enter 6-digit OTP: ").strip()
        a.verify_otp(code)
        a.mobile = phone

        # Do the chat handshake, mint x-tkp-token, then pull uid and
        # (if present) e2ee_device_id from /webclientsync.do before
        # saving — otherwise the file ends up with uid=None.
        a.establish_chat_session()
        a.mint_x_tkp_token()
        a.fetch_session_meta()

        # If webclientsync did not yield a uid, fall back to the
        # identifier returned by the accounts lookup (they are the
        # same value).
        if not a.uid:
            a.uid = a.identifier

        a.save(session_file)
        blob = {
            "uid": a.uid, "mobile": a.mobile,
            "device_id": a.device_id, "registration_id": a.registration_id,
            "session_id": a.session_id,
        }
        return cls(s, blob)

    # ------------------------------------------------------------------ helpers

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
    def send(self, chat_id: str, text: str) -> Message:
        """Send a plaintext message."""
        msgid = str(self._ts())
        body = {
            "chid": chat_id,
            "msg": text,
            "msgid": msgid,
            "sid": self._sid(),
            "dname": self._dname(),
            "unfurl": "false",
        }
        data = self.s.request(
            "POST",
            "/sendofficechatmessage.do",
            base="chat",
            data=body,
            headers={"X-SID": self._sid()},
        )
        entry = (data or [{}])[0] if isinstance(data, list) else {}
        obj = (entry or {}).get("objString") or {}
        obj.setdefault("msg", text)
        obj.setdefault("msgid", msgid)
        obj.setdefault("chid", chat_id)
        return Message.from_api(obj)

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
            headers={"X-SID": self._sid()},
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
        """Send typing (or idle) status."""
        from .constants import STATUS_IDLE, STATUS_TYPING

        body = {
            "userid": self.uid or "",
            "chid": chat_id,
            "status": STATUS_IDLE if idle else STATUS_TYPING,
            "sid": self._sid(),
            "dname": self._dname(),
        }
        return self.s.request("POST", "/sendstatus.do", base="chat", data=body)

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
            headers={"X-SID": self._sid()},
        )

    def remove_member(self, chat_id: str, uids: List[str]) -> Any:
        """Remove members by uid list."""
        return self.s.request(
            "POST",
            "/deletemember.do",
            base="chat",
            json={"removed_users": uids},
            headers={"X-SID": self._sid()},
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

    @staticmethod
    def default_session_path() -> str:
        return os.path.expanduser(SESSION_FILE)
