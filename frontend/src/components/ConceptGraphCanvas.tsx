import { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import type { Core, ElementDefinition } from 'cytoscape';
import fcose from 'cytoscape-fcose';
import Graph from 'graphology';
import louvain from 'graphology-communities-louvain';
import { Maximize2, ZoomIn, ZoomOut, Layers, Boxes } from 'lucide-react';

// Register the fcose layout once (guard against HMR double-registration).
try {
  cytoscape.use(fcose);
} catch {
  /* already registered */
}

export interface GConcept {
  id?: string;
  node_id?: string;
  label: string;
  definition?: string;
  abstraction_level?: number;
}
export interface GEdge {
  src: string;
  dst: string;
  edge_type: string;
  confidence?: number;
}

const PALETTE = [
  '#2563eb', '#7c3aed', '#db2777', '#059669', '#d97706',
  '#0891b2', '#dc2626', '#4f46e5', '#65a30d', '#c026d3',
];

interface Cluster { id: string; label: string; color: string; size: number }
interface Clustering { clusterOf: Map<string, string>; clusters: Cluster[] }

function humanize(t: string): string {
  return (t || 'related').toLowerCase().replace(/_/g, ' ');
}

// ── Clustering: topic communities via Louvain modularity on the relation graph ─
// Densely-connected concepts form a "topic"; each community is named after its
// most-connected member (the hub concept) and gets a distinct color.
function computeClustering(concepts: GConcept[], edges: GEdge[]): Clustering {
  const labels = concepts.map((c) => c.label).filter(Boolean);
  const labelSet = new Set(labels);
  const g = new Graph({ type: 'undirected' });
  const degree = new Map<string, number>(labels.map((l) => [l, 0]));
  for (const l of labels) if (!g.hasNode(l)) g.addNode(l);
  for (const e of edges) {
    if (labelSet.has(e.src) && labelSet.has(e.dst) && e.src !== e.dst && !g.hasEdge(e.src, e.dst)) {
      g.addEdge(e.src, e.dst);
      degree.set(e.src, (degree.get(e.src) || 0) + 1);
      degree.set(e.dst, (degree.get(e.dst) || 0) + 1);
    }
  }

  // No edges → community detection is meaningless; keep one "All concepts" group.
  const comm: Record<string, number> =
    g.size === 0 ? Object.fromEntries(labels.map((l) => [l, 0])) : louvain(g);

  const groups = new Map<number, string[]>();
  for (const l of labels) {
    const c = comm[l] ?? 0;
    (groups.get(c) ?? groups.set(c, []).get(c)!).push(l);
  }

  // Largest community first → stable, meaningful color assignment.
  const ordered = [...groups.values()].sort((a, b) => b.length - a.length);
  const noEdges = g.size === 0;
  const big = ordered.filter((m) => m.length >= 2);
  const singles = ordered.filter((m) => m.length < 2).flat(); // one-concept communities

  const clusterOf = new Map<string, string>();
  const clusters: Cluster[] = [];
  big.forEach((members, i) => {
    const hub = members.slice().sort((a, b) => (degree.get(b) || 0) - (degree.get(a) || 0))[0];
    const name = noEdges ? 'All concepts' : (hub.length > 22 ? hub.slice(0, 21) + '…' : hub);
    const id = `__c${i}`;
    for (const m of members) clusterOf.set(m, id);
    clusters.push({ id, label: name, color: PALETTE[i % PALETTE.length], size: members.length });
  });
  // Roll every singleton (a concept in no topic community) into one "Other" group.
  if (singles.length) {
    const id = '__other';
    for (const m of singles) clusterOf.set(m, id);
    clusters.push({ id, label: big.length ? 'Other' : 'All concepts', color: '#94a3b8', size: singles.length });
  }
  return { clusterOf, clusters };
}

type Mode = 'clusters' | 'concepts';

/** Build cytoscape elements for the detailed (concepts-in-clusters) view. */
function detailedElements(concepts: GConcept[], edges: GEdge[], cl: Clustering): ElementDefinition[] {
  const labels = new Set(concepts.map((c) => c.label).filter(Boolean));
  const colorOf = new Map(cl.clusters.map((c) => [c.id, c.color]));
  const used = new Set<string>();
  const nodes: ElementDefinition[] = [];

  for (const c of concepts) {
    if (!c.label) continue;
    const cid = cl.clusterOf.get(c.label) || cl.clusters[0]?.id;
    if (cid) used.add(cid);
    nodes.push({
      data: { id: c.label, label: c.label, parent: cid, kind: 'concept', def: c.definition || '', color: colorOf.get(cid || '') || '#3b82f6' },
    });
  }
  const parents: ElementDefinition[] = cl.clusters.filter((c) => used.has(c.id)).map((c) => ({
    data: { id: c.id, label: c.label, kind: 'cluster', color: c.color },
  }));

  const rels: ElementDefinition[] = [];
  let i = 0;
  for (const e of edges) {
    if (labels.has(e.src) && labels.has(e.dst) && e.src !== e.dst) {
      rels.push({
        data: { id: `e${i++}`, source: e.src, target: e.dst, label: humanize(e.edge_type), kind: 'rel' },
      });
    }
  }
  return [...parents, ...nodes, ...rels];
}

/** Build cytoscape elements for the top-level (one node per community) view. */
function clusterElements(concepts: GConcept[], edges: GEdge[], cl: Clustering): ElementDefinition[] {
  const colorOf = new Map(cl.clusters.map((c) => [c.id, c.color]));
  const nameOf = new Map(cl.clusters.map((c) => [c.id, c.label]));
  const counts = new Map<string, number>();
  for (const c of concepts) {
    if (!c.label) continue;
    const cid = cl.clusterOf.get(c.label);
    if (cid) counts.set(cid, (counts.get(cid) || 0) + 1);
  }
  const nodes: ElementDefinition[] = cl.clusters
    .filter((c) => counts.get(c.id))
    .map((c) => ({
      data: { id: c.id, label: `${nameOf.get(c.id)}\n${counts.get(c.id)} concepts`, kind: 'clusterTop', color: colorOf.get(c.id) },
    }));

  // Aggregate concept edges into community→community edges with a count weight.
  const agg = new Map<string, number>();
  for (const e of edges) {
    const s = cl.clusterOf.get(e.src);
    const t = cl.clusterOf.get(e.dst);
    if (s && t && s !== t) agg.set(`${s}|${t}`, (agg.get(`${s}|${t}`) || 0) + 1);
  }
  const aggEdges: ElementDefinition[] = [...agg.entries()].map(([k, w], i) => {
    const [source, target] = k.split('|');
    return { data: { id: `a${i}`, source, target, label: `${w}`, kind: 'aggRel', weight: w } };
  });
  return [...nodes, ...aggEdges];
}

const STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node[kind="cluster"]',
    style: {
      'background-opacity': 0.06,
      'background-color': 'data(color)',
      'border-width': 1.5,
      'border-color': 'data(color)',
      'border-opacity': 0.5,
      shape: 'round-rectangle',
      label: 'data(label)',
      'font-size': 13,
      'font-weight': 700,
      color: 'data(color)',
      'text-valign': 'top',
      'text-halign': 'center',
      'text-margin-y': 4,
      padding: '18px',
    },
  },
  {
    selector: 'node[kind="concept"]',
    style: {
      'background-color': '#ffffff',
      'border-width': 1.5,
      'border-color': 'data(color)',
      shape: 'round-rectangle',
      label: 'data(label)',
      'font-size': 11,
      'font-weight': 600,
      color: '#111827',
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'ellipsis',
      'text-max-width': '120px',
      width: 'label',
      height: 26,
      padding: '8px',
    },
  },
  {
    selector: 'node[kind="clusterTop"]',
    style: {
      'background-color': 'data(color)',
      'background-opacity': 0.14,
      'border-width': 2,
      'border-color': 'data(color)',
      shape: 'round-rectangle',
      label: 'data(label)',
      'font-size': 15,
      'font-weight': 700,
      color: 'data(color)',
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'wrap',
      width: 150,
      height: 70,
    },
  },
  {
    selector: 'edge[kind="rel"]',
    style: {
      width: 1.4,
      'line-color': '#c4b5fd',
      'target-arrow-color': '#9333ea',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      label: 'data(label)',
      'font-size': 9,
      'font-weight': 600,
      color: '#7c3aed',
      'text-background-color': '#faf5ff',
      'text-background-opacity': 0.95,
      'text-background-padding': '2px',
    },
  },
  {
    selector: 'edge[kind="aggRel"]',
    style: {
      width: 'mapData(weight, 1, 8, 2, 9)',
      'line-color': '#cbd5e1',
      'target-arrow-color': '#64748b',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      label: 'data(label)',
      'font-size': 11,
      'font-weight': 700,
      color: '#475569',
      'text-background-color': '#f8fafc',
      'text-background-opacity': 0.95,
      'text-background-padding': '2px',
    },
  },
  { selector: '.lod-dim', style: { 'text-opacity': 0 } },
];

export default function ConceptGraphCanvas({ concepts, edges }: { concepts: GConcept[]; edges: GEdge[] }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [mode, setMode] = useState<Mode>('clusters');
  const [selected, setSelected] = useState<{ label: string; def: string } | null>(null);

  const clustering = useMemo(() => computeClustering(concepts, edges), [concepts, edges]);

  const elements = useMemo(
    () => (mode === 'clusters'
      ? clusterElements(concepts, edges, clustering)
      : detailedElements(concepts, edges, clustering)),
    [mode, concepts, edges, clustering],
  );

  useEffect(() => {
    if (!boxRef.current) return;
    const cy = cytoscape({
      container: boxRef.current,
      elements,
      style: STYLE,
      minZoom: 0.15,
      maxZoom: 3,
      wheelSensitivity: 0.25,
    });
    cyRef.current = cy;

    const layout = cy.layout({
      name: 'fcose',
      // @ts-expect-error fcose-specific options are not in the base typings
      quality: 'default',
      animate: false,
      randomize: true,
      nodeSeparation: 90,
      idealEdgeLength: 95,
      nodeRepulsion: 6500,
      padding: 24,
    });
    layout.run();
    cy.fit(undefined, 30);

    // Semantic zoom: fade labels when zoomed far out so the top-level shape reads.
    const applyLod = () => {
      const z = cy.zoom();
      cy.batch(() => {
        cy.edges().toggleClass('lod-dim', z < 0.6);
        cy.nodes('[kind="concept"]').toggleClass('lod-dim', z < 0.45);
      });
    };
    cy.on('zoom', applyLod);
    applyLod();

    cy.on('tap', 'node[kind="concept"]', (evt) => {
      setSelected({ label: evt.target.data('label'), def: evt.target.data('def') || 'No definition available.' });
    });
    cy.on('tap', (evt) => {
      if (evt.target === cy) setSelected(null);
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements]);

  const zoomBy = (f: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };
  const fit = () => cyRef.current?.fit(undefined, 30);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="inline-flex rounded-lg border border-gray-200 overflow-hidden">
          <button
            onClick={() => setMode('clusters')}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium ${mode === 'clusters' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
          >
            <Boxes className="w-3.5 h-3.5" /> Top-level
          </button>
          <button
            onClick={() => setMode('concepts')}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-l border-gray-200 ${mode === 'concepts' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
          >
            <Layers className="w-3.5 h-3.5" /> Detailed
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => zoomBy(1.25)} className="p-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50" title="Zoom in"><ZoomIn className="w-4 h-4" /></button>
          <button onClick={() => zoomBy(0.8)} className="p-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50" title="Zoom out"><ZoomOut className="w-4 h-4" /></button>
          <button onClick={fit} className="p-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50" title="Fit to view"><Maximize2 className="w-4 h-4" /></button>
        </div>
      </div>

      <div ref={boxRef} className="border border-gray-100 rounded-lg bg-gray-50/50" style={{ height: 520 }} />

      <div className="flex items-start justify-between gap-4 text-xs text-gray-400">
        <span className="flex-shrink-0">
          {mode === 'clusters'
            ? 'Top-level topic clusters. Switch to Detailed or zoom in to see concepts.'
            : 'Scroll to zoom, drag to pan. Click a concept for its definition.'}
        </span>
        <span className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
          {clustering.clusters.slice(0, 8).map((c) => (
            <span key={c.id} className="inline-flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: c.color }} />
              <span className="truncate max-w-[120px]">{c.label}</span>
            </span>
          ))}
          {clustering.clusters.length > 8 && <span>+{clustering.clusters.length - 8} more</span>}
        </span>
      </div>

      {selected && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2">
          <p className="text-sm font-semibold text-gray-900">{selected.label}</p>
          <p className="text-xs text-gray-600 mt-0.5 leading-relaxed">{selected.def}</p>
        </div>
      )}
    </div>
  );
}
