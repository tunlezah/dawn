"""The evening readiness check: half an hour before bedtime (21:30 with sleep mode off), the next alarm due within 14
hours is checked the way its preparation will check it, without disturbing anything: is there an output, does
PipeWire answer, can its players start, and for a DAB alarm is the station on air (only when nobody is listening to the
radio, and never by retuning). The result shows under the sleep clock, in the standby strip and on Home
("06:30 ✓", or "06:30 · will start on the chime: no DAB signal on 9A"), so there is time to fix it before sleeping.
The preparation minutes before the alarm still decide what actually rings.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from typing import Any

from ..context import DawnContext
from ..state.ui import EveningCheck

log = logging.getLogger("dawn.alarms.evening")

LEAD = timedelta(minutes=30)  # before bedtime
NO_SLEEP_AT = "21:30"
LOOK_AHEAD = timedelta(hours=14)
WINDOW = timedelta(hours=2)  # a check missed by more than this (dawn-core was down) waits for tomorrow


def check_time(ctx: DawnContext, day: datetime) -> datetime:
    """When the check runs on `day` (local)."""
    sl = ctx.config.display.sleep
    hhmm = sl.start if sl.enabled and sl.start_at_time else NO_SLEEP_AT
    h, m = (int(x) for x in hhmm.split(":"))
    at = day.replace(hour=h, minute=m, second=0, microsecond=0)
    return at - LEAD if sl.enabled and sl.start_at_time else at


def due(ctx: DawnContext, now: datetime, done_on: str | None) -> bool:
    at = check_time(ctx, now)
    return done_on != now.date().isoformat() and at <= now < at + WINDOW


async def run(ctx: DawnContext, alarms: Any, now: datetime) -> EveningCheck | None:
    """Check the next alarm within LOOK_AHEAD. Never raises: a check that fails says so in its message."""
    st = ctx.store.state
    nxt = st.alarms.next
    if nxt is None:
        return None
    at = datetime.fromisoformat(nxt.at)
    if at - now > LOOK_AHEAD:
        return None
    try:
        ok, msg = await _check(ctx, alarms, nxt.id)
    except Exception as e:  # noqa: BLE001
        log.exception("evening check failed")
        ok, msg = None, f"could not be checked ({type(e).__name__}); it is checked again before it rings"
    res = EveningCheck(alarm_id=nxt.id, label=nxt.label, at=nxt.at, ok=ok, message=msg, checked_at=now.isoformat(timespec="seconds"))
    log.info("evening check of %s at %s: %s", nxt.label, nxt.at, "ready" if ok else msg)
    ctx.db.log_event("evening_check", alarm_id=nxt.id, ok=ok, message=msg)
    return res


async def _check(ctx: DawnContext, alarms: Any, alarm_id: int) -> tuple[bool | None, str]:
    from ..audio.service import AudioService

    row = alarms.get(alarm_id)
    if row is None:
        return None, "the alarm could not be read; it is checked again before it rings"
    audio = ctx.svc(AudioService)
    try:
        sinks = await audio.backend.list_sinks()
    except Exception as e:  # noqa: BLE001
        return False, f"will start on the backup tone: the audio system does not answer ({type(e).__name__})"
    if not sinks:
        return False, "will start on the backup tone: no audio output found"
    if not ctx.sim and shutil.which("mpv") is None:
        return False, "will start on the backup tone: mpv is not installed"
    source = alarms._resolve(row.source)
    if not source.startswith("dab:"):
        return True, ""
    from ..dab.service import DabService

    dab = ctx.svc(DabService)
    if dab.paused:
        return None, "the radio is paused (cool mode); it comes back and is checked before the alarm"
    listening = [s for s in (audio.arbiter.slot("user"), audio.arbiter.slot("sleep"))
                 if s is not None and s.source.kind == "dab" and s.state in ("playing", "starting", "ducked")]
    if listening:
        return None, "the radio is playing; it is checked before the alarm"
    status, msg = await dab.prepare_for(source[4:], may_retune=False, occurrence=f"evening:{row.id}")
    if status == "ok":
        return True, ""
    if status == "fail":
        return False, f"will start on the chime: {msg}"
    return None, f"{msg}; checked again before the alarm"
