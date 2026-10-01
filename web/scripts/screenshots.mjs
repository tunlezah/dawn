// Render the face and control UI to PNGs in docs/screenshots using Playwright.
// Usage: node scripts/screenshots.mjs [baseUrl=http://127.0.0.1:8080] [hubUrl=http://127.0.0.1:8099]
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const base = process.argv[2] || process.env.DAWN_URL || 'http://127.0.0.1:8080';
const hub = process.argv[3] || process.env.DAWN_HUB || 'http://127.0.0.1:8099';
const out = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'screenshots');
mkdirSync(out, { recursive: true });

const post = (url, body) => fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) }).catch(() => {});
const del = (url) => fetch(url, { method: 'DELETE' }).catch(() => {});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
const shots = [];

async function face(name, setup, settle = 1200) {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 480 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.goto(`${base}/face`, { waitUntil: 'networkidle' });
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

// Baseline sim state
await post(`${hub}/set`, { lux: 150, gps_fix: true, dab_sync: true, sdr_present: true, network_online: true });
await post(`${base}/api/audio/standby`);
await sleep(800);

await face('standby');

// Phase-dependent scenes are attempted; missing endpoints are ignored (404s are fine).
await post(`${base}/api/audio/play`, { source: 'dab:1002' });
await sleep(2500);
await face('playing');
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

await post(`${base}/api/face/touch`);
await sleep(600);
await face('menu');
await post(`${base}/api/face/menu`, { open: false });

await post(`${base}/api/face/demo`, { mode: 'lightwake' });
await sleep(600);
await face('lightwake');
await post(`${base}/api/face/demo`, { mode: null });

await post(`${hub}/set`, { lux: 0.5 });
await sleep(3500);
await face('night-standby');
await post(`${hub}/set`, { lux: 150 });

await post(`${base}/api/face/demo`, { mode: 'setup' });
await sleep(800);
await face('setup');
await post(`${base}/api/face/demo`, { mode: null });

await fetch(`${base}/api/config`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ display: { layout: 'round' } }) }).catch(() => {});
await sleep(2500);
await face('round-standby');
await fetch(`${base}/api/config`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ display: { layout: 'rect' } }) }).catch(() => {});

for (const [name, path] of [['home', '/'], ['alarms', '/alarms'], ['radio', '/radio'], ['timers', '/timers'], ['display', '/display'], ['audio', '/audio'], ['status', '/status'], ['settings', '/settings'], ['about', '/about']]) {
  await control(name, path);
}
await control('home-desktop', '/', { width: 1280, height: 800 });

await browser.close();
console.log(shots.map((s) => s.replace(out + '/', '')).join('\n'));
