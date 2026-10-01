import { test, expect } from '@playwright/test';
import { hub, post, reset, waitFor } from './helpers';

test.beforeEach(async () => { await reset(); });

test('standby shows the time, date and status dots', async ({ page }) => {
  await page.goto('/face');
  await expect(page.locator('.face-time')).toBeVisible();
  await expect(page.locator('.face-time')).toHaveText(/^\d{1,2}:\d{2}/);
  await expect(page.locator('.face-dots')).toContainText('GPS');
  await expect(page.locator('.face-dots')).toContainText('DAB');
});

test('tapping the face opens the menu and the nap picker starts a countdown', async ({ page }) => {
  await page.goto('/face');
  await page.locator('.face-root').tap();
  await expect(page.locator('.face-menu')).toBeVisible();
  await page.locator('.face-menu .tile', { hasText: 'Nap' }).first().tap();
  await page.locator('.face-menu .tile', { hasText: 'min' }).first().tap();
  await waitFor((s) => s.timers.nap !== null);
  await expect(page.locator('.face-screen')).toContainText(/\d+:\d{2}/);
  await expect(page.locator('.face-chip', { hasText: 'Nap' })).toBeVisible();
});

test('ringing: whole screen snoozes, button stops', async ({ page }) => {
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

test('night palette follows the light sensor', async ({ page }) => {
  await page.goto('/face');
  await hub({ lux: 0.5 });
  await waitFor((s) => s.display.palette === 'night', 5000);
  await expect.poll(async () => page.evaluate(() => document.documentElement.dataset.palette)).toBe('night');
  await hub({ lux: 150 });
  await waitFor((s) => s.display.palette === 'dark', 5000);
  await expect.poll(async () => page.evaluate(() => document.documentElement.dataset.palette)).toBe('dark');
});

test('DAB playback shows station, DLS and slide; volume overlay appears', async ({ page }) => {
  await page.goto('/face');
  await post('/api/dab/play', { sid: '1002' });
  await waitFor((s) => s.face.mode === 'playing' && s.now_playing.station === 'triple j');
  await expect(page.locator('.face-screen')).toContainText('triple j');
  await expect(page.locator('img.logo-tile')).toBeVisible();
  await post('/api/input', { event: 'encoder_cw' });
  await expect(page.locator('.face-overlay')).toContainText('Volume');
});
