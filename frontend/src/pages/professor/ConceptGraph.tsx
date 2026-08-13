import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Network, Loader2, RefreshCw } from 'lucide-react';
import { get, post } from '../../api/client';
import ProgressSteps from '../../components/ProgressSteps';

interface Course {
  course_id: string;
  course_name: string;
}

interface GraphData {
  concepts: { node_id: string; label: string; definition: string; abstraction_level: number }[];
  edges: { src: string; dst: string; src_id?: string; dst_id?: string; edge_type: string; confidence: number }[];
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
      // Poll for completion every 10s
      const poll = setInterval(() => {
        refetch().then((result) => {
          if (result.data && (result.data as GraphData).node_count > 0) {
            setBuildCompleted(true);
            setTimeout(() => {
              setBuilding(false);
              setBuildCompleted(false);
            }, 800);
            clearInterval(poll);
          }
        });
      }, 10000);
      // Stop polling after 3 minutes
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
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Concept Graph</h1>
          <p className="text-sm text-gray-500 mt-1">
            Visualize how concepts in your course materials are connected
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

      {/* Course selector */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <label htmlFor="course-select" className="block text-sm font-medium text-gray-700 mb-2">
          Select Course
        </label>
        <select
          id="course-select"
          value={courseId}
          onChange={(e) => setCourseId(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          <option value="">Choose a course...</option>
          {courses.map((c) => (
            <option key={c.course_id} value={c.course_id}>
              {c.course_name}
            </option>
          ))}
        </select>
      </div>

      {/* Graph display */}
      <div className="bg-white rounded-xl border border-gray-200 p-8">
        {!courseId ? (
          <div className="text-center">
            <Network className="w-16 h-16 text-gray-200 mx-auto mb-4" />
            <p className="text-sm text-gray-500">Select a course above to view its concept graph</p>
          </div>
        ) : graphLoading ? (
          <div className="text-center space-y-4">
            <div className="relative">
              <Network className="w-16 h-16 text-gray-200 mx-auto" />
              <Loader2 className="w-6 h-6 text-blue-600 animate-spin absolute top-0 right-1/2 translate-x-8" />
            </div>
            <p className="text-sm text-gray-500">Loading graph...</p>
          </div>
        ) : building ? (
          <div className="text-center space-y-4">
            <div className="relative">
              <Network className="w-16 h-16 text-gray-200 mx-auto" />
              <Loader2 className="w-6 h-6 text-blue-600 animate-spin absolute top-0 right-1/2 translate-x-8" />
            </div>
            <div>
              <h3 className="text-lg font-medium text-gray-900">Building Concept Graph</h3>
              <p className="text-sm text-gray-500 mt-1">
                AI is reading your materials and extracting concepts. This takes 30-60 seconds.
              </p>
            </div>
            <div className="max-w-sm mx-auto">
              <ProgressSteps
                steps={graphBuildSteps}
                active={building}
                completed={buildCompleted}
                duration={45000}
              />
            </div>
          </div>
        ) : nodeCount > 0 ? (
          <div className="space-y-6">
            {/* Stats */}
            <div className="flex items-center justify-center gap-8">
              <div className="text-center">
                <p className="text-3xl font-bold text-blue-600">{nodeCount}</p>
                <p className="text-xs text-gray-500">Concepts</p>
              </div>
              <div className="text-center">
                <p className="text-3xl font-bold text-purple-600">{edgeCount}</p>
                <p className="text-xs text-gray-500">Connections</p>
              </div>
            </div>

            {/* Concept list */}
            {concepts.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-3">Extracted Concepts</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {concepts.slice(0, 20).map((concept, i) => (
                    <div
                      key={concept.node_id || concept.label || i}
                      className="flex items-start gap-3 p-3 border border-gray-100 rounded-lg"
                    >
                      <div className="w-2 h-2 rounded-full bg-blue-400 mt-1.5 shrink-0" />
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-900">{concept.label}</p>
                        {concept.definition && (
                          <p className="text-xs text-gray-500 truncate">{concept.definition}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                {concepts.length > 20 && (
                  <p className="text-xs text-gray-400 mt-2 text-center">
                    Showing 20 of {concepts.length} concepts
                  </p>
                )}
              </div>
            )}

            {/* Edge list */}
            {edges.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-3">Relationships</h3>
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {edges.slice(0, 15).map((edge, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs text-gray-600">
                      <span className="font-medium text-gray-900">{edge.src || edge.src_id || '?'}</span>
                      <span className="text-purple-600 font-medium">{edge.edge_type}</span>
                      <span className="font-medium text-gray-900">{edge.dst || edge.dst_id || '?'}</span>
                      <span className="text-gray-400 ml-auto">{edge.confidence ? `${(edge.confidence * 100).toFixed(0)}%` : ''}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center space-y-4">
            <Network className="w-16 h-16 text-gray-200 mx-auto" />
            <div>
              <h3 className="text-lg font-medium text-gray-900">No Graph Yet</h3>
              <p className="text-sm text-gray-500 mt-1">
                Upload course materials — the concept graph builds automatically after ingestion completes.
              </p>
            </div>
            <button
              onClick={handleRebuild}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              Trigger Build
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
