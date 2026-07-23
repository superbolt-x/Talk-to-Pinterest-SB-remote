"""Tests for inclusive, non-overlapping date-window tiling."""

import pytest

from pinloop.dates import split_date_range


def test_seven_day_windows_tile_cleanly():
    windows = split_date_range("2026-01-01", "2026-01-21", days=7)
    assert windows == [
        ("2026-01-01", "2026-01-07"),
        ("2026-01-08", "2026-01-14"),
        ("2026-01-15", "2026-01-21"),
    ]


def test_windows_are_non_overlapping():
    windows = split_date_range("2026-01-01", "2026-01-31", days=7)
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert next_start > prev_end  # no shared boundary day -> no double counting


def test_partial_final_window():
    windows = split_date_range("2026-01-01", "2026-01-10", days=7)
    assert windows[-1] == ("2026-01-08", "2026-01-10")


def test_single_day():
    assert split_date_range("2026-01-01", "2026-01-01", days=7) == [
        ("2026-01-01", "2026-01-01")
    ]


def test_end_before_start_raises():
    with pytest.raises(ValueError):
        split_date_range("2026-01-10", "2026-01-01")


def test_days_must_be_positive():
    with pytest.raises(ValueError):
        split_date_range("2026-01-01", "2026-01-10", days=0)
