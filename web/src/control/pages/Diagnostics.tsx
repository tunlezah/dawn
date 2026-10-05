import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Spinner } from '../../shared/components';
import { api } from '../../shared/api';
import { usePoll, type Check, type CheckStatus, type Report } from '../diag/api';
import { CheckRow, StatusIcon, worst } from '../diag/common';
import { DabPanel } from '../diag/DabPanel';
import { GpsPanel } from '../diag/GpsPanel';
import { TimePanel } from '../diag/TimePanel';
import { DevicesPanel, NetworkPanel, SystemPanel } from '../diag/OtherPanels';

type Tab = 'overview' | 'dab' | 'gps' | 'time' | 'network' | 'devices' | 'system';
const TABS: { id: Tab; label: string; areas: string[] }[] = [
  { id: 'overview', label: 'Overview', areas: [] },
  { id: 'dab', label: 'DAB radio', areas: ['dab'] },
  { id: 'gps', label: 'GPS', areas: ['gps'] },
  { id: 'time', label: 'Time sync', areas: ['time'] },
  { id: 'network', label: 'Network', areas: ['network'] },
  { id: 'devices', label: 'Audio & devices', areas: ['audio', 'airplay', 'bluetooth', 'display', 'inputs', 'weather'] },
  { id: 'system', label: 'System', areas: ['system'] },
];
const AREA_TITLE: Record<string, string> = {
  dab: 'DAB radio', gps: 'GPS', time: 'Time sync', network: 'Network', audio: 'Audio', airplay: 'AirPlay', bluetooth: 'Bluetooth',
  display: 'Display', inputs: 'Knob and buttons', weather: 'Weather', system: 'System',
};

const fromHash = (): Tab => {
  const h = location.hash.replace('#', '') as Tab;
  return TABS.some((t) => t.id === h) ? h : 'overview';
};

export function Diagnostics() {
  const s = useState();
  const [tab, setTab] = useReactState<Tab>(fromHash());
  const [hours, setHours] = useReactState(24);
  const [running, setRunning] = useReactState(false);
  const report = usePoll<Report>('/api/diag', 15_000);
  useEffect(() => {
    const on = () => setTab(fromHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const go = (t: string) => {
    const id = (TABS.some((x) => x.id === t) ? t : TABS.find((x) => x.areas.includes(t))?.id ?? 'overview') as Tab;
    history.replaceState(null, '', id === 'overview' ? location.pathname : `#${id}`);
    setTab(id);
    window.scrollTo({ top: 0 });
  };
  const runNow = async () => {
    setRunning(true);
    try { await api.post('/api/diag/run'); await report.reload(); } finally { setRunning(false); }
  };
  const checks = report.data?.checks ?? [];
  const of = (areas: string[]) => checks.filter((c) => areas.includes(c.area));
  const common = { tz: s.tz, h24: s.settings.clock_24h, hours, onHours: setHours, onChanged: report.reload };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Diagnostics</h1>
        <div className="flex items-center gap-2">
          {report.data && <span className="text-xs text-muted">checked {new Date(report.data.generated_at).toLocaleTimeString('en-AU', { timeZone: s.tz, hour12: !s.settings.clock_24h })}</span>}
          <button type="button" className="btn btn-sm" onClick={runNow} disabled={running}>{running && <Spinner />}Check again</button>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto -mx-1 px-1 pb-1" role="tablist" aria-label="Diagnostics areas">
        {TABS.map((t) => {
          const st = t.id === 'overview' ? null : worst(of(t.areas));
          return (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => go(t.id)}
              className={`shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm ${tab === t.id ? 'bg-elev-2 text-fg font-medium' : 'text-muted hover:text-fg'}`}>
              {st && st !== 'ok' && st !== 'info' && report.data && <StatusIcon status={st} />}{t.label}
            </button>
          );
        })}
      </div>

      {report.error && !report.data && <Card><p className="text-sm text-err">Could not run the checks: {report.error}</p></Card>}
      {!report.data && !report.error && <div className="p-6"><Spinner /></div>}

      {report.data && tab === 'overview' && <Overview report={report.data} onGo={go} onChanged={report.reload} />}
      {tab === 'dab' && <DabPanel {...common} />}
      {tab === 'gps' && <GpsPanel {...common} />}
      {report.data && tab === 'time' && <TimePanel facts={report.data.facts.time} checks={of(['time'])} {...common} />}
      {report.data && tab === 'network' && <NetworkPanel facts={report.data.facts.network} checks={of(['network'])} {...common} />}
      {report.data && tab === 'devices' && <DevicesPanel facts={report.data.facts.system} checks={of(TABS[5].areas)} {...common} />}
      {report.data && tab === 'system' && <SystemPanel facts={report.data.facts.system} checks={of(['system'])} {...common} />}
    </div>
  );
}

function Overview({ report, onGo, onChanged }: { report: Report; onGo: (t: string) => void; onChanged: () => void }) {
  const problems = report.checks.filter((c) => c.status === 'fail').concat(report.checks.filter((c) => c.status === 'warn'));
  const areas = [...new Set(report.checks.map((c) => c.area))];
  const n = report.counts;
  return (
    <>
      <Card>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          {(['fail', 'warn', 'ok', 'info'] as CheckStatus[]).map((k) => (
            <span key={k} className="inline-flex items-center gap-2">
              <StatusIcon status={k} /><b className="tnum">{n[k] ?? 0}</b>
              <span className="text-muted">{k === 'fail' ? 'problems' : k === 'warn' ? 'warnings' : k === 'ok' ? 'fine' : 'notes'}</span>
            </span>
          ))}
        </div>
        <p className="text-xs text-muted mt-2">Each area follows its chain from the hardware up: a later step only makes sense once the earlier ones pass, so fix the first problem in a chain first.</p>
      </Card>

      <Card title={problems.length ? 'Needs attention' : 'All clear'}>
        {problems.length === 0
          ? <p className="text-sm text-muted">Every check passes. The tabs above show live readings and history if something still seems off.</p>
          : problems.map((c) => <ProblemRow key={c.id} c={c} onGo={onGo} onChanged={onChanged} />)}
      </Card>

      <Card title="Areas">
        <div className="divide-y divide-border">
          {areas.map((a) => {
            const list = report.checks.filter((c) => c.area === a);
            const bad = list.filter((c) => c.status === 'fail' || c.status === 'warn').length;
            return (
              <button key={a} type="button" onClick={() => onGo(a)} className="w-full flex items-center gap-3 py-2.5 text-left hover:bg-elev-2/40 rounded-lg px-1">
                <StatusIcon status={worst(list)} />
                <span className="flex-1">{AREA_TITLE[a] ?? a}</span>
                <span className="text-xs text-muted">{bad ? `${bad} to look at` : `${list.length} check${list.length === 1 ? '' : 's'} fine`}</span>
                <span className="text-muted" aria-hidden="true">›</span>
              </button>
            );
          })}
        </div>
      </Card>
    </>
  );
}

function ProblemRow({ c, onGo, onChanged }: { c: Check; onGo: (t: string) => void; onChanged: () => void }) {
  return (
    <div>
      <div className="text-xs text-muted pt-2 flex items-center justify-between">
        <span>{AREA_TITLE[c.area] ?? c.area}{c.group ? ` · ${c.group}` : ''}</span>
        <button type="button" className="text-xs text-accent hover:underline" onClick={() => onGo(c.area)}>Details ›</button>
      </div>
      <CheckRow c={c} onChanged={onChanged} onGo={onGo} />
    </div>
  );
}
