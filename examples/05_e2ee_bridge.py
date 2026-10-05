"""Talk to the local E2EE daemon (must be running)."""
from pyarattai import E2EEBridge

bridge = E2EEBridge()
print("status:", bridge.status())
for chat in bridge.chats()[:5]:
    print("chat:", chat)
    for m in bridge.transcript(chat["id"], limit=10):
        print("  ", m)
