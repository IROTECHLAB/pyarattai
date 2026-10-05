"""Echo bot — replies to /echo and /start."""
from pyarattai import ArattaiBot

bot = ArattaiBot("91XXXXXXXXXX")  # replace with your number


@bot.on_command("start", description="Greet the user")
def cmd_start(msg):
    msg.reply("Hello! Use /echo <text>.")


@bot.on_command("echo", description="Echo text back")
def cmd_echo(msg):
    msg.reply(" ".join(msg.args))


if __name__ == "__main__":
    bot.run()
