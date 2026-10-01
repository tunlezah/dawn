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

## Inputs

- **Short vs long press is decided on release**, with the shutdown countdown
  shown after 0.5 s of holding. Releasing while the countdown is visible cancels
  and does nothing else, so a 2 s press can never be mistaken for a tap.
- **Touch is routed through core** (`POST /api/face/touch`): the face never
  decides what a touch means. The controller applies the same rules to GPIO,
  the simulator and the API.

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
- **VEML6030/VEML7700 run at gain 1/8, 100 ms integration** (0.46 lx/count,
  ~30 klx range) so one setting covers a dark bedroom and daylight at the 10 Hz
  sample rate; the datasheet polynomial corrects readings above 1000 lx.
