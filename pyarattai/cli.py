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


def _cmd_signal(args: argparse.Namespace) -> int:
    """Manage the Signal E2EE key store."""
    from .signal import (
        SIGNAL_FILE, NODE_MODS, install_signal_helpers,
        ensure_libsignal, ensure_node_deps,
    )

    if args.action == "status":
        try:
            sig = ensure_libsignal()
            print(f"libsignal: {sig} ({sig.stat().st_size} bytes)")
        except Exception as e:
            print(f"libsignal: MISSING ({e})")
        print(f"signal file: {SIGNAL_FILE} exists={SIGNAL_FILE.exists()}")
        print(f"node_modules: {NODE_MODS} exists={NODE_MODS.exists()}")
        if SIGNAL_FILE.exists():
            import json as _json
            blob = _json.loads(SIGNAL_FILE.read_text())
            print(f"  registration_id: {blob.get('registrationId')}")
            print(f"  pre_keys:        {len(blob.get('pre_keys') or [])}")
            print(f"  sessions:        {len(blob.get('sessions') or [])}")
        return 0

    if args.action == "install":
        install_signal_helpers(force=True)
        print("wrote decrypt helper")
        try:
            p_ = ensure_libsignal()
            print(f"libsignal: {p_}")
        except Exception as e:
            print(f"libsignal setup failed: {e}")
        ensure_node_deps(silent=False)
        return 0

    if args.action == "download":
        try:
            from .signal import download_libsignal
            p_ = download_libsignal(force=True)
            print(f"libsignal: {p_}")
            return 0
        except Exception as e:
            print(f"download failed: {e}", file=sys.stderr)
            return 1

    if args.action == "register":
        from .client import ArattaiClient
        from .signal import register_signal_keys, SIGNAL_FILE
        try:
            c = ArattaiClient.from_session(args.session or SESSION_FILE)
        except Exception as e:
            print(f"cannot load session: {e}", file=sys.stderr)
            return 1
        if not c.device_id:
            # Assign a fresh device id. The server will associate it
            # with this account on the first /v2/keys/register POST and
            # return it (plus e2ee_registration_id) as Set-Cookie.
            import secrets as _sec
            import json as _json
            new_id = str(_sec.randbelow(8_000_000) + 1_000_000)
            c.device_id = new_id
            sf = os.path.expanduser(args.session or SESSION_FILE)
            blob = _json.loads(open(sf).read())
            blob["device_id"] = new_id
            open(sf, "w").write(_json.dumps(blob, indent=2))
            os.chmod(sf, 0o600)
            print(f"[signal] assigned device_id={new_id}")
        try:
            print(f"→ generating Signal keys for device {c.device_id}")
            resp = register_signal_keys(c, int(c.device_id))
            print(f"→ POST /v2/keys/register -> {resp}")
            print(f"→ wrote {SIGNAL_FILE}")

            # Persist any cookies the register response set
            # (e2ee_registration_id, e2ee_device_id, etc.) plus the
            # device_id we assigned above, so subsequent CLI invocations
            # see them.
            import json as _json
            sf = os.path.expanduser(args.session or SESSION_FILE)
            blob = _json.loads(open(sf).read())
            blob["device_id"] = str(c.device_id) if c.device_id else blob.get("device_id")
            blob["registration_id"] = str(c.registration_id) if c.registration_id else blob.get("registration_id")
            blob["cookies"] = [
                {"name": ck.name, "value": ck.value, "domain": ck.domain,
                 "path": ck.path, "secure": ck.secure, "expires": ck.expires}
                for ck in c.s.http.cookies
            ]
            open(sf, "w").write(_json.dumps(blob, indent=2))
            os.chmod(sf, 0o600)
            print(f"→ session saved ({len(blob['cookies'])} cookies)")
            return 0
        except Exception as e:
            print(f"register failed: {e}", file=sys.stderr)
            return 1

    print(f"unknown signal action: {args.action}", file=sys.stderr)
    return 1


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

    sp = sub.add_parser("signal", help="manage Signal E2EE key store")
    sp.add_argument("action", choices=["status", "install", "download", "register"])
    sp.set_defaults(func=_cmd_signal)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
