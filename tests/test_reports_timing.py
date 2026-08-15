"""Timing filters for the Reports page."""

from datetime import date, timedelta

from ui.pages.reports_page import _matches_timing


def _row(*, scheduled: int | None = None, reported: int | None = None) -> dict:
    """Build an earnings row relative to today for concise filter tests."""
    today = date.today()
    return {
        "result_date": (
            (today + timedelta(days=scheduled)).isoformat()
            if scheduled is not None else None
        ),
        "last_result_date": (
            (today + timedelta(days=reported)).isoformat()
            if reported is not None else None
        ),
    }


def test_same_day_report_is_not_still_due_today() -> None:
    row = _row(scheduled=0, reported=0)

    assert not _matches_timing(row, "Due Today")
    assert not _matches_timing(row, "Yet to release")
    assert _matches_timing(row, "Released Today")


def test_upcoming_windows_are_precise() -> None:
    tomorrow = _row(scheduled=1, reported=-90)
    next_week = _row(scheduled=7, reported=-90)
    later = _row(scheduled=8, reported=-90)

    assert _matches_timing(tomorrow, "Tomorrow")
    assert _matches_timing(tomorrow, "Next 7 Days")
    assert _matches_timing(next_week, "Next 7 Days")
    assert not _matches_timing(later, "Next 7 Days")
    assert _matches_timing(later, "Later")


def test_recent_release_windows_use_the_reported_date() -> None:
    yesterday = _row(scheduled=60, reported=-1)
    week_old = _row(scheduled=60, reported=-6)
    older = _row(scheduled=60, reported=-7)

    assert _matches_timing(yesterday, "Released Yesterday")
    assert _matches_timing(yesterday, "Released: Last 7 Days")
    assert _matches_timing(week_old, "Released: Last 7 Days")
    assert not _matches_timing(older, "Released: Last 7 Days")
    assert _matches_timing(older, "All Released")
