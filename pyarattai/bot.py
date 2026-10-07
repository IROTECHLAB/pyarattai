"""TeleBot-style bot framework for Arattai."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .client import ArattaiClient
from .constants import BOT_DB_FILE, SESSION_FILE
from .errors import ArattaiError, RateLimitError
from .models import Message as _RawMessage

__all__ = ["ArattaiBot", "Message", "CommandHandler"]


# ---------------------------------------------------------------- Message


class Message:
    """A message as seen by the bot."""

    def __init__(self, bot: "ArattaiBot", raw: _RawMessage) -> None:
        self._bot = bot
        self._raw = raw
        self.chat_id: str = (raw.raw.get("chid") if isinstance(raw.raw, dict) else "") or ""
        self.sender: Optional[str] = raw.sender
        self.dname: Optional[str] = raw.dname
        self.text: str = raw.text or ""
        self.msgid: Optional[str] = raw.msgid
        self.msguid: Optional[str] = raw.msguid
        self.time: Optional[int] = raw.time
        self.is_read: bool = raw.is_read

    # ------------------------------------------------------------- commands

    @property
    def is_command(self) -> bool:
        """True if text begins with ``/``."""
        t = self.text
        if not isinstance(t, str) or not t:
            return False
        return t.startswith("/")

    @property
    def command(self) -> str:
        """Command name (without leading ``/``)."""
        if not self.is_command:
            return ""
        assert isinstance(self.text, str)
        head = self.text.split(maxsplit=1)[0]
        return head[1:].split("@", 1)[0]

    @property
    def args(self) -> List[str]:
        """Arguments after the command name."""
        if not self.is_command:
            return []
        assert isinstance(self.text, str)
        return self.text.split()[1:]

    # ------------------------------------------------------------- actions

    def reply(self, text: str) -> "Message":
        """Reply to this message's chat.

        Uses E2EE send when the incoming frame was encrypted, otherwise
        falls back to the plaintext endpoint.
        """
        if not self.chat_id:
            raise ArattaiError("Message has no chat_id; cannot reply")

        client = self._bot.client

        # Was this Message from an E2EE frame?
        meta = {}
        try:
            meta = self._raw.meta if hasattr(self._raw, "meta") else {}
        except Exception:
            pass
        is_e2ee = isinstance(meta, dict) and (
            meta.get("enc") or meta.get("_decrypted")
        )

        if is_e2ee and self.sender and client.uid:
            from .signal import SignalBridge
            sid = getattr(self._bot, "_ws_sid", None) or client.session_id
            bridge = SignalBridge(uid=client.uid)
            bridge.send(
                client, self.chat_id, self.sender, text, sid=sid,
            )
            return Message(self._bot, _RawMessage.from_api({
                "msg": text, "msgid": "e2ee-sent",
                "msguid": "e2ee-sent",
                "chid": self.chat_id, "sender": client.uid,
            }))

        sent = client.send(self.chat_id, text)
        return Message(self._bot, sent)

    def react(self, emoji: str) -> Any:
        """Add a reaction."""
        assert self.msguid
        return self._bot.client.react(self.chat_id, self.msguid, emoji)

    def edit(self, text: str) -> Any:
        """Edit this message (bot's own only)."""
        assert self.msguid
        return self._bot.client.edit(self.chat_id, self.msguid, text)

    def delete(self) -> Any:
        """Delete this message."""
        assert self.msguid
        return self._bot.client.delete(self.chat_id, self.msguid)


# ---------------------------------------------------------------- Commands


@dataclass
class CommandHandler:
    name: str
    description: str
    help_text: str
    handler_fn: Callable[[Message], None]


# ---------------------------------------------------------------- Bot


class ArattaiBot:
    """TeleBot-style bot for Arattai.

    Example::

        bot = ArattaiBot("91XXXXXXXXXX")

        @bot.on_message()
        def handle(msg):
            if msg.text == "/start":
                msg.reply("Hello!")

        bot.run()
    """

    def __init__(
        self,
        phone: Optional[str] = None,
        session_file: str = SESSION_FILE,
        poll_interval: float = 15.0,
        e2ee_daemon: bool = False,
        db_path: Optional[str] = BOT_DB_FILE,
        watch_chats: Optional[List[str]] = None,
        transport: str = "http",
        otp_channel: str = "app",
    ) -> None:
        self.phone = phone
        self.session_file = os.path.expanduser(session_file)
        self.poll_interval = poll_interval
        self.e2ee_daemon = e2ee_daemon
        self.watch_chats = watch_chats
        self.transport = transport
        self.otp_channel = otp_channel
        self._ws = None
        self._seen_msguids = set()
        self._ws_sid = None
        self._running = False
        self.watch_chats = watch_chats
        self.transport = transport
        self._ws = None  # populated when transport == "ws"
        self._db_path = os.path.expanduser(db_path) if db_path else None
        self._db: Optional[sqlite3.Connection] = None

        self.client: ArattaiClient = self._boot_client()

        # Auto-register Signal keys if the store is missing — so
        # users never have to run a CLI step.
        try:
            from .signal import SIGNAL_FILE, register_signal_keys
            import os as _os
            if not _os.path.exists(str(SIGNAL_FILE)) and self.client.device_id:
                print("[pyarattai] registering Signal E2EE keys (first run)")
                register_signal_keys(self.client, int(self.client.device_id))
                self.client.save_session(self.session_file)
                print("[pyarattai] Signal keys registered")
        except Exception as e:
            print(f"[pyarattai] signal setup skipped: {e}")

        self._message_handlers: List[Callable[[Message], None]] = []
        self._reaction_handlers: Dict[Optional[str], List[Callable[[Message, str, str], None]]] = {}
        self._command_handlers: Dict[str, CommandHandler] = {}

        self._last_msguid: Dict[str, str] = {}
        self._running = False

    # ------------------------------------------------------------- boot

    def _boot_client(self) -> ArattaiClient:
        if os.path.exists(self.session_file):
            try:
                return ArattaiClient.from_session(self.session_file)
            except ArattaiError:
                pass
        if not self.phone:
            raise ArattaiError(
                "No session found and no phone number supplied. "
                "Pass phone=... to ArattaiBot()."
            )
        return ArattaiClient.login(self.phone, self.session_file,
                                  channel=getattr(self, "otp_channel", "app"))

    # ------------------------------------------------------------- decorators

    def on_message(self, filters: Optional[Callable[[Message], bool]] = None):
        """Register a message handler. Optional ``filters`` predicate."""

        def deco(fn: Callable[[Message], None]):
            if filters is None:
                self._message_handlers.append(fn)
            else:
                def wrapped(msg: Message) -> None:
                    if filters(msg):
                        fn(msg)

                wrapped.__name__ = getattr(fn, "__name__", "wrapped")
                self._message_handlers.append(wrapped)
            return fn

        return deco

    def on_reaction(self, emoji: Optional[str] = None):
        """Register a reaction handler. ``emoji=None`` matches any."""

        def deco(fn: Callable[[Message, str, str], None]):
            self._reaction_handlers.setdefault(emoji, []).append(fn)
            return fn

        return deco

    def on_command(self, name: str, description: str = "", help_text: str = ""):
        """Register a slash-command handler."""

        def deco(fn: Callable[[Message], None]):
            self._command_handlers[name] = CommandHandler(
                name=name,
                description=description,
                help_text=help_text or (fn.__doc__ or "").strip(),
                handler_fn=fn,
            )
            return fn

        return deco

    # ------------------------------------------------------------- helpers

    def send(self, chat_id: str, text: str) -> Message:
        """Shortcut for :meth:`ArattaiClient.send`."""
        return Message(self, self.client.send(chat_id, text))

    # ------------------------------------------------------------- storage

    def _db_conn(self) -> Optional[sqlite3.Connection]:
        if not self._db_path:
            return None
        if self._db is not None:
            return self._db
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages("
            "chat_id TEXT, msguid TEXT, sender TEXT, dname TEXT, text TEXT, "
            "time INTEGER, is_read INTEGER, meta TEXT, "
            "PRIMARY KEY(chat_id, msguid))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats("
            "id TEXT PRIMARY KEY, type INTEGER, title TEXT, last_msg_time INTEGER)"
        )
        conn.commit()
        self._db = conn
        return conn

    def _persist(self, chat_id: str, raw: _RawMessage) -> None:
        conn = self._db_conn()
        if conn is None or not raw.msguid:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?)",
                (
                    chat_id, raw.msguid, raw.sender, raw.dname, raw.text,
                    raw.time, int(raw.is_read), json.dumps(raw.meta),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------- dispatch

    def _dispatch(self, msg: Message) -> None:
        if msg.is_command:
            handler = self._command_handlers.get(msg.command)
            if handler is not None:
                try:
                    handler.handler_fn(msg)
                except Exception as e:
                    if os.environ.get("PYARATTAI_DEBUG"):
                        print(f"[pyarattai] command error: {e!r}")
                    import traceback
                    traceback.print_exc()
        for fn in self._message_handlers:
            try:
                fn(msg)
            except Exception as e:
                if os.environ.get("PYARATTAI_DEBUG"):
                    print(f"[pyarattai] handler error: {e!r}")

    # ------------------------------------------------------------- run loop

    def poll_once(self) -> None:
        """Run a single poll across every chat."""
        chats = self.client.get_chats()
        for c in chats:
            try:
                msgs = self.client.get_messages(c.id, n=20)
            except RateLimitError as e:
                # Back off hard: server is throttling this endpoint.
                wait = 90.0
                if os.environ.get("PYARATTAI_DEBUG"):
                    print(f"[pyarattai] THROTTLED; sleeping {wait}s: {e}")
                time.sleep(wait)
                continue
            except ArattaiError as e:
                if os.environ.get("PYARATTAI_DEBUG"):
                    print(f"[pyarattai] poll error for {c.id}: {e}")
                continue

            last_seen = self._last_msguid.get(c.id)
            new_msgs: List[_RawMessage] = []
            for m in msgs:
                if not m.msguid:
                    continue
                if last_seen and m.msguid == last_seen:
                    break
                new_msgs.append(m)

            if msgs and msgs[0].msguid:
                if last_seen is None:
                    # First pass for this chat: don't replay history.
                    self._last_msguid[c.id] = msgs[0].msguid
                    continue
                self._last_msguid[c.id] = msgs[0].msguid

            for m in reversed(new_msgs):
                if m.sender and self.client.uid and m.sender == self.client.uid:
                    continue
                self._persist(c.id, m)
                payload = m.raw.copy() if isinstance(m.raw, dict) else {}
                payload.setdefault("chid", c.id)
                wrapped = Message(self, _RawMessage.from_api(payload))
                wrapped.chat_id = c.id
                self._dispatch(wrapped)

    def run(self, single: bool = False) -> None:
        """Blocking run loop. Uses ``self.transport`` ("http" or "ws")."""
        if self.transport == "ws":
            self._run_ws(single=single)
        else:
            self._run_http(single=single)

    def _run_http(self, single: bool = False) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                self.poll_once()
                backoff = 1.0
            except KeyboardInterrupt:
                self._running = False
                break
            except Exception as e:  # noqa: BLE001
                if os.environ.get("PYARATTAI_DEBUG"):
                    print(f"[pyarattai] loop error: {e!r}; retrying in {backoff:.1f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            if single:
                break
            time.sleep(self.poll_interval)

    def _run_ws(self, single: bool = False) -> None:
        from .ws import WSClient
        from .models import Message as _RM

        uid = self.client.uid or "0"
        ws = WSClient(self.client.s, uid=uid, dname=self.client.mobile or "bot")
        self._ws = ws

        def on_sid(sid: str) -> None:
            self.client.session_id = sid
            self._ws_sid = sid
            if os.environ.get("PYARATTAI_DEBUG"):
                print(f"[pyarattai] X-SID captured ({len(sid)} chars)")
            try:
                import json as _json
                sf = os.path.expanduser(self.session_file)
                blob = _json.loads(open(sf).read())
                blob["session_id"] = sid
                open(sf, "w").write(_json.dumps(blob, indent=2))
                os.chmod(sf, 0o600)
            except Exception:
                pass
            # Persist so other processes (CLI, bridge.send) can use it.
            try:
                import json as _json
                sf = os.path.expanduser(self.session_file)
                blob = _json.loads(open(sf).read())
                blob["session_id"] = sid
                open(sf, "w").write(_json.dumps(blob, indent=2))
                os.chmod(sf, 0o600)
            except Exception as e:
                if os.environ.get("PYARATTAI_DEBUG"):
                    print(f"[pyarattai] failed to persist SID: {e}")

        def on_frame(fr: dict) -> None:
            mt = fr.get("mtype")
            if mt == "0":
                if os.environ.get("PYARATTAI_DEBUG"):
                    print("[pyarattai] ws handshake OK")

        def on_msg(msg: dict) -> None:
            sender = msg.get("sender")
            if sender and self.client.uid and str(sender) == str(self.client.uid):
                return
            chid = msg.get("chid") or msg.get("chat_id") or ""
            if not chid:
                return

            # Dedupe — Arattai redelivers frames on reconnect, and
            # Signal only decrypts a given ciphertext once.
            body = str(msg.get("msg") or "")
            msguid = str(msg.get("msguid") or msg.get("msgid") or "")
            keys = []
            if body: keys.append("b:" + body)
            if msguid: keys.append("m:" + msguid)
            if keys and any(k in self._seen_msguids for k in keys):
                return
            self._seen_msguids.update(keys)
            if len(self._seen_msguids) > 8000:
                self._seen_msguids = set(list(self._seen_msguids)[-4000:])

            # Auto-decrypt E2EE frames before dispatching to handlers.
            meta = msg.get("meta")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict) and meta.get("enc") and self.client.uid:
                try:
                    from .signal import SignalBridge
                    bridge = SignalBridge(uid=self.client.uid)
                    plaintext = bridge.decrypt(msg)
                    msg = dict(msg)
                    msg["msg"] = plaintext
                    meta = dict(meta)
                    meta["_decrypted"] = True
                    msg["meta"] = meta
                    if os.environ.get("PYARATTAI_DEBUG"):
                        print(f"[pyarattai] decrypted msg from "
                          f"{msg.get('dname')}: {plaintext[:60]}")
                except Exception as e:
                    if os.environ.get("PYARATTAI_DEBUG"):
                        print(f"[pyarattai] decrypt failed: {e}")

            payload = dict(msg)
            payload["chid"] = chid
            raw = _RM.from_api(payload)
            try:
                self._persist(chid, raw)
            except Exception:
                pass
            wrapped = Message(self, raw)
            wrapped.chat_id = chid
            try:
                self._dispatch(wrapped)
            except Exception as e:
                if os.environ.get("PYARATTAI_DEBUG"):
                    print(f"[pyarattai] dispatch error: {e!r}")
        ws.on_sid(on_sid)
        ws.on_frame(on_frame)
        ws.on_message(on_msg)

        if os.environ.get("PYARATTAI_DEBUG"):
            print("[pyarattai] starting WebSocket transport")
        ws.start(blocking=False)
        self._running = True

        if single:
            if os.environ.get("PYARATTAI_DEBUG"):
                print("[pyarattai] single-shot: 5s listening")
            time.sleep(5.0)
            ws.stop()
            self._running = False
            return

        try:
            while self._running:
                time.sleep(1.0)
                if ws._thread is not None and not ws._thread.is_alive():
                    if os.environ.get("PYARATTAI_DEBUG"):
                        print("[pyarattai] ws thread died; exiting")
                    break
        except KeyboardInterrupt:
            pass
        finally:
            ws.stop()
            self._running = False

    def typing(self, chat_id: str, idle: bool = False) -> None:
        """Send a typing indicator using the current WS-issued SID.

        Safe to call from any handler::

            @bot.on_message()
            def handle(msg):
                bot.typing(msg.chat_id)
                ...
                msg.reply("done")
        """
        try:
            self.client.session_id = self._ws_sid or self.client.session_id
            self.client.typing(chat_id, idle=idle)
        except Exception:
            pass

    def stop(self) -> None:
        """Stop the loop (and WS if running)."""
        self._running = False
        if self._ws is not None:
            try:
                self._ws.stop()
            except Exception:
                pass
