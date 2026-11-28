"""Software update: check git for new commits; run dawn-update (git pull + install.sh) via sudo."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from ..context import DawnContext
from ..tasks import spawn

log = logging.getLogger("dawn.update")

DEFAULT_REPO = "https://github.com/tunlezah/dawn.git"  # as in install.sh and dawn-update
UNIT = "dawn-update.service"
LOG_FILE = Path("/var/log/dawn-update.log")
FOLLOW_MAX_S = 3600  # follow the log for an hour at most (the unit has a 45-minute limit)


def ENV_FILE(ctx: DawnContext) -> Path:  # noqa: N802 - a path, like the constants above
    return ctx.data_dir / "update.env"


def RESULT_FILE(ctx: DawnContext) -> Path:  # noqa: N802
    return ctx.data_dir / "update-result.json"


async def _git(ctx: DawnContext, *args: str, timeout: float = 60) -> tuple[int, str]:
    repo = ctx.config.system.update_repo_dir
    try:
        proc = await asyncio.create_subprocess_exec("git", "-C", repo, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as e:
        return 1, str(e)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()  # a git fetch stuck on the network is not left running
        return 1, f"git {args[0]} timed out after {timeout:.0f} s"
    return proc.returncode or 0, out.decode(errors="ignore").strip()


async def check(ctx: DawnContext) -> dict:
    branch = ctx.config.system.update_branch
    rc, _ = await _git(ctx, "remote", "get-url", "origin")
    if rc != 0:  # a checkout installed from a copy: dawn-update adds the same remote
        await _git(ctx, "remote", "add", "origin", DEFAULT_REPO)
    rc, out = await _git(ctx, "fetch", "--quiet", "origin", branch)
    if rc != 0:
        log.warning("update check: git fetch failed: %s", out)
        return {"ok": False, "error": "git fetch failed", "available": False}
    _, local = await _git(ctx, "rev-parse", "--short", "HEAD")
    _, remote = await _git(ctx, "rev-parse", "--short", f"origin/{branch}")
    rc, _ = await _git(ctx, "merge-base", "HEAD", f"origin/{branch}")
    if rc != 0:
        ctx.store.state.system.update_available = False
        ctx.store.touch()
        return {"ok": False, "available": False, "local": local, "remote": remote,
                "error": f"this install's git history is unrelated to origin/{branch}; run Update once to see how to fix it"}
    _, behind = await _git(ctx, "rev-list", "--count", f"HEAD..origin/{branch}")
    available = behind.isdigit() and int(behind) > 0
    ctx.store.state.system.update_available = available
    ctx.store.touch()
    return {"ok": True, "available": available, "local": local, "remote": remote, "commits_behind": int(behind) if behind.isdigit() else None}


class UpdateRefused(Exception):
    """An update now would land on an alarm: 409 with the reason."""


def quiet_reason(ctx: DawnContext, now: datetime | None = None) -> str | None:
    """Why an update may not start now (None: it may). An update restarts dawn-core and the radio; never into a ring,
    a snooze or a nap, nor within update_quiet_minutes of the next alarm or its light wake."""
    st = ctx.store.state
    if st.alarms.ringing is not None:
        r = st.alarms.ringing
        return f"{r.label} is {'snoozed' if r.snoozed_until else 'ringing'}"
    if st.timers.nap is not None:
        return "a nap timer is running"
    nxt = st.alarms.next
    quiet = ctx.config.system.update_quiet_minutes
    if nxt is not None and quiet > 0:
        now = now or ctx.store.now()
        try:
            first = min(datetime.fromisoformat(t) for t in (nxt.at, nxt.light_wake_at) if t)
        except ValueError:
            return None
        if first - now < timedelta(minutes=quiet):
            return f"the {nxt.label or 'next'} alarm is due at {first.strftime('%H:%M')}; updates wait until {quiet} minutes before an alarm"
    return None


async def run_update(ctx: DawnContext) -> None:
    st = ctx.store.state.system
    if st.update_running:
        return
    why = quiet_reason(ctx)
    if why:
        raise UpdateRefused(why)
    st.update_running = True
    st.update_log = "starting update…\n"
    ctx.store.touch()
    ctx.db.log_event("update_start")
    spawn(_run(ctx), name="dawn-update", owner="system")


async def _systemctl_active(unit: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec("systemctl", "is-active", unit, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, TimeoutError):
        return "unknown"
    return out.decode(errors="ignore").strip() or "unknown"


async def _run(ctx: DawnContext) -> None:
    """Start dawn-update.service (it runs outside dawn-core's cgroup, so dawn-core restarting does not kill it) and
    follow its log until it ends. dawn-core is restarted part way through; the next one picks the log up again
    (`resume`)."""
    st = ctx.store.state.system
    try:
        if ctx.sim:
            for line in ("(sim) git fetch origin main", "(sim) git merge --ff-only", "(sim) install.sh --update --no-build --no-restart",
                         "(sim) restarting dawn-timed dawn-dab, then dawn-core (healthy), then dawn-face", "(sim) === done ==="):
                await asyncio.sleep(0.6)
                st.update_log = (st.update_log or "") + line + "\n"
                ctx.store.touch()
            return
        try:
            ENV_FILE(ctx).write_text(f"DAWN_UPDATE_BRANCH={ctx.config.system.update_branch}\n")
        except OSError as e:
            raise RuntimeError(f"cannot write {ENV_FILE(ctx)}: {e}") from e
        offset = _log_size()
        proc = await asyncio.create_subprocess_exec(ctx.config.system.sudo_binary, "-n", "systemctl", "start", "--no-block", UNIT,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        if proc.returncode:
            raise RuntimeError(f"could not start {UNIT}: {out.decode(errors='ignore').strip() or proc.returncode}")
        await _follow(ctx, offset)
    except Exception as e:  # noqa: BLE001
        st.update_log = (st.update_log or "") + f"\nupdate failed: {e}\n"
        log.exception("update failed")
    finally:
        st.update_running = False
        ctx.store.touch()


async def _follow(ctx: DawnContext, offset: int) -> None:
    """Copy the update log into the state while the unit runs (system.update_running follows `is-active`)."""
    st = ctx.store.state.system
    started = False
    for _ in range(FOLLOW_MAX_S):
        state = await _systemctl_active(UNIT)
        started = started or state in ("active", "activating")
        text = _log_since(offset)
        if text:
            st.update_log = text[-20000:]
            ctx.store.touch()
        if started and state not in ("active", "activating", "reloading"):
            break
        if not started and _ > 15:
            break  # it never showed as running: it finished at once, or never started (the log says which)
        await asyncio.sleep(1.0)
    st.update_log = (_log_since(offset) or st.update_log or "")[-20000:]


def resume(ctx: DawnContext) -> None:
    """At dawn-core's start: an update still running (it restarted us) is followed again, and the result of one that
    finished while we were down is shown (a face toast and the Diagnostics row)."""
    if ctx.sim:
        return
    spawn(_resume(ctx), name="dawn-update-resume", owner="system")


async def _resume(ctx: DawnContext) -> None:
    st = ctx.store.state.system
    if await _systemctl_active(UNIT) in ("active", "activating"):
        st.update_running = True
        ctx.store.touch()
        try:
            await _follow(ctx, max(0, _log_size() - 20000))
        finally:
            st.update_running = False
            ctx.store.touch()
    announce_result(ctx)


def announce_result(ctx: DawnContext) -> dict | None:
    """The last update's result (update-result.json) in the state, and a face toast the first time it is seen."""
    try:
        res = json.loads(RESULT_FILE(ctx).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(res, dict):
        return None
    ctx.store.state.system.update_result = res
    ctx.store.touch()
    seen = ctx.db.get("update.result_seen")
    if seen != res.get("at"):
        ctx.db.try_set("update.result_seen", res.get("at"))
        try:
            from ..face.service import FaceService

            face = ctx.svc(FaceService)
            if res.get("status") == "rolled_back":
                face.toast("Update rolled back", f"{res.get('message', '')}. Dawn is running the version it had before.", level="warning", seconds=30)
            elif res.get("status") == "ok" and res.get("version"):
                face.toast("Updated", f"Dawn {res['version']}", seconds=8)
        except Exception:  # noqa: BLE001
            log.debug("no face to show the update result on", exc_info=True)
    return res


def _log_size() -> int:
    try:
        return LOG_FILE.stat().st_size
    except OSError:
        return 0


def _log_since(offset: int) -> str:
    try:
        with LOG_FILE.open("rb") as f:
            f.seek(offset)
            return f.read().decode(errors="ignore")
    except OSError:
        return ""


def update_log_tail(lines: int = 50) -> str:
    try:
        return "\n".join(LOG_FILE.read_text(errors="ignore").splitlines()[-lines:])
    except OSError:
        return ""
