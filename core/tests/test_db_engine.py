"""Opening the database never stops dawn-core: a file that cannot be written is read, one that cannot be read is
replaced by an empty one in memory, and both are said so."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dawn_core.db import Database


def test_a_database_that_cannot_be_written_is_opened_read_only(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "dawn.db"
    good = Database(p)
    good.set("audio.volume", 42)
    good.log_event("x")
    good.engine.dispose()
    real_open = Database._open

    def ro_only(self, url, *, create):  # the card has gone read-only: the writable open fails
        if create and "memory" not in url and "mode=ro" not in url and not url.startswith("sqlite://" + "?"):
            raise sqlite3.OperationalError("unable to open database file")
        return real_open(self, url, create=create)

    monkeypatch.setattr(Database, "_open", ro_only)
    db = Database(p)
    assert db.mode == "ro" and not db.degraded and "unable to open" in (db.open_error or "")
    assert db.get("audio.volume") == 42  # read from the file
    assert db.try_set("audio.volume", 50) is False and db.write_errors == 1  # nothing can be saved
    db.log_event("y")  # never raises
    assert db.write_errors == 2


def test_a_database_that_cannot_be_read_gives_an_empty_one_in_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "data"
    blocker.write_text("not a directory")  # the data directory cannot exist
    db = Database(blocker / "dawn.db")
    assert db.mode == "memory" and db.degraded and db.open_error
    assert db.get("audio.volume", 35) == 35
    db.set("audio.volume", 40)  # works, in memory
    assert db.get("audio.volume") == 40 and db.write_errors == 0


def test_a_corrupt_database_file_gives_an_empty_one_in_memory(tmp_path: Path) -> None:
    p = tmp_path / "dawn.db"
    p.write_bytes(b"this is not a database at all" * 100)
    db = Database(p)
    assert db.mode == "memory" and db.degraded and "not a database" in (db.open_error or "")


def test_an_in_memory_database_by_choice_is_not_degraded() -> None:
    db = Database(":memory:")
    assert db.mode == "memory" and not db.degraded and db.open_error is None
