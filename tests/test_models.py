"""Tests for pyarattai.models."""
from __future__ import annotations

from pyarattai.models import Chat, Message


# ---- Chat ---------------------------------------------------------

def test_chat_parses_last_message_json_string():
    raw = {
        "id": "c1",
        "type": "dm",
        "title": "T",
        "last_message": '{"msg":"hi","meta":{"enc":true}}',
    }
    c = Chat.from_api(raw)
    assert c.id == "c1"
    assert c.last_message["msg"] == "hi"
    assert c.last_message["meta"]["enc"] is True


def test_chat_is_group_by_chid_suffix():
    assert Chat.from_api({"id": "X-GC"}).is_group is True
    assert Chat.from_api({"id": "X-SM"}).is_group is False
    assert Chat.from_api({"id": "12345"}).is_group is False


def test_chat_is_e2ee_signals():
    # explicit field
    assert Chat.from_api({"id": "x", "e2ee": True}).is_e2ee is True
    # addinfo marker
    assert Chat.from_api({"id": "x", "addinfo": "...:E2EE:1"}).is_e2ee is True
    # dm type
    assert Chat.from_api({"id": "x", "type": "dm"}).is_e2ee is True
    # chat_type 1
    assert Chat.from_api({"id": "x", "chat_type": 1}).is_e2ee is True
    # last msg encrypted
    assert Chat.from_api({
        "id": "x",
        "last_message": '{"meta":{"enc":true}}',
    }).is_e2ee is True
    # non-e2ee group
    assert Chat.from_api({"id": "X-GC"}).is_e2ee is False


# ---- Message ------------------------------------------------------

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
    assert m.dname == "Alice"
    assert m.meta == {"foo": "bar"}
    assert m.is_encrypted is False


def test_message_is_encrypted():
    m = Message.from_api({"msg": "cipher", "meta": {"enc": True}})
    assert m.is_encrypted is True


def test_message_nested_meta():
    """Arattai sometimes double-encodes meta."""
    raw = {"msg": "hi", "meta": '{"enc":true,"enc_keys":"{}"}'}
    m = Message.from_api(raw)
    assert isinstance(m.meta, dict)
    assert m.meta.get("enc") is True
