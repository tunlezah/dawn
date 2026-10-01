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
