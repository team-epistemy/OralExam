"""MCP/REST tools for the concept graph: get_graph, neighbors, subgraph, coverage, rebuild_graph."""
from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional

from backend.models import Caller, Role
from backend.graph.kuzu_store import KuzuSchemaManager
from backend.graph.vector_store import QdrantVectorStore
from backend.graph.metadata import GraphMetadataStore
from backend.graph.models import GraphBuildMessage, GraphVersion

logger = logging.getLogger(__name__)


class AuthorizationError(Exception):
    """Raised when a caller lacks permission for the requested operation."""
    pass


class GraphTools:
    """
    Read/write tools for the concept graph.

    Read tools require course membership.
    rebuild_graph requires professor role.

    Follows the same pattern as backend/tools/materials_tools.py.
    """

    def __init__(
        self,
        kuzu_mgr: KuzuSchemaManager,
        vector_store: QdrantVectorStore,
        metadata_store: GraphMetadataStore,
        queue,  # Queue protocol from async_jobs/queue.py or graph-specific queue
        is_member,  # callable(caller, course_id) -> bool
    ):
        self.kg = kuzu_mgr
        self.vs = vector_store
        self.meta = metadata_store
        self.queue = queue
        self.is_member = is_member

    # -- Read tools -----------------------------------------------------------

    def get_graph(self, caller: Caller, course_id: str) -> Dict[str, Any]:
        """
        Return the full concept graph for a course.

        Returns:
            Dict with "nodes", "edges", "stats", and "version" info.
        """
        self._require_member(caller, course_id)
        org_id = caller.org_id

        nodes = self.kg.get_all_concepts(org_id, course_id)
        edges = self.kg.get_all_edges(org_id, course_id)
        stats = self.kg.get_stats(org_id, course_id)

        # Active version metadata
        version = self.meta.get_active_version(org_id, course_id)
        version_info = None
        if version:
            version_info = {
                "version_id": version.version_id,
                "graph_version": version.graph_version,
                "node_count": version.node_count,
                "edge_count": version.edge_count,
                "validation_score": version.validation_score,
                "is_active": version.is_active,
                "created_at": version.created_at.isoformat() if version.created_at else None,
            }

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": stats,
            "version": version_info,
        }

    def neighbors(
        self, caller: Caller, course_id: str, node_id: str, k: int = 2
    ) -> Dict[str, Any]:
        """
        Return the k-hop neighborhood of a concept node.

        Args:
            node_id: The concept node to explore from.
            k: Number of hops (default 2).

        Returns:
            Dict with "center" node and "neighbors" list.
        """
        self._require_member(caller, course_id)
        org_id = caller.org_id

        center = self.kg.get_concept_by_id(node_id)
        if not center:
            raise AuthorizationError("concept not found")
        if center.get("org_id") != org_id or center.get("course_id") != course_id:
            raise AuthorizationError("concept not in this course")

        neighbors = self.kg.get_k_hop_neighborhood(node_id, org_id, course_id, k=k)
        return {
            "center": center,
            "neighbors": neighbors,
            "hop_radius": k,
        }

    def subgraph(
        self, caller: Caller, course_id: str, node_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Return the induced subgraph over a set of concept node IDs.

        Args:
            node_ids: List of concept node_ids to include.

        Returns:
            Dict with "nodes" and "edges" for the induced subgraph.
        """
        self._require_member(caller, course_id)
        org_id = caller.org_id
        return self.kg.get_subgraph(node_ids, org_id, course_id)

    def coverage(self, caller: Caller, course_id: str) -> Dict[str, Any]:
        """
        Concept coverage analysis for a course graph.

        Returns depth distribution, isolated nodes, and overall coverage metrics.
        """
        self._require_member(caller, course_id)
        org_id = caller.org_id

        nodes = self.kg.get_all_concepts(org_id, course_id)
        edges = self.kg.get_all_edges(org_id, course_id)

        if not nodes:
            return {
                "total_concepts": 0,
                "total_edges": 0,
                "depth_distribution": {},
                "isolated_nodes": [],
                "coverage_score": 0.0,
            }

        # Build adjacency for connectivity analysis
        node_ids = {n["node_id"] for n in nodes}
        connected_ids = set()
        for e in edges:
            connected_ids.add(e["src_id"])
            connected_ids.add(e["dst_id"])

        isolated = [
            {"node_id": n["node_id"], "label": n["label"]}
            for n in nodes
            if n["node_id"] not in connected_ids
        ]

        # Depth distribution
        depth_dist: Dict[str, int] = {}
        for n in nodes:
            dl = n.get("depth_level")
            key = str(dl) if dl is not None else "uncomputed"
            depth_dist[key] = depth_dist.get(key, 0) + 1

        # Coverage score: proportion of connected nodes
        coverage_score = len(connected_ids) / len(nodes) if nodes else 0.0

        return {
            "total_concepts": len(nodes),
            "total_edges": len(edges),
            "depth_distribution": depth_dist,
            "isolated_nodes": isolated,
            "isolated_count": len(isolated),
            "coverage_score": round(coverage_score, 4),
        }

    # -- Write tools ----------------------------------------------------------

    def rebuild_graph(
        self,
        caller: Caller,
        course_id: str,
        domain: str,
        rebuild: bool = False,
    ) -> Dict[str, str]:
        """
        Trigger an async graph build/rebuild job.

        Requires professor role. Enqueues a GraphBuildMessage to the worker queue.

        Args:
            course_id: Target course.
            domain: Knowledge domain to extract.
            rebuild: If True, full rebuild; if False, incremental.

        Returns:
            Dict with "job_id" and "status".
        """
        self._require_professor(caller, course_id)
        org_id = caller.org_id

        message = GraphBuildMessage(
            org_id=org_id,
            course_id=course_id,
            domain=domain,
            rebuild=rebuild,
            triggered_by=caller.user_id,
        )

        # Send to queue (adapts to SqsQueue or InMemoryQueue)
        self.queue.send(message)

        logger.info(
            "Graph build job queued: job_id=%s org=%s course=%s rebuild=%s",
            message.job_id, org_id[:8], course_id[:8], rebuild,
        )

        return {
            "job_id": message.job_id,
            "status": "queued",
            "rebuild": rebuild,
        }

    # -- Authorization helpers ------------------------------------------------

    def _require_member(self, caller: Caller, course_id: str) -> None:
        """Any course membership suffices for read tools."""
        if not self.is_member(caller, course_id):
            raise AuthorizationError("course membership required")

    def _require_professor(self, caller: Caller, course_id: str) -> None:
        """Professor role required for write operations."""
        self._require_member(caller, course_id)
        if caller.role != Role.PROFESSOR:
            raise AuthorizationError("professor role required for graph builds")
