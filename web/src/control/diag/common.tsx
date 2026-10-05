import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../../shared/api';
import { Spinner } from '../../shared/components';
import { runAction, toPoints, usePoll, type Check, type CheckStatus, type DiagEvent, type History } from './api';
import { Figure, TimeChart, timeTable, VIZ, type TMarker, type TRef, type TSeries } from './charts';

// ---- status: always an icon and a word, never colour alone ----------------------------------------------------
const STATUS: Record<CheckStatus, { icon: string; word: string; cls: string }> = {
  ok: { icon: '✓', word: 'OK', cls: 'text-ok' },
  warn: { icon: '!', word: 'Warning', cls: 'text-warn' },
  fail: { icon: '✕', word: 'Problem', cls: 'text-err' },
  info: { icon: 'i', word: 'Info', cls: 'text-muted' },
  off: { icon: '–', word: 'Off', cls: 'text-faint' },
};
export function StatusIcon({ status }: { status: CheckStatus }) {
  const s = STATUS[status];
  return (
    <span className={`inline-flex w-5 h-5 shrink-0 items-center justify-center rounded-full border border-current text-[11px] font-bold ${s.cls}`} role="img" aria-label={s.word} title={s.word}>
      {s.icon}
    </span>
  );
}
export function StatusWord({ status }: { status: CheckStatus }) {
  return <span className="inline-flex items-center gap-1.5 text-xs text-muted"><StatusIcon status={status} />{STATUS[status].word}</span>;
}

// ---- fixes ------------------------------------------------------------------------------------------------------
let titles: Record<string, string> | null = null;
export function useActionTitles(): Record<string, string> {
  const [t, setT] = useState(titles ?? {});
  useEffect(() => {
    if (titles) return;
    api.get<{ id: string; title: string }[]>('/api/diag/actions').then((l) => { titles = Object.fromEntries(l.map((a) => [a.id, a.title])); setT(titles); }).catch(() => {});
  }, []);
  return t;
}

/** A fix button that shows what happened next to it. `value` goes to actions that need one (gain, channel). */
export function ActionButton({ id, label, value, onDone, primary = false, confirm }: { id: string; label: string; value?: unknown; onDone?: () => void; primary?: boolean; confirm?: string }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const go = async () => {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true);
    setMsg(null);
    const r = await runAction(id, value);
    setBusy(false);
    setMsg({ ok: r.ok, text: r.message });
    onDone?.();
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" className={`btn btn-sm ${primary ? 'btn-primary' : ''}`} onClick={go} disabled={busy}>{busy ? <Spinner /> : null}{label}</button>
      {msg && <span className={`text-xs ${msg.ok ? 'text-muted' : 'text-err'}`} role="status">{msg.ok ? '✓ ' : '✕ '}{msg.text}</span>}
    </span>
  );
}

// actions that need a value are done from the area's own tools
const NEEDS_VALUE: Record<string, string> = { 'dab.gain': 'dab', 'dab.retune': 'dab' };

export function CheckRow({ c, onChanged, onGo }: { c: Check; onChanged?: () => void; onGo?: (tab: string) => void }) {
  const titlesMap = useActionTitles();
  return (
    <div className="flex gap-3 py-2.5 border-b border-border last:border-0" data-check={c.id}>
      <StatusIcon status={c.status} />
      <div className="min-w-0 flex-1">
        <div className="text-fg">{c.title}</div>
        <div className="text-sm text-muted break-words">{c.detail}</div>
        {c.hint && <div className="text-xs text-muted mt-1">→ {c.hint}</div>}
        {c.actions.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {c.actions.map((a) => NEEDS_VALUE[a]
              ? <button key={a} type="button" className="btn btn-sm" onClick={() => onGo?.(NEEDS_VALUE[a])}>{titlesMap[a] ?? a}…</button>
              : <ActionButton key={a} id={a} label={titlesMap[a] ?? a} onDone={onChanged} />)}
          </div>
        )}
      </div>
    </div>
  );
}

/** Checks of one area; ones that belong to a chain (group) are shown as numbered steps under its name. */
export function CheckList({ checks, onChanged, onGo, empty = 'Nothing to check here.' }: { checks: Check[]; onChanged?: () => void; onGo?: (tab: string) => void; empty?: string }) {
  if (!checks.length) return <div className="text-sm text-muted py-2">{empty}</div>;
  const groups: { name: string | null; items: Check[] }[] = [];
  for (const c of checks) {
    const g = groups.find((x) => x.name === c.group);
    if (g) g.items.push(c); else groups.push({ name: c.group, items: [c] });
  }
  return (
    <div>
      {groups.map((g) => g.name ? (
        <div key={g.name} className="mt-2 first:mt-0">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted pt-1">
            <StatusIcon status={worst(g.items)} /><span>{g.name}</span>
          </div>
          <ol className="ml-2.5 pl-4 border-l border-border">
            {g.items.map((c) => <li key={c.id} className="list-none"><CheckRow c={c} onChanged={onChanged} onGo={onGo} /></li>)}
          </ol>
        </div>
      ) : (
        <div key="_">{g.items.map((c) => <CheckRow key={c.id} c={c} onChanged={onChanged} onGo={onGo} />)}</div>
      ))}
    </div>
  );
}

const ORDER: CheckStatus[] = ['fail', 'warn', 'info', 'ok', 'off'];
export function worst(checks: Check[]): CheckStatus {
  return ORDER.find((s) => checks.some((c) => c.status === s && s !== 'info')) ?? (checks.some((c) => c.status === 'info') ? 'info' : 'ok');
}

// ---- facts -------------------------------------------------------------------------------------------------------
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="text-sm sm:grid sm:grid-cols-[minmax(9rem,auto)_1fr] sm:gap-x-4 sm:gap-y-1.5">
      {rows.map(([k, v]) => (
        <div key={k} className="py-1 sm:py-0 sm:contents">
          <dt className="text-muted text-xs sm:text-sm">{k}</dt>
          <dd className="m-0 min-w-0 break-words tnum">{v ?? '–'}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ago(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return '–';
  if (s < 1) return 'just now';
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
export const sinceIso = (iso: string | null | undefined) => (iso ? ago((Date.now() - Date.parse(iso)) / 1000) : '–');

// ---- history -------------------------------------------------------------------------------------------------------
export const RANGES: { hours: number; label: string }[] = [{ hours: 1, label: '1 h' }, { hours: 6, label: '6 h' }, { hours: 24, label: '24 h' }, { hours: 168, label: '7 days' }];

export function RangePicker({ hours, onChange }: { hours: number; onChange: (h: number) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-border p-0.5" role="group" aria-label="History range">
      {RANGES.map((r) => (
        <button key={r.hours} type="button" aria-pressed={hours === r.hours} onClick={() => onChange(r.hours)}
          className={`px-2.5 py-1 text-xs rounded-md ${hours === r.hours ? 'bg-elev-2 text-fg font-medium' : 'text-muted hover:text-fg'}`}>{r.label}</button>
      ))}
    </div>
  );
}

const EVENT_LABEL: Record<string, (e: DiagEvent) => string> = {
  dab_restart: (e) => `DAB decoder restarted${e.reason ? ` (${String(e.reason)})` : ''}`,
  dab_scan: () => 'DAB scan',
  ring_start: (e) => `Alarm rang${e.label ? `: ${String(e.label)}` : ''}`,
  ring_fallback: () => 'Alarm fell back to the chime',
  alarm_fire: (e) => `Alarm${e.label ? ` ${String(e.label)}` : ''}`,
  alarm_missed: (e) => `Alarm missed${e.label ? `: ${String(e.label)}` : ''}`,
  hotspot_start: () => 'Setup hotspot on', hotspot_stop: () => 'Setup hotspot off',
  diag_action: (e) => `Fix: ${String(e.action ?? '')}${e.ok === false ? ' (failed)' : ''}`,
  light_wake: () => 'Light wake', sleep_mode: (e) => `Sleep mode ${e.on ? 'on' : 'off'}${e.reason ? ` (${String(e.reason)})` : ''}`,
  reboot: () => 'Reboot', shutdown: () => 'Shutdown', restore: () => 'Settings restored', update_start: () => 'Software update', wifi_connect: () => 'Wi-Fi connect',
};
export const eventLabel = (e: DiagEvent) => (EVENT_LABEL[e.kind] ?? ((x: DiagEvent) => x.kind.replace(/_/g, ' ')))(e);

const POWER = ['reboot', 'shutdown'];
const action = (e: DiagEvent, ...prefixes: string[]) => e.kind === 'diag_action' && prefixes.some((p) => String(e.action ?? '').startsWith(p));
/** Which events each area's history marks (the rest belong to other areas). */
export const EVENTS: Record<string, (e: DiagEvent) => boolean> = {
  dab: (e) => ['dab_restart', 'dab_scan', 'ring_start', 'ring_fallback', ...POWER].includes(e.kind) || action(e, 'dab.'),
  gps: (e) => POWER.includes(e.kind) || action(e, 'gps.'),
  time: (e) => POWER.includes(e.kind) || action(e, 'time.', 'gps.'),
  network: (e) => ['hotspot_start', 'hotspot_stop', 'wifi_connect', ...POWER].includes(e.kind),
  devices: (e) => ['ring_start', 'ring_fallback', 'light_wake', 'sleep_mode', ...POWER].includes(e.kind) || action(e, 'audio.', 'airplay.', 'display.'),
  system: (e) => [...POWER, 'update_start', 'restore', 'alarm_missed'].includes(e.kind) || e.kind === 'diag_action',
};

export interface ChartSpec {
  title: string; metrics: { key: string; label?: string; color?: string }[]; unit?: string; yMin?: number; yMax?: number; refs?: TRef[];
  digits?: number; sub?: string; height?: number;
}

/** One range row above a set of history charts (one chart per unit, never two axes on one chart). */
export function HistorySection({ specs, hours, onHours, tz, h24, area }: { specs: ChartSpec[]; hours: number; onHours: (h: number) => void; tz: string; h24: boolean; area: keyof typeof EVENTS }) {
  const keys = useMemo(() => [...new Set(specs.flatMap((s) => s.metrics.map((m) => m.key)))].join(','), [specs]);
  const { data, busy, error } = usePoll<History>(`/api/diag/history?metrics=${keys}&hours=${hours}&points=240`, 60_000);
  // the axis follows the data on screen: while a new range loads, the old one stays drawn on its own axis
  const shownHours = data?.hours ?? hours;
  const t1 = Date.now(), t0 = t1 - shownHours * 3_600_000;
  const events = (data?.events ?? []).filter(EVENTS[area]);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <RangePicker hours={hours} onChange={onHours} />
        {busy && !data && <Spinner />}
        {error && <span className="text-xs text-err">{error}</span>}
        <span className="text-xs text-muted">Sampled every 10 s and saved once a minute, so it survives a reboot. The shaded band is the lowest to highest reading in each step.</span>
      </div>
      {data && specs.map((spec) => {
        const series: TSeries[] = spec.metrics.map((m, i) => {
          const hm = data.metrics[m.key];
          return { key: m.key, label: m.label ?? hm?.label ?? m.key, color: m.color ?? (i === 0 ? VIZ.s1 : VIZ.s2), points: toPoints(hm) };
        });
        const unit = spec.unit ?? data.metrics[spec.metrics[0].key]?.unit ?? '';
        const markers: TMarker[] = events.map((e) => ({ t: Date.parse(e.at), label: eventLabel(e) }));
        const empty = series.every((s) => !s.points.length);
        return (
          <Figure key={spec.title} title={spec.title} sub={spec.sub} busy={busy} legend={series.length > 1 ? series.map((s) => ({ label: s.label, color: s.color })) : undefined}
            table={() => timeTable(series, unit, tz, h24, spec.digits)}>
            {empty
              ? <div className="text-sm text-muted py-6 text-center">No readings in this range yet.</div>
              : <TimeChart series={series} unit={unit} t0={t0} t1={t1} tz={tz} h24={h24} yMin={spec.yMin} yMax={spec.yMax} refs={spec.refs} markers={markers} digits={spec.digits} height={spec.height} />}
          </Figure>
        );
      })}
      {events.length > 0 && <EventList events={events} tz={tz} h24={h24} />}
    </div>
  );
}

export function EventList({ events, tz, h24 }: { events: DiagEvent[]; tz: string; h24: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? events : events.slice(0, 8);
  return (
    <div>
      <div className="text-sm font-medium mb-1">Events in this range <span className="text-xs text-muted font-normal">(the thin vertical lines on the charts)</span></div>
      <ul className="text-sm divide-y divide-border">
        {shown.map((e) => (
          <li key={`${e.kind}-${e.id}-${e.at}`} className="py-1 flex gap-3">
            <span className="text-muted tnum shrink-0 w-28">{new Intl.DateTimeFormat('en-AU', { timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: !h24 }).format(new Date(e.at))}</span>
            <span className="min-w-0 break-words">{eventLabel(e)}{e.kind === 'diag_action' && typeof e.message === 'string' ? <span className="text-muted"> · {e.message}</span> : null}</span>
          </li>
        ))}
      </ul>
      {events.length > 8 && <button type="button" className="btn btn-ghost btn-sm mt-1" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${events.length}`}</button>}
    </div>
  );
}
