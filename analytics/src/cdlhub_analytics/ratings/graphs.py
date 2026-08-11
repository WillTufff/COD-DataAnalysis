"""The graphs a plus-minus design rests on, and what decides separability.

A plus-minus column is separable from its neighbours only where the roster
moved. That question is a graph question: players are nodes, a shared map is an
edge, and a season whose graph falls into pieces has coefficients that cannot be
compared across the pieces at all — ridge will still return numbers, and the
numbers will be the penalty's opinion about how the pieces line up.

Three statistics say it. **Components** are the pieces themselves. **Bridges**
are edges whose removal makes another one, so a bridge carrying two maps is a
whole comparison resting on two maps. **Algebraic connectivity** — the second
smallest eigenvalue of the Laplacian — is the continuous version: near zero
means the graph is one cut away from falling apart even where it is technically
whole.

`networkx` is confined to this module and every function here has a fully typed
signature, per the engineering policy: the graph library never reaches the
callers. The Fiedler value is taken through `numpy` rather than through
`networkx.algebraic_connectivity`, which needs `scipy` — a dependency that
belongs to P1, not to this phase, and these graphs are a few hundred nodes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import networkx as nx
import numpy as np


@dataclass(frozen=True)
class GraphStats:
    """One teammate graph, measured."""

    nodes: int
    edges: int
    components: int
    # Share of nodes in the largest piece. Below 1.0, coefficients in different
    # pieces are only comparable through the penalty.
    largest_component_share: float
    isolated_nodes: int
    bridges: int
    # Maps behind the median bridge. A bridge is a comparison resting on one
    # edge; this says how much play that edge is.
    bridge_median_maps: float | None
    # Second smallest Laplacian eigenvalue, over the largest component. Zero
    # where that component is a single node.
    algebraic_connectivity: float

    def payload(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "components": self.components,
            "largest_component_share": round(self.largest_component_share, 4),
            "isolated_nodes": self.isolated_nodes,
            "bridges": self.bridges,
            "bridge_median_maps": self.bridge_median_maps,
            "algebraic_connectivity": round(self.algebraic_connectivity, 6),
        }


def _fiedler(graph: nx.Graph[int], nodes: list[int]) -> float:
    """Second smallest eigenvalue of the unweighted Laplacian over `nodes`.

    The adjacency matrix is filled here rather than through
    `networkx.to_numpy_array` so "unweighted" is a property of this function
    and not of an edge attribute that happens to be absent.
    """
    if len(nodes) < 2:
        return 0.0
    index = {node: i for i, node in enumerate(nodes)}
    adjacency = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
    for left, right in graph.edges():
        i, j = index.get(left), index.get(right)
        if i is None or j is None:
            continue
        adjacency[i, j] = 1.0
        adjacency[j, i] = 1.0
    laplacian = np.diag(adjacency.sum(axis=1)) - adjacency
    eigenvalues = np.linalg.eigvalsh(laplacian)
    return float(eigenvalues[1])


def graph_stats(pairs: Iterable[tuple[int, int, int]], nodes: Iterable[int]) -> GraphStats:
    """Measure a weighted graph given as `(left, right, maps)` edges.

    Two graphs are read through this. The **teammate graph** joins players who
    shared a side, and says whether a player's column is separable from their
    neighbours'. The **lineup graph** joins the lineups that faced each other,
    and its component count is what caps the design's rank.

    `nodes` is passed separately so a node with no admitted edge still counts as
    isolated rather than vanishing from the denominator.
    """
    graph: nx.Graph[int] = nx.Graph()
    graph.add_nodes_from(int(n) for n in nodes)
    for left, right, maps in pairs:
        if left == right:
            continue
        graph.add_edge(int(left), int(right), maps=int(maps))

    order = graph.number_of_nodes()
    if order == 0:
        return GraphStats(0, 0, 0, 0.0, 0, 0, None, 0.0)

    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    largest = sorted(components[0]) if components else []
    # typeshed declares `bridges` as yielding nodes; networkx yields edge pairs.
    # The cast is the adapter absorbing an upstream stub's inaccuracy, which is
    # the reason the library is confined to one module.
    bridge_edges = cast("list[tuple[int, int]]", list(nx.bridges(graph)))
    bridge_maps = [int(graph.edges[left, right]["maps"]) for left, right in bridge_edges]
    return GraphStats(
        nodes=order,
        edges=graph.number_of_edges(),
        components=len(components),
        largest_component_share=len(largest) / order,
        isolated_nodes=sum(1 for _node, degree in graph.degree() if degree == 0),
        bridges=len(bridge_maps),
        bridge_median_maps=float(np.median(bridge_maps)) if bridge_maps else None,
        algebraic_connectivity=_fiedler(graph, largest),
    )
