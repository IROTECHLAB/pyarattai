"""React 👍 to every incoming message in one chat."""
from pyarattai import ArattaiBot

TARGET_CHAT = "your-chat-id-here"   # replace

bot = ArattaiBot("91-XXXXXXXXXX")


@bot.on_message(filters=lambda m: m.chat_id == TARGET_CHAT)
def react(msg):
    try:
        msg.react("👍")
    except Exception as e:
        print("react failed:", e)


if __name__ == "__main__":
    bot.run()
