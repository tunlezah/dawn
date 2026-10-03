// Burn-in study: render the real standby face from the simulator every 20 minutes across one day,
// once as it is today and once per candidate mitigation (injected as CSS, nothing in the app changes).
// Usage (with the simulator running): node docs/research/mockups/capture-day.mjs <outDir>
// Room profile: lit 06:00-22:00 (lux 150, dark theme + scene), dark 22:00-06:00 (lux 0.5, night palette).
// All frames are on Saturday 3 October 2026 (AEST): Sydney's DST change is in the early hours of the 4th.
import { chromium } from '../../../web/node_modules/@playwright/test/index.mjs';
import { mkdirSync } from 'node:fs';

const base = process.env.DAWN_URL || 'http://127.0.0.1:8080';
const hub = process.env.DAWN_HUB || 'http://127.0.0.1:8099';
const out = process.argv[2] || 'frames';
const STEP_MIN = 20;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const call = (method, url, body) => fetch(url, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) }).catch(() => {});

// Pixel orbit: the foreground walks a closed path of +/-ORBIT_X by +/-ORBIT_Y px, one pixel per minute.
export const ORBIT_X = 8, ORBIT_Y = 6;
export function orbitAt(minuteOfDay) {
  // a rectangular spiral would also do; a Lissajous figure visits the box evenly and never jumps
  const t = minuteOfDay / 60; // hours
  return { x: Math.round(ORBIT_X * Math.sin(2 * Math.PI * t / 1.7)), y: Math.round(ORBIT_Y * Math.sin(2 * Math.PI * t / 1.1 + 0.6)) };
}

const VARIANTS = {
  today: () => '',
  orbit: (o) => `.f-ambient > :not(.f-scene):not(.f-horizon) { translate: ${o.x}px ${o.y}px; }`,
  'orbit-autohide': (o) => `.f-ambient > :not(.f-scene):not(.f-horizon) { translate: ${o.x}px ${o.y}px; } .f-ambient .f-bar { opacity: 0; }`,
};

const browser = await chromium.launch();
for (const v of Object.keys(VARIANTS)) mkdirSync(`${out}/${v}`, { recursive: true });

await call('POST', `${hub}/set`, { lux: 150, gps_fix: true, dab_sync: true, sdr_present: true, network_online: true, audio_flowing: true });
await call('PUT', `${base}/api/display/mode`, { mode: 'manual' });
await call('PUT', `${base}/api/display/brightness`, { value: 100 });
await call('PUT', `${base}/api/audio/volume`, { volume: 43 });
await call('POST', `${base}/api/audio/standby`);
const alarms = await (await fetch(`${base}/api/alarms`)).json().catch(() => []);
if (!alarms.length) await call('POST', `${base}/api/alarms`, { label: 'Weekday', time: '06:30', repeat: 'weekdays', source: 'chime:gentle_bell' });

const ctx = await browser.newContext({ viewport: { width: 800, height: 480 }, deviceScaleFactor: 1 });
let night = null;
for (let m = 0; m < 24 * 60; m += STEP_MIN) {
  const isNight = m < 6 * 60 || m >= 22 * 60;
  if (isNight !== night) { await call('POST', `${hub}/set`, { lux: isNight ? 0.5 : 150 }); await sleep(3500); night = isNight; }
  const hh = String(Math.floor(m / 60)).padStart(2, '0'), mm = String(m % 60).padStart(2, '0');
  const weather = isNight ? 'clear-night' : m < 18 * 60 ? 'partly-day' : 'clear-night';
  const page = await ctx.newPage();
  await page.goto(`${base}/face?at=${encodeURIComponent(`2026-10-03T${hh}:${mm}:00+10:00`)}&weather=${weather}&temp=17&tmin=11&tmax=21`, { waitUntil: 'networkidle' });
  await sleep(700);
  const style = await page.addStyleTag({ content: '/* variant */' });
  for (const [name, css] of Object.entries(VARIANTS)) {
    await style.evaluate((el, c) => { el.textContent = c; }, css(orbitAt(m)));
    await sleep(120);
    await page.screenshot({ path: `${out}/${name}/${hh}${mm}.png` });
  }
  await page.close();
}
await call('POST', `${hub}/set`, { lux: 150 });
await browser.close();
console.log('frames written to', out);
