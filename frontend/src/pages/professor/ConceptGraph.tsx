import { useState, lazy, Suspense } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Network, Loader2, RefreshCw } from 'lucide-react';
import { get, post } from '../../api/client';
import ProgressSteps from '../../components/ProgressSteps';

// Cytoscape is heavy — load it only when a graph is actually rendered.
const ConceptGraphCanvas = lazy(() => import('../../components/ConceptGraphCanvas'));

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

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Concept Graph</h1>
          <p className="text-sm text-gray-500 mt-1">
            A zoomable map of how the concepts in your course connect. Start at the top-level clusters, then zoom in for detail.
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

            {/* Zoomable Cytoscape view */}
            <Suspense fallback={<div className="h-[520px] flex items-center justify-center text-sm text-gray-400">Loading map…</div>}>
              <ConceptGraphCanvas concepts={concepts} edges={edges} />
            </Suspense>

            {/* Concepts as text */}
            <div>
              <h3 className="text-sm font-semibold text-gray-900 mb-2">Concepts</h3>
              <div className="grid gap-2 sm:grid-cols-2">
                {concepts.map((c) => (
                  <div key={c.id || c.node_id || c.label} className="rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2">
                    <p className="text-sm font-medium text-gray-900">{c.label}</p>
                    {c.definition && <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">{c.definition}</p>}
                  </div>
                ))}
              </div>
            </div>

            {/* Next steps */}
            <div className="flex flex-wrap gap-3 pt-2 border-t border-gray-100">
              <Link to={`/professor/assignments/new${courseId ? `?course=${courseId}` : ''}`} className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
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
