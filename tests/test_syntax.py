"""Syntax verification tests."""

import py_compile

import pytest

MODULES_TO_CHECK = [
    "daemon/main.py",
    "daemon/config.py",
    "daemon/sync_watcher.py",
    "daemon/retrieval.py",
    "daemon/weaviate_client.py",
    "daemon/pg_client.py",
    "daemon/heartbeat.py",
    "daemon/context_assembler.py",
    "daemon/lint.py",
    "cli/mcp_adapter.py",
    "cli/main.py",
    "cli/sync_command.py",
]


@pytest.mark.parametrize("module_path", MODULES_TO_CHECK)
def test_module_compiles(module_path):
    py_compile.compile(module_path, doraise=True)
