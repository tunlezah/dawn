"""Software update: the check in dawn-core and the dawn-update script, against real git repositories on disk
(a bare "GitHub" and an install cloned or copied from it). Nothing touches the network."""

from __future__ import annotations

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
    (work / "deploy/install.sh").write_text(INSTALL_OK)
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
# Each version's install.sh is its own (it is run from the tree the update has just fast-forwarded to). It "restarts
# dawn-core" by writing what /api/health then answers, which the curl stub prints.
INSTALL_OK = """#!/bin/sh
echo "install.sh $*"
v=$(sed -n 's/^__version__ = "\\(.*\\)"/\\1/p' "$DAWN_DIR/core/dawn_core/__init__.py")
echo "{\\"ok\\":true,\\"version\\":\\"$v\\",\\"alarms_alive\\":true}" > "$STUBS/health"
"""
INSTALL_FAILS = '#!/bin/sh\necho "install.sh $*"\necho "pip: no matching distribution"\nexit 1\n'
INSTALL_BREAKS_CORE = INSTALL_OK.replace('\\"alarms_alive\\":true', '\\"alarms_alive\\":false')  # installs, but the new core's engine never ticks


def run_dawn_update(install: Path, tmp_path: Path, repo_url: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "sudo").write_text('#!/bin/sh\n[ "$1" = "-u" ] && shift 2\nexec "$@"\n')  # run as ourselves
    (stubs / "systemctl").write_text('#!/bin/sh\necho "systemctl $*"\n[ "$1" = "is-active" ] && exit 3\nexit 0\n')
    (stubs / "curl").write_text('#!/bin/sh\ncat "$STUBS/health" 2>/dev/null || exit 7\n')
    (stubs / "systemd-run").write_text('#!/bin/sh\necho "systemd-run $*" > "$STUBS/systemd-run.args"\n')
    for f in ("sudo", "systemctl", "curl", "systemd-run"):
        (stubs / f).chmod(0o755)
    env = {**os.environ, **GIT_ENV, "PATH": f"{stubs}:{os.environ['PATH']}", "DAWN_DIR": str(install), "DAWN_REPO": str(repo_url), "STUBS": str(stubs),
           "DAWN_UPDATE_LOG": str(tmp_path / "update.log"), "DAWN_UPDATE_DETACHED": "1", "DAWN_UPDATE_HEALTH_WAIT": "2", "DAWN_UPDATE_HEALTH_STEP": "0.05", **extra}
    return subprocess.run(["bash", str(ROOT / "deploy/bin/dawn-update"), "main"], env=env, capture_output=True, text=True, timeout=60)


def release(work: Path, version: str, install_sh: str) -> None:
    (work / "deploy/install.sh").write_text(install_sh)
    push_new_version(work, version)


def installed(tmp_path: Path, github: Path) -> Path:
    """/opt/dawn as a clone of GitHub at 0.1.0, whose install.sh works."""
    release(github, "0.1.0", INSTALL_OK)
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    return install


def test_a_failed_install_goes_back_to_the_version_that_was_running(github: Path, tmp_path: Path) -> None:
    install = installed(tmp_path, github)
    before = git(install, "rev-parse", "HEAD")
    release(github, "0.2.0", INSTALL_FAILS)
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode != 0 and "=== done" not in r.stdout, r.stdout
    assert "install.sh failed; going back to" in r.stdout and "rolled back to" in r.stdout and "(version 0.1.0), which is running" in r.stdout
    assert git(install, "rev-parse", "HEAD") == before  # the old code is on disk again
    assert (install / "deploy/install.sh").read_text() == INSTALL_OK  # and it is what was installed last
    assert "=== failed" in (tmp_path / "update.log").read_text()


def test_a_new_version_whose_core_is_not_healthy_is_rolled_back(github: Path, tmp_path: Path) -> None:
    install = installed(tmp_path, github)
    before = git(install, "rev-parse", "HEAD")
    release(github, "0.2.0", INSTALL_BREAKS_CORE)
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode != 0, r.stdout
    assert "dawn-core 0.2.0 did not come up healthy; going back to" in r.stdout and "(version 0.1.0), which is running" in r.stdout
    assert git(install, "rev-parse", "HEAD") == before
    assert '"version":"0.1.0"' in (tmp_path / "stubs/health").read_text()  # the old core was started again


def test_a_rollback_keeps_local_edits_the_update_did_not_touch(github: Path, tmp_path: Path) -> None:
    install = installed(tmp_path, github)
    (install / "first").write_text("edited on the Pi")
    release(github, "0.2.0", INSTALL_FAILS)
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert "rolled back to" in r.stdout, r.stdout
    assert (install / "first").read_text() == "edited on the Pi"


def test_a_failure_without_a_new_commit_has_nothing_to_roll_back(github: Path, tmp_path: Path) -> None:
    release(github, "0.1.0", INSTALL_FAILS)
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode != 0 and "the code is unchanged" in r.stdout and "going back" not in r.stdout, r.stdout


def test_dawn_update_moves_into_a_unit_of_its_own(github: Path, tmp_path: Path) -> None:
    """Started by dawn-core it is in dawn-core's control group, which the update's restart of dawn-core stops."""
    install = installed(tmp_path, github)
    (tmp_path / "systemd").mkdir()
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git", DAWN_UPDATE_DETACHED="", DAWN_SYSTEMD_DIR=str(tmp_path / "systemd"))
    assert r.returncode == 0 and "install.sh" not in r.stdout, r.stdout  # nothing ran here
    args = (tmp_path / "stubs/systemd-run.args").read_text()
    assert "--unit=dawn-update" in args and "--collect" in args and "DAWN_UPDATE_DETACHED=1" in args and args.rstrip().endswith("dawn-update main")
    assert not (tmp_path / "update.log").exists()


def test_dawn_update_fast_forwards_and_reinstalls(github: Path, tmp_path: Path) -> None:
    install = tmp_path / "opt-dawn"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "github.git"), str(install)], check=True)
    push_new_version(github, "0.2.0")
    r = run_dawn_update(install, tmp_path, tmp_path / "github.git")
    assert r.returncode == 0, r.stdout
    assert "(version 0.2.0)" in r.stdout and "install.sh --update --no-build" in r.stdout and "=== done" in r.stdout
    assert git(install, "rev-parse", "HEAD") == git(github, "rev-parse", "HEAD")
    assert "=== done" in (tmp_path / "update.log").read_text()


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
