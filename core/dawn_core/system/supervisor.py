"""SupervisorService: keeps the programs Dawn depends on running.

systemd restarts a unit that exits. This covers what systemd cannot see, and what it gives up on:
- the face kiosk whose Chromium is alive but blank, stuck on an error page or frozen: no face on the WebSocket,
  or a face whose page has stopped talking (it pings every 15 s);
- a welle-cli (the DAB decoder) that runs but answers nothing;
- the dawn user's PipeWire session, when pipewire, wireplumber or pipewire-pulse has failed (nothing but the
  backup tone can be heard then);
- a dawn-timed that has stopped writing its status file;
- a unit left `failed` (systemd's start limit; a crash it will not retry), or one of Dawn's own units stopped
  and not coming back;
- a service inside dawn-core whose start() failed because something was not ready at boot.

Every repair waits until the problem has been seen for a while, is rate-limited with a backoff that doubles while
the same thing keeps breaking (and starts over once it has been fine for ten minutes), is logged as a
`supervisor_repair` event and is listed under Diagnostics -> System. Alarms are never touched: a ring restarts
what it needs itself, and the DAB decoder is left alone while an alarm rings or is snoozed. The privileged
restarts are exactly the lines in deploy/sudoers/dawn; the PipeWire ones need no privilege (same user).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..context import DawnContext
from ..diagnostics.host import Host, make_host
from ..services import Service

log = logging.getLogger("dawn.supervisor")

OWN_UNITS = ("dawn-dab", "dawn-timed", "dawn-face")  # Restart=always: `inactive` is wrong too, not only `failed`
SYSTEM_UNITS = ("shairport-sync", "nqptp", "gpsd", "chrony", "bluetooth", "avahi-daemon", "NetworkManager")
USER_UNITS = ("pipewire", "wireplumber", "pipewire-pulse")  # the dawn user's session: no sudo needed
INACTIVE_FOR_S = 30.0  # one of Dawn's own units seen stopped this long is started again (an update restarts them briefly)
USER_FAILED_FOR_S = 10.0
FACE_STALE_S = 60.0  # a connected face whose page has not spoken for this long counts as gone
HEAL_S = 600.0  # fine for this long: the backoff starts over
REPAIRS_KEPT = 50

Fix = Callable[[], Awaitable[tuple[bool, str]]]


@dataclass
class Repair:
    at: str
    what: str
    reason: str
    ok: bool
    message: str
    next_try_s: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SupervisorService(Service):
    name = "supervisor"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.host: Host = make_host(ctx)
        self._task: asyncio.Task[None] | None = None
        self.unit_states: dict[str, str] = {}
        self.user_states: dict[str, str] | None = None
        self.repairs: deque[Repair] = deque(maxlen=REPAIRS_KEPT)
        self.checked_at: float | None = None  # time.time()
        self._bad_since: dict[str, float] = {}  # key -> monotonic time the problem was first seen
        self._good_since: dict[str, float] = {}
        self._backoff: dict[str, float] = {}  # key -> the wait after its last repair
        self._not_before: dict[str, float] = {}  # key -> monotonic time before which it is not repaired again
        self._user_unsupported_logged = False
        self._started = time.monotonic()

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self._started = time.monotonic()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="supervisor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.host.close()

    @property
    def cfg(self):  # noqa: ANN201
        return self.ctx.config.system.supervisor

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.check_interval_s)
            try:
                await self.check()
            except Exception:  # noqa: BLE001
                log.exception("supervisor check failed")

    # ---- one pass --------------------------------------------------------
    async def check(self, now: float | None = None) -> None:
        """One pass over everything. Each part stands alone: a check that raises is logged and the rest run.
        `now` is a monotonic time (tests drive the clock)."""
        now = time.monotonic() if now is None else now
        self.checked_at = time.time()
        for step in (self._core_services, self._units, self._face, self._dab, self._user_audio, self._timed):
            try:
                await step(now)
            except Exception:  # noqa: BLE001
                log.exception("supervisor: %s failed", step.__name__.strip("_"))
        self._publish()

    async def _core_services(self, now: float) -> None:
        for name in await self.ctx.registry.retry_failed():
            self._note(Repair(self.ctx.store.iso(), f"dawn-core: {name}", "could not start earlier", True, "started", 0.0))

    async def _units(self, now: float) -> None:
        self.unit_states = await self.host.units(list(OWN_UNITS + SYSTEM_UNITS))
        if not self.cfg.enabled:
            return
        for unit, state in self.unit_states.items():
            own = unit in OWN_UNITS
            if state == "unknown" or not await self.host.unit_enabled(unit):
                continue  # systemd cannot say, or the unit is not meant to run here (AirPlay not built, a kiosk disabled on a headless box)
            bad_for = self._bad_for(f"unit:{unit}", state == "failed" or (own and state == "inactive"), now)
            if state == "failed" or bad_for >= INACTIVE_FOR_S:
                await self._repair(f"unit:{unit}", unit, f"{state}" + (f" for {bad_for:.0f} s" if state != "failed" else ""), lambda u=unit: self._restart_unit(u), now)

    async def _face(self, now: float) -> None:
        """No face on the socket (or one whose page has frozen) while dawn-face is active: Chromium is blank, on an
        error page or hung, which systemd cannot see."""
        if self.ctx.sim or not self.cfg.enabled or self.ctx.ws_hub is None:
            return
        seen = self.ctx.ws_hub.face_seen(FACE_STALE_S)
        absent_for = self._bad_for("face", not seen, now)
        if seen or absent_for < self.cfg.face_absent_s:
            return
        if self.unit_states.get("dawn-face") != "active" or not await self.host.unit_enabled("dawn-face"):
            return  # stopped, starting or restarting: systemd (or the unit check above) is on it
        active_for = await self.host.unit_active_for("dawn-face")
        if active_for is not None and active_for < self.cfg.face_absent_s:
            return  # Chromium is still coming up (slow on a Pi 3 or Zero 2)
        await self._repair("face", "dawn-face", f"no face connected for {absent_for:.0f} s", lambda: self._restart_unit("dawn-face"), now)

    async def _dab(self, now: float) -> None:
        """welle-cli running but not answering: a decoder that will not stream to anyone. While an alarm rings or is
        snoozed the ring's own watch does this, with its own timing."""
        if not self.cfg.enabled or not self.ctx.config.dab.enabled:
            return
        try:
            from ..dab.service import DabService

            dab = self.ctx.svc(DabService)
        except (ImportError, KeyError):
            return
        st = self.ctx.store.state
        if not st.dab.sdr_present or st.alarms.ringing is not None or self.unit_states.get("dawn-dab") not in ("active", "unknown"):
            self._bad_for("dab", False, now)
            return
        dead_for = self._bad_for("dab", not await dab.client.reachable(), now)
        if dead_for >= self.cfg.welle_dead_s:

            async def fix() -> tuple[bool, str]:
                ok = await dab.restart_welle(reason="supervisor")
                return ok, "restarting welle-cli" if ok else "restarted moments ago already"

            await self._repair("dab", "DAB decoder (welle-cli)", f"not answering for {dead_for:.0f} s", fix, now)

    async def _user_audio(self, now: float) -> None:
        """The dawn user's PipeWire session. pipewire and pipewire-pulse are socket-activated (inactive is fine);
        wireplumber is a plain service and must be running, or no sink is ever linked."""
        if self.ctx.sim:
            return
        self.user_states = await self.host.user_units(list(USER_UNITS))
        if self.user_states is None:
            if not self._user_unsupported_logged:
                self._user_unsupported_logged = True
                log.info("systemctl --user is not available: the PipeWire session is not supervised")
            return
        if not self.cfg.enabled:
            return
        fixed = False
        for unit, state in list(self.user_states.items()):
            bad = state == "failed" or (unit == "wireplumber" and state == "inactive")
            if self._bad_for(f"user:{unit}", bad, now) >= USER_FAILED_FOR_S:
                if await self._repair(f"user:{unit}", f"{unit} (PipeWire session)", state, lambda u=unit: self._restart_user_unit(u), now):
                    fixed = True
                    self.user_states[unit] = "activating"
        if fixed:
            await self._reselect_audio()

    async def _timed(self, now: float) -> None:
        """dawn-timed writes its status every couple of seconds; a stale file is a hung daemon."""
        if not self.cfg.enabled or self.unit_states.get("dawn-timed") != "active":
            self._bad_for("timed", False, now)
            return
        age = self._status_age(self.ctx.config.diagnostics.timed_status_file)
        stale = age is not None and age > self.cfg.timed_stale_s
        self._bad_for("timed", stale, now)
        if stale:
            await self._repair("timed", "dawn-timed", f"status not written for {age:.0f} s", lambda: self._restart_unit("dawn-timed"), now)

    # ---- repairs ---------------------------------------------------------
    def _bad_for(self, key: str, bad: bool, now: float) -> float:
        """How long `key` has been seen bad (0 when it is fine). Fine for HEAL_S forgets its backoff."""
        if not bad:
            self._bad_since.pop(key, None)
            since = self._good_since.setdefault(key, now)
            if key in self._backoff and now - since >= HEAL_S:
                del self._backoff[key]
            return 0.0
        self._good_since.pop(key, None)
        return now - self._bad_since.setdefault(key, now)

    def due(self, key: str, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self._not_before.get(key, 0.0)

    async def _repair(self, key: str, what: str, reason: str, fix: Fix, now: float) -> bool:
        if not self.due(key, now):
            return False
        b = self._backoff.get(key, 0.0)
        b = float(self.cfg.min_backoff_s) if b <= 0 else min(float(self.cfg.max_backoff_s), b * 2)
        self._backoff[key] = b
        self._not_before[key] = now + b
        self._bad_since.pop(key, None)  # judged afresh once it has had time to come back
        try:
            ok, message = await fix()
        except Exception as e:  # noqa: BLE001
            ok, message = False, f"{type(e).__name__}: {e}"
        self._note(Repair(self.ctx.store.iso(), what, reason, ok, message, b))
        return ok

    def _note(self, r: Repair) -> None:
        self.repairs.append(r)
        (log.warning if r.ok else log.error)("supervisor: %s: %s; %s%s", r.what, r.reason, r.message,
                                              f" (next at the earliest in {r.next_try_s:.0f} s)" if r.next_try_s else "")
        self.ctx.db.log_event("supervisor_repair", what=r.what, reason=r.reason, ok=r.ok, message=r.message)

    async def _restart_unit(self, unit: str) -> tuple[bool, str]:
        rc, out = await self.host.sudo("systemctl", "restart", unit, timeout=60)
        return (True, f"restarted {unit}") if rc == 0 else (False, f"could not restart {unit}: {out.strip() or f'exit {rc}'}")

    async def _restart_user_unit(self, unit: str) -> tuple[bool, str]:
        rc, out = await self.host.user_restart(unit)
        return (True, f"restarted {unit}") if rc == 0 else (False, f"could not restart {unit}: {out.strip() or f'exit {rc}'}")

    async def _reselect_audio(self) -> None:
        """After a PipeWire restart the sink, its volume and mute, and the EQ chain are set again."""
        try:
            from ..audio.service import AudioService

            audio = self.ctx.svc(AudioService)
            await audio.refresh_sinks(select_now=True)
            await audio.apply_eq()
        except Exception:  # noqa: BLE001
            log.exception("audio could not be set up again after the PipeWire restart")

    @staticmethod
    def _status_age(path: str) -> float | None:
        try:
            d = json.loads(Path(path).read_text())
            return time.time() - datetime.fromisoformat(d["updated_at"]).timestamp()
        except (OSError, ValueError, KeyError, TypeError):
            return None  # no file (an older dawn-timed), or not readable: nothing to judge by

    # ---- state -----------------------------------------------------------
    def recent(self, hours: float = 24.0) -> list[dict[str, Any]]:
        since = (self.ctx.store.now().timestamp() - hours * 3600)
        out = []
        for r in self.repairs:
            try:
                if datetime.fromisoformat(r.at).timestamp() >= since:
                    out.append(r.as_dict())
            except ValueError:
                continue
        return out

    def _publish(self) -> None:
        st = self.ctx.store.state.system
        services = dict(self.unit_states)
        for unit, state in (self.user_states or {}).items():
            services[unit] = state
        if services != st.services:
            st.services = services
            self.ctx.store.touch()
