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
- **A config file that fails validation does not stop dawn-core.** It used to: a hand edit with one
  bad value, or a key from a newer version, crash-looped core at boot and left only the face's own
  backup alarm. `load_config_lenient` validates each top-level section on its own, keeps the ones that
  pass, takes the defaults for the rest (and for unknown keys) and reports what it dropped; the error
  shows under *Diagnostics → System → Configuration* and `dawn-core --check` exits 1 on it. A hot reload
  of a bad file still keeps the previous config, and now also reports the error there (it was only
  logged before).
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
  exact welle-cli flags are config (`dab.welle_args`), because the carousel/PAD
  flags differ between welle.io versions.
- **welle-cli decodes on demand by default (`-w 8000`).** Upstream `-C` needs a
  programme count (`-C 1 -P` cycles one programme at a time for slides and DLS on
  every station); the old default `-C -P` silently disabled the carousel and the
  unit's fallback `-w 8000 -C` made welle-cli exit. The carousel costs CPU and
  heat on a Pi in a closed case, and the station playing already decodes its
  slides and DLS, so it stays an option (documented in the schema) rather than
  the default. Invalid `-C`/`-P` combinations are dropped when `dab.env` is
  written, existing configs included, with a log line saying so.
- **`dab.gain` is in dB; welle-cli's `-g` is an index** into the R820T/R828D
  gain table, so the nearest step is sent. Null or negative = AGC.
- **Service ids are normalised to lowercase hex without `0x`** (`1002`), whatever
  welle-cli emits, and that form is used in `dab:<sid>` references, presets and
  logo URLs. Requests *to* welle-cli (`/mp3/…`, `/slide/…`) use `0x1002`: welle
  parses a bare number as decimal and throws on one starting with a letter.
- **"Sync" means the ensemble is being decoded right now**: upstream welle-cli has
  no sync flag, so it is read from `time_last_fct0_frame` (FIG 0/0 arrives about
  every 12 s in mode I; older than 30 s = no sync). The channel comes from
  `GET /channel` because mux.json does not carry it. SNR is mapped 0–20 dB to a
  0–100 signal figure.
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
- **An alarm answers only for occurrences after it was created, edited or switched
  on**: those moments are stored as handled. Without this a one-off alarm set in
  the evening for 06:30 found that morning's 06:30 in the 26-hour look-back,
  logged it as missed and disabled itself. The grace window for a clock that was
  off at alarm time is unchanged. If that stamp is later found more than 10 minutes in
  the future (the clock was fast when the alarm was set, and chrony has stepped it back),
  it is pulled back to the present so the real occurrence still rings.
- **Regional-only holidays are a name table** (`holidays.REGIONAL_ONLY`) layered
  on the `holidays` package, because the package does not flag which entries
  are partial-state (e.g. the Royal Queensland Show). `holiday_scope =
  include_regional` turns them on. Extra dates and name exclusions come from
  config.
- **One ring session at a time, and a real alarm keeps it.** A new alarm replaces whatever rings; a nap that
  runs out while an alarm rings is folded into it (and ends its snooze, if snoozed), and a test ring is refused
  with 409, because stopping either would have stopped the alarm too. Snooze pauses the alarm slot (so the
  previous source does not resume) and re-rings with the ramp again on the same rung; the max-ring clock counts
  ringing time only. The big button stops a snoozed session too.
- **Fallback is a watched ladder inside the ring session, not the arbiter.** Source → chime → backup tone. Every
  rung is watched each second through the alarm level's own slot (not whatever happens to be on top): no audio
  `fallback_after_s` (15 s) after the ring, or 5 s after a slow start (a retune), moves to the chime; the chime
  gets `buzzer_after_s` (8 s); audio that stops for 10 s (5 s for the chime) counts as failed, so a brief DAB
  dropout does not drop the station. A check that fails (PipeWire not answering) counts as silence, so a broken
  audio stack climbs the ladder instead of stalling on it. A DAB source whose welle-cli is unreachable gets one
  restart attempt after 3 s, in the background, so a slow restart cannot hold up the watch; with no SDR the chime
  starts at once. A chime alarm goes straight from its chime to the tone, and a tone that stops is started again.
  The ramp
  counts from the ring, so it carries on across rungs; the tone goes straight to the alarm's volume. A volume
  change by hand while ringing ends the ramp (the knob wins).
- **The backup tone needs neither mpv nor the source.** Core generates the beeps (four 120 ms beeps at 2 kHz, a
  pause, as a WAV in /run/dawn) and plays them with the first program that works: `pw-play`, `paplay`, `aplay`
  on ALSA's default device, then `aplay` straight on each card (I2S/USB first, HDMI last). The raw-card path
  only succeeds when PipeWire is not holding the card, which is exactly when PipeWire is what broke. Success is
  "the process played the file through"; a failure moves on to the next program. It is an arbiter source at the
  alarm level, so everything below stays paused, and it starts directly if even the arbiter fails. An optional
  GPIO piezo (`alarm_defaults.buzzer.gpio_pin`) beeps the same pattern: the only sound left when the amp or the
  I2S link is what broke, so it is offered rather than required.
- **The face beeps too, and rings alone when core is down.** On the backup tone core sets `ringing.face_beep`
  and the face plays the pattern through Web Audio (Chromium runs with autoplay allowed): a second process and a
  second audio client. If core does not answer at alarm time (no state over the socket and no `/api/health` for
  20 s; the probe keeps a stuck socket from ringing over a healthy core) the face rings by itself 45 s after the
  alarm, or 45 s after a ring or snooze it heard of, from what it last heard (kept in localStorage). Tap snoozes
  and a hold stops, on the face; it hands back as soon as core rings again, and otherwise rings until stopped or
  for 30 minutes. It cannot change the volume or the backlight, which belong to core.
- **The alarm level has its own mpv players** (`dawn-alarm-dab`, `-media`, `-chime`). An alarm loaded into the
  player of the source it preempts was paused by that source's duck two seconds later (a DAB alarm while
  listening to DAB). They are started in the preparation window, not at the alarm. A paused stream whose player
  was used for something else, or that ended while paused, is loaded again on resume; DAB retunes first (in the
  background, outside the arbiter's lock) when the alarm moved welle to another channel.
- **The sound starts in the background; the ring shows at once.** The tick used to await the whole start (a DAB
  tune can take 12 s or more), and a snooze or stop landing in that window was overtaken by the start that came
  after it: the chime rang through a snooze, or rang on with no session left to stop it. Every start now carries
  an epoch: snooze and stop bump it, and an overtaken start pauses or releases what it started instead of
  carrying on. Stop clears the face before tearing the audio down (which can wait on a tune), and a stopped ring
  never publishes again. A second stop (the button and the web at once) waits for the first to finish, so the
  caller never goes on to start the next ring while the old one is still being torn down.
- **Bookkeeping can fail, the alarm cannot.** An occurrence is marked handled after its ring has started (if the
  start raised, the next tick tries again within the grace window). The engine's own changes (handled
  occurrence, spent skip, a once-alarm switching itself off) take effect in memory and are saved best-effort, so a
  read-only SD card or a full disk neither silences an alarm nor makes it ring every second; the event log never
  raises; a damaged row is logged once and skipped while the others ring; when the table cannot be read the last
  copy is used; out-of-range values in a row ring with the defaults. Nothing on the way to the sound needs the
  database: the last source is kept in memory, a DAB retune that cannot save the channel tunes anyway, and a
  station row that cannot be read is skipped. Switching an alarm off and on again does not forget that today's
  occurrence has rung. Backups must contain valid alarms.
- **A ring survives a restart of dawn-core.** The session is saved (`alarms.ring`: request, rung, snooze, the
  volume from before, the time rung so far; again every 30 s while ringing) and carried on at the next start,
  ringing again or still snoozed, if it would still be going and is under 4 hours old. The time core was down
  counts as ringing time (the alarm was meant to be sounding), so a ring that crashes in a loop still ends at its
  max-ring time; a snooze carries on if it ended at most `missed_grace_minutes` ago. A shutdown keeps it, a stop
  or the max ring drops it. When dropping it cannot be saved (a read-only card), a marker in `/run/dawn` keeps
  that stopped ring from coming back at the next start: the unit sets `RuntimeDirectoryPreserve=yes`, since
  systemd otherwise deletes the directory whenever the service stops, and it is a tmpfs, so the marker never
  outlives a reboot. The nap timer is saved the same way.
- **The watchdog is fed only while the alarm engine ticks** (within 30 s): the tick is quick now, so a stuck one
  is a reason for systemd to restart dawn-core, after which the saved ring carries on. `dawn-core.service` has
  `StartLimitIntervalSec=0`: systemd's default gave up after five fast restarts, leaving no alarms at all.
- **Opening the database never fails** (`db/engine.py`). The runtime path already survived a card gone
  read-only, but a card that was read-only *at boot* (ext4 remounted after errors, the usual way an SD
  card dies) could not create SQLite's WAL files, so `create_all` raised and core crash-looped with no
  alarms at all; a corrupt file did the same. Now the file is opened for writing, then read-only with
  `immutable=1` (no locks, no -shm/-wal: the alarms and settings are read, every write fails and is
  counted as before), then an empty in-memory database stands in. In that last case the alarms are
  unknown, so `alarms.degraded` is published and the face treats core as absent for alarm purposes: it
  keeps the last next-alarm it heard instead of overwriting it with nothing, and rings by itself 45 s
  after it (`backup alarm · Dawn cannot read its alarms`). `/api/health` reports the database mode.
- **Each alarm is prepared minutes ahead** (`alarm_defaults.prepare_minutes`, 5; every 20 s, every 5 s while
  something is not ready). Its players are started; for DAB a running scan is stopped, an unreachable decoder
  restarted, the tuner moved to the station's channel and checked for sync and the station on air. Someone
  listening on another channel keeps listening ("busy": it retunes when the alarm rings). A failure seen on two
  checks in a row (or no SDR, or an unknown station) starts the ring on the chime at once, since waiting 15 s in
  silence would not bring the station back; with no audio output at all it starts on the backup tone, whose last
  players go to the sound card directly. A check older than 60 s decides nothing. Steps under way (a retune)
  are `pending`, not problems. Scans are refused into a DAB alarm's window plus the scan's own length; retunes,
  decoder restarts and gain changes by hand wait while one rings or is snoozed; the alarm's own restart is always
  allowed.
- **While ringing the big button only stops.** A press of any length stops (no shutdown countdown); a sleepy
  half-second press used to show the countdown and then do nothing, and a 3 s one powered the clock off. A
  shutdown hold that an alarm interrupts does not power off. The encoder hold snoozes like a push instead of
  opening the nap picker behind the ringing screen.
- **Ringing lights the screen as a tap does**: `sleep.wake_percent` (15 %) in the night palette, the standby wake
  level otherwise, so the time and the hint can be read; the room's level again while snoozed.
- **SDR presence is re-checked from USB ids only.** The minute check ran `rtl_test -t` on the event loop, and it
  opens the stick: a welle-cli starting at that moment (an alarm restarting it) could find it taken. The tuner
  type is still read at boot. `vcgencmd` runs in a thread.
- **PipeWire not answering at start is retried.** The ALSA fallback (or no backend) used to stay for the whole
  run, leaving the alarm's volume ramp and the speaker filter out of reach; every 30 s core tries PipeWire again
  and switches when it answers.
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
- **The scene draws the forecast hour coming up, not the last reading.** Core
  publishes the next 24 Open-Meteo hours (`weather.hours`: code, cloud, rain,
  wind, gusts, visibility); the face picks the hour nearest half an hour ahead,
  so the picture turns over on the hour by itself, between fetches and offline,
  and falls back to the current conditions when no hour fits. Ten kinds
  (clear, partly, mostly cloudy, overcast, fog, drizzle, rain, storm, hail,
  snow) plus wind, frost (<= 1 °C, nothing falling), heat (>= 32 °C) and haze
  or smoke (visibility under 12 km without fog or rain) cover what Canberra
  and most of Australia get. South of the equator the hills use Australian
  seasons: cured grass in summer, green in winter, wattle in spring.
- **What moves in the scene is a composited layer.** Clouds sit on a strip
  two screens wide that slides sideways; rain, snow and hail on a tile that
  falls and is tilted by the wind; a storm flashes by opacity. The SVGs are
  rasterised once and the GPU moves them, so the Pi does not repaint at 60 Hz,
  and the clouds never stopping helps against image retention. Low-CPU mode
  freezes all of it.
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
  the future, except in the night palette or sleep mode, where it is
  `display.sleep.wake_percent` (15 %) so a tap at 2 am is not a torch. The sleep
  clock holds the backlight at `sleep.backlight_percent` (default: the backlight
  minimum) or 0 with `screen_off`. The post-sunset cap applies only in auto mode.
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

- **Open-Meteo is fetched every 15 min and cached to `weather.json`**, two days
  of hourly forecast included so a long outage still has hours to draw; the face
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
- **dawn-core supervises the programs it depends on** (`system/supervisor.py`, `system.supervisor`).
  systemd only restarts what exits; what broke in practice stayed up: a Chromium on an error page or
  frozen after a GPU hiccup (the unit is `active`, nobody looks at the screen until morning), a welle-cli
  that stopped answering HTTP, wireplumber failed so no sink was ever linked, a dawn-timed hung on a
  socket, a unit that hit the start limit. Diagnostics could see all of it but only offered buttons.
  The supervisor runs the same checks every 15 s and does the restart itself, with three guards so it
  cannot make things worse: a problem has to be seen for a while first (150 s without a face, 45 s
  without a decoder answer, 30 s of a stopped unit), the same thing is not restarted again before a
  backoff that doubles up to 30 min (and heals after ten good minutes), and alarms are never touched (a
  ring restarts what it needs itself, and welle is left alone while an alarm rings on the radio). The
  face is judged by its page's own 15 s ping over the WebSocket, not by the socket being open: the
  browser answers protocol pings for a frozen page. Restarting a unit only when it is *enabled* keeps a
  box without AirPlay or Bluetooth from restarting what was never meant to run; `is-enabled` is read
  without sudo and cached an hour. The PipeWire session is restarted with `systemctl --user` (same
  user, no sudo), after which the sink, volume and filter chain are applied again. Every repair is a
  `supervisor_repair` event and a *Diagnostics → System → Self-repair* line, so a clock that keeps
  healing itself is still seen to be unwell.
- **A service inside dawn-core that fails to start is started again** (`ServiceRegistry.retry_failed`,
  asked by the supervisor): PipeWire's tools not answering within the 30 s start timeout left the audio
  service without its loop (no sink refresh, no PipeWire retry) until the next restart of dawn-core;
  bluetoothd or a GPIO chip late at boot did the same to their services. Attempts are 1, 2, 4… minutes
  apart (15 at most), and `start()` is written to be safe to call again (the loops are only created
  when they are not running).
- **Data on ext4 with `data=journal`** is offered two ways: a dedicated partition
  (`--data-device`) or a loop-mounted image (`--data-image-mb`), because
  repartitioning a running SD card in an installer is not something to do
  automatically. The read-only root (`--readonly`, raspi-config overlayfs) is
  only enabled together with `--data-device`.
- **Setup hotspot via NetworkManager** (`nmcli dev wifi hotspot`) after
  `wait_for_network_s` without a network; the face shows the SSID, password and
  a Wi-Fi QR code, and the control UI at `http://10.42.0.1:8080/` can join a network.
  The hotspot stops by itself when connectivity returns.
- **Backups are one JSON document** (config + alarms, presets, DAB scan results,
  KV settings, minus auth secrets). Restore replaces the tables and the config
  atomically enough for a bedside clock and republishes state.

## Sleep mode and burn-in

- **What was asked, and the answers it was built from.** Sleep starts at a set time
  *or* when the room is dark, each selectable on its own; it ends at a set morning
  time, shortly before the next alarm or when the room gets bright, whichever comes
  first; audio playing at bedtime keeps the player up until it stops; the look is a
  small amber clock that moves every 2 minutes with the alarm time and a simple
  weather icon; burn-in measures are the pixel orbit, the strip auto-hide, the
  daily scene and an optional screen-off sleep; the existing background switch
  stays and covers Standby and the ambient clock (sleep never shows a background).
- **The planner is a pure, event-driven function** (`display/sleep.py`), tested
  minute by minute over whole nights like the brightness curve. It reacts to
  *crossings* (bedtime passes, morning passes) and *changes* of the room (dark or
  bright held for `dark_after_s` / `bright_after_s`), not to levels, so a manual
  Sleep now / Wake now holds until the next real event.
- **Interplay rules chosen where the answers left room** (all in the planner's
  docstring and tests):
  - Bedtime fires even with the lights on (the triggers are OR-ed, as asked).
  - The bright end is an edge: the room *becoming* bright wakes the clock; a room
    that is already lit at bedtime does not undo the bedtime.
  - A lamp switched on in the night wakes the face; while still inside the bedtime
    window it goes back to sleep once the room has been dark again for
    `dark_after_s`, even with the dark trigger off.
  - After a morning, alarm or ring wake the dark trigger waits until the room has
    been bright once, so a dark winter morning does not put it straight back.
  - Anything ringing (an alarm, a nap, light wake) ends sleep mode. A bedtime that passes
    while something rings still applies once it stops.
  - Inside the alarm lead window (`alarm_lead_minutes` before the alarm until 5 minutes
    after it) nothing puts the face back to sleep, not even a restart or a clock step.
  - Each light state is entered *and left* only after its dwell time, so a hand over the
    sensor or someone walking past is not "the room got bright" again.
  - All of its timing is in real (UTC) seconds: Python subtracts two times in the same
    zone as wall-clock times, which made each DST change look like a one-hour clock step.
  - At boot, or when the clock steps by more than 10 minutes (chrony at boot), it
    takes the state the schedule says it should be in; the room's first reading
    after boot settles to dark (which counts) or bright (which does not count as
    the room becoming bright).
  - `alarm_lead_minutes` counts back from the alarm's light wake when it has one.
- **Defaults**: on, 22:30 to 06:30, dark below 3 lx for 60 s, bright above 30 lx
  for 20 s, 10 minutes before an alarm, move every 120 s, amber at 72 % of the
  night foreground, tap brightness 15 %. The 3 lx/30 lx pair sits between the
  night-palette threshold (5 lx) and a lit room so headlights or a phone screen
  do not count. The simulator config and the tests ship with sleep mode *off* so
  results do not depend on the hour.
- **Core decides, the face draws.** Core publishes `display.sleep` and owns the
  backlight; `FaceService` shows `sleep` only where Standby would be, so ringing,
  light wake, a nap countdown, setup and messages keep their precedence. The
  first tap wakes the full face (no menu), the second opens the menu, as in
  Standby. With `screen_off` the first tap shows the sleep clock for 10 s.
- **Sleep clock placement follows the research branch** (`docs/research/
  standby-display.md` on `claude/screen-burnin-standby-research-toz3de`): a
  Halton sequence (bases 2 and 3) inside a safe box (15–85 % × 15–87 %, or the
  inscribed circle on the round layout), indexed by the time of day, so a reload
  keeps the place and no state is needed; 0.75 s fades instead of a jump.
- **Burn-in**: the pixel orbit is a slow Lissajous path (±8 × 6 px, about a pixel
  a minute) applied to text and controls only; backgrounds stay put. The strip
  hides after 120 s in Standby (not while playing) and returns on a touch or a
  change of what it shows (a time source, the network, the next alarm). The
  daily scene offsets every random seed by the local date.

## Diagnostics

- **One service, three speeds.** Checks run every `check_interval_s` (60 s) and
  on demand; `live/dab` and `live/gps` answer once a second for the antenna and
  sky pages; history is sampled every 10 s from what the services already hold
  (no extra hardware access) and written as one SQLite row a minute
  (average/min/max per metric), kept `history_days` (7) and left out of backups.
- **Checks follow chains.** Each area is checked from the hardware up (stick →
  driver → decoder → sync → signal → station list; receiver → gpsd → reports →
  signal → fix; source → shared memory → chrony's choice). Where an early failure
  already explains the rest (no decoder answering, no GPS receiver or reports,
  offline), the later steps are left out instead of repeating the cause, so the
  first failure in a chain is the thing to fix.
- **The OS sits behind a `Host` seam.** systemctl, chronyc, /proc, /sys and USB
  are read through one object that the simulator replaces with the hub, so every
  check runs on a laptop and in tests.
- **Fixes are narrow.** Each action is a fixed operation with at most a validated
  value (a channel name, a gain); the privileged ones are exact command lines in
  `deploy/sudoers/dawn` (restart gpsd/chrony, `chronyc -n -c selectdata|ntpdata`,
  `chronyc burst 4/4`). Nothing typed in the browser reaches a shell.
- **Manual decoder restarts are not problems**: restarts tagged `manual` are left
  out of the 24-hour DAB check; an alarm's restart of a silent decoder is counted.
- **Charts follow the data-viz method**: one y-axis per chart (different units get
  their own chart), solid hairline grid, two validated series colours per theme,
  a legend whenever there are two series, tooltips on hover and keyboard, and a
  table view behind every chart.

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
