import { useEffect, useState } from 'react';
import { Card } from '../../shared/components';
import { api } from '../../shared/api';
import { fmtDuration } from '../../shared/time';
import type { Check, NetworkFacts, SystemFacts } from './api';
import { fmtNum, Table } from './charts';
import { ActionButton, ago, CheckList, Facts, HistorySection, sinceIso, type ChartSpec } from './common';

interface Common { tz: string; h24: boolean; hours: number; onHours: (h: number) => void; onChanged: () => void }

export const NETWORK_HISTORY: ChartSpec[] = [
  { title: 'Online', metrics: [{ key: 'net.online' }], unit: '%', yMin: 0, yMax: 100, digits: 0, sub: 'share of checks that reached the internet' },
  { title: 'Wi-Fi signal', metrics: [{ key: 'net.wifi' }], unit: 'dBm', digits: 0, refs: [{ y: -70, label: '−70 dBm weak' }], sub: 'closer to 0 is stronger' },
];
export const DEVICES_HISTORY: ChartSpec[] = [
  { title: 'Audio flowing while playing', metrics: [{ key: 'audio.flowing' }], unit: '%', yMin: 0, yMax: 100, digits: 0 },
  { title: 'Room light', metrics: [{ key: 'display.lux' }], unit: 'lx', yMin: 0, digits: 1 },
  { title: 'Backlight', metrics: [{ key: 'display.backlight' }], unit: '%', yMin: 0, yMax: 100, digits: 0 },
];
export const SYSTEM_HISTORY: ChartSpec[] = [
  { title: 'CPU temperature', metrics: [{ key: 'sys.temp' }], unit: '°C', digits: 1, refs: [{ y: 80, label: '80 °C the Pi slows down' }] },
  { title: 'Load', metrics: [{ key: 'sys.load' }], unit: '', yMin: 0, digits: 2, sub: '1-minute load average' },
  { title: 'Memory used', metrics: [{ key: 'sys.mem' }], unit: '%', yMin: 0, yMax: 100, digits: 0 },
];

export function NetworkPanel({ facts, checks, ...c }: Common & { facts: NetworkFacts | null; checks: Check[] }) {
  const n = facts;
  return (
    <div className="space-y-4">
      <Card title="Checks"><CheckList checks={checks} onChanged={c.onChanged} /></Card>
      {n && (
        <Card title="Connection">
          <Facts rows={[
            ['Internet', n.online ? `reachable (${n.check_host})` : `not reachable (${n.check_host})`],
            ['Address', n.ip ? `${n.ip} on ${n.interface ?? '?'}` : 'none'],
            ['Wi-Fi network', n.ssid ?? (n.hotspot ? `setup hotspot ${n.hotspot_ssid ?? ''}` : '–')],
            ...Object.entries(n.wireless).map(([ifc, w]) => [`Signal (${ifc})`, `${w.level_dbm !== null ? `${fmtNum(w.level_dbm, 0)} dBm · ` : ''}link quality ${w.quality_percent}%`] as [string, string]),
            ['Default route', n.route ? `via ${n.route.gateway} on ${n.route.interface}` : 'none'],
            ['DNS servers', n.nameservers.join(', ') || 'none'],
            ['DNS lookup', n.dns_addresses.length ? `${n.dns_host} → ${n.dns_addresses.slice(0, 3).join(', ')}` : `${n.dns_host}: ${n.dns_error ?? 'no answer'}`],
            ['Services', Object.entries(n.units).map(([u, s]) => `${u} ${s}`).join(' · ') || '–'],
            ['This clock', `http://${n.hostname}.local${n.port === 80 ? '' : `:${n.port}`}/`],
          ]} />
        </Card>
      )}
      <Card title="History"><HistorySection specs={NETWORK_HISTORY} hours={c.hours} onHours={c.onHours} tz={c.tz} h24={c.h24} area="network" /></Card>
    </div>
  );
}

const DEVICE_AREAS: [string, string][] = [['audio', 'Audio'], ['airplay', 'AirPlay'], ['bluetooth', 'Bluetooth'], ['display', 'Display'], ['inputs', 'Knob and buttons'], ['weather', 'Weather']];

export function DevicesPanel({ facts, checks, ...c }: Common & { facts: SystemFacts | null; checks: Check[] }) {
  const s = facts;
  return (
    <div className="space-y-4">
      {DEVICE_AREAS.map(([area, title]) => {
        const list = checks.filter((x) => x.area === area);
        const offered = new Set(list.flatMap((x) => x.actions));  // a fix a check already offers is not repeated below
        return (
          <Card key={area} title={title}>
            <CheckList checks={list} onChanged={c.onChanged} />
            {s && area === 'audio' && (
              <div className="mt-3 space-y-3">
                <Facts rows={[
                  ['Backend', s.audio.backend],
                  ['Output', s.audio.sink ? `${s.audio.sink.description} (${s.audio.sink.kind})${s.audio.pinned ? ' · pinned' : ''}` : 'none'],
                  ['Other outputs', s.audio.sinks.filter((k) => !k.active).map((k) => k.description).join(', ') || 'none'],
                  ['Volume', `${s.audio.volume}%${s.audio.muted ? ' (muted)' : ''} · limit ${s.audio.max_volume}% · output ceiling ${s.audio.ceiling}%`],
                  ['Playing', s.audio.active_source === 'none' ? 'nothing' : `${s.audio.active_source} · ${s.audio.flowing ? 'audio flowing' : 'no audio yet'}`],
                  ['Bass/treble filter', s.audio.eq_present ? 'loaded' : 'not loaded'],
                ]} />
                {!offered.has('audio.test_tone') && <ActionButton id="audio.test_tone" label="Play a test tone" onDone={c.onChanged} />}
              </div>
            )}
            {s && area === 'airplay' && (
              <div className="mt-3 space-y-3">
                <Facts rows={[
                  ['AirPlay', s.airplay.enabled ? `${s.airplay.available ? 'running' : 'not running'}${s.airplay.name ? ` as “${s.airplay.name}”` : ''}` : 'turned off'],
                  ['Streaming now', s.airplay.active ? 'yes' : 'no'],
                  ['Track info pipe', s.airplay.pipe ? 'connected' : 'not connected'],
                ]} />
                {!offered.has('airplay.restart') && <ActionButton id="airplay.restart" label="Restart AirPlay" onDone={c.onChanged} />}
              </div>
            )}
            {s && area === 'bluetooth' && (
              <div className="mt-3">
                <Facts rows={[
                  ['Bluetooth', s.bluetooth.enabled ? (s.bluetooth.available ? (s.bluetooth.powered ? 'on' : 'adapter off') : 'no adapter') : 'turned off'],
                  ['Paired devices', String(s.bluetooth.paired)],
                  ['Connected', s.bluetooth.connected ?? 'nothing'],
                ]} />
              </div>
            )}
            {s && area === 'display' && (
              <div className="mt-3 space-y-3">
                <Facts rows={[
                  ['Panel', `${s.display.panel} · backlight ${s.display.backlight}${s.display.sysfs ? ` (${s.display.sysfs}, ${s.display.sysfs_writable ? 'writable' : 'not writable'})` : ''}`],
                  ['Brightness', `${s.display.brightness}% · ${s.display.mode}${s.display.night ? ' · night palette' : ''}`],
                  ['Light sensor', s.display.sensor_found ? `${s.display.sensor ?? 'found'} · ${s.display.lux !== null ? `${fmtNum(s.display.lux, 1)} lx` : '–'}` : 'not found'],
                  ['Face', `showing ${s.display.face_mode}`],
                  ['Connected screens', s.display.clients.length ? s.display.clients.map((k) => `${k.role === 'face' ? 'the face' : k.role === 'control' ? 'a control page' : 'a browser'} from ${k.remote ?? '?'}, since ${new Intl.DateTimeFormat('en-AU', { timeZone: c.tz, hour: '2-digit', minute: '2-digit', hour12: !c.h24 }).format(new Date(k.connected_at))}`).join('; ') : 'none'],
                ]} />
                {!offered.has('display.restart_face') && <ActionButton id="display.restart_face" label="Restart the face kiosk" onDone={c.onChanged} />}
              </div>
            )}
            {s && area === 'inputs' && (
              <div className="mt-3">
                <Facts rows={[
                  ['Knob', s.inputs.encoder ? 'enabled' : 'disabled'],
                  ['Backend', s.inputs.backend],
                  ['Last input', s.inputs.last ? `${s.inputs.last.event} ${ago(s.inputs.last.age_s)} (${s.inputs.last.via === 'gpio' ? 'from the hardware' : 'from a screen or the web'})` : 'none since start'],
                ]} />
                <p className="text-xs text-muted mt-2">Turn the knob or press it, then look here: if “Last input” does not change, check the wiring.</p>
              </div>
            )}
            {s && area === 'weather' && (
              <div className="mt-3">
                <Facts rows={[
                  ['Weather', s.weather.enabled ? (s.weather.available ? `${s.weather.stale ? 'stale' : 'current'} · fetched ${sinceIso(s.weather.fetched_at)}` : 'no data yet') : 'turned off'],
                  ['Last attempt', sinceIso(s.weather.last_attempt)],
                  ...(s.weather.error ? [['Last error', s.weather.error] as [string, string]] : []),
                ]} />
              </div>
            )}
          </Card>
        );
      })}
      <Card title="History"><HistorySection specs={DEVICES_HISTORY} hours={c.hours} onHours={c.onHours} tz={c.tz} h24={c.h24} area="devices" /></Card>
    </div>
  );
}

export function SystemPanel({ facts, checks, ...c }: Common & { facts: SystemFacts | null; checks: Check[] }) {
  const s = facts?.system;
  const [logs, setLogs] = useState<{ source: string; lines: string[] } | null>(null);
  const load = () => api.get<{ source: string; lines: string[] }>('/api/system/logs?lines=150').then(setLogs).catch(() => {});
  useEffect(() => { load(); }, []);
  const gb = (b: number) => `${fmtNum(b / 1e9, 1)} GB`;
  return (
    <div className="space-y-4">
      <Card title="Checks"><CheckList checks={checks} onChanged={c.onChanged} /></Card>
      {facts && (
        <Card title="Services">
          <Table head={['Service', 'State']} rows={Object.entries(facts.units).map(([u, st]) => [u, st])} />
        </Card>
      )}
      {s && (
        <Card title="Board">
          <Facts rows={[
            ['Model', s.model],
            ['Software', `dawn-core ${s.version}${s.git_rev ? ` (${s.git_rev})` : ''}${s.update_running ? ' · updating' : ''}`],
            ['Up', fmtDuration(s.uptime_s)],
            ['Load / memory', `${fmtNum(s.load1, 2)} · ${fmtNum(s.mem_used_percent, 0)}% used`],
            ['CPU temperature', s.cpu_temp_c !== null ? `${fmtNum(s.cpu_temp_c, 1)} °C` : '–'],
            ['Power', s.throttled === null ? '–' : s.throttled === 0 ? 'no under-voltage or throttling since boot' : `${s.throttle_flags.join(' · ')} (0x${s.throttled.toString(16)})`],
            ['Free space', [s.disk_data ? `data ${gb(s.disk_data.free)} of ${gb(s.disk_data.total)}` : null, s.disk_root ? `system ${gb(s.disk_root.free)} of ${gb(s.disk_root.total)}` : null].filter(Boolean).join(' · ') || '–'],
            ['Heartbeat', s.heartbeat_age_s !== null ? ago(s.heartbeat_age_s) : '–'],
            ...(s.config_error ? [['Config error', s.config_error] as [string, string]] : []),
          ]} />
          {s.errors.length > 0 && (
            <div className="mt-3">
              <div className="text-sm font-medium mb-1">Recent errors ({s.error_count})</div>
              <pre className="text-[11px] leading-snug bg-black/30 rounded-xl p-3 max-h-48 overflow-auto whitespace-pre-wrap break-all">{s.errors.map((e) => `${e.ts}  ${e.level}  ${e.logger}: ${e.msg}`).join('\n')}</pre>
            </div>
          )}
        </Card>
      )}
      <Card title="Logs" action={<button type="button" className="btn btn-sm" onClick={load}>Refresh</button>}>
        <pre className="text-[11px] leading-snug bg-black/30 rounded-xl p-3 max-h-96 overflow-auto whitespace-pre-wrap break-all">{logs ? logs.lines.join('\n') : 'Loading…'}</pre>
        {logs && <div className="text-xs text-faint mt-1">source: {logs.source}</div>}
      </Card>
      <Card title="History"><HistorySection specs={SYSTEM_HISTORY} hours={c.hours} onHours={c.onHours} tz={c.tz} h24={c.h24} area="system" /></Card>
    </div>
  );
}
