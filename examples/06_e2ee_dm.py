"""Send an E2EE DM using SignalBridge directly.

For most cases client.send() handles this automatically. This example
shows the underlying SignalBridge API so you can build custom flows.
"""
from pyarattai import ArattaiClient, SignalBridge

CHAT = "1463202197010258475"   # IRONMAN's DM in the reference setup
RECIPIENT = "20031897759"

c = ArattaiClient.from_session()
sid = c._require_sid()   # WS-issued SID

bridge = SignalBridge(uid=c.uid)
bridge.send(c, CHAT, RECIPIENT, "hello via SignalBridge", sid=sid)
print("sent via E2EE bridge")
