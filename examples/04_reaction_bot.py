"""React 👍 to every incoming message in a specific chat."""
from pyarattai import ArattaiBot

TARGET_CHAT = "your-chat-id-here"

bot = ArattaiBot("91XXXXXXXXXX")


@bot.on_message(filters=lambda m: m.chat_id == TARGET_CHAT)
def react(msg):
    try:
        msg.react("👍")
    except Exception as e:  # noqa: BLE001
        print("react failed:", e)


if __name__ == "__main__":
    bot.run()
