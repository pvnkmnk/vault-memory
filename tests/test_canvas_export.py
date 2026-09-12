"""Tests for S27-2 (VAU-31): knowledge graph -> Obsidian Canvas export.

`export_graph_to_canvas` is the inverse of `CanvasGraphPipeline.parse`, so the
strongest assertion available is a round trip: canvas -> graph -> canvas must
preserve entity names and typed relationships. The layout is a deterministic
grid precisely so that a round trip is comparable at all.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.canvas_graph_pipeline import CanvasGraphPipeline, export_graph_to_canvas
from daemon.routes.graph import graph_router


# ---------------------------------------------------------------------------
# export_graph_to_canvas
# ---------------------------------------------------------------------------


def test_round_trip_preserves_entities_and_typed_edges():
    """canvas -> graph -> canvas keeps the names and the relationship labels."""
    original = {
        "nodes": [
            {"id": "a", "type": "text", "text": "Alpha"},
            {"id": "b", "type": "text", "text": "Beta"},
        ],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b", "label": "CAUSES"}],
    }

    parsed = CanvasGraphPipeline().parse("board.canvas", original)
    assert [e.entity_name for e in parsed.entities] == ["Alpha", "Beta"]

    exported = export_graph_to_canvas(parsed.entities, parsed.edges)

    # Re-parse the export: the projection survived the trip.
    reparsed = CanvasGraphPipeline().parse("board.canvas", exported)
    assert [e.entity_name for e in reparsed.entities] == ["Alpha", "Beta"]
    assert [(e.source_name, e.target_name, e.relationship_type) for e in reparsed.edges] == [
        ("Alpha", "Beta", "CAUSES")
    ]


def test_layout_is_deterministic_and_follows_the_grid():
    nodes = ["A", "B", "C", "D", "E"]
    first = export_graph_to_canvas(nodes, [], columns=2)
    second = export_graph_to_canvas(nodes, [], columns=2)

    assert first == second  # byte-identical output for identical input

    positions = {n["text"]: (n["x"], n["y"]) for n in first["nodes"]}
    # 2 columns: A,B on row 0; C,D on row 1; E wraps to row 2.
    assert positions["A"] == (0, 0)
    assert positions["B"] == (480, 0)
    assert positions["C"] == (0, 280)
    assert positions["D"] == (480, 280)
    assert positions["E"] == (0, 560)
    assert [n["id"] for n in first["nodes"]] == ["n0", "n1", "n2", "n3", "n4"]


def test_duplicate_node_names_are_collapsed_to_one_node():
    doc = export_graph_to_canvas(["Alpha", "Alpha", "Beta"], [])
    assert [n["text"] for n in doc["nodes"]] == ["Alpha", "Beta"]


def test_edges_with_unresolved_endpoints_self_loops_and_duplicates_are_dropped():
    nodes = ["Alpha", "Beta"]
    edges = [
        {"source_name": "Alpha", "target_name": "Beta", "relationship_type": "USES"},
        {"source_name": "Alpha", "target_name": "Beta", "relationship_type": "USES"},
        {"source_name": "Alpha", "target_name": "Alpha", "relationship_type": "SELF"},
        {"source_name": "Alpha", "target_name": "Ghost", "relationship_type": "USES"},
    ]

    doc = export_graph_to_canvas(nodes, edges)

    assert len(doc["edges"]) == 1
    edge = doc["edges"][0]
    assert (edge["fromNode"], edge["toNode"], edge["label"]) == ("n0", "n1", "USES")
    assert (edge["fromSide"], edge["toSide"]) == ("right", "left")


def test_accepts_both_edge_naming_conventions_and_bare_string_nodes():
    short = export_graph_to_canvas(
        ["Alpha", "Beta"], [{"source": "Alpha", "target": "Beta", "relationship": "EXTENDS"}]
    )
    assert short["edges"][0]["label"] == "EXTENDS"

    long_form = export_graph_to_canvas(
        ["Alpha", "Beta"],
        [{"source_name": "Alpha", "target_name": "Beta", "relationship_type": "USES"}],
    )
    assert long_form["edges"][0]["label"] == "USES"


def test_node_mapping_shapes_choose_file_vs_text_nodes():
    doc = export_graph_to_canvas(
        [
            {"entity_name": "Alpha", "node_type": "topic"},
            {"entity_name": "Beta", "file_path": "Knowledge/Beta.md"},
        ],
        [],
    )

    by_text = {n.get("text", n.get("file")): n for n in doc["nodes"]}
    assert by_text["Alpha"]["type"] == "text"
    assert by_text["Alpha"]["vaultMemoryNodeType"] == "topic"
    assert by_text["Knowledge/Beta.md"]["type"] == "file"
    assert by_text["Knowledge/Beta.md"]["file"] == "Knowledge/Beta.md"


def test_columns_must_be_positive():
    with pytest.raises(ValueError):
        export_graph_to_canvas(["A"], [], columns=0)


def test_unlabelled_edge_round_trips_as_connected():
    """An unlabelled edge must not change type on the way back through parse."""
    doc = export_graph_to_canvas(
        ["Alpha", "Beta"], [{"source_name": "Alpha", "target_name": "Beta"}]
    )

    assert doc["edges"][0]["label"] == "CONNECTED"
    reparsed = CanvasGraphPipeline().parse("board.canvas", doc)
    assert reparsed.edges[0].relationship_type == "CONNECTED"


def test_an_unlabelled_edge_dedupes_against_an_explicitly_connected_one():
    doc = export_graph_to_canvas(
        ["Alpha", "Beta"],
        [
            {"source_name": "Alpha", "target_name": "Beta"},
            {"source_name": "Alpha", "target_name": "Beta", "relationship_type": "CONNECTED"},
        ],
    )
    assert len(doc["edges"]) == 1


def test_file_nodes_survive_the_round_trip():
    """A CanvasEntity file node keeps its path instead of downcasting to text."""
    original = {
        "nodes": [
            {"id": "a", "type": "file", "file": "Knowledge/Alpha.md"},
            {"id": "b", "type": "text", "text": "Beta"},
        ],
        "edges": [],
    }

    parsed = CanvasGraphPipeline().parse("board.canvas", original)
    exported = export_graph_to_canvas(parsed.entities, parsed.edges)

    file_node = next(n for n in exported["nodes"] if n["id"] == "n0")
    assert file_node["type"] == "file"
    assert file_node["file"] == "Knowledge/Alpha.md"
    # The text node stays a text node.
    assert next(n for n in exported["nodes"] if n["id"] == "n1")["type"] == "text"


# ---------------------------------------------------------------------------
# POST /graph/export/canvas
# ---------------------------------------------------------------------------


class _Cursor:
    """Returns the node rows, then the edge rows, in call order."""

    def __init__(self, nodes, edges):
        self._results = [nodes, edges]
        self._current = []
        self.statements = []
        self.params = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        self.params.append(params)
        self._current = self._results.pop(0) if self._results else []

    def fetchall(self):
        return self._current


def _deps(vault, nodes, edges):
    cursor = _Cursor(nodes, edges)
    postgres = MagicMock()
    postgres.cursor.return_value.__enter__.return_value = cursor
    postgres.cursor.return_value.__exit__.return_value = False
    return SimpleNamespace(
        settings=SimpleNamespace(lite_mode=False, vault_path=str(vault)),
        postgres=postgres,
    ), cursor


def _client(deps):
    from daemon.auth import verify_api_key
    from daemon.dependencies import get_dependencies

    app = FastAPI()
    app.include_router(graph_router)
    app.dependency_overrides[get_dependencies] = lambda: deps
    # Auth is covered elsewhere; keep this focused on the export behaviour.
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    return TestClient(app)


def test_export_endpoint_returns_a_canvas_document(tmp_path):
    deps, _ = _deps(
        tmp_path,
        [{"entity_name": "Alpha", "node_type": "topic", "file_path": None}],
        [
            {
                "source_name": "Alpha",
                "target_name": "Beta",
                "relationship_type": "USES",
                "edge_source": "body",
            }
        ],
    )

    response = _client(deps).post("/graph/export/canvas", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 1
    assert body["edge_count"] == 0  # Beta is not in the node set
    assert body["written_to"] is None
    assert body["canvas"]["nodes"][0]["text"] == "Alpha"


def test_export_endpoint_writes_the_canvas_into_the_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    deps, _ = _deps(
        vault,
        [{"entity_name": "Alpha", "node_type": "note", "file_path": None}],
        [],
    )

    response = _client(deps).post(
        "/graph/export/canvas", json={"write_to": "Canvas/graph.canvas"}
    )

    assert response.status_code == 200
    assert response.json()["written_to"] == "Canvas/graph.canvas"

    written = vault / "Canvas" / "graph.canvas"
    assert written.exists()
    # The file on disk is the same document the response carried.
    assert json.loads(written.read_text(encoding="utf-8")) == response.json()["canvas"]


def test_export_endpoint_appends_the_canvas_extension(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    deps, _ = _deps(vault, [{"entity_name": "Alpha", "node_type": "note", "file_path": None}], [])

    response = _client(deps).post("/graph/export/canvas", json={"write_to": "graph"})

    assert response.status_code == 200
    assert response.json()["written_to"] == "graph.canvas"
    assert (vault / "graph.canvas").exists()


def test_export_endpoint_refuses_to_write_outside_the_vault(tmp_path):
    """A traversal path is the caller's error, so it must be a 400, not a 500."""
    vault = tmp_path / "vault"
    vault.mkdir()
    deps, _ = _deps(vault, [], [])

    response = _client(deps).post(
        "/graph/export/canvas", json={"write_to": "../escaped.canvas"}
    )

    assert response.status_code == 400
    assert not (tmp_path / "escaped.canvas").exists()


def test_export_endpoint_emits_file_nodes_from_topic_hubs(tmp_path):
    """A node row carrying a vault_path becomes a real Obsidian file node."""
    deps, _ = _deps(
        tmp_path,
        [
            {
                "entity_name": "Alpha",
                "node_type": "topic",
                "file_path": "Ontology/Alpha.md",
            }
        ],
        [],
    )

    body = _client(deps).post("/graph/export/canvas", json={}).json()

    assert body["canvas"]["nodes"][0]["type"] == "file"
    assert body["canvas"]["nodes"][0]["file"] == "Ontology/Alpha.md"


def test_filter_values_are_bound_never_concatenated_into_sql(tmp_path):
    """The statement text is identical whatever the filters, so nothing a
    caller sends can reach the query text."""
    plain_deps, plain_cursor = _deps(tmp_path, [], [])
    _client(plain_deps).post("/graph/export/canvas", json={})

    hostile = "Alpha' OR 1=1 --"
    filtered_deps, filtered_cursor = _deps(tmp_path, [], [])
    _client(filtered_deps).post(
        "/graph/export/canvas",
        json={"entity": hostile, "relationship": "USES", "source": "canvas"},
    )

    assert plain_cursor.statements == filtered_cursor.statements
    assert filtered_cursor.params[0]["entity"] == hostile


def test_export_endpoint_refuses_a_write_through_a_symlinked_directory(tmp_path):
    """A lexical component check cannot see a symlink already under the vault."""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "link").symlink_to(outside, target_is_directory=True)
    deps, _ = _deps(vault, [], [])

    response = _client(deps).post(
        "/graph/export/canvas", json={"write_to": "link/evil.canvas"}
    )

    assert response.status_code == 400
    assert not (outside / "evil.canvas").exists()


def test_export_endpoint_refuses_a_symlinked_target_file(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = tmp_path / "secret.canvas"
    secret.write_text("do not overwrite", encoding="utf-8")
    (vault / "graph.canvas").symlink_to(secret)
    deps, _ = _deps(vault, [], [])

    response = _client(deps).post(
        "/graph/export/canvas", json={"write_to": "graph.canvas"}
    )

    assert response.status_code == 400
    assert secret.read_text(encoding="utf-8") == "do not overwrite"
