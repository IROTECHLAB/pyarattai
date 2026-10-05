from __future__ import annotations

from pyarattai.models import Chat, Message


def test_chat_from_api_parses_last_message_json():
    raw = {
        "id": "c1",
        "type": 0,
        "title": "T",
        "last_message": '{"msg":"hi","meta":{"enc":true}}',
    }
    c = Chat.from_api(raw)
    assert c.id == "c1"
    assert c.last_message["msg"] == "hi"
    assert c.last_message["meta"]["enc"] is True


def test_message_from_api_basic():
    raw = {
        "msg": "hello",
        "sender": "u1",
        "dname": "Alice",
        "mtype": 0,
        "msgid": "1",
        "msguid": "1-1",
        "time": 1700000000000,
        "is_read": True,
        "meta": '{"foo":"bar"}',
    }
    m = Message.from_api(raw)
    assert m.text == "hello"
    assert m.sender == "u1"
    assert m.meta == {"foo": "bar"}
    assert m.is_encrypted is False


def test_message_is_encrypted():
    m = Message.from_api({"msg": "cipher", "meta": {"enc": True}})
    assert m.is_encrypted is True
