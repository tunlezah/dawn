from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dawn_core.config import DawnConfig, load_config
from dawn_core.config.loader import ConfigManager, dump_config, write_config


def test_defaults_validate() -> None:
    c = DawnConfig()
    assert c.inputs.encoder.clk_pin == 17
    assert c.audio.sink_priority[0] == "usb"
    assert "9A" in c.dab.scan_priority


def test_example_file_roundtrips() -> None:
    p = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    c = DawnConfig.model_validate(yaml.safe_load(p.read_text()))
    assert c == DawnConfig()


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        DawnConfig.model_validate({"general": {"nope": 1}})


def test_duplicate_pins_rejected() -> None:
    with pytest.raises(ValidationError):
        DawnConfig.model_validate({"inputs": {"big_button": {"enabled": True, "pin": 17}}})


def test_curve_must_be_sorted() -> None:
    with pytest.raises(ValidationError):
        DawnConfig.model_validate({"display": {"brightness": {"curve": [{"lux": 10, "brightness": 5}, {"lux": 1, "brightness": 50}]}}})


def test_bad_timezone_rejected() -> None:
    with pytest.raises(ValidationError):
        DawnConfig.model_validate({"general": {"timezone": "Mars/Olympus"}})


def test_write_and_load(tmp_path: Path) -> None:
    c = DawnConfig()
    c.general.name = "Bedside"
    p = write_config(c, tmp_path / "c.yaml")
    assert load_config(p).general.name == "Bedside"
    assert "Bedside" in dump_config(c)


async def test_manager_update_and_listener(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    mgr = ConfigManager(p, poll_s=0.05)
    seen = []
    mgr.on_change(lambda old, new: seen.append((old.general.name, new.general.name)))
    await mgr.update({"general": {"name": "X"}})
    assert mgr.config.general.name == "X"
    assert seen == [("Dawn", "X")]
    # invalid patch leaves config untouched
    with pytest.raises(ValidationError):
        await mgr.update({"general": {"timezone": "Nope/Nope"}})
    assert mgr.config.general.name == "X"


async def test_manager_hot_reload(tmp_path: Path) -> None:
    import asyncio
    import os
    import time

    p = tmp_path / "c.yaml"
    write_config(DawnConfig(), p)
    mgr = ConfigManager(p, poll_s=0.05)
    await mgr.start()
    try:
        c = DawnConfig()
        c.general.name = "Reloaded"
        write_config(c, p)
        os.utime(p, (time.time() + 5, time.time() + 5))
        for _ in range(40):
            await asyncio.sleep(0.05)
            if mgr.config.general.name == "Reloaded":
                break
        assert mgr.config.general.name == "Reloaded"
    finally:
        await mgr.stop()


def test_a_file_with_one_bad_section_still_loads_the_rest(tmp_path: Path) -> None:
    """A hand edit must not keep dawn-core, and its alarms, from starting: the good sections are kept, the bad one
    falls back to its defaults, and the error is reported."""
    from dawn_core.config import load_config_lenient

    p = tmp_path / "c.yaml"
    p.write_text("general:\n  name: Bedside\n  timezone: Australia/Hobart\ndisplay:\n  rotation: 45\nnonsense: 1\naudio:\n  default_volume: 20\n")
    cfg, err = load_config_lenient(p)
    assert cfg.general.name == "Bedside" and cfg.general.timezone == "Australia/Hobart" and cfg.audio.default_volume == 20
    assert cfg.display.rotation == 0  # the defaults for the section that failed
    assert err and "display" in err and "nonsense" in err
    mgr = ConfigManager(p)  # does not raise
    assert mgr.config.general.name == "Bedside" and mgr.last_error == err
    p.write_text("{{{{ not yaml")
    cfg, err = load_config_lenient(p)
    assert cfg == DawnConfig() and err and "whole file" in err


async def test_a_rejected_reload_is_reported_until_the_file_is_right_again(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    write_config(DawnConfig(), p)
    mgr = ConfigManager(p, poll_s=0.05)
    seen: list[str | None] = []
    mgr.on_error(seen.append)
    p.write_text("general:\n  timezone: Nope/Nope\n")
    assert await mgr.reload() is False
    assert seen and seen[-1] and "Nope" in seen[-1]
    write_config(DawnConfig(), p)
    assert await mgr.reload() is True
    assert seen[-1] is None


def test_check_reports_an_invalid_file(tmp_path: Path, capsys) -> None:
    from dawn_core.__main__ import main

    p = tmp_path / "c.yaml"
    p.write_text("general:\n  timezone: Nope/Nope\n")
    assert main(["--config", str(p), "--check"]) == 1
    assert "INVALID" in capsys.readouterr().out
    write_config(DawnConfig(), p)
    assert main(["--config", str(p), "--check"]) == 0
