"""Dataclasses for chats and messages."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["Chat", "Message"]


def _coerce_str(v: Any) -> Optional[str]:
    """Coerce a possibly-nested field to a string.

    Arattai sometimes returns ``msg``, ``sender``, or ``msgid`` as a
    JSON object (e.g. ``{"text": "hi"}`` or ``{"id": "..."}``) instead
    of a plain string. Return a string in all cases so downstream code
    (startswith, ==, URL building) can assume str.
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        for k in ("text", "msg", "value", "id", "uid", "user_id", "_id"):
            if k in v:
                return _coerce_str(v[k])
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_coerce_str(x) or "" for x in v)
    return str(v)


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


@dataclass
class Chat:
    """Represents an Arattai chat (DM, group, channel, or system)."""

    id: str
    type: Optional[int] = None
    title: Optional[str] = None
    pcount: Optional[int] = None
    last_msg_time: Optional[int] = None
    last_message: Optional[Dict[str, Any]] = None
    archived: bool = False
    owner_id: Optional[str] = None
    is_admin: bool = False
    participants_count: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Chat":
        lm = _maybe_json(data.get("last_message"))
        if isinstance(lm, dict) and "meta" in lm:
            lm["meta"] = _maybe_json(lm.get("meta"))
        return cls(
            id=_coerce_str(data.get("id")) or _coerce_str(data.get("chatid")) or "",
            type=data.get("type"),
            title=data.get("title"),
            pcount=data.get("pcount") or data.get("participants_count"),
            last_msg_time=data.get("last_msg_time") or data.get("lastmsgtimestamp"),
            last_message=lm if isinstance(lm, dict) else None,
            archived=bool(data.get("archived", False)),
            owner_id=data.get("owner"),
            is_admin=bool(data.get("is_admin", False)),
            participants_count=data.get("participants_count"),
            raw=data,
        )

    @property
    def is_group(self) -> bool:
        """True if this is a group chat (chid ends with -GC)."""
        return str(self.id or "").endswith("-GC")

    @property
    def is_e2ee(self) -> bool:
        """True if the chat is E2EE.

        Signals used by Arattai (any one is sufficient):

        * explicit ``e2ee: true`` field
        * ``addinfo`` contains ``E2EE:1``
        * ``last_message.meta.enc`` is true
        * ``type == "dm"``
        * ``chat_type == 1``
        * chat-id has no known non-E2EE suffix
        """
        raw = self.raw or {}

        # explicit
        if raw.get("e2ee") is True:
            return True

        # addinfo marker
        info = str(raw.get("addinfo") or "")
        if ":E2EE:1" in info or "E2EE:1" in info:
            return True

        # last message encrypted
        lm = self.last_message
        if isinstance(lm, dict):
            m = lm.get("meta")
            if isinstance(m, dict) and m.get("enc"):
                return True

        # type / chat_type markers
        if raw.get("type") == "dm":
            return True
        if raw.get("chat_type") == 1:
            return True

        # chid shape fallback
        chid = str(self.id or "")
        for suffix in ("-GC", "-SM", "-SC", "-PC"):
            if chid.endswith(suffix):
                return False
        return bool(chid)

@dataclass
class Message:
    """A single message in a chat.

    ``raw`` holds the unmodified API dict. When ``meta.enc`` is True, the
    ``text`` is a Signal ciphertext — use :class:`E2EEBridge` to decrypt.
    """

    msgid: Optional[str] = None
    msguid: Optional[str] = None
    sender: Optional[str] = None
    dname: Optional[str] = None
    text: Optional[str] = None
    mtype: Optional[int] = None
    time: Optional[int] = None
    is_read: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)
    reactions: Dict[str, List[str]] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_encrypted(self) -> bool:
        """True if ``meta.enc`` is truthy (Signal ciphertext)."""
        enc = self.meta.get("enc") if isinstance(self.meta, dict) else None
        return bool(enc)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Message":
        meta = _maybe_json(data.get("meta")) or {}
        if not isinstance(meta, dict):
            meta = {"_raw_meta": meta}
        return cls(
            msgid=_coerce_str(data.get("msgid")),
            msguid=_coerce_str(data.get("msguid")),
            sender=_coerce_str(data.get("sender")),
            dname=_coerce_str(data.get("dname")),
            text=_coerce_str(data.get("msg")),
            mtype=data.get("mtype"),
            time=data.get("time"),
            is_read=bool(data.get("is_read", False)),
            meta=meta,
            reactions=data.get("reactions") or {},
            raw=data,
        )
