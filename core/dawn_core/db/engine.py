"""SQLite engine wrapper (sync SQLModel, called from the event loop via to_thread
only where it matters; the DB is tiny and local)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from .models import KV, EventRow

log = logging.getLogger("dawn.db")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{self.path}"
        else:
            url = "sqlite://"
        self.engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        SQLModel.metadata.create_all(self.engine)

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
        with self.session() as s:
            s.add(EventRow(kind=kind, detail=json.dumps(detail, default=str)))
            s.commit()
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
