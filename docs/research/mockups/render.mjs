// Research mockups for the standby display study: sleep mode, burn-in mitigations and flight tracking.
// Nothing here is product code. Each mockup is plain HTML that reuses the face's own compiled CSS
// (design tokens, .f-header/.f-body/.f-bar, Inter) served by the simulator, so the renders sit next to
// docs/screenshots/ without a second design system. Live screens (ambient clock, scene) are captured
// from the running face and then frozen and annotated.
//
//   make sim                                  # in another terminal
//   node docs/research/mockups/render.mjs     # writes docs/research/img/*.png
//
// Flight data is a real adsb.lol snapshot around the default config location (Sydney CBD), Saturday
// 3 October 2026 21:34 AEST; map shapes are OpenStreetMap water/runway polygons (see data/).
import { chromium } from '../../../web/node_modules/@playwright/test/index.mjs';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, '..', 'img');
const framesOut = process.env.SLEEP_FRAMES || null; // optional: night frames for the burn-in study
mkdirSync(out, { recursive: true });
const base = process.env.DAWN_URL || 'http://127.0.0.1:8080';
const hub = process.env.DAWN_HUB || 'http://127.0.0.1:8099';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const call = (method, url, body) => fetch(url, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) }).catch(() => {});

const FL = JSON.parse(readFileSync(resolve(here, 'data/flights.json'), 'utf8'));
const MAP = JSON.parse(readFileSync(resolve(here, 'data/map.json'), 'utf8'));
const NOW = '21:34';
const AT = '2026-10-03T21:34:40+10:00';

// ---- formatting ---------------------------------------------------------------------------------
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const thousands = (n) => n.toLocaleString('en-AU');
const fmtAlt = (ft) => (ft < 100 ? 'Ground' : `${thousands(Math.round(ft / 25) * 25)} ft`);
const kmh = (kt) => `${Math.round(kt * 1.852)} km/h`;
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const compass = (deg) => COMPASS[Math.round(deg / 22.5) % 16];
const dist = (d) => (d < 10 ? `${d.toFixed(1)} km` : `${Math.round(d)} km`);
const trend = (fpm) => (fpm > 300 ? 'climbing' : fpm < -300 ? 'descending' : 'level');
const arrow = (fpm) => (fpm > 300 ? '↗' : fpm < -300 ? '↘' : '→');
const flightNo = (a) => a.route?.iata || a.flight || a.reg || a.hex.toUpperCase();
const routeText = (a) => (a.route ? `${a.route.fromName} → ${a.route.toName}` : a.typeName || '');

// Airline tile: the DAB monogram recipe (hashed hue, two letters) instead of real logos.
function tileColour(key) {
  const h = createHash('sha1').update(key.toLowerCase()).digest();
  const hue = h[0] / 255, sat = 0.45 + (h[1] / 255) * 0.25, val = 0.55 + (h[2] / 255) * 0.25;
  const i = Math.floor(hue * 6), f = hue * 6 - i, p = val * (1 - sat), q = val * (1 - f * sat), t = val * (1 - (1 - f) * sat);
  const [r, g, b] = [[val, t, p], [q, val, p], [p, val, t], [p, q, val], [t, p, val], [val, p, q]][i % 6];
  return `#${[r, g, b].map((x) => Math.round(x * 255).toString(16).padStart(2, '0')).join('')}`;
}
const badge = (a, cls = 'mk-badge') => {
  const code = (a.route?.iata || a.flight || '??').slice(0, 2);
  return `<div class="${cls}" style="background:${tileColour(a.flight.slice(0, 3) || code)}">${esc(code)}</div>`;
};

// ---- icons (same drawing language as web/src/face/components/icons.tsx) -------------------------
const S = (inner) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="f-icon" aria-hidden="true">${inner}</svg>`;
const ICON = {
  alarm: S('<circle cx="12" cy="13" r="7.5" /><path d="M12 9.5V13l2.5 1.8M4.5 5.5l2.5-2M19.5 5.5l-2.5-2" />'),
  volume: S('<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor" stroke="none" /><path d="M15.5 9.3a4 4 0 0 1 0 5.4" />'),
  plane: '<svg viewBox="0 0 24 24" class="f-icon" aria-hidden="true"><path fill="currentColor" d="M12 2.2c.8 0 1.3 1 1.3 2v5.1l7.4 4.3v1.9l-7.4-2.2v4.6l2.2 1.6v1.5L12 20.2l-3.5.8v-1.5l2.2-1.6v-4.6l-7.4 2.2v-1.9l7.4-4.3V4.2c0-1 .5-2 1.3-2z"/></svg>',
};
// top-down aircraft glyph, nose up, ~22 units long; rotated to the ADS-B track
const PLANE = 'M0,-11 C1.1,-11 1.7,-9.6 1.7,-8.2 L1.7,-3.2 L10,1.8 L10,4.2 L1.7,1.7 L1.7,6.5 L4.3,8.6 L4.3,10.5 L0,9.4 L-4.3,10.5 L-4.3,8.6 L-1.7,6.5 L-1.7,1.7 L-10,4.2 L-10,1.8 L-1.7,-3.2 L-1.7,-8.2 C-1.7,-9.6 -1.1,-11 0,-11 Z';

// ---- radar ----------------------------------------------------------------------------------------
function radar({ W, H, cx, cy, s, rings, near = false, labels = true, selected, glyph = 0.82, clipCircle = null, ringLabelBearing = 30 }) {
  const P = (x, y) => `${(cx + x * s).toFixed(1)},${(cy - y * s).toFixed(1)}`;
  const poly = (r) => `M${r.map(([x, y]) => P(x, y)).join('L')}Z`;
  const water = (near ? MAP.water_near : MAP.water_wide).map(poly).join('');
  const runways = MAP.runways.map(poly).join('');
  const inView = (x, y, pad = 0) => { const X = cx + x * s, Y = cy - y * s; return X > -pad && X < W + pad && Y > -pad && Y < H + pad; };
  const ringsSvg = rings.map((r) => {
    const a = (ringLabelBearing * Math.PI) / 180, lx = cx + Math.sin(a) * r * s, ly = cy - Math.cos(a) * r * s;
    return `<circle cx="${cx}" cy="${cy}" r="${(r * s).toFixed(1)}" class="mk-ring"/><text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" class="mk-ring-lbl" dy="0.35em" text-anchor="middle">${r} km</text>`;
  }).join('');
  const outer = rings[rings.length - 1] * s;
  const ac = FL.aircraft.filter((a) => a.alt_ft >= 300 && inView(a.x, a.y, 20)); // below 300 ft = still on the runway
  const trails = ac.map((a) => {
    const sel = a.hex === selected;
    const keep = sel ? a.trail.length : Math.min(a.trail.length, 6); // ~90 s for the others, the whole climb-out for the selected one
    const pts = [...a.trail.slice(a.trail.length - keep).map(([x, y]) => [x, y]), [a.x, a.y]];
    return pts.slice(1).map((p, i) => `<line x1="${P(...pts[i]).split(',')[0]}" y1="${P(...pts[i]).split(',')[1]}" x2="${P(...p).split(',')[0]}" y2="${P(...p).split(',')[1]}" class="mk-trail ${sel ? 'sel' : ''}" style="opacity:${(0.12 + 0.6 * ((i + 1) / pts.length)).toFixed(2)}"/>`).join('');
  }).join('');
  const glyphs = ac.map((a) => {
    const [X, Y] = P(a.x, a.y).split(',').map(Number);
    const sel = a.hex === selected;
    const lbl = labels && a.dist_km < (near ? 14 : 36) && a.alt_ft >= 300
      ? `<text x="${X + 13}" y="${Y - 2}" class="mk-ac-lbl ${sel ? 'sel' : ''}">${esc(flightNo(a))}</text><text x="${X + 13}" y="${Y + 11}" class="mk-ac-alt">${esc(fmtAlt(a.alt_ft))} ${arrow(a.rate_fpm)}</text>` : '';
    return `<g class="mk-ac ${sel ? 'sel' : ''}"><path d="${PLANE}" transform="translate(${X} ${Y}) rotate(${a.track ?? 0}) scale(${sel ? glyph * 1.25 : glyph})"/></g>${lbl}`;
  }).join('');
  const syd = MAP.runways.length ? (() => { const r = MAP.runways[0]; const [x, y] = r[0]; return `<text x="${(cx + x * s - 16).toFixed(1)}" y="${(cy - y * s + 4).toFixed(1)}" class="mk-place" text-anchor="end">SYD</text>`; })() : '';
  const clip = clipCircle ? `<clipPath id="mk-clip"><circle cx="${clipCircle[0]}" cy="${clipCircle[1]}" r="${clipCircle[2]}"/></clipPath>` : '';
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid slice" class="mk-radar" aria-hidden="true">
    <defs>${clip}<radialGradient id="mk-vignette" cx="${cx / W}" cy="${cy / H}" r="0.75"><stop offset="0.55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.55"/></radialGradient></defs>
    <g ${clipCircle ? 'clip-path="url(#mk-clip)"' : ''}>
      <rect width="${W}" height="${H}" class="mk-land"/>
      <path d="${water}" class="mk-water"/>
      <path d="${runways}" class="mk-runway"/>${syd}
      ${ringsSvg}
      <line x1="${cx}" y1="${cy - outer - 6}" x2="${cx}" y2="${cy - outer + 6}" class="mk-ring"/><text x="${cx}" y="${cy - outer - 12}" class="mk-ring-lbl" text-anchor="middle">N</text>
      <circle cx="${cx}" cy="${cy}" r="7" class="mk-home-ring"/><circle cx="${cx}" cy="${cy}" r="3" class="mk-home"/>
      ${trails}${glyphs}
      <rect width="${W}" height="${H}" fill="url(#mk-vignette)"/>
    </g></svg>`;
}

// ---- page shell -----------------------------------------------------------------------------------
const MOCK_CSS = `
  .mk-radar { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .mk-land { fill: color-mix(in srgb, var(--fg) 5.5%, var(--bg)); }
  .mk-water { fill: color-mix(in srgb, #000 35%, var(--bg)); stroke: color-mix(in srgb, #000 35%, var(--bg)); stroke-width: 0.8; } /* stroke hides tile seams */
  .mk-runway { fill: color-mix(in srgb, var(--fg) 30%, transparent); stroke: color-mix(in srgb, var(--fg) 30%, transparent); stroke-width: 1.4; stroke-linejoin: round; }
  .mk-place { font-size: 11px; font-weight: 600; letter-spacing: 0.12em; fill: var(--fg-faint); }
  .mk-ring { fill: none; stroke: color-mix(in srgb, var(--fg) 13%, transparent); stroke-width: 1; }
  .mk-ring-lbl { font-size: 11px; font-weight: 500; fill: var(--fg-faint); letter-spacing: 0.04em; font-variant-numeric: tabular-nums; }
  .mk-home { fill: var(--fg); } .mk-home-ring { fill: none; stroke: color-mix(in srgb, var(--fg) 35%, transparent); stroke-width: 1.2; }
  .mk-trail { stroke: var(--fg); stroke-width: 1.6; stroke-linecap: round; }
  .mk-trail.sel { stroke: var(--accent); stroke-width: 2.2; }
  .mk-ac path { fill: color-mix(in srgb, var(--fg) 88%, transparent); }
  .mk-ac.sel path { fill: var(--accent); filter: drop-shadow(0 0 6px color-mix(in srgb, var(--accent) 70%, transparent)); }
  .mk-ac-lbl { font-size: 12px; font-weight: 650; fill: var(--fg-muted); letter-spacing: 0.02em; paint-order: stroke; stroke: var(--bg); stroke-width: 3px; stroke-linejoin: round; }
  .mk-ac-lbl.sel { fill: var(--accent); font-size: 13px; }
  .mk-ac-alt { font-size: 11px; font-weight: 500; fill: var(--fg-faint); font-variant-numeric: tabular-nums; paint-order: stroke; stroke: var(--bg); stroke-width: 3px; stroke-linejoin: round; }
  .mk-over { z-index: 2; }
  .mk-spacer { flex: 1; }
  .mk-scrim-top { position: absolute; inset: 0 0 auto 0; height: 34%; z-index: 1; pointer-events: none; background: linear-gradient(to bottom, color-mix(in srgb, var(--bg) 92%, transparent), transparent); }
  .mk-scrim-bottom { position: absolute; inset: auto 0 0 0; height: 30%; z-index: 1; pointer-events: none; background: linear-gradient(to top, color-mix(in srgb, var(--bg) 80%, transparent), transparent); }
  .f-bar.mk-glass { background: color-mix(in srgb, var(--bg) 72%, transparent); border-color: color-mix(in srgb, var(--fg) 10%, transparent); backdrop-filter: blur(8px); }
  .mk-badge { width: 9.4vmin; height: 9.4vmin; border-radius: 2.4vmin; display: grid; place-items: center; font-weight: 700; font-size: 4vmin; color: rgba(255,255,255,0.92); flex: none; letter-spacing: -0.02em; }
  .f-bar .cell .label .top.num { font-size: 4.6vmin; letter-spacing: 0; text-transform: none; font-variant-numeric: tabular-nums; }
  .f-art.mk-scope { position: relative; overflow: hidden; background: color-mix(in srgb, var(--fg) 5.5%, var(--bg)); }
  .mk-dim { color: var(--fg-faint); }
  /* sleep mode */
  .mk-sleep { position: absolute; inset: 0; background: #000; }
  .mk-sleep-clock { position: absolute; display: flex; flex-direction: column; align-items: center; gap: 0.6vmin; transform: translate(-50%, -50%); }
  .mk-sleep-clock .t { font-size: 15vmin; font-weight: 600; letter-spacing: -0.03em; line-height: 1; color: color-mix(in srgb, var(--fg) 72%, #000); font-variant-numeric: tabular-nums; }
  .mk-sleep-clock .a { display: flex; align-items: center; gap: 1.2vmin; font-size: 3.4vmin; font-weight: 500; color: color-mix(in srgb, var(--fg-muted) 70%, #000); font-variant-numeric: tabular-nums; }
  .mk-panel-edge { position: absolute; inset: 0; border-radius: 50%; border: 1px dashed #3a3f4b; pointer-events: none; }
  .mk-sleep-clock .a .f-icon { width: 3.6vmin; height: 3.6vmin; }
  .mk-sleep-clock.ghost { opacity: 0.16; }
  .mk-note { position: absolute; font: 500 11px/1.3 'Inter Variable', Inter, sans-serif; color: #7d8494; letter-spacing: 0.02em; }
  .mk-path { position: absolute; inset: 0; pointer-events: none; }
  /* ambient flight line */
  .f-ambient .f-next.mk-flight { margin-top: 1.6vmin; font-size: 3.9vmin; }
  .f-ambient .f-next.mk-flight .f-icon { color: var(--accent); width: 4.6vmin; height: 4.6vmin; }
  /* round */
  .round .mk-round-card { align-self: center; text-align: center; display: flex; flex-direction: column; gap: 0.8vmin; padding: 2.2vmin 5vmin; border-radius: var(--f-radius); background: color-mix(in srgb, var(--bg) 72%, transparent); border: 1px solid color-mix(in srgb, var(--fg) 10%, transparent); backdrop-filter: blur(8px); }
  .round .mk-round-card .t { font-size: 4.6vmin; font-weight: 650; }
  .round .mk-round-card .t b { color: var(--accent); }
  .round .mk-round-card .m { font-size: 3.4vmin; color: var(--fg-muted); font-variant-numeric: tabular-nums; }
`;

let CSS_LINKS = [];
const pages = new Map();
const shell = (name, body, { palette = 'dark', round = false, css = '' } = {}) => {
  pages.set(`/__mock/${name}`, `<!doctype html><html lang="en" data-palette="${palette}"><head><meta charset="utf-8">
    ${CSS_LINKS.map((h) => `<link rel="stylesheet" href="${h}">`).join('')}<style>${MOCK_CSS}${css}</style></head>
    <body class="face"><div id="root"><div class="face-root ${round ? 'round' : ''}"><div class="face-canvas">${body}</div></div></div></body></html>`);
  return `${base}/__mock/${name}`;
};

// ---- the screens ------------------------------------------------------------------------------------
const SEL = FL.aircraft.find((a) => a.alt_ft >= 300); // nearest airborne aircraft
const header = (status, round = false) => `<div class="f-header"><div class="f-status">${status}</div><div class="face-time f-clock tnum">${NOW}</div></div>`;
const flightsStatus = (extra = '') => `<span class="dot dot-live"></span><span class="f-mode">Flights</span><span class="pct">${FL.aircraft.length} in range${extra}</span>`;

function screenRadar() {
  const svg = radar({ W: 800, H: 480, cx: 400, cy: 228, s: 6.2, rings: [10, 20, 30], selected: SEL.hex });
  const a = SEL;
  return shell('flights-radar', `
    ${svg}<div class="mk-scrim-top"></div><div class="mk-scrim-bottom"></div>
    <div class="face-screen mk-over">
      ${header(flightsStatus())}
      <div class="mk-spacer"></div>
      <div class="f-bar mk-glass">
        <div class="cell grow">${badge(a)}<div class="label"><div class="top">${esc(flightNo(a))} · ${esc(a.route?.airline || '')}</div><div class="bottom">${esc(routeText(a))} · ${esc(a.typeName)}</div></div></div>
        <div class="vsep"></div>
        <div class="cell"><div class="label"><div class="top num">${fmtAlt(a.alt_ft)}</div><div class="bottom">${arrow(a.rate_fpm)} ${trend(a.rate_fpm)}</div></div></div>
        <div class="vsep"></div>
        <div class="cell"><div class="label"><div class="top num">${dist(a.dist_km)} ${compass(a.bearing)}</div><div class="bottom">${kmh(a.gs_kt)}</div></div></div>
      </div>
    </div>`);
}

function screenOverhead() {
  const a = SEL;
  const scope = radar({ W: 212, H: 212, cx: 106, cy: 106, s: 106 / 13, rings: [5, 10], near: true, labels: false, selected: a.hex, glyph: 0.62, ringLabelBearing: 150 });
  const others = FL.aircraft.filter((x) => x.hex !== a.hex && x.route && x.alt_ft >= 300 && x.dist_km < 45).slice(0, 2);
  return shell('flights-overhead', `
    <div class="face-screen">
      ${header(flightsStatus())}
      <div class="f-body">
        <div class="f-art mk-scope">${scope}</div>
        <div class="f-info">
          <div class="f-eyebrow">Overhead now · ${dist(a.dist_km)} ${compass(a.bearing)}</div>
          <div class="f-title xl truncate">${esc(flightNo(a))}</div>
          <div class="f-sub truncate">${esc(routeText(a))}</div>
          <div class="f-meta truncate">${esc(a.route?.airline || '')}<span class="f-sep"></span>${esc(a.typeName)}<span class="f-sep"></span>${esc(a.reg || '')}</div>
          <hr class="f-hr" style="width: 30vmin; margin: 1.2vmin 0">
          <div class="f-dls">${trend(a.rate_fpm) === 'climbing' ? 'Climbing through' : trend(a.rate_fpm) === 'descending' ? 'Descending through' : 'Level at'} ${fmtAlt(a.alt_ft)} · ${kmh(a.gs_kt)}</div>
          <div class="f-tiny tnum">ADS-B<span class="f-sep"></span>1090 MHz<span class="f-sep"></span>${Math.round(a.elev_deg)}° above the horizon<span class="f-sep"></span>heading ${String(Math.round(a.track)).padStart(3, '0')}°</div>
        </div>
      </div>
      <div class="f-bar">
        <div class="cell grow">${ICON.plane}<div class="label"><div class="top">Also nearby</div><div class="bottom">${others.map((o) => `${esc(flightNo(o))} ${o.route.from === 'SYD' ? `to ${esc(o.route.toName)}` : `from ${esc(o.route.fromName)}`}`).join('  •  ')}</div></div></div>
        <div class="vsep"></div>
        <div class="cell">${ICON.alarm}<div class="label"><div class="top">Alarm</div><div class="bottom">Mon 06:30</div></div></div>
        <div class="vsep"></div>
        <div class="cell"><div class="vol">${ICON.volume}<span>43</span></div></div>
      </div>
    </div>`, { css: '.f-bar .cell.grow > .f-icon { color: var(--fg-muted); }' });
}

function screenRound() {
  const a = SEL;
  const svg = radar({ W: 480, H: 480, cx: 240, cy: 246, s: 6.2, rings: [10, 20, 30], selected: a.hex, clipCircle: [240, 240, 240], ringLabelBearing: 60 });
  return shell('flights-round', `
    ${svg}<div class="mk-scrim-top"></div><div class="mk-scrim-bottom"></div>
    <div class="face-screen mk-over">
      ${header(`<span class="dot dot-live"></span><span class="f-mode">Flights · ${FL.aircraft.length}</span>`, true)}
      <div class="mk-spacer"></div>
      <div class="mk-round-card"><div class="t"><b>${esc(flightNo(a))}</b> ${esc(routeText(a))}</div><div class="m">${fmtAlt(a.alt_ft)} ${arrow(a.rate_fpm)} · ${dist(a.dist_km)} ${compass(a.bearing)}</div></div>
    </div>`, { round: true });
}

// Sleep mode: night palette, small clock at a new spot every two minutes. Positions come from a Halton
// sequence (bases 2 and 3) inside a safe box, so over a night the clock visits the panel evenly instead of
// clustering the way plain random numbers do.
function halton(i, b) { let f = 1, r = 0; while (i > 0) { f /= b; r += f * (i % b); i = Math.floor(i / b); } return r; }
const SAFE = { x0: 120, x1: 680, y0: 70, y1: 420 };
const spot = (i) => ({ x: SAFE.x0 + halton(i + 1, 2) * (SAFE.x1 - SAFE.x0), y: SAFE.y0 + halton(i + 1, 3) * (SAFE.y1 - SAFE.y0) });
const sleepClock = (t, p, cls = '') => `<div class="mk-sleep-clock ${cls}" style="left:${p.x.toFixed(0)}px;top:${p.y.toFixed(0)}px"><div class="t">${t}</div><div class="a">${ICON.alarm}<span>06:30</span></div></div>`;

function screenSleep() { return shell('sleep-clock', `<div class="mk-sleep">${sleepClock('02:14', spot(6))}</div>`, { palette: 'night' }); }
function screenSleepSequence() {
  const times = ['02:08', '02:10', '02:12', '02:14'];
  const ps = times.map((_, i) => spot(i + 3));
  const path = ps.slice(1).map((p, i) => `<line x1="${ps[i].x}" y1="${ps[i].y}" x2="${p.x}" y2="${p.y}" stroke="#5b6170" stroke-width="1.2" stroke-dasharray="3 5"/>`).join('');
  return shell('sleep-sequence', `<div class="mk-sleep">
      <svg class="mk-path" viewBox="0 0 800 480">${path}<rect x="${SAFE.x0 - 70}" y="${SAFE.y0 - 40}" width="${SAFE.x1 - SAFE.x0 + 140}" height="${SAFE.y1 - SAFE.y0 + 80}" fill="none" stroke="#2c303a" stroke-dasharray="2 6"/></svg>
      ${times.slice(0, 3).map((t, i) => sleepClock(t, ps[i], 'ghost')).join('')}${sleepClock(times[3], ps[3])}
      <div class="mk-note" style="left:16px;bottom:12px">illustration · faded: where the clock was 2, 4 and 6 minutes ago · dashed box: area the clock moves within</div>
    </div>`, { palette: 'night' });
}
function screenSleepRound() {
  return shell('sleep-round', `<div class="mk-sleep" style="border-radius:50%">${sleepClock('02:14', { x: 205, y: 300 })}</div><div class="mk-panel-edge"></div>`, { palette: 'night', round: true });
}

// ---- live captures (real face, frozen, then annotated) -------------------------------------------------
async function freeze(page) {
  await page.evaluate(() => { const root = document.getElementById('root'); const c = root.cloneNode(true); c.id = 'frozen'; root.replaceWith(c); });
}

async function ambientWithFlights(ctx) {
  const page = await ctx.newPage();
  await page.goto(`${base}/face?at=${encodeURIComponent(AT)}&weather=clear-night&temp=16&tmin=11&tmax=21`, { waitUntil: 'networkidle' });
  await sleep(900);
  await freeze(page);
  // Sky placement: a window facing SSE with a 210° field of view; height above the horizon from the real
  // elevation angle on a square-root scale, so low aircraft on approach still clear the hills.
  const FACING = 150, FOV = 210, HORIZON = 316;
  const sky = (bearing, elev) => {
    let d = ((bearing - FACING + 540) % 360) - 180;
    if (Math.abs(d) > FOV / 2 || elev < 0.6) return null;
    return { x: 400 + (d / FOV) * 800, y: HORIZON - (HORIZON - 24) * Math.sqrt(Math.min(90, elev) / 90) };
  };
  const toSky = (x, y, altFt) => {
    const dkm = Math.hypot(x, y), b = (Math.atan2(x, y) * 180 / Math.PI + 360) % 360, e = Math.atan2(altFt * 0.3048 / 1000, dkm) * 180 / Math.PI;
    return sky(b, e);
  };
  // Only the flight the caption talks about gets a light: every aircraft in range would turn the sky into
  // a second radar (a plane passing nearly overhead sweeps right across the text).
  const lights = [SEL].map((a) => {
    const p = toSky(a.x, a.y, a.alt_ft);
    if (!p) return '';
    const tr = a.trail.slice(-3).map(([x, y, alt]) => toSky(x, y, typeof alt === 'number' ? alt : a.alt_ft)).filter(Boolean);
    const trail = tr.length ? `<polyline points="${[...tr, p].map((q) => `${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(' ')}" fill="none" stroke="#dfe7ff" stroke-opacity="0.22" stroke-width="1.3" stroke-linecap="round"/>` : '';
    const label = `<text x="${(p.x + 10).toFixed(1)}" y="${(p.y - 8).toFixed(1)}" font-size="11" font-weight="600" fill="#c9d1e0" fill-opacity="0.85" letter-spacing="0.04em">${esc(flightNo(a))}</text>`;
    return `${trail}<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="7" fill="#ffffff" fill-opacity="0.12"/><circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="1.8" fill="#ffffff"/><circle cx="${(p.x + 3.4).toFixed(1)}" cy="${(p.y + 1.7).toFixed(1)}" r="1.2" fill="#ff5a4f"/>${label}`;
  }).join('');
  const a = SEL;
  await page.evaluate(({ lights, line }) => {
    const svg = document.querySelector('#frozen svg.f-scene');
    const scrim = [...svg.querySelectorAll('rect')].find((r) => (r.getAttribute('fill') || '').includes('sc-scrim'));
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.innerHTML = lights;
    svg.insertBefore(g, scrim.nextSibling); // above the scrim: lights are the brightest thing in a night sky
    const next = document.querySelector('#frozen .f-next');
    next.insertAdjacentHTML('afterend', line);
  }, {
    lights,
    line: `<div class="f-next mk-flight">${ICON.plane}<span><b class="tnum">${esc(flightNo(a))}</b> ${esc(routeText(a))}</span><span class="f-meta ml-[1.6vmin]">${dist(a.dist_km)} ${compass(a.bearing)} · ${fmtAlt(a.alt_ft)}</span></div>`,
  });
  await page.addStyleTag({ content: MOCK_CSS });
  await sleep(300);
  await page.screenshot({ path: `${out}/flights-ambient.png` });
  await page.close();
}

async function liveStandby(ctx, name, query, css) {
  const page = await ctx.newPage();
  await page.goto(`${base}/face${query}`, { waitUntil: 'networkidle' });
  await sleep(900);
  await freeze(page);
  if (css) await page.addStyleTag({ content: css });
  await sleep(250);
  await page.screenshot({ path: `${out}/${name}.png` });
  await page.close();
}

// ---- boards ------------------------------------------------------------------------------------------------
async function board(ctx, name, files, cols = 2) {
  const page = await ctx.newPage();
  const rows = Math.ceil(files.length / cols);
  await page.setViewportSize({ width: cols * 820 + 20, height: rows * 500 + 20 });
  const tiles = files.map((f) => `<img src="data:image/png;base64,${readFileSync(`${out}/${f}.png`).toString('base64')}" style="width:800px;height:480px;border-radius:18px;border:1px solid #1d2230;display:block">`).join('');
  await page.setContent(`<body style="margin:0;background:#05070a;display:grid;grid-template-columns:repeat(${cols}, 800px);gap:20px;padding:20px">${tiles}</body>`);
  await sleep(300);
  await page.screenshot({ path: `${out}/${name}.png` });
  await page.close();
}

// ---- main ------------------------------------------------------------------------------------------------------
const faceHtml = await (await fetch(`${base}/face`)).text();
CSS_LINKS = [...faceHtml.matchAll(/<link rel="stylesheet"[^>]*href="([^"]+)"/g)].map((m) => m[1]);
if (!CSS_LINKS.length) throw new Error('no face stylesheet found; is the simulator running (make sim)?');

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 800, height: 480 }, deviceScaleFactor: 1 });
await ctx.route(`${base}/__mock/**`, (route) => route.fulfill({ contentType: 'text/html; charset=utf-8', body: pages.get(new URL(route.request().url()).pathname) }));

const shots = { 'flights-radar': screenRadar(), 'flights-overhead': screenOverhead(), 'flights-round': screenRound(), 'sleep-clock': screenSleep(), 'sleep-sequence': screenSleepSequence(), 'sleep-round': screenSleepRound() };
for (const [name, url] of Object.entries(shots)) {
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await sleep(250);
  await page.screenshot({ path: `${out}/${name}.png` });
  await page.close();
}

await call('POST', `${hub}/set`, { lux: 150, gps_fix: true, dab_sync: true, sdr_present: true, network_online: true });
await call('PUT', `${base}/api/display/mode`, { mode: 'manual' });
await call('PUT', `${base}/api/display/brightness`, { value: 100 });
await call('POST', `${base}/api/audio/standby`);
await sleep(600);
await ambientWithFlights(ctx);
// Burn-in: the status strip faded out after a few idle minutes (touch brings it back).
await liveStandby(ctx, 'burnin-strip-hidden', `?at=${encodeURIComponent('2026-10-03T14:17:00+10:00')}&weather=partly-day&temp=21&tmin=11&tmax=21`, '.f-ambient .f-bar { opacity: 0; }');
await call('POST', `${hub}/set`, { lux: 0.5 });
await sleep(3500);
await liveStandby(ctx, 'burnin-night-today', `?at=${encodeURIComponent('2026-10-03T02:14:00+10:00')}&weather=clear-night&temp=12&tmin=11&tmax=21`);
await call('POST', `${hub}/set`, { lux: 150 });

// Night frames of the sleep mode for the burn-in study (positions advance every 2 minutes; one frame per 20).
if (framesOut) {
  mkdirSync(framesOut, { recursive: true });
  for (let m = 22 * 60; m < 30 * 60; m += 20) {
    const mm = m % (24 * 60), hh = String(Math.floor(mm / 60)).padStart(2, '0'), mi = String(mm % 60).padStart(2, '0');
    const url = shell(`sleep-${hh}${mi}`, `<div class="mk-sleep">${sleepClock(`${hh}:${mi}`, spot(Math.floor((m - 22 * 60) / 2)))}</div>`, { palette: 'night' });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'networkidle' });
    await sleep(120);
    await page.screenshot({ path: `${framesOut}/${hh}${mi}.png` });
    await page.close();
  }
}

await board(ctx, 'flights-board', ['flights-radar', 'flights-overhead', 'flights-ambient', 'flights-round']);
await board(ctx, 'sleep-board', ['burnin-night-today', 'sleep-clock', 'sleep-sequence', 'sleep-round']);
await browser.close();
console.log('mockups written to', out);
