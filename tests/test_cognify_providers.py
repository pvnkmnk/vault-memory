# tests/test_cognify_providers.py
"""Tests for cognify LLM provider dispatch (Ollama default, llama.cpp OpenAI-compatible).

The daemon supports two extraction backends selected by ``settings.llm_provider``:
- ``ollama``   — Ollama ``POST {url}/api/generate`` (existing default)
- ``llamacpp`` — OpenAI-compatible ``POST {url}/v1/chat/completions``
                 (llama.cpp ``llama-server``, llamafile, vLLM, LM Studio, ...)
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from daemon.routes import knowledge


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ollama_settings():
    return SimpleNamespace(
        llm_provider="ollama",
        ollama_url="http://localhost:11434",
        ollama_model="llama3.2",
    )


@pytest.fixture
def llamacpp_settings():
    return SimpleNamespace(
        llm_provider="llamacpp",
        llamacpp_url="http://localhost:8081",
        llamacpp_model="qwen2.5-0.5b",
    )


def _ollama_response(triples):
    return {
        "response": json.dumps(triples),
        "model": "llama3.2",
    }


def _openai_response(triples, model="qwen2.5-0.5b"):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(triples)},
                "finish_reason": "stop",
            }
        ],
    }


# ── llama.cpp extraction path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llamacpp_extraction_posts_openai_chat_completions(llamacpp_settings):
    """llamacpp provider must hit /v1/chat/completions with OpenAI-style payload."""
    captured = {}

    def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = _openai_response(
            [{"subject": "Alpha", "predicate": "uses", "object": "Beta"}]
        )
        return response

    with patch("daemon.routes.knowledge.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await knowledge._extract_triples_with_llamacpp(
            "Alpha uses Beta",
            None,
            llamacpp_url=llamacpp_settings.llamacpp_url,
            llamacpp_model=llamacpp_settings.llamacpp_model,
        )

    assert captured["url"] == "http://localhost:8081/v1/chat/completions"
    payload = captured["json"]
    assert payload["messages"][0]["role"] == "user"
    assert "Alpha uses Beta" in payload["messages"][0]["content"]
    assert payload["stream"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "qwen2.5-0.5b"

    assert result["triples"] == [{"subject": "Alpha", "predicate": "uses", "object": "Beta"}]
    assert result["invalid_triples"] == 0
    assert result["model"] == "qwen2.5-0.5b"


@pytest.mark.asyncio
async def test_llamacpp_extraction_allows_empty_model(llamacpp_settings):
    """Empty llamacpp_model must omit the model field (llama-server ignores it anyway)."""
    captured = {}

    def fake_post(url, json=None, **kwargs):
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = _openai_response([], model="local-model")
        return response

    with patch("daemon.routes.knowledge.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await knowledge._extract_triples_with_llamacpp(
            "text", None, llamacpp_url="http://localhost:8081", llamacpp_model="",
        )

    assert "model" not in captured["json"]
    assert result["model"] == "local-model"  # falls back to server-reported model


@pytest.mark.asyncio
async def test_llamacpp_empty_model_warns_once(caplog):
    """Empty LLAMACPP_MODEL logs a compatibility warning exactly once."""
    # Other tests call with an empty model first; reset the module flag so
    # this test verifies the warn-once contract deterministically.
    knowledge._llamacpp_model_warned = False
    captured = {}

    def fake_post(url, json=None, **kwargs):
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = _openai_response([])
        return response

    def _make_client():
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    with patch("daemon.routes.knowledge.httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value = _make_client()
        with caplog.at_level("WARNING", logger="vault-memoryd"):
            await knowledge._extract_triples_with_llamacpp(
                "text", None, llamacpp_url="http://localhost:8081", llamacpp_model="",
            )
            # Second call must not log again
            await knowledge._extract_triples_with_llamacpp(
                "text", None, llamacpp_url="http://localhost:8081", llamacpp_model="",
            )

    warnings = [r for r in caplog.records if "LLAMACPP_MODEL" in r.message]
    assert len(warnings) == 1
    # Both requests still omit the model field
    assert "model" not in captured["json"]


@pytest.mark.asyncio
async def test_llamacpp_extraction_handles_missing_choices(llamacpp_settings):
    """Malformed OpenAI response (no choices) must yield zero triples, not raise."""

    def fake_post(url, json=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"id": "x", "choices": []}
        return response

    with patch("daemon.routes.knowledge.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await knowledge._extract_triples_with_llamacpp(
            "text", None, llamacpp_url="http://localhost:8081", llamacpp_model="",
        )

    assert result["triples"] == []
    assert result["invalid_triples"] == 0


@pytest.mark.asyncio
async def test_llamacpp_connection_error_is_connect_error(llamacpp_settings):
    """A dead llama-server must surface httpx.ConnectError (endpoint maps to OLLAMA_UNAVAILABLE)."""
    with patch("daemon.routes.knowledge.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(httpx.ConnectError):
            await knowledge._extract_triples_with_llamacpp(
                "text", None, llamacpp_url="http://localhost:8081", llamacpp_model="",
            )


# ── Ollama extraction path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_extraction_pins_temperature_in_options():
    """Ollama ignores a top-level temperature; it must be nested in options."""
    captured = {}

    def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = _ollama_response(
            [{"subject": "A", "predicate": "uses", "object": "B"}]
        )
        return response

    with patch("daemon.routes.knowledge.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await knowledge._extract_triples_with_ollama(
            "Alpha uses Beta",
            None,
            ollama_url="http://localhost:11434",
            ollama_model="llama3.2",
        )

    assert captured["url"] == "http://localhost:11434/api/generate"
    payload = captured["json"]
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload.get("options", {}).get("temperature") == 0.0
    assert result["triples"] == [{"subject": "A", "predicate": "uses", "object": "B"}]


# ── Provider dispatch ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_routes_to_ollama_by_default(ollama_settings):
    with patch(
        "daemon.routes.knowledge._extract_triples_with_ollama",
        new=AsyncMock(return_value={"triples": [], "invalid_triples": 0, "model": "llama3.2"}),
    ) as mock_ollama, patch(
        "daemon.routes.knowledge._extract_triples_with_llamacpp",
        new=AsyncMock(),
    ) as mock_llamacpp:
        await knowledge._extract_triples("text", None, ollama_settings)

    mock_ollama.assert_awaited_once()
    mock_llamacpp.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_routes_to_llamacpp(llamacpp_settings):
    with patch(
        "daemon.routes.knowledge._extract_triples_with_ollama",
        new=AsyncMock(),
    ) as mock_ollama, patch(
        "daemon.routes.knowledge._extract_triples_with_llamacpp",
        new=AsyncMock(return_value={"triples": [], "invalid_triples": 0, "model": "m"}),
    ) as mock_llamacpp:
        await knowledge._extract_triples("text", None, llamacpp_settings)

    mock_llamacpp.assert_awaited_once()
    args, kwargs = mock_llamacpp.await_args
    assert kwargs["llamacpp_url"] == "http://localhost:8081"
    assert kwargs["llamacpp_model"] == "qwen2.5-0.5b"
    mock_ollama.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_is_case_insensitive_and_defaults_on_unknown():
    """Unknown/None provider falls back to ollama; whitespace/case is normalized."""
    settings = SimpleNamespace(
        llm_provider="  LlamaCpp  ",  # case/whitespace normalization
        llamacpp_url="http://localhost:8081",
        llamacpp_model="m",
    )
    with patch(
        "daemon.routes.knowledge._extract_triples_with_llamacpp",
        new=AsyncMock(return_value={"triples": [], "invalid_triples": 0, "model": "m"}),
    ) as mock_llamacpp:
        await knowledge._extract_triples("text", None, settings)
    mock_llamacpp.assert_awaited_once()

    settings_none = SimpleNamespace(llm_provider=None)
    with patch(
        "daemon.routes.knowledge._extract_triples_with_ollama",
        new=AsyncMock(return_value={"triples": [], "invalid_triples": 0, "model": "m"}),
    ) as mock_ollama:
        await knowledge._extract_triples("text", None, settings_none)
    mock_ollama.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_without_settings_object_defaults_to_ollama():
    """A settings-less call (e.g. legacy callers) still routes to Ollama defaults."""
    with patch(
        "daemon.routes.knowledge._extract_triples_with_ollama",
        new=AsyncMock(return_value={"triples": [], "invalid_triples": 0, "model": "m"}),
    ) as mock_ollama:
        await knowledge._extract_triples("text", None, None)
    mock_ollama.assert_awaited_once()


# ── Shared prompt / parser behavior ──────────────────────────────────────────


def test_prompt_builder_includes_entity_filter():
    prompt = knowledge._build_extraction_prompt("some text", ["concept", "method"])
    assert "Only extract entities of these types: ['concept', 'method']" in prompt
    assert "some text" in prompt


def test_prompt_builder_without_filter():
    prompt = knowledge._build_extraction_prompt("some text", None)
    assert "Only extract entities" not in prompt


def test_parse_triples_response_finds_embedded_json():
    text = 'Sure! Here are the triples:\n[{"subject": "A", "predicate": "uses", "object": "B"}]\nDone.'
    triples, invalid = knowledge._parse_triples_response(text)
    assert triples == [{"subject": "A", "predicate": "uses", "object": "B"}]
    assert invalid == 0


def test_parse_triples_response_garbage_yields_empty():
    triples, invalid = knowledge._parse_triples_response("no json here at all")
    assert triples == []
    assert invalid == 0


def test_parse_triples_response_unwraps_object_wrapper():
    """OpenAI json_object mode yields {"triples": [...]} — parser must unwrap it."""
    text = json.dumps(
        {"triples": [{"subject": "A", "predicate": "uses", "object": "B"}]}
    )
    triples, invalid = knowledge._parse_triples_response(text)
    assert triples == [{"subject": "A", "predicate": "uses", "object": "B"}]
    assert invalid == 0


def test_parse_triples_response_bare_single_triple_object():
    """Small models often return one bare triple object — parse it as a 1-item list."""
    text = json.dumps({"subject": "A", "predicate": "uses", "object": "B"})
    triples, invalid = knowledge._parse_triples_response(text)
    assert triples == [{"subject": "A", "predicate": "uses", "object": "B"}]
    assert invalid == 0


def test_parse_triples_response_bare_triple_in_prose():
    text = 'Result: {"subject": "A", "predicate": "uses", "object": "B"} — done.'
    triples, invalid = knowledge._parse_triples_response(text)
    assert triples == [{"subject": "A", "predicate": "uses", "object": "B"}]
    assert invalid == 0


def test_parse_triples_response_unwraps_object_wrapper_in_prose():
    text = 'Here you go:\n{"triples": [{"subject": "A", "predicate": "uses", "object": "B"}]}\nDone.'
    triples, invalid = knowledge._parse_triples_response(text)
    assert triples == [{"subject": "A", "predicate": "uses", "object": "B"}]
    assert invalid == 0


def test_parse_triples_response_object_without_triples_key_counts_invalid():
    """A bare object without triple fields is treated as one malformed triple."""
    triples, invalid = knowledge._parse_triples_response('{"foo": "bar"}')
    assert triples == []
    assert invalid == 1


# ── Prompt wrapper modes ─────────────────────────────────────────────────────


def test_prompt_builder_array_mode_is_default():
    prompt = knowledge._build_extraction_prompt("some text")
    assert "JSON array of triples" in prompt
    assert '"triples"' not in prompt


def test_prompt_builder_object_mode_asks_for_wrapper():
    prompt = knowledge._build_extraction_prompt("some text", json_wrapper="object")
    assert '"triples"' in prompt
    assert "JSON object" in prompt
    assert "some text" in prompt


# ── Config wiring ────────────────────────────────────────────────────────────


def test_config_reads_llm_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "llamacpp")
    monkeypatch.setenv("LLAMACPP_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("LLAMACPP_MODEL", "test-model")

    from daemon.config import Settings

    settings = Settings()
    assert settings.llm_provider == "llamacpp"
    assert settings.llamacpp_url == "http://127.0.0.1:9999"
    assert settings.llamacpp_model == "test-model"


def test_config_llm_provider_default_is_ollama(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLAMACPP_URL", raising=False)
    monkeypatch.delenv("LLAMACPP_MODEL", raising=False)

    from daemon.config import Settings

    settings = Settings()
    assert settings.llm_provider == "ollama"
    assert settings.llamacpp_url == "http://localhost:8081"
    assert settings.llamacpp_model == ""
