"""Logging to journald with structured fields when available, else stdout."""

from __future__ import annotations

import json
import logging
import sys
import time


class _StdoutFormatter(logging.Formatter):
    """One JSON object per line so journald/less can grep structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k.startswith("dawn_"):
                base[k[5:]] = v
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = " ".join(f"{k[5:]}={v}" for k, v in record.__dict__.items() if k.startswith("dawn_"))
        s = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if extra:
            s += f"  [{extra}]"
        if record.exc_info:
            s += "\n" + self.formatException(record.exc_info)
        return s


def setup_logging(level: str = "INFO", human: bool | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    try:
        from systemd.journal import JournalHandler  # type: ignore

        h: logging.Handler = JournalHandler(SYSLOG_IDENTIFIER="dawn-core")
        h.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root.addHandler(h)
    except Exception:  # noqa: BLE001
        h = logging.StreamHandler(sys.stdout)
        if human is None:
            human = sys.stdout.isatty()
        h.setFormatter(_HumanFormatter() if human else _StdoutFormatter())
        root.addHandler(h)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
