// Procedural ambient scene: sky, sun/moon, stars, clouds, precipitation, hills and trees composed from the
// time of day (relative to sunrise/sunset), the forecast for the hour coming up and the season. Pure SVG + CSS,
// no bitmaps, so it costs nothing to ship and renders the same on every panel. Legibility comes from a scrim
// over the top and bottom thirds; the clock text adds its own shadow.
//
// It is a stack of layers so that what moves is only ever a composited transform (cheap on a Pi): the sky,
// the hills and the scrim are still SVGs; the clouds drift sideways across a seamless strip twice the screen's
// width, rain/snow/hail fall down a strip twice its height (tilted by the wind), and a storm flashes now and then.
// Burn-in: the clouds never stop, the moon and sun cross the sky, the hills drift a few pixels over the day and
// are redrawn from the date, and the stars are re-seeded daily.
import { memo, useMemo } from 'react';
import { dayNumber } from '../burnin';
import type { SceneWeather } from '../forecast';

export type Kind = 'clear' | 'partly' | 'cloudy' | 'overcast' | 'fog' | 'drizzle' | 'rain' | 'storm' | 'hail' | 'snow';
type Season = 'summer' | 'autumn' | 'winter' | 'spring';

export interface SceneProps {
  now: Date; tz: string; wx: SceneWeather | null;
  sunrise: string | null; sunset: string | null; latitude: number; lowCpu: boolean;
  /** burn-in: draw the hills, trees and stars from today's date so their outline is not the same edge every day */
  daily?: boolean;
}

const W = 800, H = 480;

// ---- helpers ---------------------------------------------------------------
const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a: number, b: number, x: number) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };
function hex(c: string): [number, number, number] { const n = parseInt(c.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hex(a), [r2, g2, b2] = hex(b); t = clamp(t);
  const f = (x: number, y: number) => Math.round(x + (y - x) * t).toString(16).padStart(2, '0');
  return `#${f(r1, r2)}${f(g1, g2)}${f(b1, b2)}`;
}
function minutesOfDay(d: Date, tz: string): number {
  const p = new Intl.DateTimeFormat('en-AU', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d);
  const g = (t: string) => parseInt(p.find((x) => x.type === t)?.value ?? '0', 10);
  return (g('hour') % 24) * 60 + g('minute');
}
function monthOf(d: Date, tz: string): number {
  return parseInt(new Intl.DateTimeFormat('en-AU', { timeZone: tz, month: 'numeric' }).format(d), 10);
}
// "2026-04-14T05:32" or ISO with offset -> minutes of day (wall-clock part only; the feed is already local)
function sunMinutes(iso: string | null, fallback: number): number {
  const m = iso?.match(/T(\d{2}):(\d{2})/);
  return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : fallback;
}
// deterministic pseudo-random in [0,1)
function rnd(seed: number): number { const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

/** Moon phase in [0,1): 0 new, 0.5 full (mean synodic month from a known new moon). */
export function moonPhase(d: Date): number {
  const days = (d.getTime() - Date.UTC(2000, 0, 6, 18, 14)) / 86400000;
  const p = (days / 29.530588853) % 1;
  return p < 0 ? p + 1 : p;
}

/** What to draw for a weather icon id, refined by the hour's cloud cover and rain. */
export function kindOf(wx: SceneWeather | null): Kind {
  const icon = wx?.icon;
  if (!icon) return 'clear';
  if (icon.startsWith('clear')) return (wx.cloud ?? 0) >= 25 ? 'partly' : 'clear';
  if (icon.startsWith('partly')) return (wx.cloud ?? 0) >= 65 ? 'cloudy' : 'partly';
  if (icon === 'cloudy') return 'overcast';
  if (icon === 'fog') return 'fog';
  if (icon === 'snow') return 'snow';
  if (icon === 'drizzle') return 'drizzle';
  if (icon === 'thunder') return 'storm';
  if (icon === 'hail') return 'hail';
  return 'rain';
}
export function seasonOf(month: number, latitude: number): Season {
  // northern calendar: Dec-Feb winter, Mar-May spring, Jun-Aug summer, Sep-Nov autumn; shift six months south of the equator
  const m = latitude < 0 ? ((month + 5) % 12) + 1 : month;
  if (m === 12 || m <= 2) return 'winter';
  if (m <= 5) return 'spring';
  if (m <= 8) return 'summer';
  return 'autumn';
}

// ---- palette ---------------------------------------------------------------
const SKY = {
  night: { top: '#03050d', mid: '#0a1226', hor: '#17233d' },
  day: { top: '#2b6cb3', mid: '#79afdc', hor: '#d7e8f3' },
  grey: { top: '#4b5968', mid: '#8491a0', hor: '#b7c0c9' },
  greyNight: { top: '#070a10', mid: '#121821', hor: '#1f2731' },
  storm: { top: '#262a3a', mid: '#3d4356', hor: '#5d6474' },
};
// far, middle, near hills
const HILL_NORTH: Record<Season, [string, string, string]> = {
  summer: ['#5f9d64', '#3a7a44', '#245533'],
  autumn: ['#c1773a', '#9a5226', '#5d3118'],
  winter: ['#9fb0c2', '#6f8296', '#48596b'],
  spring: ['#8fbf6a', '#5f9a4e', '#3c6b35'],
};
// southern (Australian) seasons: summer grass cures to straw, winter rain greens the paddocks,
// autumn is gum-green with the exotic street trees turning, spring is lush with wattle and blossom
const HILL_SOUTH: Record<Season, [string, string, string]> = {
  summer: ['#c9b06a', '#a68a45', '#6b5a2c'],
  autumn: ['#a3ab6c', '#7e8749', '#4f5631'],
  winter: ['#8fa483', '#64805f', '#3e5640'],
  spring: ['#9cc46e', '#6aa253', '#3f7136'],
};
// colour dabs on some of the trees
const ACCENT: Record<'north' | 'south', Partial<Record<Season, string[]>>> = {
  north: { spring: ['#f6b7c9'], autumn: ['#d9682b', '#e3a33a'] },
  south: { spring: ['#f2c94c', '#f6b7c9'], autumn: ['#c4562d', '#d9902f', '#e2b844'] },
};

// precipitation per kind: count per screen, streak length, fall time per screen height (s)
const PRECIP: Partial<Record<Kind, { n: number; len: [number, number]; s: number; alpha: number }>> = {
  drizzle: { n: 40, len: [5, 9], s: 1.5, alpha: 0.28 },
  rain: { n: 60, len: [12, 24], s: 0.8, alpha: 0.38 },
  storm: { n: 90, len: [16, 30], s: 0.6, alpha: 0.45 },
  hail: { n: 45, len: [12, 22], s: 0.6, alpha: 0.35 },
};

export const Scene = memo(function Scene({ now, tz, wx, sunrise, sunset, latitude, lowCpu, daily = false }: SceneProps) {
  const day = daily ? dayNumber(now, tz) % 9973 : 0;
  const icon = wx?.icon ?? null, temp = wx?.temperature ?? null, cloudPct = wx?.cloud ?? null, wind = wx?.wind ?? null;
  const gusts = wx?.gusts ?? null, rainMm = wx?.precip ?? null, vis = wx?.visibility ?? null;
  const minute = Math.floor(now.getTime() / 60000);
  const model = useMemo(() => {
    const t = minutesOfDay(now, tz);
    const rise = sunMinutes(sunrise, 6 * 60 + 15), set = sunMinutes(sunset, 18 * 60);
    const dayness = smooth(rise - 35, rise + 40, t) * (1 - smooth(set - 40, set + 35, t));
    const dawn = Math.exp(-(((t - rise) / 45) ** 2)), dusk = Math.exp(-(((t - set) / 45) ** 2));
    const twilight = clamp(dawn + dusk);
    const kind = kindOf(wx);
    const south = latitude < 0;
    const season = seasonOf(monthOf(now, tz), latitude);
    const precip = kind === 'drizzle' || kind === 'rain' || kind === 'storm' || kind === 'hail' || kind === 'snow';
    const snowy = kind === 'snow';
    // frost: a still, freezing hour with nothing falling (a Canberra winter morning)
    const frost = !precip && temp !== null && temp <= 1 ? clamp((1.5 - temp) / 4) : 0;
    const hot = temp !== null && temp >= 32 && (kind === 'clear' || kind === 'partly') ? clamp((temp - 31) / 8) : 0;
    // haze/smoke: poor visibility without fog or rain to explain it
    const haze = vis !== null && kind !== 'fog' && !precip ? clamp((12000 - vis) / 9000) : 0;
    const windy = clamp(Math.max(((wind ?? 0) - 15) / 40, ((gusts ?? 0) - 35) / 50));

    // sky: night <-> day, pulled towards grey by cloud cover, warmed near the horizon at twilight
    const base = (k: 'top' | 'mid' | 'hor') => mix(SKY.night[k], SKY.day[k], dayness);
    const grey = (k: 'top' | 'mid' | 'hor') => {
      const g = mix(SKY.greyNight[k], SKY.grey[k], dayness);
      return kind === 'storm' ? mix(g, mix(SKY.greyNight[k], SKY.storm[k], dayness), 0.8) : g;
    };
    const coverOf: Record<Kind, number> = { clear: 0, partly: 0.12 + clamp((cloudPct ?? 30) / 100) * 0.2, cloudy: 0.45, overcast: 0.75, fog: 0.85, drizzle: 0.7, rain: 0.8, storm: 0.92, hail: 0.88, snow: 0.75 };
    const cover = coverOf[kind];
    let top = mix(base('top'), grey('top'), cover), mid = mix(base('mid'), grey('mid'), cover), hor = mix(base('hor'), grey('hor'), cover);
    if (hot) { top = mix(top, '#5f8fc4', hot * 0.5 * dayness); mid = mix(mid, '#cfe0ea', hot * 0.4 * dayness); hor = mix(hor, '#f3e3c0', hot * 0.6 * dayness); }
    if (haze) { top = mix(top, mix('#1d1a18', '#8f8a80', dayness), haze * 0.55); mid = mix(mid, mix('#2a2420', '#c4a983', dayness), haze * 0.65); hor = mix(hor, mix('#3a2c22', '#d9b27c', dayness), haze * 0.75); }
    const warm = twilight * (1 - cover * 0.6);
    hor = mix(hor, dusk > dawn ? '#f0875a' : '#f6a35f', warm * 0.9);
    mid = mix(mid, dusk > dawn ? '#b85574' : '#d98a7a', warm * 0.55);
    top = mix(top, '#3b2d5c', warm * 0.35);

    // sun: an arc from sunrise to sunset
    const prog = (t - rise) / Math.max(60, set - rise); // 0 at sunrise, 1 at sunset
    const sunX = 80 + prog * (W - 160), sunY = 300 - Math.sin(clamp(prog) * Math.PI) * 230;
    const sunVisible = dayness > 0.02 && prog > -0.08 && prog < 1.08;
    // moon: an arc from sunset to sunrise, in today's phase (lit on the right while waxing up north, the left down south)
    const nightLen = Math.max(60, rise + 1440 - set);
    const nprog = (((t - set) % 1440) + 1440) % 1440 / nightLen;
    const moonX = 120 + clamp(nprog) * (W - 240), moonY = 250 - Math.sin(clamp(nprog) * Math.PI) * 170;
    const phase = moonPhase(now);
    const moonAlpha = (1 - dayness) * (1 - cover * 0.9) * (1 - haze * 0.6);
    const starAlpha = (1 - dayness) * (1 - cover) * (1 - haze);

    // hills: season colours, darkened at night and hazed into the sky with distance; snow and frost whiten them
    let palette: string[] = snowy ? ['#dfe6ee', '#b9c5d2', '#8694a4'] : (south ? HILL_SOUTH : HILL_NORTH)[season];
    if (frost && !snowy) palette = palette.map((c, i) => mix(c, '#e3ecf4', frost * [0.7, 0.75, 0.6][i]));
    if (hot && !snowy) palette = palette.map((c) => mix(c, '#d8c48a', hot * 0.35));
    const nightMix = (1 - dayness) * 0.82;
    const hills = palette.map((c, i) => mix(mix(c, hor, [0.45, 0.22, 0][i] + haze * 0.25), '#05070e', nightMix + (i === 2 ? 0.08 : 0)));
    const accents = (snowy ? [] : ACCENT[south ? 'south' : 'north'][season] ?? []).map((c) => mix(c, '#05070e', nightMix));

    return { t, dayness, twilight, kind, season, south, snowy, frost, hot, haze, windy, precip, cover, top, mid, hor, sunX, sunY, sunVisible, moonX, moonY, phase, moonAlpha, starAlpha, hills, accents };
  }, [minute, tz, icon, temp, cloudPct, wind, gusts, rainMm, vis, sunrise, sunset, latitude]); // eslint-disable-line react-hooks/exhaustive-deps

  const m = model;
  const d0 = day * 1013; // offsets every seed by the day (0 when the daily scene is off)
  const stars = useMemo(() => Array.from({ length: 90 }, (_, i) => ({ x: rnd(i + d0) * W, y: rnd(i + 100 + d0) * 260, r: 0.5 + rnd(i + 200) * 1.3, o: 0.35 + rnd(i + 300) * 0.65 })), [d0]);

  // clouds: a strip two screens wide that repeats every screen, drifting left (faster in the wind)
  const cloudCount = ({ clear: 0, partly: 2 + Math.round(clamp((cloudPct ?? 35) / 100) * 3), cloudy: 6, overcast: 9, fog: 3, drizzle: 8, rain: 9, storm: 10, hail: 9, snow: 8 } as Record<Kind, number>)[m.kind];
  const heavy = m.kind !== 'clear' && m.kind !== 'partly';
  const clouds = useMemo(() => Array.from({ length: cloudCount }, (_, i) => ({
    x: (i / Math.max(1, cloudCount)) * W + rnd(i + 11 + d0) * 90, y: 55 + rnd(i + 23 + d0) * (heavy ? 150 : 110), s: 0.8 + rnd(i + 37 + d0) * 0.9,
  })), [cloudCount, heavy, d0]);
  const cloudFill = m.kind === 'storm' ? mix('#11131b', '#4a4f5f', m.dayness)
    : heavy ? mix(mix('#1b2330', '#8e9aa8', m.dayness), m.hor, 0.2)
    : mix(mix('#2a3346', '#ffffff', m.dayness), '#f7b48c', m.twilight * 0.5);
  const cloudAlpha = m.kind === 'fog' ? 0.55 : heavy ? 0.92 : 0.85;
  const cloudSecs = Math.round(420 - m.windy * 340); // one screen width per 7 min when still, ~80 s in a gale

  // precipitation: a tile twice the screen each way (so it still covers the corners when the wind tilts it),
  // in a strip two tiles tall that falls one tile per cycle
  const spec = m.kind === 'snow' ? null : PRECIP[m.kind];
  const amount = clamp(rainMm === null ? 1 : 0.6 + rainMm / 6, 0.5, 1.7);
  const nDrops = spec ? Math.round(4 * spec.n * (m.kind === 'drizzle' ? 1 : amount)) : 0;
  const drops = useMemo(() => Array.from({ length: nDrops }, (_, i) => ({
    x: rnd(i + 500) * W * 2, y: rnd(i + 600) * H * 2, l: spec ? spec.len[0] + rnd(i + 700) * (spec.len[1] - spec.len[0]) : 0,
  })), [nDrops, spec]);
  const nFlakes = m.kind === 'snow' ? 280 : m.kind === 'hail' ? 160 : 0;
  const flakes = useMemo(() => Array.from({ length: nFlakes }, (_, i) => ({ x: rnd(i + 1500) * W * 2, y: rnd(i + 1600) * H * 2, r: (m.kind === 'hail' ? 1.2 : 1.4) + rnd(i + 1700) * (m.kind === 'hail' ? 1.6 : 2.2) })), [nFlakes, m.kind]);
  const tilt = (m.kind === 'snow' ? 8 + 30 * m.windy : 4 + 26 * m.windy) * (rnd(day + 3) < 0.5 ? 1 : -1);
  const fallSecs = 2 * (m.kind === 'snow' ? 9 - 4 * m.windy : spec ? spec.s : 1); // a tile is two screens tall

  // hills and trees: drawn a little wider than the screen and drifted a few pixels over the day (burn-in)
  const pines = m.snowy || (!m.south && m.season === 'winter');
  const trees = useMemo(() => Array.from({ length: 12 }, (_, i) => ({ x: -30 + i * 76 + rnd(i + 900 + d0) * 40, h: 22 + rnd(i + 950 + d0) * 26 })), [d0]);
  const hillPath = (y: number, amp: number, seed: number) => {
    let d = `M-80 ${H} L-80 ${y}`;
    for (let x = -80; x <= W + 80; x += 80) d += ` Q${x + 40} ${y - amp * (rnd(seed + x + d0) - 0.4) * 2} ${x + 80} ${y + amp * (rnd(seed + x + 7 + d0) - 0.5)}`;
    return d + ` L${W + 80} ${H} Z`;
  };
  const hillY = [322, 354, 386];
  const panX = Math.round(16 * Math.sin((2 * Math.PI * m.t) / 480)), panY = Math.round(4 * Math.sin((2 * Math.PI * m.t) / 330 + 1));
  const sunColor = m.haze > 0.3 ? mix('#ffb36b', '#e0532e', m.haze) : mix('#ffd27a', '#ff8d4d', m.twilight);

  // moon: lit limb plus a terminator ellipse; mirrored for the other hemisphere / waning
  const mr = 20, f = (1 - Math.cos(2 * Math.PI * m.phase)) / 2;
  const litRight = (m.phase < 0.5) !== m.south;
  const moonLit = `M0 ${-mr} A${mr} ${mr} 0 0 1 0 ${mr} A${(mr * Math.abs(1 - 2 * f)).toFixed(2)} ${mr} 0 0 ${f < 0.5 ? 0 : 1} 0 ${-mr} Z`;

  const boltX = 160 + rnd(Math.floor(m.t / 60) + d0) * 480;

  return (
    <div className={`f-scene ${lowCpu ? 'static' : ''}`} aria-hidden="true" data-kind={m.kind} data-season={m.season} data-hour={wx?.at ?? undefined}>
      <svg className="sc-layer" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="sc-sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={m.top} /><stop offset="0.55" stopColor={m.mid} /><stop offset="0.8" stopColor={m.hor} />
          </linearGradient>
          <radialGradient id="sc-sun"><stop offset="0" stopColor={sunColor} stopOpacity="0.95" /><stop offset="0.35" stopColor={sunColor} stopOpacity={0.55 + m.hot * 0.2} /><stop offset="1" stopColor={sunColor} stopOpacity="0" /></radialGradient>
          <radialGradient id="sc-moon"><stop offset="0" stopColor="#f3f1e6" stopOpacity={0.15 + 0.4 * f} /><stop offset="1" stopColor="#f3f1e6" stopOpacity="0" /></radialGradient>
        </defs>
        <rect width={W} height={H} fill="url(#sc-sky)" />
        {m.starAlpha > 0.02 && <g opacity={m.starAlpha}>{stars.map((s, i) => <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#e9eefc" opacity={s.o} />)}</g>}
        {m.moonAlpha > 0.02 && f > 0.02 && (
          <g opacity={m.moonAlpha} transform={`translate(${m.moonX.toFixed(1)} ${m.moonY.toFixed(1)})`}>
            <circle r={70 + 30 * f} fill="url(#sc-moon)" />
            <circle r={mr} fill="#efeadb" opacity={0.08} />
            <path d={moonLit} fill="#efeadb" transform={litRight ? undefined : 'scale(-1 1)'} />
          </g>
        )}
        {m.sunVisible && (
          <g opacity={clamp(m.dayness * 1.4) * (1 - m.cover * 0.8)}>
            <circle cx={m.sunX} cy={m.sunY} r={150 + 40 * m.hot} fill="url(#sc-sun)" />
            <circle cx={m.sunX} cy={m.sunY} r={24} fill={mix('#fff4cf', sunColor, Math.max(m.twilight * 0.8, m.haze))} />
          </g>
        )}
      </svg>

      {cloudCount > 0 && (
        <div className="sc-drift" style={{ ['--sc-secs' as string]: `${cloudSecs}s` }}>
          <svg viewBox={`0 0 ${W * 2} ${H}`} preserveAspectRatio="none">
            {!lowCpu && <defs><filter id="sc-blur" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="9" /></filter></defs>}
            <g opacity={cloudAlpha} filter={lowCpu ? undefined : 'url(#sc-blur)'} fill={cloudFill}>
              {[-W, 0, W].flatMap((off) => clouds.map((c, i) => (
                <g key={`${off}-${i}`} transform={`translate(${c.x + off} ${c.y}) scale(${c.s})`}>
                  <ellipse cx={0} cy={0} rx={110} ry={28} /><ellipse cx={-45} cy={-14} rx={55} ry={30} /><ellipse cx={30} cy={-20} rx={65} ry={36} /><ellipse cx={75} cy={-4} rx={50} ry={26} />
                </g>
              )))}
            </g>
          </svg>
        </div>
      )}

      <svg className="sc-layer" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="sc-fog" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={m.hor} stopOpacity="0" /><stop offset="0.5" stopColor={m.hor} stopOpacity="0.75" /><stop offset="1" stopColor={m.hor} stopOpacity="0.9" /></linearGradient>
          <linearGradient id="sc-haze" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={m.hor} stopOpacity="0" /><stop offset="1" stopColor={m.hor} stopOpacity="0.6" /></linearGradient>
          {!lowCpu && <filter id="sc-soft" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="3" /></filter>}
        </defs>
        {m.kind === 'fog' && <rect x={0} y={150} width={W} height={H - 150} fill="url(#sc-fog)" />}
        <g transform={`translate(${panX} ${panY})`}>
          <path d={hillPath(hillY[0], 26, 1)} fill={m.hills[0]} filter={lowCpu ? undefined : 'url(#sc-soft)'} />
          {(m.haze > 0.1 || m.hot > 0.1) && <rect x={-80} y={230} width={W + 160} height={140} fill="url(#sc-haze)" opacity={Math.max(m.haze, m.hot * 0.6)} />}
          <path d={hillPath(hillY[1], 22, 2)} fill={m.hills[1]} />
          <g fill={m.hills[2]}>
            {trees.map((tr, i) => pines
              ? <path key={i} d={`M${tr.x} ${hillY[2] - tr.h * 0.9} l${-tr.h * 0.36} ${tr.h} h${tr.h * 0.72} z`} />
              : <g key={i}><circle cx={tr.x} cy={hillY[2] + 2} r={tr.h * 0.5} /><circle cx={tr.x - tr.h * 0.42} cy={hillY[2] + 8} r={tr.h * 0.36} /><circle cx={tr.x + tr.h * 0.4} cy={hillY[2] + 7} r={tr.h * 0.4} /></g>)}
          </g>
          {m.accents.length > 0 && m.dayness > 0.1 && (
            <g opacity={0.5 * m.dayness}>{trees.filter((_, i) => i % 2 === 0).map((tr, i) => <circle key={i} cx={tr.x + (i % 2 ? -3 : 3)} cy={hillY[2] - 1} r={tr.h * 0.42} fill={m.accents[i % m.accents.length]} />)}</g>
          )}
          {m.snowy && <g fill="#eef3f8" opacity={0.8}>{trees.map((tr, i) => pines ? <path key={i} d={`M${tr.x} ${hillY[2] - tr.h * 0.9} l${-tr.h * 0.14} ${tr.h * 0.38} h${tr.h * 0.28} z`} /> : <ellipse key={i} cx={tr.x} cy={hillY[2] - tr.h * 0.38} rx={tr.h * 0.38} ry={tr.h * 0.14} />)}</g>}
          <path d={hillPath(hillY[2], 18, 3)} fill={m.hills[2]} />
        </g>
      </svg>

      {(nDrops > 0 || nFlakes > 0) && (
        <div className="sc-tilt" style={{ transform: `rotate(${tilt.toFixed(1)}deg)` }}>
          <div className={`sc-fall ${m.kind === 'snow' ? 'snow' : ''}`} style={{ ['--sc-secs' as string]: `${fallSecs.toFixed(2)}s` }}>
            <svg viewBox={`0 0 ${W * 2} ${H * 4}`} preserveAspectRatio="none">
              {nDrops > 0 && spec && (
                <g stroke="#dfe8f2" strokeOpacity={spec.alpha + 0.2 * m.dayness} strokeWidth={m.kind === 'drizzle' ? 1 : 1.3} strokeLinecap="round">
                  {[0, H * 2].flatMap((off) => drops.map((d, i) => <line key={`${off}-${i}`} x1={d.x} y1={d.y + off} x2={d.x} y2={d.y + off + d.l} />))}
                </g>
              )}
              {nFlakes > 0 && (
                <g fill="#ffffff" fillOpacity={m.kind === 'hail' ? 0.75 : 0.85}>
                  {[0, H * 2].flatMap((off) => flakes.map((d, i) => <circle key={`${off}-${i}`} cx={d.x} cy={d.y + off} r={d.r} />))}
                </g>
              )}
            </svg>
          </div>
        </div>
      )}

      {m.kind === 'storm' && !lowCpu && (
        <div className="sc-flash">
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice">
            <rect width={W} height={H} fill="#e8ecff" opacity={0.35} />
            <path d={`M${boltX} 90 l-22 70 h16 l-26 80 l46 -96 h-18 l20 -54 z`} fill="#f6f7ff" />
          </svg>
        </div>
      )}

      <svg className="sc-layer" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="sc-scrim" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#05070c" stopOpacity="0.5" /><stop offset="0.4" stopColor="#05070c" stopOpacity="0.14" /><stop offset="0.62" stopColor="#05070c" stopOpacity="0.34" /><stop offset="0.8" stopColor="#05070c" stopOpacity="0.5" /><stop offset="1" stopColor="#05070c" stopOpacity="0.78" />
          </linearGradient>
        </defs>
        <rect width={W} height={H} fill="url(#sc-scrim)" />
      </svg>
    </div>
  );
});
