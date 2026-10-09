import test from 'node:test';
import assert from 'node:assert/strict';
import { currentModelEvent, currentRegionBlocked, diagnose } from '../scripts/incident_auto_triage.mjs';

const now = new Date(2026, 9, 9, 3, 15, 0).getTime();
const failure = 'E1009 03:14:00.000 rules.go: FAILED_PRECONDITION (code 400)';
const success = 'I1009 03:14:30.000 streamGenerateContent ResponseID: example';

test('a later model answer resolves the preceding region failure', () => {
  assert.equal(currentModelEvent(`${failure}\n${success}`, now), 'SUCCESS');
  assert.equal(currentRegionBlocked({ lsTail: `${failure}\n${success}` }, now), false);
});
test('a later region failure remains actionable', () => {
  assert.equal(currentRegionBlocked({ lsTail: `${success}\n${failure}` }, now), true);
});
test('an old region failure is historical', () => {
  assert.equal(currentRegionBlocked({ lsTail: 'E1009 01:00:00.000 FAILED_PRECONDITION (code 400)' }, now), false);
});
test('saved reports cannot reactivate a recovered incident', () => {
  assert.equal(currentRegionBlocked({ lsTail: '', unlockerTail: 'region-400', gate: {
    at: now / 1000, last_400: { at: now / 1000 - 60 }, last_ok: { at: now / 1000 - 30 }
  } }, now), false);
});
test('a fresh gate failure works without language server events', () => {
  const gate = { at: now / 1000, last_400: { at: now / 1000 - 30 }, last_ok: { at: now / 1000 - 60 } };
  assert.equal(currentRegionBlocked({ lsTail: '', gate }, now), true);
  assert.equal(currentRegionBlocked({ lsTail: '', gate: { ...gate, at: now / 1000 - 500 } }, now), false);
});
test('historical TLS resets and suspended fallback routes are not a current outage', () => {
  assert.equal(diagnose({ lsTail: '', unlockerTail: 'свой прокси — недоступен; отложен на 2 мин',
    clashTail: '[2020-01-01 00:00:00.000] tls handshake eof', gate: null }).rootCause, 'HEALTHY');
});
