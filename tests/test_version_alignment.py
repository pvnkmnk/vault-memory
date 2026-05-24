import json
import asyncio
from pathlib import Path

from daemon.health import metrics
from daemon.main import app
from daemon.version import __version__


def test_runtime_versions_match_pyproject():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert f'version = "{__version__}"' in pyproject
    assert app.version == __version__


def test_health_metrics_use_shared_version():
    body = asyncio.run(metrics())

    assert f'version="{__version__}"' in body
    assert 'version="0.5.0"' not in body


def test_mcp_initialize_uses_shared_version(monkeypatch):
    from cli import mcp_adapter

    sent = []
    monkeypatch.setattr(mcp_adapter, "_send", sent.append)
    monkeypatch.setattr(
        mcp_adapter.sys,
        "stdin",
        iter([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})]),
    )

    mcp_adapter.run_mcp_adapter("http://127.0.0.1:5051")

    assert sent[0]["result"]["serverInfo"]["version"] == __version__
