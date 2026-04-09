import os
from unittest.mock import MagicMock, patch

import cli.mcp_adapter as mcp_adapter


def test_call_daemon_includes_api_key_header_when_configured():
    calls = []

    def capture_post(url, json=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "headers": headers or {}})
        response = MagicMock()
        response.json.return_value = {"results": []}
        return response

    with patch.dict(os.environ, {"VAULT_MEMORY_API_KEY": "test-key-xyz"}):
        mcp_adapter._auth_headers = {"x-api-key": os.environ["VAULT_MEMORY_API_KEY"]}
        with patch("httpx.post", side_effect=capture_post):
            mcp_adapter._call_daemon("http://localhost:5051", "search", {"query": "test"})

    assert calls
    assert calls[0]["headers"].get("x-api-key") == "test-key-xyz"
