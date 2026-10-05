from __future__ import annotations

from pyarattai.bot import Message
from pyarattai.models import Message as RawMessage


class _FakeBot:
    def __init__(self) -> None:
        self.sent = []

    class client:  # noqa: N801
        uid = "me"
        def send(self, chat_id, text):  # noqa: D401
            return RawMessage.from_api(
                {"msg": text, "sender": "me", "chid": chat_id,
                 "msgid": "1", "msguid": "1-1"}
            )


def _mk(text: str) -> Message:
    return Message(_FakeBot(), RawMessage.from_api(
        {"msg": text, "sender": "u", "dname": "D", "msgid": "1",
         "msguid": "1-1", "chid": "c", "time": 1}
    ))


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
