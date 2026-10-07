"""List every chat with type + E2EE status."""
from pyarattai import ArattaiClient

client = ArattaiClient.from_session()
for c in client.get_chats():
    tag = "E2EE" if c.is_e2ee else "    "
    print(f"{tag}  [{c.type or '?':15}] {(c.title or '')[:30]:30}  {c.id}")
