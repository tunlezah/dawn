// Render the face and control UI to PNGs in docs/screenshots using Playwright.
// Usage: node scripts/screenshots.mjs [baseUrl=http://127.0.0.1:8080] [hubUrl=http://127.0.0.1:8099]
import { chromium } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const base = process.argv[2] || process.env.DAWN_URL || 'http://127.0.0.1:8080';
const hub = process.argv[3] || process.env.DAWN_HUB || 'http://127.0.0.1:8099';
const out = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'screenshots');
mkdirSync(out, { recursive: true });

const json = (method, url, body) => fetch(url, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) }).catch(() => {});
const post = (url, body) => json('POST', url, body);
const put = (url, body) => json('PUT', url, body);
const del = (url) => fetch(url, { method: 'DELETE' }).catch(() => {});
const config = (patch) => json('PATCH', `${base}/api/config`, patch);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
const shots = [];

async function face(name, setup, settle = 1200, query = '') {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 480 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.goto(`${base}/face${query}`, { waitUntil: 'networkidle' });
  if (setup) await setup(page);
  await sleep(settle);
  const file = `${out}/face-${name}.png`;
  await page.screenshot({ path: file });
  shots.push(file);
  await ctx.close();
}

async function control(name, path, viewport = { width: 390, height: 844 }, setup, settle = 1000) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, isMobile: viewport.width < 600, hasTouch: viewport.width < 600 });
  const page = await ctx.newPage();
  await page.goto(`${base}${path}`, { waitUntil: 'networkidle' });
  if (viewport.width < 600) await page.addStyleTag({ content: 'nav{display:none !important}' });
  if (setup) await setup(page);
  await sleep(settle);
  const file = `${out}/control-${name}.png`;
  await page.screenshot({ path: file, fullPage: viewport.width < 600 });
  shots.push(file);
  await ctx.close();
}

// Board of face renders (README hero). Pure HTML on a blank page so no extra tooling is needed.
async function board(name, files, cols = 2) {
  const rows = Math.ceil(files.length / cols);
  const ctx = await browser.newContext({ viewport: { width: cols * 820 + 20, height: rows * 500 + 20 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const tiles = files.map((f) => `<img src="data:image/png;base64,${readFileSync(f).toString('base64')}" style="width:800px;height:480px;border-radius:18px;border:1px solid #1d2230;display:block">`).join('');
  await page.setContent(`<body style="margin:0;background:#05070a;display:grid;grid-template-columns:repeat(${cols}, 800px);gap:20px;padding:20px">${tiles}</body>`);
  await sleep(500);
  const file = `${out}/face-${name}.png`;
  await page.screenshot({ path: file });
  shots.push(file);
  await ctx.close();
}

// Baseline sim state: sensors live, full brightness (the sim's software dimmer would grey the renders), standby.
await post(`${hub}/set`, { lux: 150, gps_fix: true, dab_sync: true, sdr_present: true, network_online: true, audio_flowing: true });
await put(`${base}/api/display/mode`, { mode: 'manual' });
await put(`${base}/api/display/brightness`, { value: 100 });
await put(`${base}/api/audio/volume`, { volume: 43 });
await post(`${base}/api/audio/standby`);
// a couple of presets so the star and the presets page have content
const presets = await (await fetch(`${base}/api/presets`)).json().catch(() => []);
for (const [label, source] of [['triple j', 'dab:1002'], ['Double J', 'dab:1005'], ['ABC Radio Sydney', 'dab:1001']]) {
  if (!presets.some((p) => p.source === source)) await post(`${base}/api/presets`, { label, source });
}
await sleep(800);

await face('standby');

// Phase-dependent scenes are attempted; missing endpoints are ignored (404s are fine).
await post(`${base}/api/audio/play`, { source: 'dab:1002' });
await sleep(2500);
await face('playing');

await config({ display: { ambient_after_s: 2 } });
await sleep(600);
await face('ambient-playing', null, 3500);
await config({ display: { ambient_after_s: 20 } });

await post(`${base}/api/face/touch`);
await sleep(600);
await face('menu');
await post(`${base}/api/face/menu`, { open: true, page: 'presets' });
await sleep(500);
await face('presets');
await post(`${base}/api/face/menu`, { open: false });
await post(`${base}/api/audio/standby`);

await post(`${base}/api/alarms/test`, { source: 'chime:gentle_bell', label: 'Weekday' });
await sleep(1500);
await face('ringing');
await post(`${base}/api/alarms/stop`);

await post(`${base}/api/timers/nap`, { minutes: 20 });
await sleep(1200);
await face('nap-countdown');
await del(`${base}/api/timers/nap`);

await post(`${hub}/airplay/start`, { title: 'Sunrise', artist: 'Norah Jones', album: 'Come Away With Me' });
await sleep(1800);
await face('airplay');
await post(`${hub}/airplay/stop`);
await sleep(800);

await post(`${base}/api/face/demo`, { mode: 'lightwake' });
await sleep(600);
await face('lightwake');
await post(`${base}/api/face/demo`, { mode: null });

await post(`${hub}/set`, { lux: 0.5 });
await sleep(3500);
await face('night-standby');
await post(`${hub}/set`, { lux: 150 });

// sleep mode: the clock alone, with the next alarm and the weather icon under it. The sim's software dimmer
// stands in for the backlight; it is hidden here so the render shows the pixels as the panel draws them.
const shotAlarm = (await (await fetch(`${base}/api/alarms`)).json().catch(() => [])).some((a) => a.enabled)
  ? null : await (await json('POST', `${base}/api/alarms`, { label: 'Weekday', time: '06:30', repeat: 'weekdays' }))?.json();
await config({ display: { sleep: { enabled: true } } });
await post(`${base}/api/display/sleep`, { on: true });
await sleep(1500);
const noDimmer = (page) => page.addStyleTag({ content: '.face-dim{display:none !important}' });
await face('sleep', noDimmer, 1200);
await config({ display: { layout: 'round' } });
await sleep(1500);
await face('round-sleep', noDimmer, 1200);
await config({ display: { layout: 'rect' } });
await post(`${base}/api/display/sleep`, { on: false });
await config({ display: { sleep: { enabled: false } } });
if (shotAlarm?.id) await del(`${base}/api/alarms/${shotAlarm.id}`);

await post(`${base}/api/face/demo`, { mode: 'setup' });
await sleep(800);
await face('setup');
await post(`${base}/api/face/demo`, { mode: null });

await config({ display: { layout: 'round' } });
await sleep(2500);
await face('round-standby');
await post(`${base}/api/audio/play`, { source: 'dab:1002' });
await sleep(2000);
await face('round-playing');
await post(`${base}/api/audio/standby`);
await config({ display: { layout: 'rect' } });

await board('board', ['airplay', 'playing', 'presets', 'standby'].map((n) => `${out}/face-${n}.png`));

// Scenic standby across the day, weather and seasons, via the face's demo query (?at=&weather=&temp=).
const SCENES = [
  ['dawn', '2026-04-14T06:30:00+10:00', 'clear-day', 12], ['frost', '2026-07-15T07:45:00+10:00', 'clear-day', -3],
  ['spring', '2026-10-10T09:00:00+11:00', 'partly-day', 17, '&cloud=60'], ['summer', '2026-01-20T13:23:00+11:00', 'clear-day', 38],
  ['smoke', '2026-01-05T15:00:00+11:00', 'partly-day', 33, '&vis=3000&cloud=20'], ['storm', '2026-12-10T16:20:00+11:00', 'thunder', 27, '&wind=45&rain=8'],
  ['autumn', '2026-04-16T15:32:00+10:00', 'clear-day', 19], ['rain', '2026-02-10T11:00:00+11:00', 'rain', 22, '&rain=12&wind=30'],
  ['dusk', '2026-04-14T17:52:00+10:00', 'clear-day', 20], ['fog', '2026-04-16T11:50:00+10:00', 'fog', 15],
  ['night', '2026-10-24T23:00:00+11:00', 'clear-night', 9], ['winter', '2026-07-20T09:00:00+10:00', 'snow', 0, '&wind=25'],
];
for (const [name, at, weather, temp, extra = ''] of SCENES) {
  await face(`scene-${name}`, null, 900, `?at=${encodeURIComponent(at)}&weather=${weather}&temp=${temp}&tmin=${temp - 5}&tmax=${temp + 4}${extra}`);
}
await board('scenes', SCENES.map(([n]) => `${out}/face-scene-${n}.png`), 3);

await put(`${base}/api/display/mode`, { mode: 'auto' });
for (const [name, path] of [['home', '/'], ['alarms', '/alarms'], ['radio', '/radio'], ['timers', '/timers'], ['display', '/display'], ['audio', '/audio'], ['status', '/status'], ['settings', '/settings'], ['about', '/about']]) {
  await control(name, path);
}
await control('home-desktop', '/', { width: 1280, height: 800 });

// Diagnostics: the overview on a phone, and the DAB, GPS and time tabs on a desktop, scrolled to their charts
const scrollTo = (text) => async (page) => {
  await page.waitForTimeout(3500);  // live readings and the first plots
  await page.evaluate((t) => [...document.querySelectorAll('section')].find((x) => x.textContent?.includes(t))?.scrollIntoView({ block: 'start' }), text);
};
await post(`${base}/api/diag/run`);
await control('diagnostics', '/diagnostics');
await control('diagnostics-dab', '/diagnostics#dab', { width: 1280, height: 900 }, (page) => page.waitForTimeout(3500));
await control('diagnostics-dab-plots', '/diagnostics#dab', { width: 1280, height: 1000 }, scrollTo('Signal plots'), 1500);
await control('diagnostics-gps', '/diagnostics#gps', { width: 1280, height: 900 }, scrollTo('Where they are'), 1500);
await control('diagnostics-time', '/diagnostics#time', { width: 1280, height: 1000 }, scrollTo('From each time source'), 1000);
await config({ display: { sleep: { enabled: true } } });
await control('display-sleep', '/display', { width: 1280, height: 1000 }, scrollTo('Sleep mode'), 1000);
await config({ display: { sleep: { enabled: false } } });

await browser.close();
console.log(shots.map((s) => s.replace(out + '/', '')).join('\n'));
