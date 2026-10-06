"""The full UI state pushed over the WebSocket on every change.

This is the contract between core and both web targets. Keep it flat-ish and
JSON-friendly. Timestamps are ISO-8601 strings with offset.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FaceMode = Literal["standby", "sleep", "playing", "ringing", "countdown", "setup", "message", "lightwake"]
SourceKind = Literal["dab", "chime", "url", "playlist", "airplay", "bluetooth", "buzzer", "none"]
RingTier = Literal["source", "chime", "buzzer"]  # an alarm's own source, then the chime, then the backup tone


class FaceMessage(BaseModel):
    title: str
    body: str = ""
    level: Literal["info", "warning", "error"] = "info"
    until: str | None = None


class FaceState(BaseModel):
    mode: FaceMode = "standby"
    message: FaceMessage | None = None
    hint: str | None = None
    menu_open: bool = False
    menu_page: str | None = None
    wake_until: str | None = None  # standby wake-to-full timer
    peek_until: str | None = None  # screen-off sleep: a tap shows the sleep clock until then
    shutdown_countdown: int | None = None
    setup: SetupInfo | None = None


class SetupInfo(BaseModel):
    ssid: str
    password: str | None = None
    url: str
    qr_payload: str


class DisplayState(BaseModel):
    brightness: int = 60  # what is currently applied (1-100)
    target: int = 60
    mode: Literal["auto", "manual"] = "auto"
    manual_until: str | None = None
    night: bool = False
    palette: Literal["dark", "light", "night"] = "dark"
    lux: float | None = None
    sensor: str | None = None  # driver name or None if not found
    sensor_found: bool = False
    layout: Literal["rect", "round"] = "rect"
    low_cpu: bool = False
    overlay_dim: float = 0.0  # 0..1 for HDMI software dimmer
    backlight_driver: str = "none"
    sunrise: str | None = None
    sunset: str | None = None
    schedule_night: bool = False
    show_seconds: bool = False
    ambient_after_s: int = 20  # playing face -> ambient clock after this many idle seconds (0 = never)
    scene: bool = True  # scenic ambient background
    # sleep mode: wanted by the planner; the face shows it in Standby (face.mode == "sleep")
    sleep: bool = False
    sleep_reason: str | None = None  # schedule | dark | manual
    sleep_enabled: bool = True
    sleep_next_start: str | None = None
    sleep_next_end: str | None = None
    sleep_screen_off: bool = False
    sleep_jump_s: int = 120
    sleep_level: int = 72  # % of the night amber the sleep clock is drawn at
    room: Literal["dark", "bright", "between"] | None = None  # as the sleep triggers see it (None = no sensor)
    # burn-in protection
    orbit: bool = True
    strip_autohide_s: int = 120
    scene_daily: bool = True


class SinkInfo(BaseModel):
    id: str
    name: str
    description: str
    kind: Literal["usb", "hifiberry", "headphones", "hdmi", "other"] = "other"
    active: bool = False


class EqState(BaseModel):
    enabled: bool = True
    bass_db: float = 0.0
    treble_db: float = 0.0
    bass_max_db: float = 0.0
    highpass_hz: float | None = None


class SourceStatus(BaseModel):
    kind: SourceKind
    priority: int
    state: Literal["idle", "starting", "playing", "paused", "ducked", "error"] = "idle"
    label: str = ""
    detail: str | None = None


class AudioState(BaseModel):
    volume: int = 35
    muted: bool = False
    sink: SinkInfo | None = None
    sinks: list[SinkInfo] = Field(default_factory=list)
    pinned_sink: str | None = None
    eq: EqState = EqState()
    active_source: SourceKind = "none"
    sources: list[SourceStatus] = Field(default_factory=list)
    volume_overlay_until: str | None = None
    backend: str = "sim"
    audio_flowing: bool = False


class NowPlaying(BaseModel):
    source: SourceKind = "none"
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    station: str | None = None
    station_sid: str | None = None
    logo_url: str | None = None
    artwork_url: str | None = None
    dls: str | None = None
    slide_url: str | None = None
    signal: int | None = None  # 0..100
    codec: str | None = None
    bitrate: int | None = None
    url: str | None = None
    started_at: str | None = None
    position_s: float | None = None  # track progress (AirPlay) sampled at position_at
    duration_s: float | None = None
    position_at: str | None = None  # ISO time of the sample; None while paused


class AlarmSummary(BaseModel):
    id: int
    label: str
    enabled: bool
    time: str
    repeat: str
    days: list[int] = Field(default_factory=list)
    source: str
    volume: int
    skip_next: bool = False
    skip_public_holidays: bool = False
    next_at: str | None = None
    light_wake: bool = False


class NextAlarm(BaseModel):
    id: int
    label: str
    at: str
    in_seconds: int
    light_wake_at: str | None = None  # when its light-wake starts, if it has one


class RingingInfo(BaseModel):
    kind: Literal["alarm", "nap"]
    alarm_id: int | None = None
    label: str
    started_at: str
    snoozed_until: str | None = None
    snooze_count: int = 0
    source: str
    fallback: bool = False  # not the alarm's own source: the chime or the backup tone
    tier: RingTier = "source"
    fallback_reason: str | None = None  # why it left the rung above
    audible: bool | None = None  # audio confirmed flowing on this rung (None: not known yet)
    face_beep: bool = False  # the face beeps too: core's own sound may not be getting out
    volume_target: int = 70
    ends_at: str | None = None


class AlarmPrep(BaseModel):
    """The next alarm's sound, checked and made ready in the minutes before it rings (alarm_defaults.prepare_minutes)."""

    alarm_id: int
    label: str
    at: str
    source: str
    ready: bool = False
    pending: bool = False  # not ready only because a step is under way (tuning; the radio in use until the alarm)
    problems: list[str] = Field(default_factory=list)
    start_tier: RingTier = "source"  # where it will start if nothing changes before it rings
    checked_at: str | None = None


class AlarmsState(BaseModel):
    items: list[AlarmSummary] = Field(default_factory=list)
    next: NextAlarm | None = None
    ringing: RingingInfo | None = None
    on_leave_until: str | None = None
    light_wake_active: bool = False
    prepare: AlarmPrep | None = None


class TimerInfo(BaseModel):
    kind: Literal["sleep", "nap"]
    ends_at: str
    total_s: int
    remaining_s: int
    fading: bool = False


class TimersState(BaseModel):
    sleep: TimerInfo | None = None
    nap: TimerInfo | None = None
    sleep_choices: list[int] = Field(default_factory=lambda: [15, 30, 45, 60, 90])
    nap_choices: list[int] = Field(default_factory=lambda: [20, 30, 45, 60])


class DabService(BaseModel):
    sid: str
    label: str
    short_label: str = ""
    ensemble: str = ""
    ensemble_id: str = ""
    channel: str = ""
    bitrate: int | None = None
    codec: str | None = None
    pty: str | None = None
    logo_url: str
    signal: int | None = None
    has_slide: bool = False


class ScanProgress(BaseModel):
    running: bool = False
    channel: str | None = None
    index: int = 0
    total: int = 0
    found_services: int = 0
    found_ensembles: int = 0
    started_at: str | None = None


class DabState(BaseModel):
    enabled: bool = True
    available: bool = False  # welle reachable
    sdr_present: bool = False
    tuner: str | None = None
    channel: str | None = None
    ensemble: str | None = None
    sync: bool = False
    snr: float | None = None
    services: list[DabService] = Field(default_factory=list)
    scan: ScanProgress = ScanProgress()
    last_scan_at: str | None = None
    service_state: str = "unknown"  # systemd state of dawn-dab


class Preset(BaseModel):
    id: int
    label: str
    source: str  # dab:<sid> | url:<url> | playlist:<name> | chime:<name>
    logo_url: str | None = None
    position: int = 0


class TimeSource(BaseModel):
    name: str  # GPS / DAB / NTP(<host>)
    kind: Literal["gps", "dab", "ntp", "other"] = "other"
    state: str = "?"  # chrony state char
    selected: bool = False
    reach: int = 0
    last_rx_s: int | None = None
    offset_ms: float | None = None
    live: bool = False


class GpsInfo(BaseModel):
    available: bool = False
    fix: int = 0  # 0 none, 2 2D, 3 3D
    lat: float | None = None
    lon: float | None = None
    sats_used: int = 0
    sats_seen: int = 0
    time: str | None = None
    device: str | None = None
    source: str = "none"  # gpsd | serial | none
    hdop: float | None = None
    snr_avg: float | None = None  # mean C/N0 (dBHz) of the satellites in use


class TimeSourcesState(BaseModel):
    active: Literal["GPS", "DAB", "NTP", "none"] = "none"
    synced: bool = False
    system_offset_ms: float | None = None
    stratum: int | None = None
    sources: list[TimeSource] = Field(default_factory=list)
    gps: GpsInfo = GpsInfo()
    dab_time_live: bool = False
    chrony_available: bool = False
    updated_at: str | None = None


class WeatherState(BaseModel):
    available: bool = False
    stale: bool = False
    temperature: float | None = None
    code: int | None = None
    icon: str | None = None  # icon id, e.g. "rain-day"
    description: str | None = None
    is_day: bool = True
    t_min: float | None = None
    t_max: float | None = None
    sunrise: str | None = None
    sunset: str | None = None
    fetched_at: str | None = None
    units: str = "celsius"
    location_label: str | None = None


class NetworkInfo(BaseModel):
    online: bool = False
    ip: str | None = None
    ssid: str | None = None
    interface: str | None = None
    hotspot_active: bool = False
    hotspot_ssid: str | None = None
    mdns_name: str | None = None


class SystemState(BaseModel):
    model: str = "unknown"
    hostname: str = "dawn"
    cpu_temp_c: float | None = None
    uptime_s: int = 0
    load1: float | None = None
    mem_used_percent: float | None = None
    throttled: int | None = None  # vcgencmd get_throttled bitmask (None = not a Pi / unavailable)
    throttle_flags: list[str] = Field(default_factory=list)
    sdr_present: bool = False
    sdr_tuner: str | None = None
    panel: str = "none"
    network: NetworkInfo = NetworkInfo()
    update_available: bool = False
    update_running: bool = False
    update_log: str | None = None
    heartbeat_at: str | None = None
    sim: bool = False
    version: str = "0.0.0"
    git_rev: str | None = None
    booted_at: str | None = None
    config_error: str | None = None
    services: dict[str, str] = Field(default_factory=dict)


class BtDevice(BaseModel):
    address: str
    name: str
    paired: bool = False
    connected: bool = False
    trusted: bool = False
    icon: str | None = None
    rssi: int | None = None


class BluetoothState(BaseModel):
    available: bool = False
    powered: bool = False
    discoverable: bool = False
    discoverable_until: str | None = None
    scanning: bool = False
    name: str = "Dawn"
    devices: list[BtDevice] = Field(default_factory=list)
    connected: str | None = None
    playing: bool = False


class AirPlayState(BaseModel):
    available: bool = False
    name: str = "Dawn"
    active: bool = False
    client: str | None = None
    playing: bool = False


class DiagProblem(BaseModel):
    id: str
    area: str
    title: str
    status: Literal["warn", "fail"]
    detail: str


class DiagnosticsSummary(BaseModel):
    """The latest background check run: counts and the worst few findings (full report at /api/diag)."""

    updated_at: str | None = None
    fail: int = 0
    warn: int = 0
    problems: list[DiagProblem] = Field(default_factory=list)


class SettingsSummary(BaseModel):
    timezone: str = "Australia/Sydney"
    holiday_region: str = "NSW"
    holiday_scope: str = "statewide"
    latitude: float = 0.0
    longitude: float = 0.0
    location_source: Literal["gps", "config"] = "config"
    name: str = "Dawn"
    clock_24h: bool = True
    theme: str = "dark"
    auth_required: bool = False


class UIState(BaseModel):
    version: int = 0
    now: str = ""
    tz: str = "Australia/Sydney"
    face: FaceState = FaceState()
    display: DisplayState = DisplayState()
    audio: AudioState = AudioState()
    now_playing: NowPlaying = NowPlaying()
    alarms: AlarmsState = AlarmsState()
    timers: TimersState = TimersState()
    dab: DabState = DabState()
    presets: list[Preset] = Field(default_factory=list)
    time_sources: TimeSourcesState = TimeSourcesState()
    weather: WeatherState = WeatherState()
    system: SystemState = SystemState()
    bluetooth: BluetoothState = BluetoothState()
    airplay: AirPlayState = AirPlayState()
    settings: SettingsSummary = SettingsSummary()
    diagnostics: DiagnosticsSummary = DiagnosticsSummary()
