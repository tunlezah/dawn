"""SQLModel tables. Runtime state lives here; configuration lives in config.yaml."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class AlarmRow(SQLModel, table=True):
    __tablename__ = "alarm"

    id: int | None = Field(default=None, primary_key=True)
    label: str = "Alarm"
    enabled: bool = True
    time: str = "06:30"  # HH:MM local
    repeat: str = "weekdays"  # once|weekdays|weekends|daily|custom
    days: str = ""  # custom: comma separated 0..6 (Mon..Sun)
    skip_public_holidays: bool = False
    holiday_region: str | None = None  # null = settings default
    holiday_scope: str | None = None  # statewide|include_regional|null
    source: str = "chime:gentle_bell"  # chime:<name> | dab:<sid> | url:<url> | playlist:<name> | last-played
    volume: int = 70
    ramp_seconds: int = 60
    snooze_minutes: int = 9
    fallback_after_s: int = 15
    max_ring_minutes: int = 30
    skip_next: bool = False
    light_wake: bool = False
    light_wake_minutes: int = 10
    last_fired_occurrence: str | None = None  # ISO local datetime of the last occurrence handled
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PresetRow(SQLModel, table=True):
    __tablename__ = "preset"

    id: int | None = Field(default=None, primary_key=True)
    label: str
    source: str
    position: int = 0


class KV(SQLModel, table=True):
    """Small key/value store for volatile runtime state (volume, last source...)."""

    __tablename__ = "kv"

    key: str = Field(primary_key=True)
    value: str = ""


class DabEnsembleRow(SQLModel, table=True):
    __tablename__ = "dab_ensemble"

    id: int | None = Field(default=None, primary_key=True)
    channel: str = Field(index=True)
    eid: str = ""
    label: str = ""
    snr: float | None = None
    scanned_at: datetime = Field(default_factory=utcnow)


class DabServiceRow(SQLModel, table=True):
    __tablename__ = "dab_service"

    id: int | None = Field(default=None, primary_key=True)
    sid: str = Field(index=True)
    channel: str = Field(index=True)
    ensemble_eid: str = ""
    ensemble_label: str = ""
    label: str = ""
    short_label: str = ""
    bitrate: int | None = None
    codec: str | None = None
    pty: str | None = None
    signal: int | None = None
    has_slide: bool = False
    scanned_at: datetime = Field(default_factory=utcnow)


class EventRow(SQLModel, table=True):
    """Alarm/system event log (fired, missed, stopped, snoozed, scan...)."""

    __tablename__ = "event"

    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    kind: str = Field(index=True)
    detail: str = ""
