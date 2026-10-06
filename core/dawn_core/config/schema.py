"""The complete, validated schema for /etc/dawn/config.yaml.

Every tunable in Dawn lives here. The JSON Schema of this model is exposed at
/api/config/schema so the control UI can render an editor for any section.
Keep field descriptions short and user-facing: they are shown in the UI.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AUState = Literal["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------- #
# General
# --------------------------------------------------------------------------- #
class GeneralConfig(StrictModel):
    name: str = Field("Dawn", description="Device name, used for AirPlay, Bluetooth and mDNS.")
    timezone: str = Field("Australia/Sydney", description="IANA time zone, e.g. Australia/Melbourne.")
    hostname: str = Field("dawn", description="Linux hostname; advertised as <hostname>.local.")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    data_dir: str = Field("/var/lib/dawn", description="Writable state directory (SQLite, caches, media).")
    runtime_dir: str = Field("/run/dawn", description="Runtime directory for sockets and the heartbeat file.")

    @field_validator("timezone")
    @classmethod
    def _tz_exists(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:  # pragma: no cover - depends on tzdata
            raise ValueError(f"unknown time zone {v!r}") from e
        return v


class LocationConfig(StrictModel):
    latitude: float = Field(-33.8688, ge=-90, le=90, description="Fallback latitude when there is no GPS fix.")
    longitude: float = Field(151.2093, ge=-180, le=180, description="Fallback longitude when there is no GPS fix.")
    elevation_m: float = Field(0.0, description="Elevation in metres (for sunrise/sunset).")
    prefer_gps: bool = Field(True, description="Use the GPS position when a fix is available.")
    city_label: str = Field("Sydney", description="Label shown on the face next to the weather.")


class HolidayConfig(StrictModel):
    region: AUState = Field("NSW", description="Default Australian state for public holidays.")
    scope: Literal["statewide", "include_regional"] = Field(
        "statewide", description="Whether regional-only holidays (e.g. Royal Queensland Show) count."
    )
    extra_dates: list[str] = Field(
        default_factory=list, description="Additional holiday dates (YYYY-MM-DD) that alarms should skip."
    )
    exclude_names: list[str] = Field(
        default_factory=list, description="Holiday names (substring match) that should NOT count as holidays."
    )


# --------------------------------------------------------------------------- #
# Hardware: GPIO inputs
# --------------------------------------------------------------------------- #
class EncoderConfig(StrictModel):
    enabled: bool = True
    clk_pin: int = Field(17, ge=0, le=27, description="Encoder A (CLK) BCM pin; the encoder's middle pin goes to GND.")
    dt_pin: int = Field(27, ge=0, le=27, description="Encoder B (DT) BCM pin.")
    sw_pin: int = Field(22, ge=0, le=27, description="Encoder push-switch BCM pin (other side to GND).")
    volume_step: int = Field(2, ge=1, le=20, description="Volume change per detent.")
    long_press_s: float = Field(1.0, ge=0.3, le=5, description="Hold time for the nap-timer picker.")
    invert: bool = Field(False, description="Swap rotation direction (instead of swapping the A/B wires).")
    bounce_time_s: float = Field(0.005, ge=0, le=0.1)


class BigButtonConfig(StrictModel):
    enabled: bool = Field(False, description="Not fitted on the reference build; enable only with a button wired to the pin.")
    pin: int = Field(23, ge=0, le=27, description="Arcade button BCM pin (to GND, internal pull-up).")
    long_press_s: float = Field(3.0, ge=1, le=10, description="Hold time for safe shutdown.")
    bounce_time_s: float = Field(0.02, ge=0, le=0.2)


class InputsConfig(StrictModel):
    pin_factory: Literal["lgpio", "rpigpio", "pigpio", "native", "mock"] = Field(
        "lgpio", description="gpiozero pin factory. lgpio works on Pi 3/4/5/Zero 2 W."
    )
    encoder: EncoderConfig = EncoderConfig()
    big_button: BigButtonConfig = BigButtonConfig()
    standby_wake_s: int = Field(20, ge=3, le=120, description="Seconds the face stays bright after a wake tap.")
    touch_menu_timeout_s: int = Field(15, ge=3, le=60, description="Face menu auto-close delay.")


# --------------------------------------------------------------------------- #
# Display and brightness
# --------------------------------------------------------------------------- #
class CurvePoint(StrictModel):
    lux: float = Field(ge=0)
    brightness: int = Field(ge=1, le=100)


class BacklightConfig(StrictModel):
    driver: Literal["auto", "sysfs", "hyperpixel_pwm", "overlay", "sim", "none"] = Field(
        "auto", description="auto picks sysfs (DSI) > HyperPixel PWM > software overlay (HDMI)."
    )
    sysfs_path: str | None = Field(None, description="Override /sys/class/backlight/<name> directory.")
    pwm_pin: int = Field(19, ge=0, le=27, description="HyperPixel backlight PWM BCM pin.")
    pwm_frequency_hz: int = Field(1000, ge=100, le=20000)
    min_percent: int = Field(1, ge=0, le=100, description="Never drive the backlight below this.")
    max_percent: int = Field(100, ge=1, le=100)
    invert: bool = False


class LuxSensorConfig(StrictModel):
    driver: Literal["auto", "veml6030", "veml7700", "bh1750", "sim", "none"] = "auto"
    i2c_bus: int = Field(1, ge=0, le=20)
    address: int | None = Field(None, description="Override I2C address (VEML: 0x10 or 0x48, BH1750: 0x23/0x5C).")
    sample_hz: float = Field(10.0, ge=1, le=20, description="Lux sampling and brightness loop rate.")
    smoothing: float = Field(0.3, ge=0.0, le=1.0, description="EMA factor for lux (1 = no smoothing).")


class BrightnessConfig(StrictModel):
    mode: Literal["auto", "manual"] = "auto"
    manual_percent: int = Field(60, ge=1, le=100)
    curve: list[CurvePoint] = Field(
        default_factory=lambda: [
            CurvePoint(lux=0, brightness=1),
            CurvePoint(lux=3, brightness=6),
            CurvePoint(lux=20, brightness=20),
            CurvePoint(lux=100, brightness=45),
            CurvePoint(lux=400, brightness=80),
            CurvePoint(lux=1000, brightness=100),
        ],
        description="Piecewise-linear lux -> backlight % curve (sorted by lux).",
    )
    hysteresis_percent: int = Field(4, ge=0, le=30, description="Ignore target changes smaller than this.")
    slew_s: float = Field(2.0, ge=0, le=20, description="Seconds to glide from the current to the target level.")
    night_lux_threshold: float = Field(5.0, ge=0, description="Below this lux the face uses the night palette.")
    night_hysteresis_lux: float = Field(2.0, ge=0)
    post_sunset_cap_percent: int = Field(40, ge=1, le=100, description="Brightness cap in the hour after sunset.")
    post_sunset_cap_minutes: int = Field(60, ge=0, le=240)
    sunset_night_palette: bool = Field(True, description="Use the night palette from sunset to sunrise (schedule).")
    manual_override_until: Literal["sunrise", "forever"] = Field(
        "sunrise", description="Manual slider override lasts until the next sunrise, or forever."
    )
    standby_wake_percent: int = Field(100, ge=1, le=100)

    @field_validator("curve")
    @classmethod
    def _sorted(cls, v: list[CurvePoint]) -> list[CurvePoint]:
        if len(v) < 2:
            raise ValueError("curve needs at least two points")
        lux = [p.lux for p in v]
        if lux != sorted(lux):
            raise ValueError("curve points must be sorted by lux")
        return v


def _hhmm(v: str) -> str:
    try:
        h, m = (int(x) for x in v.strip().split(":"))
    except ValueError as e:
        raise ValueError(f"expected HH:MM, got {v!r}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"expected HH:MM, got {v!r}")
    return f"{h:02d}:{m:02d}"


class SleepConfig(StrictModel):
    """Sleep mode: the clock alone, small and dim, moving every couple of minutes. Shown in Standby only."""

    enabled: bool = Field(True, description="Use sleep mode at all.")
    start_at_time: bool = Field(True, description="Go to sleep at the bedtime every night (even with the lights on).")
    start: str = Field("22:30", description="Bedtime (HH:MM).")
    start_when_dark: bool = Field(True, description="Also go to sleep whenever the room goes dark (light sensor).")
    end_at_time: bool = Field(True, description="Wake the face at the morning time every day.")
    end: str = Field("06:30", description="Morning time (HH:MM).")
    end_before_alarm: bool = Field(True, description="Wake the face this many minutes before the next alarm (or its light-wake).")
    alarm_lead_minutes: int = Field(10, ge=0, le=120)
    end_when_bright: bool = Field(True, description="Wake the face when the room gets bright (light sensor).")
    dark_lux: float = Field(3.0, ge=0, description="Below this the room is dark.")
    dark_after_s: int = Field(60, ge=0, le=3600, description="... for this many seconds before the dark trigger counts.")
    bright_lux: float = Field(30.0, ge=0, description="Above this the room is bright.")
    bright_after_s: int = Field(20, ge=0, le=3600, description="... for this many seconds (car headlights do not count).")
    jump_every_s: int = Field(120, ge=10, le=3600, description="The sleep clock moves to a new place this often.")
    level_percent: int = Field(72, ge=10, le=100, description="Brightness of the sleep clock's amber, in the pixels (on top of the backlight).")
    backlight_percent: int | None = Field(None, ge=0, le=100, description="Backlight while asleep (null = the backlight minimum).")
    screen_off: bool = Field(False, description="Backlight fully off on a black screen while asleep; a tap shows the sleep clock for 10 s, a second tap the full face.")
    wake_percent: int = Field(15, ge=1, le=100, description="Brightness of a tap-to-wake while asleep or in the night palette.")

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time(cls, v: object) -> str:
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 24 * 60:
            v = f"{v // 60}:{v % 60}"  # YAML 1.1 reads an unquoted 22:30 as the number 1350 (base 60)
        if not isinstance(v, str):
            raise ValueError(f"expected HH:MM, got {v!r}")
        return _hhmm(v)

    @model_validator(mode="after")
    def _distinct(self) -> SleepConfig:
        if self.start == self.end:
            raise ValueError("sleep.start and sleep.end must differ")
        return self


class BurnInConfig(StrictModel):
    """Image-retention protection for the IPS panel (see docs/research/standby-display.md on the research branch)."""

    pixel_orbit: bool = Field(True, description="Drift the face's text a few pixels over time (±8 × 6 px, 1 px a minute): invisible, but no edge stays put.")
    strip_autohide_s: int = Field(120, ge=0, le=3600, description="In Standby, fade the bottom status strip after this many seconds without a touch (0 = never).")
    scene_daily: bool = Field(True, description="Redraw the background's hills and trees from the date, so their outline changes every day.")


class DisplayConfig(StrictModel):
    panel: Literal["auto", "waveshare_dsi", "hyperpixel4", "hdmi", "none"] = "auto"
    width: int = Field(800, ge=240, le=4096)
    height: int = Field(480, ge=240, le=4096)
    rotation: Literal[0, 90, 180, 270] = 0
    layout: Literal["rect", "round"] = Field("rect", description="Face layout variant.")
    theme: Literal["dark", "light"] = Field("dark", description="Theme for the control UI and daytime face.")
    low_cpu: Literal["auto", "on", "off"] = Field("auto", description="Reduce face animations (Zero 2 W / 3B+).")
    show_seconds: bool = False
    clock_24h: bool = True
    ambient_after_s: int = Field(20, ge=0, le=600, description="While playing, show the ambient clock after this many seconds without a touch (0 = never).")
    scene: bool = Field(True, description="Background image behind the clock in Standby and the ambient clock: sky for the time of day, weather and season (off in the night palette and in sleep mode).")
    sleep: SleepConfig = SleepConfig()
    burn_in: BurnInConfig = BurnInConfig()
    backlight: BacklightConfig = BacklightConfig()
    lux_sensor: LuxSensorConfig = LuxSensorConfig()
    brightness: BrightnessConfig = BrightnessConfig()
    kiosk_url: str = Field("http://127.0.0.1:8080/face", description="URL the dawn-face kiosk opens.")


# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #
class EqConfig(StrictModel):
    enabled: bool = True
    bass_db: float = Field(0.0, ge=-12, le=12)
    treble_db: float = Field(0.0, ge=-12, le=12)
    bass_hz: float = Field(120.0, ge=40, le=400)
    treble_hz: float = Field(6000.0, ge=2000, le=12000)
    bass_max_db: float = Field(
        0.0, ge=0, le=12,
        description="Most bass boost allowed. Every +6 dB needs 4x the amplifier power; keep 0 for the 3 W I2S amp.",
    )
    highpass_hz: float | None = Field(
        110.0, ge=20, le=300,
        description="2nd-order high-pass ahead of the tone controls, protecting a small driver (null = off).",
    )


class AudioConfig(StrictModel):
    backend: Literal["auto", "pipewire", "alsa", "sim"] = "auto"
    sink_priority: list[Literal["usb", "hifiberry", "headphones", "hdmi"]] = Field(
        default_factory=lambda: ["usb", "hifiberry", "headphones", "hdmi"],
        description="Boot-time sink selection order.",
    )
    pinned_sink: str | None = Field(
        None, description="Sink to always use: a PipeWire node.name, or a kind (usb, hifiberry, headphones, hdmi). null = auto.",
    )
    default_volume: int = Field(35, ge=0, le=100)
    max_volume: int = Field(100, ge=1, le=100, description="Highest volume any control, alarm ramp or chime may set.")
    output_ceiling_percent: int = Field(
        100, ge=1, le=100,
        description="Hardware sink level at volume 100. Calibrate on the device so 100 sits just below audible clipping.",
    )
    duck_percent: int = Field(20, ge=0, le=100, description="Duck level before pausing a lower-priority source.")
    duck_seconds: float = Field(2.0, ge=0, le=10)
    volume_overlay_s: float = Field(1.5, ge=0.2, le=10)
    eq: EqConfig = EqConfig()
    mpv_binary: str = "mpv"
    mpv_extra_args: list[str] = Field(default_factory=list)
    media_dir: str = Field("/var/lib/dawn/media", description="Local playlist files.")
    chime_dir: str | None = Field(None, description="Override bundled chime directory.")
    default_chime: Literal["gentle_bell", "rising_synth", "birds"] = "gentle_bell"


class AirPlayConfig(StrictModel):
    enabled: bool = True
    name: str | None = Field(None, description="Advertised name (null = general.name).")
    metadata_pipe: str = "/tmp/shairport-sync-metadata"
    dbus: bool = Field(True, description="Use the shairport-sync D-Bus interface for pause/resume.")


class BluetoothConfig(StrictModel):
    enabled: bool = True
    name: str | None = Field(None, description="Advertised name (null = general.name).")
    auto_reconnect: bool = True
    discoverable_timeout_s: int = Field(180, ge=30, le=600)
    adapter: str = "hci0"


# --------------------------------------------------------------------------- #
# DAB+
# --------------------------------------------------------------------------- #
class DabConfig(StrictModel):
    enabled: bool = True
    welle_url: str = Field("http://127.0.0.1:8000", description="welle-cli web interface base URL.")
    welle_binary: str = "welle-cli"
    welle_args: list[str] = Field(
        default_factory=lambda: ["-w", "8000"],
        description="Extra welle-cli args (channel and gain are added by the service). Add -C 1 -P to collect logos for every station in the background (one more programme decoded and MP3-encoded all the time).",
    )
    service_name: str = Field("dawn-dab", description="systemd unit that runs welle-cli.")
    default_channel: str = Field("9A", description="Channel to tune at boot if nothing was played before.")
    scan_priority: list[str] = Field(
        default_factory=lambda: ["9A", "9B", "9C", "8A", "8B", "8C", "8D"],
        description="Channels scanned first (Australian capitals).",
    )
    scan_channels: list[str] = Field(
        default_factory=lambda: [
            f"{n}{letter}" for n in range(5, 14) for letter in "ABCD"
        ] + ["13E", "13F"],
        description="All Band III channels considered by a scan.",
    )
    scan_dwell_s: float = Field(6.0, ge=2, le=30, description="Seconds to wait for sync on each channel.")
    sync_timeout_s: float = Field(12.0, ge=3, le=60, description="Seconds to wait for audio after tuning.")
    audio_timeout_s: float = Field(15.0, ge=3, le=120, description="No audio flowing for this long = failure.")
    gain: float | None = Field(None, description="Fixed tuner gain in dB, rounded to the nearest R820T/R828D step (null or negative = AGC).")
    poll_interval_s: float = Field(1.0, ge=0.2, le=10, description="How often /mux.json is read for DLS/MOT.")

    @field_validator("scan_channels", "scan_priority")
    @classmethod
    def _channels_upper(cls, v: list[str]) -> list[str]:
        return [c.upper() for c in v]


# --------------------------------------------------------------------------- #
# Alarms and timers
# --------------------------------------------------------------------------- #
class AlarmDefaults(StrictModel):
    volume: int = Field(70, ge=1, le=100)
    ramp_seconds: int = Field(60, ge=0, le=600)
    ramp_start_percent: int = Field(10, ge=1, le=100)
    snooze_minutes: int = Field(9, ge=1, le=60)
    fallback_after_s: int = Field(15, ge=3, le=120, description="Switch to the chime after this long without audio.")
    max_ring_minutes: int = Field(30, ge=1, le=180)
    missed_grace_minutes: int = Field(10, ge=0, le=60, description="Fire late if within this window after boot.")
    light_wake_minutes: int = Field(10, ge=1, le=60)
    chime: Literal["gentle_bell", "rising_synth", "birds"] = "gentle_bell"


class TimersConfig(StrictModel):
    sleep_choices_min: list[int] = Field(default_factory=lambda: [15, 30, 45, 60, 90])
    sleep_fade_s: int = Field(30, ge=0, le=300)
    nap_choices_min: list[int] = Field(default_factory=lambda: [20, 30, 45, 60])
    nap_default_min: int = Field(20, ge=1, le=240)
    nap_volume: int = Field(60, ge=1, le=100)
    nap_chime: Literal["gentle_bell", "rising_synth", "birds"] = "rising_synth"


# --------------------------------------------------------------------------- #
# Time sources, GPS, weather, network, system, web
# --------------------------------------------------------------------------- #
class GpsConfig(StrictModel):
    enabled: bool = True
    source: Literal["auto", "gpsd", "serial", "sim", "none"] = "auto"
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = Field(2947, ge=1, le=65535)
    serial_device: str = "/dev/ttyACM0"
    serial_baud: int = 9600


class TimeSourcesConfig(StrictModel):
    chrony_poll_s: int = Field(30, ge=5, le=600)
    chronyc_binary: str = "chronyc"
    stale_after_s: int = Field(600, ge=30, le=86400, description="Source considered stale after this long without samples.")
    gps: GpsConfig = GpsConfig()


class WeatherConfig(StrictModel):
    enabled: bool = True
    provider: Literal["open-meteo", "none"] = "open-meteo"
    base_url: str = "https://api.open-meteo.com/v1/forecast"
    interval_minutes: int = Field(15, ge=5, le=180)
    stale_after_hours: float = Field(2.0, ge=0.25, le=48)
    units: Literal["celsius", "fahrenheit"] = "celsius"


class HotspotConfig(StrictModel):
    enabled: bool = True
    ssid: str | None = Field(None, description="Hotspot SSID (null = <name>-<serial4>).")
    password: str = Field("dawnsetup", min_length=8, max_length=63)
    wait_for_network_s: int = Field(45, ge=5, le=600, description="Seconds without a network before the hotspot starts.")
    interface: str = "wlan0"


class NetworkConfig(StrictModel):
    hotspot: HotspotConfig = HotspotConfig()
    online_check_host: str = "1.1.1.1"
    online_check_interval_s: int = Field(30, ge=5, le=600)


class AuthConfig(StrictModel):
    pin: str | None = Field(None, description="Optional PIN for the control UI (null = open on the LAN).")
    session_hours: int = Field(24 * 30, ge=1)


class WebConfig(StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)
    static_dir: str | None = Field(None, description="Built web assets (null = bundled web/dist).")
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    auth: AuthConfig = AuthConfig()


class SystemConfig(StrictModel):
    heartbeat_interval_s: int = Field(5, ge=1, le=60)
    watchdog: bool = Field(True, description="Send systemd watchdog keepalives.")
    cpu_temp_path: str = "/sys/class/thermal/thermal_zone0/temp"
    vcgencmd_binary: str = Field("vcgencmd", description="Reads under-voltage / throttling flags on a Pi.")
    update_repo_dir: str = Field("/opt/dawn", description="Git checkout used by the software updater.")
    update_branch: str = "main"
    sudo_binary: str = "sudo"
    allow_shutdown: bool = True
    log_tail_lines: int = Field(200, ge=20, le=2000)


class DiagnosticsConfig(StrictModel):
    history_days: int = Field(7, ge=1, le=31, description="Days of signal and timing history kept for the Diagnostics graphs (one row a minute).")
    check_interval_s: int = Field(60, ge=15, le=3600, description="How often the checks behind the Diagnostics page run in the background.")
    timed_status_file: str = Field("/run/dawn-timed/status.json", description="Status file written by dawn-timed (DAB time to chrony).")


class SimConfig(StrictModel):
    hub_url: str = Field("http://127.0.0.1:8099", description="Sim hub base URL (used when DAWN_SIM=1).")


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #
class DawnConfig(StrictModel):
    """Root of config.yaml."""

    version: Annotated[int, Field(ge=1)] = 1
    general: GeneralConfig = GeneralConfig()
    location: LocationConfig = LocationConfig()
    holidays: HolidayConfig = HolidayConfig()
    inputs: InputsConfig = InputsConfig()
    display: DisplayConfig = DisplayConfig()
    audio: AudioConfig = AudioConfig()
    airplay: AirPlayConfig = AirPlayConfig()
    bluetooth: BluetoothConfig = BluetoothConfig()
    dab: DabConfig = DabConfig()
    alarm_defaults: AlarmDefaults = AlarmDefaults()
    timers: TimersConfig = TimersConfig()
    time_sources: TimeSourcesConfig = TimeSourcesConfig()
    weather: WeatherConfig = WeatherConfig()
    network: NetworkConfig = NetworkConfig()
    web: WebConfig = WebConfig()
    system: SystemConfig = SystemConfig()
    diagnostics: DiagnosticsConfig = DiagnosticsConfig()
    sim: SimConfig = SimConfig()

    @model_validator(mode="after")
    def _pins_unique(self) -> DawnConfig:
        pins = []
        if self.inputs.encoder.enabled:
            pins += [self.inputs.encoder.clk_pin, self.inputs.encoder.dt_pin, self.inputs.encoder.sw_pin]
        if self.inputs.big_button.enabled:
            pins.append(self.inputs.big_button.pin)
        if self.display.backlight.driver == "hyperpixel_pwm":
            pins.append(self.display.backlight.pwm_pin)
        if len(pins) != len(set(pins)):
            raise ValueError(f"GPIO pins must be unique, got {pins}")
        return self

    def airplay_name(self) -> str:
        return self.airplay.name or self.general.name

    def bluetooth_name(self) -> str:
        return self.bluetooth.name or self.general.name
