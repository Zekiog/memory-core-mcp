import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractRecords, extractNewestTs } from './heartbeat.mjs';

test('extractRecords: bare array', () => {
  assert.deepEqual(extractRecords([{ a: 1 }]), [{ a: 1 }]);
});
test('extractRecords: records envelope', () => {
  assert.deepEqual(extractRecords({ records: [{ a: 1 }] }), [{ a: 1 }]);
});
test('extractRecords: data envelope', () => {
  assert.deepEqual(extractRecords({ data: [{ a: 1 }] }), [{ a: 1 }]);
});
test('extractRecords: none / null', () => {
  assert.deepEqual(extractRecords({ foo: 1 }), []);
  assert.deepEqual(extractRecords(null), []);
});
test('extractNewestTs: picks max created_at', () => {
  const body = { records: [
    { created_at: '2026-06-24T10:00:00Z' },
    { created_at: '2026-06-24T11:00:00Z' },
  ] };
  assert.equal(extractNewestTs(body), Date.parse('2026-06-24T11:00:00Z'));
});
test('extractNewestTs: alt field ts', () => {
  assert.equal(extractNewestTs([{ ts: '2026-06-24T09:00:00Z' }]), Date.parse('2026-06-24T09:00:00Z'));
});
test('extractNewestTs: empty -> null', () => {
  assert.equal(extractNewestTs({ records: [] }), null);
});
test('extractNewestTs: unparseable -> null', () => {
  assert.equal(extractNewestTs([{ created_at: 'not-a-date' }]), null);
});

import { classify } from './heartbeat.mjs';

const NOW = Date.parse('2026-06-24T12:00:00Z');

test('classify: fresh record -> ok', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T11:50:00Z' }], NOW, 30), 'ok');
});
test('classify: old record -> stale', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T11:00:00Z' }], NOW, 30), 'stale');
});
test('classify: boundary (exactly threshold) -> ok', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T11:30:00Z' }], NOW, 30), 'ok');
});
test('classify: empty body -> stale', () => {
  assert.equal(classify(false, { records: [] }, NOW, 30), 'stale');
});
test('classify: http failed -> down', () => {
  assert.equal(classify(true, null, NOW, 30), 'down');
});

import { decide } from './heartbeat.mjs';

test('decide: ok->ok silent', () => assert.deepEqual(decide('ok', 'ok'), { alert: false, severity: null }));
test('decide: ok->stale', () => assert.deepEqual(decide('ok', 'stale'), { alert: true, severity: 'stale' }));
test('decide: ok->down', () => assert.deepEqual(decide('ok', 'down'), { alert: true, severity: 'down' }));
test('decide: stale->ok recovered', () => assert.deepEqual(decide('stale', 'ok'), { alert: true, severity: 'recovered' }));
test('decide: down->ok recovered', () => assert.deepEqual(decide('down', 'ok'), { alert: true, severity: 'recovered' }));
test('decide: stale->down escalation', () => assert.deepEqual(decide('stale', 'down'), { alert: true, severity: 'down' }));
test('decide: down->stale partial', () => assert.deepEqual(decide('down', 'stale'), { alert: true, severity: 'stale' }));
test('decide: stale->stale silent', () => assert.deepEqual(decide('stale', 'stale'), { alert: false, severity: null }));
test('decide: first run undefined+ok silent', () => assert.deepEqual(decide(undefined, 'ok'), { alert: false, severity: null }));
test('decide: first run undefined+down alert', () => assert.deepEqual(decide(undefined, 'down'), { alert: true, severity: 'down' }));
