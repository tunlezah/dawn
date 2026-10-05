import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Row, Slider, Switch, Empty } from '../../shared/components';
import { actions, api } from '../../shared/api';
import { BluetoothPanel } from '../components/BluetoothPanel';

const KIND_LABEL: Record<string, string> = { usb: 'USB DAC', hifiberry: 'I2S amp (hifiberry-dac)', headphones: '3.5 mm jack', hdmi: 'HDMI', other: 'Other' };

export function Audio() {
  const s = useState();
  const [vol, setVol] = useReactState(s.audio.volume);
  const [bass, setBass] = useReactState(s.audio.eq.bass_db);
  const [treble, setTreble] = useReactState(s.audio.eq.treble_db);
  const [airplayName, setAirplayName] = useReactState('');
  useEffect(() => setVol(s.audio.volume), [s.audio.volume]);
  useEffect(() => { setBass(s.audio.eq.bass_db); setTreble(s.audio.eq.treble_db); }, [s.audio.eq.bass_db, s.audio.eq.treble_db]);
  useEffect(() => { setAirplayName(s.airplay.name); }, [s.airplay.name]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Audio</h1>

      <Card title="Volume">
        <div className="flex items-center gap-3">
          <button className="btn btn-sm" onClick={() => actions.mute()}>{s.audio.muted ? '🔇' : '🔊'}</button>
          <Slider label="Master volume" value={vol} onChange={setVol} onCommit={(v) => actions.setVolume(v)} />
          <span className="tnum w-8 text-right">{vol}</span>
        </div>
        <p className="text-xs text-muted mt-2">Encoder steps of 2. Alarms use their own stored volume and restore this one afterwards. All volume is in software: 100 is the calibrated ceiling (<code>audio.output_ceiling_percent</code>), never above it.</p>
      </Card>

      <Card title="Output" action={<span className="chip">{s.audio.backend}</span>}>
        {s.audio.sinks.length === 0 && <Empty>No audio sinks found.</Empty>}
        <Row label="Automatic (priority order)" hint="USB › I2S amp › headphone jack › HDMI">
          <input type="radio" name="sink" checked={!s.audio.pinned_sink} onChange={() => api.put('/api/audio/sink', { name: null })} />
        </Row>
        {s.audio.sinks.map((k) => (
          <Row key={k.id} label={k.description} hint={`${KIND_LABEL[k.kind] ?? k.kind}${k.active ? ' · active' : ''}`}>
            <input type="radio" name="sink" checked={s.audio.pinned_sink === k.name} onChange={() => api.put('/api/audio/sink', { name: k.name })} />
          </Row>
        ))}
      </Card>

      <Card title="Tone" action={<Switch on={s.audio.eq.enabled} onChange={(v) => api.put('/api/audio/eq', { enabled: v })} label="EQ enabled" />}>
        <div className={s.audio.eq.enabled ? '' : 'opacity-40 pointer-events-none'}>
          <div className="flex items-center gap-3 py-2"><span className="w-14 text-sm text-muted">Bass</span><Slider label="Bass" min={-12} max={s.audio.eq.bass_max_db} step={0.5} value={Math.min(bass, s.audio.eq.bass_max_db)} onChange={setBass} onCommit={(v) => api.put('/api/audio/eq', { bass_db: v })} /><span className="tnum w-14 text-right text-sm">{bass > 0 ? '+' : ''}{bass} dB</span></div>
          <div className="flex items-center gap-3 py-2"><span className="w-14 text-sm text-muted">Treble</span><Slider label="Treble" min={-12} max={12} step={0.5} value={treble} onChange={setTreble} onCommit={(v) => api.put('/api/audio/eq', { treble_db: v })} /><span className="tnum w-14 text-right text-sm">{treble > 0 ? '+' : ''}{treble} dB</span></div>
        </div>
        <p className="text-xs text-muted mt-1">Two-band shelving EQ in a PipeWire filter chain (applies to every source).{s.audio.eq.bass_max_db === 0 ? ' Bass can be cut but not boosted: the small amp clips sooner with every dB of bass.' : ` Bass boost is limited to +${s.audio.eq.bass_max_db} dB.`}{s.audio.eq.highpass_hz !== null && ` A ${s.audio.eq.highpass_hz} Hz high-pass protects the speaker.`}</p>
      </Card>

      <Card title="AirPlay" action={<span className={`chip ${s.airplay.available ? '' : 'opacity-60'}`}>{s.airplay.available ? (s.airplay.active ? 'streaming' : 'ready') : 'unavailable'}</span>}>
        <Row label="Device name" hint="How Dawn appears in the AirPlay menu" stack>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); api.patch('/api/config', { airplay: { name: airplayName || null } }); }}>
            <input type="text" value={airplayName} onChange={(e) => setAirplayName(e.target.value)} className="w-40" />
            <button className="btn btn-sm">Save</button>
          </form>
        </Row>
        {s.airplay.client && <Row label="Connected" hint={s.airplay.client} />}
      </Card>

      <BluetoothPanel />

      <Card title="Sources (priority)">
        {s.audio.sources.length === 0 && <div className="text-muted text-sm">Nothing active. Priority: alarm › sleep timer › selected source › AirPlay › Bluetooth.</div>}
        {s.audio.sources.map((src) => <Row key={src.kind + src.priority} label={`${src.kind} · ${src.label}`} hint={src.detail ?? undefined}><span className="chip">{src.state}</span></Row>)}
      </Card>
    </div>
  );
}
