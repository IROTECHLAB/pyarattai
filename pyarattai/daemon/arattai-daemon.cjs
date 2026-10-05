#!/usr/bin/env node
// Minimal E2EE daemon stub for pyarattai.
// This file is a placeholder — a real implementation must be provided
// by the user (bundled with their e2ee-send.cjs / e2ee-decrypt.cjs).
//
// HTTP contract expected by pyarattai.E2EEBridge:
//   GET  /status
//   GET  /chats
//   GET  /transcript/:chid?limit=50
//   POST /send-e2ee   {chid, recipient, text}
//   GET  /stream      (SSE)
const http = require('http');

const PORT = process.env.PORT || 8766;

const server = http.createServer((req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/status') {
    return res.end(JSON.stringify({ ok: true, stub: true }));
  }
  if (req.url === '/chats') return res.end('[]');
  if (req.url.startsWith('/transcript/')) return res.end('[]');
  res.statusCode = 501;
  res.end(JSON.stringify({ error: 'not implemented in stub' }));
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[pyarattai-daemon stub] listening on 127.0.0.1:${PORT}`);
});
