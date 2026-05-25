"""Canvas graph extraction helpers.

The sync watcher owns Canvas file chunking. This module owns the lighter graph
projection used to populate ``canvas_entities`` and canvas-sourced
``relationships`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
