"""A full bot with commands, help, and typing feedback."""
import time
from pyarattai import ArattaiBot

bot = ArattaiBot("91-XXXXXXXXXX")


@bot.on_command("start", description="Show welcome message")
def cmd_start(msg):
    msg.reply(f"👋 Hello {msg.dname}!\nUse /help to see commands.")


@bot.on_command("help", description="List available commands")
def cmd_help(msg):
    lines = ["📖 *Available commands:*"]
    for name, h in bot._command_handlers.items():
        lines.append(f"  /{name} — {h.description or h.help_text}")
    msg.reply("\n".join(lines))


@bot.on_command("ping", description="Check bot is alive")
def cmd_ping(msg):
    bot.typing(msg.chat_id, idle=False)
    time.sleep(0.5)
    bot.typing(msg.chat_id, idle=True)
    msg.reply("🏓 pong")


@bot.on_command("echo", description="Echo text back")
def cmd_echo(msg):
    msg.reply(" ".join(msg.args))


@bot.on_message()
def log(msg):
    if not msg.is_command:
        print(f"[{msg.chat_id[:20]}] {msg.dname}: {msg.text[:80]}")


if __name__ == "__main__":
    bot.run()
