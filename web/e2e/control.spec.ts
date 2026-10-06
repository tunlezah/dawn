import { test, expect } from '@playwright/test';
import { BASE, SLEEP_DEFAULTS, config, hub, post, reset, state, waitFor } from './helpers';

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

test('diagnostics: from More; the overview, DAB live signal and plots, and the time chains', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: /Diagnostics/ }).click();
  await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Areas' })).toBeVisible();
  await page.getByRole('tab', { name: /DAB radio/ }).click();
  await expect(page.getByText('Signal-to-noise', { exact: true })).toBeVisible();
  await expect(page.getByText('ABC Sydney').first()).toBeVisible();
  const spectrum = page.locator('figure', { hasText: 'Spectrum' }).first();
  await expect(spectrum.locator('svg')).toBeVisible();
  // every chart has a table view
  await spectrum.getByRole('button', { name: 'Table' }).click();
  await expect(spectrum.locator('table')).toBeVisible();
  await page.getByRole('tab', { name: /Time sync/ }).click();
  for (const chain of ['GPS → chrony', 'DAB → chrony', 'Network → chrony']) await expect(page.getByText(chain, { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: /GPS/ }).click();
  await expect(page.locator('figure', { hasText: 'Where they are' }).locator('svg')).toBeVisible();
});

test('diagnostics: an unplugged GPS shows as a problem with its fix, and clears', async ({ page }) => {
  try {
    await hub({ gps_present: false });
    await post('/api/diag/run');
    await page.goto('/diagnostics');
    await expect(page.getByRole('heading', { name: 'Needs attention' })).toBeVisible();
    await expect(page.locator('[data-check="gps.receiver"]').first()).toContainText('GPS receiver');
    await page.getByRole('tab', { name: /GPS/ }).click();
    await page.getByRole('button', { name: 'Restart gpsd' }).first().click();
    await expect(page.getByRole('status').first()).toContainText('gpsd');
  } finally {
    await hub({ gps_present: true });
  }
  await post('/api/diag/run');
  const s = await state();
  expect(s.diagnostics.problems.some((p: any) => p.id === 'gps.receiver')).toBe(false);
});

test('display: sleep mode can be turned on, sent to sleep and woken from the web', async ({ page }) => {
  try {
    await page.goto('/display');
    await page.getByRole('switch', { name: 'Use sleep mode' }).click();
    await waitFor((s) => s.display.sleep_enabled);
    await expect(page.getByTestId('sleep-next')).toContainText('Next sleep');
    await page.getByRole('button', { name: 'Sleep now' }).click();
    await waitFor((s) => s.display.sleep && s.face.mode === 'sleep');
    await expect(page.getByTestId('sleep-status')).toContainText('asleep');
    await page.getByRole('button', { name: 'Wake now' }).click();
    await waitFor((s) => !s.display.sleep);
    await expect(page.getByTestId('sleep-status')).toContainText('awake');
  } finally {
    await post('/api/display/sleep', { on: false });
    await config({ display: { sleep: SLEEP_DEFAULTS } });
  }
});
