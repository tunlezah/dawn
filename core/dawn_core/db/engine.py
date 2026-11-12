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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlmodel import Session, SQLModel, create_engine, delete, select

from .models import KV, EventRow

log = logging.getLogger("dawn.db")

# The schema's version, kept in SQLite's `PRAGMA user_version`. Raise it, and add a step to MIGRATIONS, for a change
# that adding columns cannot express (new columns need neither: see Database._migrate). Changes must stay
# *additive*: a failed update is rolled back to the previous code (deploy/bin/dawn-update), which then runs on the
# database the newer code has already migrated, so nothing an older version reads or writes may be renamed, removed
# or given a new meaning.
SCHEMA_VERSION = 1
MIGRATIONS: dict[int, list[str]] = {}  # {version reached: [SQL statements]}, applied in order above the stored version


def _sql_literal(v: Any) -> str | None:
    """A constant as SQLite DDL takes it for DEFAULT, or None when there is none to give."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return None


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.write_errors = 0  # writes that failed (storage read-only or full); Diagnostics shows them
        self.last_write_error: str | None = None
        self._write_error_logged = 0.0
        self.mode = "rw"  # rw | ro (the file is read, nothing can be saved) | memory (nothing could be read)
        self.open_error: str | None = None  # why it is not rw
        # read-only, with a write-ahead log beside the file that this open cannot read: what was read is as of the last
        # checkpoint, so alarms changed since then are missing and the face's own memory of them is the better one
        self.stale = False
        self.migrated: list[str] = []  # what opening changed in the schema ("alarm.light_wake"), for the log and tests
        self.migration_error: str | None = None  # the schema could not be brought up to date (Diagnostics)
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
            wal = self.path.with_name(self.path.name + "-wal")
            try:
                self.stale = wal.stat().st_size > 0
            except OSError:
                self.stale = False
            if self.stale:
                self.open_error = f"{self.open_error}; {wal.name} is present and an immutable open does not read it: changes since the last checkpoint are missing"
                log.critical("%s has a write-ahead log this read-only open cannot read: alarms changed since the last checkpoint are missing", self.path)
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
            self._migrate(engine)
        else:
            with engine.connect() as conn:  # prove it can be read before relying on it
                conn.execute(text("SELECT name FROM sqlite_master LIMIT 1")).fetchall()
        return engine

    def _migrate(self, engine: Engine) -> None:
        """Bring a database made by an older version up to this one. `create_all` makes missing tables but never
        touches an existing one, so a column added to a model since the file was made is simply not there, and
        every read of that table fails: for the alarm table that is every alarm, on every tick, from the first
        start after the update. Missing columns are added here with their defaults (existing rows get them), then
        the numbered steps in MIGRATIONS run. Never raises: a database that cannot be altered (read-only card) is
        left as it is, and the reads that then fail are handled where they are made."""
        try:
            with engine.begin() as conn:
                have = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
                self._add_missing_columns(conn)
                for version in sorted(v for v in MIGRATIONS if v > have):
                    for stmt in MIGRATIONS[version]:
                        conn.execute(text(stmt))
                    self.migrated.append(f"schema {version}")
                if have > SCHEMA_VERSION:
                    log.warning("%s was last written by a newer version (schema %d, this is %d); used as it is", self.path, have, SCHEMA_VERSION)
                elif have != SCHEMA_VERSION:
                    conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
            if self.migrated:
                log.warning("database brought up to date: %s", ", ".join(self.migrated))
        except Exception as e:  # noqa: BLE001
            self.migration_error = f"{type(e).__name__}: {e}"
            log.critical("cannot bring %s up to date (%s): tables changed since it was made may not be readable", self.path, self.migration_error)

    def _add_missing_columns(self, conn: Connection) -> None:
        insp = inspect(conn)
        for table in SQLModel.metadata.sorted_tables:
            present = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in present:
                    continue
                default = getattr(col.default, "arg", None) if col.default is not None else None
                value = default(None) if callable(default) else default  # a default_factory (created_at): its value now
                literal = _sql_literal(value)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=conn.dialect)}'
                if literal is not None:
                    # in the table itself, so rows written by an older version (after a rolled-back update) get it too
                    ddl += f" DEFAULT {literal}" + ("" if col.nullable else " NOT NULL")
                conn.execute(text(ddl))
                if literal is None and value is not None:
                    conn.execute(table.update().where(col.is_(None)).values({col.name: value}))
                self.migrated.append(f"{table.name}.{col.name}")
            for index in table.indexes:  # an index on a column that was just added (create_all skips existing tables)
                index.create(conn, checkfirst=True)

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

    def prune_events(self, days: int) -> int:
        """Delete events older than `days` (the table grew for ever otherwise). Never raises: a read-only
        database simply keeps them."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        try:
            with self.session() as s:
                n = s.exec(delete(EventRow).where(EventRow.at < cutoff)).rowcount  # type: ignore[arg-type,attr-defined]
                s.commit()
        except Exception as e:  # noqa: BLE001
            self.write_failed("prune events", e)
            return 0
        return int(n or 0)

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
