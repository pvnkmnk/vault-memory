"""Tests for canvas graph extraction pipeline."""

from daemon.canvas_graph_pipeline import CanvasGraphPipeline


def test_parse_extracts_entities_and_explicit_edges():
    pipeline = CanvasGraphPipeline()
    data = {
        "nodes": [
            {"id": "n1", "type": "text", "text": "Architecture Overview"},
            {"id": "n2", "type": "file", "file": "Projects/MyProject.md"},
        ],
        "edges": [
            {"id": "e1", "fromNode": "n1", "toNode": "n2"},
        ],
    }

    result = pipeline.parse("boards/map.canvas", data)
    entity_names = {e.entity_name for e in result.entities}
    assert "Architecture Overview" in entity_names
    assert "MyProject" in entity_names
    assert any(
        edge.source_name == "Architecture Overview"
        and edge.target_name == "MyProject"
        and edge.relationship_type == "connected"
        for edge in result.edges
    )


def test_parse_infers_wikilink_relationships():
    pipeline = CanvasGraphPipeline()
    data = {
        "nodes": [
            {"id": "n1", "type": "text", "text": "Plan links [[Roadmap]] and [[Architecture]]"},
            {"id": "n2", "type": "text", "text": "Roadmap"},
        ],
        "edges": [],
    }

    result = pipeline.parse("boards/links.canvas", data)
    assert any(edge.target_name == "Roadmap" for edge in result.edges)
    assert any(edge.target_name == "Architecture" for edge in result.edges)
