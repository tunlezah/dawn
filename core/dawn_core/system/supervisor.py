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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..context import DawnContext
from ..diagnostics.host import Host, make_host
from ..services import Service
from ..tasks import spawn

log = logging.getLogger("dawn.supervisor")

OWN_UNITS = ("dawn-dab", "dawn-timed", "dawn-face")  # Restart=always: `inactive` is wrong too, not only `failed`
SYSTEM_UNITS = ("shairport-sync", "nqptp", "gpsd", "chrony", "bluetooth", "avahi-daemon", "NetworkManager")
USER_UNITS = ("pipewire", "wireplumber", "pipewire-pulse")  # the dawn user's session: no sudo needed
INACTIVE_FOR_S = 30.0  # one of Dawn's own units seen stopped this long is started again (an update restarts them briefly)
USER_FAILED_FOR_S = 10.0
PIPEWIRE_HUNG_FOR_S = 45.0  # wpctl timing out this long (three checks): pipewire runs but answers no client
WPCTL_TIMEOUT_S = 10.0
FACE_STALE_S = 60.0  # a connected face whose page has not spoken for this long counts as gone
HEAL_S = 600.0  # fine for this long: the backoff starts over
REPAIRS_KEPT = 50

Fix = Callable[[], Awaitable[tuple[bool, str]]]


NOSYNC_RESTARTS_BEFORE_RESET = 3  # decoder restarts for "no sync" before the stick is re-enumerated on USB
USB_RESET_GRACE_S = 180.0  # after re-enumerating: no sync by then and Diagnostics asks for a replug
FRAME_STALE_MS = 60_000  # the face's last animation frame older than this while it still pings: a frozen screen
CHRONYC_TIMEOUTS = 4  # chronyc polls in a row that timed out (30 s apart): chronyd is hung
GPSD_QUIET_S = 120.0
WIFI_DRIVER_S = 600.0
RECYCLE_WINDOW = timedelta(hours=2)  # face_recycle_at until then; not quiet in that time: skipped that day
RECYCLE_MIN_UP_S = 6 * 3600.0
RECYCLE_ALARM_GAP = timedelta(minutes=60)


def recycle_due(now: datetime, at: str, done_on: date | None) -> str | None:
    """"due" in the window after `at` (local time) on a day not yet done, "missed" once the window has passed without
    it, else None."""
    if done_on == now.date():
        return None
    h, m = (int(x) for x in at.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < start:
        return None
    return "due" if now < start + RECYCLE_WINDOW else "missed"


def quiet_for_recycle(st: Any, now: datetime, active_for_s: float | None) -> str | None:
    """Why the face may not be refreshed now (None: it may): it must be on Standby or the sleep clock, with nothing
    playing, ringing or counting down, no alarm (or its light wake) within the hour, and up for six hours."""
    if st.face.mode not in ("standby", "sleep"):
        return f"the face shows {st.face.mode}"
    if st.audio.active_source != "none":
        return "something is playing"
    if st.alarms.ringing is not None or st.alarms.light_wake_active or st.timers.nap is not None:
        return "an alarm, a nap or a light wake is on"
    nxt = st.alarms.next
    if nxt is not None:
        try:
            first = min(datetime.fromisoformat(t) for t in (nxt.at, nxt.light_wake_at) if t)
            if first - now < RECYCLE_ALARM_GAP:
                return "an alarm is due within the hour"
        except ValueError:
            return "the next alarm cannot be read"
    if active_for_s is None or active_for_s < RECYCLE_MIN_UP_S:
        return "the kiosk started less than six hours ago"
    return None


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
        self._timed_value: str | None = None  # dawn-timed's last updated_at, and when (monotonic) it last changed
        self._timed_changed = 0.0
        self._recycled_on: date | None = None  # the day the face had its daily refresh (or it was skipped)
        self.wifi_driver_tries = 0
        self.dab_nosync_restarts = 0  # decoder restarts for "no sync" in a row (heals after ten minutes of sync)
        self._usb_reset_at: float | None = None  # monotonic: the stick was re-enumerated then
        self.dab_stuck_since: float | None = None  # monotonic: re-enumerating did not help either
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
            self._task = spawn(self._loop(), name="supervisor")
        self.ctx.liveness.register("supervisor", max_age_s=4 * self.cfg.check_interval_s + 120, restart=self.restart_loop)
        self.ctx.liveness.task("supervisor", self._task)
        self.ctx.liveness.on_restart = self.note_loop_restart
        if not getattr(self, "_update_resumed", False):
            self._update_resumed = True
            from . import update

            update.resume(self.ctx)  # an update that restarted us is followed again; a finished one's result is shown

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.host.close()

    @property
    def cfg(self):  # noqa: ANN201
        return self.ctx.config.system.supervisor

    def note_loop_restart(self, name: str, attempt: int, ok: bool, why: str) -> None:
        """Liveness restarted a loop inside dawn-core: a self-repair like any other (Diagnostics → System)."""
        from ..liveness import BACKOFF_S

        nxt = BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1]
        self._note(Repair(self.ctx.store.iso(), f"dawn-core: {name}", why, ok, f"restarted the loop (attempt {attempt})" if ok else "the restart failed", nxt))

    async def restart_loop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = spawn(self._loop(), name="supervisor")
        self.ctx.liveness.task("supervisor", self._task)

    async def _loop(self) -> None:
        from ..power.service import cool_plan

        while True:
            plan = cool_plan(self.ctx)
            # half as often in cool mode (poll_scale is 1 again within an alarm's preparation window)
            await asyncio.sleep(self.cfg.check_interval_s * (plan.poll_scale if plan else 1.0))
            self.ctx.liveness.beat("supervisor")
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
        for step in (self._core_services, self._units, self._face, self._face_recycle, self._dab, self._user_audio, self._timed,
                     self._chrony, self._gpsd, self._wifi_driver):
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
            if unit == self.ctx.config.dab.service_name and state == "inactive" and self._dab_paused_on_purpose():
                self._bad_for(f"unit:{unit}", False, now)
                continue
            bad_for = self._bad_for(f"unit:{unit}", state == "failed" or (own and state == "inactive"), now)
            if state == "failed" or bad_for >= INACTIVE_FOR_S:
                await self._repair(f"unit:{unit}", unit, f"{state}" + (f" for {bad_for:.0f} s" if state != "failed" else ""), lambda u=unit: self._restart_unit(u), now)

    def _dab_paused_on_purpose(self) -> bool:
        """Cool mode stopped the decoder, and the loop that will start it again is alive. With that loop dead the usual
        policy applies, so a controller that died cannot leave the radio off."""
        try:
            from ..dab.service import DabService

            paused = bool(self.ctx.svc(DabService).paused)
        except (ImportError, KeyError):
            return False
        return paused and "power" in self.ctx.liveness.names() and "power" not in self.ctx.liveness.check()

    async def _face(self, now: float) -> None:
        """No face on the socket (or one whose page has frozen) while dawn-face is active: Chromium is blank, on an
        error page or hung, which systemd cannot see."""
        if self.ctx.sim or not self.cfg.enabled or self.ctx.ws_hub is None:
            return
        seen = self.ctx.ws_hub.face_seen(FACE_STALE_S)
        frozen = seen and self.ctx.ws_hub.face_frozen(FRAME_STALE_MS)  # pings arrive but no frame is drawn
        absent_for = self._bad_for("face", not seen or frozen, now)
        if (seen and not frozen) or absent_for < self.cfg.face_absent_s:
            return
        if self.unit_states.get("dawn-face") != "active" or not await self.host.unit_enabled("dawn-face"):
            return  # stopped, starting or restarting: systemd (or the unit check above) is on it
        active_for = await self.host.unit_active_for("dawn-face")
        if active_for is not None and active_for < self.cfg.face_absent_s:
            return  # Chromium is still coming up (slow on a Pi 3 or Zero 2)
        why = f"the face's page draws no frames (frozen screen) for {absent_for:.0f} s" if frozen else f"no face connected for {absent_for:.0f} s"
        await self._repair("face", "dawn-face", why, lambda: self._restart_unit("dawn-face"), now)

    async def _face_recycle(self, now: float) -> None:
        """Once a day, at the first quiet moment after `face_recycle_at`, a fresh Chromium: one that runs for months
        grows (memory, GPU state) and is better replaced while nobody is looking than found frozen in the morning."""
        if self.ctx.sim or not self.cfg.enabled or not self.cfg.face_recycle_at:
            return
        if self.unit_states.get("dawn-face") != "active" or not await self.host.unit_enabled("dawn-face"):
            return
        wall = self.ctx.store.now()
        due = recycle_due(wall, self.cfg.face_recycle_at, self._recycled_on)
        if due is None:
            return
        if due == "missed":
            log.info("supervisor: no quiet moment for the face's daily refresh since %s; skipping today", self.cfg.face_recycle_at)
            self._recycled_on = wall.date()
            return
        active_for = await self.host.unit_active_for("dawn-face")
        why_not = quiet_for_recycle(self.ctx.store.state, wall, active_for)
        if why_not:
            return
        self._recycled_on = wall.date()
        await self._repair("face-recycle", "dawn-face", "daily refresh", lambda: self._restart_unit("dawn-face"), now)

    async def _chrony(self, now: float) -> None:
        """chronyd that no longer answers chronyc (4 polls in a row, two minutes): systemd sees it as running."""
        if not self.cfg.enabled:
            return
        try:
            from ..timesync.service import TimeSourceService

            n = self.ctx.svc(TimeSourceService).chronyc_timeouts
        except (ImportError, KeyError):
            return
        if n >= CHRONYC_TIMEOUTS:
            await self._repair("chrony-hung", "chrony", f"chronyc timed out {n} times in a row", lambda: self._restart_unit("chrony"), now)

    async def _gpsd(self, now: float) -> None:
        """A receiver is fitted and gpsd is connected, but no position report has come for two minutes: gpsd lost it."""
        if not self.cfg.enabled:
            return
        try:
            from ..timesync.gps import GpsdClient
            from ..timesync.service import TimeSourceService

            ts = self.ctx.svc(TimeSourceService)
        except (ImportError, KeyError):
            return
        c = ts.gps
        quiet = None
        if ts.presence == "present" and isinstance(c, GpsdClient) and c.connected:
            last = c.fix.last_tpv_at
            quiet = time.time() - last if last else None
        hung = quiet is not None and quiet > GPSD_QUIET_S
        self._bad_for("gpsd-quiet", hung, now)
        if hung:
            await self._repair("gpsd-quiet", "gpsd", f"no position report for {quiet:.0f} s", lambda: self._restart_unit("gpsd"), now)

    async def _wifi_driver(self, now: float) -> None:
        """Optional (wifi_driver_recovery): a saved Wi-Fi network but no usable wlan0 for 10 minutes. NetworkManager is
        restarted twice at most; after that Diagnostics suggests a reboot (never done automatically)."""
        if self.ctx.sim or not self.cfg.enabled or not self.cfg.wifi_driver_recovery:
            return
        net = self.ctx.store.state.system.network
        if not net.wifi_configured or net.online:
            self._bad_for("wifi-driver", False, now)
            self.wifi_driver_tries = 0
            return
        iface = self.ctx.config.network.hotspot.interface
        missing = not await self.host.exists(f"/sys/class/net/{iface}")
        rc, out = (127, "") if missing else await self.host.run("nmcli", "-t", "-f", "DEVICE,STATE", "dev", timeout=10)
        unavailable = rc == 0 and any(ln.split(":")[:2] == [iface, "unavailable"] for ln in out.splitlines())
        bad_for = self._bad_for("wifi-driver", missing or unavailable, now)
        if bad_for >= WIFI_DRIVER_S and self.wifi_driver_tries < 2:
            why = f"{iface} {'missing' if missing else 'unavailable'} for {bad_for:.0f} s"
            if await self._repair("wifi-driver", "NetworkManager", why, lambda: self._restart_unit("NetworkManager"), now):
                self.wifi_driver_tries += 1

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
            self._bad_for("dab-nosync", False, now)
            return
        reachable = await dab.client.reachable()
        dead_for = self._bad_for("dab", not reachable, now)
        if dead_for >= self.cfg.welle_dead_s:

            async def fix() -> tuple[bool, str]:
                ok = await dab.restart_welle(reason="supervisor")
                return ok, "restarting welle-cli" if ok else "restarted moments ago already"

            await self._repair("dab", "DAB decoder (welle-cli)", f"not answering for {dead_for:.0f} s", fix, now)
        if reachable:
            await self._dab_zombie(dab, now)

    async def _dab_zombie(self, dab: Any, now: float) -> None:
        """welle-cli answers but no samples arrive (a stick that stopped delivering them: its OFDM thread waits for ever,
        and burns CPU doing it): no sync for `dab.no_sync_restart_s` on a channel a scan found good, with no scan
        running. Restart the decoder; after three in a row with the stick still on the bus, re-enumerate it on USB
        (dawn-usb-reset); if that does not bring sync back within three minutes, Diagnostics says to replug it."""
        scanning = bool(dab._scan_task and not dab._scan_task.done())
        zombie = not dab.live_sync and not scanning and dab.channel_known_good()
        nosync_for = self._bad_for("dab-nosync", zombie, now)
        if not zombie:
            if dab.live_sync and now - self._good_since.get("dab-nosync", now) >= HEAL_S:
                self.dab_nosync_restarts = 0  # ten minutes of sync: the count starts over
            if dab.live_sync:
                self.dab_stuck_since = None
                self._usb_reset_at = None
            return
        if self._usb_reset_at is not None and now - self._usb_reset_at >= USB_RESET_GRACE_S and self.dab_stuck_since is None:
            self.dab_stuck_since = self._usb_reset_at
            log.error("supervisor: the SDR stick still delivers no samples %.0f s after re-enumerating it", now - self._usb_reset_at)
        if nosync_for < self.ctx.config.dab.no_sync_restart_s:
            return
        if self.dab_nosync_restarts >= NOSYNC_RESTARTS_BEFORE_RESET and self.ctx.store.state.dab.sdr_present:
            if self._usb_reset_at is None:

                async def reset() -> tuple[bool, str]:
                    rc, out = await self.host.sudo("/usr/local/bin/dawn-usb-reset", timeout=30)
                    if rc == 0:
                        self._usb_reset_at = now
                    return (rc == 0), (out.strip()[:200] or "re-enumerated the stick") if rc == 0 else f"dawn-usb-reset failed: {out.strip()[:200] or rc}"

                await self._repair("dab-usb", "RTL-SDR stick", f"no samples through {self.dab_nosync_restarts} decoder restarts", reset, now)
            return

        async def fix() -> tuple[bool, str]:
            ok = await dab.restart_welle(reason="no sync")
            if ok:
                self.dab_nosync_restarts += 1
            return ok, "restarting welle-cli" if ok else "restarted moments ago already"

        await self._repair("dab-nosync", "DAB decoder (welle-cli)", f"no sync for {nosync_for:.0f} s on {dab.channel}, a channel scanned good", fix, now)

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
        # a pipewire that is `active` but answers nothing: every client blocks (mpv, pw-play, pw-dump), so nothing is
        # heard and nothing can tell; systemd sees a healthy unit. wpctl timing out, check after check, is the sign.
        if not fixed and self.user_states.get("pipewire") == "active":
            rc, _ = await self.host.run("wpctl", "status", timeout=WPCTL_TIMEOUT_S)
            if self._bad_for("user:pipewire-hung", rc == 124, now) >= PIPEWIRE_HUNG_FOR_S:
                if await self._repair("user:pipewire-hung", "pipewire (PipeWire session)", f"wpctl not answering for {PIPEWIRE_HUNG_FOR_S:.0f} s",
                                      lambda: self._restart_user_unit("pipewire"), now):
                    fixed = True
        else:
            self._bad_for("user:pipewire-hung", False, now)
        if fixed:
            await self._reselect_audio()

    async def _timed(self, now: float) -> None:
        """dawn-timed writes its status every couple of seconds; a stale file is a hung daemon."""
        if not self.cfg.enabled or self.unit_states.get("dawn-timed") != "active":
            self._bad_for("timed", False, now)
            return
        age = self._status_age(self.ctx.config.diagnostics.timed_status_file, now)
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

    def _status_age(self, path: str, now: float) -> float | None:
        """How long dawn-timed's `updated_at` has stayed the same, on the supervisor's own monotonic clock. Comparing the
        stamp with the wall clock made a clock step (chrony at boot, by an hour) look like a hung daemon."""
        try:
            value = json.loads(Path(path).read_text())["updated_at"]
        except (OSError, ValueError, KeyError, TypeError):
            return None  # no file (an older dawn-timed), or not readable: nothing to judge by
        if value != self._timed_value:
            self._timed_value, self._timed_changed = value, now
        return now - self._timed_changed

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
