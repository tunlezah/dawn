"""Software update: check git for new commits; run dawn-update (git pull + install.sh) via sudo."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..context import DawnContext

log = logging.getLogger("dawn.update")


async def _git(ctx: DawnContext, *args: str, timeout: float = 60) -> tuple[int, str]:
    repo = ctx.config.system.update_repo_dir
    try:
        proc = await asyncio.create_subprocess_exec("git", "-C", repo, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="ignore").strip()
    except (TimeoutError, OSError) as e:
        return 1, str(e)


async def check(ctx: DawnContext) -> dict:
    branch = ctx.config.system.update_branch
    rc, _ = await _git(ctx, "fetch", "--quiet", "origin", branch)
    if rc != 0:
        return {"ok": False, "error": "git fetch failed", "available": False}
    _, local = await _git(ctx, "rev-parse", "--short", "HEAD")
    _, remote = await _git(ctx, "rev-parse", "--short", f"origin/{branch}")
    _, behind = await _git(ctx, "rev-list", "--count", f"HEAD..origin/{branch}")
    available = behind.isdigit() and int(behind) > 0
    ctx.store.state.system.update_available = available
    ctx.store.touch()
    return {"ok": True, "available": available, "local": local, "remote": remote, "commits_behind": int(behind) if behind.isdigit() else None}


async def run_update(ctx: DawnContext) -> None:
    st = ctx.store.state.system
    if st.update_running:
        return
    st.update_running = True
    st.update_log = "starting update…\n"
    ctx.store.touch()
    ctx.db.log_event("update_start")
    asyncio.create_task(_run(ctx), name="dawn-update")


async def _run(ctx: DawnContext) -> None:
    st = ctx.store.state.system
    try:
        if ctx.sim:
            for line in ("(sim) git fetch origin main", "(sim) git merge --ff-only", "(sim) install.sh --update --no-build", "(sim) done; services would restart now"):
                await asyncio.sleep(0.6)
                st.update_log = (st.update_log or "") + line + "\n"
                ctx.store.touch()
            return
        proc = await asyncio.create_subprocess_exec(ctx.config.system.sudo_binary, "-n", "/usr/local/bin/dawn-update", ctx.config.system.update_branch, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            st.update_log = ((st.update_log or "") + line.decode(errors="ignore"))[-20000:]
            ctx.store.touch()
        await proc.wait()
        st.update_log = (st.update_log or "") + f"\nexit code {proc.returncode}\n"
    except Exception as e:  # noqa: BLE001
        st.update_log = (st.update_log or "") + f"\nupdate failed: {e}\n"
        log.exception("update failed")
    finally:
        st.update_running = False
        ctx.store.touch()


def update_log_tail(lines: int = 50) -> str:
    try:
        return "\n".join(Path("/var/log/dawn-update.log").read_text(errors="ignore").splitlines()[-lines:])
    except OSError:
        return ""
