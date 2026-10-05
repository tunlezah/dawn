# Acceptance checklist

Each item lists how it is covered in the simulator (automated or scripted) and
the on-device procedure. "sim" steps use `scripts/simctl.sh start` and the hub at
http://localhost:8099/.

- [x] **Weekday alarm set to a DAB+ station rings at 06:30, ramps 10%→70% over 60 s,
  skips a NSW public holiday, snoozes on screen tap, stops on a 2 s hold (or the big button).**
  - sim/tests: `core/tests/test_scheduler.py` (weekday/holiday/DST), `test_alarm_engine.py`
    (`test_holiday_skipped_in_engine`: Labour Day NSW 2026-10-05 is skipped, Tuesday fires),
    `test_snooze_and_stop_restore_volume`; ramp verified live (volume 10 → 36 → 60 over a 4 s test ramp);
    Playwright `face.spec.ts` "ringing: a tap snoozes, the button stops" and "ringing: holding the
    screen for 2 s stops without snoozing".
  - device: create the alarm in *Alarms* with source = station, volume 70, ramp 60, *Skip public
    holidays* (NSW). Use *Test ring* to hear the ramp; tap the screen (snooze), hold it 2 s (stop).

- [x] **Unplug the SDR: the same alarm rings the chime within 15 s of the trigger.**
  - sim: hub *SDR plugged → off*, then *Alarms → test ring* on the DAB alarm: the chime starts
    immediately (`ring_fallback reason="no SDR"`). With the SDR present but no audio flowing
    (hub *Audio flowing → off*) the chime takes over at `fallback_after_s` (15 s default; 4 s in the
    scripted check). welle-cli gets one restart attempt after 3 s when it is unreachable.
  - device: pull the stick, trigger the alarm, expect the chime ≤ 15 s; `journalctl -u dawn-core`
    shows `ring_fallback` and `dab_restart`.

- [x] **Cover the lux sensor: face dims to night palette within 3 s; uncover: returns within 3 s;
  slider override holds until sunrise.**
  - sim/tests: `test_brightness.py::test_night_threshold_scenario_within_three_seconds`; live check:
    lux 150 → 0.5 gives palette `night` within 1 s and brightness 6 % by 3.2 s; back to 150 restores
    within 3.2 s. Manual slider → `mode=manual`, `manual_until=<next sunrise>`. Playwright
    "night palette follows the light sensor".
  - device: cover the VEML6030; use the slider in *Display* or the face menu; *Display* shows
    "until sunrise".

- [x] **Pull the GPS: status shows DAB as active source within 2 min, then NTP if the SDR is also
  removed.**
  - sim: hub *GPS fix → off*: `time_sources.active` = DAB within one chrony poll; *SDR plugged → off*:
    active = NTP. `test_timesync.py` covers the chrony parsing and selection logic.
  - device: unplug the GPS; chrony reselects within its poll interval (≤ 2 min with `poll 4`);
    *Status → Time* shows the selected source and offsets.

- [x] **Play from an iPhone via AirPlay and from Android via Bluetooth; metadata appears on the face;
  an alarm interrupts both and playback resumes after stop.**
  - sim: hub *iPhone: AirPlay start* (title/artist/artwork on the face), *Android: Bluetooth play*
    (queued behind AirPlay), *Alarms → test ring* (both paused, Pause sent to the phone), stop →
    AirPlay resumes; AirPlay stop → Bluetooth resumes. `test_arbiter.py` and `test_airplay_meta.py`.
  - device: pair the Android phone from *Audio → Bluetooth → Pair new device*; choose "Dawn" in the
    iPhone AirPlay menu.
- [x] **The face control bar works by touch: ◀◀ ❚❚ ▶▶ control the phone, ◀ ☆ ▶ cycle and star
  presets; a tap opens the menu sheet with the volume slider and the Standby tile (hold 3 s to
  shut down); left alone for `display.ambient_after_s` the player gives way to the ambient
  clock and a tap brings it back.**
  - sim: Playwright `face.spec.ts` "playing: the control bar stars the station…", "menu sheet:
    the slider sets the volume and the Standby tile is the big button", "menu sheet: holding
    Standby shows the shutdown countdown…" and "AirPlay: artwork, device, progress and
    transport; idle switches to the ambient clock".
  - device: tap the star while a station plays → it appears under *Radio → Presets*; pause from
    the bar → the phone pauses; wait 20 s → ambient clock with the track in the strip.

- [x] **Face reaches standby within 40 s of power-on with no network.**
  - design: `dawn-core` starts every service independently (failures are logged, not fatal), the
    weather/hotspot/chrony paths are non-blocking, `dawn-face` waits only for `/api/health`; the
    setup hotspot appears after `wait_for_network_s` (45 s) without blocking standby.
  - device: boot with Wi-Fi disabled; the clock shows within ~40 s on a Pi 4 (Chromium start
    dominates); measure with `systemd-analyze blame`.

- [x] **Same image boots on a Pi 3B+ with the HyperPixel fallback and on a Pi 5 with the DSI panel.**
  - design: `install.sh` detects the board and panel (DRM connectors, config.txt), writes
    `vc4-kms-dpi-hyperpixel4` or `vc4-kms-dsi-7inch` (Waveshare 43H); the backlight driver is
    chosen at runtime (sysfs / PWM / overlay); low-power boards get the low-CPU face. The code uses
    `lgpio` so GPIO works on Pi 3/4/5/Zero 2 W.
  - device: run `sudo ./deploy/install.sh` on each board (`--display hyperpixel4` if the panel is not
    yet attached at install time) and check *Status → Hardware*.

## On-device checks: reference hardware

For the final build (Pi 4, Waveshare 43H DSI, Audio Amp SHIM + PC68-4, VEML6030, Adafruit
377 encoder, no big button, one 5 V 3 A supply), after `sudo ./deploy/install.sh --audio
hifiberry` and a reboot. The sim covers the logic (`core/tests/test_hardware_profile.py`);
these need the hardware.

- [ ] **DAB, AirPlay, Bluetooth and the chime fallback all play through the SHIM.**
  - *Audio → Output* shows the I2S amp as active and pinned; play each source in turn, then
    pull the SDR and trigger a test ring on a DAB alarm to hear the chime.
- [ ] **No pop when a stream starts or stops, or when the alarm hands over to the chime.**
  - start/stop a station, pause AirPlay, and run a DAB test ring with the SDR unplugged.
    `wpctl inspect <hardware sink id>` shows `session.suspend-timeout-seconds = 0`.
- [ ] **Mono downmix works: a left-only and a right-only test tone are both audible.**
  - `speaker-test -D pipewire -c 2 -t sine -s 1` then `-s 2` (as user dawn, with
    `XDG_RUNTIME_DIR=/run/user/$(id -u dawn)`); both channels sound from the one speaker.
- [ ] **At maximum volume loud content doesn't audibly clip, and the alarm is clearly loud
  enough at 1 m.**
  - calibrate `audio.output_ceiling_percent` as in the README, then play bass-heavy music at
    100 and run a test ring at volume 100 with the case closed.
- [ ] **Encoder: exactly one step per detent in each direction; a press registers on GPIO22.**
  - turn one detent at a time and watch the volume overlay change by `volume_step` (2); a
    full turn is 24 steps. Wrong direction: set `inputs.encoder.invert`. Push = next preset.
- [ ] **Backlight covers the full 0–255 range, and auto-dimming follows the VEML6030.**
  - `cat /sys/class/backlight/*/max_brightness` is 255; *Display* slider at 1 % and 100 %
    reads 3 and 255 in `brightness`; cover the sensor window and the face dims.
    *Status → Hardware → Light sensor* shows veml6030 at 0x10.
- [ ] **Touch lines up with the display after any rotation.**
  - with `display.rotation` set and the installer re-run, tap the four corner tiles of the
    menu sheet.
- [ ] **No phantom button events with nothing connected to GPIO23.**
  - leave it for an hour with `journalctl -u dawn-core -f`: no `button_down`/`button_up`,
    no shutdown countdown.
- [ ] **Load test: DAB playing, the face running and an alarm at maximum volume give
  `vcgencmd get_throttled` = `0x0`.**
  - run it for 10 minutes; *Status → Hardware → Power and throttling* stays green. Check the
    CPU temperature there too (passive heatsink).

## Automated test inventory

| Area | Tests |
|---|---|
| Config schema, hot reload, API | `test_config.py`, `test_api_smoke.py` |
| Scheduling: DST gap/overlap, leap day, skip-next, holidays, leave, grace | `test_scheduler.py`, `test_holidays.py`, `test_alarm_engine.py` |
| Arbiter priority, duck/pause/resume | `test_arbiter.py` |
| Input semantics (button/encoder/touch, hold countdown) | `test_input_controller.py` |
| Reference hardware: volume ceiling, EQ limits, sink pinning, throttle flags, encoder on mock GPIO | `test_hardware_profile.py` |
| Brightness curve, hysteresis, slew, 3 s scenario | `test_brightness.py` |
| Time sources: chrony parsing, NMEA | `test_timesync.py` |
| DAB: mux parsing, SID normalisation, scan order, monograms | `test_dab.py` |
| dawn-timed: FIC/FIG 0/10 round trip, SHM layout, utctime | `dawn-timed/tests/test_fic_shm.py` |
| Weather WMO mapping | `test_weather.py` |
| AirPlay metadata pipe | `test_airplay_meta.py` |
| Wi-Fi parsing, backup/restore | `test_net_backup.py` |
| UI smoke (face + control) against the simulator | `web/e2e/*.spec.ts` |
