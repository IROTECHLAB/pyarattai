"""Talk to the local E2EE daemon.

Requires a Node daemon listening on 127.0.0.1:8766 that implements
the protocol in pyarattai.e2ee (see pyarattai/daemon/). Ships as
`pyarattai[daemon]` extras — the bundled arattai-daemon.cjs is a
stub and needs to be replaced with a real libsignal implementation.
"""
from pyarattai import E2EEBridge

bridge = E2EEBridge()
print("status:", bridge.status())
for chat in bridge.chats()[:5]:
    print("chat:", chat)
    for m in bridge.transcript(chat["id"], limit=10):
        print("  ", m)
