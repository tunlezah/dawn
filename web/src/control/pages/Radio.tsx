import { useMemo, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Empty, Row, Spinner, Dot } from '../../shared/components';
import { actions, api } from '../../shared/api';
import type { DabService, Preset } from '../../shared/types';

function SignalBars({ value }: { value: number | null }) {
  const n = value === null ? 0 : value > 75 ? 4 : value > 50 ? 3 : value > 25 ? 2 : value > 5 ? 1 : 0;
  return <span className="inline-flex items-end gap-0.5 h-3" aria-label={`signal ${value ?? 0}%`}>{[1, 2, 3, 4].map((i) => <span key={i} className={`w-1 rounded-sm ${i <= n ? 'bg-ok' : 'bg-faint'}`} style={{ height: `${i * 25}%` }} />)}</span>;
}

export function Radio() {
  const s = useState();
  const [filter, setFilter] = useReactState('');
  const [dragId, setDragId] = useReactState<number | null>(null);
  const [addUrl, setAddUrl] = useReactState('');
  const np = s.now_playing;
  const scan = s.dab.scan;

  const groups = useMemo(() => {
    const m = new Map<string, DabService[]>();
    for (const svc of s.dab.services) {
      if (filter && !svc.label.toLowerCase().includes(filter.toLowerCase())) continue;
      const k = `${svc.channel} · ${svc.ensemble || 'Ensemble'}`;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(svc);
    }
    return [...m.entries()];
  }, [s.dab.services, filter]);

  const presetSources = new Set(s.presets.map((p) => p.source));
  const addPreset = (svc: DabService) => api.post('/api/presets', { label: svc.label, source: `dab:${svc.sid}` });
  const removePresetBySource = (src: string) => { const p = s.presets.find((x) => x.source === src); if (p) api.del(`/api/presets/${p.id}`); };
  const reorder = (from: number, to: number) => {
    const ids = s.presets.map((p) => p.id);
    const [moved] = ids.splice(ids.indexOf(from), 1);
    ids.splice(ids.indexOf(to), 0, moved);
    api.put('/api/presets/order', { ids });
  };
  const move = (p: Preset, dir: -1 | 1) => {
    const ids = s.presets.map((x) => x.id);
    const i = ids.indexOf(p.id), j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    api.put('/api/presets/order', { ids });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Radio</h1>
        <div className="flex items-center gap-2 text-xs text-muted">
          <Dot state={!s.dab.sdr_present ? 'err' : s.dab.sync ? 'ok' : 'warn'} />
          {!s.dab.sdr_present ? 'no SDR' : s.dab.sync ? `${s.dab.channel} · ${s.dab.ensemble ?? ''} · SNR ${s.dab.snr?.toFixed(0) ?? '–'} dB` : `${s.dab.channel ?? ''} no sync`}
        </div>
      </div>

      {np.source === 'dab' && (
        <Card className="border-accent/60">
          <div className="flex items-center gap-4">
            <img src={np.slide_url || np.logo_url || ''} alt="" className="w-20 h-20 rounded-xl object-cover bg-elev-2" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold truncate">{np.station}</div>
              <div className="text-sm text-muted min-h-[2.5rem]">{np.dls || '…'}</div>
              <div className="text-xs text-faint mt-1 flex items-center gap-2"><SignalBars value={np.signal} />{np.codec} {np.bitrate ? `${np.bitrate} kbps` : ''}</div>
            </div>
            <button className="btn btn-sm" onClick={() => actions.standby()}>Stop</button>
          </div>
        </Card>
      )}

      <Card title="Presets" action={<span className="text-xs text-muted">encoder push cycles these</span>}>
        {s.presets.length === 0 && <Empty>No presets. Tap ☆ on a station below.</Empty>}
        <ul>
          {s.presets.map((p, i) => (
            <li key={p.id} draggable onDragStart={() => setDragId(p.id)} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (dragId !== null && dragId !== p.id) reorder(dragId, p.id); setDragId(null); }}
              className={`flex items-center gap-3 py-2 border-b border-border last:border-0 ${dragId === p.id ? 'opacity-50' : ''}`}>
              <span className="text-faint cursor-grab select-none" title="drag to reorder">⋮⋮</span>
              <span className="text-xs text-muted w-4 tnum">{i + 1}</span>
              {p.logo_url ? <img src={p.logo_url} alt="" className="w-9 h-9 rounded-lg object-cover bg-elev-2" /> : <span className="w-9 h-9 rounded-lg bg-elev-2 flex items-center justify-center">📻</span>}
              <button className="flex-1 text-left truncate" onClick={() => actions.play(p.source)}>{p.label}<span className="block text-xs text-faint truncate">{p.source}</span></button>
              <button className="btn btn-ghost btn-sm" aria-label="Move up" onClick={() => move(p, -1)} disabled={i === 0}>↑</button>
              <button className="btn btn-ghost btn-sm" aria-label="Move down" onClick={() => move(p, 1)} disabled={i === s.presets.length - 1}>↓</button>
              <button className="btn btn-ghost btn-sm" aria-label="Remove preset" onClick={() => api.del(`/api/presets/${p.id}`)}>✕</button>
            </li>
          ))}
        </ul>
        <form className="flex gap-2 mt-3" onSubmit={(e) => { e.preventDefault(); if (addUrl) { api.post('/api/presets', { label: addUrl.replace(/^https?:\/\//, '').slice(0, 40), source: `url:${addUrl}` }); setAddUrl(''); } }}>
          <input type="url" placeholder="Add a stream URL as a preset…" value={addUrl} onChange={(e) => setAddUrl(e.target.value)} />
          <button className="btn btn-sm">Add</button>
        </form>
      </Card>

      <Card title="Stations" action={
        scan.running
          ? <button className="btn btn-sm" onClick={() => api.del('/api/dab/scan')}><Spinner /> {scan.channel ?? ''} {scan.index}/{scan.total} · cancel</button>
          : <button className="btn btn-sm btn-primary" onClick={() => api.post('/api/dab/scan')} disabled={!s.dab.sdr_present}>Scan</button>}>
        {scan.running && (
          <div className="mb-3">
            <div className="h-1.5 rounded-full bg-elev-2 overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${scan.total ? (scan.index / scan.total) * 100 : 0}%` }} /></div>
            <div className="text-xs text-muted mt-1">Found {scan.found_ensembles} ensembles, {scan.found_services} stations so far</div>
          </div>
        )}
        {s.dab.services.length > 8 && <input type="text" placeholder="Filter stations" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-3" />}
        {s.dab.services.length === 0 && !scan.running && <Empty>{s.dab.sdr_present ? 'No stations yet. Run a scan.' : 'Plug in the RTL-SDR to scan for DAB+ stations.'}</Empty>}
        {groups.map(([k, list]) => (
          <div key={k} className="mb-3">
            <div className="text-xs uppercase tracking-wide text-muted mb-1">{k}</div>
            {list.map((svc) => (
              <Row key={svc.sid} label={svc.label} hint={`${svc.codec ?? ''} ${svc.bitrate ? svc.bitrate + ' kbps' : ''} · ${svc.sid.toUpperCase()}`}>
                <SignalBars value={svc.signal} />
                <button className="btn btn-ghost btn-sm" aria-label="Preset" onClick={() => presetSources.has(`dab:${svc.sid}`) ? removePresetBySource(`dab:${svc.sid}`) : addPreset(svc)}>{presetSources.has(`dab:${svc.sid}`) ? '★' : '☆'}</button>
                <button className={`btn btn-sm ${np.station_sid === svc.sid ? 'btn-primary' : ''}`} onClick={() => api.post('/api/dab/play', { sid: svc.sid })}>
                  <img src={svc.logo_url} alt="" className="w-6 h-6 rounded object-cover" />{np.station_sid === svc.sid ? 'Playing' : 'Play'}
                </button>
              </Row>
            ))}
          </div>
        ))}
        {s.dab.last_scan_at && <div className="text-xs text-faint">Last scan {new Date(s.dab.last_scan_at).toLocaleString()}</div>}
      </Card>
    </div>
  );
}
