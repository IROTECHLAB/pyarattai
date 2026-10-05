"""List every chat your account can see."""
from pyarattai import ArattaiClient

client = ArattaiClient.from_session()
for c in client.get_chats():
    print(f"{c.id}\ttype={c.type}\t{c.title}")
