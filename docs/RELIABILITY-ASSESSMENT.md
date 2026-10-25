# Reliability assessment: will the alarm sound at the right time?

Scope: dawn-core 0.2.3 (`core/`), the face (`web/src/face`), the systemd units, chrony and installer in `deploy/`.
The question is whether an alarm can be trusted on a clock that runs for months, and what (memory leaks included)
could keep one from ringing. This is the second round; the first (against 0.2.2) is summarised at the end, and every
fix it produced was checked again here.

Method: every file on the path from the 1 Hz engine tick to sound (and to the face's own backup ring) was read;
the full suite was run (259 core tests, 8 dawn-timed tests, 9 web unit specs and the 24 Playwright face and control
tests against the simulator: all pass); five suspected defects
were reproduced with tests; the engine was walked through three simulated days and a matrix of clock steps; the
database open path was tried against a card that has gone read-only; dawn-core was soaked for 15 minutes two ways
(in-process under tracemalloc, and as the plain `dawn-core` process) while the kiosk page sat on it in Chromium;
and the face was made to ring by itself with dawn-core frozen at alarm time. The reproductions and soak tools are
listed under *How to re-run* at the end.

## Verdict

The alarm path is sound. The scheduler is a pure, time-zone-aware function with the right DST semantics; the engine
fires exactly once per occurrence, remembers what it handled across restarts and clock steps, keeps acting on its
own changes when the SD card cannot take them, and hands every ring to a watched ladder of sounds that climbs on
silence. systemd never stops restarting it, the watchdog is gated on the engine actually ticking, a ring survives a
restart, and the face rings by itself when core is gone, frozen, or has lost its alarms. The 0.2.3 fixes hold: a dead
mpv counts as silence, sources let go of their players, a hung tone player is abandoned, helpers are killed on
timeout, a failing tick starves the watchdog, the event log is pruned.

No memory leak was found in dawn-core or in the face. Everything that grows is bounded (listed under *Long-running
behaviour*), and the soaks show flat traced memory, object counts, descriptors, threads, tasks and listener lists
under a load far above a bedside clock's.

What this round did find is smaller than the first round's, and none of it bites on the normal path:

| # | Severity | Where | What happens |
|---|---|---|---|
| 1 | Low likelihood, silent when it happens | `alarms/service.py:428`, `scheduler.py:124` | An alarm whose `last_fired_occurrence` has no time-zone offset (a hand edit, an edited backup) is listed as armed with a next time on the face and in the UI, but the engine skips it every tick. One log line, nothing on the face or in Diagnostics. |
| 2 | Low-Medium | `alarms/ringing.py:274`, `alarms/timers.py:96` | A sleep timer whose 30 s fade overlaps the ring makes the alarm's ramp stop at the bedtime listening volume: the fade's own volume steps look like "the knob wins". |
| 3 | Low | `alarms/service.py:506`, `dab/service.py:510` | A preparation that hangs (a `systemctl restart dawn-dab` that never returns) blocks every later preparation for the life of the process. Alarms still ring, but start on a rung already known to be broken and the "Ready to ring" card freezes. |
| 4 | Low (config) | `config/schema.py:365` | `missed_grace_minutes: 0` is accepted and makes every alarm a miss: the tick always lands a second or more past the minute. |
| 5 | Low | `db/engine.py:55` | The last-resort read-only open (`immutable=1`) ignores SQLite's write-ahead log. It is only reached when the `-shm` file is missing while a `-wal` exists; then alarms edited since the last checkpoint are invisible. The realistic sequences (a crash, then a read-only card at the next boot; a clean stop, then read-only) read everything. |
| 6 | Design, unchanged | no RTC, `alarm_defaults.missed_grace_minutes` 10 | After a power cut the clock boots behind by the outage plus up to an hour and the alarm is marked missed if the time sync lands more than 10 minutes past it. No power at alarm time is the one case nothing covers. |

Findings 1 to 5 are reproduced by tests that pass while the defect is present (`scratchpad/repro/test_repro.py`,
five of six; the sixth is a DST confidence check that also passes). Suggested fixes are at the end; the first four
are a few lines each.

## The findings in detail

### 1. A stamp without a time zone: armed on the face, skipped by the engine

`_tick_row` begins with `datetime.fromisoformat(stamp) > now + timedelta(minutes=10)` and `due_occurrence`
compares the stamp with aware datetimes. A stamp such as `2026-10-01T06:30:00` (no `+11:00`) makes both raise
`TypeError`, which `tick()` catches per row (`_row_failed`) and logs once; the row is skipped on every tick from then
on. `publish()` and `to_out()` never look at the stamp, so the alarm keeps its `next_at`, the face shows it as the next
alarm, Diagnostics is happy, and `engine_alive` stays true, so the face trusts core and nothing rings.

Dawn has always written aware stamps (checked back to the first commit of the engine), so this needs a hand edit of
`dawn.db`, a backup edited by hand, or a future writer that drops the offset. It is listed first anyway because it is
the one failure shape found whose only symptom is silence while every screen says "armed". The fix is small: parse
the stamp once, in the alarm's zone when it has none, and show a row the engine cannot evaluate as broken
(`AlarmSummary.problem`, a Diagnostics row) instead of as scheduled.

### 2. A sleep-timer fade that overlaps the ring stops the ramp

`RingSession._ramp` ends when `audio.volume` differs from the last value it set ("a change by hand"). The sleep
timer's fade (`TimerService._run_sleep`) sets the master volume every second and, when it ends, restores the volume
from before the fade. If the fade's last seconds overlap a ring (a 30-minute sleep timer started at 06:00 with the
alarm at 06:30), the ramp sees the fade's step, stops, and the alarm settles at the restored bedtime volume (20 in
the reproduction) instead of ramping to 70; the ring's own `prev_volume` is then a mid-fade value. It is audible, so
it is not a missed alarm, and it needs that exact overlap; the fix is to end the sleep timer when a ring starts (an
alarm outranks it anyway) or to let the ramp tell its own `set_volume` calls from a hand's.

### 3. A preparation that hangs is never replaced

`_schedule_prepare` creates `_prep_task` and does nothing while one is running; `_prepare` itself has no time limit.
Inside it, `DabService.restart_welle` awaits `sudo systemctl restart dawn-dab` without a timeout (systemd's own
90 s stop timeout bounds it unless systemd or D-Bus is wedged), and `prepare_players` can spend up to ~40 s on an
mpv that answers nothing. While the task hangs, no alarm is prepared, `alarms.prepare` freezes on the face and the
control UI, and every ring starts on its source rung even when the check that hung had already found the source
broken, which costs up to `fallback_after_s` (15 s) of silence. `AlarmService.stop()` cancels the task, so a restart
clears it. Wrap `_prepare` in `asyncio.wait_for(..., 120)` and give the restart a timeout.

### 4. `missed_grace_minutes: 0` disables every alarm

The schema allows 0 (`ge=0`); `_tick_row` fires only when `now - due <= grace`, and the tick lands 1 to 3 s after the
minute, so with 0 every occurrence is logged `alarm_missed` and never rings. Nobody should set it, but a setting that
turns every alarm off should not be accepted: make it `ge=1` (or compare in whole minutes).

### 5. The read-only fallback can show alarms as of the last checkpoint

`Database.__init__` tries a writable open, then `mode=ro&immutable=1`, then an empty in-memory database.
`immutable=1` tells SQLite the file cannot change, so it never reads the `-wal` file, and rows committed since the
last checkpoint are invisible. The experiments (Dawn's own `Database` class, a bind mount remounted read-only):

| Scenario | Files present | What Dawn does | Alarms read |
|---|---|---|---|
| A crash (or the watchdog), then the card is read-only at the next start | `dawn.db`, `-wal`, `-shm` | the writable open silently becomes read-only (SQLite downgrades it); `mode` says `rw`; every write fails and is counted | all, including those still in the WAL |
| A clean stop, then read-only at the next start | `dawn.db` only | the writable open fails (no `-shm` can be made), the immutable open is used, `mode` is `ro` | all (the WAL was checkpointed at the stop) |
| A crash, then read-only, with the `-shm` file gone | `dawn.db`, `-wal` | as above | **the alarm set last night is missing** |

The third row is unusual (nothing in Dawn deletes `-shm` on its own), so this is Low. Two things are worth fixing
cheaply: when the immutable open is used while a non-empty `-wal` sits beside the file, say so (`degraded_reason`),
and let the face treat it as `degraded`, since the face's memory of the next alarm is then fresher than the file; and
make Diagnostics tell a read-only card from `write_errors` rather than from `mode`, which reads `rw` in the first row.

### 6. Time after a power cut (design, unchanged)

The reference Pi 4 has no RTC. `fake-hwclock` restores the time saved at shutdown or on the hour, so after a cut the
clock boots behind by the outage plus up to an hour, and the engine evaluates alarms against it until chrony's first
update steps it (`makestep 1 3`). If power returns after the alarm time, the step lands past `missed_grace_minutes`
(10) and the occurrence is logged `alarm_missed`: a cut from 05:00 to 06:45 with a 06:30 alarm rings nothing. The
clock-step matrix (below) confirms every branch behaves as documented, including a late ring within the window.
Recommendations stand: a DS3231 on I2C bus 1 (nothing in the build uses 0x68) or the Pi 5's RTC, so the clock is
right from the first tick; `missed_grace_minutes` 30 to 60, so a late ring beats none; and a UPS HAT if "trust the
alarms" includes storms, because no power during the alarm is the one case nothing in the design covers.

## Long-running behaviour

Everything that accumulates was looked for and is bounded: the state event list (200), the in-memory log (500),
supervisor repairs (50), welle messages (200), the Diagnostics series cache (32 entries), the holiday calendar cache
(64), the AirPlay metadata buffer (4 MB), the per-station DAB maps (one entry per station), the supervisor's
timing maps (one entry per watched thing), the WebSocket hub's client maps (cleared in `finally`), mpv listener lists
(one per live source since 0.2.3), and the SQLite tables (metrics pruned to `history_days`, events to `events_days`,
hourly). Every `setInterval`/`setTimeout`/listener in the face is cleared on unmount; the scene animates with CSS, not
timers; the beeper reuses one `AudioContext`.

### Soak: dawn-core, 15 minutes two ways

The driver (`scratchpad/soak/soak_driver.py`) runs a play/stop cycle every 7 s across DAB stations (with retunes),
the chime and presets; a test ring every 17 s with a snooze and a stop; an alarm created, listed, edited, skip-next
and enabled toggled, and deleted every 12 s; a face touch or encoder event every 1.5 s; a sweep of thirteen GET
endpoints (state, health, alarms, events, the Diagnostics report and history, the backup document, stations,
presets, config, logs, sun, weather) every 9 s; a config patch (a hot reload of every service) every 45 s; a sleep
timer and a nap timer every two minutes; two persistent WebSocket clients (one announcing itself as the face, with
its 15 s pings) and one that connects, listens 12 s and leaves, a third of the time without closing; and a Chromium
kiosk page on the face. In 15 minutes: 38 rings, 131 play cycles, 72 alarm edit cycles, 572 inputs, 1213 polls,
19 hot reloads, 61 transient socket clients, and 12 000 state messages (198 MB) to the two persistent clients.

| | start (after the scan) | 2 min | 8.5 min | 15 min |
|---|---|---|---|---|
| plain `dawn-core` process: RSS | 123 MB | 125 MB | 135 MB | 135 MB, unchanged from 8.5 min on |
| plain process: threads / descriptors | 8 / 35 | 9 / 36 | 9 / 43 | 8 / 37 |
| in-process run: Python-traced memory | 60.8 MB | 60.8 MB | 62.6 MB (peak 62.8) | 62.6 MB |
| in-process run: reachable objects | 150 552 | 150 571 | | 150 851 |
| in-process run: asyncio tasks | 42 | 44 | | 41 |

Everything watched inside the process stayed where it should: mpv listener lists at 0 to 3 (one per live source),
arbiter slots 0 to 3, WebSocket clients 2 to 4 with every departed client gone from the hub's maps, state
subscribers 2, holiday calendars 1 to 2, DAB station maps 11, supervisor timing maps 12, repairs 0, the in-memory
log at 34 of 500. tracemalloc's growth table from the 2-minute baseline to the end names nothing in `dawn_core`
beyond the Diagnostics history filling its one-hour window (+55 KB, bounded by hours times keys) and 3 KB of log
records; the rest is the soak client's own WebSocket buffers. The RSS plateau at 135 MB after eight minutes is the
allocator and SQLite's page cache settling, not growth: it did not move in the last six minutes. One note on the
soak itself: its touch events snoozed every test ring within two seconds, so the ladder's fallback rungs were
exercised by the suite and by the end-to-end test below rather than under this load.

### Soak: the kiosk page, 13 minutes

`soak_face.js` opened `/face` in Chromium (800 by 480, touch) against each soaked core and read the page's heap and
DOM over the DevTools protocol every 30 s after a forced garbage collection, while the page cycled through standby,
playing, the menu, ringing (snoozed, stopped), the nap countdown and the sleep timer.

| | start | 2 min | 13 min | range over the run |
|---|---|---|---|---|
| JS heap in use, after GC | 2.4 MB | 2.6 MB | 2.9 MB | 2.4 to 3.1 MB |
| DOM nodes | 41 to 72 | 109 to 136 | 41 to 73 | 41 to 295 (the ringing and playing screens) |
| JS event listeners | 150 | 155 to 161 | 150 to 153 | 150 to 169 |
| documents / frames | 1 | 1 to 2 | 1 | |

No page errors in either run. Nodes and listeners return to their baseline after each screen; the heap moves with
the screen and does not trend. This covers the page's JavaScript and DOM; Chromium's own renderer and compositor
memory on a page that never reloads is not measured here, which is why round 1's suggestion of a daily reload at an
idle moment (never while ringing or inside the preparation window) stands: the supervisor's kiosk restart covers a
page that stops talking, not one that slowly bloats.

CPU, not memory, is the cost that grows with use: every mpv property change republishes the whole state (with a
presets query), the state message is ~18 KB and goes to every client on every change (coalesced to 40 ms; several a
second while something plays or rings), and during a ring `pw-dump` runs once a second. Fine on a Pi 4; on a Zero 2
it is the thing to watch, and it does not affect whether the alarm rings.

## What was verified solid

- **Scheduling.** `scheduler.py` is pure and tested for DST, leap days, holidays, leave and skip-next. A walk of the
  real engine (`scratchpad/repro/test_walk.py`) through 3 to 6 April 2026 (the AEDT to AEST change) with seven alarm
  kinds fired exactly these and nothing else: weekdays on the Monday only; daily three times; custom (Sat, Sun) twice;
  once at 23:30 then off; a 02:30 alarm once per day including the night the hour repeats; skip-next skipped exactly one
  and rang after; the holiday-skipping alarm never, because Good Friday to Easter Monday are all NSW holidays. No
  `alarm_missed`, no failed tick.
- **Clock steps.** Forward 8 minutes across the alarm: rings, 180 s late. Forward 15 minutes: a miss, logged. Back
  5 minutes after a ring: no second ring. Back 15 minutes after a ring: rings again at the real 06:30 (the first was on
  a fast clock; documented). A boot an hour behind whose sync lands 2 minutes past the alarm: rings, 120 s late.
- **Storage.** Writes fail safely in memory first (`_handled`, `_disabled`, `_skip_spent`); the read-only card
  experiments above; the suite's read-only, full-disk, corrupt-row and unreadable-database tests.
- **The ladder.** Source to chime to backup tone with every rung watched through the alarm's own slot; a dead mpv,
  a closed IPC, a hung tone player, a failing sound check, a backup tone that stops: all climb (suite). The face beeps
  on the tone rung.
- **Restarts.** A ring and a snooze are saved every 30 s and resumed (`_resume_ring`) with the downtime counted
  against `max_ring_minutes`; the nap timer too; a stop that could not be saved leaves a marker in `/run/dawn`
  (`RuntimeDirectoryPreserve=yes`). `dawn-core.service`: `Restart=always`, `StartLimitIntervalSec=0`, `WatchdogSec=60`
  fed every 5 s only while the engine ticked within 30 s, `READY=1` sent by the first service; the hardware watchdog
  at 15 s.
- **The face as the last line.** `web/src/face/backup.ts` keeps the next alarm and any ring in localStorage, counts
  core as down when no state has come for 20 s and `/api/health` does not answer (or answers `alarms_alive: false`),
  and rings by itself 45 s after the alarm for up to 30 minutes, snooze and stop on the face, handing back when core
  rings. The five backup unit specs pass. End to end (`scratchpad/soak/run_hang.sh`, a real `dawn-core` process
  and Chromium on the simulator): an alarm set for 14:01, dawn-core frozen with SIGSTOP 25 s before it; the face
  rang by itself and beeped 46 s after the alarm ("backup alarm · Dawn is not responding"); core thawed 60 s after
  the alarm fired the occurrence at once (`alarm_fire`, 60 s late, inside the grace window) and the face handed the
  ring back within a second. Note that a frozen core keeps its socket open, so the face never showed "offline": it
  judged core down from 20 s without state and a health probe that did not answer, which is the harder case.
- **The 0.2.3 fixes.** `test_reliability_fixes.py` (21 tests) passes; the code paths were re-read.

## Suggested fixes, smallest first

1. `scheduler._parse` and `_tick_row`: parse `last_fired_occurrence` once; a stamp without a zone gets the clock's
   zone. Add `problem: str | None` to `AlarmSummary`, set from `_row_errors`, so a row the engine cannot evaluate shows
   as broken on the face and in Diagnostics rather than as scheduled.
2. `AlarmDefaults.missed_grace_minutes`: `ge=1`.
3. `AlarmService.start_ring`: cancel a running sleep timer (`TimerService.cancel_sleep(resume=False)`) before the
   session starts, or have `_ramp` compare against a counter of programmatic volume changes instead of the value.
4. `AlarmService._schedule_prepare`: `asyncio.wait_for(self._prepare(...), 120)`; `DabService.restart_welle`:
   `wait_for(proc.wait(), 60)` with a kill. The same timeout on the other bare `proc.wait()` calls
   (`airplay/service.py:101`, `api/router_system.py:42`) costs nothing.
5. `Database.__init__`: when the immutable open is used and `dawn.db-wal` exists and is not empty, set
   `open_error` to say recent changes may be missing and count it as degraded for the alarms; Diagnostics: report
   "read-only" from `write_errors` as well as `mode`.
6. Hardware and policy: an RTC; `missed_grace_minutes: 30`; `Storage=persistent` with `SystemMaxUse=64M` for
   journald (the journal is in RAM on Raspberry Pi OS, so the night's logs die with the reboot that follows a
   failure); a UPS if storms count.

## History

**Round 1 (0.2.2, October 2026)** found eight things and 0.2.3 fixed seven: an mpv that died mid-ring still counted
as "audio flowing" when PipeWire was not the backend; every play added a never-removed listener to its mpv player
(the one true leak, CPU as much as memory); a hung backup-tone player counted as sounding for ever; helpers that timed
out were left running; a tick that raised every time still fed the watchdog and `/api/health`; the event table was
never pruned; and a station picked during a ring came back silent. The eighth was the power-cut design risk above.
All seven fixes were re-verified in this round.

## How to re-run

Everything used here is in [`docs/reliability/`](reliability/README.md): the reproductions (`test_repro.py`, which
pass while a defect is present), the engine walks (`test_walk.py`), the read-only card experiment (`ro_writer.py`,
`ro_reader.py`), the two core soaks (`soak_driver.py`, `soak_core.py`), the kiosk page soak (`soak_face.js`) and the
frozen-core test (`run_hang.sh`). The suite itself is `make test` and, in `web/`, `npx playwright test`.
