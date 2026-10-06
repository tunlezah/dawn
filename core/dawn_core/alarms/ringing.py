"""A ring session: one alarm or nap ringing, with ramp, snooze, max ring, and a ladder of sounds.

The ladder is the alarm's own source, then the chime, then the backup tone (alarms/buzzer.py: beeps played
without mpv, plus an optional GPIO buzzer). Every rung is watched: when nothing has been heard within its time
(`fallback_after_s` from the ring for the source, `buzzer_after_s` for the chime), or what was heard stops for a
while, the next rung takes over. The backup tone is the last; on it the face beeps as well, since core's own
sound may not be getting out at all.

The sound starts in the background, so the ring shows at once and the alarm engine never waits for a DAB
station to tune. Snooze and stop can therefore arrive while a rung is still starting: every start carries an
epoch, and a start that was overtaken stands down instead of carrying on, so nothing rings on through a snooze
or after a stop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from typing import Any

from ..audio.arbiter import Slot
from ..audio.service import AudioService
from ..context import DawnContext
from ..state.ui import RingingInfo

log = logging.getLogger("dawn.ring")

TIERS = ("source", "chime", "buzzer")
TIER_NAMES = {"source": "alarm source", "chime": "chime", "buzzer": "backup tone"}
MIN_HEARD_S = {"source": 5.0, "chime": 3.0}  # a rung that was slow to start still gets this long to be heard
DROPOUT_S = {"source": 10.0, "chime": 5.0}  # audio that stops for this long has failed (DAB drops out briefly)
START_EXTRA_S = 10.0  # a rung still starting this long after its deadline is abandoned
DAB_RESTART_AFTER_S = 3.0
BUZZER_RETRY_S = 5.0


@dataclass
class RingRequest:
    kind: str  # alarm | nap | test
    label: str
    source: str
    volume: int
    ramp_seconds: int = 60
    ramp_start_percent: int = 10
    snooze_minutes: int = 9
    fallback_after_s: int = 15
    buzzer_after_s: int = 8
    max_ring_minutes: int = 30
    chime: str = "gentle_bell"
    alarm_id: int | None = None
    occurrence: str | None = None
    start_tier: str = "source"  # a rung below the source when the alarm's preparation found it will not play
    start_reason: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RingRequest:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


class RingSession:
    def __init__(
        self,
        ctx: DawnContext,
        req: RingRequest,
        on_done: Callable[[RingSession, str], Awaitable[None] | None],
        *,
        buzzer: Any = None,
        persist: Callable[[dict[str, Any] | None], None] | None = None,
    ):
        self.ctx = ctx
        self.req = req
        self.on_done = on_done
        self.buzzer = buzzer
        self.audio: AudioService = ctx.svc(AudioService)
        self.started_at = ctx.store.now()
        self.prev_volume = self.audio.volume
        self.prev_muted = self.audio.muted
        self.tier = req.start_tier if req.start_tier in TIERS else "source"
        self.fallback_reason = req.start_reason
        self.audible: bool | None = None
        self.restarted_dab = False
        self.snooze_count = 0
        self.snoozed_until: datetime | None = None
        self.ring_elapsed = 0.0
        self.active = False
        self.epoch = 0
        self._persist_cb = persist
        self._ramp_origin = time.monotonic()
        self._started = asyncio.Event()
        self._start_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._ramp_task: asyncio.Task[None] | None = None
        self._snooze_task: asyncio.Task[None] | None = None
        self._max_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def fallback(self) -> bool:
        return self.tier != "source"

    @property
    def snoozed(self) -> bool:
        return self.snoozed_until is not None

    def _stale(self, epoch: int) -> bool:
        return self._stopping or epoch != self.epoch

    def _ref(self, tier: str) -> str:
        return {"source": self.req.source, "chime": f"chime:{self.req.chime}", "buzzer": "buzzer:"}[tier]

    def _resolved(self, tier: str) -> str:
        """The reference to play, with `last-played` looked up (a database that cannot answer leaves it as is)."""
        ref = self._ref(tier)
        try:
            return self.audio.resolve(ref)
        except Exception:  # noqa: BLE001
            return ref

    # ---- lifecycle -------------------------------------------------------
    async def start(self, *, snoozed_until: datetime | None = None) -> None:
        """Show the ring now and start its sound in the background. `snoozed_until` resumes a snoozed ring
        (after a restart of dawn-core): it stays quiet until then."""
        self.active = True
        self.ctx.db.log_event("ring_start", ring_kind=self.req.kind, label=self.req.label, source=self.req.source, alarm_id=self.req.alarm_id, tier=self.tier)
        self._max_task = asyncio.create_task(self._max_ring(), name="ring-max")
        if self.tier != "source":
            log.warning("%s starts on the %s: %s", self.req.label, TIER_NAMES[self.tier], self.fallback_reason)
            self.ctx.db.log_event("ring_fallback", ring_kind=self.req.kind, label=self.req.label, reason=self.fallback_reason, source=self.req.source, tier=self.tier)
        now = self.ctx.store.now()
        if snoozed_until is not None and snoozed_until > now:
            self.snoozed_until = snoozed_until
            self._snooze_task = asyncio.create_task(self._wake_from_snooze((snoozed_until - now).total_seconds()), name="ring-snooze")
            self._started.set()
            self._publish()
            self._persist()
            return
        self._ramp_origin = time.monotonic()
        self._go(self.tier)

    async def settled(self, timeout: float = 10.0) -> None:
        """Wait until the current rung has started (or failed to); for tests and callers that want the sound going."""
        deadline = time.monotonic() + timeout
        while True:
            evt = self._started
            await asyncio.wait_for(evt.wait(), max(0.01, deadline - time.monotonic()))
            if evt is self._started:
                return

    def _go(self, tier: str) -> None:
        """Start `tier` in the background; whatever was starting or watching before stands down."""
        self.epoch += 1
        self.tier = tier
        self.audible = None
        self._cancel_watch()
        self._started = asyncio.Event()
        self._start_task = asyncio.create_task(self._run(tier, self.epoch), name=f"ring-{tier}")
        self._publish()
        self._persist()

    async def _run(self, tier: str, epoch: int) -> None:
        t0 = time.monotonic()
        started = self._started
        reason: str | None
        try:
            reason = await asyncio.wait_for(self._start_tier(tier, epoch), self._wait(tier) + START_EXTRA_S)
        except TimeoutError:
            reason = "did not start in time"
        except Exception as e:  # noqa: BLE001
            expected = isinstance(e, (RuntimeError, ValueError, OSError))  # mpv missing, a deleted playlist...
            log.warning("%s: the %s could not start: %s", self.req.label, TIER_NAMES[tier], e, exc_info=not expected)
            reason = str(e) or type(e).__name__
        finally:
            started.set()
        if self._stale(epoch):
            return
        if reason is not None:
            self._climb(tier, reason, epoch)
            return
        self._ramp_task = asyncio.create_task(self._ramp(epoch), name="ring-ramp")
        self._watch_task = asyncio.create_task(self._watch(tier, epoch, t0), name="ring-watchdog")

    def _wait(self, tier: str) -> float:
        """How long a rung has to be heard, counted from when it began."""
        if tier == "source" and not self.req.source.startswith("chime:"):
            return float(self.req.fallback_after_s)
        return float(self.req.buzzer_after_s)

    async def _start_tier(self, tier: str, epoch: int) -> str | None:
        """Start `tier` at the alarm level. None when it is playing (or the start was overtaken), else why not."""
        await self._prepare_output(tier, epoch)
        if self._stale(epoch):
            return None
        ref = self._resolved(tier)
        if ref.startswith("dab:") and not self.ctx.store.state.dab.sdr_present:
            return "no SDR"
        try:
            slot = await self.audio.play(ref, level="alarm", remember=False)
        except Exception as e:
            if tier != "buzzer" or self.buzzer is None:
                raise
            log.error("%s: the backup tone could not go through the audio arbiter (%s); starting it directly", self.req.label, e)
            await self.buzzer.start()
            return None
        if self._stale(epoch):
            await self._stand_down(slot)
            return None
        if slot.state == "error":
            return slot.source.error or "source failed"
        return None

    async def _stand_down(self, slot: Slot) -> None:
        """A start that a snooze or a stop overtook: do not leave its sound playing."""
        if self.audio.arbiter.slot("alarm") is not slot:
            return  # a newer start owns the alarm level now
        if self._stopping:
            await self.audio.stop_level("alarm")
        elif self.snoozed and slot.state in ("playing", "starting", "ducked"):
            await self.audio.arbiter.pause_level("alarm")

    async def _prepare_output(self, tier: str, epoch: int) -> None:
        """Unmute and set the starting volume. Best effort: a backend that cannot do it must not stop the sound."""
        try:
            await self.audio.ensure_unmuted()
            if not self._stale(epoch):
                await self.audio.set_volume(self._ramp_level(tier), persist=False, overlay=False)
        except Exception:  # noqa: BLE001
            log.exception("could not set the alarm volume")

    def _climb(self, tier: str, reason: str, epoch: int) -> None:
        i = TIERS.index(tier)
        nxt = TIERS[min(i + 1, len(TIERS) - 1)]
        if nxt == "chime" and self._resolved("source").startswith("chime:"):
            nxt = "buzzer"  # the source was a chime already: the same player will not do better
        if nxt == tier:
            log.error("%s: the backup tone did not start (%s); trying again", self.req.label, reason)
            self._start_task = asyncio.create_task(self._retry(tier, epoch), name="ring-retry")
            return
        log.warning("%s: %s on the %s; switching to the %s", self.req.label, reason, TIER_NAMES[tier], TIER_NAMES[nxt])
        self.ctx.db.log_event("ring_fallback", ring_kind=self.req.kind, label=self.req.label, reason=reason, source=self.req.source, tier=nxt, from_tier=tier)
        self.fallback_reason = reason
        self._go(nxt)

    async def _retry(self, tier: str, epoch: int) -> None:
        await asyncio.sleep(BUZZER_RETRY_S)
        if not self._stale(epoch):
            self._go(tier)

    def _ramp_level(self, tier: str) -> int:
        """The ramp counts from the ring (or the end of a snooze), so it carries on across rungs; the backup tone
        goes straight to the alarm's volume."""
        target = self.req.volume
        if tier == "buzzer" or self.req.ramp_seconds <= 0:
            return target
        start = min(target, max(1, self.req.ramp_start_percent))
        frac = min(1.0, (time.monotonic() - self._ramp_origin) / self.req.ramp_seconds)
        return round(start + (target - start) * frac)

    async def _ramp(self, epoch: int) -> None:
        """Raise the volume to the alarm's over ramp_seconds. A change by hand while it ramps ends it: the knob wins."""
        last = self.audio.volume
        while not self._stale(epoch):
            if self.audio.volume != last:
                log.info("%s: volume changed by hand to %s; the ramp stops", self.req.label, self.audio.volume)
                return
            done = self.tier == "buzzer" or self.req.ramp_seconds <= 0 or time.monotonic() - self._ramp_origin >= self.req.ramp_seconds
            v = self._ramp_level(self.tier)
            if v != last:
                last = await self.audio.set_volume(v, persist=False, overlay=False)
            if done:
                return
            await asyncio.sleep(0.5)

    async def _watch(self, tier: str, epoch: int, t0: float) -> None:
        """Climb a rung when nothing has been heard by the deadline, or what was heard stops for a while."""
        deadline = max(t0 + self._wait(tier), time.monotonic() + MIN_HEARD_S.get(tier, 3.0))
        heard = False
        silent_since: float | None = None
        while True:
            await asyncio.sleep(1.0)
            if self._stale(epoch):
                return
            flowing = await self.audio.level_flowing("alarm")
            if self._stale(epoch):
                return
            now = time.monotonic()
            if flowing:
                heard, silent_since = True, None
                self._set_audible(True)
                continue
            if tier == "buzzer":
                self._set_audible(False)  # nothing above it: it keeps trying the next player, and the face beeps
                continue
            if tier == "source" and not self.restarted_dab and now - t0 >= DAB_RESTART_AFTER_S and self._resolved(tier).startswith("dab:"):
                await self._restart_dab_if_dead()
            if not heard:
                if now >= deadline:
                    self._set_audible(False)
                    self._climb(tier, f"no audio after {now - t0:.0f} s", epoch)
                    return
            else:
                silent_since = silent_since or now
                if now - silent_since >= DROPOUT_S[tier]:
                    self._set_audible(False)
                    self._climb(tier, f"audio stopped for {now - silent_since:.0f} s", epoch)
                    return

    async def _restart_dab_if_dead(self) -> None:
        try:
            from ..dab.service import DabService

            dab = self.ctx.svc(DabService)
            if not await dab.healthy():
                self.restarted_dab = True
                log.warning("DAB not healthy during ring; restarting welle-cli once")
                await dab.restart_welle(reason="alarm")
        except Exception:  # noqa: BLE001
            log.exception("dab restart attempt failed")

    def _set_audible(self, value: bool) -> None:
        if self.audible != value:
            self.audible = value
            self._publish()

    async def _max_ring(self) -> None:
        limit = self.req.max_ring_minutes * 60
        while self.ring_elapsed < limit:
            await asyncio.sleep(1.0)
            if self.snoozed_until is None:
                self.ring_elapsed += 1.0
        log.warning("%s rang for %d min without response; stopping", self.req.label, self.req.max_ring_minutes)
        await self.stop("max_ring")

    # ---- snooze ----------------------------------------------------------
    async def snooze(self) -> bool:
        if not self.active or self.snoozed_until is not None:
            return False
        self.snooze_count += 1
        self.snoozed_until = self.ctx.store.now() + timedelta(minutes=self.req.snooze_minutes)
        self.epoch += 1  # a rung still starting stands down; the watchdog and the ramp stop
        self._cancel_watch()
        self.audible = None
        self._snooze_task = asyncio.create_task(self._wake_from_snooze(self.req.snooze_minutes * 60), name="ring-snooze")
        self._publish()  # the face reacts now, even if the audio takes a moment to let go
        self._persist()
        self.ctx.db.log_event("ring_snooze", ring_kind=self.req.kind, label=self.req.label, count=self.snooze_count, until=self.snoozed_until.isoformat())
        try:
            await self.audio.arbiter.pause_level("alarm")
        except Exception:  # noqa: BLE001
            log.exception("pausing the alarm for the snooze failed; stopping its sound instead")
            with suppress(Exception):
                await self.audio.stop_level("alarm")
        if self.buzzer is not None:
            await self.buzzer.stop()
        return True

    async def _wake_from_snooze(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self._ring_again("snooze over")

    async def wake_now(self, why: str) -> bool:
        """End a snooze early (a nap timer running out while the alarm is snoozed)."""
        if not self.active or self.snoozed_until is None:
            return False
        if self._snooze_task and not self._snooze_task.done():
            self._snooze_task.cancel()
        self._ring_again(why)
        return True

    def _ring_again(self, why: str) -> None:
        if not self.active or self._stopping:
            return
        self.snoozed_until = None
        self._ramp_origin = time.monotonic()  # the ramp starts again
        self.ctx.db.log_event("ring_resume", ring_kind=self.req.kind, label=self.req.label, reason=why)
        self._go(self.tier)

    # ---- stop ------------------------------------------------------------
    async def stop(self, reason: str = "user", *, forget: bool = True) -> None:
        """End the ring. `forget=False` (dawn-core shutting down) keeps it saved, so the restart carries on with it."""
        if self._stopping:
            return
        self._stopping = True
        self.active = False
        self.epoch += 1
        self._cancel_watch()
        for t in (self._snooze_task, self._max_task):
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()
        # the face first: tearing the audio down can wait for a DAB tune that is still under way
        self.ctx.store.state.alarms.ringing = None
        self.ctx.store.touch()
        if forget and self._persist_cb and self.req.kind != "test":
            self._persist_cb(None)
        try:
            await self.audio.stop_level("alarm")
        except Exception:  # noqa: BLE001
            log.exception("stopping the alarm's sound failed")
        if self.buzzer is not None:
            with suppress(Exception):
                await self.buzzer.stop()
        try:
            await self.audio.set_volume(self.prev_volume, overlay=False)
            if self.prev_muted:
                await self.audio.set_mute(True, overlay=False)
        except Exception:  # noqa: BLE001
            log.exception("restoring the volume after the alarm failed")
        self.ctx.db.log_event("ring_stop", ring_kind=self.req.kind, label=self.req.label, reason=reason, snoozes=self.snooze_count, fallback=self.fallback, tier=self.tier)
        r = self.on_done(self, reason)
        if asyncio.iscoroutine(r):
            await r

    def _cancel_watch(self) -> None:
        """The watchdog and the ramp; a rung that is still starting is left to notice the epoch and stand down
        (cancelling it could orphan an mpv that is coming up)."""
        for t in (self._watch_task, self._ramp_task):
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()
        self._watch_task = self._ramp_task = None

    # ---- state -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """What a restart of dawn-core needs to carry on with this ring."""
        return {
            "req": asdict(self.req), "started_at": self.started_at.isoformat(), "snooze_count": self.snooze_count,
            "snoozed_until": self.snoozed_until.isoformat() if self.snoozed_until else None, "tier": self.tier,
            "fallback_reason": self.fallback_reason, "prev_volume": self.prev_volume, "prev_muted": self.prev_muted,
            "saved_at": self.ctx.store.iso(),
        }

    def _persist(self) -> None:
        if self._persist_cb and self.req.kind != "test" and not self._stopping:
            self._persist_cb(self.snapshot())

    def _publish(self) -> None:
        if self._stopping:
            return  # a stopped ring never comes back on the face
        ends = self.started_at + timedelta(minutes=self.req.max_ring_minutes)
        self.ctx.store.state.alarms.ringing = RingingInfo(
            kind="nap" if self.req.kind == "nap" else "alarm",
            alarm_id=self.req.alarm_id,
            label=self.req.label,
            started_at=self.started_at.isoformat(timespec="seconds"),
            snoozed_until=self.snoozed_until.isoformat(timespec="seconds") if self.snoozed_until else None,
            snooze_count=self.snooze_count,
            source=self._ref(self.tier),
            fallback=self.fallback,
            tier=self.tier,  # type: ignore[arg-type]
            fallback_reason=self.fallback_reason if self.fallback else None,
            audible=self.audible,
            face_beep=self.tier == "buzzer" and self.snoozed_until is None,
            volume_target=self.req.volume,
            ends_at=ends.isoformat(timespec="seconds"),
        )
        self.ctx.store.touch()
