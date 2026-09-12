"""Canvas graph extraction helpers.

The sync watcher owns Canvas file chunking. This module owns the lighter graph
projection used to populate ``canvas_entities`` and canvas-sourced
``relationships`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class CanvasEntity:
    canvas_path: str
    node_id: str
    entity_name: str
    entity_type: str
    node_text: str


@dataclass(frozen=True)
class CanvasRelationship:
    source_name: str
    target_name: str
    relationship_type: str = "CONNECTED"


@dataclass
class CanvasGraphResult:
    entities: list[CanvasEntity] = field(default_factory=list)
    edges: list[CanvasRelationship] = field(default_factory=list)


class CanvasGraphPipeline:
    """Extract a conservative graph projection from Obsidian Canvas JSON."""

    def parse(self, rel_path: str, data: dict[str, Any]) -> CanvasGraphResult:
        """Parse Canvas JSON into entity and relationship records.

        File nodes become entities named by their referenced vault file. Text
        nodes become entities named by a compact version of the node text.
        Edges are only emitted when both endpoints can be resolved to entities.
        """
        if not isinstance(data, dict):
            return CanvasGraphResult()

        node_names: dict[str, str] = {}
        entities: list[CanvasEntity] = []

        for node in data.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue

            entity_name, entity_type, node_text = self._entity_from_node(node)
            if not entity_name:
                continue

            node_names[node_id] = entity_name
            entities.append(
                CanvasEntity(
                    canvas_path=rel_path,
                    node_id=node_id,
                    entity_name=entity_name,
                    entity_type=entity_type,
                    node_text=node_text[:1000],
                )
            )

        relationships: list[CanvasRelationship] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for edge in data.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = node_names.get(str(edge.get("fromNode") or ""))
            target = node_names.get(str(edge.get("toNode") or ""))
            if not source or not target or source == target:
                continue

            relationship_type = self._relationship_type(edge)
            key = (source, target, relationship_type)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            relationships.append(
                CanvasRelationship(
                    source_name=source,
                    target_name=target,
                    relationship_type=relationship_type,
                )
            )

        return CanvasGraphResult(entities=entities, edges=relationships)

    @staticmethod
    def _entity_from_node(node: dict[str, Any]) -> tuple[str, str, str]:
        file_path = str(node.get("file") or "").strip()
        if file_path:
            return Path(file_path).stem, "file", file_path

        text = str(node.get("text") or "").strip()
        if text:
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            return first_line[:120], "text", text

        return "", "", ""

    @staticmethod
    def _relationship_type(edge: dict[str, Any]) -> str:
        label = str(edge.get("label") or "").strip()
        if not label:
            return "CONNECTED"
        normalized = "".join(ch if ch.isalnum() else "_" for ch in label.upper())
        normalized = "_".join(part for part in normalized.split("_") if part)
        return normalized or "CONNECTED"


# ---------------------------------------------------------------------------
# S27-2 (VAU-31): Knowledge Graph -> Canvas export
#
# The inverse of CanvasGraphPipeline.parse. Layout is a deterministic grid so
# the same graph always exports byte-identically — that keeps the output
# diffable in git and, more practically, testable.
# ---------------------------------------------------------------------------

DEFAULT_COLUMNS = 4
DEFAULT_NODE_WIDTH = 400
DEFAULT_NODE_HEIGHT = 200
DEFAULT_COLUMN_SPACING = 480
DEFAULT_ROW_SPACING = 280


def _field(source: Any, keys: Sequence[str]) -> str:
    """Read the first populated field from a mapping or an object.

    Callers hand us three shapes: psycopg2 ``RealDictRow``s (mappings),
    ``CanvasEntity``/``CanvasRelationship`` dataclasses, and bare strings.
    Reading mappings *and* attributes is what makes the export the true inverse
    of :meth:`CanvasGraphPipeline.parse` — re-exporting a parsed result has to
    work, and that result is dataclasses, not dicts.
    """
    for key in keys:
        value = source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)
        if value:
            return str(value).strip()
    return ""


def _node_name(node: Any) -> str:
    """Read a node name from a bare string, a graph row, or an entity object."""
    if isinstance(node, str):
        return node.strip()
    return _field(node, ("entity_name", "name", "source_name"))


def _node_type(node: Any) -> str:
    # CanvasEntity calls this `entity_type`; DB rows call it `node_type`.
    return _field(node, ("node_type", "entity_type"))


def _node_file(node: Any) -> str:
    """Resolve the vault path a node should render as a ``file`` node.

    DB rows carry an explicit ``file_path``. A ``CanvasEntity`` file node has
    no such field: the parser stores the referenced path in ``node_text`` and
    marks ``entity_type`` as ``file``, so recover it from there. Without this
    the round-trip downcasts every file node to a text node.
    """
    path = _field(node, ("file_path", "file", "vault_path"))
    if path:
        return path
    if _field(node, ("entity_type",)) == "file":
        return _field(node, ("node_text",))
    return ""


def _edge_endpoints(edge: Any) -> tuple[str, str, str]:
    """Read source/target/label from either naming convention."""
    source = _field(edge, ("source_name", "source"))
    target = _field(edge, ("target_name", "target"))
    label = _field(edge, ("relationship_type", "relationship", "label"))
    return source, target, label


def export_graph_to_canvas(
    nodes: Iterable[Any],
    edges: Iterable[Any],
    *,
    columns: int = DEFAULT_COLUMNS,
    node_width: int = DEFAULT_NODE_WIDTH,
    node_height: int = DEFAULT_NODE_HEIGHT,
    column_spacing: int = DEFAULT_COLUMN_SPACING,
    row_spacing: int = DEFAULT_ROW_SPACING,
) -> dict[str, Any]:
    """Project a knowledge-graph slice into an Obsidian Canvas document.

    Nodes are placed left-to-right, top-to-bottom on a fixed grid and given
    stable ``n<i>`` ids derived from their order of first appearance, so the
    same input always produces the same document. Edges are only emitted when
    both endpoints resolved to an exported node; self-loops and duplicate
    (source, target, label) triples are dropped for the same reason
    :meth:`CanvasGraphPipeline.parse` drops them.

    A node carrying a ``file_path`` becomes a ``file`` canvas node (Obsidian
    renders the real note); otherwise it becomes a ``text`` node showing the
    entity name.
    """
    if columns < 1:
        raise ValueError("columns must be at least 1")

    canvas_nodes: list[dict[str, Any]] = []
    node_ids: dict[str, str] = {}
    normalized: list[tuple[str, str, str]] = []

    for node in nodes or []:
        name = _node_name(node)
        if not name or name in node_ids:
            continue
        node_ids[name] = f"n{len(normalized)}"
        normalized.append((name, _node_type(node), _node_file(node)))

    for index, (name, node_type, file_path) in enumerate(normalized):
        canvas_node: dict[str, Any] = {
            "id": node_ids[name],
            "x": (index % columns) * column_spacing,
            "y": (index // columns) * row_spacing,
            "width": node_width,
            "height": node_height,
        }
        if file_path:
            canvas_node["type"] = "file"
            canvas_node["file"] = file_path
        else:
            canvas_node["type"] = "text"
            canvas_node["text"] = name
        if node_type:
            # Colour is cosmetic; keep it as metadata rather than inventing a
            # colour per ontology type, which the user cannot configure.
            canvas_node["vaultMemoryNodeType"] = node_type
        canvas_nodes.append(canvas_node)

    canvas_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges or []:
        source, target, label = _edge_endpoints(edge)
        from_id = node_ids.get(source)
        to_id = node_ids.get(target)
        if not from_id or not to_id or from_id == to_id:
            continue
        # Match the parser's normalization: an unlabelled canvas edge parses
        # back as CONNECTED, so an edge must not change type on the way in.
        label = label or "CONNECTED"
        key = (from_id, to_id, label)
        if key in seen:
            continue
        seen.add(key)
        canvas_edges.append(
            {
                "id": f"e{len(canvas_edges)}",
                "fromNode": from_id,
                "toNode": to_id,
                "fromSide": "right",
                "toSide": "left",
                "label": label,
            }
        )

    return {"nodes": canvas_nodes, "edges": canvas_edges}
