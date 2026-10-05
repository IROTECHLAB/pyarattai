"""Signal / E2EE support for pyarattai.

Shells out to a bundled Node script that uses the pure-JS
``libsignal-protocol`` vendored bundle. The Node script:

  * regenerates Signal keys (identity, signed prekey, 100 one-time prekeys)
  * decrypts incoming ``meta.enc_keys`` wrapped messages
  * encrypts outgoing messages for a recipient's device bundles
  * persists session state back to disk after every operation, so
    Signal's double-ratchet advances correctly across process runs

The library never implements Signal crypto in Python.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ArattaiError

__all__ = [
    "SignalBridge",
    "install_signal_helpers",
    "ensure_libsignal",
    "ensure_node_deps",
    "download_libsignal",
    "run_keygen",
    "register_signal_keys",
]

log = logging.getLogger("pyarattai.signal")

# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

HOME = Path(os.path.expanduser("~"))
PROJ = HOME / ".pyarattai"
NODE_MODS = PROJ / "node_modules"
SIGNAL_FILE = PROJ / "signal-storage.json"
LIBSIGNAL_DIR = PROJ / "arattai-app" / "js"
LIBSIGNAL_FILE = LIBSIGNAL_DIR / "libsignal-min.js"
LIBSIGNAL_VENDORED = (HOME / "arattai-js" / "vendor-libsignal"
                      / "dist" / "libsignal-protocol.js")

DECRYPT_JS = PROJ / ".decrypt.cjs"
SEND_JS = PROJ / ".send.cjs"
KEYGEN_JS = PROJ / ".keygen.cjs"

# ---------------------------------------------------------------------------
# node bootstrap shared by every helper script
# ---------------------------------------------------------------------------

BOOTSTRAP_JS = r'''
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');

(function bootstrap() {
  if (globalThis.__bootstrapped) return;
  globalThis.__bootstrapped = true;
  const setG = (n, v) => { try {
    Object.defineProperty(globalThis, n,
      { value: v, writable: true, configurable: true, enumerable: false });
  } catch { try { globalThis[n] = v; } catch {} } };
  setG('window', globalThis);
  setG('self', globalThis);
  if (!globalThis.navigator) setG('navigator', { userAgent: 'node' });

  const el = () => ({
    tagName:'x', nodeType:1, style:{}, attributes:{}, children:[], childNodes:[],
    body:{appendChild(){}}, head:{appendChild(){}},
    setAttribute(){}, getAttribute(){return null;}, hasAttribute(){return false;},
    appendChild(c){return c;}, removeChild(c){return c;}, cloneNode(){return el();},
    addEventListener(){}, removeEventListener(){},
    querySelector(){return null;}, querySelectorAll(){return [];},
    getElementsByTagName(){return [];},
    toDataURL(){return 'data:,';},
    getContext(){return new Proxy({},{get:()=>()=>{},set:()=>true});},
    createTextNode(t){return {nodeType:3,textContent:t};}
  });
  const doc = {
    nodeType:9, createElement:el, createTextNode:el, createDocumentFragment:el,
    createNodeIterator:()=>({nextNode:()=>null}),
    getElementsByTagName:()=>[], querySelector:()=>null, querySelectorAll:()=>[],
    addEventListener(){}, removeEventListener(){}, currentScript:null,
    baseURI:'https://web.arattai.in/', cookie:'',
    body:el(), head:el(), documentElement:el(),
    implementation:{createHTMLDocument:()=>doc}
  };
  doc.ownerDocument = doc;
  setG('document', doc);
  setG('location', { href:'https://web.arattai.in/', origin:'https://web.arattai.in',
    pathname:'/', protocol:'https:', host:'web.arattai.in' });
  if (!globalThis.screen) setG('screen', { width:1080, height:1920 });
  if (!globalThis.performance) setG('performance', { now:()=>Date.now() });
  if (!globalThis.addEventListener) setG('addEventListener', () => {});
  if (!globalThis.removeEventListener) setG('removeEventListener', () => {});
  if (!globalThis.MutationObserver) setG('MutationObserver',
    class { observe(){} disconnect(){} takeRecords(){return [];} });

  const PROJ = path.join(os.homedir(), '.pyarattai');
  const Module = require('module');
  const orig = Module._resolveFilename;
  Module._resolveFilename = function (req, ...a) {
    if (['bytebuffer','long','protobufjs'].includes(req))
      return orig.call(this, path.join(PROJ, 'node_modules', req), ...a);
    return orig.call(this, req, ...a);
  };

  const BB = require('bytebuffer');
  const Long = require('long');
  const protobuf = require('protobufjs');
  const BBClass = BB.ByteBuffer || BB;
  BBClass.Long = Long;

  const ProtoBufShim = protobuf.ProtoBuf || protobuf;
  if (!ProtoBufShim.loadProto && protobuf.loadProto)
    ProtoBufShim.loadProto = protobuf.loadProto;
  if (!protobuf.ProtoBuf) protobuf.ProtoBuf = ProtoBufShim;

  setG('ByteBuffer', BBClass);
  setG('Long', Long);
  setG('protobuf', protobuf);
  setG('dcodeIO', {
    ByteBuffer: BBClass, Long: Long,
    protobuf: protobuf, ProtoBuf: ProtoBufShim,
  });
})();

const LIB = process.env.LIBSIGNAL_PATH;
if (!LIB || !fs.existsSync(LIB)) {
  console.error('ERR: LIBSIGNAL_PATH not set or missing: ' + LIB);
  process.exit(2);
}
let libsignal;
try {
  const required = require(LIB);
  libsignal = globalThis.libsignal || globalThis.Signal ||
             (required && required.default) || required;
} catch (e) {
  console.error('ERR loading ' + LIB + ': ' + e.message);
  process.exit(2);
}
if (!libsignal || !libsignal.Curve) {
  console.error('ERR: libsignal has no Curve. keys=' +
    Object.keys(libsignal || {}).join(','));
  process.exit(3);
}
'''

# ---------------------------------------------------------------------------
# persistent store used by both decrypt and send
# ---------------------------------------------------------------------------

STORE_JS = r'''
function b64ToBuf(b64) {
  const b = Buffer.from(b64, 'base64');
  const u = new Uint8Array(b.length);
  u.set(b);
  return u.buffer;
}
function bufToB64(v) {
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
      sender_keys: [],
      aes_keys: [],
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
    return { pubKey: b64ToBuf(k.pub), privKey: b64ToBuf(k.priv) };
  }
  async storePreKey(id, r){
    this._preKeys.set(Number(id), {
      pub: bufToB64(r.pubKey), priv: bufToB64(r.privKey),
    });
    this.persist();
  }
  async removePreKey(id){ this._preKeys.delete(Number(id)); this.persist(); }

  async loadSignedPreKey(id){
    const k = this._signedPreKeys.get(Number(id));
    if (!k) return undefined;
    return {
      pubKey: b64ToBuf(k.pub),
      privKey: b64ToBuf(k.priv),
      signature: b64ToBuf(k.sign),
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
      pub: bufToB64(r.pubKey), priv: bufToB64(r.privKey),
      sign: bufToB64(r.signature), time: Date.now(),
    });
    this.persist();
  }
  async removeSignedPreKey(id){
    this._signedPreKeys.delete(Number(id)); this.persist();
  }
  async getIdentityKeyPair(){
    if (!this._identityKey) throw new Error('no identity');
    return {
      pubKey: b64ToBuf(this._identityKey.pub),
      privKey: b64ToBuf(this._identityKey.priv),
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

KEYGEN_JS_SRC = BOOTSTRAP_JS + r'''
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
    console.error('ERR: libsignal missing Curve/KeyHelper');
    process.exit(3);
  }

  const idKp = Curve.generateKeyPair();
  const regId = Math.floor(Math.random() * 16380) + 1;

  const maybe = v => (v && typeof v.then === 'function') ? v : Promise.resolve(v);
  const preKeys = [];
  for (let tag = 1; tag <= 100; tag++) {
    preKeys.push(await maybe(KeyHelper.generatePreKey(tag)));
  }
  const spk = await maybe(KeyHelper.generateSignedPreKey(idKp, 1));

  const state = {
    identityKey: {
      priv: toB64(idKp.privKey),
      pub:  toB64(idKp.pubKey),
    },
    registrationId: regId,
    signed_pre_keys: [{
      tag: spk.keyId || 1, is_default: true,
      pub:  toB64(spk.keyPair.pubKey),
      priv: toB64(spk.keyPair.privKey),
      sign: Buffer.from(spk.signature).toString('base64'),
      time: Date.now(),
    }],
    pre_keys: preKeys.map(pk => ({
      tag: pk.keyId,
      pub:  toB64(pk.keyPair.pubKey),
      priv: toB64(pk.keyPair.privKey),
    })),
    sessions: [], sender_keys: [], aes_keys: [],
  };
  fs.writeFileSync(outPath, JSON.stringify(state, null, 2));

  const body = { data: {
    registration_id: regId,
    device_id: parseInt(deviceId, 10),
    identity_key:  { tag: 0, pub: state.identityKey.pub },
    signed_prekey: {
      tag:  state.signed_pre_keys[0].tag,
      pub:  state.signed_pre_keys[0].pub,
      sign: state.signed_pre_keys[0].sign,
    },
    onetime_prekeys: preKeys.map(pk => ({
      tag: pk.keyId,
      pub: toB64(pk.keyPair.pubKey),
    })),
  }};
  process.stdout.write('SERVER_BODY ' + JSON.stringify(body) + '\n');
})().catch(e => {
  console.error('KEYGEN_FAILED: ' + e.message);
  process.exit(2);
});
'''

# ---------------------------------------------------------------------------
# decrypt
# ---------------------------------------------------------------------------

DECRYPT_JS_SRC = BOOTSTRAP_JS + STORE_JS + r'''
async function decrypt(storagePath, MY_UID, frame) {
  const { SessionCipher, SignalProtocolAddress } = libsignal;

  const m = (frame.msg && typeof frame.msg === 'object') ? frame.msg : frame;
  const store = new PersistentStore(storagePath);

  let meta = typeof m.meta === 'string' ? JSON.parse(m.meta) : (m.meta || {});

  let encKeys = null;
  if (meta && typeof meta === 'object') {
    let ek = meta.enc_keys;
    if (typeof ek === 'string') { try { ek = JSON.parse(ek); } catch {} }
    if (ek && typeof ek === 'object' &&
        Object.keys(ek).some(k => /^\d+_\d+_\d+$/.test(k))) {
      encKeys = ek;
    }
  }
  if (!encKeys) {
    // recursive fallback
    (function walk(node) {
      if (!node || typeof node !== 'object' || encKeys) return;
      for (const k of Object.keys(node)) {
        const v = node[k];
        if (k === 'enc_keys') {
          let parsed = v;
          if (typeof v === 'string' && v.trim().startsWith('{')) {
            try { parsed = JSON.parse(v); } catch { continue; }
          }
          if (parsed && typeof parsed === 'object' &&
              Object.keys(parsed).some(x => /^\d+_\d+_\d+$/.test(x))) {
            encKeys = parsed;
            return;
          }
        }
        walk(v);
      }
    })(m);
  }
  if (!encKeys) throw new Error('no enc_keys with device labels');

  // Wrapped key for our device
  let ourKey = null;
  for (const k of Object.keys(encKeys)) {
    if (!/^\d+_\d+_\d+$/.test(k)) continue;
    if (k.split('_')[0] === MY_UID) { ourKey = encKeys[k]; break; }
  }
  if (!ourKey) throw new Error('no wrapped key for uid ' + MY_UID);

  // Sender device id
  const sd = encKeys.source_device_id;
  if (sd == null) throw new Error('no source_device_id');
  const ss = String(sd);
  let senderDev;
  if (/^\d+_\d+_\d+$/.test(ss)) senderDev = ss.split('_')[2];
  else if (/^\d+_\d+$/.test(ss)) senderDev = ss.split('_')[1];
  else senderDev = ss;

  const senderUid = String(m.sender);
  const addr = new SignalProtocolAddress(senderUid, Number(senderDev));
  const cipher = new SessionCipher(store, addr);

  // try prekey then whisper — both catch sync + async errors
  const tryCall = (fn) => new Promise(resolve => {
    try {
      const r = fn();
      if (r && typeof r.then === 'function') {
        r.then(b => resolve({ ok: true, bytes: Buffer.from(b) }),
               e => resolve({ ok: false, err: String(e.message || e) }));
      } else {
        resolve({ ok: true, bytes: Buffer.from(r) });
      }
    } catch (e) { resolve({ ok: false, err: String(e.message || e) }); }
  });

  const r1 = await tryCall(() => cipher.decryptPreKeyWhisperMessage(ourKey, 'base64'));
  let keyBytes;
  if (r1.ok) keyBytes = r1.bytes;
  else {
    const r2 = await tryCall(() => cipher.decryptWhisperMessage(ourKey, 'base64'));
    if (r2.ok) keyBytes = r2.bytes;
    else throw new Error('prekey=' + r1.err + ' | whisper=' + r2.err);
  }

  // AES-GCM body
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
  const raw = fs.readFileSync(0, 'utf8');
  const frame = JSON.parse(raw);
  try {
    const plain = await decrypt(storagePath, MY_UID, frame);
    process.stdout.write('PYARATTAI_PLAIN\x00' + plain);
  } catch (e) {
    process.stderr.write('ERR: ' + e.message + '\n');
    process.exit(1);
  }
})();
'''

# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

SEND_JS_SRC = BOOTSTRAP_JS + STORE_JS + r'''
async function encrypt(storagePath, MY_UID, MY_DEVICE_ID, text, bundles) {
  const { SessionBuilder, SessionCipher, SignalProtocolAddress } = libsignal;
  const store = new PersistentStore(storagePath);
  const crypto = require('crypto').webcrypto;

  // generate AES key for the message body
  const aesKeyRaw = crypto.getRandomValues(new Uint8Array(32));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const aesKey = await crypto.subtle.importKey('raw', aesKeyRaw,
    { name: 'AES-GCM' }, false, ['encrypt']);
  const body = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv, tagLength: 128 },
    aesKey, new TextEncoder().encode(text)));
  const bodyB64 = Buffer.from(iv).toString('base64') + '$'
                + Buffer.from(body).toString('base64');

  const encKeys = {};
  for (const b of bundles) {
    const addr = new SignalProtocolAddress(b.user_id, Number(b.device_id));

    // Only build a fresh session if we don't have a working one. If
    // we do, keep it — that preserves the ratchet state with this peer
    // so both sides stay in sync.
    let needNewSession = true;
    try {
      const existing = await store.loadSession(`${b.user_id}.${b.device_id}`);
      if (existing && existing.sessionState) needNewSession = false;
    } catch (e) {}

    if (needNewSession) {
      const builder = new SessionBuilder(store, addr);
      await builder.processPreKey({
        registrationId: b.registration_id,
        identityKey: b64ToBuf(b.identity_key.pub),
        signedPreKey: {
          keyId: b.signed_prekey.tag,
          publicKey: b64ToBuf(b.signed_prekey.pub),
          signature: b64ToBuf(b.signed_prekey.sign),
        },
        preKey: {
          keyId: b.onetime_prekey.tag,
          publicKey: b64ToBuf(b.onetime_prekey.pub),
        },
      });
    }

    const cipher = new SessionCipher(store, addr);
    const enc = await cipher.encrypt(aesKeyRaw.buffer);
    const key = `${b.user_id}_${b.registration_id}_${b.device_id}`;
    // The vanilla libsignal returns enc.body as a BINARY STRING (one char
    // per byte, 0-255). Buffer.from(str) defaults to UTF-8 and mangles
    // bytes >= 0x80. Force 'binary' (latin1) so byte values are preserved.
    let bodyB64;
    if (typeof enc.body === 'string') {
      bodyB64 = Buffer.from(enc.body, 'binary').toString('base64');
    } else {
      bodyB64 = Buffer.from(new Uint8Array(enc.body)).toString('base64');
    }
    encKeys[key] = bodyB64;
    console.log('[send]', key, 'type:', enc.type,
                'body len:', (enc.body.length || enc.body.byteLength),
                '-> b64 len:', bodyB64.length);
  }
  encKeys.source_device_id = String(MY_DEVICE_ID);
  return { msg: bodyB64, enc_keys: encKeys };
}

(async () => {
  const storagePath = process.argv[2];
  const MY_UID = process.env.MY_UID;
  const MY_DEVICE_ID = process.env.MY_DEVICE_ID;
  const raw = fs.readFileSync(0, 'utf8');
  const input = JSON.parse(raw);  // {text, bundles}
  try {
    const result = await encrypt(storagePath, MY_UID, MY_DEVICE_ID,
                                 input.text, input.bundles);
    process.stdout.write('PYARATTAI_SEND\x00' + JSON.stringify(result));
  } catch (e) {
    process.stderr.write('ERR: ' + e.message + '\n');
    process.exit(1);
  }
})();
'''

# ---------------------------------------------------------------------------
# installation
# ---------------------------------------------------------------------------

def install_signal_helpers(force: bool = False) -> None:
    """Write all three helper scripts to ``~/.pyarattai/``."""
    PROJ.mkdir(parents=True, exist_ok=True)
    for path, src in [
        (DECRYPT_JS, DECRYPT_JS_SRC),
        (SEND_JS, SEND_JS_SRC),
        (KEYGEN_JS, KEYGEN_JS_SRC),
    ]:
        if force or not path.exists():
            path.write_text(src)


# ---------------------------------------------------------------------------
# libsignal + node deps
# ---------------------------------------------------------------------------

def _libsignal_candidates() -> List[Path]:
    return [
        LIBSIGNAL_VENDORED,
        LIBSIGNAL_FILE,
        LIBSIGNAL_DIR / "libsignal-min.9637104ddb88d7a7d218c6d90a10f250.js",
        HOME / "arattai-js" / "arattai-app" / "js" /
            "libsignal-min.9637104ddb88d7a7d218c6d90a10f250.js",
    ]


def ensure_libsignal(force: bool = False) -> Path:
    """Return path to a working libsignal JS bundle."""
    for c in _libsignal_candidates():
        if c.exists():
            return c
    raise ArattaiError(
        "libsignal bundle not found. Expected one of: " +
        ", ".join(str(p) for p in _libsignal_candidates())
    )


def ensure_node_deps(silent: bool = False) -> bool:
    """Install bytebuffer/long/protobufjs into ``~/.pyarattai/``."""
    need = ["bytebuffer@4.1.0", "long@3.2.0", "protobufjs@4.1.3"]
    if all((NODE_MODS / n.split("@")[0]).exists() for n in need):
        return True
    npm = shutil.which("npm")
    if not npm:
        if not silent:
            print("[signal] npm not found; install Node.js (pkg install nodejs)")
        return False
    PROJ.mkdir(parents=True, exist_ok=True)
    pkg = PROJ / "package.json"
    if not pkg.exists():
        pkg.write_text('{"name":"pyarattai","private":true,"version":"0.0.0"}\n')
    if not silent:
        print(f"[signal] npm install in {PROJ} ...")
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
# run helpers
# ---------------------------------------------------------------------------

def _run_node(script: Path, *args: str, stdin: Optional[Any] = None,
              my_uid: Optional[str] = None, my_device_id: Optional[str] = None,
              timeout: float = 60.0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LIBSIGNAL_PATH"] = str(ensure_libsignal())
    env["NODE_PATH"] = str(NODE_MODS)
    if my_uid:
        env["MY_UID"] = str(my_uid)
    if my_device_id:
        env["MY_DEVICE_ID"] = str(my_device_id)
    node = shutil.which("node") or "node"
    payload = json.dumps(stdin) if stdin is not None else None
    return subprocess.run(
        [node, str(script), *args],
        input=payload,
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def run_keygen(device_id: int) -> Dict[str, Any]:
    """Generate Signal keys; returns the SERVER_BODY dict for /v2/keys/register."""
    install_signal_helpers()
    r = _run_node(KEYGEN_JS, str(SIGNAL_FILE), str(device_id), timeout=180)
    if r.returncode != 0:
        raise ArattaiError(f"keygen failed: {r.stderr.strip()[:400]}")
    for line in r.stdout.splitlines():
        if line.startswith("SERVER_BODY "):
            payload = line[len("SERVER_BODY "):]
            end = payload.rfind("}")
            if end >= 0:
                payload = payload[:end + 1]
            return json.loads(payload)
    raise ArattaiError("keygen produced no SERVER_BODY")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

def register_signal_keys(client, device_id: int) -> Dict[str, Any]:
    """Publish public keys; re-publish existing store if present."""
    ensure_node_deps(silent=False)
    ensure_libsignal()
    install_signal_helpers()

    if SIGNAL_FILE.exists():
        blob = json.loads(SIGNAL_FILE.read_text())
        spk = (blob.get("signed_pre_keys") or [{}])[0]
        if not spk.get("pub"):
            raise ArattaiError("signal file exists but has no signed prekey")
        onetime = [
            {"tag": k["tag"], "pub": k["pub"]}
            for k in blob.get("pre_keys", []) if k.get("pub")
        ]
        server_body = {"data": {
            "registration_id": blob["registrationId"],
            "device_id": int(device_id),
            "identity_key": {"tag": 0, "pub": blob["identityKey"]["pub"]},
            "signed_prekey": {
                "tag": spk["tag"], "pub": spk["pub"], "sign": spk["sign"],
            },
            "onetime_prekeys": onetime,
        }}
    else:
        server_body = run_keygen(int(device_id))

    # Mint x-tkp-token first, if helper available
    try:
        from .auth import Auth as _Auth
        a = _Auth(client.s)
        a.uid = client.uid
        a.mint_x_tkp_token()
    except Exception as e:
        log.debug("mint before register failed: %s", e)

    try:
        r = client.s.request(
            "POST", "/v2/keys/register", base="chat",
            json=server_body, raw=True,
        )
    except Exception as e:
        payload = getattr(e, "payload", None)
        print(f"[signal.register] {e}")
        if payload is not None:
            print(f"[signal.register] body: {str(payload)[:600]}")
        raise

    # Install Set-Cookie from 204 into our session
    for ck in r.cookies:
        client.s.http.cookies.set_cookie(ck)
    reg = r.cookies.get("e2ee_registration_id")
    dev = r.cookies.get("e2ee_device_id")
    if reg:
        client.registration_id = str(reg)
    if dev:
        client.device_id = str(dev)

    try:
        return r.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# SignalBridge
# ---------------------------------------------------------------------------

class SignalBridge:
    """Shells out to Node for Signal E2EE operations."""

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
                "Run `pyarattai signal register`."
            )
        wrapped = {"msg": frame}
        r = _run_node(
            DECRYPT_JS, str(self.signal_file), self.uid,
            stdin=wrapped, my_uid=self.uid,
        )
        if r.returncode != 0:
            raise ArattaiError(f"decrypt failed: {r.stderr.strip()[:400]}")
        out = r.stdout
        marker = "PYARATTAI_PLAIN\x00"
        idx = out.find(marker)
        if idx >= 0:
            return out[idx + len(marker):]
        # fallback: take the last line
        return out.split("\n")[-1] if "\n" in out else out

    # ------------------------------------------------------------------ send

    def send(
        self,
        client,
        chat_id: str,
        recipient_uid: str,
        text: str,
        sid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Encrypt + POST an E2EE message.

        Args:
            client: ArattaiClient (with valid session).
            chat_id: chat to send to.
            recipient_uid: recipient user id.
            text: plaintext.
            sid: X-SID header. If None, uses client.session_id.

        Returns:
            Server response (parsed JSON or None).
        """
        # 1. Fetch bundles
        # Exclude our own current device — the reference does this
        # and it prevents our own bundle from being wrapped and stored.
        our_dev_label = (
            f"{client.uid}_{client.registration_id}_{client.device_id}"
        )
        bundles_resp = client.s.request(
            "POST", "/v2/keys/requestbundle", base="chat",
            json={"recipients": [recipient_uid, client.uid],
                  "exclude_devices": [our_dev_label]},
        )
        data = ((bundles_resp or {}).get("message", {}).get("data")
                or (bundles_resp or {}).get("data") or [])

        # 2. Encrypt
        r = _run_node(
            SEND_JS, str(self.signal_file),
            stdin={"text": text, "bundles": data},
            my_uid=self.uid, my_device_id=str(client.device_id),
        )
        if r.returncode != 0:
            raise ArattaiError(f"encrypt failed: {r.stderr.strip()[:400]}")
        # The node script may emit debug lines before the JSON payload.
        # Look for the PYARATTAI_SEND marker and parse only what follows.
        out = r.stdout
        marker = "PYARATTAI_SEND\x00"
        idx = out.find(marker)
        if idx >= 0:
            out = out[idx + len(marker):]
        else:
            # Fallback: try to find the first '{' and parse from there
            brace = out.find("{")
            if brace >= 0:
                out = out[brace:]
        encrypted = json.loads(out)

        # 3. POST
        sid_val = sid or client.session_id or ""
        # Sanity: the SID is a URL-encoded base64 value like "NENQ1Q6...";
        # an x-tkp-token starts with "<uid>-<uuid>-". If we see the latter,
        # the caller hasn't captured the real X-SID yet.
        if sid_val and ("-" in sid_val[:30] and sid_val.split("-")[0].isdigit()
                        and "%" not in sid_val):
            raise ArattaiError(
                "sid value looks like x-tkp-token, not the WS X-SID. "
                "Open a WS connection first (bot.run() does this) "
                "so the mtype:0 frame delivers the real SID."
            )
        body = {
            "chid": chat_id,
            "msg": encrypted["msg"],
            "msgid": str(int(time.time() * 1000)),
            "sid": sid_val,
            "dname": client.mobile or "pyarattai",
            "unfurl": "false",
            "notification_text": encrypted["msg"],
            "enc_keys": json.dumps(encrypted["enc_keys"]),
        }
        headers = {}
        if sid_val:
            headers["X-SID"] = sid_val
        return client.s.request(
            "POST", "/e2ee/sendofficechatmessage.api", base="chat",
            data=body, headers=headers,
        )
