// Small SVG charts for the Diagnostics page, built to the data-viz method: thin marks, solid hairline grid, one
// y-axis per chart (different units get their own chart), a crosshair tooltip on lines, per-mark tooltips on
// columns and dots, a legend whenever there are two series, and a table view behind every chart.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react';

export const VIZ = { s1: 'var(--viz-1)', s2: 'var(--viz-2)', dim: 'var(--viz-dim)', grid: 'var(--viz-grid)', axis: 'var(--viz-axis)', surface: 'var(--bg-elev)', text: 'var(--fg-muted)', faint: 'var(--fg-faint)' };

export function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(Math.floor(el.getBoundingClientRect().width));
    const ro = new ResizeObserver((e) => setW(Math.floor(e[0].contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// ---- numbers -------------------------------------------------------------------------------------------------
export function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–';
  const a = Math.abs(v);
  const d = digits ?? (a >= 100 || a === 0 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3);
  return v.toLocaleString('en-AU', { minimumFractionDigits: d, maximumFractionDigits: d });
}
export function withUnit(s: string, unit: string): string {
  if (!unit || s === '–') return s;
  return unit === '%' || unit === '°' ? `${s}${unit}` : `${s} ${unit}`;
}
export const fmtU = (v: number | null | undefined, unit: string, digits?: number) => withUnit(fmtNum(v, digits), unit);

function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f >= 7.5 ? 10 : f >= 3.5 ? 5 : f >= 1.5 ? 2 : 1) * mag;
}
/** A y scale on round numbers. Fixed ends (e.g. 0-100 %) stay as given. */
export function niceScale(lo: number, hi: number, count = 3, fixed: [number | undefined, number | undefined] = [undefined, undefined]) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-9) { const pad = Math.abs(hi) * 0.1 || 1; lo -= pad; hi += pad; }
  const step = niceStep(hi - lo, count);
  const nlo = fixed[0] ?? Math.floor(lo / step) * step;
  const nhi = fixed[1] ?? Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = Math.ceil(nlo / step) * step; v <= nhi + step * 1e-6; v += step) ticks.push(Number(v.toFixed(10)));
  return { lo: nlo, hi: nhi, ticks, digits: step >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(step) - 1e-9)) };
}

// ---- time in the clock's zone ----------------------------------------------------------------------------------
const fmts = new Map<string, Intl.DateTimeFormat>();
function parts(t: number, tz: string) {
  let f = fmts.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat('en-AU', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'short' });
    fmts.set(tz, f);
  }
  const p = f.formatToParts(new Date(t));
  const g = (k: string) => p.find((x) => x.type === k)?.value ?? '0';
  return { y: +g('year'), mo: +g('month'), d: +g('day'), h: +g('hour') % 24, mi: +g('minute'), s: +g('second'), wd: g('weekday') };
}
function offsetMs(t: number, tz: string): number {
  const p = parts(t, tz);
  return Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s) - Math.floor(t / 1000) * 1000;
}
export function clockLabel(t: number, tz: string, h24: boolean, withDay = false): string {
  const p = parts(t, tz);
  const hm = h24 ? `${String(p.h).padStart(2, '0')}:${String(p.mi).padStart(2, '0')}` : `${p.h % 12 || 12}:${String(p.mi).padStart(2, '0')}${p.h < 12 ? 'am' : 'pm'}`;
  return withDay ? `${p.wd} ${p.d} · ${hm}` : hm;
}
/** Tick instants on local-time boundaries (15 min, 1 h, 4 h or a day, by the span). */
function timeTicks(t0: number, t1: number, tz: string): { t: number; day: boolean }[] {
  const span = t1 - t0, H = 3_600_000;
  const step = span <= 1.6 * H ? H / 4 : span <= 6.5 * H ? H : span <= 26 * H ? 4 * H : 24 * H;
  const out: { t: number; day: boolean }[] = [];
  const off0 = offsetMs(t0, tz);
  let local = Math.ceil((t0 + off0) / step) * step;
  for (let i = 0; i < 40; i++) {
    const guess = local - offsetMs(local - off0, tz);
    if (guess > t1) break;
    if (guess >= t0) out.push({ t: guess, day: step >= 24 * H });
    local += step;
  }
  return out;
}

// ---- frame: title, legend, chart/table toggle -----------------------------------------------------------------
export interface Key { label: string; color: string; kind?: 'line' | 'dot' | 'rect' }
export function Legend({ items }: { items: Key[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted mb-1" aria-label="legend">
      {items.map((k) => (
        <span key={k.label} className="inline-flex items-center gap-1.5">
          {k.kind === 'dot' ? <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: k.color }} />
            : k.kind === 'rect' ? <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: k.color }} />
              : <span className="inline-block w-3 h-0.5 rounded" style={{ background: k.color }} />}
          {k.label}
        </span>
      ))}
    </div>
  );
}

export function Figure({ title, sub, legend, table, busy, children }: { title: string; sub?: ReactNode; legend?: Key[]; table: () => ReactNode; busy?: boolean; children: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <figure className="m-0 min-w-0">
      <figcaption className="flex items-start justify-between gap-2 mb-1">
        <div className="min-w-0">
          <div className="text-sm font-medium text-fg">{title}</div>
          {sub && <div className="text-xs text-muted">{sub}</div>}
        </div>
        <button type="button" className="text-xs text-muted hover:text-fg shrink-0 px-1.5 py-0.5 rounded-md border border-border" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? 'Chart' : 'Table'}
        </button>
      </figcaption>
      {legend && legend.length >= 2 && <Legend items={legend} />}
      <div className="transition-opacity" style={{ opacity: busy ? 0.55 : 1 }}>{asTable ? <div className="max-h-72 overflow-auto">{table()}</div> : children}</div>
    </figure>
  );
}

export function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <table className="viz-table">
      <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

function Tip({ x, y, w, children }: { x: number; y: number; w: number; children: ReactNode }) {
  const flip = x > w * 0.55;
  return <div className="viz-tip" style={{ top: Math.max(0, y), left: flip ? undefined : x + 14, right: flip ? w - x + 14 : undefined }}>{children}</div>;
}

/** A label drawn over data: a ring of the card colour keeps it legible where it crosses a line. */
function Halo({ x, y, children, anchor = 'end', size = 9.5, ink = VIZ.text }: { x: number; y: number; children: ReactNode; anchor?: 'start' | 'middle' | 'end'; size?: number; ink?: string }) {
  return <text x={x} y={y} textAnchor={anchor} fontSize={size} fill={ink} stroke={VIZ.surface} strokeWidth={3} strokeLinejoin="round" paintOrder="stroke">{children}</text>;
}

/** y tick text: the top tick carries a short unit (dB, ms, %…), so the axis says what it measures. */
const tickText = (v: number, top: boolean, unit: string, digits: number) => (top && unit && unit.length <= 4 ? withUnit(fmtNum(v, digits), unit) : fmtNum(v, digits));

/** 4 px rounded data end, square at the baseline (columns grow up from `base`). */
function colPath(x0: number, x1: number, yTop: number, base: number): string {
  const h = base - yTop;
  if (h <= 0.5) return '';
  const r = Math.min(4, h, (x1 - x0) / 2);
  return `M${x0},${base}V${yTop + r}Q${x0},${yTop} ${x0 + r},${yTop}H${x1 - r}Q${x1},${yTop} ${x1},${yTop + r}V${base}Z`;
}

// ---- time series ---------------------------------------------------------------------------------------------
export interface TPoint { t: number; v: number; lo: number; hi: number }
export interface TSeries { key: string; label: string; color: string; points: TPoint[] }
export interface TMarker { t: number; label: string }
export interface TRef { y: number; label: string }

function segments(points: TPoint[]): TPoint[][] {
  if (points.length < 2) return points.length ? [points] : [];
  const gaps = points.slice(1).map((p, i) => p.t - points[i].t).sort((a, b) => a - b);
  const typical = gaps[Math.floor(gaps.length / 2)];
  const out: TPoint[][] = [[points[0]]];
  for (let i = 1; i < points.length; i++) {
    if (points[i].t - points[i - 1].t > Math.max(2.5 * typical, 90_000)) out.push([]);  // no data: the device was off
    out[out.length - 1].push(points[i]);
  }
  return out;
}

export function TimeChart({ series, unit, t0, t1, tz, h24, height = 132, yMin, yMax, refs = [], markers = [], digits }: {
  series: TSeries[]; unit: string; t0: number; t1: number; tz: string; h24: boolean; height?: number;
  yMin?: number; yMax?: number; refs?: TRef[]; markers?: TMarker[]; digits?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hi, setHi] = useState<number | null>(null); // index into `times`
  const M = { l: 50, r: 52, t: 8, b: 20 };
  const all = series.flatMap((s) => s.points);
  const sc = useMemo(() => {
    const lo = Math.min(...all.map((p) => p.lo), ...refs.map((r) => r.y));
    const hiV = Math.max(...all.map((p) => p.hi), ...refs.map((r) => r.y));
    return niceScale(yMin ?? lo, yMax ?? hiV, 3, [yMin, yMax]);
  }, [all.length, series, refs, yMin, yMax]); // eslint-disable-line react-hooks/exhaustive-deps
  const times = useMemo(() => [...new Set(all.map((p) => p.t))].sort((a, b) => a - b), [series]); // eslint-disable-line react-hooks/exhaustive-deps
  const pw = Math.max(10, w - M.l - M.r), ph = height - M.t - M.b;
  const x = (t: number) => M.l + ((t - t0) / Math.max(1, t1 - t0)) * pw;
  const y = (v: number) => M.t + (1 - (v - sc.lo) / Math.max(1e-9, sc.hi - sc.lo)) * ph;
  const ticks = useMemo(() => timeTicks(t0, t1, tz), [t0, t1, tz]);
  const bucket = times.length > 1 ? (times[times.length - 1] - times[0]) / (times.length - 1) : 60_000;

  const pick = (px: number) => {
    if (!times.length) return null;
    const t = t0 + ((px - M.l) / pw) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    return best;
  };
  const onKey = (e: KeyboardEvent) => {
    if (!times.length) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      setHi((i) => Math.max(0, Math.min(times.length - 1, (i ?? times.length - 1) + (e.key === 'ArrowLeft' ? -1 : 1))));
    } else if (e.key === 'Escape') setHi(null);
  };

  // end labels only when they do not collide (otherwise the legend and the tooltip carry identity)
  const ends = series.map((s) => s.points[s.points.length - 1]).map((p) => (p ? y(p.v) : null));
  const collide = ends.some((a, i) => a !== null && ends.some((b, j) => j > i && b !== null && Math.abs(a - b) < 12));
  const ht = hi !== null ? times[hi] : null;
  const near = ht !== null ? markers.filter((m) => Math.abs(m.t - ht) <= bucket / 2 + 30_000) : [];

  return (
    <div ref={ref} className="viz" tabIndex={0} onKeyDown={onKey} onBlur={() => setHi(null)} aria-label={`${series.map((s) => s.label).join(', ')} over time`}
      onPointerLeave={() => setHi(null)}>
      {w > 0 && (
        <svg width={w} height={height} role="img" aria-hidden="true"
          onPointerMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); setHi(pick(e.clientX - r.left)); }}>
          {sc.ticks.map((v) => (
            <g key={v}>
              <line x1={M.l} x2={M.l + pw} y1={y(v)} y2={y(v)} stroke={VIZ.grid} strokeWidth={1} shapeRendering="crispEdges" />
              <text x={M.l - 6} y={y(v)} dy="0.32em" textAnchor="end" fontSize={10} fill={VIZ.text} className="tnum">{tickText(v, v === sc.ticks[sc.ticks.length - 1], unit, sc.digits)}</text>
            </g>
          ))}
          <line x1={M.l} x2={M.l + pw} y1={M.t + ph} y2={M.t + ph} stroke={VIZ.axis} strokeWidth={1} shapeRendering="crispEdges" />
          {ticks.map((k) => (
            <text key={k.t} x={x(k.t)} y={height - 5} textAnchor="middle" fontSize={10} fill={VIZ.text} className="tnum">
              {k.day ? clockLabel(k.t, tz, h24, true).split(' · ')[0] : clockLabel(k.t, tz, h24)}
            </text>
          ))}
          {markers.map((m, i) => m.t >= t0 && m.t <= t1 && (
            <line key={i} x1={x(m.t)} x2={x(m.t)} y1={M.t} y2={M.t + ph} stroke={VIZ.faint} strokeWidth={1} shapeRendering="crispEdges" />
          ))}
          {refs.map((r) => <line key={r.label} x1={M.l} x2={M.l + pw} y1={y(r.y)} y2={y(r.y)} stroke={VIZ.faint} strokeWidth={1} shapeRendering="crispEdges" />)}
          {series.map((s) => segments(s.points).map((seg, i) => (
            <g key={`${s.key}-${i}`}>
              {seg.some((p) => p.hi - p.lo > 1e-9) && (
                <path d={`M${seg.map((p) => `${x(p.t).toFixed(1)},${y(p.hi).toFixed(1)}`).join('L')}L${[...seg].reverse().map((p) => `${x(p.t).toFixed(1)},${y(p.lo).toFixed(1)}`).join('L')}Z`}
                  fill={s.color} opacity={0.1} />
              )}
              {seg.length > 1
                ? <path d={`M${seg.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('L')}`} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                : <circle cx={x(seg[0].t)} cy={y(seg[0].v)} r={2} fill={s.color} />}
            </g>
          )))}
          {series.map((s) => {
            const p = s.points[s.points.length - 1];
            if (!p) return null;
            return (
              <g key={`end-${s.key}`}>
                <circle cx={x(p.t)} cy={y(p.v)} r={4} fill={s.color} stroke={VIZ.surface} strokeWidth={2} />
                {!collide && <text x={x(p.t) + 8} y={y(p.v) + 3.5} fontSize={10.5} fill="var(--fg)" className="tnum" stroke={VIZ.surface} strokeWidth={3} strokeLinejoin="round" paintOrder="stroke">{fmtNum(p.v, digits)}</text>}
              </g>
            );
          })}
          {refs.map((r) => <Halo key={r.label} x={M.l + pw - 2} y={y(r.y) - 3}>{r.label}</Halo>)}
          {ht !== null && (
            <g pointerEvents="none">
              <line x1={x(ht)} x2={x(ht)} y1={M.t} y2={M.t + ph} stroke="var(--fg-muted)" strokeWidth={1} shapeRendering="crispEdges" />
              {series.map((s) => {
                const p = s.points.find((q) => q.t === ht);
                return p ? <circle key={s.key} cx={x(p.t)} cy={y(p.v)} r={4} fill={s.color} stroke={VIZ.surface} strokeWidth={2} /> : null;
              })}
            </g>
          )}
          <rect x={M.l} y={M.t} width={pw} height={ph} fill="transparent" />
        </svg>
      )}
      {ht !== null && (
        <Tip x={x(ht)} y={M.t} w={w}>
          <div className="m mb-0.5">{clockLabel(ht, tz, h24, t1 - t0 > 26 * 3_600_000)}</div>
          {series.map((s) => {
            const p = s.points.find((q) => q.t === ht);
            return (
              <div key={s.key}>
                <span className="k" style={{ background: s.color }} />
                <b className="tnum">{p ? withUnit(fmtNum(p.v, digits), unit) : 'no data'}</b>
                {p && p.hi - p.lo > 1e-9 && <span className="m tnum"> ({fmtNum(p.lo, digits)}–{fmtNum(p.hi, digits)})</span>}
                {series.length > 1 && <span className="m"> {s.label}</span>}
              </div>
            );
          })}
          {near.map((m, i) => <div key={i} className="m">▸ {m.label}</div>)}
        </Tip>
      )}
    </div>
  );
}

export function timeTable(series: TSeries[], unit: string, tz: string, h24: boolean, digits?: number, maxRows = 60) {
  const times = [...new Set(series.flatMap((s) => s.points.map((p) => p.t)))].sort((a, b) => b - a);
  const step = Math.max(1, Math.ceil(times.length / maxRows));
  const rows = times.filter((_, i) => i % step === 0).map((t) => [
    clockLabel(t, tz, h24, true),
    ...series.map((s) => {
      const p = s.points.find((q) => q.t === t);
      return p ? `${withUnit(fmtNum(p.v, digits), unit)}${p.hi - p.lo > 1e-9 ? ` (${fmtNum(p.lo, digits)}–${fmtNum(p.hi, digits)})` : ''}` : '–';
    }),
  ]);
  return <Table head={['Time', ...series.map((s) => s.label)]} rows={rows} />;
}

// ---- a line over a numeric x (spectrum, impulse response) -----------------------------------------------------
export function LineChart({ xs, ys, xUnit, yUnit, xTitle, height = 166, xDigits = 2, yDigits = 1, refs = [], dots = [], color = VIZ.s1, yMin, yMax, label }: {
  xs: number[]; ys: number[]; xUnit: string; yUnit: string; xTitle: string; height?: number; xDigits?: number; yDigits?: number;
  refs?: TRef[]; dots?: { x: number; y: number; label: string }[]; color?: string; yMin?: number; yMax?: number; label: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hi, setHi] = useState<number | null>(null);
  const M = { l: 48, r: 12, t: 8, b: 34 };
  const pw = Math.max(10, w - M.l - M.r), ph = height - M.t - M.b;
  const xlo = xs[0] ?? 0, xhi = xs[xs.length - 1] ?? 1;
  const sc = niceScale(yMin ?? Math.min(...ys, ...refs.map((r) => r.y)), yMax ?? Math.max(...ys, ...refs.map((r) => r.y)), 3, [yMin, yMax]);
  const xsc = niceScale(xlo, xhi, Math.max(2, Math.floor(pw / 70)), [xlo, xhi]);
  const x = (v: number) => M.l + ((v - xlo) / Math.max(1e-9, xhi - xlo)) * pw;
  const y = (v: number) => M.t + (1 - (Math.max(sc.lo, Math.min(sc.hi, v)) - sc.lo) / Math.max(1e-9, sc.hi - sc.lo)) * ph;
  const d = xs.map((v, i) => `${i ? 'L' : 'M'}${x(v).toFixed(1)},${y(ys[i]).toFixed(1)}`).join('');
  const pick = (px: number) => {
    if (!xs.length) return null;
    const v = xlo + ((px - M.l) / pw) * (xhi - xlo);
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - v) < Math.abs(xs[best] - v)) best = i;
    return best;
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const stepN = Math.max(1, Math.round(xs.length / 64));
    setHi((i) => Math.max(0, Math.min(xs.length - 1, (i ?? Math.floor(xs.length / 2)) + (e.key === 'ArrowLeft' ? -stepN : stepN))));
  };
  return (
    <div ref={ref} className="viz" tabIndex={0} onKeyDown={onKey} onBlur={() => setHi(null)} onPointerLeave={() => setHi(null)} aria-label={label}>
      {w > 0 && (
        <svg width={w} height={height} role="img" aria-hidden="true"
          onPointerMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); setHi(pick(e.clientX - r.left)); }}>
          {sc.ticks.map((v) => (
            <g key={v}>
              <line x1={M.l} x2={M.l + pw} y1={y(v)} y2={y(v)} stroke={VIZ.grid} strokeWidth={1} shapeRendering="crispEdges" />
              <text x={M.l - 6} y={y(v)} dy="0.32em" textAnchor="end" fontSize={10} fill={VIZ.text} className="tnum">{tickText(v, v === sc.ticks[sc.ticks.length - 1], yUnit, sc.digits)}</text>
            </g>
          ))}
          <line x1={M.l} x2={M.l + pw} y1={M.t + ph} y2={M.t + ph} stroke={VIZ.axis} strokeWidth={1} shapeRendering="crispEdges" />
          {xsc.ticks.map((v) => <text key={v} x={x(v)} y={M.t + ph + 14} textAnchor="middle" fontSize={10} fill={VIZ.text} className="tnum">{fmtNum(v, Math.max(xsc.digits, 0))}</text>)}
          <text x={M.l + pw / 2} y={height - 3} textAnchor="middle" fontSize={10} fill={VIZ.text}>{xTitle}</text>
          {refs.map((r) => <line key={r.label} x1={M.l} x2={M.l + pw} y1={y(r.y)} y2={y(r.y)} stroke={VIZ.faint} strokeWidth={1} shapeRendering="crispEdges" />)}
          <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
          {dots.map((p) => (
            <g key={p.label}>
              <circle cx={x(p.x)} cy={y(p.y)} r={4} fill={color} stroke={VIZ.surface} strokeWidth={2} />
              <Halo x={x(p.x) + 7} y={y(p.y) - 6} anchor="start" size={10} ink="var(--fg)">{p.label}</Halo>
            </g>
          ))}
          {refs.map((r) => <Halo key={r.label} x={M.l + pw - 2} y={y(r.y) - 3}>{r.label}</Halo>)}
          {hi !== null && (
            <g pointerEvents="none">
              <line x1={x(xs[hi])} x2={x(xs[hi])} y1={M.t} y2={M.t + ph} stroke="var(--fg-muted)" strokeWidth={1} shapeRendering="crispEdges" />
              <circle cx={x(xs[hi])} cy={y(ys[hi])} r={4} fill={color} stroke={VIZ.surface} strokeWidth={2} />
            </g>
          )}
          <rect x={M.l} y={M.t} width={pw} height={ph} fill="transparent" />
        </svg>
      )}
      {hi !== null && (
        <Tip x={x(xs[hi])} y={M.t} w={w}>
          <b className="tnum">{fmtU(ys[hi], yUnit, yDigits)}</b> <span className="m tnum">at {fmtU(xs[hi], xUnit, xDigits)}</span>
        </Tip>
      )}
    </div>
  );
}

export function xyTable(xs: number[], ys: number[], xHead: string, yHead: string, xDigits = 2, yDigits = 1, maxRows = 64) {
  const step = Math.max(1, Math.ceil(xs.length / maxRows));
  return <Table head={[xHead, yHead]} rows={xs.map((v, i) => [fmtNum(v, xDigits), fmtNum(ys[i], yDigits)]).filter((_, i) => i % step === 0)} />;
}

// ---- columns (one per category) ------------------------------------------------------------------------------
export interface Col { key: string; label: string; value: number | null; color: string; note?: string }
export function ColumnChart({ cols, unit, height = 150, yMax, refs = [], xLabel, xTitle, label, digits = 0 }: {
  cols: Col[]; unit: string; height?: number; yMax?: number; refs?: TRef[]; xLabel?: (c: Col, i: number) => string | null; xTitle?: string; label: string; digits?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hi, setHi] = useState<number | null>(null);
  const M = { l: 48, r: 8, t: 10, b: (xLabel ? 20 : 6) + (xTitle ? 14 : 0) };
  const pw = Math.max(10, w - M.l - M.r), ph = height - M.t - M.b;
  const sc = niceScale(0, yMax ?? Math.max(1, ...cols.map((c) => c.value ?? 0), ...refs.map((r) => r.y)), 3, [0, yMax]);
  const band = pw / Math.max(1, cols.length);
  const bw = Math.max(1, Math.min(24, band - 2));
  const y = (v: number) => M.t + (1 - (Math.min(v, sc.hi) - sc.lo) / Math.max(1e-9, sc.hi - sc.lo)) * ph;
  const cx = (i: number) => M.l + band * i + band / 2;
  // labels at about 6 px a character; a label is dropped when it would touch the last one kept
  const shown = new Set<number>();
  if (xLabel) {
    let lastRight = -Infinity;
    cols.forEach((c, i) => {
      const t = xLabel(c, i);
      if (!t) return;
      const half = (t.length * 6) / 2;
      if (cx(i) - half >= lastRight + 4) { shown.add(i); lastRight = cx(i) + half; }
    });
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    setHi((i) => Math.max(0, Math.min(cols.length - 1, (i ?? -1) + (e.key === 'ArrowLeft' ? -1 : 1))));
  };
  return (
    <div ref={ref} className="viz" tabIndex={0} onKeyDown={onKey} onBlur={() => setHi(null)} onPointerLeave={() => setHi(null)} aria-label={label}>
      {w > 0 && (
        <svg width={w} height={height} role="img" aria-hidden="true">
          {sc.ticks.map((v) => (
            <g key={v}>
              <line x1={M.l} x2={M.l + pw} y1={y(v)} y2={y(v)} stroke={VIZ.grid} strokeWidth={1} shapeRendering="crispEdges" />
              <text x={M.l - 6} y={y(v)} dy="0.32em" textAnchor="end" fontSize={10} fill={VIZ.text} className="tnum">{tickText(v, v === sc.ticks[sc.ticks.length - 1], unit, sc.digits)}</text>
            </g>
          ))}
          {refs.map((r) => <line key={r.label} x1={M.l} x2={M.l + pw} y1={y(r.y)} y2={y(r.y)} stroke={VIZ.faint} strokeWidth={1} shapeRendering="crispEdges" />)}
          {cols.map((c, i) => c.value !== null && c.value > 0 && (
            <path key={c.key} d={colPath(cx(i) - bw / 2, cx(i) + bw / 2, y(c.value), M.t + ph)} fill={c.color} style={{ filter: hi === i ? 'brightness(1.25)' : undefined }} />
          ))}
          <line x1={M.l} x2={M.l + pw} y1={M.t + ph} y2={M.t + ph} stroke={VIZ.axis} strokeWidth={1} shapeRendering="crispEdges" />
          {refs.map((r) => <Halo key={r.label} x={M.l + pw - 2} y={y(r.y) - 3}>{r.label}</Halo>)}
          {xLabel && cols.map((c, i) => { const t = xLabel(c, i); return t && shown.has(i) ? <text key={c.key} x={cx(i)} y={M.t + ph + 14} textAnchor="middle" fontSize={9.5} fill={VIZ.text}>{t}</text> : null; })}
          {xTitle && <text x={M.l + pw / 2} y={height - 3} textAnchor="middle" fontSize={10} fill={VIZ.text}>{xTitle}</text>}
          {cols.map((c, i) => <rect key={c.key} x={M.l + band * i} y={M.t} width={band} height={ph} fill="transparent" onPointerEnter={() => setHi(i)} />)}
        </svg>
      )}
      {hi !== null && cols[hi] && (
        <Tip x={cx(hi)} y={M.t} w={w}>
          <b className="tnum">{fmtU(cols[hi].value, unit, digits)}</b> <span className="m">{cols[hi].label}</span>
          {cols[hi].note && <div className="m">{cols[hi].note}</div>}
        </Tip>
      )}
    </div>
  );
}

// ---- sky plot (azimuth/elevation) -----------------------------------------------------------------------------
export interface SkyDot { key: string; az: number; el: number; color: string; title: string; detail: string }
export function SkyPlot({ dots, label }: { dots: SkyDot[]; label: string }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hi, setHi] = useState<number | null>(null);
  const size = Math.min(w, 300), R = size / 2 - 16, c = size / 2;
  const pos = (az: number, el: number) => {
    const r = (R * (90 - Math.max(0, Math.min(90, el)))) / 90, a = (az * Math.PI) / 180;
    return { x: c + r * Math.sin(a), y: c - r * Math.cos(a) };
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    setHi((i) => Math.max(0, Math.min(dots.length - 1, (i ?? -1) + (e.key === 'ArrowLeft' ? -1 : 1))));
  };
  const h = hi !== null ? dots[hi] : null;
  const hp = h ? pos(h.az, h.el) : null;
  return (
    <div ref={ref} className="viz" tabIndex={0} onKeyDown={onKey} onBlur={() => setHi(null)} onPointerLeave={() => setHi(null)} aria-label={label}>
      {size > 0 && (
        <svg width={size} height={size} role="img" aria-hidden="true" className="mx-auto block">
          {[0, 30, 60].map((el) => <circle key={el} cx={c} cy={c} r={(R * (90 - el)) / 90} fill="none" stroke={el ? VIZ.grid : VIZ.axis} strokeWidth={1} />)}
          <line x1={c - R} x2={c + R} y1={c} y2={c} stroke={VIZ.grid} strokeWidth={1} />
          <line x1={c} x2={c} y1={c - R} y2={c + R} stroke={VIZ.grid} strokeWidth={1} />
          {(['N', 'E', 'S', 'W'] as const).map((d, i) => {
            const p = pos(i * 90, -12);
            return <text key={d} x={p.x} y={p.y} dy="0.32em" textAnchor="middle" fontSize={10} fill={VIZ.text}>{d}</text>;
          })}
          <text x={c + 3} y={c - (R * 60) / 90 - 2} fontSize={9} fill={VIZ.text}>30°</text>
          <text x={c + 3} y={c - (R * 30) / 90 - 2} fontSize={9} fill={VIZ.text}>60°</text>
          {dots.map((d, i) => {
            const p = pos(d.az, d.el);
            return (
              <g key={d.key} onPointerEnter={() => setHi(i)}>
                <circle cx={p.x} cy={p.y} r={12} fill="transparent" />
                <circle cx={p.x} cy={p.y} r={hi === i ? 6 : 5} fill={d.color} stroke={VIZ.surface} strokeWidth={2} />
              </g>
            );
          })}
        </svg>
      )}
      {h && hp && (
        <Tip x={hp.x + (w - size) / 2} y={Math.max(0, hp.y - 20)} w={w}>
          <b>{h.title}</b>
          <div className="m">{h.detail}</div>
        </Tip>
      )}
    </div>
  );
}

// ---- figures ---------------------------------------------------------------------------------------------------
export type Tone = 'ok' | 'warn' | 'err' | 'off';
export function Meter({ value, min, max, tone, label }: { value: number | null; min: number; max: number; tone: Tone; label: string }) {
  const color = tone === 'off' ? 'var(--fg-faint)' : `var(--${tone})`;
  const f = value === null ? 0 : Math.max(0, Math.min(1, (value - min) / (max - min)));
  return (
    <div className="h-2 rounded-full overflow-hidden" style={{ background: `color-mix(in srgb, ${color} 22%, transparent)` }} role="meter" aria-label={label}
      aria-valuemin={min} aria-valuemax={max} aria-valuenow={value ?? undefined}>
      <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${f * 100}%`, background: color }} />
    </div>
  );
}

export function Sparkline({ values, height = 28, label }: { values: number[]; height?: number; label: string }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  if (values.length < 2) return <div ref={ref} style={{ height }} />;
  const lo = Math.min(...values), hiV = Math.max(...values), span = hiV - lo || 1;
  const x = (i: number) => 2 + (i / (values.length - 1)) * (w - 8);
  const y = (v: number) => 3 + (1 - (v - lo) / span) * (height - 6);
  const last = values.length - 1;
  return (
    <div ref={ref} aria-label={label} role="img">
      {w > 0 && (
        <svg width={w} height={height}>
          <path d={values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')} fill="none" stroke={VIZ.dim} strokeWidth={1.5} strokeLinejoin="round" />
          <circle cx={x(last)} cy={y(values[last])} r={3} fill={VIZ.s1} stroke={VIZ.surface} strokeWidth={1.5} />
        </svg>
      )}
    </div>
  );
}

export function Stat({ label, value, sub, children }: { label: string; value: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-elev-2/40 p-3 min-w-0">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-xl font-semibold pnum mt-0.5 truncate">{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}
