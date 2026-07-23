"""Date helpers — inclusive, non-overlapping window tiling for time-series pulls."""

from __future__ import annotations

from datetime import date, timedelta


def split_date_range(
    date_range_start: str, date_range_end: str, days: int = 7
) -> list[tuple[str, str]]:
    """Split an inclusive [start, end] range into consecutive non-overlapping windows.

    Pinterest analytics ranges are inclusive on both ends, so reusing an end date
    as the next window's start double-counts that day. These windows tile the
    range cleanly: summing a metric across them equals the whole-range value.

    Returns a list of (start_iso, end_iso) tuples. Dates are "YYYY-MM-DD".
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    start = date.fromisoformat(date_range_start)
    end = date.fromisoformat(date_range_end)
    if end < start:
        raise ValueError("date_range_end must be on or after date_range_start")

    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=days - 1), end)
        windows.append((cursor.isoformat(), window_end.isoformat()))
        cursor = window_end + timedelta(days=1)
    return windows
