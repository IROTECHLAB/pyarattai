# irotechlab-pyarattai

A Python client + bot framework for [Arattai](https://arattai.in).

TeleBot-style API. End-to-end encrypted DM send & receive. WebSocket push. Runs on Termux, Linux, macOS.

    from pyarattai import ArattaiBot

    bot = ArattaiBot("91-XXXXXXXXXX")   # prompts for OTP on first run

    @bot.on_command("start")
    def cmd_start(msg):
        msg.reply("Hello!")

    @bot.on_message()
    def handle(msg):
        if msg.text.startswith("/echo "):
            msg.reply(msg.text[6:])

    bot.run()

That's it. First run prompts for OTP, auto-registers Signal keys, establishes a WebSocket, and starts dispatching messages.

## Install

    pip install irotechlab-pyarattai

Requires Python 3.9+ and Node.js 16+. Install Node with `pkg install nodejs` on Termux, `brew install node` on macOS, or `apt install nodejs` on Debian.

The first run copies the bundled `libsignal-protocol.js` (1.4 MB) into `~/.pyarattai/` and runs `npm install bytebuffer long protobufjs` into `~/.pyarattai/node_modules`. This is one-time setup.

## Authentication

Login is interactive on the first run. You'll see a prompt like:

    App OTP sent to 91-82******67 (check the Arattai app)
    Enter OTP  ('resend' = new code, 'sms'/'app' = switch channel):

Type the 6-digit code from the Arattai app. If the app notification doesn't arrive, type `sms` to force SMS delivery.

The session is cached in `~/.pyarattai/session.json` (mode 0600). Every subsequent run reuses it — no OTP prompt.

To start fresh:

    from pyarattai import ArattaiClient
    ArattaiClient.from_session().logout()   # server-side logout + local cleanup

## Bot API

Construct a bot:

    from pyarattai import ArattaiBot

    bot = ArattaiBot(
        "91-XXXXXXXXXX",
        transport="ws",          # "ws" (default, push) or "http" (poll)
        poll_interval=15.0,      # only used when transport="http"
        otp_channel="app",       # "app" | "sms" | "auto"
    )

Register handlers:

    @bot.on_command("echo", description="Echo text")
    def cmd_echo(msg):
        msg.reply(" ".join(msg.args))

    @bot.on_message(filters=lambda m: m.chat_id == "CT_...")
    def handle(msg):
        print(msg.dname, "said", msg.text)

    @bot.on_reaction(emoji="👍")
    def on_react(msg, emoji, sender):
        print(f"{sender} reacted with {emoji}")

Message object fields:

    msg.chat_id      str
    msg.sender       uid of the sender
    msg.dname        display name
    msg.text         decrypted text (E2EE is decrypted automatically)
    msg.msgid
    msg.msguid
    msg.time
    msg.is_read
    msg.is_command   True for "/cmd ..."
    msg.command      "cmd"
    msg.args         ["arg1", "arg2"]

Message actions:

    msg.reply("hi")          # send a message to this chat
    msg.react("❤")           # react to this message
    msg.edit("new text")     # edit (bot's own messages only)
    msg.delete()             # delete this message

Bot helpers:

    bot.send(chat_id, "text")             # send message (auto E2EE routing)
    bot.typing(chat_id)                   # show typing indicator
    bot.typing(chat_id, idle=True)        # clear it
    bot.run(single=False)                 # blocking loop; single=True → one pass
    bot.stop()                            # graceful shutdown

## Client API

For non-bot use cases, use `ArattaiClient` directly:

    from pyarattai import ArattaiClient

    client = ArattaiClient.from_session()          # uses cached session
    # or: client = ArattaiClient.login("91-...")   # forces OTP

    client.get_chats()                             # list[Chat]
    client.get_chat(chat_id)                       # Chat
    client.get_messages(chat_id, n=50)             # list[Message]
    client.send(chat_id, "text")                   # auto-routes E2EE / plaintext

    client.typing(chat_id)                         # show typing
    client.typing(chat_id, idle=True)              # clear typing
    client.react(chat_id, msguid, "👍")
    client.unreact(chat_id, msguid, "👍")
    client.mark_read(chat_id, msguid)

    client.get_participants(chat_id)
    client.add_member(chat_id, [uid])
    client.remove_member(chat_id, [uid])
    client.update_chat(chat_id, title="New", description="...")
    client.set_role(chat_id, uid, role=1)          # 1 = admin

    client.get_permalink(chat_id)                  # {link, validity}
    client.join_by_invite(token)
    client.leave_chat(chat_id)

    client.get_channels()
    client.join_channel("@username")

    client.logout()                                # server-side + local cleanup

### Automatic E2EE routing

`client.send()` learns from the server. On the first message to a chat it tries the plaintext endpoint; if the server rejects it (E2EE-only chat), the client retries via Signal E2EE and caches the decision. Subsequent sends go straight to the right endpoint.

The cache lives in `session.json` under `_e2ee_chats`, so the CLI reuses it between invocations.

## E2EE

### DMs

Fully supported. Pairwise Signal encryption via a vendored, pure-JS `libsignal-protocol` bundle. Messages arriving on the WebSocket with `meta.enc: true` are decrypted transparently; replies are encrypted with the same pipeline.

### Groups

**Not supported in this version.** Group E2EE requires Signal SenderKeys (the `GroupCipher` and `GroupSessionBuilder` classes), which the vendored 2021 libsignal bundle does not expose. Scheduled for v0.2.

Non-E2EE groups work as plaintext. E2EE groups raise a clear error: `ArattaiError: group E2EE is not supported in this version`.

### Signal store

Signal keys live in `~/.pyarattai/signal-storage.json`. Register once with `pyarattai signal register` — or just run your bot; login auto-registers the first time.
