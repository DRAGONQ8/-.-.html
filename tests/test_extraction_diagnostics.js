// Exercise the host's actual ZIP resource-message handler with injected outcomes.
// Run with: node --test tests/test_extraction_diagnostics.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readdirSync(root).find(name => name.endsWith('.html') && name !== 'ZIP_Launcher_V4_Fixed.html');
assert.ok(html, 'launcher source HTML must exist');
const source = fs.readFileSync(path.join(root, html), 'utf8');
const start = source.indexOf("  if (data.type === '__ZIP_REQ_BLOB__'");
const end = source.indexOf("\n  if (data.type === '__ZIP_ASSET_OK__')", start);
assert.ok(start >= 0 && end > start, 'host resource handler must exist');

async function request(resourcePath, extract, stale = false) {
  const sent = [];
  const viewerWindow = {postMessage: message => sent.push(message)};
  const state = {map: new Map([['image.webp', {}]]), activeRunToken: 'run-1', projectId: 'project-1'};
  const context = vm.createContext({
    Blob, state, snap: null, contextToken: 'run-1',
    data: {type: '__ZIP_REQ_BLOB__', reqId: 'request-1', path: resourcePath},
    e: {source: viewerWindow}, isFromViewer: true, isFromChildTab: false, targetProjectId: 'project-1',
    $: () => ({contentWindow: viewerWindow}),
    sanitizeArchivePath: input => ({rejected: input.includes('..'), safe: input}),
    extractBlobShared: async (...args) => { const value = await extract(...args); if (stale) state.activeRunToken = 'run-2'; return value; }
  });
  await vm.runInContext(`(async () => { ${source.slice(start, end)} })()`, context);
  if (stale) { assert.equal(sent.length, 0, 'stale request must not reply to a new runtime'); return; }
  assert.equal(sent.length, 1, 'each accepted request must receive a response');
  assert.equal(sent[0].type, '__ZIP_RES_BLOB__');
  assert.equal(sent[0].reqId, 'request-1');
  return sent[0];
}

function error(code, message) {
  const err = new Error(message);
  err.code = code;
  return err;
}

test('existing resource returns binary data without an error', async () => {
  const blob = new Blob([new Uint8Array([1, 2, 3])], {type:'image/webp'});
  const result = await request('image.webp', async () => blob);
  assert.equal(result.blob, blob);
  assert.ok(!result.error);
});

test('missing entry reports ARCHIVE_ENTRY_MISSING', async () => {
  const result = await request('missing.webp', async () => { throw Error('must not extract missing entry'); });
  assert.equal(result.blob, null);
  assert.equal(result.error?.code, 'ARCHIVE_ENTRY_MISSING');
  assert.ok(result.error?.message);
});

test('invalid path reports INVALID_RESOURCE_PATH', async () => {
  const result = await request('../image.webp', async () => { throw Error('must not extract invalid path'); });
  assert.equal(result.blob, null);
  assert.equal(result.error?.code, 'INVALID_RESOURCE_PATH');
  assert.ok(result.error?.message);
});

test('CRC failure preserves its error code and useful details', async () => {
  const result = await request('image.webp', async () => { throw error('CRC32_MISMATCH', 'CRC32 mismatch for image.webp'); });
  assert.equal(result.blob, null);
  assert.equal(result.error?.code, 'CRC32_MISMATCH');
  assert.match(result.error?.message || '', /CRC32/);
});

test('decompression exceptions do not become silent null results', async () => {
  const result = await request('image.webp', async () => { throw Error('invalid compressed data'); });
  assert.equal(result.blob, null);
  assert.equal(result.error?.code, 'ZIP_DECOMPRESSION_FAILED');
  assert.match(result.error?.message || '', /invalid compressed data/);
});

test('stale extraction results retain a distinct status', async () => {
  const result = await request('image.webp', async () => { throw error('STALE_RUNTIME', 'runtime changed during extraction'); });
  assert.equal(result.blob, null);
  assert.equal(result.error?.code, 'STALE_RUNTIME');
});


test('a replaced runtime never receives an old extraction response', async () => {
  await request('image.webp', async () => new Blob(['ok']), true);
});

test('real extractor identifies a CRC mismatch', async () => {
  const fnStart = source.indexOf('async function extractBlobShared(');
  const fnEnd = source.indexOf('\nasync function getText(', fnStart);
  assert.ok(fnStart >= 0 && fnEnd > fnStart);
  const state = {blobs: new Map(), map: new Map(), operationSeq: 1,
    inFlightExtractions: new Map(), brokenFiles: new Set()};
  state.map.set('image.webp', {size: 1, entry: {
    _data: {crc32: 1}, async: async () => new Uint8Array([42])
  }});
  const context = vm.createContext({Blob, Uint8Array, state,
    MIME_MAP: {webp: 'image/webp'}, ARCHIVE_POLICY: {maxSingleEagerFileBytes: 1024},
    ext: p => p.split('.').pop(), computeCrc32Uint8: () => 2,
    addDiag: () => {}, cacheBlobWithMemoryGuard: () => {}});
  vm.runInContext(source.slice(fnStart, fnEnd), context);
  await assert.rejects(context.extractBlobShared('image.webp'), error => error.code === 'CRC32_MISMATCH');
  assert.equal(state.brokenFiles.has('image.webp'), true);
});
