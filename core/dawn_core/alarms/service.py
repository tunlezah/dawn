"""AlarmService: alarm storage, the 1 Hz engine tick, firing, light-wake, preparation, ringing.

The engine is written so that an alarm rings even when other things are broken:
- Every write it makes (the occurrence it handled, a spent skip, a once-alarm switching itself off) takes effect
  in memory first and is then saved best-effort, so a database that has gone read-only (a failing SD card) or a
  full disk cannot keep an alarm from ringing, nor make it ring twice.
- Each alarm is evaluated on its own: one malformed row (a hand edit, a foreign backup) is logged and skipped
  instead of stopping every other alarm. If the alarms cannot be read at all, the last copy read is used.
- Firing never waits for the sound: the ring shows at once and its sound starts in the background
  (alarms/ringing.py), so the tick keeps its 1 Hz pace and the watchdog can trust it (`last_tick`).
- A ring in progress is saved, so a restart of dawn-core (a crash, the systemd watchdog, an update) carries on
  with it, ringing or still snoozed, instead of silently dropping it.
- In the minutes before each alarm (`alarm_defaults.prepare_minutes`) its sound is made ready: the alarm's
  players are started, a DAB station is tuned and in sync, scans are held off, and what will not play is known
  in advance, so the ring starts on a rung that works instead of waiting in silence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import select

from ..audio.service import AudioService
from ..config import DawnConfig
from ..context import DawnContext
from ..db.models import AlarmRow
from ..services import Service
from ..state.ui import AlarmPrep, AlarmSummary, NextAlarm
from .buzzer import Buzzer, BuzzerSource
from .holidays import HolidayCalendar
from .ringing import RingRequest, RingSession
from .scheduler import AlarmSpec, due_occurrence, next_occurrence
from .schemas import AlarmIn, AlarmOut

log = logging.getLogger("dawn.alarms")

PREP_EVERY_S = 20.0  # re-check a prepared alarm this often
PREP_RETRY_S = 5.0  # and this often while something is not ready
PREP_FRESH_S = 60.0  # a check older than this does not decide where a ring starts
RESUME_MAX_AGE = timedelta(hours=4)  # a saved ring older than this is not carried on after a restart


class RingBusy(RuntimeError):
    """A test ring was asked for while an alarm rings."""


def parse_days(days: str | None) -> list[int]:
    """The stored "0,1,2" form, ignoring anything that is not a weekday number."""
    out = []
    for x in (days or "").split(","):
        x = x.strip()
        if x.isdigit() and 0 <= int(x) <= 6:
            out.append(int(x))
    return sorted(set(out))


def _later(a: str | None, b: str | None) -> str | None:
    """The later of two ISO stamps (a malformed one loses)."""
    if not a or not b:
        return a or b
    try:
        da = datetime.fromisoformat(a)
    except ValueError:
        return b
    try:
        return a if da >= datetime.fromisoformat(b) else b
    except ValueError:
        return a


def source_family(ref: str) -> str | None:
    """The player family a source reference plays through (None for sources without a player)."""
    scheme = ref.partition(":")[0]
    return {"dab": "dab", "chime": "chime", "url": "media", "playlist": "media"}.get(scheme)


class AlarmService(Service):
    name = "alarms"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.ring: RingSession | None = None
        self.buzzer = Buzzer(ctx)
        self._task: asyncio.Task[None] | None = None
        self._calendars: dict[tuple[str, str], HolidayCalendar] = {}
        self._light_wake_alarm: int | None = None
        self._light_wake_occurrence: str | None = None  # occurrence ISO currently being lit
        self._light_wake_dismissed: str | None = None  # occurrence ISO
        self._last_summary: Any = None
        # what the engine changed but the database may not have taken (see the module docstring)
        self._handled: dict[int, str] = {}  # alarm id -> latest occurrence handled
        self._pulled: dict[int, str] = {}  # alarm id -> a stored stamp from a fast clock, to be ignored
        self._disabled: set[int] = set()  # once-alarms that switched themselves off
        self._skip_spent: set[int] = set()  # alarms whose skip-next has been used
        self._rows_cache: list[AlarmRow] = []
        self._rows_error: str | None = None
        self._row_errors: dict[int, str] = {}  # logged once per alarm and message
        self._leave: date | None = None
        self.last_tick: float | None = None  # monotonic time the last engine tick ended (the watchdog checks it)
        self.started: float | None = None
        # preparation of the next alarm
        self._prep: AlarmPrep | None = None
        self._prep_key: tuple[int, str] | None = None
        self._prep_task: asyncio.Task[None] | None = None
        self._prep_due = 0.0
        self._prep_at = 0.0  # monotonic time of the last finished check
        self._dab_fail: dict[tuple[int, str], int] = {}  # consecutive failed DAB checks per occurrence
        self._next_dab: tuple[str, datetime] | None = None  # the next alarm on DAB: (label, when)

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.started = time.monotonic()
        # the engine first: nothing below may keep it from running (and once only, should start() be tried again)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="alarm-engine")
        try:
            self.ctx.svc(AudioService).register_factory("buzzer", self._make_buzzer)
        except KeyError:
            log.error("no audio service: alarms can only show on the face")
        try:
            self.ctx.store.state.alarms.on_leave_until = self.ctx.db.get("alarms.leave_until")
        except Exception:  # noqa: BLE001
            log.exception("cannot read the leave setting")
        self.publish_safe(force=True)
        await self._resume_ring()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._prep_task:
            self._prep_task.cancel()
        if self.ring:
            await self.ring.stop("shutdown", forget=False)
        self.buzzer.close()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.holidays != new.holidays or old.general.timezone != new.general.timezone:
            self._calendars.clear()
        self.publish_safe(force=True)

    async def _make_buzzer(self, arg: str, level: str = "alarm") -> BuzzerSource:
        if level != "alarm":
            raise ValueError("the backup tone only plays for alarms")
        return BuzzerSource(self.buzzer)

    def alive(self, max_age_s: float = 30.0) -> bool:
        """The engine is ticking (the systemd watchdog is only fed while it is). Before it has started (the services
        start one after another at boot, the heartbeat first) it counts as alive."""
        if self.started is None:
            return True
        if self._task is None or self._task.done():
            return False
        now = time.monotonic()
        if self.last_tick is None:
            return self.started is not None and now - self.started < 90
        return now - self.last_tick < max_age_s

    # ---- holidays --------------------------------------------------------
    def calendar(self, region: str | None = None, scope: str | None = None) -> HolidayCalendar:
        h = self.ctx.config.holidays
        key = (region or h.region, scope or h.scope)
        cal = self._calendars.get(key)
        if cal is None:
            cal = HolidayCalendar(key[0], key[1], h.extra_dates, h.exclude_names)
            self._calendars[key] = cal
        return cal

    # ---- storage ---------------------------------------------------------
    def rows(self) -> list[AlarmRow]:
        with self.ctx.db.session() as s:
            return list(s.exec(select(AlarmRow).order_by(AlarmRow.time)).all())  # type: ignore[attr-defined]

    def rows_safe(self) -> list[AlarmRow]:
        """The alarms as the engine sees them: read fresh (the last copy if the database cannot be read), with the
        engine's own unsaved changes on top."""
        try:
            rows = self.rows()
            self._rows_cache = rows
            if self._rows_error:
                log.warning("alarms can be read again")
            self._rows_error = None
        except Exception as e:  # noqa: BLE001
            if self._rows_error is None:
                log.error("cannot read the alarms (%s); using the last copy read (%d alarms)", e, len(self._rows_cache))
            self._rows_error = str(e)
            rows = self._rows_cache
        for row in rows:
            rid = row.id or 0
            stored = row.last_fired_occurrence
            if stored is not None and self._pulled.get(rid) == stored:
                stored = None  # a stamp from a fast clock that has been pulled back (see _tick_row)
            row.last_fired_occurrence = _later(stored, self._handled.get(rid))
            if rid in self._disabled:
                row.enabled = False
            if rid in self._skip_spent:
                row.skip_next = False
        return rows

    def get(self, alarm_id: int) -> AlarmRow | None:
        with self.ctx.db.session() as s:
            return s.get(AlarmRow, alarm_id)

    def _armed(self, now: datetime | None) -> str:
        """An alarm only answers for occurrences after it was created, edited or switched on: one set at 22:00
        for 06:30 must not count this morning's 06:30 as missed (and a once-alarm must not disable itself)."""
        return (now or self.ctx.store.now()).isoformat(timespec="seconds")

    def _forget(self, alarm_id: int) -> None:
        """The user changed this alarm: the engine's unsaved changes to it no longer apply."""
        self._handled.pop(alarm_id, None)
        self._pulled.pop(alarm_id, None)
        self._disabled.discard(alarm_id)
        self._skip_spent.discard(alarm_id)
        self._row_errors.pop(alarm_id, None)

    def create(self, data: AlarmIn, *, now: datetime | None = None) -> AlarmRow:
        row = AlarmRow(**data.model_dump(exclude={"days"}), days=",".join(map(str, data.days)), last_fired_occurrence=self._armed(now))
        with self.ctx.db.session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        self._forget(row.id or 0)
        self.ctx.db.log_event("alarm_create", id=row.id, label=row.label, time=row.time)
        self.publish_safe(force=True)
        return row

    def update(self, alarm_id: int, data: AlarmIn, *, now: datetime | None = None) -> AlarmRow | None:
        with self.ctx.db.session() as s:
            row = s.get(AlarmRow, alarm_id)
            if not row:
                return None
            for k, v in data.model_dump(exclude={"days"}).items():
                setattr(row, k, v)
            row.days = ",".join(map(str, data.days))
            row.last_fired_occurrence = self._armed(now)
            row.updated_at = datetime.now(UTC)
            s.add(row)
            s.commit()
            s.refresh(row)
        self._forget(alarm_id)
        self.publish_safe(force=True)
        return row

    def set_enabled(self, alarm_id: int, enabled: bool, *, now: datetime | None = None) -> AlarmRow | None:
        row = self.get(alarm_id)
        if row is None:
            return None
        if enabled and (not row.enabled or alarm_id in self._disabled):
            out = self.patch(alarm_id, enabled=True, last_fired_occurrence=self._armed(now))
            self._forget(alarm_id)  # armed from now: what the engine remembered no longer applies
            return out
        # anything else keeps an occurrence handled but not saved, so it cannot ring twice once saving works again
        return self.patch(alarm_id, enabled=enabled)

    def set_skip_next(self, alarm_id: int, skip: bool) -> AlarmRow | None:
        out = self.patch(alarm_id, skip_next=skip)
        self._skip_spent.discard(alarm_id)
        return out

    def patch(self, alarm_id: int, **fields: Any) -> AlarmRow | None:
        with self.ctx.db.session() as s:
            row = s.get(AlarmRow, alarm_id)
            if not row:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            s.add(row)
            s.commit()
            s.refresh(row)
        self.publish_safe(force=True)
        return row

    def delete(self, alarm_id: int) -> bool:
        with self.ctx.db.session() as s:
            row = s.get(AlarmRow, alarm_id)
            if not row:
                return False
            s.delete(row)
            s.commit()
        self._forget(alarm_id)
        self.publish_safe(force=True)
        return True

    def reset_memory(self) -> None:
        """The alarm table was replaced (a backup restored): drop everything remembered about the old rows."""
        self._handled.clear()
        self._pulled.clear()
        self._disabled.clear()
        self._skip_spent.clear()
        self._row_errors.clear()
        self._rows_cache = []

    def _save(self, row: AlarmRow, **fields: Any) -> None:
        """A change the engine makes to an alarm. It takes effect in memory at once and is then saved; if saving
        fails the engine keeps acting on it for as long as dawn-core runs, so the alarm neither rings twice nor
        stops ringing because the storage broke."""
        rid = row.id or 0
        for k, v in fields.items():
            setattr(row, k, v)
        if "last_fired_occurrence" in fields:
            self._handled[rid] = _later(fields["last_fired_occurrence"], self._handled.get(rid)) or fields["last_fired_occurrence"]
        if fields.get("enabled") is False:
            self._disabled.add(rid)
        if fields.get("skip_next") is False:
            self._skip_spent.add(rid)
        try:
            self.patch(rid, **fields)
        except Exception as e:  # noqa: BLE001
            self.ctx.db.write_failed(f"alarm {row.label}", e)

    def _row_failed(self, row: AlarmRow, e: BaseException) -> None:
        rid = row.id or 0
        msg = f"{type(e).__name__}: {e}"
        if self._row_errors.get(rid) != msg:
            self._row_errors[rid] = msg
            log.error("alarm %s (%r) cannot be evaluated and is skipped: %s", rid, row.label, msg)

    # ---- scheduling helpers ---------------------------------------------
    @staticmethod
    def spec_for(row: AlarmRow) -> AlarmSpec:
        return AlarmSpec(
            time=row.time, repeat=row.repeat, days=parse_days(row.days), enabled=row.enabled, skip_public_holidays=row.skip_public_holidays,
            skip_next=row.skip_next, last_fired_occurrence=row.last_fired_occurrence,
        )

    def leave_until(self) -> date | None:
        try:
            v = self.ctx.db.get("alarms.leave_until")
        except Exception:  # noqa: BLE001
            return self._leave  # the database cannot answer: the last value read
        try:
            self._leave = date.fromisoformat(v) if v else None
        except (TypeError, ValueError):
            self._leave = None
        return self._leave

    def _holiday_fn(self, row: AlarmRow):
        if not row.skip_public_holidays:
            return None
        return self.calendar(row.holiday_region, row.holiday_scope).is_holiday

    def next_for(self, row: AlarmRow, after: datetime | None = None) -> datetime | None:
        after = after or self.ctx.store.now()
        return next_occurrence(self.spec_for(row), after, self.ctx.store.tz, self._holiday_fn(row), self.leave_until())

    def to_out(self, row: AlarmRow) -> AlarmOut:
        try:
            nxt = self.next_for(row)
        except Exception as e:  # noqa: BLE001
            self._row_failed(row, e)
            nxt = None
        return AlarmOut(
            id=row.id or 0, label=row.label, enabled=row.enabled, time=row.time, repeat=row.repeat,  # type: ignore[arg-type]
            days=parse_days(row.days), skip_public_holidays=row.skip_public_holidays,
            holiday_region=row.holiday_region, holiday_scope=row.holiday_scope, source=row.source, volume=row.volume,  # type: ignore[arg-type]
            ramp_seconds=row.ramp_seconds, snooze_minutes=row.snooze_minutes, fallback_after_s=row.fallback_after_s,
            max_ring_minutes=row.max_ring_minutes, skip_next=row.skip_next, light_wake=row.light_wake,
            light_wake_minutes=row.light_wake_minutes, next_at=nxt.isoformat(timespec="seconds") if nxt else None,
            last_fired_occurrence=row.last_fired_occurrence,
        )

    def _resolve(self, ref: str) -> str:
        try:
            return self.ctx.svc(AudioService).resolve(ref)
        except Exception:  # noqa: BLE001
            return ref

    # ---- engine ----------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("alarm tick failed")
            self.last_tick = time.monotonic()
            await asyncio.sleep(1.0)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or self.ctx.store.now()
        tz = self.ctx.store.tz
        grace = timedelta(minutes=self.ctx.config.alarm_defaults.missed_grace_minutes)
        leave = self.leave_until()
        light_candidate: tuple[int, datetime] | None = None
        prep_candidate: tuple[AlarmRow, datetime] | None = None
        next_dab: tuple[str, datetime] | None = None
        for row in self.rows_safe():
            if not row.enabled:
                continue
            try:
                nxt = await self._tick_row(row, now, tz, grace, leave)
            except Exception as e:  # noqa: BLE001
                self._row_failed(row, e)
                continue
            if nxt is None:
                continue
            if row.light_wake and self.ring is None and nxt - now <= timedelta(minutes=row.light_wake_minutes):
                if self._light_wake_dismissed != nxt.isoformat() and (light_candidate is None or nxt < light_candidate[1]):
                    light_candidate = (row.id or 0, nxt)
            if prep_candidate is None or nxt < prep_candidate[1]:
                prep_candidate = (row, nxt)
            if self._resolve(row.source).startswith("dab:") and (next_dab is None or nxt < next_dab[1]):
                next_dab = (row.label, nxt)
        self._next_dab = next_dab
        active = light_candidate is not None
        self._light_wake_alarm = light_candidate[0] if light_candidate else None
        self._light_wake_occurrence = light_candidate[1].isoformat() if light_candidate else None
        if active != self.ctx.store.state.alarms.light_wake_active:
            self.ctx.store.state.alarms.light_wake_active = active
            self.ctx.db.log_event("light_wake", active=active, alarm_id=self._light_wake_alarm)
            self.ctx.store.touch()
        self._schedule_prepare(prep_candidate, now)
        self.publish_safe()

    async def _tick_row(self, row: AlarmRow, now: datetime, tz, grace: timedelta, leave: date | None) -> datetime | None:
        """Fire, miss or skip what is due for one alarm; returns its next occurrence after `now`."""
        stamp = row.last_fired_occurrence
        if stamp and datetime.fromisoformat(stamp) > now + timedelta(minutes=10):
            # stamped while the clock was fast, and it has been stepped back since: only up to now is handled.
            # The stored stamp is ignored from here on, even if the database cannot take the new one.
            rid = row.id or 0
            self._pulled[rid] = stamp
            self._handled.pop(rid, None)
            self._save(row, last_fired_occurrence=now.isoformat(timespec="seconds"))
        spec = self.spec_for(row)
        hol = self._holiday_fn(row)
        # a pending skip whose occurrence has now passed: consume it
        if row.skip_next:
            plain = AlarmSpec(**{**spec.__dict__, "skip_next": False})
            skipped = next_occurrence(plain, now - timedelta(hours=26), tz, hol, leave, horizon_days=3)
            while skipped is not None and spec.last_fired_occurrence and datetime.fromisoformat(spec.last_fired_occurrence) >= skipped:
                skipped = next_occurrence(plain, skipped, tz, hol, leave, horizon_days=3)
            if skipped is not None and skipped <= now:
                log.info("alarm %s: skipped occurrence %s", row.label, skipped)
                self.ctx.db.log_event("alarm_skipped", id=row.id, label=row.label, occurrence=skipped.isoformat())
                self._save(row, skip_next=False, last_fired_occurrence=skipped.isoformat())
                return next_occurrence(self.spec_for(row), now, tz, hol, leave)
        due = due_occurrence(spec, now, tz, hol, leave)
        if due is not None:
            age = now - due
            if age <= grace:
                await self.fire(row, due, late_s=int(age.total_seconds()))
            else:
                log.warning("alarm %s missed (%s, %d min late)", row.label, due, age.total_seconds() // 60)
                self.ctx.db.log_event("alarm_missed", id=row.id, label=row.label, occurrence=due.isoformat(), late_minutes=int(age.total_seconds() // 60))
                self._save(row, last_fired_occurrence=due.isoformat(), **({"enabled": False} if row.repeat == "once" else {}))
            if not row.enabled:
                return None
        return next_occurrence(self.spec_for(row), now, tz, hol, leave)

    async def fire(self, row: AlarmRow, occurrence: datetime, late_s: int = 0) -> None:
        """Ring this occurrence, then mark it handled. The ring shows at once and its sound starts in the background,
        so this is quick; if starting the ring raised, the occurrence stays due and the next tick tries again."""
        self._light_wake_dismissed = occurrence.isoformat()
        await self.start_ring(self._request(row, occurrence))
        self._save(row, last_fired_occurrence=occurrence.isoformat(), **({"enabled": False} if row.repeat == "once" else {}))
        self.ctx.db.log_event("alarm_fire", id=row.id, label=row.label, occurrence=occurrence.isoformat(), late_s=late_s, source=row.source)

    def _request(self, row: AlarmRow, occurrence: datetime) -> RingRequest:
        """The ring for an alarm, with its defaults standing in for anything missing or out of range in the row."""
        d = self.ctx.config.alarm_defaults

        def num(v: Any, default: int, lo: int, hi: int) -> int:
            try:
                n = int(v)
            except (TypeError, ValueError):
                return default
            return n if lo <= n <= hi else default

        tier, reason = self._start_tier(row.id or 0, occurrence)
        return RingRequest(
            kind="alarm", label=str(row.label or "Alarm"), source=str(row.source or f"chime:{d.chime}"), volume=num(row.volume, d.volume, 1, 100),
            ramp_seconds=num(row.ramp_seconds, d.ramp_seconds, 0, 600), ramp_start_percent=d.ramp_start_percent,
            snooze_minutes=num(row.snooze_minutes, d.snooze_minutes, 1, 60), fallback_after_s=num(row.fallback_after_s, d.fallback_after_s, 3, 120),
            buzzer_after_s=d.buzzer_after_s, max_ring_minutes=num(row.max_ring_minutes, d.max_ring_minutes, 1, 180), chime=d.chime,
            alarm_id=row.id, occurrence=occurrence.isoformat(), start_tier=tier, start_reason=reason,
        )

    # ---- preparation -------------------------------------------------------
    def _schedule_prepare(self, cand: tuple[AlarmRow, datetime] | None, now: datetime) -> None:
        mins = self.ctx.config.alarm_defaults.prepare_minutes
        if cand is None or mins <= 0 or cand[1] - now > timedelta(minutes=mins) or self.ring is not None:
            if self._prep is not None or self._prep_key is not None:
                self._prep, self._prep_key = None, None
                self.ctx.store.state.alarms.prepare = None
                self.ctx.store.touch()
            return
        row, at = cand
        key = (row.id or 0, at.isoformat())
        if key != self._prep_key:
            self._prep_key, self._prep, self._prep_due = key, None, 0.0
            self._dab_fail = {k: v for k, v in self._dab_fail.items() if k == key}  # the old occurrences are over
            log.info("preparing %s for %s", row.label, at.strftime("%H:%M"))
        if (self._prep_task and not self._prep_task.done()) or time.monotonic() < self._prep_due:
            return
        self._prep_task = asyncio.create_task(self._prepare(row, at, key), name="alarm-prepare")

    async def _prepare(self, row: AlarmRow, at: datetime, key: tuple[int, str]) -> None:
        """Get one alarm's sound ready, and work out the rung it will start on if nothing changes."""
        problems: list[str] = []
        pending = True  # every problem so far is a step under way (tuning), not something wrong
        start = "source"
        ref = self._resolve(row.source)
        family = source_family(ref)
        try:
            audio = self.ctx.svc(AudioService)
            failed = await audio.prepare_players([f for f in dict.fromkeys([family, "chime"]) if f])
            problems += failed
            pending = pending and not failed
            source_player_ok = not any(p.startswith(f"{family} player") for p in failed)
            chime_ok = not any(p.startswith("chime player") for p in failed)
            no_output = not audio.has_output()
            if no_output:
                problems.append("no audio output found")
                pending = False
            if family and not source_player_ok:
                start = "chime" if family != "chime" else "buzzer"
            if no_output:
                start = "buzzer"  # nothing to play to: the GPIO buzzer and the face at once
            if ref.startswith("dab:") and start == "source":
                dab_problem, hard, under_way = await self._prepare_dab(ref[4:], key)
                if dab_problem:
                    problems.append(dab_problem)
                    pending = pending and under_way
                    if hard:
                        start = "chime"
            if start == "chime" and not chime_ok:
                start = "buzzer"
        except Exception as e:  # noqa: BLE001
            log.exception("preparing %s failed", row.label)
            problems.append(f"preparation failed: {e}")
            pending = False
        if key != self._prep_key:
            return  # the target moved on while this ran
        prev = self._prep
        self._prep = AlarmPrep(
            alarm_id=key[0], label=row.label, at=at.isoformat(timespec="seconds"), source=ref, ready=not problems, pending=bool(problems) and pending,
            problems=problems, start_tier=start, checked_at=self.ctx.store.iso(),  # type: ignore[arg-type]
        )
        self._prep_at = time.monotonic()
        self._prep_due = self._prep_at + (PREP_EVERY_S if not problems else PREP_RETRY_S)
        self.ctx.store.state.alarms.prepare = self._prep
        self.ctx.store.touch()
        if prev is None or prev.problems != problems:
            if not problems:
                log.info("%s at %s is ready", row.label, at.strftime("%H:%M"))
            else:
                (log.info if self._prep.pending else log.warning)("%s at %s: %s (starts on the %s)", row.label, at.strftime("%H:%M"), "; ".join(problems), start)
            self.ctx.db.log_event("alarm_prepare", id=key[0], label=row.label, occurrence=key[1], ready=not problems, problems=problems, start_tier=start)

    async def _prepare_dab(self, sid: str, key: tuple[int, str]) -> tuple[str | None, bool, bool]:
        """(what is not ready, whether the ring should start on the chime because of it, whether it is a step
        under way rather than a fault)."""
        try:
            from ..dab.service import DabService

            dab = self.ctx.svc(DabService)
        except (ImportError, KeyError):
            return "no DAB service", True, False
        audio = self.ctx.svc(AudioService)
        listening = [s for s in (audio.arbiter.slot("user"), audio.arbiter.slot("sleep"))
                     if s is not None and s.source.kind == "dab" and s.state in ("playing", "starting", "ducked")]
        status, msg = await dab.prepare_for(sid, may_retune=not listening)
        if status == "ok":
            self._dab_fail.pop(key, None)
            return None, False, False
        if status in ("busy", "tuning"):
            return msg, False, True
        n = self._dab_fail[key] = self._dab_fail.get(key, 0) + 1
        # one failed check may be the tuner settling; two in a row will not come right in time
        return msg, status == "fail" and (n >= 2 or msg in ("no SDR", "station unknown")), False

    def _start_tier(self, alarm_id: int, occurrence: datetime) -> tuple[str, str | None]:
        """Where a ring starts: below its source when a recent check found the source will not play."""
        p = self._prep
        if p is None or p.alarm_id != alarm_id or self._prep_key != (alarm_id, occurrence.isoformat()):
            return "source", None
        if time.monotonic() - self._prep_at > PREP_FRESH_S or p.start_tier == "source":
            return "source", None
        return p.start_tier, "not ready: " + "; ".join(p.problems)

    def dab_conflict(self, within_s: float = 0.0, *, ringing_only: bool = False) -> str | None:
        """Why the DAB tuner must not be taken away now: an alarm on DAB ringing or snoozed, or (unless
        `ringing_only`) one due within the preparation window plus `within_s`, the time the caller needs it for."""
        r = self.ring
        if r is not None and r.active and self._resolve(r.req.source).startswith("dab:") and not r.fallback:
            return f"{r.req.label} is {'snoozed' if r.snoozed else 'ringing'} on the radio"
        if ringing_only or self._next_dab is None:
            return None
        label, at = self._next_dab
        lead = self.ctx.config.alarm_defaults.prepare_minutes * 60 + within_s
        if (at - self.ctx.store.now()).total_seconds() <= lead:
            return f"{label} rings at {at.strftime('%H:%M')} on the radio"
        return None

    # ---- ringing ---------------------------------------------------------
    async def start_ring(self, req: RingRequest) -> RingSession:
        cur = self.ring
        if cur is not None and cur.active and cur.req.kind == "alarm" and req.kind != "alarm":
            if req.kind == "test":
                raise RingBusy(f"{cur.req.label} is ringing; stop it first")
            # a nap running out while an alarm rings or is snoozed: the alarm is what wakes you
            log.info("%s ended while %s is %s; the alarm carries on", req.label, cur.req.label, "snoozed" if cur.snoozed else "ringing")
            self.ctx.db.log_event("ring_merged", ring_kind=req.kind, label=req.label, into=cur.req.label)
            await cur.wake_now(f"{req.label.lower()} ended")
            return cur
        if cur is not None:
            log.info("replacing active ring %s with %s", cur.req.label, req.label)
            await cur.stop("replaced")
        session = RingSession(self.ctx, req, self._ring_done, buzzer=self.buzzer, persist=self._persist_ring)
        self.ring = session
        await session.start()
        return session

    def _persist_ring(self, data: dict[str, Any] | None, started: str) -> None:
        ok = self.ctx.db.try_set("alarms.ring", data)
        marker = self.ctx.runtime_dir / "ring-stopped"
        try:
            if data is None and not ok:
                # the database still holds the ring: note in /run that it was stopped, so a restart does not bring it back
                marker.write_text(started)
            elif ok and marker.exists():
                marker.unlink()
        except OSError:
            pass

    async def _resume_ring(self) -> None:
        """A ring that a restart of dawn-core cut short (a crash, the watchdog, an update) carries on: ringing again
        now, or still snoozed until its time."""
        try:
            data = self.ctx.db.get("alarms.ring")
        except Exception:  # noqa: BLE001
            return
        if not isinstance(data, dict):
            return
        now = self.ctx.store.now()
        try:
            req = RingRequest.from_dict(data["req"])
            started = datetime.fromisoformat(data["started_at"])
            saved = datetime.fromisoformat(data.get("saved_at") or data["started_at"])
            snoozed = datetime.fromisoformat(data["snoozed_until"]) if data.get("snoozed_until") else None
        except (KeyError, TypeError, ValueError) as e:
            log.warning("a saved ring could not be read (%s); dropped", e)
            self.ctx.db.try_set("alarms.ring", None)
            return
        grace = timedelta(minutes=self.ctx.config.alarm_defaults.missed_grace_minutes)
        elapsed = float(data.get("ring_elapsed") or 0.0)
        if snoozed is not None:
            alive = now - snoozed <= grace  # still snoozed, or the snooze ended while core was down
        else:
            # it would still be ringing: the time core was down counts, so a crash loop cannot ring past its max ring
            elapsed += max(0.0, (now - saved).total_seconds())
            alive = elapsed < req.max_ring_minutes * 60
        try:
            stopped = (self.ctx.runtime_dir / "ring-stopped").read_text().strip() == data["started_at"]
        except OSError:
            stopped = False
        if not alive or stopped or now - started > RESUME_MAX_AGE or started > now + timedelta(minutes=10):
            self.ctx.db.try_set("alarms.ring", None)
            return
        req.start_tier = str(data.get("tier") or "source")
        req.start_reason = data.get("fallback_reason")
        log.warning("carrying on with %s after a restart (%s)", req.label, "snoozed" if snoozed and snoozed > now else "ringing")
        self.ctx.db.log_event("ring_restored", ring_kind=req.kind, label=req.label, alarm_id=req.alarm_id, snoozed_until=data.get("snoozed_until"))
        session = RingSession(self.ctx, req, self._ring_done, buzzer=self.buzzer, persist=self._persist_ring)
        session.started_at = started
        session.snooze_count = int(data.get("snooze_count") or 0)
        session.prev_volume = int(data.get("prev_volume", session.prev_volume))
        session.prev_muted = bool(data.get("prev_muted", False))
        session.ring_elapsed = elapsed if snoozed is None else float(data.get("ring_elapsed") or 0.0)
        self.ring = session
        self._light_wake_dismissed = req.occurrence
        await session.start(snoozed_until=snoozed)

    async def _ring_done(self, session: RingSession, reason: str) -> None:
        if self.ring is session:
            self.ring = None
        self.publish_safe(force=True)

    async def snooze(self) -> bool:
        return await self.ring.snooze() if self.ring else False

    async def stop_ringing(self) -> bool:
        if not self.ring:
            return False
        await self.ring.stop("user")
        return True

    async def ring_nap(self) -> None:
        t = self.ctx.config.timers
        d = self.ctx.config.alarm_defaults
        await self.start_ring(RingRequest(kind="nap", label="Nap", source=f"chime:{t.nap_chime}", volume=t.nap_volume, ramp_seconds=0, chime=t.nap_chime,
                                          snooze_minutes=d.snooze_minutes, buzzer_after_s=d.buzzer_after_s))

    async def test_ring(self, row: AlarmRow | None = None, *, source: str | None = None, label: str = "Test", volume: int = 60, ramp_seconds: int = 0) -> None:
        d = self.ctx.config.alarm_defaults
        if row is not None:
            req = RingRequest(kind="test", label=f"{row.label} (test)", source=row.source, volume=row.volume, ramp_seconds=row.ramp_seconds,
                              ramp_start_percent=d.ramp_start_percent, snooze_minutes=row.snooze_minutes, fallback_after_s=row.fallback_after_s,
                              buzzer_after_s=d.buzzer_after_s, max_ring_minutes=min(row.max_ring_minutes, 5), chime=d.chime, alarm_id=row.id)
        else:
            req = RingRequest(kind="test", label=label, source=source or f"chime:{d.chime}", volume=volume, ramp_seconds=ramp_seconds,
                              buzzer_after_s=d.buzzer_after_s, max_ring_minutes=5, chime=d.chime)
        await self.start_ring(req)

    async def dismiss_light_wake(self) -> None:
        if self.ctx.store.state.alarms.light_wake_active:
            self._light_wake_dismissed = self._light_wake_occurrence
            self.ctx.store.state.alarms.light_wake_active = False
            self.ctx.store.touch()

    async def set_leave(self, until: str | None) -> None:
        if until:
            date.fromisoformat(until)
        self.ctx.db.set("alarms.leave_until", until)
        self.ctx.store.state.alarms.on_leave_until = until
        self.ctx.db.log_event("leave", until=until)
        self.publish_safe(force=True)

    # ---- state -----------------------------------------------------------
    def publish_safe(self, force: bool = False) -> None:
        try:
            self.publish(force)
        except Exception:  # noqa: BLE001
            log.exception("publishing the alarms failed")

    def publish(self, force: bool = False) -> None:
        now = self.ctx.store.now()
        self.leave_until()
        items = []
        nxt: tuple[datetime, AlarmRow] | None = None
        for row in self.rows_safe():
            try:
                n = self.next_for(row, now)
                items.append(
                    AlarmSummary(
                        id=row.id or 0, label=row.label, enabled=row.enabled, time=row.time, repeat=row.repeat,
                        days=parse_days(row.days), source=row.source, volume=row.volume,
                        skip_next=row.skip_next, skip_public_holidays=row.skip_public_holidays,
                        next_at=n.isoformat(timespec="seconds") if n else None, light_wake=row.light_wake,
                    )
                )
            except Exception as e:  # noqa: BLE001
                self._row_failed(row, e)
                continue
            if n and (nxt is None or n < nxt[0]):
                nxt = (n, row)
        summary = ([(i.id, i.enabled, i.next_at, i.skip_next, i.label, i.time) for i in items], nxt[0].isoformat() if nxt else None, (nxt[0] - now).total_seconds() // 60 if nxt else None)
        if not force and summary == self._last_summary:
            return
        self._last_summary = summary
        st = self.ctx.store.state.alarms
        st.items = items
        st.next = NextAlarm(
            id=nxt[1].id or 0, label=nxt[1].label, at=nxt[0].isoformat(timespec="seconds"), in_seconds=int((nxt[0] - now).total_seconds()),
            light_wake_at=(nxt[0] - timedelta(minutes=nxt[1].light_wake_minutes)).isoformat(timespec="seconds") if nxt[1].light_wake else None,
        ) if nxt else None
        st.on_leave_until = self._leave.isoformat() if self._leave else None
        # nothing could be read from the database: the alarms are unknown here, and the face rings from what it
        # last heard (alarms/degraded on the face)
        st.degraded = self.ctx.db.degraded or (self._rows_error is not None and not self._rows_cache)
        st.degraded_reason = self.ctx.db.open_error if self.ctx.db.degraded else self._rows_error
        self.ctx.store.touch()
