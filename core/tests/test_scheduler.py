from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dawn_core.alarms.scheduler import AlarmSpec, due_occurrence, next_occurrence, occurrence_on

SYD = ZoneInfo("Australia/Sydney")


def at(y: int, m: int, d: int, hh: int, mm: int = 0, tz: ZoneInfo = SYD) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=tz)


def test_weekday_alarm_skips_weekend() -> None:
    spec = AlarmSpec(time="06:30", repeat="weekdays")
    # Friday 2026-10-02 07:00 -> Monday 2026-10-05 06:30 (Sat/Sun skipped; Sunday is DST change day)
    nxt = next_occurrence(spec, at(2026, 10, 2, 7, 0), SYD)
    assert nxt == at(2026, 10, 5, 6, 30)
    assert nxt.utcoffset() == timedelta(hours=11)  # AEDT after the 4 Oct transition


def test_once_alarm_today_or_tomorrow() -> None:
    spec = AlarmSpec(time="22:00", repeat="once")
    assert next_occurrence(spec, at(2026, 3, 1, 21, 0), SYD) == at(2026, 3, 1, 22, 0)
    assert next_occurrence(spec, at(2026, 3, 1, 22, 30), SYD) == at(2026, 3, 2, 22, 0)


def test_custom_days() -> None:
    spec = AlarmSpec(time="08:00", repeat="custom", days=[1, 3])  # Tue, Thu
    assert next_occurrence(spec, at(2026, 10, 5, 9, 0), SYD) == at(2026, 10, 6, 8, 0)  # Mon -> Tue
    assert next_occurrence(spec, at(2026, 10, 6, 9, 0), SYD) == at(2026, 10, 8, 8, 0)  # Tue -> Thu


def test_disabled_or_no_days() -> None:
    assert next_occurrence(AlarmSpec(time="08:00", enabled=False), at(2026, 1, 1, 0), SYD) is None
    assert next_occurrence(AlarmSpec(time="08:00", repeat="custom", days=[]), at(2026, 1, 1, 0), SYD) is None


def test_spring_forward_gap_rings_once() -> None:
    # Sydney: 2026-10-04 02:00 AEST -> 03:00 AEDT. 02:30 does not exist.
    spec = AlarmSpec(time="02:30", repeat="daily")
    occ = next_occurrence(spec, at(2026, 10, 3, 12, 0), SYD)
    assert occ.date() == date(2026, 10, 4)
    assert occ.hour == 3 and occ.minute == 30 and occ.utcoffset() == timedelta(hours=11)
    following = next_occurrence(spec, occ, SYD)
    assert following == at(2026, 10, 5, 2, 30)
    assert following.timestamp() - occ.timestamp() == 23 * 3600  # exactly one ring on transition day


def test_fall_back_ambiguous_rings_once() -> None:
    # Sydney: 2027-04-04 03:00 AEDT -> 02:00 AEST. 02:30 happens twice.
    spec = AlarmSpec(time="02:30", repeat="daily")
    occ = next_occurrence(spec, at(2027, 4, 3, 12, 0), SYD)
    assert occ.date() == date(2027, 4, 4) and occ.fold == 0
    assert occ.utcoffset() == timedelta(hours=11)  # first (AEDT) occurrence
    following = next_occurrence(spec, occ, SYD)
    assert following.date() == date(2027, 4, 5)
    assert following.timestamp() - occ.timestamp() == 25 * 3600


def test_normal_alarm_across_dst_keeps_wall_clock() -> None:
    spec = AlarmSpec(time="06:30", repeat="daily")
    a = next_occurrence(spec, at(2026, 10, 3, 7, 0), SYD)  # Sun 4 Oct 06:30 AEDT
    b = next_occurrence(spec, a, SYD)
    assert a.hour == 6 and b.hour == 6
    assert a.timestamp() - at(2026, 10, 3, 6, 30).timestamp() == 23 * 3600


def test_leap_day() -> None:
    spec = AlarmSpec(time="07:00", repeat="daily")
    assert next_occurrence(spec, at(2028, 2, 28, 8, 0), SYD) == at(2028, 2, 29, 7, 0)
    assert next_occurrence(spec, at(2027, 2, 28, 8, 0), SYD) == at(2027, 3, 1, 7, 0)


def test_skip_next_skips_exactly_one() -> None:
    spec = AlarmSpec(time="06:30", repeat="weekdays", skip_next=True)
    nxt = next_occurrence(spec, at(2026, 10, 5, 7, 0), SYD)  # Mon -> would be Tue, skipped -> Wed
    assert nxt == at(2026, 10, 7, 6, 30)


def test_holidays_skipped_for_repeating_only() -> None:
    hol = {date(2026, 10, 5)}  # Labour Day NSW (Monday)
    spec = AlarmSpec(time="06:30", repeat="weekdays", skip_public_holidays=True)
    nxt = next_occurrence(spec, at(2026, 10, 2, 7, 0), SYD, is_holiday=lambda d: d in hol)
    assert nxt == at(2026, 10, 6, 6, 30)
    once = AlarmSpec(time="06:30", repeat="once", skip_public_holidays=True)
    assert next_occurrence(once, at(2026, 10, 4, 7, 0), SYD, is_holiday=lambda d: d in hol) == at(2026, 10, 5, 6, 30)


def test_leave_until_suppresses_repeating() -> None:
    spec = AlarmSpec(time="06:30", repeat="daily")
    nxt = next_occurrence(spec, at(2026, 10, 1, 7, 0), SYD, leave_until=date(2026, 10, 10))
    assert nxt == at(2026, 10, 11, 6, 30)


def test_due_occurrence_and_last_fired() -> None:
    spec = AlarmSpec(time="06:30", repeat="daily")
    now = at(2026, 10, 2, 6, 30).replace(second=5)  # 5 s after
    due = due_occurrence(spec, now, SYD)
    assert due == at(2026, 10, 2, 6, 30)
    spec.last_fired_occurrence = due.isoformat()
    assert due_occurrence(spec, now, SYD) is None
    assert due_occurrence(spec, at(2026, 10, 2, 6, 29), SYD) is None


def test_occurrence_on_regular_day() -> None:
    occ = occurrence_on(date(2026, 7, 1), datetime(2000, 1, 1, 6, 30).time(), SYD)
    assert occ == at(2026, 7, 1, 6, 30) and occ.utcoffset() == timedelta(hours=10)
