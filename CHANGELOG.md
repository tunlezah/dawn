# Changelog

All notable changes to Dawn are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- The standby scene follows the forecast for the hour coming up instead of the last reading,
  and changes on the hour by itself. Core asks Open-Meteo for two days of hourly weather, cloud,
  rain, wind and visibility and publishes the next 24 hours as `weather.hours`. New looks:
  mostly cloudy, drizzle, heavy rain, thunderstorms with lightning, hail, wind, frost, heat haze
  and smoke, a moon in its real phase crossing the night sky, and Australian seasons south of
  the equator. Clouds drift with the wind and rain or snow falls at its slant, on composited layers
  (cheap on the Pi; frozen in low-CPU mode); the hills shift a few pixels over the day against
  burn-in. The simulator's forecast changes every hour. Demo URLs take `wind`, `cloud`, `rain` and `vis`.
- Alarms: new options `alarm_defaults.buzzer_after_s`, `prepare_minutes` and `buzzer.gpio_pin` /
  `gpio_active_high`; the installer adds `alsa-utils` (`aplay` for the backup tone);
  `dawn-core.service` sets `StartLimitIntervalSec=0` and keeps `/run/dawn` across restarts
  (`RuntimeDirectoryPreserve=yes`); the systemd watchdog is fed only while the alarm engine ticks. While ringing, any press of the big button stops (no shutdown countdown) and the
  encoder hold snoozes; the screen lights as for a tap. A test ring while an alarm rings answers 409, and
  so do scans into a DAB alarm and retunes or decoder restarts while one rings. Unit tests no longer
  reach a simulator left running.
- `dab.welle_args` defaults to `-w 8000` (decode on demand); bad `-C`/`-P` combinations are
  dropped from existing configs too, and `dab.gain` is sent to welle-cli as the R820T/R828D
  gain-table index it expects.
- The *Display* background switch now says it covers Standby and the ambient clock (never sleep
  mode or the night palette).
- New sudo rules, exact command lines only: restart gpsd and chrony, `chronyc -n -c
  selectdata`/`ntpdata`, `chronyc burst 4/4`.
- README and setup screen give the control UI's real address, `http://dawn.local:8080/`
  (setup hotspot: `http://10.42.0.1:8080/`): nothing listens on port 80.
- Reference hardware is final: Pimoroni Audio Amp SHIM (MAX98357A I2S, mono) driving a
  Dayton PC68-4 2.5" 4 Ω driver replaces the USB DAC, MAX9744 amp and 12 V supply, so the
  clock runs from one 5 V USB-C supply; Adafruit 377 bare encoder instead of a KY-040;
  no big button. README hardware table and pin map, the *About* wiring list and the
  *Audio* page are updated.
- `install.sh --audio hifiberry` now writes `dtparam=audio=off`, `dtoverlay=hifiberry-dac`
  and `gpio=25=op,dh`, installs the mono filter chain, adds a WirePlumber rule that keeps
  ALSA sinks running when idle (no MAX98357A pop) and pins the I2S amp
  (`audio.pinned_sink: hifiberry`). `--update` keeps an existing I2S set-up. The managed
  `config.txt` block starts with `[all]`.
- The Waveshare DSI panel overlay is `vc4-kms-dsi-7inch` (the 43H 4.3" panel presents as
  the official 7" display) instead of `vc4-kms-dsi-waveshare-panel,4_3_inch`.
- `inputs.big_button.enabled` defaults to `false`.
- `audio.pinned_sink` also accepts a sink kind (`hifiberry`, `usb`, …); a missing pinned
  sink is logged once instead of every refresh.
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
- **Alarms that always ring.** A third rung after the chime, the **backup tone**: beeps generated by core
  and played without mpv by `pw-play`, `paplay`, `aplay` or `aplay` straight on a sound card, whichever
  works (`alarm_defaults.buzzer_after_s`, 8 s after the chime goes unheard). The face beeps along through
  Chromium, and an optional active piezo on a GPIO pin (`alarm_defaults.buzzer.gpio_pin`, e.g. GPIO26)
  sounds with it. Every rung is watched for audio, including the chime, and audio that stops mid-ring
  (10 s) moves on too.
- **Alarm preparation** (`alarm_defaults.prepare_minutes`, 5): minutes before each alarm its players are
  started, a DAB station is tuned and checked for sync and on air, a running scan is stopped and new
  scans are refused; what will not play is known in advance and the ring starts on the chime at once.
  Shown on the next-alarm card and under *Diagnostics → Audio → Alarms*.
- **The face rings by itself** when dawn-core does not answer at alarm time (no state and no
  `/api/health`), from the last alarm and ring it heard: tap snoozes, a 2 s hold stops, on the face.
- Rings and snoozes in progress, and nap timers, carry on after a restart of dawn-core (crash, watchdog,
  update).
- Diagnostics: the next alarm's preparation, rings that needed the backup tone, missed alarms and rings
  carried over a restart (*Audio → Alarms*); failed database writes (*System*).
- **Diagnostics** page (`/diagnostics`; under *More* on a phone, with a badge, and a banner on
  *Home* when something fails). Every place a fault can sit is checked and explained, problems
  first, each with what to do and a one-tap fix where one exists. Tabs:
  - *DAB radio*: live SNR (meter and a two-minute trace, once a second), FIC CRC errors per
    minute, frequency correction, tuner gain, the frame counter's age; the spectrum, the
    null-symbol spectrum (interference), the impulse response and the symbol-phase histogram
    from welle-cli; per-station frame/Reed-Solomon/AAC error rates; transmitters heard (TII);
    tune a channel, fixed gain or AGC, rescan, restart the decoder; welle-cli's arguments and
    messages.
  - *GPS*: fix, satellites used and strong, HDOP, a sky plot and the signal per satellite,
    receiver/gpsd facts, how late the time report arrives (TOFF); restart gpsd.
  - *Time sync*: chrony's tracking; the GPS → chrony, DAB → chrony and Network → chrony chains
    followed step by step with chrony's own reason when a source is not used; sources with
    state, reach, spread and options; dawn-timed's status; the NTP shared-memory segments;
    poll all sources, restart chrony or dawn-timed.
  - *Network*, *Audio & devices* (audio, AirPlay, Bluetooth, display, knob, weather) and
    *System* (services, power and throttling, temperature, disk, recent errors, logs).
  - A week of history per area (one SQLite row a minute: average, minimum and maximum) with
    the events that matter to it (decoder restarts, scans, fixes, alarms, sleep mode).
  - API: `GET /api/diag`, `POST /api/diag/run`, `GET /api/diag/live/{dab,gps}`,
    `GET /api/diag/dab/plot/{spectrum,nullspectrum,impulseresponse,constellation}`,
    `GET /api/diag/history`, `GET /api/diag/events`, `POST /api/diag/action/{id}`.
    Config: `diagnostics.history_days`, `check_interval_s`, `timed_status_file`.
  - dawn-timed writes `/run/dawn-timed/status.json`.
- **Sleep mode** (`display.sleep`): in Standby the face becomes the clock alone, small and deep
  amber on black at the backlight floor, with the next alarm and a weather icon underneath; it
  fades to a new place every `jump_every_s` (120 s). It starts at the bedtime (`start`, 22:30)
  or when the room goes dark (below `dark_lux` for `dark_after_s`), each on its own switch, and
  ends at the morning time (`end`, 06:30), `alarm_lead_minutes` before the next alarm or its
  light wake, or when the room gets bright, whichever comes first; anything ringing ends it.
  Playing audio stays on screen until it stops. A tap shows the full face for
  `inputs.standby_wake_s` at `wake_percent` (15 %, also used for a tap in the night palette
  instead of 100 %). `screen_off` turns the backlight off on a black screen; a tap then shows
  the sleep clock for 10 s. *Display → Sleep mode* sets all of it and has Sleep now / Wake now
  (`POST /api/display/sleep`). Transitions are logged as `sleep_mode` events.
- **Burn-in protection** (`display.burn_in`): the face's foreground drifts within ±8 × 6 px
  (pixel orbit), the Standby status strip fades after `strip_autohide_s` without a touch and
  comes back on a tap or a change worth seeing, and the background's hills, trees and stars
  are drawn from the date (`scene_daily`).
- Simulator: DAB SNR, GPS signal, Wi-Fi level, GPS plugged and failed-service controls on the
  hub; a fake welle-cli that follows upstream (nested labels, on-demand decoding, error
  counters driven by the SNR, plot endpoints, TII).
- Mono filter chain (`deploy/pipewire/dawn-eq-mono.conf`): (L+R)/2 to both I2S channels.
  Both chains gain a 2nd-order high-pass, `audio.eq.highpass_hz` (default 110 Hz, null =
  off), which stays in when the tone controls are off.
- `audio.eq.bass_max_db` (default 0): bass can be cut, not boosted, unless raised.
- `audio.output_ceiling_percent`: the hardware sink level at volume 100, applied to every
  volume path (controls, alarm ramps, chimes, sleep fade). Lowering `audio.max_volume`
  below the current volume now clamps it straight away.
- `display.rotation` is applied by the installer (kernel `video=…,rotate=` and a libinput
  touch calibration matrix).
- *Status → Hardware → Power and throttling*: `vcgencmd get_throttled` flags (state
  `system.throttled`, `system.throttle_flags`); changes are logged.
- Tests for the hardware profile: volume ceiling, alarm limits, EQ limits, pinning by kind,
  throttle parsing, and the encoder on gpiozero mock pins (pull-ups, one step per detent,
  invert, switch, no phantom button). `gpiozero` joins the dev dependencies.
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

### Fixed
- Alarms (found in an audit of the alarm path; covered by tests in `test_alarm_robustness.py`,
  `test_alarm_support.py` and `test_buzzer.py`, except the systemd unit and the `rtl_test`/`vcgencmd`
  changes, which need the device):
  - A DAB alarm while DAB was playing (or a stream alarm over a stream, a chime over a chime) went silent
    after 2 s and fell back to the chime: both shared one mpv, and the preempted source's duck paused it.
    The alarm level now has its own players.
  - Stopping an alarm while its source was still starting (a DAB tune) left the chime ringing with no
    way to stop it and the face stuck on the ringing screen; snoozing then rang the chime through the
    snooze.
  - A database that could not be written (an SD card gone read-only, a full disk) stopped every alarm
    from ringing; so did one malformed alarm row, which also made adding alarms fail; a failing state
    update right after an alarm was marked handled lost that alarm for good.
  - When mpv could not start or the chime did not play, the alarm showed "chime fallback" in silence.
  - A nap running out while an alarm rang (or was snoozed) replaced the alarm; a test ring did too.
  - Holding the big button 3 s while ringing powered the clock off, and a half-second press did nothing.
  - A crash or restart of dawn-core during a ring or a snooze dropped it, and the nap timer with it.
  - A scan could run into a DAB alarm (and retune under it when it ended); retunes and decoder restarts
    from the web were allowed while one rang.
  - The alarm tick waited for the sound to start (12 s and more for DAB), and the DAB sync wait ate into
    the 15 s before the chime; the systemd watchdog was fed even with the alarm engine stuck, and systemd
    gave up restarting dawn-core after five quick failures.
  - The ringing screen stayed at the backlight minimum in a dark room; the ramp fought the volume knob;
    a sink muted behind core's back stayed muted; the encoder hold opened the nap picker behind the ring.
  - After an alarm, a radio station it had preempted on another channel resumed on a dead stream.
  - The once-a-minute SDR check ran `rtl_test` (blocking the event loop and opening the stick welle-cli
    needs); `vcgencmd` blocked it every 5 s. PipeWire not answering at start left the ALSA fallback in
    place for the whole run.
  - Found by an independent review of the changes above, each reproduced first: with the database
    unreadable a DAB alarm lost its station (the source lookup and the retune both needed it); a sound
    check that raised stopped the ladder where it was; a backup tone that died stayed silent; a slow
    decoder restart held up the watch; a stop still finishing could mute or silence the next ring; a ring
    carried over a restart started its max-ring clock again, and one stopped while the card was read-only
    came back; an alarm that rang while the card was read-only rang again when switched on from the web
    (it already was); with no audio output the ring still began on its source; the face could hand its own
    ring back on stale state, and waited 45 s before beeping for a core that had gone away mid-tone.
- DAB with upstream welle-cli (the version the installer builds): ensemble and station labels
  are objects and showed as `{'label': …}`; sync was read from a field that does not exist
  (now the FIG 0/0 frame counter); the channel was never read back (now `GET /channel`);
  slides and logos asked for the wrong station (`/slide/1002` is decimal to welle-cli; ids are
  now `0x…`); the unit's fallback `-w 8000 -C` made welle-cli exit at first boot.
- An alarm created, edited or switched on after its time counted that morning's occurrence:
  a one-off alarm set in the evening for the next morning was logged as missed and disabled at
  once, and a repeating one set a few minutes after its time rang straight away. (An alarm set
  while the clock was fast still rings at the real time once chrony corrects the clock.)
- The face's Standby tile could shut the clock down: its button-down and button-up requests
  raced, and an up that arrived first left the hold timer running into the shutdown countdown.
- A GPS fix lingered after the receiver was unplugged; it now expires 10 s after the last report.
- dawn-timed no longer restart-loops when chrony's SHM 2 segment cannot be attached; the reason
  shows in its status and in *Diagnostics → Time sync*.

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
