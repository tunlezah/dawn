import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Row, Switch, Spinner, Empty } from '../../shared/components';
import { api, ApiError } from '../../shared/api';
import { ConfigEditor } from '../components/ConfigEditor';

const STATES = ['ACT', 'NSW', 'NT', 'QLD', 'SA', 'TAS', 'VIC', 'WA'];
const ZONES = ['Australia/Sydney', 'Australia/Melbourne', 'Australia/Brisbane', 'Australia/Adelaide', 'Australia/Perth', 'Australia/Hobart', 'Australia/Darwin', 'Australia/Canberra', 'Australia/Broken_Hill', 'Australia/Lord_Howe', 'Pacific/Auckland', 'UTC'];

interface Cfg {
  general: { name: string; timezone: string; hostname: string; log_level: string };
  location: { latitude: number; longitude: number; elevation_m: number; prefer_gps: boolean; city_label: string };
  holidays: { region: string; scope: string };
  web: { auth: { pin: string | null } };
  display: { clock_24h: boolean };
}
interface WifiNet { ssid: string; signal: number; security: string; active: boolean }

export function Settings() {
  const s = useState();
  const [cfg, setCfg] = useReactState<Cfg | null>(null);
  const [draft, setDraft] = useReactState<Partial<Cfg['location']> & { name?: string; hostname?: string; city_label?: string }>({});
  const [pin, setPin] = useReactState('');
  const [wifi, setWifi] = useReactState<WifiNet[] | null>(null);
  const [wifiErr, setWifiErr] = useReactState<string | null>(null);
  const [wifiBusy, setWifiBusy] = useReactState(false);
  const [join, setJoin] = useReactState<{ ssid: string; psk: string } | null>(null);
  const [msg, setMsg] = useReactState<string | null>(null);
  const reload = () => api.get<Cfg>('/api/config').then((c) => { setCfg(c); setDraft({}); });
  useEffect(() => { reload(); }, []);
  const patch = async (p: Record<string, unknown>) => { try { await api.patch('/api/config', p); await reload(); setMsg('Saved'); setTimeout(() => setMsg(null), 1500); } catch (e) { setMsg(e instanceof ApiError ? `Error: ${JSON.stringify(e.detail)}` : String(e)); } };
  const scanWifi = async () => { setWifiBusy(true); setWifiErr(null); try { setWifi(await api.get<WifiNet[]>('/api/wifi/networks')); } catch (e) { setWifiErr(e instanceof ApiError && e.status === 404 ? 'Wi-Fi management is not available in this build.' : String(e)); } finally { setWifiBusy(false); } };
  const restore = async (file: File) => { const text = await file.text(); try { await api.post('/api/system/restore', JSON.parse(text)); setMsg('Restored. Services apply the new settings now.'); reload(); } catch (e) { setMsg(`Restore failed: ${e instanceof ApiError ? e.message : String(e)}`); } };

  if (!cfg) return <div className="p-6"><Spinner /></div>;
  const loc = { ...cfg.location, ...draft };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1 className="text-2xl font-semibold">Settings</h1>{msg && <span className="chip">{msg}</span>}</div>

      <Card title="Device">
        <Row label="Name" hint="AirPlay, Bluetooth and the face" stack><input type="text" className="w-full" value={draft.name ?? cfg.general.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} onBlur={() => draft.name !== undefined && draft.name !== cfg.general.name && patch({ general: { name: draft.name } })} /></Row>
        <Row label="Hostname" hint={`reachable as ${cfg.general.hostname}.local`} stack><input type="text" className="w-full" value={draft.hostname ?? cfg.general.hostname} onChange={(e) => setDraft({ ...draft, hostname: e.target.value })} onBlur={() => draft.hostname !== undefined && draft.hostname !== cfg.general.hostname && api.put('/api/system/hostname', { hostname: draft.hostname }).then(reload).catch((e) => setMsg(String(e.message)))} /></Row>
        <Row label="Time zone"><select className="w-56" value={cfg.general.timezone} onChange={(e) => patch({ general: { timezone: e.target.value } })}>{[...new Set([cfg.general.timezone, ...ZONES])].map((z) => <option key={z}>{z}</option>)}</select></Row>
        <Row label="24-hour clock"><Switch on={cfg.display.clock_24h} onChange={(v) => patch({ display: { clock_24h: v } })} /></Row>
        <Row label="Control UI PIN" hint={cfg.web.auth.pin ? 'PIN required on the LAN' : 'Open on the LAN (default)'} stack>
          <input type="password" inputMode="numeric" placeholder="new PIN" className="w-28" value={pin} onChange={(e) => setPin(e.target.value)} />
          <button className="btn btn-sm" onClick={() => { patch({ web: { auth: { pin: pin || null } } }); setPin(''); }}>{pin ? 'Set' : 'Clear'}</button>
        </Row>
      </Card>

      <Card title="Location" action={<span className="chip">{s.settings.location_source === 'gps' ? 'using GPS fix' : 'using these values'}</span>}>
        <Row label="Prefer GPS position" hint="Falls back to the values below without a fix"><Switch on={cfg.location.prefer_gps} onChange={(v) => patch({ location: { prefer_gps: v } })} /></Row>
        <div className="grid grid-cols-2 gap-2 py-2">
          <label className="text-sm text-muted">Latitude<input type="number" step="0.0001" value={loc.latitude} onChange={(e) => setDraft({ ...draft, latitude: Number(e.target.value) })} /></label>
          <label className="text-sm text-muted">Longitude<input type="number" step="0.0001" value={loc.longitude} onChange={(e) => setDraft({ ...draft, longitude: Number(e.target.value) })} /></label>
          <label className="text-sm text-muted">Label<input type="text" value={draft.city_label ?? cfg.location.city_label} onChange={(e) => setDraft({ ...draft, city_label: e.target.value })} /></label>
          <label className="text-sm text-muted">Elevation (m)<input type="number" value={loc.elevation_m} onChange={(e) => setDraft({ ...draft, elevation_m: Number(e.target.value) })} /></label>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-sm btn-primary" onClick={() => patch({ location: { latitude: loc.latitude, longitude: loc.longitude, elevation_m: loc.elevation_m, city_label: draft.city_label ?? cfg.location.city_label } })}>Save location</button>
          {s.time_sources.gps.lat !== null && <button className="btn btn-sm" onClick={() => patch({ location: { latitude: Number(s.time_sources.gps.lat!.toFixed(4)), longitude: Number(s.time_sources.gps.lon!.toFixed(4)) } })}>Copy current GPS fix</button>}
        </div>
      </Card>

      <Card title="Public holidays">
        <Row label="State" hint="Default for alarms that skip public holidays"><select className="w-28" value={cfg.holidays.region} onChange={(e) => patch({ holidays: { region: e.target.value } })}>{STATES.map((st) => <option key={st}>{st}</option>)}</select></Row>
        <Row label="Regional holidays" hint="e.g. Royal Queensland Show (Brisbane only)"><select className="w-44" value={cfg.holidays.scope} onChange={(e) => patch({ holidays: { scope: e.target.value } })}><option value="statewide">Statewide only</option><option value="include_regional">Include regional</option></select></Row>
      </Card>

      <Card title="Wi-Fi" action={<button className="btn btn-sm" onClick={scanWifi} disabled={wifiBusy}>{wifiBusy ? <Spinner /> : 'Scan'}</button>}>
        <div className="text-sm text-muted mb-2">{s.system.network.online ? `Connected: ${s.system.network.ssid ?? s.system.network.interface ?? ''} · ${s.system.network.ip ?? ''}` : s.system.network.hotspot_active ? `Setup hotspot "${s.system.network.hotspot_ssid}" is active` : 'Offline'}</div>
        {wifiErr && <div className="text-sm text-warn">{wifiErr}</div>}
        {wifi && wifi.length === 0 && <Empty>No networks found.</Empty>}
        {wifi?.map((n) => (
          <Row key={n.ssid} label={n.ssid || '(hidden)'} hint={`${n.signal}% · ${n.security || 'open'}${n.active ? ' · connected' : ''}`}>
            {!n.active && <button className="btn btn-sm" onClick={() => setJoin({ ssid: n.ssid, psk: '' })}>Join</button>}
          </Row>
        ))}
        {join && (
          <form className="flex gap-2 mt-3" onSubmit={async (e) => { e.preventDefault(); try { await api.post('/api/wifi/connect', join); setMsg(`Joining ${join.ssid}…`); setJoin(null); } catch (err) { setMsg(String((err as Error).message)); } }}>
            <span className="self-center text-sm">{join.ssid}</span><input type="password" placeholder="password" value={join.psk} onChange={(e) => setJoin({ ...join, psk: e.target.value })} /><button className="btn btn-sm btn-primary">Connect</button><button type="button" className="btn btn-sm btn-ghost" onClick={() => setJoin(null)}>✕</button>
          </form>
        )}
      </Card>

      <Card title="Backup and restore">
        <Row label="Backup" hint="config.yaml + alarms, presets and settings as one JSON file"><a className="btn btn-sm" href="/api/system/backup" download={`dawn-backup-${new Date().toISOString().slice(0, 10)}.json`}>Download</a></Row>
        <Row label="Restore" hint="Replaces the configuration and database contents"><label className="btn btn-sm cursor-pointer">Choose file<input type="file" accept="application/json" className="hidden" onChange={(e) => e.target.files?.[0] && restore(e.target.files[0])} /></label></Row>
      </Card>

      <Card title="Software update" action={<span className="chip">{s.system.update_running ? 'running…' : s.system.update_available ? 'update available' : `v${s.system.version}${s.system.git_rev ? ` · ${s.system.git_rev}` : ''}`}</span>}>
        <Row label="Update from git" hint="git pull + install.sh, then services restart"><button className="btn btn-sm btn-primary" disabled={s.system.update_running} onClick={() => api.post('/api/system/update').catch((e) => setMsg(String(e.message)))}>Update now</button><button className="btn btn-sm" onClick={() => api.post('/api/system/update/check').catch(() => {})}>Check</button></Row>
        {s.system.update_log && <pre className="text-[11px] bg-black/40 rounded-xl p-3 max-h-48 overflow-auto whitespace-pre-wrap mt-2">{s.system.update_log}</pre>}
      </Card>

      <Card title="All options" action={<span className="text-xs text-muted">every config.yaml value</span>}>
        <ConfigEditor onSaved={reload} />
      </Card>

      <Card title="Power">
        <div className="flex gap-2">
          <button className="btn" onClick={() => confirm('Reboot now?') && api.post('/api/system/reboot')}>Reboot</button>
          <button className="btn btn-danger" onClick={() => confirm('Shut down now? Unplug only after the screen goes dark.') && api.post('/api/system/shutdown')}>Shut down</button>
        </div>
      </Card>
    </div>
  );
}
