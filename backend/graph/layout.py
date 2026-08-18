"""Pure-Python concept-graph layout — turns extracted concepts + prerequisite
relations into the demo's renderable node/edge shape.

Nodes get {id, label, x, y}; x is driven by prerequisite depth (roots on the
left, dependents to the right), y spreads siblings within a layer. Edges are
emitted as [from_id, to_id] pairs. Mirrors the ConceptGraph structure the demo
hardcodes, but computed from real extracted relations. No AWS/LLM dependency.
"""
from __future__ import annotations
import re
from typing import Dict, List, Tuple

_X_STEP, _Y_STEP, _X0, _Y0 = 170, 90, 90, 70


def slugify(label: str, i: int = 0) -> str:
    """Stable node id from a concept label (matches demo slugify)."""
    s = re.sub(r"[^a-z0-9]", "", (label or "").lower())[:12]
    return s or f"t{i}"


def build_node_ids(concepts: List[Dict]) -> Dict[str, str]:
    """Map each concept label to a unique slug id, disambiguating collisions."""
    ids, used = {}, set()
    for i, c in enumerate(concepts):
        base = slugify(c.get("label", ""), i)
        sid, n = base, 1
        while sid in used:
            sid = f"{base}{n}"
            n += 1
        used.add(sid)
        ids[c.get("label", "")] = sid
    return ids


def _edge_pairs(edges: List[Dict], label_to_id: Dict[str, str]) -> List[Tuple[str, str]]:
    """Resolve relation src/dst labels to node-id pairs, dropping unknowns."""
    pairs = []
    for e in edges or []:
        src, dst = label_to_id.get(e.get("src")), label_to_id.get(e.get("dst"))
        if src and dst and src != dst:
            pairs.append((src, dst))
    return pairs


def _depths(node_ids: List[str], pairs: List[Tuple[str, str]]) -> Dict[str, int]:
    """Longest-path depth from roots; cycle-safe via bounded relaxation."""
    depth = {n: 0 for n in node_ids}
    for _ in range(len(node_ids)):
        changed = False
        for src, dst in pairs:
            if depth[dst] < depth[src] + 1:
                depth[dst] = depth[src] + 1
                changed = True
        if not changed:
            break
    return depth


def compute_layout(concepts: List[Dict], edges: List[Dict]) -> Dict:
    """Produce {nodes:[{id,label,x,y}], edges:[[from,to]]} from concepts+relations."""
    label_to_id = build_node_ids(concepts)
    node_ids = list(label_to_id.values())
    pairs = _edge_pairs(edges, label_to_id)
    depth = _depths(node_ids, pairs)
    nodes = _place_nodes(concepts, label_to_id, depth)
    return {"nodes": nodes, "edges": [[s, d] for s, d in pairs]}


def _place_nodes(concepts: List[Dict], label_to_id: Dict[str, str], depth: Dict[str, int]) -> List[Dict]:
    """Assign x by depth layer, y by position within the layer."""
    row: Dict[int, int] = {}
    nodes = []
    for c in concepts:
        sid = label_to_id.get(c.get("label", ""))
        d = depth.get(sid, 0)
        y_idx = row.get(d, 0)
        row[d] = y_idx + 1
        nodes.append({
            "id": sid, "label": c.get("label", ""),
            "x": _X0 + d * _X_STEP, "y": _Y0 + y_idx * _Y_STEP,
        })
    return nodes


def neighbors(edges: List[Dict], concept_id: str, concepts: List[Dict]) -> List[Dict]:
    """Direct neighbors of a concept id, tagged inbound/outbound with edge type."""
    label_to_id = build_node_ids(concepts)
    id_to_label = {v: k for k, v in label_to_id.items()}
    out = []
    for e in edges or []:
        src, dst = label_to_id.get(e.get("src")), label_to_id.get(e.get("dst"))
        if src == concept_id and dst:
            out.append({"id": dst, "label": id_to_label.get(dst, ""),
                        "edge_type": e.get("edge_type", ""), "direction": "outbound"})
        elif dst == concept_id and src:
            out.append({"id": src, "label": id_to_label.get(src, ""),
                        "edge_type": e.get("edge_type", ""), "direction": "inbound"})
    return out
