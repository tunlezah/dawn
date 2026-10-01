#!/usr/bin/env python3
"""Generate config/config.example.yaml from the pydantic schema, with field
descriptions as comments, so the example never drifts from the code."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from dawn_core.config.schema import DawnConfig  # noqa: E402


def emit(model: Any, indent: int, out: list[str]) -> None:
    fields = type(model).model_fields
    for name, f in fields.items():
        val = getattr(model, name)
        pad = "  " * indent
        desc = f.description
        if hasattr(type(val), "model_fields") and not isinstance(val, (str, int, float, bool, list, type(None))):
            if desc:
                out.append(f"{pad}# {desc}")
            out.append(f"{pad}{name}:")
            emit(val, indent + 1, out)
        else:
            if desc:
                out.append(f"{pad}# {desc}")
            dumped = yaml.safe_dump({name: _plain(val)}, sort_keys=False, default_flow_style=False, width=100).rstrip()
            for line in dumped.splitlines():
                out.append(f"{pad}{line}")
    if indent == 0:
        out.append("")


def _plain(v: Any) -> Any:
    if hasattr(type(v), "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def main() -> None:
    cfg = DawnConfig()
    out = [
        "# Dawn configuration (/etc/dawn/config.yaml)",
        "# Generated from dawn_core.config.schema; every value shown is the default.",
        "# The file is validated on load and hot-reloaded when it changes.",
        "",
    ]
    for name in DawnConfig.model_fields:
        val = getattr(cfg, name)
        if hasattr(type(val), "model_fields") and not isinstance(val, (str, int, float, bool, list, type(None))):
            out.append(f"{name}:")
            emit(val, 1, out)
            out.append("")
        else:
            out.append(f"{name}: {val}")
            out.append("")
    target = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"
    target.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    # round-trip check
    DawnConfig.model_validate(yaml.safe_load(target.read_text()))
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
