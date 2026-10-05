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
# shared JS preamble (bootstrap + store class)
# ---------------------------------------------------------------------------

_BOOTSTRAP = r'''
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');

(function () {
  const setG = (n, v) => { try {
    Object.defineProperty(globalThis, n,
      { value: v, writable: true, configurable: true, enumerable: false });
  } catch { try { globalThis[n] = v; } catch {} } };
  setG('window', globalThis);
  setG('self', globalThis);
  if (!globalThis.navigator) setG('navigator', { userAgent: 'node' });

  const el = () => ({
    tagName: 'x', nodeType: 1, style: {}, attributes: {},
    children: [], childNodes: [],
    body: { appendChild(){} }, head: { appendChild(){} },
    setAttribute(){}, getAttribute(){ return null; },
    hasAttribute(){ return false; },
    appendChild(c){ return c; }, removeChild(c){ return c; },
    cloneNode(){ return el(); },
    addEventListener(){}, removeEventListener(){},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    getElementsByTagName(){ return []; },
    toDataURL(){ return 'data:,'; },
    getContext(){ return new Proxy({}, { get: () => () => {}, set: () => true }); },
    createTextNode(t){ return { nodeType: 3, textContent: t }; },
  });
  const doc = {
    nodeType: 9, createElement: el, createTextNode: el,
    createDocumentFragment: el,
    createNodeIterator: () => ({ nextNode: () => null }),
    getElementsByTagName: () => [], querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(){}, removeEventListener(){},
    currentScript: null,
    baseURI: 'https://web.arattai.in/', cookie: '',
    body: el(), head: el(), documentElement: el(),
    implementation: { createHTMLDocument: () => doc },
  };
  doc.ownerDocument = doc;
  setG('document', doc);
  setG('location', {
    href: 'https://web.arattai.in/', origin: 'https://web.arattai.in',
    pathname: '/', protocol: 'https:', host: 'web.arattai.in',
  });
  if (!globalThis.screen) setG('screen', { width: 1080, height: 1920 });
  if (!globalThis.performance) setG('performance', { now: () => Date.now() });
  if (!globalThis.addEventListener) setG('addEventListener', () => {});
  if (!globalThis.removeEventListener) setG('removeEventListener', () => {});
  if (!globalThis.MutationObserver) setG('MutationObserver',
    class { observe(){} disconnect(){} takeRecords(){ return []; } });

  // resolve deps from ~/.pyarattai/node_modules
  const PROJ = path.join(os.homedir(), '.pyarattai');
  const Module = require('module');
  const orig = Module._resolveFilename;
  Module._resolveFilename = function (req, ...a) {
    if (['bytebuffer', 'long', 'protobufjs'].includes(req))
      return orig.call(this, path.join(PROJ, 'node_modules', req), ...a);
    return orig.call(this, req, ...a);
  };

  const BB = require('bytebuffer');
  const Long = require('long');
  const protobuf = require('protobufjs');
  const BBClass = BB.ByteBuffer || BB;
  BBClass.Long = Long;

  const ProtoBuf = protobuf.ProtoBuf || protobuf;
  if (!ProtoBuf.loadProto && protobuf.loadProto)
    ProtoBuf.loadProto = protobuf.loadProto;
  if (!protobuf.ProtoBuf) protobuf.ProtoBuf = ProtoBuf;

  setG('ByteBuffer', BBClass);
  setG('Long', Long);
  setG('protobuf', protobuf);
  setG('dcodeIO', {
    ByteBuffer: BBClass, Long,
    protobuf, ProtoBuf,
  });
})();

const LIB = process.env.LIBSIGNAL_PATH;
if (!LIB || !fs.existsSync(LIB)) {
  console.error('no libsignal at ' + LIB); process.exit(2);
}
let libsignal;
try {
  const required = require(LIB);
  libsignal = globalThis.libsignal || globalThis.Signal ||
             (required && required.default) || required;
} catch (e) {
  console.error('libsignal load failed: ' + e.message); process.exit(2);
}
if (!libsignal || !libsignal.Curve) {
  console.error('libsignal has no Curve; keys=' +
    Object.keys(libsignal || {}).join(','));
  process.exit(3);
}

// libsignal throws from inside its own .then chains for malformed
// ciphertext. Node 26 exits on unhandled rejections, so install a
// global handler that swallows them. The individual operation code
// checks the return value / catches to decide success or failure.
process.on('unhandledRejection', (e) => {
  // no-op: we already track the error via safeTry's resolve
});
process.on('uncaughtException', (e) => {
  // no-op: same reason
});
'''

_STORE = r'''
function b64buf(b64) {
  const b = Buffer.from(b64, 'base64');
  const u = new Uint8Array(b.length);
  u.set(b);
  return u.buffer;
}
function bufb64(v) {
  if (v == null) return null;
  if (Buffer.isBuffer(v)) return v.toString('base64');
  return Buffer.from(new Uint8Array(v)).toString('base64');
}

class PersistentStore {
  constructor(jsonPath) {
    this.path = jsonPath;
    const raw = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
    this._sessions = new Map();
    for (const s of raw.sessions || []) this._sessions.set(s.id, s.session);
    this._preKeys = new Map();
    for (const k of raw.pre_keys || [])
      this._preKeys.set(Number(k.tag), { pub: k.pub, priv: k.priv });
    this._signedPreKeys = new Map();
    for (const k of raw.signed_pre_keys || [])
      this._signedPreKeys.set(Number(k.tag), k);
    this._identityKey = raw.identityKey || null;
    this._registrationId = raw.registrationId || 0;
    this.Direction = { SENDING: 0, RECEIVING: 1 };
  }

  persist() {
    const out = {
      identityKey: this._identityKey,
      registrationId: this._registrationId,
      signed_pre_keys: [...this._signedPreKeys.values()],
      pre_keys: [...this._preKeys.entries()].map(([tag, k]) => ({
        tag, pub: k.pub, priv: k.priv,
      })),
      sessions: [...this._sessions.entries()].map(([id, session]) => ({
        id, session,
      })),
      sender_keys: [], aes_keys: [],
    };
    const tmp = this.path + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(out, null, 2));
    fs.renameSync(tmp, this.path);
  }

  async loadSession(a){ return this._sessions.get(a); }
  async storeSession(a, r){ this._sessions.set(a, r); this.persist(); }
  async removeSession(a){ this._sessions.delete(a); this.persist(); }
  async removeAllSessions(a){ this._sessions.delete(a); this.persist(); }
  async loadPreKey(id){
    const k = this._preKeys.get(Number(id));
    if (!k) return undefined;
    return { pubKey: b64buf(k.pub), privKey: b64buf(k.priv) };
  }
  async storePreKey(id, r){
    this._preKeys.set(Number(id),
      { pub: bufb64(r.pubKey), priv: bufb64(r.privKey) });
    this.persist();
  }
  async removePreKey(id){ this._preKeys.delete(Number(id)); this.persist(); }
  async loadSignedPreKey(id){
    const k = this._signedPreKeys.get(Number(id));
    if (!k) return undefined;
    return {
      pubKey: b64buf(k.pub), privKey: b64buf(k.priv),
      signature: b64buf(k.sign),
    };
  }
  async loadSignedPreKeys(){
    const out = [];
    for (const [id, rec] of this._signedPreKeys) out.push({ id, ...rec });
    return out;
  }
  async storeSignedPreKey(id, r){
    this._signedPreKeys.set(Number(id), {
      tag: Number(id), is_default: true,
      pub: bufb64(r.pubKey), priv: bufb64(r.privKey),
      sign: bufb64(r.signature), time: Date.now(),
    });
    this.persist();
  }
  async removeSignedPreKey(id){
    this._signedPreKeys.delete(Number(id)); this.persist();
  }
  async getIdentityKeyPair(){
    if (!this._identityKey) throw new Error('no identity');
    return {
      pubKey: b64buf(this._identityKey.pub),
      privKey: b64buf(this._identityKey.priv),
    };
  }
  async getLocalRegistrationId(){ return this._registrationId; }
  async isTrustedIdentity(){ return true; }
  async saveIdentity(){ return false; }
  async loadIdentityKey(){ return null; }
}
'''

# ---------------------------------------------------------------------------
# keygen
# ---------------------------------------------------------------------------

_KEYGEN = _BOOTSTRAP + r'''
const toB64 = v => {
  if (v == null) throw new Error('null key');
  if (typeof v === 'string') return v;
  if (Buffer.isBuffer(v)) return v.toString('base64');
  if (v instanceof Uint8Array) return Buffer.from(v).toString('base64');
  if (v instanceof ArrayBuffer) return Buffer.from(new Uint8Array(v)).toString('base64');
  if (ArrayBuffer.isView(v))
    return Buffer.from(new Uint8Array(v.buffer, v.byteOffset, v.byteLength)).toString('base64');
  if (v.data) return toB64(v.data);
  if (Array.isArray(v)) return Buffer.from(v).toString('base64');
  if (v.buffer && v.byteLength != null) return toB64(v.buffer);
  throw new Error('unknown key shape: ' + Object.prototype.toString.call(v));
};

(async () => {
  const outPath = process.argv[2];
  const deviceId = process.argv[3];
  const Curve = libsignal.Curve;
  const KeyHelper = libsignal.KeyHelper;
  if (!Curve || !KeyHelper) {
    console.error('libsignal missing Curve/KeyHelper'); process.exit(3);
  }

  const idKp = Curve.generateKeyPair();
  const regId = Math.floor(Math.random() * 16380) + 1;
  const maybe = v => (v && typeof v.then === 'function') ? v : Promise.resolve(v);

  const preKeys = [];
  for (let tag = 1; tag <= 100; tag++)
    preKeys.push(await maybe(KeyHelper.generatePreKey(tag)));
  const spk = await maybe(KeyHelper.generateSignedPreKey(idKp, 1));

  const state = {
    identityKey: { priv: toB64(idKp.privKey), pub: toB64(idKp.pubKey) },
    registrationId: regId,
    signed_pre_keys: [{
      tag: spk.keyId || 1, is_default: true,
      pub: toB64(spk.keyPair.pubKey), priv: toB64(spk.keyPair.privKey),
      sign: Buffer.from(spk.signature).toString('base64'),
      time: Date.now(),
    }],
    pre_keys: preKeys.map(pk => ({
      tag: pk.keyId,
      pub: toB64(pk.keyPair.pubKey),
      priv: toB64(pk.keyPair.privKey),
    })),
    sessions: [], sender_keys: [], aes_keys: [],
  };
  fs.writeFileSync(outPath, JSON.stringify(state, null, 2));

  const body = { data: {
    registration_id: regId,
    device_id: parseInt(deviceId, 10),
    identity_key: { tag: 0, pub: state.identityKey.pub },
    signed_prekey: {
      tag: state.signed_pre_keys[0].tag,
      pub: state.signed_pre_keys[0].pub,
      sign: state.signed_pre_keys[0].sign,
    },
    onetime_prekeys: preKeys.map(pk => ({
      tag: pk.keyId, pub: toB64(pk.keyPair.pubKey),
    })),
  }};
  process.stdout.write('PYA_KEYGEN\x00' + JSON.stringify(body));
})().catch(e => {
  console.error('keygen failed: ' + (e.stack || e.message));
  process.exit(1);
});
'''

# ---------------------------------------------------------------------------
# decrypt
# ---------------------------------------------------------------------------

_DECRYPT = _BOOTSTRAP + _STORE + r'''
function safeTry(fn) {
  return new Promise(resolve => {
    try {
      const r = fn();
      if (r && typeof r.then === 'function') {
        r.then(b => resolve({ ok: true, bytes: Buffer.from(b) }),
               e => resolve({ ok: false, err: String(e.message || e) }));
      } else {
        resolve({ ok: true, bytes: Buffer.from(r) });
      }
    } catch (e) {
      resolve({ ok: false, err: String(e.message || e) });
    }
  });
}

function allOurKeys(encKeys, MY_UID) {
  const out = [];
  for (const k of Object.keys(encKeys)) {
    if (!/^\d+_\d+_\d+$/.test(k)) continue;
    if (k.split('_')[0] === MY_UID) out.push({ label: k, b64: encKeys[k] });
  }
  return out;
}

async function decrypt(storagePath, MY_UID, frame) {
  const { SessionCipher, SignalProtocolAddress } = libsignal;
  const m = (frame.msg && typeof frame.msg === 'object') ? frame.msg : frame;
  let meta = typeof m.meta === 'string' ? JSON.parse(m.meta) : (m.meta || {});

  let encKeys = null;
  let ek = meta.enc_keys;
  if (typeof ek === 'string') { try { ek = JSON.parse(ek); } catch {} }
  if (ek && typeof ek === 'object' &&
      Object.keys(ek).some(k => /^\d+_\d+_\d+$/.test(k))) {
    encKeys = ek;
  }
  if (!encKeys) throw new Error('no enc_keys');

  const ourKeys = allOurKeys(encKeys, MY_UID);
  if (ourKeys.length === 0) throw new Error(
    'no wrapped key for uid ' + MY_UID +
    '; labels=' + Object.keys(encKeys).join(','));

  const sd = encKeys.source_device_id;
  if (sd == null) throw new Error('no source_device_id');
  const ss = String(sd);
  let senderDev;
  if (/^\d+_\d+_\d+$/.test(ss)) senderDev = ss.split('_')[2];
  else if (/^\d+_\d+$/.test(ss)) senderDev = ss.split('_')[1];
  else senderDev = ss;

  const senderUid = String(m.sender);

  let keyBytes = null;
  const errors = [];
  for (const ourKey of ourKeys) {
    const store = new PersistentStore(storagePath);
    const addr = new SignalProtocolAddress(senderUid, Number(senderDev));
    const cipher = new SessionCipher(store, addr);

    const r1 = await safeTry(() => cipher.decryptPreKeyWhisperMessage(ourKey.b64, 'base64'));
    if (r1.ok) { keyBytes = r1.bytes; break; }

    const r2 = await safeTry(() => cipher.decryptWhisperMessage(ourKey.b64, 'base64'));
    if (r2.ok) { keyBytes = r2.bytes; break; }

    errors.push(ourKey.label + ': prekey=' + r1.err + ' | whisper=' + r2.err);
  }
  if (!keyBytes) throw new Error('all our keys failed: ' + errors.join(' || '));

  const parts = String(m.msg || '').split('$');
  if (parts.length !== 2) throw new Error('body not iv$ct');
  const iv = Buffer.from(parts[0], 'base64');
  const ct = Buffer.from(parts[1], 'base64');
  const cw = require('crypto').webcrypto;
  const aesKey = await cw.subtle.importKey('raw', keyBytes,
    { name: 'AES-GCM' }, false, ['decrypt']);
  const pt = await cw.subtle.decrypt(
    { name: 'AES-GCM', iv, tagLength: 128 }, aesKey, ct);
  return Buffer.from(pt).toString('utf8');
}

(async () => {
  const storagePath = process.argv[2];
  const MY_UID = process.env.MY_UID || process.argv[3];
  const frame = JSON.parse(fs.readFileSync(0, 'utf8'));
  try {
    const plain = await decrypt(storagePath, MY_UID, frame);
    process.stdout.write('PYA_DECRYPT\x00' + plain);
  } catch (e) {
    console.error('ERR: ' + (e.message || e));
    process.exit(1);
  }
})();
'''

# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

_SEND = _BOOTSTRAP + _STORE + r'''
function bodyToB64(body) {
  // libsignal-protocol-javascript returns enc.body as a binary string
  // (each char 0-255). We must use latin1, not utf8, to preserve bytes.
  if (typeof body === 'string') {
    return Buffer.from(body, 'binary').toString('base64');
  }
  return Buffer.from(new Uint8Array(body)).toString('base64');
}

async function encrypt(storagePath, MY_DEVICE_ID, text, bundles) {
  const { SessionBuilder, SessionCipher, SignalProtocolAddress } = libsignal;
  const store = new PersistentStore(storagePath);
  const cw = require('crypto').webcrypto;

  // 1. Random AES-256 key + 12-byte IV for the message body
  const aesKeyRaw = cw.getRandomValues(new Uint8Array(32));
  const iv = cw.getRandomValues(new Uint8Array(12));
  const aesKey = await cw.subtle.importKey('raw', aesKeyRaw,
    { name: 'AES-GCM' }, false, ['encrypt']);
  const ct = new Uint8Array(await cw.subtle.encrypt(
    { name: 'AES-GCM', iv, tagLength: 128 },
    aesKey, new TextEncoder().encode(text)));
  const bodyB64 = Buffer.from(iv).toString('base64') + '$'
                + Buffer.from(ct).toString('base64');

  // 2. Wrap the AES key for each recipient device.
  //    Always rebuild sessions from fresh bundles — Signal's session
  //    ratchet can drift if we reuse an old cached one and then the
  //    recipient shows "Waiting for this message".
  const encKeys = {};
  for (const b of bundles) {
    const addr = new SignalProtocolAddress(b.user_id, Number(b.device_id));
    const builder = new SessionBuilder(store, addr);
    await builder.processPreKey({
      registrationId: b.registration_id,
      identityKey: b64buf(b.identity_key.pub),
      signedPreKey: {
        keyId: b.signed_prekey.tag,
        publicKey: b64buf(b.signed_prekey.pub),
        signature: b64buf(b.signed_prekey.sign),
      },
      preKey: {
        keyId: b.onetime_prekey.tag,
        publicKey: b64buf(b.onetime_prekey.pub),
      },
    });
    const cipher = new SessionCipher(store, addr);
    const enc = await cipher.encrypt(aesKeyRaw.buffer);
    const key = `${b.user_id}_${b.registration_id}_${b.device_id}`;
    encKeys[key] = bodyToB64(enc.body);
  }
  encKeys.source_device_id = String(MY_DEVICE_ID);
  return { msg: bodyB64, enc_keys: encKeys };
}

(async () => {
  const storagePath = process.argv[2];
  const MY_DEVICE_ID = process.env.MY_DEVICE_ID;
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  try {
    const result = await encrypt(storagePath, MY_DEVICE_ID,
                                 input.text, input.bundles);
    process.stdout.write('PYA_SEND\x00' + JSON.stringify(result));
  } catch (e) {
    console.error('ERR: ' + (e.stack || e.message));
    process.exit(1);
  }
})();
'''

# ---------------------------------------------------------------------------
# write helpers
# ---------------------------------------------------------------------------

def install_signal_helpers(force: bool = False) -> None:
    """Write all Node helper scripts to ``~/.pyarattai/``."""
    PROJ.mkdir(parents=True, exist_ok=True)
    for path, src in [
        (KEYGEN_JS, _KEYGEN),
        (DECRYPT_JS, _DECRYPT),
        (SEND_JS, _SEND),
    ]:
        if force or not path.exists():
            path.write_text(src)


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------

def ensure_libsignal() -> Path:
    """Return the path to a working libsignal bundle."""
    for c in (LIBSIGNAL_VENDORED, LIBSIGNAL_WEB):
        if c.exists():
            return c
    raise ArattaiError(
        "libsignal bundle not found. Place libsignal-protocol.js at "
        f"{LIBSIGNAL_VENDORED} or libsignal-min.js at {LIBSIGNAL_WEB}."
    )


def ensure_node_deps(silent: bool = False) -> bool:
    """Install bytebuffer/long/protobufjs into ~/.pyarattai/."""
    need = ["bytebuffer@4.1.0", "long@3.2.0", "protobufjs@4.1.3"]
    if all((NODE_MODS / n.split("@")[0]).exists() for n in need):
        return True
    npm = shutil.which("npm")
    if not npm:
        if not silent:
            print("[signal] npm not found — install Node.js")
        return False
    PROJ.mkdir(parents=True, exist_ok=True)
    if not (PROJ / "package.json").exists():
        (PROJ / "package.json").write_text(
            '{"name":"pyarattai","private":true,"version":"0.0.0"}\n')
    if not silent:
        print(f"[signal] npm install in {PROJ}")
    r = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund", "--silent",
         "--save-exact", *need],
        cwd=str(PROJ), capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        if not silent:
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
        [node, "--unhandled-rejections=warn", str(script), *args],
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

        # 2. Encrypt
        r = _run_node(
            SEND_JS, str(self.signal_file),
            stdin={"text": text, "bundles": data},
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
        return client.s.request(
            "POST", "/e2ee/sendofficechatmessage.api", base="chat",
            data=body, headers={"X-SID": sid_val},
        )
