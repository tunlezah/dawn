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

test('diagnostics: no GPS module is "not fitted", never a problem; plugged in, unplugged (missing) and forgotten', async ({ page }) => {
  test.setTimeout(90_000);
  const gpsProblem = (s: any) => s.diagnostics.problems.some((p: any) => p.id.startsWith('gps.') || p.id.startsWith('time.gps'));
  try {
    // no module, and none remembered: "not fitted"
    await hub({ gps_present: false });
    // Forget is offered once the module shows as gone (as the Settings button is): not while it still reads present
    await waitFor((s) => s.time_sources.gps.presence !== 'present', 20_000);
    await post('/api/diag/action/gps.forget');
    await waitFor((s) => s.time_sources.gps.presence === 'absent', 20_000);
    await post('/api/diag/run');
    expect(gpsProblem(await state())).toBe(false);
    await page.goto('/diagnostics');
    await page.getByRole('tab', { name: /GPS/ }).click();
    await expect(page.getByText('No GPS module fitted').first()).toBeVisible();
    await expect(page.locator('[data-check="gps.receiver"]').getByRole('img', { name: 'Not fitted' })).toBeVisible();
    await page.goto('/settings');
    await expect(page.getByTestId('settings-gps-chip')).toHaveText('No GPS module');
    await expect(page.getByText('No GPS module detected. Plug one in and it appears here.')).toBeVisible();
    // plugged in: present, and the switches appear
    await hub({ gps_present: true });
    await waitFor((s) => s.time_sources.gps.presence === 'present', 20_000);
    await expect(page.getByText('Use GPS for time and location')).toBeVisible();
    // unplugged: a module that was there and has gone is a warning, with Forget
    await hub({ gps_present: false });
    await waitFor((s) => s.time_sources.gps.presence === 'missing', 20_000);
    await expect(page.getByTestId('settings-gps-chip')).toHaveText('Missing');
    await post('/api/diag/run');
    const s = await state();
    expect(s.diagnostics.problems.some((p: any) => p.id === 'gps.receiver')).toBe(true);
    await page.goto('/diagnostics');
    await page.getByRole('tab', { name: /GPS/ }).click();
    await expect(page.locator('[data-check="gps.receiver"]').getByRole('img', { name: 'Warning' })).toBeVisible();
    await page.getByRole('button', { name: 'Forget the GPS module' }).first().click();
    await waitFor((x) => x.time_sources.gps.presence === 'absent', 10_000);
  } finally {
    await hub({ gps_present: true });
  }
  await waitFor((s) => s.time_sources.gps.presence === 'present', 20_000);
  await post('/api/diag/run');
  expect(gpsProblem(await state())).toBe(false);
});

test('diagnostics: a decoder that answers but decodes nothing is restarted by itself', async ({ page }) => {
  test.setTimeout(120_000);
  const repairs = async () => {
    const ev = await (await fetch(BASE + '/api/events?limit=500')).json();
    return ev.filter((e: any) => e.kind === 'supervisor_repair' && String(e.reason).includes('no sync')).length;
  };
  const before = await repairs();  // the simulator's database outlives a run
  try {
    await hub({ dab_zombie: true });  // welle answers, the stick delivers nothing: no sync
    // no_sync_restart_s is 20 s in the simulator (180 s on a Pi); the supervisor checks every 15 s
    await expect.poll(repairs, { timeout: 90_000, intervals: [1000] }).toBeGreaterThan(before);
    await post('/api/diag/run');
    await page.goto('/diagnostics');
    await page.getByRole('tab', { name: /System/ }).click();
    await expect(page.locator('[data-check="system.supervisor"]')).toContainText('DAB decoder');
  } finally {
    await hub({ dab_zombie: false });
  }
  await waitFor((s) => s.dab.sync, 30_000);
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
