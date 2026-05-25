from daemon.retrieval import extract_entities


def test_extract_entities_dedupes_in_first_seen_order():
    assert extract_entities("Alpha beta alpha Beta gamma which") == [
        "alpha",
        "beta",
        "gamma",
    ]
