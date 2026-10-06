// Diagnostics API: response types and a polling hook that pauses while the tab is hidden and keeps the
// previous data on screen while it refetches.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../shared/api';
import type { TPoint } from './charts';

export type CheckStatus = 'ok' | 'warn' | 'fail' | 'info' | 'off';
export interface Check { id: string; area: string; title: string; status: CheckStatus; detail: string; hint: string | null; actions: string[]; group: string | null }

export interface Usb { vid: string; pid: string; product: string | null; manufacturer: string | null; path: string; power: string | null }
export interface DabService {
  sid: string; label: string; codec: string | null; bitrate: number | null; protection: string | null; subchannel: number | null;
  decoding: boolean; audio_format: string | null; samplerate: number | null; audio_level: number;
  frame_errors: number; rs_errors: number; aac_errors: number; rates: { frame: number; rs: number; aac: number; at: number } | null;
}
export interface DabFacts {
  enabled: boolean; sdr: { present: boolean; tuner: string | null; usb: Usb[] }; dvb_driver_loaded: boolean; unit: string | null; service_name: string;
  reachable: boolean; welle_url: string; cmdline: string | null; expected_args: string[]; arg_notes: string[]; gain_config: number | null;
  mux: {
    channel: string | null; mhz: number | null; ensemble: string | null; ensemble_id: string | null; sync: boolean; snr: number | null; signal: number;
    freq_correction_hz: number | null; fic_crc_errors: number | null; fic_errors_per_min: number | null; fct0_age_s: number | null; gain_db: number | null;
    hardware: string | null; software: string | null; tii: { comb: number; pattern: number; delay: number; delay_km: number; error: number }[];
    cir_peaks: { index: number; value: number }[]; utc_time: Record<string, number> | null;
  } | null;
  services: DabService[]; playing: { sid: string; label: string } | null; stations: number;
  ensembles: { channel: string; eid: string; label: string; snr: number | null; scanned_at: string }[];
  last_scan_at: string | null; scanning: boolean; presets_unknown: string[]; alarms_unknown: string[];
  restarts_24h: string[]; fallbacks_24h: { at: string }[]; messages: { at: string; text: string }[];
}
export interface Satellite { prn: number; gnss: string; el: number | null; az: number | null; ss: number | null; used: boolean; health: number | null }
export interface GpsDetail {
  mode: number; lat: number | null; lon: number | null; alt: number | null; time: string | null; sats_used: number; sats_seen: number; device: string | null;
  status: number | null; ept: number | null; eph: number | null; epv: number | null; hdop: number | null; vdop: number | null; pdop: number | null; tdop: number | null;
  satellites: Satellite[]; toff_ms: number | null; driver: string | null; subtype: string | null; bps: number | null; activated: string | null;
  gpsd_version: string | null; error: string | null; has_fix: boolean; snr_used_avg: number | null; snr_max: number | null; source: string;
  connected: boolean; connect_error: string | null; enabled: boolean;
}
export interface GpsFacts {
  enabled: boolean; configured_source: string; detail: GpsDetail; usb: Usb[]; paths: Record<string, boolean>; unit: string | null;
  last_msg_age_s: number | null; toff_age_s: number | null; location_source: string; prefer_gps: boolean; configured_distance_km: number | null;
}
export interface ChronySource {
  name: string; kind: string; refclock: boolean; state: string; state_label: string; reason: string; used: boolean; selected: boolean;
  reach: number; reach_count: number; last_rx_s: number | null; stratum: number | null; offset_ms: number | null; error_ms: number | null; live: boolean;
  stats: { samples: number; runs: number; span_s: number; freq_ppm: number; skew_ppm: number; offset_s: number; std_dev_s: number } | null;
  select: { state: string; options: string; effective: string; last_sample_s: number | null; score: number; interval_lo_s: number; interval_hi_s: number; leap: string } | null;
  ntp: { remote: string; stratum: number; poll_s: number; root_delay_s: number; root_dispersion_s: number; offset_s: number; peer_delay_s: number;
    peer_dispersion_s: number; response_time_s: number; tests: string; total_tx: number; total_rx: number; total_valid_rx: number } | null;
}
export interface TimedStatus {
  version: string; pid: number; started_at: string; updated_at: string; welle_url: string; welle_reachable: boolean; mode: string; synced: boolean;
  utctime_has_seconds: boolean; samples_written: number; last_sample_at: string | null; last_dab_time: string | null; last_offset_ms: number | null;
  last_fic_at: string | null; last_fig010_at: string | null; fig010_long: number; fig010_short: number; fibs: number; fib_crc_errors: number;
  shm_unit: number; shm_attached: boolean; shm_error: string | null; dry_run: boolean; last_error: string | null;
}
export interface TimeFacts {
  units: Record<string, string>; chronyc_error: string | null; select_error: string | null; ntpdata_error: string | null;
  tracking: { refid: string; refname: string; stratum: number; system_offset_s: number; last_offset_s: number; rms_s: number; leap: string; ref_time: number;
    freq_ppm: number; resid_freq_ppm: number; skew_ppm: number; root_delay_s: number; root_dispersion_s: number; update_interval_s: number } | null;
  synced: boolean; sources: ChronySource[]; activity: { online: number; offline: number; burst_online: number; burst_offline: number; unresolved: number } | null;
  shm: { key: number; unit: number; perms: string; size: number; nattch: number; cpid: number; lpid: number; uid: number }[];
  servers: string[]; online: boolean; timed: TimedStatus | null; timed_age_s: number | null; timed_error: string | null;
}
export interface NetworkFacts {
  online: boolean; ip: string | null; ssid: string | null; interface: string | null; hotspot: boolean; hotspot_ssid: string | null; check_host: string;
  route: { interface: string; gateway: string } | null; nameservers: string[]; dns_host: string; dns_addresses: string[]; dns_error: string | null;
  wireless: Record<string, { quality_percent: number; level_dbm: number | null }>; units: Record<string, string>; hostname: string; port: number;
}
export interface WsClient { remote: string | null; connected_at: string; role: string | null; agent: string | null }
export interface Sink { id: string; name: string; description: string; kind: string; active: boolean }
export interface SystemFacts {
  units: Record<string, string>;
  audio: { backend: string; sink: Sink | null; sinks: Sink[]; pinned: string | null; volume: number; muted: boolean; active_source: string; flowing: boolean; eq_present: boolean; ceiling: number; max_volume: number };
  airplay: { enabled: boolean; available: boolean; name: string | null; active: boolean; pipe: boolean };
  bluetooth: { enabled: boolean; available: boolean; powered: boolean; paired: number; connected: string | null };
  display: { panel: string; backlight: string; brightness: number; sysfs: string | null; sysfs_writable: boolean; sensor: string | null; sensor_found: boolean; lux: number | null;
    mode: string; night: boolean; face_mode: string; clients: WsClient[]; face_clients: WsClient[] };
  inputs: { encoder: boolean; button: boolean; backend: string; last: { event: string; age_s: number; via: string } | null };
  weather: { enabled: boolean; available: boolean; stale: boolean; fetched_at: string | null; error: string | null; last_attempt: string | null };
  system: { model: string; version: string; git_rev: string | null; uptime_s: number; load1: number | null; mem_used_percent: number | null; cpu_temp_c: number | null;
    throttled: number | null; throttle_flags: string[]; heartbeat_age_s: number | null; config_error: string | null; failed_services: Record<string, string>; sim: boolean;
    disk_data: { path: string; total: number; free: number } | null; disk_root: { total: number; free: number } | null; errors: { ts: string; level: string; logger: string; msg: string }[]; error_count: number; update_running: boolean };
}
export interface Facts { dab: DabFacts; gps: GpsFacts; time: TimeFacts; network: NetworkFacts; system: SystemFacts }
export interface Report { generated_at: string; counts: Record<CheckStatus, number>; checks: Check[]; facts: Facts }
export interface Live<F> { generated_at: string; facts: F; checks: Check[] }

export interface Plot { kind: string; available: boolean; n?: number; bins?: number[]; floor_db?: number; center_mhz?: number | null; span_mhz?: number;
  peak_index?: number; us_per_sample?: number; histogram?: number[]; phase_error_deg?: number | null; points?: number[] }
export interface HistoryMetric { label: string; unit: string; points: [string, number, number, number][] }
export interface DiagEvent { id: number; at: string; kind: string; [k: string]: unknown }
export interface History { hours: number; metrics: Record<string, HistoryMetric>; events: DiagEvent[] }

export const toPoints = (m: HistoryMetric | undefined): TPoint[] => (m?.points ?? []).map(([at, v, lo, hi]) => ({ t: Date.parse(at), v, lo, hi }));

/** Poll `path` every `ms` while the page is visible; `null` or `ms <= 0` pauses (nothing is fetched, the last data
 *  stays on screen). Only the newest request's answer is used: a slow older one never overwrites it. */
export function usePoll<T>(path: string | null, ms: number): { data: T | null; error: string | null; busy: boolean; reload: () => Promise<void> } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!path) return;
    const mine = ++seq.current;
    setBusy(true);
    try {
      const d = await api.get<T>(path);
      if (mine === seq.current) { setData(d); setError(null); }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [path]);
  useEffect(() => () => { seq.current++; }, []);  // answers arriving after unmount are dropped
  useEffect(() => {
    if (!path || ms <= 0) return;
    load();
    let stop = false;
    let timer = 0;
    const tick = async () => {
      if (stop) return;
      if (!document.hidden) await load();
      if (!stop) timer = window.setTimeout(tick, ms);
    };
    timer = window.setTimeout(tick, ms);
    return () => { stop = true; clearTimeout(timer); };
  }, [path, ms, load]);
  return { data, error, busy, reload: load };
}

export async function runAction(id: string, value?: unknown): Promise<{ ok: boolean; message: string }> {
  try {
    return await api.post<{ ok: boolean; message: string }>(`/api/diag/action/${id}`, value === undefined ? {} : { value });
  } catch (e) {
    return { ok: false, message: e instanceof ApiError ? e.message : String(e) };
  }
}
