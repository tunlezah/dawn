"""Software update: check git for new commits; run dawn-update (git pull + install.sh, health check, rollback).

dawn-update moves itself into a systemd unit of its own (`dawn-update.service`) before it does anything: it restarts
dawn-core half-way through, and a script started from here would be stopped along with the rest of dawn-core's
control group. So nothing is read from the script's output: its log file is followed instead, and a dawn-core that
the update has just restarted picks the log up again where the old one left off (`resume`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from ..context import DawnContext

log = logging.getLogger("dawn.update")

DEFAULT_REPO = "https://github.com/tunlezah/dawn.git"  # as in install.sh and dawn-update
UNIT = "dawn-update.service"
LOG_PATH = Path("/var/log/dawn-update.log")
HEADER = "=== dawn-update "  # every run begins its part of the log with this
HOLD_BEFORE_ALARM_S = 30 * 60  # an update restarts dawn-core, the decoder and the kiosk, and can take ten minutes
FOLLOW_MAX_S = 45 * 60

_tasks: set[asyncio.Task[None]] = set()  # the event loop only keeps weak references to tasks


class UpdateBlocked(RuntimeError):
    """An update was asked for at a moment it must not run."""


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


def blocked(ctx: DawnContext, now: datetime | None = None) -> str | None:
    """Why an update must not start now: it restarts everything an alarm needs, the sound included."""
    alarms = ctx.store.state.alarms
    if alarms.ringing is not None:
        return f"{alarms.ringing.label} is {'snoozed' if alarms.ringing.snoozed_until else 'ringing'}; update once it has been stopped"
    if alarms.next is not None:
        try:
            at = datetime.fromisoformat(alarms.next.at)
            left = (at - (now or ctx.store.now())).total_seconds()
        except (TypeError, ValueError):
            return None
        if left <= HOLD_BEFORE_ALARM_S:
            return f"{alarms.next.label} rings at {at.strftime('%H:%M')}; update after it (an update restarts the clock's services and can take ten minutes)"
    if ctx.store.state.timers.nap is not None:
        return "a nap timer is running; update after it"
    return None


async def run_update(ctx: DawnContext) -> None:
    st = ctx.store.state.system
    if st.update_running:
        return
    reason = blocked(ctx)
    if reason:
        raise UpdateBlocked(reason)
    st.update_running = True
    st.update_log = "starting update…\n"
    ctx.store.touch()
    ctx.db.log_event("update_start")
    _spawn(_run(ctx))


def _spawn(coro) -> None:  # noqa: ANN001
    task = asyncio.create_task(coro, name="dawn-update")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def resume(ctx: DawnContext) -> None:
    """dawn-core has just started. If that is because an update restarted it, the update is still running in its
    own unit: show it as running and follow its log to the end."""
    if ctx.sim or await _unit_state() not in ("active", "activating"):
        return
    st = ctx.store.state.system
    st.update_running = True
    st.update_log = log_section()
    ctx.store.touch()
    log.info("an update is in progress; following %s", LOG_PATH)
    _spawn(_follow(ctx))


async def _run(ctx: DawnContext) -> None:
    st = ctx.store.state.system
    try:
        if ctx.sim:
            for line in ("(sim) git fetch origin main", "(sim) git merge --ff-only", "(sim) install.sh --update --no-build", "(sim) done; services would restart now"):
                await asyncio.sleep(0.6)
                st.update_log = (st.update_log or "") + line + "\n"
                ctx.store.touch()
            return
        started = time.time()
        proc = await asyncio.create_subprocess_exec(ctx.config.system.sudo_binary, "-n", "/usr/local/bin/dawn-update", ctx.config.system.update_branch, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdout
        # With systemd the script hands over to its own unit and returns at once, printing nothing (or why it could
        # not start). Without (a box that is not a Pi install) it runs here, and its output is the progress.
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            st.update_log = ((st.update_log or "") + line.decode(errors="ignore"))[-20000:]
            ctx.store.touch()
        await proc.wait()
        if proc.returncode:
            st.update_log = (st.update_log or "") + f"\nexit code {proc.returncode}\n"
        elif await _unit_state() in ("active", "activating") or log_section(since=started):
            await _follow(ctx, since=started)
    except Exception as e:  # noqa: BLE001
        st.update_log = (st.update_log or "") + f"\nupdate failed: {e}\n"
        log.exception("update failed")
    finally:
        st.update_running = False
        ctx.store.touch()


async def _follow(ctx: DawnContext, since: float | None = None) -> None:
    """Show the update's log as it grows, until its unit has ended."""
    st = ctx.store.state.system
    deadline = time.monotonic() + FOLLOW_MAX_S
    ended = 0
    try:
        while ended < 2 and time.monotonic() < deadline:
            text = await asyncio.to_thread(log_section, since)
            if text and text != st.update_log:
                st.update_log = text
                ctx.store.touch()
            ended = ended + 1 if await _unit_state() not in ("active", "activating") else 0  # twice: the last lines are in the file by then
            await asyncio.sleep(1.0)
        if ended < 2:
            st.update_log = (st.update_log or "") + f"\nstill running after {FOLLOW_MAX_S // 60} min; see {LOG_PATH} and `systemctl status {UNIT}`\n"
    finally:
        st.update_running = False
        ctx.store.touch()


async def _unit_state() -> str:
    """`systemctl is-active` of the update's unit: active while it runs, inactive once it has ended (it is a
    transient unit, gone when done), unknown where there is no systemd."""
    try:
        proc = await asyncio.create_subprocess_exec("systemctl", "is-active", UNIT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return "unknown"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()
        return "unknown"
    return out.decode().strip() or "unknown"


def log_section(since: float | None = None, path: Path | None = None, limit: int = 20000) -> str:
    """The latest run's part of the update log (from its `=== dawn-update` line on), at most `limit` characters
    from its end; "" when there is none, or when the file has not been written since `since` (time.time())."""
    path = path or LOG_PATH
    try:
        if since is not None and path.stat().st_mtime < since - 1:
            return ""
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 256_000))
            text = f.read().decode(errors="ignore")
    except OSError:
        return ""
    start = text.rfind(HEADER)
    return text[start:][-limit:] if start >= 0 else ""
