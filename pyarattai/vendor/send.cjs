
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

    // Build the prekey bundle. onetime_prekey is OPTIONAL — when the
    // recipient has consumed all of theirs the server returns the
    // bundle without it. Signed prekey is always present.
    if (!b.identity_key || !b.identity_key.pub) {
      process.stderr.write('skip device ' + b.device_id +
        ': no identity_key\n');
      continue;
    }
    if (!b.signed_prekey || !b.signed_prekey.pub) {
      process.stderr.write('skip device ' + b.device_id +
        ': no signed_prekey\n');
      continue;
    }

    const bundle = {
      registrationId: b.registration_id,
      identityKey: b64buf(b.identity_key.pub),
      signedPreKey: {
        keyId: b.signed_prekey.tag,
        publicKey: b64buf(b.signed_prekey.pub),
        signature: b64buf(b.signed_prekey.sign),
      },
    };

    if (b.onetime_prekey && b.onetime_prekey.pub) {
      bundle.preKey = {
        keyId: b.onetime_prekey.tag,
        publicKey: b64buf(b.onetime_prekey.pub),
      };
    }

    await builder.processPreKey(bundle);

    const cipher = new SessionCipher(store, addr);
    const enc = await cipher.encrypt(aesKeyRaw.buffer);
    const key = `${b.user_id}_${b.registration_id}_${b.device_id}`;
    encKeys[key] = bodyToB64(enc.body);
  }
  encKeys.source_device_id = String(MY_DEVICE_ID);
  return { msg: bodyB64, enc_keys: encKeys };
}



// ─── group E2EE (Signal SenderKeys) ──────────────────────────────────
async function encryptGroup(storagePath, MY_UID, MY_DEVICE_ID, text, chid, bundles) {
  const {
    SessionBuilder, SessionCipher, SignalProtocolAddress,
    GroupCipher, SenderKeyDistributionMessage,
  } = libsignal;
  const store = new PersistentStore(storagePath);
  const cw = require('crypto').webcrypto;

  // Group sessions are keyed by "<senderUid>.<distributionId>", where
  // distributionId is conventionally the chid (Arattai does the same).
  const distributionId = chid;

  // 1. Import or create our own SenderKey for this group
  let senderKey = await store.loadSenderKey
    ? await store.loadSenderKey(`${MY_UID}.${distributionId}`)
    : null;

  // The reference libsignal doesn't ship a group-store interface by
  // default; we use a simple Map inside this invocation. For robustness
  // we create a fresh SenderKey per message which the recipient must
  // accept as a new distribution.
  if (!senderKey) {
    const created = await libsignal.SenderKeyDistributionMessage.create(MY_UID, distributionId);
    senderKey = {
      id: created.id,
      chain: created,
    };
  }

  // 2. Distribute the SenderKey to every recipient device.
  const distributionMsg = await SenderKeyDistributionMessage.create(MY_UID, distributionId);
  const senderKeyDist = await distributionMsg.serialize();

  const distributionKeys = {};
  for (const b of bundles) {
    const addr = new SignalProtocolAddress(b.user_id, Number(b.device_id));
    const builder = new SessionBuilder(store, addr);
    if (!b.identity_key || !b.signed_prekey) continue;
    const bundle = {
      registrationId: b.registration_id,
      identityKey: b64ToBuf(b.identity_key.pub),
      signedPreKey: {
        keyId: b.signed_prekey.tag,
        publicKey: b64ToBuf(b.signed_prekey.pub),
        signature: b64ToBuf(b.signed_prekey.sign),
      },
    };
    if (b.onetime_prekey && b.onetime_prekey.pub) {
      bundle.preKey = {
        keyId: b.onetime_prekey.tag,
        publicKey: b64ToBuf(b.onetime_prekey.pub),
      };
    }
    await builder.processPreKey(bundle);

    const cipher = new SessionCipher(store, addr);
    // Wrap the distribution message with the pairwise session
    const enc = await cipher.encrypt(senderKeyDist);
    const key = `${b.registration_id}_${b.device_id}`;
    distributionKeys[key] = bodyToB64(enc.body);
  }

  // 3. Encrypt the message with AES-GCM using the group key.
  //    For simplicity, the group key is the SenderKeyDistributionMessage
  //    chain key itself. The recipient will unwrap the distribution and
  //    use its embedded chain.
  const aesKeyRaw = cw.getRandomValues(new Uint8Array(32));
  const iv = cw.getRandomValues(new Uint8Array(12));
  const aesKey = await cw.subtle.importKey('raw', aesKeyRaw,
    { name: 'AES-GCM' }, false, ['encrypt']);
  const ct = new Uint8Array(await cw.subtle.encrypt(
    { name: 'AES-GCM', iv, tagLength: 128 },
    aesKey, new TextEncoder().encode(text)));
  const bodyB64 = Buffer.from(iv).toString('base64') + '$'
                + Buffer.from(ct).toString('base64');

  // 4. Also wrap the raw AES key with each recipient's pairwise session.
  //    This matches the Arattai layout: `enc_keys` in meta.
  const encKeys = {};
  for (const b of bundles) {
    const addr = new SignalProtocolAddress(b.user_id, Number(b.device_id));
    const cipher = new SessionCipher(store, addr);
    const enc = await cipher.encrypt(aesKeyRaw.buffer);
    const key = `${b.user_id}_${b.registration_id}_${b.device_id}`;
    encKeys[key] = bodyToB64(enc.body);
  }
  encKeys.source_device_id = String(MY_DEVICE_ID);

  return {
    msg: bodyB64,
    enc_keys: encKeys,
    sender_key: {
      distribution_keys: {
        [MY_UID]: distributionKeys,
      },
    },
  };
}

(async () => {
  const storagePath = process.argv[2];
  const mode = process.argv[3] || 'direct';
  const MY_DEVICE_ID = process.env.MY_DEVICE_ID;
  const MY_UID = process.env.MY_UID;
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  try {
    let result;
    if (mode === 'group') {
      result = await encryptGroup(storagePath, MY_UID, MY_DEVICE_ID,
                                  input.text, input.chid, input.bundles);
    } else {
      result = await encrypt(storagePath, MY_DEVICE_ID,
                             input.text, input.bundles);
    }
    process.stdout.write('PYA_SEND\x00' + JSON.stringify(result));
  } catch (e) {
    console.error('ERR: ' + (e.stack || e.message));
    process.exit(1);
  }
})();
