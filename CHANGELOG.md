# Changelog

All notable changes to Dawn are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-02

First complete build: all ten phases of the Dawn specification.

### Added
- Phase 10: Playwright smoke tests for the face and the control UI against the
  simulator, GitHub Actions CI (pytest, ruff, web build, e2e), full README with
  wiring table, first-boot steps and troubleshooting, acceptance checklist with
  verification steps, `scripts/simctl.sh`.
- Phase 9: `deploy/install.sh` (board/panel detection, apt packages, rtl-sdr-blog
  + welle.io + shairport-sync/nqptp builds, config.txt overlays, user/groups,
  venv, web build, data partition/image with data=journal, optional read-only
  root), systemd units with watchdogs, cage/Chromium kiosk, sudoers/polkit/
  udev/logrotate/avahi, setup hotspot with QR, Wi-Fi management, hostname,
  backup/restore JSON, git-based software update.
- Phase 8: AirPlay 2 (shairport-sync metadata pipe parser, title/artist/artwork
  on the face, sender pause/resume over D-Bus, stream mute) and Bluetooth (BlueZ
  D-Bus backend with just-works agent, pairing/unpairing from the web UI,
  auto-reconnect, AVRCP metadata, A2DP playback into the arbiter), sim-hub
  phone simulation, PipeWire EQ filter chain, WirePlumber Bluetooth policy and
  shairport-sync configs.
- Phase 7: weather (Open-Meteo, WMO icon mapping, cache with stale marker),
  Settings page (device, location, holidays, Wi-Fi, backup/restore, update,
  schema-driven editor for every option, power), About page, face polish
  (light-wake screen, DLS marquee, round display variant), canned weather in the
  simulator.
- Phase 6: time sources. chrony status parsing (sources/tracking, live flags,
  active reference), gpsd JSON client with serial NMEA fallback, GPS position
  feeding sunrise/weather, dawn-timed daemon (edge-detected utctime, FIC FIG 0/10
  fallback, SHM 2 writer), chrony/gpsd/udev deploy configs, Status page with
  per-source offsets and a logs tail, time-source dots on the face.
- Phase 5: brightness controller (10 Hz, piecewise-linear curve, hysteresis,
  2 s slew), VEML6030/VEML7700/BH1750 drivers with auto-detect, sysfs/HyperPixel
  PWM/overlay backlight drivers, sunrise/sunset via astral, night palette with
  lux and schedule modes, post-sunset cap, manual override until sunrise,
  standby wake and light-wake levels, Display page with curve editor.
- Phase 4: alarm engine (pure scheduler with DST/leap/holiday/skip-next/leave
  handling, 1 Hz tick with grace window and missed-alarm log, once-alarms
  self-disable), AU public holidays with regional scope, ring sessions (ramp,
  chime fallback with DAB restart-once, snooze, max ring, volume restore),
  light-wake, nap and sleep timers, alarm/timer API, Alarms and Timers pages.
- Phase 3: DAB+ via welle-cli (HTTP client, SID normalisation, sync detection),
  Band III scan with Australian-capital priority and progress, stored ensembles
  and services, playback through mpv, DLS and MOT slides on the face, slide-based
  logos with SVG monogram fallback, presets with drag-to-order, Radio page.
- Phase 2: audio arbiter with strict priorities (alarm > sleep > user > AirPlay >
  Bluetooth), duck-then-pause and resume-previous; PipeWire/ALSA/sim backends;
  mpv player wrapper; bundled chimes (gentle bell, rising synth, birds); master
  volume with encoder steps and 1.5 s face overlay; two-band EQ; sink priority
  and pinning; KY-040 encoder + big button via gpiozero/lgpio with the full
  control semantics; face state machine (standby/playing/menu/wake/message/
  shutdown countdown); Home quick actions and Audio page.
- Phase 1: repository scaffold, validated `config.yaml` schema with hot reload,
  simulators (GPS/NMEA + fake gpsd, fake welle-cli with canned ensemble, DLS and
  MOT slides, lux sensor, encoder/button keyboard, backlight printer), core API
  skeleton with full-state WebSocket, face UI showing the time.
