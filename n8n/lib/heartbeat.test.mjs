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
test('extractRecords: results envelope', () => {
  assert.deepEqual(extractRecords({ results: [{ a: 1 }] }), [{ a: 1 }]);
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
test('classify: future timestamp -> stale', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T13:00:00Z' }], NOW, 30), 'stale');
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

import { buildHeartbeatItem } from './heartbeat.mjs';

const T = Date.parse('2026-06-24T12:00:00Z');

test('build: ok->ok no alert, keeps since', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: '2026-06-24T11:55:00Z' }], nowMs: T, prevState: 'ok', prevSince: '2026-06-24T09:00:00Z' });
  assert.equal(out.alert, false);
  assert.equal(out.current, 'ok');
  assert.equal(out.text, null);
  assert.equal(out.nextState, 'ok');
  assert.equal(out.nextSince, '2026-06-24T09:00:00Z');
});
test('build: ok->down alerts + new since', () => {
  const out = buildHeartbeatItem({ httpFailed: true, body: null, nowMs: T, prevState: 'ok', prevSince: '2026-06-24T09:00:00Z' });
  assert.equal(out.alert, true);
  assert.equal(out.severity, 'down');
  assert.match(out.text, /DOWN/);
  assert.equal(out.nextState, 'down');
  assert.equal(out.nextSince, '2026-06-24T12:00:00.000Z');
});
test('build: down->ok recovered with downtime', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: '2026-06-24T11:59:00Z' }], nowMs: T, prevState: 'down', prevSince: '2026-06-24T11:00:00Z' });
  assert.equal(out.severity, 'recovered');
  assert.match(out.text, /RECOVERED/);
  assert.match(out.text, /Downtime ≈ 60m/);
});
test('build: stale alert reports age', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: '2026-06-24T11:00:00Z' }], nowMs: T, prevState: 'ok', prevSince: null });
  assert.equal(out.severity, 'stale');
  assert.match(out.text, /60m old/);
});
test('build: stale with unparseable timestamp -> ?m old, no null', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: 'not-a-date' }], nowMs: T, prevState: 'ok', prevSince: null });
  assert.equal(out.severity, 'stale');
  assert.match(out.text, /\?m old/);
  assert.ok(!out.text.includes('null'), `text should not contain "null": ${out.text}`);
});
