"""Canvas to knowledge graph extraction utilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


@dataclass
class CanvasEntity:
    canvas_path: str
    node_id: str
    entity_name: str
    entity_type: str
    node_text: str


@dataclass
class CanvasRelationship:
    source_name: str
    target_name: str
    relationship_type: str


@dataclass
class CanvasGraphParseResult:
    entities: list[CanvasEntity]
    edges: list[CanvasRelationship]


class CanvasGraphPipeline:
    """Extract entities and relationships from Obsidian Canvas JSON."""

    def parse(self, canvas_path: str, data: dict[str, Any]) -> CanvasGraphParseResult:
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []

        entities: list[CanvasEntity] = []
        relationships: list[CanvasRelationship] = []
        node_name_map: dict[str, str] = {}

        for node in nodes:
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue

            entity_name = self._entity_name_for_node(node)
            if not entity_name:
                continue

            node_name_map[node_id] = entity_name
            entity = CanvasEntity(
                canvas_path=canvas_path,
                node_id=node_id,
                entity_name=entity_name,
                entity_type=self._entity_type_for_node(node),
                node_text=str(node.get("text") or "")[:4000],
            )
            entities.append(entity)

            # Infer relationships from wikilinks embedded in node text.
            node_text = str(node.get("text") or "")
            for target in self._extract_wikilinks(node_text):
                if target and target != entity_name:
                    relationships.append(
                        CanvasRelationship(
                            source_name=entity_name,
                            target_name=target,
                            relationship_type="references",
                        )
                    )

        # Parse explicit canvas edges.
        for edge in edges:
            source_id = str(edge.get("fromNode") or "").strip()
            target_id = str(edge.get("toNode") or "").strip()
            source_name = node_name_map.get(source_id)
            target_name = node_name_map.get(target_id)
            if not source_name or not target_name or source_name == target_name:
                continue
            relationship_type = self._relationship_type_for_edge(edge)
            relationships.append(
                CanvasRelationship(
                    source_name=source_name,
                    target_name=target_name,
                    relationship_type=relationship_type,
                )
            )

        dedup_entities = self._dedupe_entities(entities)
        dedup_edges = self._dedupe_edges(relationships)
        return CanvasGraphParseResult(entities=dedup_entities, edges=dedup_edges)

    def _entity_name_for_node(self, node: dict[str, Any]) -> str:
        file_path = str(node.get("file") or "").strip()
        if file_path:
            return Path(file_path).stem.strip()

        raw = str(node.get("text") or "").strip()
        if not raw:
            return ""

        first_line = raw.splitlines()[0].strip()
        first_line = re.sub(r"^[#\-\*\s>]+", "", first_line)

        # Keep text node identity when possible, even if it references wikilinks.
        plain = WIKILINK_RE.sub("", first_line)
        plain = re.sub(r"\s+", " ", plain).strip(" -:;,.")
        if plain:
            return plain[:200].strip()

        # If node is effectively just a link, use link target.
        wikilinks = self._extract_wikilinks(raw)
        if wikilinks:
            return wikilinks[0]
        return first_line[:200].strip()

    def _entity_type_for_node(self, node: dict[str, Any]) -> str:
        if node.get("file"):
            return "file"
        node_type = str(node.get("type") or "text").strip().lower()
        if node_type in {"group", "text", "file"}:
            return node_type
        return "text"

    def _relationship_type_for_edge(self, edge: dict[str, Any]) -> str:
        label = str(edge.get("label") or "").strip().lower()
        if label:
            # Keep relationship labels safe/simple for SQL filters.
            return re.sub(r"[^a-z0-9_]+", "_", label)[:64] or "connected"
        edge_type = str(edge.get("type") or "").strip().lower()
        if edge_type:
            return re.sub(r"[^a-z0-9_]+", "_", edge_type)[:64] or "connected"
        return "connected"

    def _extract_wikilinks(self, text: str) -> list[str]:
        links = []
        for match in WIKILINK_RE.findall(text):
            target = match.strip()
            if target:
                links.append(Path(target).stem.strip())
        return links

    def _dedupe_entities(self, entities: list[CanvasEntity]) -> list[CanvasEntity]:
        seen: set[tuple[str, str]] = set()
        out: list[CanvasEntity] = []
        for entity in entities:
            key = (entity.canvas_path, entity.node_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(entity)
        return out

    def _dedupe_edges(self, edges: list[CanvasRelationship]) -> list[CanvasRelationship]:
        seen: set[tuple[str, str, str]] = set()
        out: list[CanvasRelationship] = []
        for edge in edges:
            key = (edge.source_name, edge.target_name, edge.relationship_type)
            if key in seen:
                continue
            seen.add(key)
            out.append(edge)
        return out
