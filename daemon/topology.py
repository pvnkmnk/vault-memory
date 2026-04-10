# daemon/topology.py
"""
Topology analysis for knowledge graph.
Provides community detection, god node identification, and topology-aware search.
"""

from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
import logging

logger = logging.getLogger("vault-memory.topology")


# Optional: networkx for advanced topology
try:
    import networkx as nx

    HAS_NETWORKX = True
except ImportError:
    nx = None  # type: ignore
    HAS_NETWORKX = False

# Optional: graspagic for Leiden community detection
try:
    from graspologic import Native

    HAS_GRASPOLOGIC = True
except ImportError:
    HAS_GRASPOLOGIC = False


@dataclass
class Community:
    """Represents a detected community in the knowledge graph."""

    id: int
    nodes: List[str] = field(default_factory=list)
    god_nodes: List[str] = field(default_factory=list)
    size: int = 0
    internal_edges: int = 0
    density: float = 0.0


@dataclass
class TopologyReport:
    """Complete topology analysis report."""

    communities: List[Community] = field(default_factory=list)
    god_nodes: List[Dict[str, Any]] = field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    avg_centrality: float = 0.0
    graph_diameter: Optional[int] = None


def build_networkx_graph(
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> Optional[nx.Graph]:
    """
    Build a NetworkX graph from entities and relationships.
    Returns None if networkx is not available.
    """
    if not HAS_NETWORKX:
        logger.warning("networkx not installed - topology features disabled")
        return None

    G = nx.Graph()

    # Add nodes
    for entity in entities:
        G.add_node(
            entity["entity_name"],
            centrality=entity.get("centrality", 0.0),
            node_type=entity.get("node_type", "note"),
        )

    # Add edges with weights
    for rel in relationships:
        source = rel.get("source_name")
        target = rel.get("target_name")
        if source and target and G.has_node(source) and G.has_node(target):
            weight = 1.0
            # Weight by edge source (frontmatter > body)
            if rel.get("edge_source") == "frontmatter":
                weight = 2.0
            G.add_edge(source, target, weight=weight, **rel)

    return G


def detect_communities(
    G: nx.Graph,
    method: str = "louvain",
) -> List[Community]:
    """
    Detect communities using the specified algorithm.
    Supports: louvain, leiden, label_propagation.
    """
    if not HAS_NETWORKX:
        return []

    if method == "louvain":
        try:
            import community.community_louvain as community_louvain

            partition = community_louvain.best_partition(G)
        except ImportError:
            # Fallback to greedy modularity
            partition = nx.community.greedy_modularity_communities(G)
            partition = {i: set(nodes) for i, nodes in enumerate(partition)}

    elif method == "leiden":
        if HAS_GRASPOLOGIC:
            # Use graspolic's Leiden implementation
            try:
                from graspologic.algorithms import leiden

                partition = leiden(G)
            except Exception:
                partition = _fallback_community_detection(G)
        else:
            partition = _fallback_community_detection(G)

    elif method == "label_propagation":
        communities = list(nx.community.label_propagation_communities(G))
        partition = {i: comm for i, comm in enumerate(communities)}
    else:
        partition = _fallback_community_detection(G)

    # Convert partition dict to Community objects
    communities: List[Community] = []
    for comm_id, nodes in partition.items():
        nodes_list = list(nodes)
        comm = Community(
            id=comm_id,
            nodes=nodes_list,
            size=len(nodes_list),
        )

        # Find god nodes (highest centrality in community)
        if HAS_NETWORKX:
            node_centralities = [(n, G.nodes[n].get("centrality", 0.0)) for n in nodes_list]
            node_centralities.sort(key=lambda x: x[1], reverse=True)
            comm.god_nodes = [n for n, _ in node_centralities[:3]]

        # Calculate density
        if len(nodes_list) > 1:
            subgraph = G.subgraph(nodes_list)
            comm.internal_edges = subgraph.number_of_edges()
            max_edges = len(nodes_list) * (len(nodes_list) - 1) // 2
            comm.density = comm.internal_edges / max_edges if max_edges > 0 else 0.0

        communities.append(comm)

    return communities


def _fallback_community_detection(G: nx.Graph) -> Dict[int, Set[str]]:
    """Fallback community detection using connected components."""
    if nx.is_connected(G):
        return {0: set(G.nodes())}

    # Use connected components as communities
    components = list(nx.connected_components(G))
    return {i: comp for i, comp in enumerate(components)}


def find_god_nodes(
    entities: List[Dict[str, Any]],
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Find god nodes - highly central entities that connect communities.
    Returns top_k entities sorted by centrality.
    """
    if not entities:
        return []

    # Sort by centrality
    sorted_entities = sorted(
        entities,
        key=lambda e: e.get("centrality", 0.0),
        reverse=True,
    )

    return [
        {
            "entity_name": e.get("entity_name"),
            "centrality": e.get("centrality", 0.0),
            "node_type": e.get("node_type", "note"),
        }
        for e in sorted_entities[:top_k]
    ]


def topology_score(
    entity_name: str,
    community: Optional[Community],
    god_nodes: List[str],
    query_communities: List[int],
) -> float:
    """
    Calculate topology-aware score for an entity.
    Boosts entities that share communities with query or are god nodes.
    """
    score = 1.0

    if community is None:
        return score

    # Boost if in same community as query
    if community.id in query_communities:
        score *= 1.5

    # Boost god nodes
    if entity_name in god_nodes:
        score *= 2.0

    return score


def generate_graph_report(report: TopologyReport) -> str:
    """Generate markdown report from topology analysis."""
    lines = [
        "# Graph Topology Report",
        "",
        f"- Total Nodes: {report.total_nodes}",
        f"- Total Edges: {report.total_edges}",
        f"- Communities: {len(report.communities)}",
        f"- Avg Centrality: {report.avg_centrality:.3f}",
        "",
        "## God Nodes",
        "",
    ]

    for god in report.god_nodes:
        lines.append(f"- [[{god['entity_name']}]] — centrality: {god.get('centrality', 0.0):.3f}")

    lines.extend(["", "## Communities", ""])

    for comm in report.communities:
        lines.append(f"### Community {comm.id} ({comm.size} nodes)")
        lines.append("")
        if comm.god_nodes:
            lines.append(f"God nodes: {', '.join(f'[[{n}]]' for n in comm.god_nodes)}")
        lines.append(f"Density: {comm.density:.3f}")
        lines.append("")

        # Show node relationships
        sample_nodes = comm.nodes[:5]
        for node in sample_nodes:
            lines.append(f"- [[{node}]]")
        if len(comm.nodes) > 5:
            lines.append(f"- ... and {len(comm.nodes) - 5} more")

        lines.append("")

    return "\n".join(lines)
