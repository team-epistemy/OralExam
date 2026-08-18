import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Network, Loader2, RefreshCw } from 'lucide-react';
import { get, post } from '../../api/client';
import ProgressSteps from '../../components/ProgressSteps';

interface Course {
  course_id: string;
  course_name: string;
}

interface Concept {
  id?: string;
  node_id?: string;
  label: string;
  definition?: string;
  abstraction_level?: number;
}

interface Edge {
  src: string;
  dst: string;
  edge_type: string;
  confidence?: number;
}

interface GraphData {
  concepts: Concept[];
  edges: Edge[];
  node_count: number;
  edge_count: number;
}

// ── Layout ──────────────────────────────────────────────────────────────────
const NODE_W = 156;
const NODE_H = 48;
const COL_GAP = 120;
const ROW_GAP = 74;
const MARGIN = 28;
const MAX_LAYER = 8;

interface PNode { key: string; label: string; layer: number; x: number; y: number; }
interface LEdge { from: PNode; to: PNode; label: string; }

function humanize(t: string): string {
  return (t || 'related').toLowerCase().replace(/_/g, ' ');
}

/** Cycle-safe layered layout (Kahn's + best-effort placement for cycle nodes). */
function layoutGraph(concepts: Concept[], edges: Edge[]) {
  const labels = concepts.map((c) => c.label).filter(Boolean);
  const exists = new Set(labels);
  const adj = new Map<string, string[]>(labels.map((l) => [l, []]));
  const indeg = new Map<string, number>(labels.map((l) => [l, 0]));
  const rels = edges.filter((e) => exists.has(e.src) && exists.has(e.dst) && e.src !== e.dst);
  for (const e of rels) {
    adj.get(e.src)!.push(e.dst);
    indeg.set(e.dst, (indeg.get(e.dst) || 0) + 1);
  }
  const layer = new Map<string, number>(labels.map((l) => [l, 0]));
  const left = new Map(indeg);
  const queue = labels.filter((l) => (indeg.get(l) || 0) === 0);
  const seen = new Set<string>();
  while (queue.length) {
    const u = queue.shift()!;
    seen.add(u);
    for (const v of adj.get(u)!) {
      layer.set(v, Math.max(layer.get(v)!, layer.get(u)! + 1));
      left.set(v, left.get(v)! - 1);
      if (left.get(v) === 0) queue.push(v);
    }
  }
  // Nodes stuck in cycles: place just after their deepest resolved predecessor.
  for (const l of labels) {
    if (seen.has(l)) continue;
    let best = 0;
    for (const e of rels) if (e.dst === l && layer.has(e.src)) best = Math.max(best, layer.get(e.src)!);
    layer.set(l, best + 1);
  }
  const rowOf = new Map<number, number>();
  const nodes = new Map<string, PNode>();
  for (const c of concepts) {
    if (!c.label) continue;
    const L = Math.min(layer.get(c.label) || 0, MAX_LAYER);
    const row = rowOf.get(L) || 0;
    rowOf.set(L, row + 1);
    nodes.set(c.label, {
      key: c.id || c.node_id || c.label,
      label: c.label,
      layer: L,
      x: MARGIN + L * (NODE_W + COL_GAP),
      y: MARGIN + row * (NODE_H + ROW_GAP),
    });
  }
  const lEdges: LEdge[] = rels
    .map((e) => ({ from: nodes.get(e.src)!, to: nodes.get(e.dst)!, label: humanize(e.edge_type) }))
    .filter((le) => le.from && le.to);
  const list = [...nodes.values()];
  const width = Math.max(MARGIN, ...list.map((n) => n.x + NODE_W)) + MARGIN;
  const height = Math.max(MARGIN, ...list.map((n) => n.y + NODE_H)) + MARGIN;
  return { nodes: list, edges: lEdges, width, height };
}

/** Anchor points: exit the source's right (or bottom for back-edges), enter the target's left. */
function edgePath(e: LEdge) {
  const s = e.from, t = e.to;
  const forward = t.x >= s.x + NODE_W;
  const sx = forward ? s.x + NODE_W : s.x + NODE_W / 2;
  const sy = forward ? s.y + NODE_H / 2 : s.y + NODE_H;
  const tx = forward ? t.x : t.x + NODE_W / 2;
  const ty = forward ? t.y + NODE_H / 2 : t.y;
  const mx = (sx + tx) / 2;
  const d = forward ? `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ty}, ${tx} ${ty}` : `M ${sx} ${sy} Q ${mx} ${sy + 40}, ${tx} ${ty}`;
  return { d, lx: (sx + tx) / 2, ly: (sy + ty) / 2 };
}

export default function ConceptGraph() {
  const [courseId, setCourseId] = useState('');

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<Course[]>('/api/professor/courses'),
    retry: false,
  });

  const { data: graphData, isLoading: graphLoading, refetch } = useQuery({
    queryKey: ['concept-graph', courseId],
    queryFn: () => get<GraphData>(`/api/courses/${courseId}/graph`),
    enabled: !!courseId,
    retry: false,
  });

  const [building, setBuilding] = useState(false);
  const [buildCompleted, setBuildCompleted] = useState(false);

  const graphBuildSteps = [
    { label: 'Fetching course chunks...', endPercent: 20 },
    { label: 'Extracting concepts...', endPercent: 60 },
    { label: 'Identifying relationships...', endPercent: 85 },
    { label: 'Building graph...', endPercent: 100 },
  ];

  const handleRebuild = async () => {
    if (!courseId) return;
    setBuilding(true);
    setBuildCompleted(false);
    try {
      await post(`/api/courses/${courseId}/graph/rebuild`, { domain: 'general', rebuild: true });
      const poll = setInterval(() => {
        refetch().then((result) => {
          if (result.data && (result.data as GraphData).node_count > 0) {
            setBuildCompleted(true);
            setTimeout(() => { setBuilding(false); setBuildCompleted(false); }, 800);
            clearInterval(poll);
          }
        });
      }, 10000);
      setTimeout(() => { clearInterval(poll); setBuilding(false); setBuildCompleted(false); }, 180000);
    } catch (err) {
      console.error('Rebuild failed:', err);
      setBuilding(false);
      setBuildCompleted(false);
    }
  };

  const concepts = graphData?.concepts || [];
  const edges = graphData?.edges || [];
  const nodeCount = graphData?.node_count || concepts.length;
  const edgeCount = graphData?.edge_count || edges.length;

  const layout = useMemo(() => layoutGraph(concepts, edges), [concepts, edges]);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Concept Graph</h1>
          <p className="text-sm text-gray-500 mt-1">
            How the concepts in your course materials connect — edges are labeled with the relationship.
          </p>
        </div>
        {courseId && (
          <button
            onClick={handleRebuild}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            Rebuild Graph
          </button>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <label htmlFor="course-select" className="block text-sm font-medium text-gray-700 mb-2">Select Course</label>
        <select
          id="course-select"
          value={courseId}
          onChange={(e) => setCourseId(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">Choose a course...</option>
          {courses.map((c) => (<option key={c.course_id} value={c.course_id}>{c.course_name}</option>))}
        </select>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-6">
        {!courseId ? (
          <div className="text-center py-8">
            <Network className="w-16 h-16 text-gray-200 mx-auto mb-4" />
            <p className="text-sm text-gray-500">Select a course above to view its concept graph</p>
          </div>
        ) : graphLoading ? (
          <div className="text-center py-8 space-y-3">
            <Loader2 className="w-8 h-8 text-blue-600 animate-spin mx-auto" />
            <p className="text-sm text-gray-500">Loading graph...</p>
          </div>
        ) : building ? (
          <div className="text-center py-8 space-y-4">
            <h3 className="text-lg font-medium text-gray-900">Building Concept Graph</h3>
            <p className="text-sm text-gray-500">AI is reading your materials and extracting concepts.</p>
            <div className="max-w-sm mx-auto">
              <ProgressSteps steps={graphBuildSteps} active={building} completed={buildCompleted} duration={45000} />
            </div>
          </div>
        ) : nodeCount > 0 ? (
          <div className="space-y-5">
            <div className="flex items-center gap-8">
              <div><p className="text-3xl font-bold text-blue-600">{nodeCount}</p><p className="text-xs text-gray-500">Concepts</p></div>
              <div><p className="text-3xl font-bold text-purple-600">{edgeCount}</p><p className="text-xs text-gray-500">Relationships</p></div>
            </div>

            {/* Graphical view */}
            <div className="border border-gray-100 rounded-lg overflow-auto bg-gray-50/50" style={{ maxHeight: 560 }}>
              <svg width={layout.width} height={layout.height} className="block">
                <defs>
                  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="#9333ea" />
                  </marker>
                </defs>
                {layout.edges.map((e, i) => {
                  const p = edgePath(e);
                  return (
                    <g key={i}>
                      <path d={p.d} fill="none" stroke="#c4b5fd" strokeWidth={1.5} markerEnd="url(#arrow)" />
                      <rect x={p.lx - humanize(e.label).length * 3.1 - 4} y={p.ly - 8} width={humanize(e.label).length * 6.2 + 8} height={15} rx={3} fill="#faf5ff" opacity={0.95} />
                      <text x={p.lx} y={p.ly + 3} textAnchor="middle" className="fill-purple-700" style={{ fontSize: 9.5, fontWeight: 600 }}>{e.label}</text>
                    </g>
                  );
                })}
                {layout.nodes.map((n) => (
                  <g key={n.key}>
                    <rect x={n.x} y={n.y} width={NODE_W} height={NODE_H} rx={9} fill="#ffffff" stroke="#3b82f6" strokeWidth={1.5} />
                    <text x={n.x + NODE_W / 2} y={n.y + NODE_H / 2 + 4} textAnchor="middle" className="fill-gray-900" style={{ fontSize: 12, fontWeight: 600 }}>
                      {n.label.length > 20 ? n.label.slice(0, 19) + '…' : n.label}
                    </text>
                  </g>
                ))}
              </svg>
            </div>
            <p className="text-xs text-gray-400">Arrows point from a concept to what it enables / is a prerequisite for. Scroll to see the full graph.</p>

            {/* Next steps */}
            <div className="flex flex-wrap gap-3 pt-2 border-t border-gray-100">
              <Link to="/professor/exam-builder" className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
                Build Exam →
              </Link>
              <Link to={`/professor/assignments/new${courseId ? `?course=${courseId}` : ''}`} className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors">
                Create Assignment →
              </Link>
            </div>
          </div>
        ) : (
          <div className="text-center py-8 space-y-3">
            <Network className="w-16 h-16 text-gray-200 mx-auto" />
            <h3 className="text-lg font-medium text-gray-900">No Graph Yet</h3>
            <p className="text-sm text-gray-500">Upload course materials — the concept graph builds automatically after ingestion.</p>
            <button onClick={handleRebuild} className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
              <RefreshCw className="w-4 h-4" /> Trigger Build
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
