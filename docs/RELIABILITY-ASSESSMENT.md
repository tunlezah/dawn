# Reliability assessment: will the alarm sound at the right time?

Scope: dawn-core 0.2.2 (`core/`), the face (`web/src/face`), the systemd units and chrony configuration in
`deploy/`. The question asked was whether the alarm can be trusted on a clock that runs for months, and what
(memory leaks included) could stop it ringing. Everything below was checked against the code and, where it is a
defect, reproduced with a test; the full core suite (237 tests) passes on this tree.

## Verdict

The scheduling core is sound and unusually well defended. The alarm engine is a pure 1 Hz tick over a pure
scheduler with tests for DST, leap days, holidays, leave, skip-next, read-only storage, malformed rows, restarts
mid-ring and mid-snooze. The ladder of sounds (source, chime, backup tone, face beep, GPIO buzzer) works as
described in the README when PipeWire and mpv are healthy. A ring survives a dawn-core restart, systemd never
gives up restarting dawn-core, the watchdog is gated on the engine tick, and the face rings by itself when core
is unreachable.

What can still stop a ring, in order of how much I would worry:

| # | Severity | Where | What happens |
|---|---|---|---|
| 1 | High | `audio/player.py:37`, `audio/service.py:243` | An mpv that dies mid-ring is still reported as "audio flowing" whenever PipeWire is not the backend (the ALSA fallback that is used exactly when PipeWire broke). The ladder never climbs; the alarm is silent until `max_ring_minutes`. |
| 2 | High (long-running) | `audio/sources.py:68`, `audio/player.py:74` | Every play, preset change and ring adds a listener to its mpv player that is never removed. Each mpv event then re-publishes the state once per stale listener. Grows for the life of the process. |
| 3 | Medium | `alarms/buzzer.py:264-271` | A backup-tone player that hangs (PipeWire wedged, not dead) is counted as sounding for ever; the chain never moves on to `aplay` on the raw card. |
| 4 | Medium | `audio/backend.py:35-43` | `pw-dump` / `wpctl` that time out are not killed. During a ring `pw-dump` runs every second, so a wedged PipeWire leaves a hung process a second. |
| 5 | Medium (design) | `deploy/chrony/chrony.conf`, no RTC in the build | After a power cut the Pi boots with yesterday's clock until GPS/NTP/DAB sync. A sync that lands more than `missed_grace_minutes` (10) past the alarm marks it *missed*: no ring. |
| 6 | Low-Medium | `alarms/service.py:376-380`, `api/router_state.py:24` | A tick that raises every time still feeds the watchdog and `/api/health` still says ok, so neither systemd nor the face steps in. |
| 7 | Low | `db/engine.py:148` | The `event` table is never pruned. Bounded in practice (tens to hundreds of rows a day) but grows for ever; it is also in every backup. |
| 8 | Low | `timesync/service.py:113`, `net/nmcli.py:68`, `dab/service.py:336` | Same timeout-without-kill pattern as 4, at lower rates (every 30 s). |

None of 1-4 bites on the normal path (PipeWire up, mpv up). 1 and 3 bite precisely in the degraded paths the
README promises will still ring, which is why they rank first. 2 is the only true memory leak found in core;
it is small per event but its cost is CPU, not just memory, and it grows linearly with use.

## How each was verified

Reproductions are in a scratch pytest file (`scratchpad/repro/test_repro.py`, eight tests, all pass, meaning
each defect is present). The relevant assertions, in prose:

1. **Dead mpv reads as flowing.** `MpvPlayer.flowing` is `playing and not core-idle and not paused-for-cache`
   from the cached property dict. When mpv dies, `_rx` (`player.py:134`) logs "IPC closed" and returns; nothing
   resets the dict and `alive` is never consulted. `AudioService._slot_flowing` then asks
   `backend.stream_running()`, which the base `AudioBackend` and `AlsaBackend` answer with `None`, and
   `running is not False` makes that *True*. With the PipeWire backend `pw-dump` no longer lists the stream and
   the check correctly fails, so the exposure is the ALSA/no-backend fallback. In that state, a crashed alarm mpv
   (out-of-memory on a Zero 2, a decoder crash on a bad stream) rings nothing and the face does not beep either:
   `face_beep` is only set on the backup-tone rung.
2. **Listener growth.** Forty `play` + `stop_level` cycles on the alarm level leave forty entries in
   `players["dawn-alarm-chime"]._listeners`; firing one player event calls `AudioService.publish()` forty
   times. Each `publish()` runs a presets query and touches the state (a full-state WebSocket broadcast). In the
   simulator soak below this shows as a slow RSS creep; on a real clock with DAB metadata changing every few
   seconds, after a few months each change fans out to thousands of publishes on the event loop.
3. **Hung tone player.** With `player_commands` returning a command that never exits, `Buzzer.sounding` is True
   after 3 s and the loop is still inside the first `_play`: `asyncio.wait(..., timeout=0.4)` decides it is
   "playing" and the following `await out` has no cap. A wedged PipeWire (every client blocks; it happens after a
   USB DAC is yanked) therefore looks like a working backup tone. The supervisor (`system/supervisor.py:175`)
   restarts PipeWire only on a systemd state of `failed`/`inactive`, never on "answers nothing".
4. **Timeout without kill.** `audio.backend.run("sleep", "37", timeout=0.2)` returns 124 and `sleep 37` is still
   running afterwards; `diagnostics.host.Host.run` with the same arguments kills it (the pattern to copy).
5. **Clock after a power cut.** Not a code defect; see the design section.
6. **Failing tick still alive.** With `tick()` monkeypatched to raise, `AlarmService.alive()` is True and
   `last_tick` is fresh after 2.5 s. `tick()` already isolates per-alarm failures, so the exposure is to a failure
   outside `_tick_row`; small, but it is the one case neither the watchdog nor the face's backup covers, since
   `/api/health` does not look at the engine.
7. **Events unpruned.** 500 `log_event` calls add 500 rows; nothing on `Database` prunes or trims.

### Simulator soak

Five minutes of `dawn_sim` with core as its child, driven at ~2 play/stop + test-ring/snooze/stop cycles a
second (523 cycles), sampling `/proc/<core>/status` every 10 s:

| Time | RSS | Open fds | Threads |
|---|---|---|---|
| 0 s | 107 MB | 28 | 7 |
| 60 s | 111 MB | 31 | 7 |
| 300 s | 112 MB | 31 | 7 |

Descriptors and threads are flat. Memory rises ~4 MB in the first minute (warm-up: caches, the first
JSON schemas) and then creeps by tens of kB per 10 s, consistent with finding 2 (two sources, and so two
listeners, per cycle). Nothing else in core grows: the state event list is capped at 200, the in-memory log at
500, supervisor repairs at 50, the series cache at 32 entries, the holiday calendar cache at 64, and the
Diagnostics history is pruned hourly to `history_days`.

## Design-level risks (not bugs, but they decide whether you can trust the alarm)

**Time after a power cut.** The reference Pi 4 has no RTC. `fake-hwclock` restores the time saved at shutdown
or on the hour, so after a cut the clock boots behind by the outage plus up to an hour, and the engine evaluates
alarms against it until chrony's first update steps it (`makestep 1 3`: steps on the first three updates only,
then slews at most ~8 %, so a later large error takes hours to correct). Two consequences:

- The common one: power returns after the alarm time. The step lands past `missed_grace_minutes` (10) and the
  occurrence is logged `alarm_missed` and not rung. A cut from 05:00 to 06:45 with a 06:30 alarm rings nothing.
- The rare one: the wrong clock crosses the alarm time in the seconds before sync, and the alarm rings at the
  wrong real time; it is then not rung again when the clock steps.

Recommendations: a DS3231 RTC module on I2C bus 1 (address 0x68; nothing in the build uses it) or the Pi 5's
RTC with its battery, so the clock is right from the first tick; and raise `alarm_defaults.missed_grace_minutes`
to 30-60 so a late ring beats none. No power during the alarm is the one case nothing in the design covers (the
face dies with the Pi): a UPS HAT or a pass-through power bank is the only answer, and worth it if "I need to
trust the alarms" includes storms.

**The face backs up a dead core, not a silent one.** `web/src/face/backup.ts:211` returns while core answers,
so an engine that runs but does not fire (finding 6, or any future bug of that shape) is not covered. Making
`/api/health` report `alarms.alive()` (and the face treat a false answer as "core down") closes this cheaply.

**Logs are volatile.** The installer does not configure journald; on Raspberry Pi OS the journal lives in RAM
and is gone after the reboot that follows an overnight failure. The SQLite `event` table survives, which is why
`alarm_missed` / `ring_fallback` rows matter. `Storage=persistent` with `SystemMaxUse=64M` would keep the
journal too.

**The kiosk is a long-running Chromium.** The face re-renders on every state message (several a second) and
animates continuously. I found no timer or listener leak in the face code (every `setInterval`/`setTimeout` is
cleared; the WebSocket store is a singleton; the beeper reuses one `AudioContext`), but Chromium itself on a
page that never reloads is a known slow grower. The supervisor restarts a kiosk whose page stops talking, and
the small boards get `--max-old-space-size=96`, which turns a leak into a restart rather than a hang. A
scheduled reload once a day at an idle moment (never while ringing or within the preparation window) would make
that deterministic. This only matters for the alarm when core is also down, since the face is the backup ringer.

**CPU on a Zero 2.** During a ring `pw-dump` runs once a second (the ring's watch) plus every 2 s (the audio
loop) and the whole state is serialised and broadcast several times a second. Not a leak; fine on a Pi 4.

## What is solid (so the above is read in proportion)

- `alarms/scheduler.py` is pure, time-zone aware, and the DST, leap-day, holiday, leave and skip-next cases
  are tested. `due_occurrence` plus `last_fired_occurrence` makes firing idempotent across restarts.
- Engine writes take effect in memory first; a read-only or full SD card cannot stop or double a ring
  (`test_alarm_robustness.py` covers read-only, event-log failure, corrupt rows, unreadable database).
- A ring and a snooze are saved every 30 s and resumed after a crash, the systemd watchdog or an update
  (`alarms/service.py:631`), with the time core was down counted against `max_ring_minutes`.
- `dawn-core.service`: `Restart=always`, `StartLimitIntervalSec=0`, `WatchdogSec=60` fed only while the engine
  ticks, `Type=notify` with READY sent first; hardware watchdog at 15 s.
- The face keeps the next alarm in localStorage and rings 45 s after it if core is down or has lost its
  database; `--autoplay-policy=no-user-gesture-required` lets it sound without a touch.
- Every bounded structure I looked for is bounded (listed under the soak); metrics are pruned hourly.

## Suggested fixes, smallest first

1. `MpvPlayer.flowing` and `playing`: return False unless `self.alive`; in `_rx`, on EOF reset the property
   dict to idle and call `_notify()`; in `AudioService._slot_flowing`, treat a player that is not alive as not
   flowing regardless of what the backend says. One test: the dead-mpv reproduction above, inverted.
2. `PlayerSource.stop()` (or the arbiter's `_stop_slot`) removes the source's listener from its player; add
   `Player.off_change()`. Or have `_changed` return early when `not self._started`. Assert the listener count in
   the existing alarm-level test.
3. `Buzzer._play`: cap the `await out` at, say, `CYCLES * 1.32 s + 5 s`; on expiry kill the process and return
   False so the next player is tried. Supervisor: treat `wpctl status` timing out on three consecutive checks as
   a wedged PipeWire and restart the user units.
4. `audio.backend.run`: kill on timeout as `Host.run` does. Same one-liner in `timesync/service.py`,
   `net/nmcli.py`, `dab/service.py`.
5. Config and hardware: `missed_grace_minutes: 30` (or more); an RTC; journald persistent.
6. `AlarmService._loop`: set `last_tick` only after a tick that completed, or stop feeding after N consecutive
   failures; `/api/health` returns `alarms_alive`; the face's `useCoreUp` treats `ok && !alarms_alive` as down.
7. Prune `event` rows older than ~90 days in the hourly prune that already runs for metrics.

## Status (0.2.3)

Findings 1, 2, 3, 4, 6, 7 and 8 are fixed in 0.2.3 (see the CHANGELOG), each with a regression test in
`core/tests/test_reliability_fixes.py`; the supervisor also restarts a PipeWire that answers nothing. The
design items (an RTC, `missed_grace_minutes`, a UPS, persistent journald, a scheduled kiosk reload) are left
as recommendations: they are hardware and policy choices.
