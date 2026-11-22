"""Software update: the check in dawn-core and the dawn-update script, against real git repositories on disk
(a bare "GitHub" and an install cloned or copied from it). Nothing touches the network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dawn_core.system import update

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV}).stdout.strip()


def commit(repo: Path, name: str, version: str = "0.1.0") -> None:
    (repo / "core/dawn_core").mkdir(parents=True, exist_ok=True)
    (repo / "core/dawn_core/__init__.py").write_text(f'__version__ = "{version}"\n')
    (repo / name).write_text(name)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)


@pytest.fixture()
def github(tmp_path: Path, monkeypatch) -> Path:
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "deploy").mkdir()
    (work / "deploy/install.sh").write_text("#!/bin/sh\necho \"install.sh $*\"\n")
    (work / "deploy/install.sh").chmod(0o755)
    commit(work, "first")
    bare = tmp_path / "github.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    git(work, "remote", "add", "origin", str(bare))
    return work


def push_new_version(work: Path, version: str) -> None:
    commit(work, f"release-{version}", version)
    git(work, "push", "-q", "origin", "main")


def ctx_for(repo: Path):
    system = SimpleNamespace(update_available=False)
    return SimpleNamespace(
        config=SimpleNamespace(system=SimpleNamespace(update_repo_dir=str(repo), update_branch="main")),
        store=SimpleNamespace(state=SimpleNamespace(system=system), touch=lambda: None),
    )


async def test_check_finds_new_commits(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)  # noqa: ASYNC221 - test setup
    ctx = ctx_for(install)
    assert (await update.check(ctx))["available"] is False
    push_new_version(github, "0.2.0")
    r = await update.check(ctx)
    assert r["ok"] and r["available"] and r["commits_behind"] == 1 and ctx.store.state.system.update_available


async def test_check_reports_unrelated_history(github: Path, tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "opt-dawn"  # installed from a copy, then committed locally: no remote, its own history
    install.mkdir()
    git(install, "init", "-q", "-b", "master")
    commit(install, "baseline")
    monkeypatch.setattr(update, "DEFAULT_REPO", str(tmp_path / "github.git"))
    r = await update.check(ctx_for(install))
    assert git(install, "remote", "get-url", "origin") == str(tmp_path / "github.git")  # the missing remote was added
    assert not r["ok"] and not r["available"] and "unrelated" in r["error"]


# ---- the dawn-update script ------------------------------------------------------
def run_dawn_update(install: Path, tmp_path: Path, repo_url: Path, *, health: str = "follow") -> subprocess.CompletedProcess[str]:
    """dawn-update with sudo, systemctl and curl stubbed: systemctl records each call (in order) in calls.log, and
    curl answers /api/health with the version in the checkout ("follow"), an old version ("stale") or nothing ("down")."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    (stubs / "sudo").write_text('#!/bin/sh\n[ "$1" = "-u" ] && shift 2\nexec "$@"\n')  # run as ourselves
    (stubs / "systemctl").write_text(f'#!/bin/sh\necho "systemctl $*"\necho "$*" >>"{calls}"\n')
    (stubs / "curl").write_text(
        "#!/bin/sh\n"
        f'case "{health}" in down) exit 7;; stale) echo \'{{"alarms_alive": true, "version": "0.0.1"}}\'; exit 0;; esac\n'
        'v="$(sed -n \'s/^__version__ = "\\(.*\\)"/\\1/p\' "$DAWN_DIR/core/dawn_core/__init__.py")"\n'
        'echo "{\\"alarms_alive\\": true, \\"version\\": \\"$v\\"}"\n'
    )
    for f in ("sudo", "systemctl", "curl"):
        (stubs / f).chmod(0o755)
    env = {**os.environ, **GIT_ENV, "PATH": f"{stubs}:{os.environ['PATH']}", "DAWN_DIR": str(install), "DAWN_REPO": str(repo_url),
           "DAWN_UPDATE_LOG": str(tmp_path / "update.log"), "DAWN_HEALTH_WAIT_S": "2", "DAWN_UPDATE_RESULT": str(tmp_path / "result.json")}
    return subprocess.run(["bash", str(ROOT / "deploy/bin/dawn-update"), "main"], env=env, capture_output=True, text=True, timeout=60)


def systemctl_calls(tmp_path: Path) -> list[str]:
    p = tmp_path / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def test_dawn_update_fast_forwards_and_reinstalls(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    push_new_version(github, "0.2.0")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode == 0, r.stdout
    assert "(version 0.2.0)" in r.stdout and "install.sh --update --no-build --no-restart" in r.stdout and "=== done" in r.stdout
    assert git(install, "rev-parse", "HEAD") == git(github, "rev-parse", "HEAD")
    assert "=== done" in (tmp_path / "update.log").read_text()
    # in order: the decoder and dawn-timed, dawn-core (then healthy on 0.2.0), the face last for the new bundle
    assert systemctl_calls(tmp_path) == ["restart dawn-timed dawn-dab", "restart dawn-core", "restart dawn-face"]
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["status"] == "ok" and res["version"] == "0.2.0"


def test_dawn_update_adds_a_missing_remote(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    git(install, "remote", "remove", "origin")
    push_new_version(github, "0.2.0")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode == 0, r.stdout
    assert "added git remote origin" in r.stdout and "(version 0.2.0)" in r.stdout


def test_dawn_update_explains_unrelated_history_and_changes_nothing(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    install.mkdir()
    git(install, "init", "-q", "-b", "master")
    commit(install, "baseline")
    before = git(install, "rev-parse", "HEAD")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode != 0
    assert "unrelated to origin/main" in r.stdout and "reset --hard origin/main" in r.stdout and "install.sh" not in r.stdout
    assert git(install, "rev-parse", "HEAD") == before
    # the repair it prints, then the next update works
    git(install, "reset", "-q", "--hard", "origin/main")
    push_new_version(github, "0.2.0")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode == 0 and "(version 0.2.0)" in r.stdout, r.stdout


def test_version_files_agree() -> None:
    r = subprocess.run(["python3", str(ROOT / "scripts/version.py"), "check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    from dawn_core import __version__
    assert r.stdout.strip() == f"version {__version__}"


def test_dawn_update_rolls_back_when_core_does_not_come_up_healthy(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    before = git(install, "rev-parse", "HEAD")
    (install / "web/dist").mkdir(parents=True)
    (install / "web/dist/index.html").write_text("old bundle")
    # the new release's install builds a new bundle (as build-web.sh does: the old one goes to dist.old)
    (github / "deploy/install.sh").write_text(
        '#!/bin/sh\necho "install.sh $*"\ncd "$(dirname "$0")/../web" && mv dist dist.old && mkdir dist && echo new >dist/index.html\n')
    push_new_version(github, "0.2.0")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git", health="stale")  # core still reports the old version
    assert r.returncode != 0
    assert "did not report healthy with version 0.2.0" in r.stdout and "ROLLED BACK" in r.stdout
    assert git(install, "rev-parse", "HEAD") == before
    assert (install / "web/dist/index.html").read_text() == "old bundle" and not (install / "web/dist.old").exists()
    assert systemctl_calls(tmp_path)[-2:] == ["restart dawn-core", "restart dawn-face"]  # back on the old code, the face too
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["status"] == "rolled_back" and res["from"] == git(install, "rev-parse", "--short", "HEAD")


def test_dawn_update_rolls_back_when_the_install_fails(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    before = git(install, "rev-parse", "HEAD")
    (github / "deploy/install.sh").write_text('#!/bin/sh\necho "install.sh $*"\necho "web build failed" && exit 1\n')
    push_new_version(github, "0.2.0")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode != 0 and "install.sh failed" in r.stdout and "ROLLED BACK" in r.stdout
    assert git(install, "rev-parse", "HEAD") == before
    assert "restart dawn-timed dawn-dab" in systemctl_calls(tmp_path)  # what was running is restarted on the old code


def test_dawn_update_when_already_up_to_date_restarts_nothing(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode == 0 and "already up to date" in r.stdout and systemctl_calls(tmp_path) == []


# ---- the web build never leaves the face without a UI ------------------------------------------------------------
def _build_web(tmp_path: Path, vite_ok: bool, pages: tuple[str, ...] = ("index.html", "face/index.html")) -> subprocess.CompletedProcess[str]:
    web = tmp_path / "web"
    (web / "dist").mkdir(parents=True, exist_ok=True)
    (web / "dist/index.html").write_text("old")
    stubs = tmp_path / "npmstub"
    stubs.mkdir(exist_ok=True)
    make = " && ".join(f'mkdir -p "$(dirname dist.new/{p})" && echo new > dist.new/{p}' for p in pages)
    (stubs / "npm").write_text(
        "#!/bin/sh\n"
        'case "$1" in ci|run) exit 0;; esac\n'
        + (f"rm -rf dist.new && mkdir dist.new && {make}\nexit 0\n" if vite_ok else "rm -rf dist.new && mkdir dist.new && exit 137\n")
    )
    (stubs / "npm").chmod(0o755)
    return subprocess.run(["bash", str(ROOT / "deploy/build-web.sh"), str(web), str(stubs / "npm")], capture_output=True, text=True, timeout=30)


def test_a_failed_web_build_leaves_the_bundle_in_use(tmp_path: Path) -> None:
    r = _build_web(tmp_path, vite_ok=False)  # killed (137: the OOM killer on a Zero 2 W)
    assert r.returncode != 0 and "left as it was" in r.stdout
    assert (tmp_path / "web/dist/index.html").read_text() == "old" and not (tmp_path / "web/dist.new").exists()


def test_a_build_missing_the_face_page_is_not_swapped_in(tmp_path: Path) -> None:
    r = _build_web(tmp_path, vite_ok=True, pages=("index.html",))
    assert r.returncode != 0 and (tmp_path / "web/dist/index.html").read_text() == "old"


def test_a_complete_web_build_is_swapped_in_and_the_old_one_kept(tmp_path: Path) -> None:
    r = _build_web(tmp_path, vite_ok=True)
    assert r.returncode == 0, r.stdout
    assert (tmp_path / "web/dist/index.html").read_text().strip() == "new"
    assert (tmp_path / "web/dist/face/index.html").exists() and (tmp_path / "web/dist.old/index.html").read_text() == "old"


# ---- no update into an alarm --------------------------------------------------------------------------------------
async def test_an_update_is_refused_near_an_alarm(tmp_config) -> None:
    from datetime import timedelta

    from httpx import ASGITransport, AsyncClient

    from dawn_core.app import create_app
    from dawn_core.config import ConfigManager
    from dawn_core.state.ui import NextAlarm, RingingInfo, TimerInfo

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app), AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"X-Dawn-Client": "1"}) as c:
        ctx = app.state.ctx
        st = ctx.store.state
        now = ctx.store.now()

        def quiet() -> None:
            st.alarms.ringing, st.timers.nap = None, None
            st.alarms.next = NextAlarm(id=1, label="Work", at=(now + timedelta(hours=5)).isoformat(), in_seconds=5 * 3600)

        quiet()
        st.alarms.ringing = RingingInfo(kind="alarm", label="Work", started_at=now.isoformat(), source="chime:birds")
        r = await c.post("/api/system/update")
        assert r.status_code == 409 and "ringing" in r.json()["detail"]
        quiet()
        st.timers.nap = TimerInfo(kind="nap", ends_at=now.isoformat(), total_s=1200, remaining_s=600)
        assert (await c.post("/api/system/update")).status_code == 409
        quiet()
        st.alarms.next = NextAlarm(id=1, label="Work", at=(now + timedelta(minutes=90)).isoformat(), in_seconds=5400,
                                   light_wake_at=(now + timedelta(minutes=50)).isoformat())
        r = await c.post("/api/system/update")
        assert r.status_code == 409 and "Work" in r.json()["detail"]  # its light wake is within the hour
        quiet()
        r = await c.post("/api/system/update")
        assert r.status_code == 200 and r.json()["started"]
