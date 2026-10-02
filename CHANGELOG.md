# Changelog

All notable changes to Dawn are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Touch-only operation: the encoder and big button are now optional. The face
  menu is a bottom sheet over the player (the clock and what is playing stay
  visible) with Presets · Nap · Sleep · Brightness · Standby and a volume
  slider; from standby the Standby tile reads *Radio on* and plays the first
  preset. The Standby tile sends the button's own down/up events, so a tap is
  the short press and a 3 s hold is the same shutdown countdown. While ringing,
  hold the screen 2 s to stop (a tap still snoozes). A tap outside the sheet
  closes it; a finger on the slider or a tile holds it open past the auto-close
  timeout (`POST /api/face/menu/activity`). The control bar no longer carries
  − 🔊 +: volume pops up with the sheet instead of being always on, and the
  volume overlay is suppressed while the sheet is open.
- Face redesign as one coherent design system (800×480 first, vmin-scaled):
  a fixed header slot (mode + live dot + DAB signal bars left, clock right), a
  40/60 artwork/identity layout, three explicit levels of type (primary near-
  white, secondary muted blue-grey, tertiary faint), barely-raised cards with a
  ~20 px radius and hairline dividers, and a persistent bottom control bar.
  AirPlay/Bluetooth get ◀◀ ❚❚ ▶▶, radio gets previous preset / ☆ star / next
  preset; both get − volume +. The DAB screen shows ensemble, programme type,
  codec/bitrate, DLS and a technical line (channel, Band III frequency,
  ensemble). Standby is an ambient clock with date, place + weather, next alarm
  and a status strip (time sources or now playing · alarm · volume). Menu tiles
  use SVG icons instead of emoji. Round layout restyled to match.
- Face screenshots are rendered at full brightness (the sim's HDMI software
  dimmer no longer greys the docs renders); new renders for AirPlay, ambient,
  presets and the round player, plus a 2×2 board for the README.

### Added
- Scenic standby background (`display.scene`, default on): a procedural SVG
  scene behind the ambient clock, composed from the time of day relative to the
  weather feed's sunrise/sunset (night, dawn, day, dusk sky), the weather icon
  (sun or moon and stars, soft clouds, fog, rain or snow) and the season for the
  hemisphere (summer greens, autumn ochres, snowy winter with pines, spring
  blossoms). A scrim keeps the clock legible; the status strip becomes glass.
  Off in the night palette; precipitation animation and blur filters are
  dropped in low-CPU mode. The face accepts `?at=…&weather=…&temp=…` demo
  parameters so any moment can be rendered; the screenshot script uses them
  for a 12-scene board.
- `display.ambient_after_s` (default 20, 0 = never): while playing, the face
  becomes the ambient clock with a now-playing strip after this many seconds
  without a touch; a touch or a new track brings the player back. Editable in
  *Display → Face*.
- AirPlay track progress: the shairport-sync `prgr` (RTP start/current/end)
  and `astm` (duration) metadata items are parsed into `now_playing.position_s`
  / `duration_s` / `position_at`; the face extrapolates while playing and
  freezes on pause. The sim phone emits them too.
- `POST /api/presets/prev` (previous preset) alongside `/next`; the face's star
  button adds/removes the current station as a preset.
- Playwright coverage for the control bar (star, next preset, volume), AirPlay
  progress/transport and the ambient idle switch.

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
