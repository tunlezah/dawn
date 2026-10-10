import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, ClockInput, Empty, Row, Sheet, Switch } from '../../shared/components';
import { api, actions } from '../../shared/api';
import { fmtDayTime } from '../../shared/time';
import { ringNote } from '../../shared/alarms';
import type { AlarmSummary, DabService } from '../../shared/types';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const STATES = ['ACT', 'NSW', 'NT', 'QLD', 'SA', 'TAS', 'VIC', 'WA'];

interface AlarmForm {
  label: string; enabled: boolean; time: string; repeat: string; days: number[]; skip_public_holidays: boolean;
  holiday_region: string | null; holiday_scope: string | null; source: string; volume: number; ramp_seconds: number;
  snooze_minutes: number; fallback_after_s: number; max_ring_minutes: number; skip_next: boolean; light_wake: boolean; light_wake_minutes: number;
}

const EMPTY: AlarmForm = {
  label: 'Alarm', enabled: true, time: '06:30', repeat: 'weekdays', days: [], skip_public_holidays: false, holiday_region: null, holiday_scope: null,
  source: 'chime:gentle_bell', volume: 70, ramp_seconds: 60, snooze_minutes: 9, fallback_after_s: 15, max_ring_minutes: 30, skip_next: false, light_wake: false, light_wake_minutes: 10,
};

function repeatLabel(a: { repeat: string; days: number[] }): string {
  if (a.repeat === 'custom') return a.days.length ? a.days.map((d) => DAYS[d]).join(' ') : 'Never';
  return a.repeat[0].toUpperCase() + a.repeat.slice(1);
}

function sourceLabel(src: string, services: DabService[]): string {
  const [kind, arg] = [src.split(':')[0], src.slice(src.indexOf(':') + 1)];
  if (kind === 'dab') return services.find((s) => s.sid === arg)?.label ?? `DAB ${arg.toUpperCase()}`;
  if (kind === 'chime') return `Chime · ${arg.replace('_', ' ')}`;
  if (kind === 'url') return `Stream · ${arg.replace(/^https?:\/\//, '')}`;
  if (kind === 'playlist') return `Playlist · ${arg}`;
  if (src === 'last-played') return 'Last played';
  return src;
}

export function Alarms() {
  const s = useState();
  const [editing, setEditing] = useReactState<(AlarmForm & { id?: number }) | null>(null);
  const [leave, setLeave] = useReactState(s.alarms.on_leave_until ?? '');
  useEffect(() => setLeave(s.alarms.on_leave_until ?? ''), [s.alarms.on_leave_until]);

  const openEdit = async (a?: AlarmSummary) => {
    if (!a) { setEditing({ ...EMPTY }); return; }
    const full = await api.get<AlarmForm & { id: number }>(`/api/alarms/${a.id}`);
    setEditing(full);
  };
  const save = async () => {
    if (!editing) return;
    const { id, ...body } = editing as AlarmForm & { id?: number; next_at?: string; last_fired_occurrence?: string };
    delete (body as Record<string, unknown>).next_at; delete (body as Record<string, unknown>).last_fired_occurrence;
    if (id) await api.put(`/api/alarms/${id}`, body); else await api.post('/api/alarms', body);
    setEditing(null);
  };
  const set = <K extends keyof AlarmForm>(k: K, v: AlarmForm[K]) => setEditing((e) => (e ? { ...e, [k]: v } : e));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Alarms</h1>
        <button className="btn btn-primary" onClick={() => openEdit()}>+ Add</button>
      </div>

      {s.alarms.ringing && (
        <Card className="border-accent">
          <div className="flex items-center justify-between gap-3">
            <div><div className="text-accent font-semibold">{s.alarms.ringing.snoozed_until ? 'Snoozed' : 'Ringing'} · {s.alarms.ringing.label}</div><div className="text-xs text-muted">{ringNote(s.alarms.ringing)} · snoozes {s.alarms.ringing.snooze_count}</div></div>
            <div className="flex gap-2">{!s.alarms.ringing.snoozed_until && <button className="btn" onClick={() => actions.snooze()}>Snooze</button>}<button className="btn btn-danger" onClick={() => actions.stopRinging()}>Stop</button></div>
          </div>
        </Card>
      )}

      <Card>
        {s.alarms.items.length === 0 && <Empty>No alarms yet.</Empty>}
        {s.alarms.items.map((a) => (
          <div key={a.id} className="flex items-center gap-3 py-3 border-b border-border last:border-0">
            <button className="flex-1 text-left min-w-0" onClick={() => openEdit(a)}>
              <div className="flex items-baseline gap-2">
                <span className={`text-3xl font-semibold tnum ${a.enabled ? '' : 'text-faint'}`}>{a.time}</span>
                <span className="text-sm text-muted truncate">{a.label}</span>
              </div>
              <div className="text-xs text-muted truncate">
                {repeatLabel(a)} · {sourceLabel(a.source, s.dab.services)} · {a.volume}%
                {a.skip_public_holidays && ' · skips holidays'}{a.light_wake && ' · light'}
                {a.enabled && a.next_at && <span className="text-faint"> · next {fmtDayTime(a.next_at, s.tz, s.settings.clock_24h)}</span>}
                {a.skip_next && <span className="text-warn"> · next skipped</span>}
              </div>
            </button>
            <button className={`btn btn-sm ${a.skip_next ? 'btn-primary' : 'btn-ghost'}`} title="Skip next" onClick={() => api.post(`/api/alarms/${a.id}/skip-next`)} disabled={!a.enabled}>skip</button>
            <Switch on={a.enabled} label={`Enable ${a.label}`} onChange={(v) => api.post(`/api/alarms/${a.id}/enabled`, { enabled: v })} />
          </div>
        ))}
      </Card>

      <Card title="On leave">
        <Row label="Suppress repeating alarms until" hint="Inclusive. One-off alarms still ring.">
          <input type="date" value={leave} onChange={(e) => setLeave(e.target.value)} className="w-40" />
          <button className="btn btn-sm" onClick={() => api.put('/api/alarms/leave', { until: leave || null })}>Set</button>
          {s.alarms.on_leave_until && <button className="btn btn-sm btn-ghost" onClick={() => api.put('/api/alarms/leave', { until: null })}>Clear</button>}
        </Row>
      </Card>

      <Sheet open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Edit alarm' : 'New alarm'}>
        {editing && (
          <div className="space-y-3">
            <div className="flex gap-3">
              <ClockInput label="Alarm time" value={editing.time} onChange={(v) => set('time', v)} h24={s.settings.clock_24h} className="text-xl shrink-0" />
              <input type="text" value={editing.label} onChange={(e) => set('label', e.target.value)} placeholder="Label" />
            </div>
            <div className="flex flex-wrap gap-2">
              {['once', 'weekdays', 'weekends', 'daily', 'custom'].map((r) => <button key={r} className={`chip ${editing.repeat === r ? 'text-accent border-accent' : ''}`} onClick={() => set('repeat', r)}>{r}</button>)}
            </div>
            {editing.repeat === 'custom' && (
              <div className="flex gap-1">{DAYS.map((d, i) => <button key={d} className={`chip ${editing.days.includes(i) ? 'text-accent border-accent' : ''}`} onClick={() => set('days', editing.days.includes(i) ? editing.days.filter((x) => x !== i) : [...editing.days, i].sort())}>{d}</button>)}</div>
            )}
            <Row label="Source">
              <select value={editing.source.startsWith('url:') || editing.source.startsWith('playlist:') ? '__custom' : editing.source} onChange={(e) => set('source', e.target.value === '__custom' ? 'url:' : e.target.value)} className="w-56">
                <optgroup label="Chime"><option value="chime:gentle_bell">Gentle bell</option><option value="chime:rising_synth">Rising synth</option><option value="chime:birds">Birds</option></optgroup>
                <option value="last-played">Last played</option>
                {s.dab.services.length > 0 && <optgroup label="DAB+">{s.dab.services.map((d) => <option key={d.sid} value={`dab:${d.sid}`}>{d.label}</option>)}</optgroup>}
                {s.presets.filter((p) => !p.source.startsWith('dab:') && !p.source.startsWith('chime:')).length > 0 && <optgroup label="Presets">{s.presets.filter((p) => !p.source.startsWith('dab:') && !p.source.startsWith('chime:')).map((p) => <option key={p.id} value={p.source}>{p.label}</option>)}</optgroup>}
                <option value="__custom">Stream URL / playlist…</option>
              </select>
            </Row>
            {(editing.source.startsWith('url:') || editing.source.startsWith('playlist:')) && <input type="text" value={editing.source} onChange={(e) => set('source', e.target.value)} placeholder="url:https://… or playlist:Name" />}
            <Row label={`Volume ${editing.volume}%`}><input type="range" min={1} max={100} value={editing.volume} onChange={(e) => set('volume', Number(e.target.value))} className="w-40" /></Row>
            <Row label="Ramp" hint="seconds from 10% to target"><input type="number" min={0} max={600} value={editing.ramp_seconds} onChange={(e) => set('ramp_seconds', Number(e.target.value))} className="w-24" /></Row>
            <Row label="Snooze minutes"><input type="number" min={1} max={60} value={editing.snooze_minutes} onChange={(e) => set('snooze_minutes', Number(e.target.value))} className="w-24" /></Row>
            <Row label="Skip public holidays" hint={editing.skip_public_holidays ? `${editing.holiday_region ?? s.settings.holiday_region} · ${(editing.holiday_scope ?? s.settings.holiday_scope).replace('_', ' ')}` : 'Repeating alarms only'}>
              <Switch on={editing.skip_public_holidays} onChange={(v) => set('skip_public_holidays', v)} />
            </Row>
            {editing.skip_public_holidays && (
              <div className="flex gap-2">
                <select value={editing.holiday_region ?? ''} onChange={(e) => set('holiday_region', e.target.value || null)}><option value="">Default ({s.settings.holiday_region})</option>{STATES.map((st) => <option key={st} value={st}>{st}</option>)}</select>
                <select value={editing.holiday_scope ?? ''} onChange={(e) => set('holiday_scope', e.target.value || null)}><option value="">Default scope</option><option value="statewide">Statewide only</option><option value="include_regional">Include regional</option></select>
              </div>
            )}
            <Row label="Light wake" hint="Screen to full white before the alarm"><input type="number" min={1} max={60} value={editing.light_wake_minutes} onChange={(e) => set('light_wake_minutes', Number(e.target.value))} className="w-20" disabled={!editing.light_wake} /><Switch on={editing.light_wake} onChange={(v) => set('light_wake', v)} /></Row>
            <details className="text-sm"><summary className="text-muted cursor-pointer">Advanced</summary>
              <Row label="Chime fallback after (s)" hint="without audio flowing"><input type="number" min={3} max={120} value={editing.fallback_after_s} onChange={(e) => set('fallback_after_s', Number(e.target.value))} className="w-24" /></Row>
              <Row label="Max ring (min)"><input type="number" min={1} max={180} value={editing.max_ring_minutes} onChange={(e) => set('max_ring_minutes', Number(e.target.value))} className="w-24" /></Row>
            </details>
            <div className="flex gap-2 pt-2">
              {editing.id && <button className="btn" onClick={() => api.post(`/api/alarms/${editing.id}/test`)}>Test ring</button>}
              {!editing.id && <button className="btn" onClick={() => api.post('/api/alarms/test', { source: editing.source, label: editing.label, volume: editing.volume, ramp_seconds: 0 })}>Test ring</button>}
              {editing.id && <button className="btn btn-ghost text-err" onClick={async () => { await api.del(`/api/alarms/${editing.id}`); setEditing(null); }}>Delete</button>}
              <div className="flex-1" />
              <button className="btn btn-primary" onClick={save}>Save</button>
            </div>
          </div>
        )}
      </Sheet>
    </div>
  );
}
