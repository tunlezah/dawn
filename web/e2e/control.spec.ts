import { test, expect } from '@playwright/test';
import { BASE, post, reset, waitFor } from './helpers';

test.beforeEach(async () => { await reset(); });

test('home shows the clock and quick actions', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText(/^\d{1,2}:\d{2}/).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Next alarm' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Quick actions' })).toBeVisible();
});

test('alarms: add, see it listed, skip next, delete', async ({ page }) => {
  // clean leftovers from earlier runs, then use a unique label
  for (const a of await (await fetch(BASE + '/api/alarms')).json()) {
    if (String(a.label).startsWith('PW ')) await fetch(`${BASE}/api/alarms/${a.id}`, { method: 'DELETE' });
  }
  const label = `PW ${Date.now().toString().slice(-6)}`;
  await page.goto('/alarms');
  await page.getByRole('button', { name: '+ Add' }).click();
  await page.getByPlaceholder('Label').fill(label);
  await page.getByRole('button', { name: 'Save' }).click();
  const labelEl = page.getByText(label, { exact: true });
  await expect(labelEl).toBeVisible();
  const row = page.locator('div.flex.items-center', { has: labelEl }).last();
  await row.getByRole('button', { name: 'skip' }).click();
  await expect(row.getByText('next skipped')).toBeVisible();
  const alarms = await (await fetch(BASE + '/api/alarms')).json();
  const mine = alarms.find((a: any) => a.label === label);
  expect(mine.skip_next).toBe(true);
  await fetch(`${BASE}/api/alarms/${mine.id}`, { method: 'DELETE' });
});

test('radio: stations are listed with logos and play works', async ({ page }) => {
  await page.goto('/radio');
  const svc = await (await fetch(BASE + '/api/dab/services')).json();
  if (svc.length === 0) {
    await post('/api/dab/scan');
    await waitFor((s) => !s.dab.scan.running && s.dab.services.length > 0, 60000);
  }
  await expect(page.getByText('ABC Classic')).toBeVisible();
  await page.getByRole('button', { name: /Play/ }).first().click();
  await waitFor((s) => s.now_playing.source === 'dab');
  await expect(page.getByRole('button', { name: 'Stop' }).first()).toBeVisible();
});

test('timers: starting a sleep timer while playing promotes it; cancel keeps playing', async ({ page }) => {
  await post('/api/dab/play', { sid: '1003' });
  await waitFor((s) => s.audio.active_source === 'dab');
  await page.goto('/timers');
  await page.getByRole('button', { name: /^30/ }).first().click();
  const s = await waitFor((x) => x.timers.sleep !== null);
  expect(s.audio.sources[0].priority).toBe(80);
  await page.getByRole('button', { name: 'Cancel' }).first().click();
  const t = await waitFor((x) => x.timers.sleep === null);
  expect(t.audio.active_source).toBe('dab');
});

test('settings: change the device name through the config API and see it in state', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  await fetch(BASE + '/api/config', { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ general: { name: 'Dawn (e2e)' } }) });
  await waitFor((s) => s.settings.name === 'Dawn (e2e)');
  await expect(page.locator('main')).toBeVisible();
  await fetch(BASE + '/api/config', { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ general: { name: 'Dawn (sim)' } }) });
});

test('status: time sources and logs render', async ({ page }) => {
  await page.goto('/status');
  await expect(page.getByRole('heading', { name: 'Time' })).toBeVisible();
  await expect(page.getByText('GPS', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Logs' })).toBeVisible();
  await expect(page.locator('pre')).not.toContainText('Loading…', { timeout: 10000 });
});
