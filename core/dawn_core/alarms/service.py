"""AlarmService: alarm storage, the 1 Hz engine tick, firing, light-wake, ringing."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import select

from ..config import DawnConfig
from ..context import DawnContext
from ..db.models import AlarmRow
from ..services import Service
from ..state.ui import AlarmSummary, NextAlarm
from .holidays import HolidayCalendar
from .ringing import RingRequest, RingSession
from .scheduler import AlarmSpec, due_occurrence, next_occurrence
from .schemas import AlarmIn, AlarmOut

log = logging.getLogger("dawn.alarms")


class AlarmService(Service):
    name = "alarms"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.ring: RingSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._calendars: dict[tuple[str, str], HolidayCalendar] = {}
        self._light_wake_alarm: int | None = None
        self._light_wake_occurrence: str | None = None  # occurrence ISO currently being lit
        self._light_wake_dismissed: str | None = None  # occurrence ISO
        self._last_summary: Any = None

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.ctx.store.state.alarms.on_leave_until = self.ctx.db.get("alarms.leave_until")
        self.publish(force=True)
        self._task = asyncio.create_task(self._loop(), name="alarm-engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.ring:
            await self.ring.stop("shutdown")

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.holidays != new.holidays or old.general.timezone != new.general.timezone:
            self._calendars.clear()
        self.publish(force=True)

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

    def get(self, alarm_id: int) -> AlarmRow | None:
        with self.ctx.db.session() as s:
            return s.get(AlarmRow, alarm_id)

    def _armed(self, now: datetime | None) -> str:
        """An alarm only answers for occurrences after it was created, edited or switched on: one set at 22:00
        for 06:30 must not count this morning's 06:30 as missed (and a once-alarm must not disable itself)."""
        return (now or self.ctx.store.now()).isoformat(timespec="seconds")

    def create(self, data: AlarmIn, *, now: datetime | None = None) -> AlarmRow:
        row = AlarmRow(**data.model_dump(exclude={"days"}), days=",".join(map(str, data.days)), last_fired_occurrence=self._armed(now))
        with self.ctx.db.session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        self.ctx.db.log_event("alarm_create", id=row.id, label=row.label, time=row.time)
        self.publish(force=True)
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
        self.publish(force=True)
        return row

    def set_enabled(self, alarm_id: int, enabled: bool, *, now: datetime | None = None) -> AlarmRow | None:
        row = self.get(alarm_id)
        if row is None:
            return None
        if enabled and not row.enabled:
            return self.patch(alarm_id, enabled=True, last_fired_occurrence=self._armed(now))
        return self.patch(alarm_id, enabled=enabled)

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
        self.publish(force=True)
        return row

    def delete(self, alarm_id: int) -> bool:
        with self.ctx.db.session() as s:
            row = s.get(AlarmRow, alarm_id)
            if not row:
                return False
            s.delete(row)
            s.commit()
        self.publish(force=True)
        return True

    # ---- scheduling helpers ---------------------------------------------
    @staticmethod
    def spec_for(row: AlarmRow) -> AlarmSpec:
        days = [int(x) for x in row.days.split(",") if x.strip() != ""]
        return AlarmSpec(
            time=row.time, repeat=row.repeat, days=days, enabled=row.enabled, skip_public_holidays=row.skip_public_holidays,
            skip_next=row.skip_next, last_fired_occurrence=row.last_fired_occurrence,
        )

    def leave_until(self) -> date | None:
        v = self.ctx.db.get("alarms.leave_until")
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            return None

    def _holiday_fn(self, row: AlarmRow):
        if not row.skip_public_holidays:
            return None
        return self.calendar(row.holiday_region, row.holiday_scope).is_holiday

    def next_for(self, row: AlarmRow, after: datetime | None = None) -> datetime | None:
        after = after or self.ctx.store.now()
        return next_occurrence(self.spec_for(row), after, self.ctx.store.tz, self._holiday_fn(row), self.leave_until())

    def to_out(self, row: AlarmRow) -> AlarmOut:
        nxt = self.next_for(row)
        return AlarmOut(
            id=row.id or 0, label=row.label, enabled=row.enabled, time=row.time, repeat=row.repeat,  # type: ignore[arg-type]
            days=[int(x) for x in row.days.split(",") if x.strip() != ""], skip_public_holidays=row.skip_public_holidays,
            holiday_region=row.holiday_region, holiday_scope=row.holiday_scope, source=row.source, volume=row.volume,  # type: ignore[arg-type]
            ramp_seconds=row.ramp_seconds, snooze_minutes=row.snooze_minutes, fallback_after_s=row.fallback_after_s,
            max_ring_minutes=row.max_ring_minutes, skip_next=row.skip_next, light_wake=row.light_wake,
            light_wake_minutes=row.light_wake_minutes, next_at=nxt.isoformat(timespec="seconds") if nxt else None,
            last_fired_occurrence=row.last_fired_occurrence,
        )

    # ---- engine ----------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("alarm tick failed")
            await asyncio.sleep(1.0)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or self.ctx.store.now()
        tz = self.ctx.store.tz
        grace = timedelta(minutes=self.ctx.config.alarm_defaults.missed_grace_minutes)
        leave = self.leave_until()
        light_candidate: tuple[int, datetime] | None = None
        for row in self.rows():
            if not row.enabled:
                continue
            if row.last_fired_occurrence and datetime.fromisoformat(row.last_fired_occurrence) > now + timedelta(minutes=10):
                # stamped while the clock was fast, and it has been stepped back since: only up to now is handled
                row = self.patch(row.id or 0, last_fired_occurrence=now.isoformat(timespec="seconds")) or row
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
                    self.patch(row.id or 0, skip_next=False, last_fired_occurrence=skipped.isoformat())
                    continue
            due = due_occurrence(spec, now, tz, hol, leave)
            if due is not None:
                age = now - due
                if age <= grace:
                    await self.fire(row, due, late_s=int(age.total_seconds()))
                else:
                    log.warning("alarm %s missed (%s, %d min late)", row.label, due, age.total_seconds() // 60)
                    self.ctx.db.log_event("alarm_missed", id=row.id, label=row.label, occurrence=due.isoformat(), late_minutes=int(age.total_seconds() // 60))
                    self.patch(row.id or 0, last_fired_occurrence=due.isoformat(), enabled=(row.repeat != "once"))
                continue
            if row.light_wake and self.ring is None:
                nxt = next_occurrence(spec, now, tz, hol, leave)
                if nxt and nxt - now <= timedelta(minutes=row.light_wake_minutes):
                    if self._light_wake_dismissed != nxt.isoformat():
                        light_candidate = (row.id or 0, nxt)
        active = light_candidate is not None
        self._light_wake_alarm = light_candidate[0] if light_candidate else None
        self._light_wake_occurrence = light_candidate[1].isoformat() if light_candidate else None
        if active != self.ctx.store.state.alarms.light_wake_active:
            self.ctx.store.state.alarms.light_wake_active = active
            self.ctx.db.log_event("light_wake", active=active, alarm_id=self._light_wake_alarm)
            self.ctx.store.touch()
        self.publish()

    async def fire(self, row: AlarmRow, occurrence: datetime, late_s: int = 0) -> None:
        self.patch(row.id or 0, last_fired_occurrence=occurrence.isoformat(), enabled=(row.repeat != "once"))
        self.ctx.db.log_event("alarm_fire", id=row.id, label=row.label, occurrence=occurrence.isoformat(), late_s=late_s, source=row.source)
        self._light_wake_dismissed = occurrence.isoformat()
        d = self.ctx.config.alarm_defaults
        req = RingRequest(
            kind="alarm", label=row.label, source=row.source, volume=row.volume, ramp_seconds=row.ramp_seconds,
            ramp_start_percent=d.ramp_start_percent, snooze_minutes=row.snooze_minutes, fallback_after_s=row.fallback_after_s,
            max_ring_minutes=row.max_ring_minutes, chime=d.chime, alarm_id=row.id, occurrence=occurrence.isoformat(),
        )
        await self.start_ring(req)

    # ---- ringing ---------------------------------------------------------
    async def start_ring(self, req: RingRequest) -> RingSession:
        if self.ring is not None:
            log.info("replacing active ring %s with %s", self.ring.req.label, req.label)
            await self.ring.stop("replaced")
        self.ring = RingSession(self.ctx, req, self._ring_done)
        await self.ring.start()
        return self.ring

    async def _ring_done(self, session: RingSession, reason: str) -> None:
        if self.ring is session:
            self.ring = None
        self.publish(force=True)

    async def snooze(self) -> bool:
        return await self.ring.snooze() if self.ring else False

    async def stop_ringing(self) -> bool:
        if not self.ring:
            return False
        await self.ring.stop("user")
        return True

    async def ring_nap(self) -> None:
        t = self.ctx.config.timers
        await self.start_ring(RingRequest(kind="nap", label="Nap", source=f"chime:{t.nap_chime}", volume=t.nap_volume, ramp_seconds=0, chime=t.nap_chime, snooze_minutes=self.ctx.config.alarm_defaults.snooze_minutes))

    async def test_ring(self, row: AlarmRow | None = None, *, source: str | None = None, label: str = "Test", volume: int = 60, ramp_seconds: int = 0) -> None:
        d = self.ctx.config.alarm_defaults
        if row is not None:
            req = RingRequest(kind="test", label=f"{row.label} (test)", source=row.source, volume=row.volume, ramp_seconds=row.ramp_seconds, ramp_start_percent=d.ramp_start_percent, snooze_minutes=row.snooze_minutes, fallback_after_s=row.fallback_after_s, max_ring_minutes=min(row.max_ring_minutes, 5), chime=d.chime, alarm_id=row.id)
        else:
            req = RingRequest(kind="test", label=label, source=source or f"chime:{d.chime}", volume=volume, ramp_seconds=ramp_seconds, max_ring_minutes=5, chime=d.chime)
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
        self.publish(force=True)

    # ---- state -----------------------------------------------------------
    def publish(self, force: bool = False) -> None:
        now = self.ctx.store.now()
        items = []
        nxt: tuple[datetime, AlarmRow] | None = None
        for row in self.rows():
            n = self.next_for(row, now)
            items.append(
                AlarmSummary(
                    id=row.id or 0, label=row.label, enabled=row.enabled, time=row.time, repeat=row.repeat,
                    days=[int(x) for x in row.days.split(",") if x.strip() != ""], source=row.source, volume=row.volume,
                    skip_next=row.skip_next, skip_public_holidays=row.skip_public_holidays,
                    next_at=n.isoformat(timespec="seconds") if n else None, light_wake=row.light_wake,
                )
            )
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
        st.on_leave_until = self.ctx.db.get("alarms.leave_until")
        self.ctx.store.touch()
