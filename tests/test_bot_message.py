"""Tests for the bot Message wrapper."""
from __future__ import annotations

from pyarattai.bot import Message
from pyarattai.models import Message as RawMessage


class FakeClient:
    uid = "me"

    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, **_):
        self.sent.append((chat_id, text))
        return RawMessage.from_api({
            "msg": text, "sender": "me",
            "chid": chat_id, "msgid": "1", "msguid": "1-1",
        })


class FakeBot:
    def __init__(self):
        self.client = FakeClient()


def _mk(text: str) -> Message:
    raw = RawMessage.from_api({
        "msg": text, "sender": "u", "dname": "D",
        "msgid": "1", "msguid": "1-1", "chid": "c", "time": 1,
    })
    m = Message(FakeBot(), raw)
    m.chat_id = "c"
    return m


def test_is_command_and_args():
    m = _mk("/echo hello world")
    assert m.is_command is True
    assert m.command == "echo"
    assert m.args == ["hello", "world"]


def test_non_command():
    m = _mk("hello")
    assert m.is_command is False
    assert m.command == ""
    assert m.args == []


def test_command_strips_bot_mention():
    m = _mk("/start@mybot")
    assert m.command == "start"


def test_reply_plaintext_path():
    m = _mk("hi")
    m.reply("pong")
    assert m._bot.client.sent == [("c", "pong")]
