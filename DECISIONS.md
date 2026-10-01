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
