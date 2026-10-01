import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Dot, Row } from '../../shared/components';
import { api } from '../../shared/api';
import { fmtDuration, fmtShort } from '../../shared/time';

const STATE_LABEL: Record<string, string> = { '*': 'selected', '+': 'combined', '-': 'not combined', '?': 'unreachable', x: 'falseticker', '~': 'too variable' };

export function Status() {
  const s = useState();
  const [logs, setLogs] = useReactState<{ source: string; lines: string[] } | null>(null);
  const [auto, setAuto] = useReactState(false);
  const load = () => api.get<{ source: string; lines: string[] }>('/api/system/logs?lines=120').then(setLogs).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!auto) return; const id = setInterval(load, 3000); return () => clearInterval(id); }, [auto]);
  const ts = s.time_sources;
  const sys = s.system;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Status</h1>

      <Card title="Time" action={<span className={`chip ${ts.synced ? 'text-ok' : 'text-warn'}`}>{ts.synced ? `synced · ${ts.active}` : ts.chrony_available ? 'not synced' : 'chrony unavailable'}</span>}>
        <div className="flex gap-4 mb-3 text-sm">
          {(['GPS', 'DAB', 'NTP'] as const).map((k) => {
            const live = ts.sources.some((x) => x.kind === k.toLowerCase() && x.live);
            return <span key={k} className="flex items-center gap-1.5"><Dot state={live ? 'ok' : 'off'} />{k}{ts.active === k && <span className="text-xs text-accent">active</span>}</span>;
          })}
          {ts.system_offset_ms !== null && <span className="text-muted ml-auto tnum">system offset {ts.system_offset_ms.toFixed(2)} ms · stratum {ts.stratum}</span>}
        </div>
        {ts.sources.length === 0 && <div className="text-sm text-muted">No chrony sources reported.</div>}
        {ts.sources.map((src) => (
          <Row key={src.name} label={src.name} hint={`${src.kind.toUpperCase()} · ${STATE_LABEL[src.state] ?? src.state} · reach ${src.reach} · last ${src.last_rx_s !== null ? fmtDuration(src.last_rx_s) : '–'} ago`}>
            <span className={`tnum text-sm ${src.live ? '' : 'text-faint'}`}>{src.offset_ms !== null ? `${src.offset_ms > 0 ? '+' : ''}${src.offset_ms.toFixed(src.kind === 'gps' ? 3 : 1)} ms` : '–'}</span>
            <Dot state={src.live ? 'ok' : 'off'} />
          </Row>
        ))}
        <div className="text-xs text-faint mt-2">Updated {fmtShort(ts.updated_at, s.tz, s.settings.clock_24h)} · alarms use the system clock only</div>
      </Card>

      <Card title="GPS" action={<span className="chip">{ts.gps.fix >= 3 ? '3D fix' : ts.gps.fix === 2 ? '2D fix' : ts.gps.available ? 'no fix' : 'no receiver'}</span>}>
        <Row label="Position" hint={ts.gps.lat !== null ? `${ts.gps.lat.toFixed(5)}, ${ts.gps.lon?.toFixed(5)}` : 'using configured location'}><span className="text-sm text-muted">{s.settings.location_source}</span></Row>
        <Row label="Satellites" hint={`${ts.gps.sats_used} used of ${ts.gps.sats_seen} seen`} />
        <Row label="Device" hint={ts.gps.device ?? '–'} />
        {ts.gps.time && <Row label="GPS time" hint={ts.gps.time} />}
      </Card>

      <Card title="Hardware">
        <Row label="Board" hint={sys.model} />
        <Row label="Display panel" hint={`${sys.panel} · backlight ${s.display.backlight_driver}`} />
        <Row label="SDR" hint={sys.sdr_present ? (sys.sdr_tuner ?? 'RTL2832U') : 'not detected'}><Dot state={sys.sdr_present ? 'ok' : 'off'} /></Row>
        <Row label="Light sensor" hint={s.display.sensor ?? 'not found'}><Dot state={s.display.sensor_found ? 'ok' : 'off'} /></Row>
        <Row label="Audio" hint={`${s.audio.backend} · ${s.audio.sink?.description ?? 'no sink'}`} />
        <Row label="CPU temperature" hint={sys.cpu_temp_c !== null ? `${sys.cpu_temp_c.toFixed(1)} °C` : '–'}><Dot state={sys.cpu_temp_c !== null && sys.cpu_temp_c > 75 ? 'warn' : 'ok'} /></Row>
        <Row label="Uptime" hint={`${fmtDuration(sys.uptime_s)} · load ${sys.load1 ?? '–'} · mem ${sys.mem_used_percent ?? '–'}%`} />
        <Row label="Network" hint={sys.network.online ? `${sys.network.ssid ?? sys.network.interface ?? 'online'} · ${sys.network.ip ?? ''}` : sys.network.hotspot_active ? `hotspot ${sys.network.hotspot_ssid}` : 'offline'}><Dot state={sys.network.online ? 'ok' : 'off'} /></Row>
        <Row label="Software" hint={`dawn-core ${sys.version}${sys.git_rev ? ` (${sys.git_rev})` : ''} · heartbeat ${fmtShort(sys.heartbeat_at, s.tz, s.settings.clock_24h)}`} />
        {Object.keys(sys.services).length > 0 && <Row label="Services" hint={Object.entries(sys.services).map(([k, v]) => `${k}: ${v}`).join(' · ')} />}
        <Row label="DAB decoder" hint={`${s.dab.service_state} · ${s.dab.channel ?? ''} ${s.dab.sync ? `sync · SNR ${s.dab.snr?.toFixed(0)} dB` : 'no sync'}`}><Dot state={s.dab.sync ? 'ok' : s.dab.available ? 'warn' : 'off'} /></Row>
      </Card>

      <Card title="Logs" action={<div className="flex gap-2"><button className="btn btn-sm" onClick={load}>Refresh</button><button className={`btn btn-sm ${auto ? 'btn-primary' : ''}`} onClick={() => setAuto(!auto)}>Auto</button></div>}>
        <pre className="text-[11px] leading-snug bg-black/40 rounded-xl p-3 max-h-96 overflow-auto whitespace-pre-wrap break-all">{logs ? logs.lines.join('\n') : 'Loading…'}</pre>
        {logs && <div className="text-xs text-faint mt-1">source: {logs.source}</div>}
      </Card>
    </div>
  );
}
