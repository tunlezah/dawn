"""`python -m dawn_core` / `dawn-core` entry point."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from .config import ConfigManager, config_path
from .logging_setup import setup_logging


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dawn-core", description="Dawn core service")
    ap.add_argument("--config", help="config.yaml path (default: $DAWN_CONFIG or /etc/dawn/config.yaml)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--sim", action="store_true", help="run with simulated hardware (same as DAWN_SIM=1)")
    ap.add_argument("--check", action="store_true", help="validate the config and exit")
    args = ap.parse_args(argv)
    if args.config:
        os.environ["DAWN_CONFIG"] = args.config
    if args.sim:
        os.environ["DAWN_SIM"] = "1"
    mgr = ConfigManager(config_path())
    setup_logging(mgr.config.general.log_level)
    if args.check:
        if mgr.last_error:
            print(f"INVALID: {mgr.path}: {mgr.last_error}")
            return 1
        print(f"OK: {mgr.path}")
        return 0
    from .app import create_app

    app = create_app(mgr)
    uvicorn.run(
        app,
        host=args.host or mgr.config.web.host,
        port=args.port or mgr.config.web.port,
        log_level="warning",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
