// The face's backup alarm logic, without a browser (src/face/backup.ts).
import { test, expect } from '@playwright/test';
import { LOCAL_AFTER_MS, LOCAL_MAX_MS, owedRing, type Heard } from '../src/face/backup';

const T = Date.parse('2026-10-07T06:30:00+11:00');
const next = { id: 1, label: 'Work', at: '2026-10-07T06:30:00+11:00' };

test('an alarm passing while core is down is owed 45 s later, for 30 minutes', () => {
  const heard: Heard = { next, ring: null, at: T - 3_600_000 };
  expect(owedRing(heard, T + LOCAL_AFTER_MS - 1, [])).toBeNull(); // core may still be coming back
  expect(owedRing(heard, T + LOCAL_AFTER_MS, [])?.key).toBe(`alarm:1:${next.at}`);
  expect(owedRing(heard, T + LOCAL_AFTER_MS + LOCAL_MAX_MS + 1, [])).toBeNull(); // too long ago
  expect(owedRing(heard, T + LOCAL_AFTER_MS, [`alarm:1:${next.at}`])).toBeNull(); // stopped on the face already
  expect(owedRing(null, T, [])).toBeNull();
});

test('a ring core was running when it went away is owed again 45 s later', () => {
  const heard: Heard = { next: null, ring: { label: 'Work', started: next.at, snoozedUntil: null }, at: T + 60_000 };
  expect(owedRing(heard, T + 60_000 + LOCAL_AFTER_MS - 1, [])).toBeNull();
  expect(owedRing(heard, T + 60_000 + LOCAL_AFTER_MS, [])?.label).toBe('Work');
});

test('a ring the face was already beeping along with carries on at once', () => {
  const heard: Heard = { next: null, ring: { label: 'Work', started: next.at, snoozedUntil: null, beep: true }, at: T + 60_000 };
  expect(owedRing(heard, T + 60_000, [])?.label).toBe('Work'); // no silent gap while core is down
});

test('a snoozed ring is owed 45 s after its snooze would have ended', () => {
  const until = '2026-10-07T06:39:00+11:00';
  const heard: Heard = { next: null, ring: { label: 'Work', started: next.at, snoozedUntil: until }, at: T + 60_000 };
  expect(owedRing(heard, Date.parse(until) + LOCAL_AFTER_MS - 1, [])).toBeNull();
  expect(owedRing(heard, Date.parse(until) + LOCAL_AFTER_MS, [])).not.toBeNull();
});
