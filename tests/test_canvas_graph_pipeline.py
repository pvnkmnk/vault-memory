from daemon.canvas_graph_pipeline import CanvasGraphPipeline


def test_canvas_graph_pipeline_extracts_typed_entities_and_edges():
    pipeline = CanvasGraphPipeline()

    result = pipeline.parse(
        "Project/boards/map.canvas",
        {
            "nodes": [
                {"id": "n1", "text": "Alpha Concept\nextra context"},
                {"id": "n2", "file": "Project/notes/Beta Note.md"},
            ],
            "edges": [
                {"id": "e1", "fromNode": "n1", "toNode": "n2", "label": "depends on"},
                {"id": "e2", "fromNode": "n1", "toNode": "n2", "label": "depends on"},
            ],
        },
    )

    assert [(e.entity_name, e.entity_type, e.node_id) for e in result.entities] == [
        ("Alpha Concept", "text", "n1"),
        ("Beta Note", "file", "n2"),
    ]
    assert [(e.source_name, e.target_name, e.relationship_type) for e in result.edges] == [
        ("Alpha Concept", "Beta Note", "DEPENDS_ON")
    ]


def test_canvas_graph_pipeline_ignores_unresolvable_edges():
    result = CanvasGraphPipeline().parse(
        "canvas.canvas",
        {"nodes": [{"id": "n1", "text": "Alpha"}], "edges": [{"fromNode": "n1", "toNode": "missing"}]},
    )

    assert len(result.entities) == 1
    assert result.edges == []
