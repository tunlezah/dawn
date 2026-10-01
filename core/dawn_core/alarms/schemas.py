from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Repeat = Literal["once", "weekdays", "weekends", "daily", "custom"]


class AlarmIn(BaseModel):
    label: str = "Alarm"
    enabled: bool = True
    time: str = "06:30"
    repeat: Repeat = "weekdays"
    days: list[int] = Field(default_factory=list)
    skip_public_holidays: bool = False
    holiday_region: str | None = None
    holiday_scope: Literal["statewide", "include_regional"] | None = None
    source: str = "chime:gentle_bell"
    volume: int = Field(70, ge=1, le=100)
    ramp_seconds: int = Field(60, ge=0, le=600)
    snooze_minutes: int = Field(9, ge=1, le=60)
    fallback_after_s: int = Field(15, ge=3, le=120)
    max_ring_minutes: int = Field(30, ge=1, le=180)
    skip_next: bool = False
    light_wake: bool = False
    light_wake_minutes: int = Field(10, ge=1, le=60)

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("time must be HH:MM")
        return f"{int(h):02d}:{int(m):02d}"

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be 0..6 (Mon..Sun)")
        return sorted(set(v))


class AlarmOut(AlarmIn):
    id: int
    next_at: str | None = None
    last_fired_occurrence: str | None = None


class TestRingIn(BaseModel):
    source: str = "chime:gentle_bell"
    label: str = "Test"
    volume: int = Field(60, ge=1, le=100)
    ramp_seconds: int = Field(0, ge=0, le=600)


class LeaveIn(BaseModel):
    until: str | None = None  # YYYY-MM-DD


class TimerIn(BaseModel):
    minutes: int = Field(ge=1, le=600)
