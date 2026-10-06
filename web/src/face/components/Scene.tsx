// Procedural ambient scene: sky, sun/moon, stars, clouds, precipitation, hills and trees composed from the
// time of day (relative to sunrise/sunset), the current weather icon and the season. Pure SVG + CSS, no
// bitmaps, so it costs nothing to ship and renders the same on every panel. Legibility comes from a scrim
// over the top and bottom thirds; the clock text adds its own shadow.
import { useMemo } from 'react';
import { dayNumber } from '../burnin';

type Kind = 'clear' | 'partly' | 'overcast' | 'fog' | 'rain' | 'snow';
type Season = 'summer' | 'autumn' | 'winter' | 'spring';

export interface SceneProps {
  now: Date; tz: string; icon: string | null; temperature: number | null;
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

function kindOf(icon: string | null): Kind {
  if (!icon) return 'clear';
  if (icon.startsWith('clear')) return 'clear';
  if (icon.startsWith('partly')) return 'partly';
  if (icon === 'cloudy') return 'overcast';
  if (icon === 'fog') return 'fog';
  if (icon === 'snow') return 'snow';
  return 'rain'; // drizzle, rain, thunder, hail
}
function seasonOf(month: number, latitude: number): Season {
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
};
const HILL: Record<Season, [string, string, string]> = {
  summer: ['#5f9d64', '#3a7a44', '#245533'],
  autumn: ['#c1773a', '#9a5226', '#5d3118'],
  winter: ['#9fb0c2', '#6f8296', '#48596b'],
  spring: ['#8fbf6a', '#5f9a4e', '#3c6b35'],
};

export function Scene({ now, tz, icon, temperature, sunrise, sunset, latitude, lowCpu, daily = false }: SceneProps) {
  const day = daily ? dayNumber(now, tz) % 9973 : 0;
  const model = useMemo(() => {
    const t = minutesOfDay(now, tz);
    const rise = sunMinutes(sunrise, 6 * 60 + 15), set = sunMinutes(sunset, 18 * 60);
    const dayness = smooth(rise - 35, rise + 40, t) * (1 - smooth(set - 40, set + 35, t));
    const dawn = Math.exp(-(((t - rise) / 45) ** 2)), dusk = Math.exp(-(((t - set) / 45) ** 2));
    const twilight = clamp(dawn + dusk);
    const kind = kindOf(icon);
    const season = seasonOf(monthOf(now, tz), latitude);
    const snowy = kind === 'snow' || (temperature !== null && temperature <= 2 && season === 'winter');
    const overcast = kind === 'overcast' || kind === 'rain' || kind === 'snow' || kind === 'fog';

    // sky: night <-> day, pulled towards grey by cloud cover, warmed near the horizon at twilight
    const base = (k: 'top' | 'mid' | 'hor') => mix(SKY.night[k], SKY.day[k], dayness);
    const grey = (k: 'top' | 'mid' | 'hor') => mix(SKY.greyNight[k], SKY.grey[k], dayness);
    const cover = overcast ? (kind === 'fog' ? 0.85 : 0.75) : kind === 'partly' ? 0.2 : 0;
    let top = mix(base('top'), grey('top'), cover), mid = mix(base('mid'), grey('mid'), cover), hor = mix(base('hor'), grey('hor'), cover);
    const warm = twilight * (1 - cover * 0.6);
    hor = mix(hor, dusk > dawn ? '#f0875a' : '#f6a35f', warm * 0.9);
    mid = mix(mid, dusk > dawn ? '#b85574' : '#d98a7a', warm * 0.55);
    top = mix(top, '#3b2d5c', warm * 0.35);

    // sun / moon
    const prog = (t - rise) / Math.max(60, set - rise); // 0 at sunrise, 1 at sunset
    const sunX = 80 + prog * (W - 160), sunY = 300 - Math.sin(clamp(prog) * Math.PI) * 230;
    const sunVisible = dayness > 0.02 && prog > -0.08 && prog < 1.08;
    const moonAlpha = (1 - dayness) * (1 - cover * 0.9);
    const starAlpha = (1 - dayness) * (1 - cover);

    // hills: season colours, darkened at night and hazed into the sky with distance; snow overrides
    const palette = snowy ? ['#dfe6ee', '#b9c5d2', '#8694a4'] : HILL[season];
    const nightMix = (1 - dayness) * 0.82;
    const hills = palette.map((c, i) => mix(mix(c, hor, [0.45, 0.22, 0][i]), '#05070e', nightMix + (i === 2 ? 0.08 : 0)));

    return { t, dayness, twilight, kind, season, snowy, overcast, cover, top, mid, hor, sunX, sunY, sunVisible, moonAlpha, starAlpha, hills };
  }, [now.getTime() - (now.getTime() % 60000), tz, icon, temperature, sunrise, sunset, latitude]); // eslint-disable-line react-hooks/exhaustive-deps

  const m = model;
  const d0 = day * 1013; // offsets every seed by the day (0 when the daily scene is off)
  const stars = useMemo(() => Array.from({ length: 90 }, (_, i) => ({ x: rnd(i + d0) * W, y: rnd(i + 100 + d0) * 260, r: 0.5 + rnd(i + 200) * 1.3, o: 0.35 + rnd(i + 300) * 0.65 })), [d0]);
  const cloudCount = m.kind === 'clear' ? 0 : m.kind === 'partly' ? 3 : m.kind === 'fog' ? 0 : 8;
  const clouds = useMemo(() => Array.from({ length: cloudCount }, (_, i) => ({
    x: (i / Math.max(1, cloudCount)) * W + rnd(i + 11 + d0) * 90, y: 55 + rnd(i + 23 + d0) * (m.overcast ? 150 : 110), s: 0.8 + rnd(i + 37 + d0) * 0.9,
  })), [cloudCount, m.overcast, d0]);
  const cloudFill = m.overcast ? mix(mix('#1b2330', '#8e9aa8', m.dayness), m.hor, 0.2) : mix(mix('#2a3346', '#ffffff', m.dayness), '#f7b48c', m.twilight * 0.5);
  const cloudAlpha = m.overcast ? 0.92 : 0.85;
  const drops = useMemo(() => Array.from({ length: m.kind === 'rain' ? 60 : m.kind === 'snow' ? 70 : 0 }, (_, i) => ({ x: rnd(i + 500) * W, y: rnd(i + 600) * H, l: 10 + rnd(i + 700) * 14, r: 1.2 + rnd(i + 800) * 2 })), [m.kind]);
  const pines = m.season === 'winter' || m.snowy;
  const trees = useMemo(() => Array.from({ length: 11 }, (_, i) => ({ x: 10 + i * 76 + rnd(i + 900 + d0) * 40, h: 22 + rnd(i + 950 + d0) * 26 })), [d0]);
  const hillPath = (y: number, amp: number, seed: number) => {
    let d = `M0 ${H} L0 ${y}`;
    for (let x = 0; x <= W; x += 80) d += ` Q${x + 40} ${y - amp * (rnd(seed + x + d0) - 0.4) * 2} ${x + 80} ${y + amp * (rnd(seed + x + 7 + d0) - 0.5)}`;
    return d + ` L${W} ${H} Z`;
  };
  const hillY = [322, 354, 386];
  const sunColor = mix('#ffd27a', '#ff8d4d', m.twilight);

  return (
    <svg className={`f-scene ${lowCpu ? 'static' : ''}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="sc-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={m.top} /><stop offset="0.55" stopColor={m.mid} /><stop offset="0.8" stopColor={m.hor} />
        </linearGradient>
        <radialGradient id="sc-sun"><stop offset="0" stopColor={sunColor} stopOpacity="0.95" /><stop offset="0.35" stopColor={sunColor} stopOpacity="0.55" /><stop offset="1" stopColor={sunColor} stopOpacity="0" /></radialGradient>
        <radialGradient id="sc-moon"><stop offset="0" stopColor="#f3f1e6" stopOpacity="0.5" /><stop offset="1" stopColor="#f3f1e6" stopOpacity="0" /></radialGradient>
        <linearGradient id="sc-fog" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={m.hor} stopOpacity="0" /><stop offset="0.5" stopColor={m.hor} stopOpacity="0.75" /><stop offset="1" stopColor={m.hor} stopOpacity="0.9" /></linearGradient>
        <linearGradient id="sc-scrim" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#05070c" stopOpacity="0.5" /><stop offset="0.4" stopColor="#05070c" stopOpacity="0.14" /><stop offset="0.62" stopColor="#05070c" stopOpacity="0.34" /><stop offset="0.8" stopColor="#05070c" stopOpacity="0.5" /><stop offset="1" stopColor="#05070c" stopOpacity="0.78" />
        </linearGradient>
        {!lowCpu && <filter id="sc-blur" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="9" /></filter>}
        {!lowCpu && <filter id="sc-soft" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="3" /></filter>}
      </defs>
      <rect width={W} height={H} fill="url(#sc-sky)" />
      {m.starAlpha > 0.02 && <g opacity={m.starAlpha}>{stars.map((s, i) => <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#e9eefc" opacity={s.o} />)}</g>}
      {m.moonAlpha > 0.02 && (
        <g opacity={m.moonAlpha}>
          <circle cx={640} cy={92} r={70} fill="url(#sc-moon)" />
          <circle cx={640} cy={92} r={20} fill="#efeadb" />
          <circle cx={650} cy={86} r={18} fill={m.top} />
        </g>
      )}
      {m.sunVisible && (
        <g opacity={clamp(m.dayness * 1.4) * (1 - m.cover * 0.8)}>
          <circle cx={m.sunX} cy={m.sunY} r={150} fill="url(#sc-sun)" />
          <circle cx={m.sunX} cy={m.sunY} r={24} fill={mix('#fff4cf', sunColor, m.twilight * 0.8)} />
        </g>
      )}
      <g opacity={cloudAlpha} filter={lowCpu ? undefined : 'url(#sc-blur)'}>
        {clouds.map((c, i) => (
          <g key={i} transform={`translate(${c.x} ${c.y}) scale(${c.s})`} fill={cloudFill}>
            <ellipse cx={0} cy={0} rx={110} ry={28} /><ellipse cx={-45} cy={-14} rx={55} ry={30} /><ellipse cx={30} cy={-20} rx={65} ry={36} /><ellipse cx={75} cy={-4} rx={50} ry={26} />
          </g>
        ))}
      </g>
      {m.kind === 'fog' && <rect x={0} y={150} width={W} height={H - 150} fill="url(#sc-fog)" />}
      <path d={hillPath(hillY[0], 26, 1)} fill={m.hills[0]} filter={lowCpu ? undefined : 'url(#sc-soft)'} />
      <path d={hillPath(hillY[1], 22, 2)} fill={m.hills[1]} />
      <g fill={m.hills[2]}>
        {trees.map((tr, i) => pines
          ? <path key={i} d={`M${tr.x} ${hillY[2] - tr.h * 0.9} l${-tr.h * 0.36} ${tr.h} h${tr.h * 0.72} z`} />
          : <g key={i}><circle cx={tr.x} cy={hillY[2] + 2} r={tr.h * 0.5} /><circle cx={tr.x - tr.h * 0.42} cy={hillY[2] + 8} r={tr.h * 0.36} /><circle cx={tr.x + tr.h * 0.4} cy={hillY[2] + 7} r={tr.h * 0.4} /></g>)}
      </g>
      <path d={hillPath(hillY[2], 18, 3)} fill={m.hills[2]} />
      {m.season === 'spring' && !m.snowy && m.dayness > 0.1 && (
        <g opacity={0.45 * m.dayness} fill="#f6b7c9">{trees.filter((_, i) => i % 3 === 0).map((tr, i) => <circle key={i} cx={tr.x} cy={hillY[2] + 1} r={tr.h * 0.5} />)}</g>
      )}
      {m.kind === 'rain' && (
        <g className="sc-rain" stroke="#dfe8f2" strokeOpacity={0.35 + 0.2 * m.dayness} strokeWidth={1.2} strokeLinecap="round">
          {drops.map((d, i) => <line key={i} x1={d.x} y1={d.y} x2={d.x - d.l * 0.25} y2={d.y + d.l} />)}
        </g>
      )}
      {m.kind === 'snow' && (
        <g className="sc-snow" fill="#ffffff" fillOpacity={0.85}>{drops.map((d, i) => <circle key={i} cx={d.x} cy={d.y} r={d.r} />)}</g>
      )}
      <rect width={W} height={H} fill="url(#sc-scrim)" />
    </svg>
  );
}
