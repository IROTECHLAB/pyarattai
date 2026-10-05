"""Send a message to a chat id."""
import sys

from pyarattai import ArattaiClient

if len(sys.argv) < 3:
    print("usage: 03_send_message.py <chat_id> <text>")
    raise SystemExit(1)

client = ArattaiClient.from_session()
msg = client.send(sys.argv[1], sys.argv[2])
print("sent", msg.msgid)
