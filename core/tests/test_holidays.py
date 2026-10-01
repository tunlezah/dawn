from __future__ import annotations

from datetime import date

from dawn_core.alarms.holidays import HolidayCalendar


def test_nsw_public_holidays_2026() -> None:
    cal = HolidayCalendar("NSW")
    assert cal.is_holiday(date(2026, 1, 26))  # Australia Day
    assert cal.is_holiday(date(2026, 10, 5))  # Labour Day
    assert cal.is_holiday(date(2026, 6, 8))  # King's Birthday
    assert not cal.is_holiday(date(2026, 10, 6))
    assert cal.name(date(2026, 4, 25)) == "ANZAC Day"


def test_regional_only_requires_scope() -> None:
    statewide = HolidayCalendar("QLD", "statewide")
    regional = HolidayCalendar("QLD", "include_regional")
    ekka = date(2026, 8, 12)  # Royal Queensland Show (Brisbane)
    assert not statewide.is_holiday(ekka)
    assert regional.is_holiday(ekka)
    assert any(r for _, _, r in regional.holidays_in(2026))
    assert statewide.is_holiday(date(2026, 5, 4))  # Labour Day QLD is statewide


def test_extra_and_exclusions() -> None:
    cal = HolidayCalendar("VIC", extra_dates=["2026-07-01"], exclude_names=["Grand Final"])
    assert cal.is_holiday(date(2026, 7, 1))
    assert not cal.is_holiday(date(2026, 9, 25))  # Friday before the AFL Grand Final excluded
    assert cal.is_holiday(date(2026, 11, 3))  # Melbourne Cup


def test_year_boundary_and_leap() -> None:
    cal = HolidayCalendar("ACT")
    assert cal.is_holiday(date(2028, 1, 26))
    assert not cal.is_holiday(date(2028, 2, 29))
