# Fixes found on a fresh install (Pi 4 B, Raspberry Pi OS Lite 64-bit, Debian 13 trixie, 2026-10-08)

Installed with `sudo ./deploy/install.sh` from a copy of the repo (no `--audio`).
Each fix below is in this working tree; `git diff 17c5e0e` shows the whole patch.

## 1. Face never starts at boot (screen stays black)

- **Symptom:** `dawn-face` enabled but `inactive (dead)` after every boot, no journal entries.
- **Cause:** `deploy/systemd/dawn-face.service` has `WantedBy=graphical.target`; Pi OS Lite boots to
  `multi-user.target`, so nothing pulls the unit in. Started by hand, cage + Chromium run fine.
- **Fix:** `WantedBy=multi-user.target` (dawn-core already uses it), and `install.sh` now runs
  `systemctl reenable dawn-face` so re-running it on an existing install moves the old symlink.

## 2. shairport-sync build fails, installer still reports success (no AirPlay)

- **Symptom:** log shows `configure: WARNING: unrecognized options: --with-pw, --with-systemd`,
  then `the glib 2.0 library is required`, `make: *** No targets`, `shairport-sync: command not found`,
  and finally `=== Dawn installed ===`.
- **Causes / fixes in `deploy/install.sh`:**
  - current shairport-sync renamed the options: `--with-pw` → `--with-pipewire`,
    `--with-systemd` → `--with-systemd-startup`;
  - missing build deps: `libglib2.0-dev` (D-Bus interface), `libplist-utils` (`plistutil`, AirPlay 2),
    `systemd-dev` (pkg-config `systemd`), `libswresample-dev`;
  - `build_shairport` is called as `build_shairport || echo WARNING`, which disables `set -e` inside
    the function, so the failed `configure`/`make` fell through to the success `echo`. Added explicit
    `|| return 1`. (`build_rtlsdr` / `build_welle` have the same pattern and would also hide failures.)

## 3. shairport-sync config rejected by current shairport-sync

`deploy/shairport-sync/shairport-sync.conf`:
- `output_backend = "pw"` → fatal `the audio backend selected: "pw" is not supported`. Now `"pipewire"`,
  and the `pw = { … }` section is now `pipewire = { … }`.
- `session_timeout = 20` → warning, must be 0 or ≥ 60; set to 60.
- `cover_art_cache_directory = ""` → warning "ignored"; removed (behaviour unchanged).

## 4. shairport-sync gives up after 5 fast failures

`deploy/systemd/shairport-sync.service.d/dawn.conf` sets `Restart=always` but not
`StartLimitIntervalSec=0`, so after a crash loop (e.g. PipeWire not ready at boot) it stays failed
for good. Added `StartLimitIntervalSec=0` under `[Unit]`, as dawn-core does.

## 5. shairport-sync cannot own its D-Bus name as user `dawn`

Upstream's `/etc/dbus-1/system.d/shairport-sync-dbus.conf` only allows `root` and `shairport-sync`
to own `org.gnome.ShairportSync`, but Dawn runs it as `dawn` (drop-in), so AirPlay pause/resume from
dawn-core would fail. Added `deploy/dbus/dawn-shairport-sync.conf` (allow `dawn` to own the name),
installed by `install.sh`. Verified: `busctl --system list` shows the name owned by user dawn.

## 6. AirPlay shown as "unavailable" / "not running" until a phone connects

`core/dawn_core/airplay/service.py` `_pipe_loop` set `available = True` only after the blocking
`os.open()` on the metadata FIFO returned, which happens when shairport-sync first writes metadata
(a sender connects). Now marked available as soon as the FIFO exists.

## 7. Bluetooth pairing agent never registers (dbus-fast 5.x)

- **Symptom:** `bluetooth agent registration failed: Argument 'signature' has incorrect type (expected str, got NoneType)`.
- **Cause:** `core/dawn_core/bluetooth/backend.py` uses `from __future__ import annotations`; with
  dbus-fast 5.2.0, a `-> None` return annotation on a `@method()` fails in the decorator.
  Reproduced in isolation: same class without `-> None` loads fine.
- **Fix:** removed `-> None` from the Agent's `@method()`s (no annotation = no return value).
  Consider pinning `dbus-fast` or adding a unit test that instantiates the Agent class.

## 8. Bluetooth rfkill soft-blocked

Pi OS ships with Bluetooth soft-blocked; adapter showed `PowerState: off-blocked`, Diagnostics said
"No powered adapter". Installer now runs `rfkill unblock bluetooth` (systemd-rfkill persists it).

## 9. `/dev/i2c-*` missing, light sensor can never be found

`dtparam=i2c_arm=on` enables the controller but nothing loads `i2c-dev`, so `/dev/i2c-1` does not
exist. Installer now writes `/etc/modules-load.d/dawn-i2c.conf` (`i2c-dev`) and modprobes it.

## 10. Installer prints the wrong URLs

Final line printed `http://dawn.local/face` / `http://dawn.local/`; core listens on 8080 only.
Now prints `:8080`.

## 11. Mouse cursor shown on the face (Pi screen only)

- **Symptom:** a cursor sits on the 4.3" panel; the same page in a browser has none.
- **Cause:** the Pi's HDMI-CEC remote inputs (`vc4-hdmi-0`, `vc4-hdmi-1`) advertise `REL_X/REL_Y` and udev tags
  them `ID_INPUT_POINTINGSTICK`, so cage sees two mice and draws its own cursor. The page's `cursor: none`
  can't hide the compositor's cursor.
- **Fix:** `deploy/udev/99-dawn.rules` sets `LIBINPUT_IGNORE_DEVICE=1` on them (Dawn doesn't use CEC keys).

## 12. DAB+ scan finds nothing

- **Symptom:** `dab_scan {'ensembles': 0, 'services': 0}`, though a radio nearby receives DAB+.
- **Cause:** each channel got `scan_dwell_s` (6 s) to sync and list services. With a modest antenna
  (SNR 8–11 dB) welle-cli took ~22 s to lock on 9C (Canberra, "CA ABC&SBS RADIO", 20 services).
- **Fix:** `scanner.scan` keeps waiting up to `dab.scan_signal_wait_s` (new, default 40 s) on a channel whose
  SNR shows a DAB signal (≥ 5 dB; empty channels read 0–2 dB). Tests in `test_dab.py`.

## 13. Alarm time picker ignores the 12/24-hour setting

`<input type="time">` follows the browser's locale, not `display.clock_24h`. The alarm editor and the sleep
times on Display now use `ClockInput` (hour / minute / am-pm selects) from `shared/components.tsx`.

## 14. Audio Amp SHIM is now the installer default

`install.sh` used `--audio auto` (onboard audio, nothing pinned) unless `--audio hifiberry` was given, so a plain
install of the reference build had no sound from the SHIM. Now `--audio` defaults to `shim` (alias `hifiberry`);
`usb`, `headphones` (`jack`), `hdmi` and `auto` pick other outputs, pin `audio.pinned_sink` to that kind (`auto`
unpins; a specific PipeWire node chosen in the web UI is left alone), and the choice is kept in
`/etc/dawn/audio-output` so `--update` / `dawn-update` keep it.

## 15. AM/PM letters crowded on the face

The clocks use a tight negative `letter-spacing` in `em`; it is inherited as an absolute length sized for the
big digits, so the small "pm" span had its letters squashed together. New `.f-ampm` / `.f-clock-sec` classes
(`face.css`) reset the spacing and size the suffix relative to its clock; AM/PM is now upper case, spaced off
the digits, and also shown on the light-wake screen.

## Not fixed (notes for upstream)

- **Panel detection can't tell if a DSI panel is attached.** With `vc4-kms-dsi-7inch` loaded,
  `card1-DSI-1/status` reads `connected` even when nothing on the panel's I2C bus answers, so
  `detect_panel` always says `waveshare_dsi` once the overlay is there. A probe of 0x45 on i2c-10
  would be a better test, and Diagnostics could flag `failed to enable backlight: -5` from dmesg.
- **No warning when the 43H panel's controllers don't respond.** On this unit: `panel-simple … failed to
  enable backlight: -5`, `edt_ft5x06 10-0038: probe … failed with error -5`, backlight stuck at 3/255,
  and dawn-core logs `backlight write failed: [Errno 5]` many times per second (log spam; should back off).
  Also: the README says the 43H has Goodix touch, but `vc4-kms-dsi-7inch` sets up an FT5x06 at 0x38.
  Needs checking against a working 43H.
- README has the HyperPixel 4 GPIO note twice (two slightly different versions) under *Wiring*.
- `git pull --ff-only || true` in the source builds hides failures.
