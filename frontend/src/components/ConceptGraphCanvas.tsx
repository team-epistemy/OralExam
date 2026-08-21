import { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import type { Core, ElementDefinition } from 'cytoscape';
import fcose from 'cytoscape-fcose';
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

// ── Clustering: group concepts into abstraction-level bands ───────────────────
// Lower abstraction_level = more concrete/foundational; higher = more abstract.
// Bands become the "top-level" nodes you collapse to and expand from.
const BANDS = [
  { id: '__band_foundational', label: 'Foundational', color: '#2563eb', max: 0.34 },
  { id: '__band_core', label: 'Core', color: '#7c3aed', max: 0.67 },
  { id: '__band_advanced', label: 'Advanced', color: '#db2777', max: Infinity },
];

function bandFor(level?: number) {
  const v = typeof level === 'number' ? level : 0.5;
  return BANDS.find((b) => v < b.max) || BANDS[BANDS.length - 1];
}

function humanize(t: string): string {
  return (t || 'related').toLowerCase().replace(/_/g, ' ');
}

type Mode = 'clusters' | 'concepts';

/** Build cytoscape elements for the detailed (concepts-in-clusters) view. */
function detailedElements(concepts: GConcept[], edges: GEdge[]): ElementDefinition[] {
  const labels = new Set(concepts.map((c) => c.label).filter(Boolean));
  const usedBands = new Set<string>();
  const nodes: ElementDefinition[] = [];

  for (const c of concepts) {
    if (!c.label) continue;
    const b = bandFor(c.abstraction_level);
    usedBands.add(b.id);
    nodes.push({
      data: { id: c.label, label: c.label, parent: b.id, kind: 'concept', def: c.definition || '' },
    });
  }
  // Parent (cluster) compound nodes — only those that actually contain concepts.
  const parents: ElementDefinition[] = BANDS.filter((b) => usedBands.has(b.id)).map((b) => ({
    data: { id: b.id, label: b.label, kind: 'cluster', color: b.color },
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

/** Build cytoscape elements for the top-level (one node per cluster) view. */
function clusterElements(concepts: GConcept[], edges: GEdge[]): ElementDefinition[] {
  const bandOfLabel = new Map<string, string>();
  const counts = new Map<string, number>();
  for (const c of concepts) {
    if (!c.label) continue;
    const b = bandFor(c.abstraction_level);
    bandOfLabel.set(c.label, b.id);
    counts.set(b.id, (counts.get(b.id) || 0) + 1);
  }
  const nodes: ElementDefinition[] = BANDS.filter((b) => counts.get(b.id)).map((b) => ({
    data: { id: b.id, label: `${b.label}\n${counts.get(b.id)} concepts`, kind: 'clusterTop', color: b.color },
  }));

  // Aggregate concept edges into band→band edges with a count weight.
  const agg = new Map<string, number>();
  for (const e of edges) {
    const s = bandOfLabel.get(e.src);
    const t = bandOfLabel.get(e.dst);
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
      'border-color': '#3b82f6',
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

  const elements = useMemo(
    () => (mode === 'clusters' ? clusterElements(concepts, edges) : detailedElements(concepts, edges)),
    [mode, concepts, edges],
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

      <div className="flex items-center justify-between text-xs text-gray-400">
        <span>
          {mode === 'clusters'
            ? 'Top-level clusters by abstraction. Switch to Detailed or zoom in to see concepts.'
            : 'Scroll to zoom, drag to pan. Click a concept for its definition.'}
        </span>
        <span className="flex items-center gap-3">
          {BANDS.map((b) => (
            <span key={b.id} className="inline-flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: b.color }} /> {b.label}
            </span>
          ))}
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
