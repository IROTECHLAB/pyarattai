"""Show a typing indicator for 3s, then send a reply.

Demonstrates client.typing() and the fact that each send opens its
own short WS to grab a fresh SID.
"""
import time
from pyarattai import ArattaiClient

CHAT = "your-chat-id-here"   # replace

c = ArattaiClient.from_session()
print("typing...")
c.typing(CHAT, idle=False)
time.sleep(3)
c.typing(CHAT, idle=True)
c.send(CHAT, "typing demo complete")
print("done")
