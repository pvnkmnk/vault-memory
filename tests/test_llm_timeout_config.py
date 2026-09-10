# tests/test_llm_timeout_config.py
"""Regression tests: LLM_TIMEOUT_SECONDS env override must parse to int.

Sourcery finding on PR #74: llm_timeout_seconds was in the generic string
branch of Settings.__post_init__, so consumers received a str when the env
var was set, violating the Settings dataclass/Protocol contract (int field).
"""


def test_llm_timeout_env_override_parses_as_int(monkeypatch):
    from daemon.config import Settings

    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
    settings = Settings()
    assert isinstance(settings.llm_timeout_seconds, int)
    assert settings.llm_timeout_seconds == 45


def test_llm_timeout_default_is_int(monkeypatch):
    from daemon.config import Settings

    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    settings = Settings()
    assert isinstance(settings.llm_timeout_seconds, int)
    assert settings.llm_timeout_seconds == 120


def test_llm_timeout_invalid_value_raises_like_other_int_settings(monkeypatch):
    """Garbage values raise ValueError, same as VAULT_MEMORY_PORT and the other
    int-parsed settings — consistent behavior across the Settings contract."""
    from daemon.config import Settings

    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")
    try:
        Settings()
    except ValueError:
        pass  # expected, consistent with other int settings
    else:
        raise AssertionError("expected ValueError for non-integer LLM_TIMEOUT_SECONDS")
