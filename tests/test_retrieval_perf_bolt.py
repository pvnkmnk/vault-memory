from datetime import datetime, timezone
from daemon.retrieval import extract_time_range


def test_extract_time_range_last_week():
    query = "notes from last week about project alpha"
    res = extract_time_range(query, {})
    assert res is not None
    assert "start" in res and "end" in res


def test_extract_time_range_last_month():
    query = "documents created last month"
    res = extract_time_range(query, {})
    assert res is not None
    assert "start" in res and "end" in res


def test_extract_time_range_this_year():
    now = datetime.now(timezone.utc)
    query = "meeting summary from this year"
    res = extract_time_range(query, {})
    assert res == {"start": f"{now.year}-01-01", "end": now.strftime("%Y-%m-%d")}


def test_extract_time_range_in_year():
    query = "reports published in 2024"
    res = extract_time_range(query, {})
    assert res == {"start": "2024-01-01", "end": "2024-12-31"}


def test_extract_time_range_date_span():
    query = "logs between 2024-01-01 .. 2024-06-30"
    res = extract_time_range(query, {})
    assert res == {"start": "2024-01-01", "end": "2024-06-30"}


def test_extract_time_range_none():
    query = "general search query without temporal keywords"
    res = extract_time_range(query, {})
    assert res is None


def test_extract_time_range_context_override():
    context = {"time_range": {"start": "2023-01-01", "end": "2023-12-31"}}
    query = "notes from last week"
    res = extract_time_range(query, context)
    assert res == context["time_range"]
