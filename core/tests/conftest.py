from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["DAWN_SIM"] = "1"


@pytest.fixture()
def tmp_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(
        "general:\n  timezone: Australia/Sydney\n  data_dir: %s\n  runtime_dir: %s\n"
        "audio:\n  backend: sim\ndisplay:\n  backlight:\n    driver: sim\n  lux_sensor:\n    driver: sim\n"
        "  sleep:\n    enabled: false\n"  # on by default; off here so face-mode tests do not depend on the hour
        "inputs:\n  pin_factory: mock\ntime_sources:\n  gps:\n    source: sim\nsystem:\n  watchdog: false\n"
        # nothing here may reach a simulator left running by `make sim` (its long polls outlive a test's event loop)
        "sim:\n  hub_url: http://127.0.0.1:9\ndab:\n  welle_url: http://127.0.0.1:9\n"
        % (tmp_path / "data", tmp_path / "run")
    )
    os.environ["DAWN_DATA_DIR"] = str(tmp_path / "data")
    os.environ["DAWN_RUNTIME_DIR"] = str(tmp_path / "run")
    return p
