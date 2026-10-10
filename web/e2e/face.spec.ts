import { test, expect } from '@playwright/test';
import { BASE, HUB, SLEEP_DEFAULTS, config, hub, post, reset, waitFor } from './helpers';

test.beforeEach(async () => { await reset(); });

test('standby shows the time, date and status dots', async ({ page }) => {
  await page.goto('/face');
  await expect(page.locator('.face-time')).toBeVisible();
  await expect(page.locator('.face-time')).toHaveText(/^\d{1,2}:\d{2}/);
  await expect(page.locator('.face-dots')).toContainText('GPS');
  await expect(page.locator('.face-dots')).toContainText('DAB');
});

test('standby scene draws the forecast hour coming up, and its clouds keep drifting', async ({ page }) => {
  const w = await (await fetch(`${BASE}/api/weather`)).json();
  expect(w.hours.length).toBeGreaterThan(1);
  await page.goto('/face');
  const scene = page.locator('.f-scene');
  // the hour nearest half an hour from now
  const key = (d: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Australia/Sydney', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23' })
    .format(d).replace(', ', 'T') + ':00';
  await expect(scene).toHaveAttribute('data-hour', key(new Date(Date.now() + 30 * 60_000)));
  const drift = () => page.evaluate(() => { const d = document.querySelector('.sc-drift'); return d ? getComputedStyle(d).transform : null; });
  const first = await drift();
  if (first) await expect.poll(drift).not.toBe(first);
});

test('tapping the face opens the menu sheet and the nap picker starts a countdown', async ({ page }) => {
  await page.goto('/face');
  await page.locator('.face-root').tap();
  await expect(page.locator('.face-menu')).toBeVisible();
  // the sheet leaves the clock visible above it and carries the volume slider and the power tile
  await expect(page.locator('.f-ambient .f-ambient-clock')).toBeVisible();
  await expect(page.locator('#face-volume')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Radio on' })).toBeVisible();
  await page.locator('.face-menu .tile', { hasText: 'Nap' }).first().tap();
  await page.locator('.face-menu .tile', { hasText: 'min' }).first().tap();
  await waitFor((s) => s.timers.nap !== null);
  await expect(page.locator('.face-screen')).toContainText(/\d+:\d{2}/);
  await expect(page.locator('.face-chip', { hasText: 'Nap' })).toBeVisible();
});

test('ringing: a tap snoozes, the button stops', async ({ page }) => {
  await page.goto('/face');
  await post('/api/alarms/test', { source: 'chime:gentle_bell', label: 'E2E alarm', volume: 40 });
  await waitFor((s) => s.face.mode === 'ringing');
  await expect(page.locator('.face-screen')).toContainText('E2E alarm');
  await expect(page.locator('.face-screen')).toContainText('Tap anywhere to snooze');
  await page.locator('.face-root').tap();
  const s = await waitFor((x) => x.alarms.ringing && x.alarms.ringing.snoozed_until);
  expect(s.alarms.ringing.snooze_count).toBe(1);
  await expect(page.locator('.face-screen')).toContainText('Snoozed');
  await post('/api/input', { event: 'button_short' });
  await waitFor((x) => x.alarms.ringing === null && x.face.mode === 'standby');
});

test('ringing: holding the screen for 2 s stops without snoozing', async ({ page }) => {
  await page.goto('/face');
  await post('/api/alarms/test', { source: 'chime:gentle_bell', label: 'E2E hold', volume: 40 });
  await waitFor((s) => s.face.mode === 'ringing');
  const box = (await page.locator('.f-ring').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await expect(page.locator('.face-screen')).toContainText('Keep holding to stop');
  await page.waitForTimeout(2300);
  await page.mouse.up();
  const s = await waitFor((x) => x.alarms.ringing === null && x.face.mode === 'standby');
  expect(s.alarms.ringing).toBeNull();
});

test('ringing: when nothing can be heard it climbs to the backup tone and the face beeps along', async ({ page }) => {
  test.setTimeout(45_000);
  await page.goto('/face');
  await hub({ audio_flowing: false }); // nothing reaches the speaker: not the source, not the chime
  await post('/api/alarms/test', { source: 'chime:gentle_bell', label: 'E2E silent', volume: 40 });
  const s = await waitFor((x) => x.alarms.ringing?.tier === 'buzzer', 20_000);
  expect(s.alarms.ringing.face_beep).toBe(true);
  await expect(page.locator('.face-screen')).toContainText('backup tone');
  await expect(page.locator('.face-root[data-beep="1"]')).toHaveCount(1);
  await page.locator('.face-root').tap(); // snoozed: the face goes quiet too
  await waitFor((x) => x.alarms.ringing?.snoozed_until);
  await expect(page.locator('.face-root[data-beep="1"]')).toHaveCount(0);
  await post('/api/input', { event: 'button_short' });
  await waitFor((x) => x.alarms.ringing === null);
});

test('core not answering at alarm time: the face rings by itself, snoozes and stops locally', async ({ page }) => {
  test.setTimeout(75_000);
  // what the face heard before core went away: an alarm a minute ago
  const at = new Date(Date.now() - 60_000).toISOString();
  await page.addInitScript((heard) => localStorage.setItem('dawn.face.backup', JSON.stringify({ heard, silenced: [] })),
    { next: { id: 99, label: 'E2E backup', at }, ring: null, at: Date.now() - 120_000 });
  // core is down as far as the face can tell: no state over the socket, no answer from /api/health
  await page.routeWebSocket(/\/ws$/, (ws) => ws.close());
  await page.route('**/api/health', (route) => route.abort());
  await page.goto('/face');
  const ring = page.locator('.f-ring.local');
  await expect(ring).toBeVisible({ timeout: 45_000 });
  await expect(ring).toContainText('E2E backup');
  await expect(ring).toContainText('Dawn is not responding');
  await expect(page.locator('.face-root[data-beep="1"]')).toHaveCount(1);
  await ring.tap(); // tap = snooze, on the face itself
  await expect(ring).toContainText('Snoozed until');
  await expect(page.locator('.face-root[data-beep="1"]')).toHaveCount(0);
  const box = (await ring.boundingBox())!; // hold = stop
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(2300);
  await page.mouse.up();
  await expect(ring).toHaveCount(0);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('dawn.face.backup') || '{}'));
  expect(saved.silenced).toContain(`alarm:99:${at}`); // stays stopped after a reload
});

test('menu sheet: the slider sets the volume and the Standby tile is the big button', async ({ page }) => {
  // Radio on plays the first preset, so make sure there is one
  const presets = await (await fetch(`${BASE}/api/presets`)).json();
  if (presets.length === 0) await post('/api/presets', { label: 'triple j', source: 'dab:1002' });
  await page.goto('/face');
  await post('/api/dab/play', { sid: '1002' });
  await waitFor((s) => s.face.mode === 'playing' && s.now_playing.station === 'triple j');
  await page.locator('.f-info').tap();
  await expect(page.locator('.face-menu')).toBeVisible();
  // the player is still there above the sheet
  await expect(page.locator('.f-title')).toContainText('triple j');
  const slider = page.locator('#face-volume');
  await slider.fill('65');
  await waitFor((s) => s.audio.volume === 65);
  await expect(page.locator('.f-rail .num')).toHaveText('65');
  // the volume overlay never pops over the sheet
  await expect(page.locator('.face-overlay')).toHaveCount(0);
  // a finger on the sheet keeps it open past the timeout (sim config: 15 s is the default)
  await post('/api/face/menu/activity');
  // tap = the big button's short press: playing -> standby, and the sheet closes
  await page.getByRole('button', { name: 'Standby' }).tap();
  await waitFor((s) => s.face.mode === 'standby' && !s.face.menu_open);
  // from standby the same tile reads Radio on and plays the first preset
  await page.locator('.face-root').tap();
  await page.getByRole('button', { name: 'Radio on' }).tap();
  await waitFor((s) => s.face.mode === 'playing');
  // a tap on the player outside the sheet closes it
  await page.locator('.face-root').tap();
  await waitFor((s) => s.face.menu_open);
  await page.locator('.f-info').tap();
  await waitFor((s) => !s.face.menu_open);
});

test('menu sheet: holding Standby shows the shutdown countdown and releasing cancels it', async ({ page }) => {
  await page.goto('/face');
  await post('/api/dab/play', { sid: '1002' });
  await waitFor((s) => s.face.mode === 'playing');
  await page.locator('.f-info').tap();
  const tile = page.getByRole('button', { name: 'Standby' });
  const box = (await tile.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await waitFor((s) => s.face.shutdown_countdown !== null, 3000);
  await expect(page.locator('.face-overlay')).toContainText('Shutting down in');
  await page.mouse.up();
  await waitFor((s) => s.face.shutdown_countdown === null);
  // released during the countdown: cancelled, still playing
  expect((await (await fetch(`${BASE}/api/state`)).json()).face.mode).toBe('playing');
});

test('night palette follows the light sensor', async ({ page }) => {
  await page.goto('/face');
  await hub({ lux: 0.5 });
  await waitFor((s) => s.display.palette === 'night', 5000);
  await expect.poll(async () => page.evaluate(() => document.documentElement.dataset.palette)).toBe('night');
  await hub({ lux: 150 });
  await waitFor((s) => s.display.palette === 'dark', 5000);
  await expect.poll(async () => page.evaluate(() => document.documentElement.dataset.palette)).toBe('dark');
});

test('DAB playback shows station, DLS and slide; the encoder still pops the volume overlay', async ({ page }) => {
  await page.goto('/face');
  await post('/api/dab/play', { sid: '1002' });
  await waitFor((s) => s.face.mode === 'playing' && s.now_playing.station === 'triple j');
  await expect(page.locator('.face-screen')).toContainText('triple j');
  await expect(page.locator('img.logo-tile')).toBeVisible();
  await post('/api/input', { event: 'encoder_cw' });
  await expect(page.locator('.face-overlay')).toContainText('Volume');
});

test('playing: the control bar stars the station as a preset and cycles presets', async ({ page }) => {
  // make sure there is something to cycle to and that triple j is not a preset yet
  for (const p of await (await fetch(`${BASE}/api/presets`)).json()) {
    if (p.source === 'dab:1002') await fetch(`${BASE}/api/presets/${p.id}`, { method: 'DELETE' });
  }
  const presets = await (await fetch(`${BASE}/api/presets`)).json();
  if (!presets.some((p: any) => p.source === 'dab:1005')) await post('/api/presets', { label: 'Double J', source: 'dab:1005' });
  await page.goto('/face');
  await post('/api/dab/play', { sid: '1002' });
  await waitFor((s) => s.face.mode === 'playing' && s.now_playing.station === 'triple j');
  await expect(page.locator('.f-header .f-clock')).toHaveText(/^\d{1,2}:\d{2}/);
  await expect(page.locator('.f-bar')).toBeVisible();
  await page.getByRole('button', { name: 'Save as preset' }).tap();
  await waitFor((s) => s.presets.some((p: any) => p.source === 'dab:1002'));
  await expect(page.getByRole('button', { name: 'Remove preset' })).toBeVisible();
  await page.getByRole('button', { name: 'Next preset' }).tap();
  await waitFor((s) => s.now_playing.station === 'Double J');
  await expect(page.locator('.f-title')).toContainText('Double J');
  // volume is not in the bar any more; it pops up with the menu sheet
  await expect(page.getByRole('button', { name: 'Volume up' })).toHaveCount(0);
});

test('AirPlay: artwork, device, progress and transport; idle switches to the ambient clock', async ({ page }) => {
  await fetch(`${BASE}/api/config`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ display: { ambient_after_s: 2 } }) });
  try {
    await page.goto('/face');
    await fetch(`${HUB}/airplay/start`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'Sunrise', artist: 'Norah Jones' }) });
    await waitFor((s) => s.face.mode === 'playing' && s.now_playing.source === 'airplay');
    await expect(page.locator('.f-title')).toHaveText('Sunrise');
    await expect(page.locator('.f-sub')).toHaveText('Norah Jones');
    await expect(page.locator('.f-device')).toContainText("Sam's iPhone");
    await expect(page.locator('.f-progress')).toBeVisible();
    await expect(page.locator('.f-times')).toContainText('3:44');
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
    // no touch for ambient_after_s -> ambient clock with the now-playing strip; a tap brings the player back
    await expect(page.locator('.f-ambient')).toBeVisible({ timeout: 6000 });
    await expect(page.locator('.f-ambient .f-bar')).toContainText('Sunrise');
    await page.locator('.face-root').tap();
    await expect(page.locator('.f-progress')).toBeVisible();
    await waitFor((s) => !s.face.menu_open);
  } finally {
    await fetch(`${HUB}/airplay/stop`, { method: 'POST' });
    await fetch(`${BASE}/api/config`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ display: { ambient_after_s: 20 } }) });
  }
});

test('sleep mode: the clock alone; it moves, a tap wakes the full face, ringing wins', async ({ page }) => {
  await config({ display: { sleep: { enabled: true, jump_every_s: 10 } } });
  try {
    await page.goto('/face');
    expect((await post('/api/display/sleep', { on: true })).status).toBe(200);
    await waitFor((s) => s.face.mode === 'sleep' && s.display.sleep_reason === 'manual');
    const clock = page.locator('[data-testid=sleep] .f-sleep-clock');
    await expect(clock).toBeVisible();
    await expect(clock.locator('.t')).toHaveText(/^\d{1,2}:\d{2}/);
    // nothing else: no strip, no background, amber on black
    await expect(page.locator('.f-bar')).toHaveCount(0);
    await expect(page.locator('.f-scene')).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.palette)).toBe('night');
    // it fades to a new place every jump_every_s
    const where = await clock.getAttribute('style');
    await expect.poll(() => clock.getAttribute('style'), { timeout: 15000 }).not.toBe(where);
    // a tap wakes the full face for a while, without opening the menu
    await page.locator('.face-root').tap();
    const s = await waitFor((x) => x.face.mode === 'standby');
    expect(s.face.menu_open).toBe(false);
    await expect(page.locator('.f-ambient-clock')).toBeVisible();
    // asleep again, an alarm rings: ringing shows at once and sleep mode lets go
    await post('/api/display/sleep', { on: true });
    await waitFor((x) => x.face.mode === 'sleep');
    await post('/api/alarms/test', { source: 'chime:gentle_bell', label: 'E2E sleep', volume: 30 });
    await waitFor((x) => x.face.mode === 'ringing');
    await expect(page.locator('.face-screen')).toContainText('E2E sleep');
    await waitFor((x) => x.display.sleep === false);
    await post('/api/alarms/stop');
    await waitFor((x) => x.face.mode === 'standby');
  } finally {
    await post('/api/alarms/stop');
    await post('/api/display/sleep', { on: false });
    await config({ display: { sleep: SLEEP_DEFAULTS } });
  }
});

test('sleep mode with the screen off: black until a tap shows the clock', async ({ page }) => {
  await config({ display: { sleep: { enabled: true, screen_off: true } } });
  try {
    await page.goto('/face');
    await post('/api/display/sleep', { on: true });
    await waitFor((s) => s.face.mode === 'sleep' && s.display.sleep_screen_off);
    await expect(page.locator('[data-testid=sleep]')).toBeVisible();
    await expect(page.locator('.f-sleep-clock')).toHaveCount(0);
    await waitFor((s) => s.display.brightness === 0);
    await page.locator('.face-root').tap();
    await waitFor((s) => s.face.peek_until !== null && s.face.mode === 'sleep');
    await expect(page.locator('.f-sleep-clock')).toBeVisible();
  } finally {
    await post('/api/display/sleep', { on: false });
    await config({ display: { sleep: SLEEP_DEFAULTS } });
  }
});
