"""Market hours utilities for NSE/BSE Indian equity markets."""

from datetime import date, datetime, time, timedelta

import pytz

_IST = pytz.timezone("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)

# NSE/BSE public holidays 2024-2025 (YYYY-MM-DD)
_NSE_HOLIDAYS: set[str] = {
    # 2024
    "2024-01-22", "2024-01-26", "2024-03-08", "2024-03-25",
    "2024-03-29", "2024-04-14", "2024-04-17", "2024-04-21",
    "2024-05-23", "2024-06-17", "2024-07-17", "2024-08-15",
    "2024-10-02", "2024-11-01", "2024-11-15", "2024-11-20",
    "2024-12-25",
    # 2025
    "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10",
    "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15",
    "2025-08-27", "2025-10-02", "2025-10-02", "2025-10-20",
    "2025-10-21", "2025-11-05", "2025-12-25",
}


def get_current_ist_time() -> datetime:
    """Return the current date and time in IST."""
    return datetime.now(_IST)


def is_trading_day(dt: datetime | None = None) -> bool:
    """Return True if *dt* is a trading day (not a weekend or NSE holiday).

    Args:
        dt: Date to check; defaults to today in IST.
    """
    if dt is None:
        dt = get_current_ist_time()
    # Weekend check (Monday=0, Sunday=6)
    if dt.weekday() >= 5:
        return False
    date_str = dt.strftime("%Y-%m-%d")
    return date_str not in _NSE_HOLIDAYS


def last_completed_session(dt: datetime | None = None) -> date:
    """The most recent trading day whose close has already passed.

    Used to tell whether a fetched frame is missing its newest bar. Today
    only counts once the market has closed — during the session the day's
    candle is still forming, so a frame that ends yesterday is correct, not
    stale, and must not trigger a backfill.

    Walks back over weekends and NSE holidays. Bounded at 10 days so a gap in
    the holiday table can never spin.
    """
    if dt is None:
        dt = get_current_ist_time()

    cursor = dt
    if not (is_trading_day(cursor)
            and cursor.time().replace(tzinfo=None) > _MARKET_CLOSE):
        cursor = cursor - timedelta(days=1)

    for _ in range(10):
        if is_trading_day(cursor):
            return cursor.date()
        cursor = cursor - timedelta(days=1)
    return cursor.date()


def recent_trading_sessions(count: int, end: date | None = None) -> list[date]:
    """The last *count* trading days up to and including *end*.

    Gives the set of sessions a daily frame is expected to contain, so a
    missing one can be identified by date rather than inferred from the
    length of the frame.

    Args:
        count: How many sessions to return.
        end: Last session to include; defaults to
            :func:`last_completed_session`.

    Returns:
        Dates in ascending order.
    """
    if end is None:
        end = last_completed_session()
    out: list[date] = []
    cursor = datetime.combine(end, time(12, 0))
    # Bounded well above `count` so a run of holidays cannot exhaust it.
    for _ in range(count * 4 + 20):
        if len(out) >= count:
            break
        if is_trading_day(cursor):
            out.append(cursor.date())
        cursor -= timedelta(days=1)
    return sorted(out)


def is_market_open(dt: datetime | None = None) -> bool:
    """Return True if the NSE/BSE market is currently open.

    Args:
        dt: Moment to check; defaults to now in IST.
    """
    if dt is None:
        dt = get_current_ist_time()
    if not is_trading_day(dt):
        return False
    current_time = dt.time().replace(tzinfo=None)
    return _MARKET_OPEN <= current_time <= _MARKET_CLOSE


def get_market_countdown(dt: datetime | None = None) -> str:
    """Return a human-readable countdown to market open or close.

    Args:
        dt: Reference moment; defaults to now in IST.

    Returns:
        Strings like "Opens in 2h 15m", "Closes in 45m",
        "Market opens tomorrow", or "Open on Monday".
    """
    if dt is None:
        dt = get_current_ist_time()

    now_time = dt.time().replace(tzinfo=None)

    if is_market_open(dt):
        # Time to close
        close_dt = dt.replace(
            hour=_MARKET_CLOSE.hour,
            minute=_MARKET_CLOSE.minute,
            second=0,
            microsecond=0,
        )
        delta = close_dt - dt
        return f"Closes in {_fmt_delta(delta)}"

    if is_trading_day(dt) and now_time < _MARKET_OPEN:
        # Before market open today
        open_dt = dt.replace(
            hour=_MARKET_OPEN.hour,
            minute=_MARKET_OPEN.minute,
            second=0,
            microsecond=0,
        )
        delta = open_dt - dt
        return f"Opens in {_fmt_delta(delta)}"

    # Find the next trading day
    next_day = dt + timedelta(days=1)
    for _ in range(7):
        if is_trading_day(next_day):
            day_name = _day_name(next_day, dt)
            return f"Opens {day_name} at 9:15 AM"
        next_day += timedelta(days=1)

    return "Market schedule unavailable"


def _fmt_delta(delta: timedelta) -> str:
    """Format a timedelta as 'Xh Ym' or 'Ym'."""
    total_minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _day_name(target: datetime, reference: datetime) -> str:
    """Return 'today', 'tomorrow', or the weekday name relative to *reference*."""
    diff = (target.date() - reference.date()).days
    if diff == 0:
        return "today"
    if diff == 1:
        return "tomorrow"
    return target.strftime("%A")
