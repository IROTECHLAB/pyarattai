
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
