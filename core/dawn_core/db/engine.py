"""SQLite engine wrapper (sync SQLModel, called from the event loop via to_thread
only where it matters; the DB is tiny and local).

Opening never fails: an SD card that has gone read-only, or a database file that
cannot be opened, must not keep dawn-core (and with it every alarm) from
starting. The file is opened for writing; if that fails it is opened read-only
(the alarms are read, nothing can be saved); if even that fails an empty
in-memory database stands in, and `mode` says so for Diagnostics and the face.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from .models import KV, EventRow

log = logging.getLogger("dawn.db")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.write_errors = 0  # writes that failed (storage read-only or full); Diagnostics shows them
        self.last_write_error: str | None = None
        self._write_error_logged = 0.0
        self.mode = "rw"  # rw | ro (the file is read, nothing can be saved) | memory (nothing could be read)
        self.open_error: str | None = None  # why it is not rw
        if str(self.path) == ":memory:":
            self.engine = self._open("sqlite://", create=True)
            self.mode = "memory"
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.error("cannot create %s: %s", self.path.parent, e)
        try:
            self.engine = self._open(f"sqlite:///{self.path}", create=True)
            return
        except Exception as e:  # noqa: BLE001
            self.open_error = f"{type(e).__name__}: {e}"
            log.error("cannot open %s for writing (%s); trying read-only", self.path, self.open_error)
        try:
            # immutable: no locks and no -shm/-wal files, which a read-only filesystem cannot create
            self.engine = self._open(f"sqlite:///file:{self.path}?mode=ro&immutable=1&uri=true", create=False)
            self.mode = "ro"
            log.error("%s is read-only: alarms and settings are read from it, but nothing can be saved", self.path)
            return
        except Exception as e:  # noqa: BLE001
            self.open_error = f"{self.open_error}; read-only: {type(e).__name__}: {e}"
            log.critical("cannot read %s at all (%s); running with an empty in-memory database: no alarms are known",
                         self.path, self.open_error)
        self.engine = self._open("sqlite://", create=True)
        self.mode = "memory"

    def _open(self, url: str, *, create: bool) -> Engine:
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            for pragma in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA foreign_keys=ON"):
                try:
                    cur.execute(pragma)
                except Exception as e:  # noqa: BLE001  (a read-only file cannot switch journal modes)
                    log.debug("%s: %s", pragma, e)
            cur.close()

        if create:
            SQLModel.metadata.create_all(engine)
        else:
            with engine.connect() as conn:  # prove it can be read before relying on it
                conn.execute(text("SELECT name FROM sqlite_master LIMIT 1")).fetchall()
        return engine

    @property
    def degraded(self) -> bool:
        """Nothing could be read from the real database: the alarms are unknown."""
        return self.mode == "memory" and str(self.path) != ":memory:"

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as s:
            yield s

    # ---- kv --------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self.session() as s:
            row = s.get(KV, key)
            if row is None:
                return default
            try:
                return json.loads(row.value)
            except json.JSONDecodeError:
                return row.value

    def set(self, key: str, value: Any) -> None:
        with self.session() as s:
            row = s.get(KV, key)
            if row is None:
                row = KV(key=key, value=json.dumps(value))
                s.add(row)
            else:
                row.value = json.dumps(value)
                s.add(row)
            s.commit()

    def try_set(self, key: str, value: Any) -> bool:
        """`set` for state worth keeping that must not stop the caller (volume, a ring in progress): an SD card
        that has gone read-only or a full disk is logged, and False is returned."""
        try:
            self.set(key, value)
        except Exception as e:  # noqa: BLE001
            self.write_failed(key, e)
            return False
        return True

    def write_failed(self, what: str, e: BaseException) -> None:
        """Count a failed write and log it at most once a minute (the alarm engine retries every second)."""
        self.write_errors += 1
        self.last_write_error = f"{what}: {e}"
        now = time.monotonic()
        if now - self._write_error_logged > 60:
            self._write_error_logged = now
            log.error("database write failed (%s): %s", what, e)

    def all_kv(self) -> dict[str, Any]:
        with self.session() as s:
            out = {}
            for row in s.exec(select(KV)).all():
                try:
                    out[row.key] = json.loads(row.value)
                except json.JSONDecodeError:
                    out[row.key] = row.value
            return out

    # ---- events ----------------------------------------------------------
    def log_event(self, kind: str, **detail: Any) -> None:
        """Record an event. Never raises: the log is for reading later, and nothing that logs (an alarm firing
        above all) may fail because the storage has gone read-only or is full. The journal still gets it."""
        try:
            with self.session() as s:
                s.add(EventRow(kind=kind, detail=json.dumps(detail, default=str)))
                s.commit()
        except Exception as e:  # noqa: BLE001
            self.write_failed(f"event {kind}", e)
        log.info("event %s %s", kind, detail, extra={"dawn_kind": kind})

    def events_since(self, since: datetime, kinds: list[str] | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """Events at or after `since` (aware datetime), newest first, optionally of some kinds only."""
        with self.session() as s:
            q = select(EventRow).where(EventRow.at >= since.astimezone(UTC))
            if kinds:
                q = q.where(EventRow.kind.in_(kinds))  # type: ignore[attr-defined]
            rows = s.exec(q.order_by(EventRow.at.desc()).limit(limit)).all()  # type: ignore[attr-defined]
        return [self._event(r) for r in rows]

    @staticmethod
    def _event(r: EventRow) -> dict[str, Any]:
        try:
            d = json.loads(r.detail)
        except json.JSONDecodeError:
            d = {"detail": r.detail}
        at = r.at if r.at.tzinfo else r.at.replace(tzinfo=UTC)
        return {"id": r.id, "at": at.isoformat(), "kind": r.kind, **d}

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.exec(select(EventRow).order_by(EventRow.at.desc()).limit(limit)).all()  # type: ignore[attr-defined]
            out = []
            for r in rows:
                try:
                    d = json.loads(r.detail)
                except json.JSONDecodeError:
                    d = {"detail": r.detail}
                at = r.at if r.at.tzinfo else r.at.replace(tzinfo=UTC)
                out.append({"id": r.id, "at": at.isoformat(), "kind": r.kind, **d})
            return out
