from daemon.retrieval import extract_entities, extract_time_range


def test_extract_entities_dedupes_in_first_seen_order():
    assert extract_entities("Alpha beta alpha Beta gamma which") == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_extract_time_range_preset_context():
    context = {"time_range": {"start": "2023-01-01", "end": "2023-12-31"}}
    assert extract_time_range("any query", context) == {"start": "2023-01-01", "end": "2023-12-31"}


def test_extract_time_range_patterns():
    # Last week
    tr_week = extract_time_range("notes from last week", {})
    assert tr_week is not None and "start" in tr_week and "end" in tr_week

    # Last month
    tr_month = extract_time_range("notes from last month", {})
    assert tr_month is not None and "start" in tr_month and "end" in tr_month

    # This year
    tr_year = extract_time_range("projects this year", {})
    assert tr_year is not None and "start" in tr_year and "end" in tr_year

    # In year
    tr_in_year = extract_time_range("docs in 2024", {})
    assert tr_in_year == {"start": "2024-01-01", "end": "2024-12-31"}

    # Explicit date range
    tr_range = extract_time_range("logs between 2024-01-01..2024-06-30", {})
    assert tr_range == {"start": "2024-01-01", "end": "2024-06-30"}

    # No match
    assert extract_time_range("general query about architecture", {}) is None
