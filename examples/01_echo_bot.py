"""Echo bot — the 15-line quickstart.

First run: prompts for OTP, auto-registers Signal keys, connects WS.
Subsequent runs: loads the cached session, no prompt.
"""
from pyarattai import ArattaiBot

bot = ArattaiBot("91-XXXXXXXXXX")  # replace with your number


@bot.on_command("start", description="Greet the user")
def cmd_start(msg):
    msg.reply(f"Hello {msg.dname}!")


@bot.on_command("echo", description="Echo text back")
def cmd_echo(msg):
    msg.reply(" ".join(msg.args) or "(nothing to echo)")


if __name__ == "__main__":
    bot.run()
