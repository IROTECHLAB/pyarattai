"""Command-line interface for pyarattai."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .auth import Auth
from .bot import ArattaiBot
from .client import ArattaiClient
from .constants import SESSION_FILE
from .errors import ArattaiError


def _cmd_login(args: argparse.Namespace) -> int:
    path = os.path.expanduser(args.session or SESSION_FILE)
    try:
        ArattaiClient.login(args.phone, path)
    except ArattaiError as e:
        print(f"login failed: {e}", file=sys.stderr)
        return 1
    print(f"session saved to {path}")
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    path = os.path.expanduser(args.session or SESSION_FILE)
    if os.path.exists(path):
        os.remove(path)
        print(f"removed {path}")
    else:
        print("no session file")
    return 0


def _cmd_chats(args: argparse.Namespace) -> int:
    c = ArattaiClient.from_session(args.session or SESSION_FILE)
    for ch in c.get_chats():
        print(f"{ch.id}\t{ch.type}\t{ch.title or ''}")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    c = ArattaiClient.from_session(args.session or SESSION_FILE)
    for m in c.get_messages(args.chid, n=args.limit):
        flag = "ENC" if m.is_encrypted else "   "
        print(f"{flag} [{m.time}] {m.dname or m.sender}: {m.text}")
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    c = ArattaiClient.from_session(args.session or SESSION_FILE)
    c.send(args.chid, args.text)
    print("sent")
    return 0


def _cmd_daemon(args: argparse.Namespace) -> int:
    from . import daemon

    if args.action == "start":
        pid = daemon.start()
        print(f"daemon started pid={pid}")
    elif args.action == "stop":
        ok = daemon.stop()
        print("stopped" if ok else "not running")
    elif args.action == "status":
        pid = daemon.status()
        print(f"running pid={pid}" if pid else "not running")
    return 0


def _cmd_bot(args: argparse.Namespace) -> int:
    if args.action != "init":
        print("only `bot init <name>` is supported", file=sys.stderr)
        return 1
    target = args.name if args.name.endswith(".py") else f"{args.name}.py"
    if os.path.exists(target):
        print(f"{target} already exists", file=sys.stderr)
        return 1
    with open(target, "w") as f:
        f.write(
            "from pyarattai import ArattaiBot\n\n"
            f'bot = ArattaiBot("91XXXXXXXXXX")\n\n'
            "@bot.on_command(\"start\", description=\"Greet\")\n"
            "def cmd_start(msg):\n"
            "    msg.reply(\"Hello!\")\n\n"
            "@bot.on_message()\n"
            "def handle(msg):\n"
            "    if msg.text == \"/echo\":\n"
            "        msg.reply(\"pong\")\n\n"
            "if __name__ == \"__main__\":\n"
            "    bot.run()\n"
        )
    print(f"created {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pyarattai", description="Arattai client CLI")
    p.add_argument("--version", action="version", version=f"pyarattai {__version__}")
    p.add_argument("--session", default=None, help="path to session.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("login", help="interactive OTP login")
    sp.add_argument("phone")
    sp.set_defaults(func=_cmd_login)

    sp = sub.add_parser("logout", help="delete the saved session")
    sp.set_defaults(func=_cmd_logout)

    sp = sub.add_parser("chats", help="list chats")
    sp.set_defaults(func=_cmd_chats)

    sp = sub.add_parser("read", help="read last N messages")
    sp.add_argument("chid")
    sp.add_argument("limit", nargs="?", type=int, default=30)
    sp.set_defaults(func=_cmd_read)

    sp = sub.add_parser("send", help="send a message")
    sp.add_argument("chid")
    sp.add_argument("text")
    sp.set_defaults(func=_cmd_send)

    sp = sub.add_parser("daemon", help="manage the E2EE daemon")
    sp.add_argument("action", choices=["start", "stop", "status"])
    sp.set_defaults(func=_cmd_daemon)

    sp = sub.add_parser("bot", help="bot helpers")
    sp.add_argument("action", choices=["init"])
    sp.add_argument("name")
    sp.set_defaults(func=_cmd_bot)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
