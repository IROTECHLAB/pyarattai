
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
