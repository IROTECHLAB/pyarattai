"""Signal / E2EE support for pyarattai.

Architecture
------------
Every Signal operation runs in a short-lived Node subprocess that uses
the pure-JS ``libsignal-protocol`` bundle. The Node script:

  * loads the shared Signal store from ``~/.pyarattai/signal-storage.json``
  * performs one operation (keygen / decrypt / encrypt)
  * **persists the store back to disk** before exiting

Because every invocation reads and writes the same file, ratchet state
survives across Python processes — this is what makes decryption work
even though we spawn a new Node process each time.

Rules
-----
* Never delete sessions implicitly. Sessions are managed by libsignal.
* Never send anything on stdout except one marker-prefixed payload.
  All debug output goes to stderr.
"""
from __future__ import annotations

import os

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ArattaiError

__all__ = [
    "SignalBridge",
    "install_signal_helpers",
    "ensure_libsignal",
    "ensure_node_deps",
    "register_signal_keys",
    "run_keygen",
]

log = logging.getLogger("pyarattai.signal")

# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

HOME = Path(os.path.expanduser("~"))
PROJ = HOME / ".pyarattai"
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
NODE_MODS = PROJ / "node_modules"
SIGNAL_FILE = PROJ / "signal-storage.json"
LIBSIGNAL_VENDORED = (HOME / "arattai-js" / "vendor-libsignal"
                      / "dist" / "libsignal-protocol.js")
LIBSIGNAL_WEB = PROJ / "arattai-app" / "js" / "libsignal-min.js"

KEYGEN_JS = PROJ / ".keygen.cjs"
DECRYPT_JS = PROJ / ".decrypt.cjs"
SEND_JS = PROJ / ".send.cjs"

MARK_DECRYPT = "PYA_DECRYPT\x00"
MARK_SEND = "PYA_SEND\x00"
MARK_KEYGEN = "PYA_KEYGEN\x00"

# ---------------------------------------------------------------------------
# vendor loading
# ---------------------------------------------------------------------------

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


def _vendor(name: str) -> str:
    """Read a file from the packaged ``vendor/`` directory."""
    p = VENDOR_DIR / name
    if not p.exists():
        raise ArattaiError(
            f"pyarattai install is missing {name!r}; reinstall the package"
        )
    return p.read_text()


def _vendor_path(name: str) -> Path:
    p = VENDOR_DIR / name
    if not p.exists():
        raise ArattaiError(
            f"pyarattai install is missing {name!r}; reinstall the package"
        )
    return p


# ---------------------------------------------------------------------------
# write helpers
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def install_signal_helpers(force: bool = False) -> None:
    """Copy the vendored Node scripts into ``~/.pyarattai/``.

    On first run this also copies ``libsignal-protocol.js``.
    """
    PROJ.mkdir(parents=True, exist_ok=True)
    for name in ("libsignal-protocol.js", "keygen.cjs", "decrypt.cjs", "send.cjs"):
        dest = PROJ / name if name.endswith(".js") else PROJ / ("." + name)
        src = VENDOR_DIR / name
        if not src.exists():
            raise ArattaiError(
                f"pyarattai package missing vendor/{name}; reinstall it"
            )
        if force or not dest.exists():
            dest.write_text(src.read_text())


def ensure_libsignal() -> Path:
    """Return path to a working libsignal JS bundle.

    Order:
      1. ~/.pyarattai/libsignal-protocol.js (copied on install)
      2. bundled vendor/libsignal-protocol.js
    """
    install_signal_helpers()
    for c in [PROJ / "libsignal-protocol.js",
              VENDOR_DIR / "libsignal-protocol.js"]:
        if c.exists():
            return c
    raise ArattaiError(
        "libsignal bundle missing; reinstall irotechlab-pyarattai"
    )


def ensure_node_deps(silent: bool = False) -> bool:
    """Install bytebuffer/long/protobufjs into ~/.pyarattai/."""
    need = ["bytebuffer@4.1.0", "long@3.2.0", "protobufjs@4.1.3"]
    if all((NODE_MODS / n.split("@")[0]).exists() for n in need):
        return True
    npm = shutil.which("npm")
    if not npm:
        if not silent:
            if os.environ.get("PYARATTAI_DEBUG"):
                print("[signal] npm not found — install Node.js")
        return False
    PROJ.mkdir(parents=True, exist_ok=True)
    if not (PROJ / "package.json").exists():
        (PROJ / "package.json").write_text(
            '{"name":"pyarattai","private":true,"version":"0.0.0"}\n')
    if not silent:
        if os.environ.get("PYARATTAI_DEBUG"):
            print(f"[signal] npm install in {PROJ}")
    r = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund", "--silent",
         "--save-exact", *need],
        cwd=str(PROJ), capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        if not silent:
            if os.environ.get("PYARATTAI_DEBUG"):
                print(f"[signal] npm install failed:\n{r.stderr[-400:]}")
        return False
    return True


# ---------------------------------------------------------------------------
# node invocation
# ---------------------------------------------------------------------------

def _run_node(
    script: Path, *args: str, stdin: Optional[Any] = None,
    my_uid: Optional[str] = None, my_device_id: Optional[str] = None,
    timeout: float = 90.0,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LIBSIGNAL_PATH"] = str(ensure_libsignal())
    env["NODE_PATH"] = str(NODE_MODS)
    if my_uid:
        env["MY_UID"] = str(my_uid)
    if my_device_id:
        env["MY_DEVICE_ID"] = str(my_device_id)
    node = shutil.which("node") or "node"
    return subprocess.run(
        [node, "--no-warnings", "--unhandled-rejections=warn",
         str(script), *args],
        input=json.dumps(stdin) if stdin is not None else None,
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def _extract_marked(stdout: str, marker: str) -> Optional[str]:
    """Return the substring after the first occurrence of ``marker``."""
    idx = stdout.find(marker)
    if idx < 0:
        return None
    return stdout[idx + len(marker):]


# ---------------------------------------------------------------------------
# keygen + register
# ---------------------------------------------------------------------------

def run_keygen(device_id: int) -> Dict[str, Any]:
    """Generate fresh Signal keys; write store; return SERVER_BODY dict."""
    install_signal_helpers()
    r = _run_node(KEYGEN_JS, str(SIGNAL_FILE), str(device_id), timeout=180)
    if r.returncode != 0:
        raise ArattaiError(f"keygen failed: {r.stderr.strip()[:400]}")
    payload = _extract_marked(r.stdout, MARK_KEYGEN)
    if payload is None:
        raise ArattaiError("keygen produced no output marker")
    return json.loads(payload)


def register_signal_keys(client, device_id: int) -> Any:
    """Publish keys to /v2/keys/register (re-publishes existing store)."""
    ensure_node_deps(silent=False)
    ensure_libsignal()
    install_signal_helpers()

    if SIGNAL_FILE.exists():
        blob = json.loads(SIGNAL_FILE.read_text())
        spk = (blob.get("signed_pre_keys") or [{}])[0]
        if not spk.get("pub"):
            raise ArattaiError("signal file has no signed_prekey")
        onetime = [
            {"tag": k["tag"], "pub": k["pub"]}
            for k in blob.get("pre_keys", []) if k.get("pub")
        ]
        body = {"data": {
            "registration_id": blob["registrationId"],
            "device_id": int(device_id),
            "identity_key": {"tag": 0, "pub": blob["identityKey"]["pub"]},
            "signed_prekey": {
                "tag": spk["tag"], "pub": spk["pub"], "sign": spk["sign"],
            },
            "onetime_prekeys": onetime,
        }}
    else:
        body = run_keygen(int(device_id))

    # Mint x-tkp-token immediately before POST
    try:
        from .auth import Auth as _Auth
        a = _Auth(client.s)
        a.uid = client.uid
        a.mint_x_tkp_token()
    except Exception:
        pass

    r = client.s.request(
        "POST", "/v2/keys/register", base="chat",
        json=body, raw=True,
    )
    # Install Set-Cookie
    for ck in r.cookies:
        client.s.http.cookies.set_cookie(ck)
    if r.cookies.get("e2ee_registration_id"):
        client.registration_id = str(r.cookies.get("e2ee_registration_id"))
    if r.cookies.get("e2ee_device_id"):
        client.device_id = str(r.cookies.get("e2ee_device_id"))

    try:
        return r.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# SignalBridge
# ---------------------------------------------------------------------------

class SignalBridge:
    """High-level E2EE operations on top of the persistent Signal store."""

    def __init__(self, uid: str, signal_file: Optional[Path] = None) -> None:
        self.uid = uid
        self.signal_file = signal_file or SIGNAL_FILE
        install_signal_helpers()
        try:
            ensure_libsignal()
        except ArattaiError as e:
            log.warning("libsignal missing: %s", e)
        ensure_node_deps(silent=True)

    # ------------------------------------------------------------------ decrypt

    def decrypt(self, frame: Dict[str, Any]) -> str:
        """Decrypt a single mtype=12 frame. Returns plaintext."""
        if not self.signal_file.exists():
            raise ArattaiError(
                f"signal store missing: {self.signal_file}. "
                "Run `pyarattai signal register` first."
            )
        r = _run_node(
            DECRYPT_JS, str(self.signal_file), self.uid,
            stdin={"msg": frame}, my_uid=self.uid,
        )
        if r.returncode != 0:
            raise ArattaiError(f"decrypt failed: {r.stderr.strip()[:400]}")
        payload = _extract_marked(r.stdout, MARK_DECRYPT)
        if payload is None:
            raise ArattaiError(
                f"decrypt marker missing; stdout tail: {r.stdout[-200:]}"
            )
        return payload

    # ------------------------------------------------------------------ send

    def send(
        self,
        client,
        chat_id: str,
        recipient_uid: str,
        text: str,
        sid: Optional[str] = None,
    ) -> Any:
        """Encrypt + POST an E2EE message.

        Args:
            client: ArattaiClient with a valid session.
            chat_id: chat id.
            recipient_uid: recipient user id.
            text: plaintext.
            sid: X-SID header (from WS mtype:0). Required — the server
                rejects anything else.

        Returns:
            Server response (parsed JSON or None for 204).
        """
        # 1. Fetch bundles for recipient + ourselves
        our_dev = f"{client.uid}_{client.registration_id}_{client.device_id}"
        bundles_resp = client.s.request(
            "POST", "/v2/keys/requestbundle", base="chat",
            json={"recipients": [recipient_uid, client.uid],
                  "exclude_devices": [our_dev]},
        )
        data = ((bundles_resp or {}).get("message", {}).get("data")
                or (bundles_resp or {}).get("data") or [])
        if not data:
            raise ArattaiError("no device bundles for recipients")

        # 2. Decide mode.
        #   - DM (non group): pairwise Signal, always E2EE.
        #   - Group with E2EE:1 in addinfo: SenderKeys (group mode).
        #   - Group without E2EE: plaintext — caller should use
        #     client.send() instead of this method.
        mode = "direct"
        try:
            ch = client.get_chat(chat_id)
            if getattr(ch, "is_group", False) and getattr(ch, "is_e2ee", False):
                mode = "group"
            elif getattr(ch, "is_group", False) and not getattr(ch, "is_e2ee", False):
                raise ArattaiError(
                    f"chat {chat_id} is a non-E2EE group; use "
                    f"client.send() (plaintext) instead of "
                    f"SignalBridge.send()"
                )
        except ArattaiError:
            raise
        except Exception:
            # get_chat failed — fall back to chid suffix for DMs
            if not str(chat_id).endswith("-GC"):
                mode = "direct"

        r = _run_node(
            SEND_JS, str(self.signal_file), mode,
            stdin={"text": text, "bundles": data, "chid": chat_id},
            my_uid=self.uid, my_device_id=str(client.device_id),
        )
        if r.returncode != 0:
            raise ArattaiError(f"encrypt failed: {r.stderr.strip()[:400]}")
        payload = _extract_marked(r.stdout, MARK_SEND)
        if payload is None:
            raise ArattaiError(
                f"send marker missing; stdout tail: {r.stdout[-200:]}"
            )
        encrypted = json.loads(payload)

        # 3. POST
        sid_val = sid or client.session_id or ""
        if not sid_val:
            raise ArattaiError(
                "no X-SID available — open a WS connection first "
                "so the mtype:0 frame delivers the sid."
            )
        body = {
            "chid": chat_id,
            "msg": encrypted["msg"],
            "msgid": str(int(__import__("time").time() * 1000)),
            "sid": sid_val,
            "dname": client.mobile or "pyarattai",
            "unfurl": "false",
            "notification_text": encrypted["msg"],
            "enc_keys": json.dumps(encrypted["enc_keys"]),
        }
        if mode == "group" and encrypted.get("sender_key"):
            body["sender_key"] = json.dumps(encrypted["sender_key"])
        return client.s.request(
            "POST", "/e2ee/sendofficechatmessage.api", base="chat",
            data=body, headers={"X-SID": sid_val},
        )
