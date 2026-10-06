// Burn-in protection for the IPS panel (image retention), from the standby-display research:
// the sleep clock jumps between well-spread places, and everything else drifts a few pixels.

/** Van der Corput radical inverse: Halton sequence in base `b`, evenly filling [0,1) without clumping. */
export function halton(i: number, b: number): number {
  let f = 1, r = 0;
  while (i > 0) { f /= b; r += f * (i % b); i = Math.floor(i / b); }
  return r;
}

/** Where the sleep clock sits for jump number `i`, as % of the canvas. Rect: a safe box (15-85% wide, 15-87% high,
 *  the research's 120-680 × 70-420 px on 800×480); round: inside the inscribed circle. */
export function sleepSpot(i: number, round: boolean): { x: number; y: number } {
  const u = halton(i + 1, 2), v = halton(i + 1, 3);
  if (round) {
    const r = 30 * Math.sqrt(u), a = 2 * Math.PI * v;
    return { x: 50 + r * Math.cos(a), y: 50 + r * Math.sin(a) };
  }
  return { x: 15 + u * 70, y: 15 + v * 72 };
}

/** Pixel orbit: a slow Lissajous path within ±8 × ±6 px, about one pixel a minute, recomputed each minute. */
export function orbitAt(minuteOfDay: number): { x: number; y: number } {
  const t = minuteOfDay / 60;
  return { x: Math.round(8 * Math.sin((2 * Math.PI * t) / 1.7)), y: Math.round(6 * Math.sin((2 * Math.PI * t) / 1.1 + 0.6)) };
}

/** Seconds since local midnight in the device's time zone. */
export function secondsOfDay(d: Date, tz: string): number {
  const p = new Intl.DateTimeFormat('en-AU', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).formatToParts(d);
  const g = (t: string) => parseInt(p.find((x) => x.type === t)?.value ?? '0', 10);
  return (g('hour') % 24) * 3600 + g('minute') * 60 + g('second');
}

/** Day number of the local date (changes at local midnight): seeds the daily scene. */
export function dayNumber(d: Date, tz: string): number {
  const [y, m, day] = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d).split('-').map(Number);
  return Math.floor(Date.UTC(y, m - 1, day) / 86400000);
}
