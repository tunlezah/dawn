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
