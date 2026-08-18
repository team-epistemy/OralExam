"""Kuzu property graph schema manager with org_id/course_id tenant filtering."""
from __future__ import annotations

import json
import tempfile
import os
from pathlib import Path
from typing import List, Dict, Optional, Any

try:
    import kuzu
    KUZU_AVAILABLE = True
except ImportError:
    KUZU_AVAILABLE = False

import networkx as nx

from backend.graph.models import ConceptNode, ConceptEdge, AssessmentNode


class KuzuSchemaManager:
    """
    Manages the Kuzu embedded graph database for Epistemy.

    All queries are scoped by org_id + course_id for tenant isolation.

    Node tables: Concept, Assessment
    Rel tables:  ConceptEdge (typed), AssessmentUsesConcept
    """

    DDL = """
    CREATE NODE TABLE IF NOT EXISTS Concept (
        node_id           STRING,
        org_id            STRING,
        course_id         STRING,
        label             STRING,
        definition        STRING,
        domain            STRING,
        graph_type        STRING,
        aliases           STRING,
        abstraction_level DOUBLE,
        depth_level       INT64,
        status            STRING,
        corpus_id         STRING,
        version           INT64,
        PRIMARY KEY (node_id)
    );

    CREATE NODE TABLE IF NOT EXISTS Assessment (
        node_id         STRING,
        org_id          STRING,
        course_id       STRING,
        content         STRING,
        professor_id    STRING,
        corpus_id       STRING,
        assessment_type STRING,
        depth_level     INT64,
        version         INT64,
        PRIMARY KEY (node_id)
    );

    CREATE REL TABLE IF NOT EXISTS ConceptEdge (
        FROM Concept TO Concept,
        edge_id     STRING,
        org_id      STRING,
        course_id   STRING,
        edge_type   STRING,
        confidence  DOUBLE,
        corpus_id   STRING,
        version     INT64
    );

    CREATE REL TABLE IF NOT EXISTS AssessmentUsesConcept (
        FROM Assessment TO Concept,
        weight DOUBLE
    );
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        Initialize a Kuzu graph database.

        Args:
            db_path: Filesystem path for persistent storage, or ":memory:" for temp.
        """
        if not KUZU_AVAILABLE:
            raise ImportError("kuzu not installed. Run: pip install kuzu")

        self.db_path = db_path
        if db_path == ":memory:":
            self._tmpdir = tempfile.mkdtemp()
            self.db = kuzu.Database(os.path.join(self._tmpdir, "epistemy.db"))
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self.db = kuzu.Database(db_path)
        self.conn = kuzu.Connection(self.db)
        self._apply_ddl()

    def _apply_ddl(self) -> None:
        for stmt in self.DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    self.conn.execute(stmt + ";")
                except Exception:
                    pass  # table already exists

    # -- Concept upserts -------------------------------------------------------

    def upsert_concept(self, node: ConceptNode) -> str:
        """Insert or update a ConceptNode. Returns node_id."""
        res = self.conn.execute(
            "MATCH (n:Concept {node_id: $nid}) RETURN n.node_id",
            {"nid": node.node_id}
        )
        exists = res.has_next()

        aliases_json = json.dumps(node.aliases)
        graph_type = node.graph_type.value if hasattr(node.graph_type, "value") else str(node.graph_type)
        status = node.status.value if hasattr(node.status, "value") else str(node.status)

        if exists:
            self.conn.execute("""
                MATCH (n:Concept {node_id: $nid})
                SET n.org_id = $org_id,
                    n.course_id = $course_id,
                    n.label = $label,
                    n.definition = $definition,
                    n.domain = $domain,
                    n.graph_type = $graph_type,
                    n.aliases = $aliases,
                    n.abstraction_level = $al,
                    n.status = $status,
                    n.corpus_id = $corpus_id,
                    n.version = $version
            """, {
                "nid": node.node_id,
                "org_id": node.org_id,
                "course_id": node.course_id,
                "label": node.label,
                "definition": node.definition,
                "domain": node.domain,
                "graph_type": graph_type,
                "aliases": aliases_json,
                "al": node.abstraction_level,
                "status": status,
                "corpus_id": node.corpus_id or "",
                "version": node.version,
            })
        else:
            self.conn.execute("""
                CREATE (:Concept {
                    node_id: $nid,
                    org_id: $org_id,
                    course_id: $course_id,
                    label: $label,
                    definition: $definition,
                    domain: $domain,
                    graph_type: $graph_type,
                    aliases: $aliases,
                    abstraction_level: $al,
                    depth_level: $dl,
                    status: $status,
                    corpus_id: $corpus_id,
                    version: $version
                })
            """, {
                "nid": node.node_id,
                "org_id": node.org_id,
                "course_id": node.course_id,
                "label": node.label,
                "definition": node.definition,
                "domain": node.domain,
                "graph_type": graph_type,
                "aliases": aliases_json,
                "al": node.abstraction_level,
                "dl": node.depth_level or 0,
                "status": status,
                "corpus_id": node.corpus_id or "",
                "version": node.version,
            })
        return node.node_id

    def upsert_concept_edge(self, edge: ConceptEdge) -> str:
        """Insert a ConceptEdge (skip if duplicate src/dst/type)."""
        edge_type = edge.edge_type.value if hasattr(edge.edge_type, "value") else str(edge.edge_type)

        res = self.conn.execute("""
            MATCH (a:Concept {node_id: $src})-[r:ConceptEdge]->(b:Concept {node_id: $dst})
            WHERE r.edge_type = $etype
            RETURN r.edge_id
        """, {"src": edge.src_id, "dst": edge.dst_id, "etype": edge_type})

        if not res.has_next():
            self.conn.execute("""
                MATCH (a:Concept {node_id: $src}), (b:Concept {node_id: $dst})
                CREATE (a)-[:ConceptEdge {
                    edge_id: $eid,
                    org_id: $org_id,
                    course_id: $course_id,
                    edge_type: $etype,
                    confidence: $conf,
                    corpus_id: $corpus_id,
                    version: $version
                }]->(b)
            """, {
                "src": edge.src_id,
                "dst": edge.dst_id,
                "eid": edge.edge_id,
                "org_id": edge.org_id,
                "course_id": edge.course_id,
                "etype": edge_type,
                "conf": edge.confidence,
                "corpus_id": edge.corpus_id or "",
                "version": edge.version,
            })
        return edge.edge_id

    def upsert_assessment(self, node: AssessmentNode) -> str:
        """Insert an AssessmentNode (idempotent)."""
        res = self.conn.execute(
            "MATCH (n:Assessment {node_id: $nid}) RETURN n.node_id",
            {"nid": node.node_id}
        )
        if not res.has_next():
            dl = node.depth_level.value if hasattr(node.depth_level, "value") else int(node.depth_level)
            self.conn.execute("""
                CREATE (:Assessment {
                    node_id: $nid,
                    org_id: $org_id,
                    course_id: $course_id,
                    content: $content,
                    professor_id: $pid,
                    corpus_id: $corpus_id,
                    assessment_type: $atype,
                    depth_level: $dl,
                    version: $version
                })
            """, {
                "nid": node.node_id,
                "org_id": node.org_id,
                "course_id": node.course_id,
                "content": node.content,
                "pid": node.professor_id,
                "corpus_id": node.corpus_id,
                "atype": node.assessment_type,
                "dl": dl,
                "version": node.version,
            })
        return node.node_id

    # -- Tenant-scoped graph queries -------------------------------------------

    def get_prerequisites(
        self, node_id: str, org_id: str, course_id: str, max_depth: int = 5
    ) -> List[Dict]:
        """Return all prerequisite concepts reachable from node_id within tenant scope."""
        results: List[Dict] = []
        visited: set = set()
        queue = [(node_id, 0)]

        while queue:
            cur_id, depth = queue.pop(0)
            if depth >= max_depth or cur_id in visited:
                continue
            visited.add(cur_id)

            res = self.conn.execute("""
                MATCH (a:Concept)-[:ConceptEdge {edge_type: 'PREREQUISITE_FOR'}]->(b:Concept {node_id: $nid})
                WHERE a.org_id = $org_id AND a.course_id = $course_id
                RETURN a.node_id, a.label, a.abstraction_level
            """, {"nid": cur_id, "org_id": org_id, "course_id": course_id})

            while res.has_next():
                row = res.get_next()
                prereq_id = row[0]
                results.append({
                    "node_id": prereq_id,
                    "label": row[1],
                    "abstraction_level": row[2],
                    "hop_count": depth + 1,
                })
                queue.append((prereq_id, depth + 1))

        return results

    def get_k_hop_neighborhood(
        self, node_id: str, org_id: str, course_id: str, k: int = 2
    ) -> List[Dict]:
        """Return all nodes within k hops (any edge direction) within tenant scope."""
        results: List[Dict] = []
        visited = {node_id}
        queue = [(node_id, 0)]

        while queue:
            cur_id, depth = queue.pop(0)
            if depth >= k:
                continue

            # Outgoing
            res = self.conn.execute("""
                MATCH (a:Concept {node_id: $nid})-[:ConceptEdge]->(b:Concept)
                WHERE b.org_id = $org_id AND b.course_id = $course_id
                RETURN b.node_id, b.label, b.abstraction_level
            """, {"nid": cur_id, "org_id": org_id, "course_id": course_id})
            while res.has_next():
                row = res.get_next()
                if row[0] not in visited:
                    visited.add(row[0])
                    results.append({
                        "node_id": row[0], "label": row[1], "abstraction_level": row[2]
                    })
                    queue.append((row[0], depth + 1))

            # Incoming
            res = self.conn.execute("""
                MATCH (b:Concept)-[:ConceptEdge]->(a:Concept {node_id: $nid})
                WHERE b.org_id = $org_id AND b.course_id = $course_id
                RETURN b.node_id, b.label, b.abstraction_level
            """, {"nid": cur_id, "org_id": org_id, "course_id": course_id})
            while res.has_next():
                row = res.get_next()
                if row[0] not in visited:
                    visited.add(row[0])
                    results.append({
                        "node_id": row[0], "label": row[1], "abstraction_level": row[2]
                    })
                    queue.append((row[0], depth + 1))

        return results

    def get_concept_by_id(self, node_id: str) -> Optional[Dict]:
        """Retrieve a single concept by ID."""
        res = self.conn.execute(
            "MATCH (n:Concept {node_id: $nid}) "
            "RETURN n.node_id, n.label, n.abstraction_level, n.domain, n.org_id, n.course_id",
            {"nid": node_id}
        )
        if res.has_next():
            row = res.get_next()
            return {
                "node_id": row[0],
                "label": row[1],
                "abstraction_level": row[2],
                "domain": row[3],
                "org_id": row[4],
                "course_id": row[5],
            }
        return None

    def get_concept_by_label(
        self, label: str, domain: str, org_id: str, course_id: str
    ) -> Optional[Dict]:
        """Find a concept by label within a tenant scope."""
        res = self.conn.execute("""
            MATCH (n:Concept)
            WHERE n.label = $label AND n.domain = $domain
              AND n.org_id = $org_id AND n.course_id = $course_id
            RETURN n.node_id, n.label, n.abstraction_level
        """, {"label": label, "domain": domain, "org_id": org_id, "course_id": course_id})
        if res.has_next():
            row = res.get_next()
            return {"node_id": row[0], "label": row[1], "abstraction_level": row[2]}
        return None

    def get_all_concepts(self, org_id: str, course_id: str) -> List[Dict]:
        """List all concepts for a course (tenant-scoped)."""
        res = self.conn.execute("""
            MATCH (n:Concept)
            WHERE n.org_id = $org_id AND n.course_id = $course_id
            RETURN n.node_id, n.label, n.definition, n.domain,
                   n.abstraction_level, n.depth_level, n.status
        """, {"org_id": org_id, "course_id": course_id})

        results: List[Dict] = []
        while res.has_next():
            row = res.get_next()
            results.append({
                "node_id": row[0],
                "label": row[1],
                "definition": row[2],
                "domain": row[3],
                "abstraction_level": row[4],
                "depth_level": row[5],
                "status": row[6],
            })
        return results

    def get_all_edges(self, org_id: str, course_id: str) -> List[Dict]:
        """List all edges for a course (tenant-scoped)."""
        res = self.conn.execute("""
            MATCH (a:Concept)-[r:ConceptEdge]->(b:Concept)
            WHERE r.org_id = $org_id AND r.course_id = $course_id
            RETURN a.node_id, b.node_id, r.edge_type, r.confidence, r.edge_id
        """, {"org_id": org_id, "course_id": course_id})

        results: List[Dict] = []
        while res.has_next():
            row = res.get_next()
            results.append({
                "src_id": row[0],
                "dst_id": row[1],
                "edge_type": row[2],
                "confidence": row[3],
                "edge_id": row[4],
            })
        return results

    def get_subgraph(
        self, node_ids: List[str], org_id: str, course_id: str
    ) -> Dict[str, Any]:
        """Return the induced subgraph over a set of node IDs (nodes + edges between them)."""
        nodes: List[Dict] = []
        for nid in node_ids:
            concept = self.get_concept_by_id(nid)
            if concept and concept.get("org_id") == org_id and concept.get("course_id") == course_id:
                nodes.append(concept)

        valid_ids = {n["node_id"] for n in nodes}
        edges: List[Dict] = []
        for nid in valid_ids:
            res = self.conn.execute("""
                MATCH (a:Concept {node_id: $nid})-[r:ConceptEdge]->(b:Concept)
                WHERE r.org_id = $org_id AND r.course_id = $course_id
                  AND b.node_id IN $ids
                RETURN a.node_id, b.node_id, r.edge_type, r.confidence
            """, {"nid": nid, "org_id": org_id, "course_id": course_id, "ids": list(valid_ids)})
            while res.has_next():
                row = res.get_next()
                edges.append({
                    "src_id": row[0], "dst_id": row[1],
                    "edge_type": row[2], "confidence": row[3],
                })

        return {"nodes": nodes, "edges": edges}

    # -- Topological depth computation -----------------------------------------

    def compute_topological_depths(
        self, domain: str, org_id: str, course_id: str
    ) -> Dict[str, int]:
        """
        Compute topological depth for all Concept nodes in a tenant-scoped domain.
        Depth = longest prerequisite chain from a root node.
        Updates depth_level in Kuzu and returns {node_id: depth} mapping.
        """
        G = nx.DiGraph()

        # Load edges
        res = self.conn.execute("""
            MATCH (a:Concept)-[r:ConceptEdge]->(b:Concept)
            WHERE a.domain = $domain AND a.org_id = $org_id AND a.course_id = $course_id
              AND b.org_id = $org_id AND b.course_id = $course_id
              AND r.edge_type IN ['PREREQUISITE_FOR', 'ENABLES']
            RETURN a.node_id, b.node_id
        """, {"domain": domain, "org_id": org_id, "course_id": course_id})
        while res.has_next():
            row = res.get_next()
            G.add_edge(row[0], row[1])

        # Load all nodes for domain
        res = self.conn.execute("""
            MATCH (n:Concept)
            WHERE n.domain = $domain AND n.org_id = $org_id AND n.course_id = $course_id
            RETURN n.node_id
        """, {"domain": domain, "org_id": org_id, "course_id": course_id})
        while res.has_next():
            row = res.get_next()
            G.add_node(row[0])

        # Compute longest paths from root nodes
        depths: Dict[str, int] = {}
        roots = [n for n in G.nodes() if G.in_degree(n) == 0]
        for node in G.nodes():
            max_depth = 0
            for root in roots:
                try:
                    paths = list(nx.all_simple_paths(G, root, node, cutoff=20))
                    if paths:
                        max_depth = max(max_depth, max(len(p) - 1 for p in paths))
                except nx.NetworkXError:
                    pass
            depths[node] = max_depth

        # Write back to Kuzu
        for node_id, depth in depths.items():
            self.conn.execute(
                "MATCH (n:Concept {node_id: $nid}) SET n.depth_level = $dl",
                {"nid": node_id, "dl": depth}
            )
        return depths

    # -- Stats -----------------------------------------------------------------

    def get_stats(self, org_id: Optional[str] = None, course_id: Optional[str] = None) -> Dict:
        """Get graph statistics, optionally scoped by tenant."""
        if org_id and course_id:
            node_count = self.conn.execute(
                "MATCH (n:Concept) WHERE n.org_id = $org_id AND n.course_id = $course_id RETURN count(n)",
                {"org_id": org_id, "course_id": course_id}
            ).get_next()[0]
            edge_count = self.conn.execute(
                "MATCH ()-[r:ConceptEdge]->() WHERE r.org_id = $org_id AND r.course_id = $course_id RETURN count(r)",
                {"org_id": org_id, "course_id": course_id}
            ).get_next()[0]
            assessment_count = self.conn.execute(
                "MATCH (n:Assessment) WHERE n.org_id = $org_id AND n.course_id = $course_id RETURN count(n)",
                {"org_id": org_id, "course_id": course_id}
            ).get_next()[0]
        else:
            node_count = self.conn.execute("MATCH (n:Concept) RETURN count(n)").get_next()[0]
            edge_count = self.conn.execute("MATCH ()-[r:ConceptEdge]->() RETURN count(r)").get_next()[0]
            assessment_count = self.conn.execute("MATCH (n:Assessment) RETURN count(n)").get_next()[0]

        return {
            "concept_nodes": node_count,
            "concept_edges": edge_count,
            "assessments": assessment_count,
        }
