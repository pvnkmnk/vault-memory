import pytest
from daemon.retrieval import extract_time_range


def test_extract_time_range_relative():
    context = {}

    # Test last week
    res_week = extract_time_range("find notes from last week", context)
    assert res_week is not None
    assert "start" in res_week and "end" in res_week

    # Test last month
    res_month = extract_time_range("what changed last month?", context)
    assert res_month is not None
    assert "start" in res_month and "end" in res_month

    # Test this year
    res_year = extract_time_range("projects created this year", context)
    assert res_year is not None
    assert "start" in res_year and "end" in res_year

    # Test specific year
    res_in_year = extract_time_range("documents in 2023", context)
    assert res_in_year == {"start": "2023-01-01", "end": "2023-12-31"}

    # Test explicit date range
    res_range = extract_time_range("events between 2023-01-15..2023-06-30", context)
    assert res_range == {"start": "2023-01-15", "end": "2023-06-30"}

    # Test no time match
    res_none = extract_time_range("python architecture design", context)
    assert res_none is None


def test_extract_time_range_context_override():
    context = {"time_range": {"start": "2022-01-01", "end": "2022-12-31"}}
    res = extract_time_range("notes from last week", context)
    assert res == {"start": "2022-01-01", "end": "2022-12-31"}
