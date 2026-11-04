// Exercise the launcher's real extraction function with controlled decompression timing.
// Run with: node --test tests/test_extraction_race.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const sourceName = fs.readdirSync(root).find(name => name.endsWith('.html') && name !== 'ZIP_Launcher_V4_Fixed.html');
assert.ok(sourceName, 'launcher source HTML must exist');
const source = fs.readFileSync(path.join(root, sourceName), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const start = source.indexOf('async function extractBlobShared(');
const end = source.indexOf('\nasync function getText(', start);
assert.ok(start >= 0 && end > start, 'launcher extraction function must exist');

function setup() {
  const state = { blobs: new Map(), map: new Map(), projectId: 'project', operationSeq: 1,
    inFlightExtractions: new Map(), brokenFiles: new Set() };
  const context = vm.createContext({
    Blob, Uint8Array, state, MIME_MAP: { png: 'image/png' },
    ARCHIVE_POLICY: { maxSingleEagerFileBytes: 1024 },
    ext: p => p.split('.').pop(),
    cacheBlobWithMemoryGuard: (p, b) => state.blobs.set(p, b),
    computeCrc32Uint8: () => 0,
    addDiag: () => {}
  });
  vm.runInContext(source.slice(start, end), context);
  return { state, extract: context.extractBlobShared };
}

function controlledEntry() {
  const pending = [];
  return { pending, entry: { async() { return new Promise(resolve => pending.push(resolve)); } } };
}

async function bytes(blob) {
  assert.ok(blob instanceof Blob, 'current request must receive a Blob');
  return [...new Uint8Array(await blob.arrayBuffer())];
}

test('a new navigation does not inherit a stale extraction result', async () => {
  const {state, extract} = setup();
  const item = controlledEntry();
  state.map.set('a.png', {size: 1, entry: item.entry});
  const old = extract('a.png').catch(() => null);
  state.operationSeq++;
  const current = extract('a.png');
  await tick();
  for (const finish of item.pending) finish(new Uint8Array([42]));
  await old;
  assert.deepEqual(await bytes(await current), [42]);
});

test('completion of an old flight cannot discard a newer in-flight extraction', async () => {
  const {state, extract} = setup();
  const item = controlledEntry();
  state.map.set('a.png', {size: 1, entry: item.entry});
  const old = extract('a.png').catch(() => null);
  await tick();
  state.operationSeq++;
  state.inFlightExtractions.clear(); // project reset while the old decompression continues
  const current = extract('a.png');
  await tick();
  item.pending[0](new Uint8Array([7]));
  await old;
  const duplicate = extract('a.png');
  await tick();
  const count = item.pending.length;
  for (const finish of item.pending.slice(1)) finish(new Uint8Array([9]));
  assert.deepEqual(await bytes(await current), [9]);
  assert.deepEqual(await bytes(await duplicate), [9]);
  assert.equal(count, 2, 'duplicate requests must share the current flight');
});

test('active project and independent snapshot do not share different archive entries', async () => {
  const {state, extract} = setup();
  const active = controlledEntry();
  const saved = controlledEntry();
  state.map.set('a.png', {size: 1, entry: active.entry});
  const snapshot = { projectId: 'project', blobs: new Map(), map: new Map([
    ['a.png', {size: 1, entry: saved.entry}]
  ]) };
  const a = extract('a.png');
  const b = extract('a.png', snapshot);
  await tick();
  for (const finish of active.pending) finish(new Uint8Array([1]));
  for (const finish of saved.pending) finish(new Uint8Array([2]));
  assert.deepEqual(await bytes(await a), [1]);
  assert.deepEqual(await bytes(await b), [2]);
});
