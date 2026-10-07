"""Send a message. Auto-routes E2EE / plaintext based on chat."""
import sys
from pyarattai import ArattaiClient

if len(sys.argv) < 3:
    print("usage: 03_send_message.py <chat_id> <text>")
    raise SystemExit(1)

client = ArattaiClient.from_session()
msg = client.send(sys.argv[1], " ".join(sys.argv[2:]))
print(f"sent: {msg.msgid}")
print(f"cache state: {client._e2ee_chats}")
