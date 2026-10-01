// Mirrors core/dawn_core/state/ui.py. Keep in sync when the state model changes.

export type FaceMode = 'standby' | 'playing' | 'ringing' | 'countdown' | 'setup' | 'message' | 'lightwake';
export type SourceKind = 'dab' | 'chime' | 'url' | 'playlist' | 'airplay' | 'bluetooth' | 'none';

export interface FaceMessage { title: string; body: string; level: 'info' | 'warning' | 'error'; until: string | null }
export interface SetupInfo { ssid: string; password: string | null; url: string; qr_payload: string }
export interface FaceState {
  mode: FaceMode; message: FaceMessage | null; hint: string | null; menu_open: boolean; menu_page: string | null;
  wake_until: string | null; shutdown_countdown: number | null; setup: SetupInfo | null;
}
export interface DisplayState {
  brightness: number; target: number; mode: 'auto' | 'manual'; manual_until: string | null; night: boolean;
  palette: 'dark' | 'light' | 'night'; lux: number | null; sensor: string | null; sensor_found: boolean;
  layout: 'rect' | 'round'; low_cpu: boolean; overlay_dim: number; backlight_driver: string;
  sunrise: string | null; sunset: string | null; schedule_night: boolean; show_seconds: boolean;
}
export interface SinkInfo { id: string; name: string; description: string; kind: 'usb' | 'hifiberry' | 'headphones' | 'hdmi' | 'other'; active: boolean }
export interface EqState { enabled: boolean; bass_db: number; treble_db: number }
export interface SourceStatus { kind: SourceKind; priority: number; state: 'idle' | 'starting' | 'playing' | 'paused' | 'ducked' | 'error'; label: string; detail: string | null }
export interface AudioState {
  volume: number; muted: boolean; sink: SinkInfo | null; sinks: SinkInfo[]; pinned_sink: string | null; eq: EqState;
  active_source: SourceKind; sources: SourceStatus[]; volume_overlay_until: string | null; backend: string; audio_flowing: boolean;
}
export interface NowPlaying {
  source: SourceKind; title: string | null; artist: string | null; album: string | null; station: string | null; station_sid: string | null;
  logo_url: string | null; artwork_url: string | null; dls: string | null; slide_url: string | null; signal: number | null;
  codec: string | null; bitrate: number | null; url: string | null; started_at: string | null;
}
export interface AlarmSummary {
  id: number; label: string; enabled: boolean; time: string; repeat: string; days: number[]; source: string; volume: number;
  skip_next: boolean; skip_public_holidays: boolean; next_at: string | null; light_wake: boolean;
}
export interface NextAlarm { id: number; label: string; at: string; in_seconds: number }
export interface RingingInfo {
  kind: 'alarm' | 'nap'; alarm_id: number | null; label: string; started_at: string; snoozed_until: string | null;
  snooze_count: number; source: string; fallback: boolean; volume_target: number; ends_at: string | null;
}
export interface AlarmsState { items: AlarmSummary[]; next: NextAlarm | null; ringing: RingingInfo | null; on_leave_until: string | null; light_wake_active: boolean }
export interface TimerInfo { kind: 'sleep' | 'nap'; ends_at: string; total_s: number; remaining_s: number; fading: boolean }
export interface TimersState { sleep: TimerInfo | null; nap: TimerInfo | null; sleep_choices: number[]; nap_choices: number[] }
export interface DabService {
  sid: string; label: string; short_label: string; ensemble: string; ensemble_id: string; channel: string; bitrate: number | null;
  codec: string | null; pty: string | null; logo_url: string; signal: number | null; has_slide: boolean;
}
export interface ScanProgress { running: boolean; channel: string | null; index: number; total: number; found_services: number; found_ensembles: number; started_at: string | null }
export interface DabState {
  enabled: boolean; available: boolean; sdr_present: boolean; tuner: string | null; channel: string | null; ensemble: string | null;
  sync: boolean; snr: number | null; services: DabService[]; scan: ScanProgress; last_scan_at: string | null; service_state: string;
}
export interface Preset { id: number; label: string; source: string; logo_url: string | null; position: number }
export interface TimeSource { name: string; kind: 'gps' | 'dab' | 'ntp' | 'other'; state: string; selected: boolean; reach: number; last_rx_s: number | null; offset_ms: number | null; live: boolean }
export interface GpsInfo { available: boolean; fix: number; lat: number | null; lon: number | null; sats_used: number; sats_seen: number; time: string | null; device: string | null }
export interface TimeSourcesState {
  active: 'GPS' | 'DAB' | 'NTP' | 'none'; synced: boolean; system_offset_ms: number | null; stratum: number | null; sources: TimeSource[];
  gps: GpsInfo; dab_time_live: boolean; chrony_available: boolean; updated_at: string | null;
}
export interface WeatherState {
  available: boolean; stale: boolean; temperature: number | null; code: number | null; icon: string | null; description: string | null;
  is_day: boolean; t_min: number | null; t_max: number | null; sunrise: string | null; sunset: string | null; fetched_at: string | null;
  units: string; location_label: string | null;
}
export interface NetworkInfo { online: boolean; ip: string | null; ssid: string | null; interface: string | null; hotspot_active: boolean; hotspot_ssid: string | null; mdns_name: string | null }
export interface SystemState {
  model: string; hostname: string; cpu_temp_c: number | null; uptime_s: number; load1: number | null; mem_used_percent: number | null;
  sdr_present: boolean; sdr_tuner: string | null; panel: string; network: NetworkInfo; update_available: boolean; update_running: boolean;
  update_log: string | null; heartbeat_at: string | null; sim: boolean; version: string; git_rev: string | null; booted_at: string | null;
  config_error: string | null; services: Record<string, string>;
}
export interface BtDevice { address: string; name: string; paired: boolean; connected: boolean; trusted: boolean; icon: string | null; rssi: number | null }
export interface BluetoothState { available: boolean; powered: boolean; discoverable: boolean; discoverable_until: string | null; scanning: boolean; name: string; devices: BtDevice[]; connected: string | null; playing: boolean }
export interface AirPlayState { available: boolean; name: string; active: boolean; client: string | null; playing: boolean }
export interface SettingsSummary {
  timezone: string; holiday_region: string; holiday_scope: string; latitude: number; longitude: number; location_source: 'gps' | 'config';
  name: string; clock_24h: boolean; theme: string; auth_required: boolean;
}
export interface UIState {
  version: number; now: string; tz: string; face: FaceState; display: DisplayState; audio: AudioState; now_playing: NowPlaying;
  alarms: AlarmsState; timers: TimersState; dab: DabState; presets: Preset[]; time_sources: TimeSourcesState; weather: WeatherState;
  system: SystemState; bluetooth: BluetoothState; airplay: AirPlayState; settings: SettingsSummary;
}

export const EMPTY_STATE: UIState = {
  version: 0, now: '', tz: 'Australia/Sydney',
  face: { mode: 'standby', message: null, hint: null, menu_open: false, menu_page: null, wake_until: null, shutdown_countdown: null, setup: null },
  display: { brightness: 60, target: 60, mode: 'auto', manual_until: null, night: false, palette: 'dark', lux: null, sensor: null, sensor_found: false, layout: 'rect', low_cpu: false, overlay_dim: 0, backlight_driver: 'none', sunrise: null, sunset: null, schedule_night: false, show_seconds: false },
  audio: { volume: 35, muted: false, sink: null, sinks: [], pinned_sink: null, eq: { enabled: true, bass_db: 0, treble_db: 0 }, active_source: 'none', sources: [], volume_overlay_until: null, backend: 'sim', audio_flowing: false },
  now_playing: { source: 'none', title: null, artist: null, album: null, station: null, station_sid: null, logo_url: null, artwork_url: null, dls: null, slide_url: null, signal: null, codec: null, bitrate: null, url: null, started_at: null },
  alarms: { items: [], next: null, ringing: null, on_leave_until: null, light_wake_active: false },
  timers: { sleep: null, nap: null, sleep_choices: [15, 30, 45, 60, 90], nap_choices: [20, 30, 45, 60] },
  dab: { enabled: true, available: false, sdr_present: false, tuner: null, channel: null, ensemble: null, sync: false, snr: null, services: [], scan: { running: false, channel: null, index: 0, total: 0, found_services: 0, found_ensembles: 0, started_at: null }, last_scan_at: null, service_state: 'unknown' },
  presets: [],
  time_sources: { active: 'none', synced: false, system_offset_ms: null, stratum: null, sources: [], gps: { available: false, fix: 0, lat: null, lon: null, sats_used: 0, sats_seen: 0, time: null, device: null }, dab_time_live: false, chrony_available: false, updated_at: null },
  weather: { available: false, stale: false, temperature: null, code: null, icon: null, description: null, is_day: true, t_min: null, t_max: null, sunrise: null, sunset: null, fetched_at: null, units: 'celsius', location_label: null },
  system: { model: '', hostname: 'dawn', cpu_temp_c: null, uptime_s: 0, load1: null, mem_used_percent: null, sdr_present: false, sdr_tuner: null, panel: 'none', network: { online: false, ip: null, ssid: null, interface: null, hotspot_active: false, hotspot_ssid: null, mdns_name: null }, update_available: false, update_running: false, update_log: null, heartbeat_at: null, sim: false, version: '', git_rev: null, booted_at: null, config_error: null, services: {} },
  bluetooth: { available: false, powered: false, discoverable: false, discoverable_until: null, scanning: false, name: 'Dawn', devices: [], connected: null, playing: false },
  airplay: { available: false, name: 'Dawn', active: false, client: null, playing: false },
  settings: { timezone: 'Australia/Sydney', holiday_region: 'NSW', holiday_scope: 'statewide', latitude: 0, longitude: 0, location_source: 'config', name: 'Dawn', clock_24h: true, theme: 'dark', auth_required: false },
};
