# Decisions

Short records of the choices made while building Dawn, newest at the bottom of
each section. Every entry says what was decided and why, so it can be revisited.

## Repository layout

- **Three Python packages, one web app.** `core/` (`dawn_core`), `dawn-timed/`
  (`dawn_timed`) and `sim/` (`dawn_sim`) are separate installable packages so the
  Pi only needs `core` + `dawn-timed`, and the laptop simulator never ships to the
  device. The web app builds both UI targets from one Vite project.
- **Default branch is `main`.** The repository was empty; `main` matches GitHub's
  default.

## Configuration

- **`/etc/dawn/config.yaml` is the single source of truth for every option.**
  The schema is a pydantic model (`dawn_core.config.schema`), exported as JSON
  Schema at `/api/config/schema`, so the control UI can render an editor for
  *any* section without a bespoke page. Volatile runtime state (current volume,
  current source, scan results, alarm fire history) lives in SQLite instead.
- **Hot reload by polling mtime every 2 s** rather than inotify: no extra
  dependency, works on every filesystem (including overlayfs), and 2 s latency
  is fine for a config file.
- **Writing the file back from the UI drops comments.** We dump a clean YAML
  document; the example file in `config/` keeps the commented reference.

## Simulators

- **`DAWN_SIM=1` swaps hardware backends, not code paths.** Every hardware
  touchpoint (GPIO, I2C lux, backlight, PipeWire, welle-cli, gpsd, chrony,
  BlueZ, shairport) sits behind a small backend interface with a `real` and a
  `sim` implementation. Sim backends talk to a single "sim hub" process over
  HTTP so the laptop can poke lux, GPS fix, DAB sync, network state and inputs.
- **The fake welle-cli mirrors the real HTTP surface** (`/mux.json`,
  `/channel`, `/mp3/<sid>`, `/slide/<sid>`, `/fic`) so core and `dawn-timed`
  run unchanged against it.

## Audio

- **Priority levels are fixed integers** (alarm 100 > sleep 80 > user 60 >
  AirPlay 40 > Bluetooth 20) and one slot exists per level. A new source at a
  level replaces the previous one at that level; there is never a queue of user
  sources. Preemption ducks the lower source to 20 % for 2 s *per source gain*
  (mpv `volume`, stream mute for AirPlay/Bluetooth), not the master volume, so
  the alarm ramp and the duck never fight over the sink volume.
- **One mpv process per source family** (`dawn-chime`, `dawn-media`, `dawn-dab`),
  controlled over the JSON IPC socket. PipeWire then shows separately named
  streams, which is what the "audio flowing" detection keys on.
- **PipeWire is driven with the CLI tools** (`wpctl`, `pw-dump`, `pw-cli`,
  `pw-metadata`) instead of Python bindings: no compiled dependency, trivially
  replaceable, and the ALSA fallback (`amixer`) is the same shape.
- **"Mute and go to standby"** (big button while playing) stops the user source
  and pauses AirPlay/Bluetooth via their remote-control interface. It does not
  leave the sink muted, otherwise the next alarm would ring silently.
- **Chimes are synthesized** (`scripts/gen_chimes.py`) and committed as small
  OGG files, so the alarm works with no network and nothing to download.
- **EQ lives in a PipeWire filter-chain sink (`dawn_eq`).** When present it
  becomes the default sink and its output stream is re-targeted to the selected
  hardware sink; master volume still applies to the hardware sink. Without the
  filter chain everything works, just without tone control.
- **The reference build is one 5 V supply with an I2S amp** (Pimoroni Audio Amp
  SHIM, MAX98357A, mono ~2.5 W into a 2.5" 4 Ω driver) instead of a USB DAC, a
  class-D amp and a 12 V rail. It uses the stock `hifiberry-dac` overlay
  (`--audio hifiberry`), so it needs no new sink kind; `install.sh --update`
  keeps it by reading its own `config.txt` block.
- **Mono is mixed in software.** The SHIM's left/right/mix mode is unconfirmed,
  so the filter chain (`dawn-eq-mono.conf`) sums (L+R)/2 and sends it to both I2S
  channels: whichever the amp takes, it gets the whole programme. The stereo
  chain is kept for USB DACs.
- **Protect the driver in the graph, not with presets.** A 2nd-order 110 Hz
  high-pass sits ahead of the shelves (the PC68-4 is specified from 120 Hz) and
  stays in when the tone controls are off; bass can be cut but not boosted by
  default (`eq.bass_max_db = 0`) because each +6 dB costs 4× the power and the
  3 W amp clips first.
- **Volume ceiling as a scale, not a clamp.** The MAX98357A has a fixed gain, so
  `audio.output_ceiling_percent` maps volume 100 onto a calibrated sink level
  and every path (controls, alarm ramp, chimes, sleep fade) is scaled into it:
  the whole 0–100 range stays usable, and nothing can drive the amp into
  clipping. `audio.max_volume` remains the clamp on the user scale.
- **Pin the I2S amp by kind.** `audio.pinned_sink` also accepts a sink kind
  (`hifiberry`), because the node name differs between boards and OS releases;
  the installer sets it so a USB audio device plugged in later cannot take over
  despite `usb` ranking first in `sink_priority`.
- **No idle suspend on ALSA outputs.** The MAX98357A pops whenever the I2S clocks
  start or stop, so WirePlumber keeps the sink running when idle
  (`session.suspend-timeout-seconds = 0`, `node.pause-on-idle = false`).

## Inputs

- **Short vs long press is decided on release**, with the shutdown countdown
  shown after 0.5 s of holding. Releasing while the countdown is visible cancels
  and does nothing else, so a 2 s press can never be mistaken for a tap.
- **Touch is routed through core** (`POST /api/face/touch`): the face never
  decides what a touch means. The controller applies the same rules to GPIO,
  the simulator and the API.
- **The encoder and big button are optional; touch is the baseline.** The menu
  sheet carries their jobs: a volume slider, and a Standby tile that sends the
  button's own `button_down` / `button_up` events, so a tap is the short press
  and a 3 s hold is the identical shutdown countdown (one controller, one set
  of semantics, whether or not the button is wired). Two exceptions live in the
  face because they are gestures, not semantics: hold 2 s while ringing = stop
  (tap = snooze, so the two stay on different gestures), and the sheet's
  keep-alive (`POST /api/face/menu/activity`) that re-arms the auto-close
  timer while a finger is on the slider or a tile. The GPIO inputs stay
  supported and auto-detected; nothing in core changed shape for them.
- **Bare encoder, internal pull-ups, no big button.** The Adafruit 377 has no
  pull-ups or + pin (a KY-040 module has both), so A, B and the switch rely on
  the SoC pull-ups; gpiozero's `RotaryEncoder` always enables them and the
  switch is a pulled-up `Button`. One full quadrature cycle is one step, which
  is one detent on this 24-detent part. The big button is not fitted, so it is
  off by default: GPIO23 is never claimed and a floating pin cannot produce
  phantom presses. The Standby tile on the face does its job.
- **Volume is on demand, not always on.** The control bar lost its − 🔊 + cell;
  the slider appears with the sheet (a tap anywhere) and the overlay is
  suppressed while the sheet is up so the two never show the same number
  twice. The ambient strip keeps its read-only level.

## DAB+

- **welle-cli runs as its own unit (`dawn-dab`)** and core talks to it over HTTP
  only. Core writes `/var/lib/dawn/dab.env` (`DAWN_DAB_CHANNEL`, `DAWN_WELLE_ARGS`)
  so a restart, by us or by systemd, comes back on the last-used channel. The
  exact welle-cli flags are config (`dab.welle_args`, default `-w 8000 -C`),
  because the carousel/PAD flags differ between welle.io versions.
- **Service ids are normalised to lowercase hex without `0x`** (`1002`), whatever
  welle-cli emits, and that form is used in `dab:<sid>` references, presets and
  logo URLs.
- **"Sync" means an ensemble was decoded**: `demodulator.sync` when welle exposes
  it, otherwise a non-empty ensemble label or service list. SNR is mapped 0–20 dB
  to a 0–100 signal figure.
- **The latest MOT slide per service is the station logo** (`/var/lib/dawn/logos/`),
  served from `/api/dab/logo/<sid>`; with no slide the same URL returns an SVG
  monogram tile (two letters, colour hashed from the SID). One URL, no client logic.
- **Scans stop DAB playback** (retuning kills the stream) and refuse to run while
  a DAB alarm is ringing; afterwards welle is retuned to the channel it was on.

## Alarms and timers

- **Scheduling is a pure function** (`alarms/scheduler.py`) over an `AlarmSpec`,
  a time zone and a holiday predicate, so DST, leap days, holidays, skip-next and
  leave can be unit-tested without a clock. Non-existent local times
  (spring-forward gap) are normalised through UTC and ring an hour later on the
  wall clock; ambiguous times (fall-back) use the first occurrence. Either way an
  alarm rings exactly once per scheduled day.
- **No double fire, no skip, on clock steps.** Each alarm remembers the last
  occurrence it handled (`last_fired_occurrence`); the 1 Hz tick only fires an
  occurrence that is newer than that. A backwards step (chrony `makestep`)
  therefore cannot re-fire; a forward step fires if within the 10-minute grace
  window and logs a miss otherwise.
- **Regional-only holidays are a name table** (`holidays.REGIONAL_ONLY`) layered
  on the `holidays` package, because the package does not flag which entries
  are partial-state (e.g. the Royal Queensland Show). `holiday_scope =
  include_regional` turns them on. Extra dates and name exclusions come from
  config.
- **One ring session at a time.** A nap or test ring arriving while an alarm
  rings replaces it (logged). Snooze pauses the alarm slot (so the previous
  source does not resume) and re-rings with the ramp again; the max-ring clock
  counts ringing time only. The big button stops a snoozed session too.
- **Fallback is decided inside the ring session, not the arbiter.** It watches
  "audio flowing" every second; a DAB source whose welle-cli is unreachable gets
  one restart attempt after 3 s, and whatever happens the chime takes over at
  `fallback_after_s` (15 s). With no SDR present the chime starts immediately.
- **Sleep timer promotes the playing slot** from `user` to `sleep` (arbiter
  `move`) instead of restarting the stream; cancelling demotes it back and keeps
  playing. Nothing playing: the last-played source starts at the sleep level.

## Face design system

- **One header slot on every screen.** The clock is always top-right at the same
  size; the top-left status line answers "which mode, is audio flowing, how good
  is the signal" (`● DAB ▂▅▇ 72%`, `● AIRPLAY`). Fixed positions across states
  are what make the face read as an appliance rather than a media player.
- **Three levels of information, not more graphics.** Primary (clock, track or
  station name) is bold near-white; secondary (artist, ensemble, device) is the
  muted blue-grey; tertiary (frequency, bitrate, elapsed time) is faint and
  small. Components only ever pick from these three (`.f-title/.f-clock`,
  `.f-sub/.f-eyebrow`, `.f-meta/.f-tiny` in `face.css`).
- **Cards are derived, not painted.** Card, border and divider colours are
  `color-mix()` of the palette foreground over the background, so the night
  (amber-on-black) and light palettes get matching surfaces for free.
- **The bottom of the screen is the control surface.** A persistent 72 px bar
  with large targets: transport for phones (shairport `RemoteControl` / BlueZ
  `MediaPlayer1`), preset prev / star / next for radio, − volume + for both.
  Buttons stop pointer propagation; a touch anywhere else still opens the menu
  via core, so the input semantics table is unchanged.
- **Idle while playing = ambient clock.** After `display.ambient_after_s`
  without a touch the face shows the standby clock with a now-playing strip.
  This is purely a face-side timer (a touch or a new track resets it); core's
  face mode stays `playing`, so alarms, timers and the arbiter are unaffected.
- **AirPlay progress comes from the metadata pipe**, not from guessing: `prgr`
  carries start/current/end RTP timestamps at 44.1 kHz, `astm` the duration.
  Core stores the sample and its wall-clock time; the face extrapolates while
  `airplay.playing` and freezes on pause, so no polling is needed.
- **DAB slides are shown whole** (`object-fit: contain` on the card) because
  MOT slides are 320×240 and cropping loses the text broadcasters put on them.
- **The standby scene is procedural, not photographic.** Photographs would need
  licensing, storage on the Pi and one per time/weather/season combination;
  an SVG composed from a few parameters (dayness and twilight from sunrise/
  sunset, a weather kind from the icon, a season from month and hemisphere)
  covers every combination for a few kilobytes, stays soft rather than busy,
  and respects the low-CPU and night-palette modes. Colours are mixed in code
  from four sky palettes and four hill palettes so the look stays coherent.
- **Demo parameters live in the URL, not in core.** `?at=`, `?weather=` and
  `?temp=` shift the face's clock and weather purely client-side, so design
  review and screenshots never put fake state into the real store.
- **Docs screenshots are taken at 100 % brightness.** The sim's HDMI overlay
  dimmer is part of the product, but a greyed render misrepresents the design.

## Display and brightness

- **The brightness maths is pure** (`display/curve.py`): piecewise-linear
  interpolation in lux, a percentage hysteresis on the *target*, and a timed
  glide (slew) of the *applied* value. The 10 Hz loop in `DisplayService` just
  feeds it lux and time, so cover/uncover timing is unit-tested.
- **Night palette has its own lux hysteresis** (enter below `night_lux_threshold`,
  leave above threshold + `night_hysteresis_lux`), independent of the brightness
  hysteresis, so the palette never flickers at the threshold.
- **Manual override is runtime state, not config.** The slider sets
  `display.mode = manual` in SQLite with `manual_until = next sunrise`
  (astral, from the GPS or configured position); the config only holds the
  default mode/level and whether overrides end at sunrise or never.
- **Forced levels bypass the curve but keep the slew**: light-wake forces 100 %,
  a standby wake tap forces `standby_wake_percent` while `face.wake_until` is in
  the future. The post-sunset cap applies only in auto mode.
- **No sensor = schedule mode**: the configured manual level by day and the
  curve's lowest point after sunset; the face shows a "sensor not found" chip.
- **HDMI panels dim with a software overlay** (`display.overlay_dim`, drawn by
  the face). The sim backlight also dims the face a little so the effect is
  visible on a laptop while the hub prints the level.
- **The Waveshare 43H 4.3" DSI panel uses the official 7" overlay**
  (`vc4-kms-dsi-7inch`); it presents itself as that display, and the overlay
  for Waveshare's older PCB-backed 4.3" LCD leaves it dark. Rotation is a boot
  setting (`video=DSI-1:…,rotate=` plus a libinput calibration matrix written
  by the installer from `display.rotation`), not something the face does.
- **Under-voltage and throttling are diagnostics, not guesses.** With one 5 V
  supply and a passive heatsink by the bed, `vcgencmd get_throttled` is read on
  every heartbeat and shown in *Status → Hardware*; bit 0x10000 (under-voltage
  since boot) is the one to look for.
- **VEML6030/VEML7700 run at gain 1/8, 100 ms integration** (0.46 lx/count,
  ~30 klx range) so one setting covers a dark bedroom and daylight at the 10 Hz
  sample rate; the datasheet polynomial corrects readings above 1000 lx.

## Time sources

- **chrony owns the clock; core only reports.** `TimeSourceService` runs
  `chronyc -c sources` / `tracking` every 30 s and publishes which reference is
  selected (`*`), each source's offset, reach and liveness (reach ≠ 0, not `?`/`x`,
  last sample within `stale_after_s`). Alarm evaluation never looks at this.
- **gpsd is the primary GPS path**, read over its JSON protocol on 2947 (no
  python-gps dependency); the simulator speaks the same protocol. A direct
  NMEA-over-serial reader is the fallback when gpsd is not running, and the same
  NMEA parser is unit-tested.
- **dawn-timed uses edge detection on `utctime`**: welle-cli's mux.json carries
  whole seconds, so a sample is emitted only when the value ticks over, which
  places it within one poll period (200 ms at 5 Hz) of the true second. If
  `utctime` is missing or lacks seconds, the daemon switches to parsing FIG 0/10
  from the `/fic` stream (CRC-checked; the long form has milliseconds). Samples
  are written only while the decoder reports sync.
- **SHM 2 is created with perm 0666** (`refclock SHM 2:perm=0666`) so dawn-timed
  can run unprivileged as user `dawn`; the segment layout follows ntpd's
  `struct shmTime`, with the `time_t` width chosen for the platform (64-bit, or
  32-bit builds older than Debian 13) and overridable with `--time-t-bytes`.
- **GPS SHM 0 is `prefer trust` with `delay 0.2`**: NMEA-only receivers deliver
  the sentence tens of ms after the second; the fixed delay bounds the error and
  chrony still steps the clock with `makestep 1 3` on boot.

## Weather and control UI

- **Open-Meteo is fetched every 15 min and cached to `weather.json`**; the face
  shows the cached forecast with a "stale" marker once it is older than 2 h.
  Sunrise/sunset for brightness come from astral, not from the forecast, so the
  display schedule works offline.
- **The simulator serves a canned Open-Meteo response** from the hub, so `make
  sim` needs no internet and the "network offline" toggle exercises the stale
  path.
- **Settings has a schema-driven "All options" editor.** It renders the pydantic
  JSON Schema of `config.yaml` and PATCHes single values, so every option in the
  project is changeable from the web UI without a bespoke form; the curated
  sections above it cover the common ones.
- **Round display variant is a CSS mode** (`display.layout = round`): a circular
  canvas of `100vmin` centred on the panel, with the face content re-flowed to
  the centre. No separate component tree, so new face features work on both.

## AirPlay and Bluetooth

- **Both are "external" arbiter sources**: audio reaches PipeWire on its own, so
  the source objects only (a) pause/resume the sender and (b) mute/unmute the
  stream node. Ducking an external stream means muting it; the 2 s duck before
  pause still applies so the sender sees a clean pause. The arbiter therefore
  needs no special cases for them.
- **AirPlay state comes from the shairport-sync metadata pipe**, parsed as a
  small state machine (`pbeg`/`pend`/`pfls`/`prsm`, `minm`/`asar`/`asal`,
  `PICT`, `snam`). Pause/resume of the sender uses shairport-sync's D-Bus
  `RemoteControl` interface via `busctl`, so no Python D-Bus dependency is
  needed for AirPlay.
- **Bluetooth uses BlueZ over D-Bus with dbus-fast**, low-level `Message` calls
  (no introspection) plus `AddMatch` for property/ObjectManager signals, so a
  missing interface never raises at import time. A just-works agent
  (`NoInputNoOutput`) is registered so phones can pair while Dawn is
  discoverable from the web UI; paired devices are marked trusted and
  reconnected automatically every 30 s while nothing is connected.
- **Playback detection uses MediaTransport1 `State == active`** (audio really
  flowing) with MediaPlayer1 `Status`/`Track` for metadata. A paused phone keeps
  its slot (paused) so it resumes correctly after an alarm; a disconnect
  releases it.
- **The AirPlay name lives in shairport-sync's config file**, so a tiny sudo
  helper (`deploy/bin/dawn-airplay-name`) rewrites it and restarts the service
  when `airplay.name` changes. The Bluetooth alias is set directly on the adapter.
- **The simulator emits real metadata-pipe bytes** for AirPlay and a fake BlueZ
  snapshot for Bluetooth, so the parser and the priority behaviour (alarm > user
  > AirPlay > Bluetooth, resume after stop) are exercised on a laptop.

## Deployment

- **One installer, idempotent, marked edits.** `deploy/install.sh` detects the
  board and panel, writes a `# >>> dawn >>>` block into `config.txt`, installs
  its own files under `/etc/.../*.d/` or `/etc/dawn`, and builds rtl-sdr-blog,
  welle.io and shairport-sync (+ nqptp) only when their binaries are missing or
  `--rebuild` is given. `--update` is what `dawn-update` runs after `git pull`.
- **Everything runs as the unprivileged `dawn` user**, including welle-cli,
  shairport-sync (via a drop-in) and the kiosk. Privileged actions go through a
  short sudoers allow-list (`deploy/sudoers/dawn`) and a polkit rule for
  NetworkManager/BlueZ/hostname, so the service never needs root.
- **PipeWire runs in dawn's user session** (`loginctl enable-linger dawn`), and
  the system units point `XDG_RUNTIME_DIR` at it. mpv, shairport-sync and the
  Bluetooth A2DP sink all meet in that one session, which is what the arbiter
  controls.
- **The kiosk is cage + Chromium on tty1** (`dawn-face.service`,
  `Conflicts=getty@tty1`), launched by `dawn-face`, which waits for the core
  health endpoint and applies low-CPU Chromium flags on Zero 2 W / Pi 3.
- **Watchdogs at two levels**: systemd pets the bcm2835 hardware watchdog
  (`RuntimeWatchdogSec=15s`), and `dawn-core.service` has `WatchdogSec=60` fed by
  `sd_notify(WATCHDOG=1)` from the heartbeat loop that also writes
  `/run/dawn/heartbeat`.
- **Data on ext4 with `data=journal`** is offered two ways: a dedicated partition
  (`--data-device`) or a loop-mounted image (`--data-image-mb`), because
  repartitioning a running SD card in an installer is not something to do
  automatically. The read-only root (`--readonly`, raspi-config overlayfs) is
  only enabled together with `--data-device`.
- **Setup hotspot via NetworkManager** (`nmcli dev wifi hotspot`) after
  `wait_for_network_s` without a network; the face shows the SSID, password and
  a Wi-Fi QR code, and the control UI at `http://10.42.0.1/` can join a network.
  The hotspot stops by itself when connectivity returns.
- **Backups are one JSON document** (config + alarms, presets, DAB scan results,
  KV settings, minus auth secrets). Restore replaces the tables and the config
  atomically enough for a bedside clock and republishes state.

## Testing

- **Unit tests target the pure cores** (scheduler, arbiter, brightness maths,
  parsers) and the engine with injected time, so they run in seconds without
  hardware or a clock. Everything hardware-facing is behind a backend interface
  with a sim implementation driven by the hub.
- **Playwright smoke tests run against the simulator stack**, not against mocks,
  and drive the same hub toggles a developer uses (lux, GPS, SDR). They check the
  behaviours a bedside user sees: time on the face, snooze by tapping, stop by
  button, night palette, DAB now-playing, alarm/timer/radio/settings pages.
- **Screenshots are generated, not hand-made** (`web/scripts/screenshots.mjs`)
  so the renders in `docs/screenshots/` stay in step with the UI.
