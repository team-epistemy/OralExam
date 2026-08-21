import { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { useParams, Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Network, HelpCircle, ClipboardList, Upload, Check, Pencil, X, Save, Trash2, AlertTriangle, Eye, Loader2, Users, Copy } from 'lucide-react';
import { get, post, put, del } from '../../api/client';
import type { Material } from '../../api/materials';
import { listMaterials } from '../../api/materials';
import { createStudent } from '../../api/students';
import DocumentViewerModal from '../../components/DocumentViewerModal';
import type { Question } from '../../api/questions';

// Cytoscape is heavy — load it only when a graph is actually rendered.
const ConceptGraphCanvas = lazy(() => import('../../components/ConceptGraphCanvas'));
import { listQuestions } from '../../api/questions';
import type { Assignment } from '../../api/assignments';
import { listAssignments } from '../../api/assignments';
import StatusBadge from '../../components/StatusBadge';
import ProgressSteps from '../../components/ProgressSteps';

interface Course {
  id: string;
  name: string;
  student_count: number;
  // No columns back these yet; the API returns empty values.
  code?: string;
  description?: string;
  join_code?: string;
}

type Tab = 'materials' | 'graph' | 'questions' | 'assignments' | 'students';

export default function CourseDetail() {
  const { courseId } = useParams<{ courseId: string }>();
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<Tab>(
    (['materials', 'graph', 'questions', 'assignments', 'students'].includes(tabParam || '')
      ? (tabParam as Tab)
      : 'materials'),
  );

  // Keep the active tab in sync with ?tab= so the left-nav "Add Students" link
  // switches to the Students tab even when the course page is already mounted.
  useEffect(() => {
    const t = searchParams.get('tab');
    if (t && ['materials', 'graph', 'questions', 'assignments', 'students'].includes(t)) {
      setActiveTab(t as Tab);
    }
  }, [searchParams]);
  const queryClient = useQueryClient();

  const { data: course } = useQuery({
    queryKey: ['course', courseId],
    queryFn: () => get<Course>(`/api/courses/${courseId}`),
    enabled: !!courseId,
  });

  const { data: materials = [] } = useQuery({
    queryKey: ['materials', courseId],
    queryFn: () => listMaterials(courseId!),
    enabled: !!courseId,
  });

  const { data: questions = [] } = useQuery({
    queryKey: ['questions', courseId],
    queryFn: () => listQuestions(courseId!),
    enabled: !!courseId,
  });

  const { data: assignments = [] } = useQuery({
    queryKey: ['assignments', courseId],
    queryFn: () => listAssignments(courseId!),
    enabled: !!courseId,
  });


  const tabs = [
    { id: 'materials' as Tab, label: 'Materials', icon: FileText },
    { id: 'graph' as Tab, label: 'Concept Graph', icon: Network },
    { id: 'questions' as Tab, label: 'Questions', icon: HelpCircle },
    { id: 'assignments' as Tab, label: 'Assignments', icon: ClipboardList },
    { id: 'students' as Tab, label: 'Students', icon: Users },
  ];

  return (
    <div className="space-y-6">
      {/* Course header */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{course?.name || 'Course'}</h1>
            <p className="text-sm text-gray-500 mt-1">
              {course?.student_count ?? 0} {course?.student_count === 1 ? 'student' : 'students'}
            </p>
            {course?.description && (
              <p className="text-sm text-gray-600 mt-2">{course.description}</p>
            )}
          </div>
          {course?.join_code && (
            <div className="text-right">
              <p className="text-xs text-gray-500">Join Code</p>
              <p className="text-lg font-mono font-bold text-blue-600">{course.join_code}</p>
            </div>
          )}
        </div>
      </div>


      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-6">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      {activeTab === 'materials' && (
        <MaterialsTab materials={materials} courseId={courseId!} queryClient={queryClient} />
      )}
      {activeTab === 'graph' && <GraphTab courseId={courseId!} />}
      {activeTab === 'questions' && <QuestionsTab questions={questions} courseId={courseId!} materials={materials} queryClient={queryClient} />}
      {activeTab === 'assignments' && <AssignmentsTab assignments={assignments} courseId={courseId!} queryClient={queryClient} />}
      {activeTab === 'students' && <StudentsTab courseId={courseId!} />}
    </div>
  );
}

function MaterialsTab({ materials, courseId, queryClient }: { materials: Material[]; courseId: string; queryClient: ReturnType<typeof useQueryClient> }) {
  const [viewing, setViewing] = useState<{ id: string; name: string } | null>(null);

  const handleDelete = async (materialId: string) => {
    if (!confirm('Delete this material? This will also remove its chunks and embeddings.')) return;
    try {
      await del(`/api/materials/${materialId}`);
      queryClient.invalidateQueries({ queryKey: ['materials', courseId] });
    } catch (err: any) {
      alert('Failed to delete material: ' + (err?.message || 'Unknown error'));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Link
          to={`/professor/upload?course=${courseId}`}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          <Upload className="w-4 h-4" />
          Upload Material
        </Link>
      </div>
      {materials.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <FileText className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500">No materials uploaded yet</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
          {materials.map((material: any, i: number) => (
            <div key={material.material_id || material.id || i} className="flex items-center gap-4 px-5 py-3">
              <FileText className="w-4 h-4 text-gray-400" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 truncate">{material.display_name || material.filename || material.file_name}</p>
                <p className="text-xs text-gray-500">{material.created_at ? new Date(material.created_at).toLocaleDateString() : ''}</p>
              </div>
              <StatusBadge status={material.status || 'ready'} />
              <button
                onClick={() => setViewing({
                  id: material.material_id || material.id,
                  name: material.display_name || material.filename || material.file_name || 'Document',
                })}
                className="p-1.5 text-blue-600 hover:bg-blue-50 rounded transition-colors"
                title="View document"
              >
                <Eye className="w-4 h-4" />
              </button>
              <button
                onClick={() => handleDelete(material.material_id || material.id)}
                className="p-1.5 text-red-600 hover:bg-red-50 rounded transition-colors"
                title="Delete material"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
      {viewing && (
        <DocumentViewerModal
          materialId={viewing.id}
          fallbackName={viewing.name}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}

function GraphTab({ courseId }: { courseId: string }) {
  const queryClient = useQueryClient();
  const { data: graphData, isLoading } = useQuery({
    queryKey: ['course-graph', courseId],
    queryFn: () => get<any>(`/api/courses/${courseId}/graph`),
    enabled: !!courseId,
    // A post-delete rebuild runs in the background; poll while it's in flight.
    refetchInterval: (query) => (query.state.data?.is_stale ? 8000 : false),
  });

  const rawConcepts = graphData?.concepts || [];
  const edges = graphData?.edges || [];
  const nodeCount = graphData?.node_count || rawConcepts.length;
  const isStale = Boolean(graphData?.is_stale);

  const rebuildMutation = useMutation({
    mutationFn: () => post(`/api/courses/${courseId}/graph/rebuild`, { domain: 'general' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['course-graph', courseId] }),
  });

  // Local state for curating concepts
  const [concepts, setConcepts] = useState<Array<{ id: string; label: string }>>([]);
  const [newConcept, setNewConcept] = useState('');

  // Sync local state when graphData loads/changes
  useEffect(() => {
    if (rawConcepts.length > 0) {
      setConcepts(rawConcepts.map((c: any, i: number) => ({
        id: c.id || c.concept_id || `concept-${i}`,
        label: c.label,
      })));
    }
  }, [rawConcepts]);

  const removeConcept = (id: string) => {
    setConcepts(prev => prev.filter(c => c.id !== id));
  };

  const addConcept = () => {
    const trimmed = newConcept.trim();
    if (!trimmed) return;
    setConcepts(prev => [...prev, { id: `custom-${Date.now()}`, label: trimmed }]);
    setNewConcept('');
  };

  if (isLoading) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
        <p className="text-sm text-gray-500">Loading graph...</p>
      </div>
    );
  }

  if (nodeCount > 0) {
    return (
      <div className="space-y-4">
        {isStale && (
          <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
            <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-900">This graph may be out of date</p>
              <p className="text-xs text-amber-700 mt-0.5">
                Material was deleted since it was built. A rebuild runs automatically —
                if these concepts still look wrong, rebuild manually.
              </p>
            </div>
            <button
              onClick={() => rebuildMutation.mutate()}
              disabled={rebuildMutation.isPending}
              className="flex-shrink-0 px-3 py-1.5 bg-amber-600 text-white rounded-lg text-xs font-medium hover:bg-amber-700 disabled:opacity-50"
            >
              {rebuildMutation.isPending ? 'Rebuilding...' : 'Rebuild now'}
            </button>
          </div>
        )}

        {/* Graph stats */}
        <div className="flex items-center justify-center gap-8 py-4">
          <div className="text-center">
            <p className="text-3xl font-bold text-blue-600">{nodeCount}</p>
            <p className="text-xs text-gray-500">Concepts</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-purple-600">{edges.length}</p>
            <p className="text-xs text-gray-500">Connections</p>
          </div>
        </div>

        {/* Zoomable concept map */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Concept Map</h3>
          <Suspense fallback={<div className="h-[520px] flex items-center justify-center text-sm text-gray-400">Loading map…</div>}>
            <ConceptGraphCanvas concepts={rawConcepts} edges={edges} />
          </Suspense>
        </div>

        {/* Concept chips - Review & Curate */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-1">Concept Graph — Review & Curate</h3>
          <p className="text-xs text-gray-500 mb-4 leading-relaxed">
            These concepts were extracted from your materials. Remove anything you don't cover, or add a concept the extractor missed. Selected concepts will be used for question generation.
          </p>

          {/* Chip grid */}
          <div className="flex flex-wrap gap-2">
            {concepts.map((c) => (
              <span
                key={c.id}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-full text-xs font-medium"
              >
                <svg className="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                </svg>
                {c.label}
                <button
                  onClick={() => removeConcept(c.id)}
                  className="ml-0.5 hover:bg-blue-700 rounded-full w-4 h-4 flex items-center justify-center text-white/80 hover:text-white transition-colors"
                  title="Remove concept"
                >
                  &times;
                </button>
              </span>
            ))}
          </div>

          {/* Add a concept input */}
          <div className="flex gap-2 mt-4">
            <input
              type="text"
              value={newConcept}
              onChange={(e) => setNewConcept(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addConcept(); }}
              placeholder="Add a concept the extractor missed..."
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <button
              onClick={addConcept}
              disabled={!newConcept.trim()}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              + Add
            </button>
          </div>

          {/* Count footer */}
          <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between">
            <p className="text-xs text-gray-500">{concepts.length} concepts selected</p>
          </div>
        </div>

        {/* Relationships */}
        {edges.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-medium text-gray-700 mb-3">Relationships</h3>
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {edges.slice(0, 15).map((edge: any, i: number) => (
                <div key={i} className="flex items-center gap-2 text-xs text-gray-600">
                  <span className="font-medium text-gray-900">{edge.src || '?'}</span>
                  <span className="text-purple-600 font-medium">{edge.edge_type}</span>
                  <span className="font-medium text-gray-900">{edge.dst || '?'}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
      <Network className="w-12 h-12 text-gray-300 mx-auto mb-4" />
      <h3 className="text-lg font-medium text-gray-900 mb-2">Concept Graph</h3>
      <p className="text-sm text-gray-500 max-w-md mx-auto">
        The concept graph builds automatically after material upload. If it hasn't built yet, go to the Concept Graph page to trigger it.
      </p>
      <Link to="/professor/graph" className="mt-4 inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
        Go to Concept Graph
      </Link>
    </div>
  );
}

function QuestionsTab({ questions, courseId, materials, queryClient }: { questions: Question[]; courseId: string; materials: any[]; queryClient: ReturnType<typeof useQueryClient> }) {
  const [generating, setGenerating] = useState(false);
  const [genCompleted, setGenCompleted] = useState(false);
  const [genResult, setGenResult] = useState<string>('');
  const [selectedMaterials, setSelectedMaterials] = useState<Set<string>>(new Set());
  const [selectedTopics, setSelectedTopics] = useState<Set<string>>(new Set());
  const [count, setCount] = useState(5);
  const [difficulty, setDifficulty] = useState('balanced');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [editPoints, setEditPoints] = useState(1);
  const [saving, setSaving] = useState(false);

  const startEditing = (q: any) => {
    setEditingId(q.question_id || q.id);
    setEditText(q.question || q.text);
    setEditPoints(q.points || 1);
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditText('');
    setEditPoints(1);
  };

  const saveEdit = async (questionId: string) => {
    setSaving(true);
    try {
      await put(`/api/questions/${questionId}`, { text: editText, points: editPoints });
      queryClient.invalidateQueries({ queryKey: ['questions', courseId] });
      setEditingId(null);
    } catch (err: any) {
      alert('Failed to save: ' + (err?.message || 'Unknown error'));
    } finally {
      setSaving(false);
    }
  };

  const handleApprove = async (questionId: string) => {
    try {
      await post(`/api/questions/${questionId}/approve`, {});
      queryClient.invalidateQueries({ queryKey: ['questions', courseId] });
    } catch (err: any) {
      alert('Failed to approve: ' + (err?.message || 'Unknown error'));
    }
  };

  const handleReject = async (questionId: string) => {
    try {
      await post(`/api/questions/${questionId}/reject`, {});
      queryClient.invalidateQueries({ queryKey: ['questions', courseId] });
    } catch (err: any) {
      alert('Failed to reject: ' + (err?.message || 'Unknown error'));
    }
  };

  // Fetch concepts from the graph for topic selection
  const { data: graphData } = useQuery({
    queryKey: ['course-graph', courseId],
    queryFn: () => get<any>(`/api/courses/${courseId}/graph`),
    enabled: !!courseId,
  });
  const graphConcepts: { label: string }[] = graphData?.concepts || [];

  const toggleTopic = (label: string) => {
    setSelectedTopics(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const questionGenSteps = [
    { label: 'Reading course material...', endPercent: 25 },
    { label: 'Identifying concepts...', endPercent: 50 },
    { label: 'Generating Socratic questions...', endPercent: 85 },
    { label: 'Finalizing...', endPercent: 100 },
  ];

  const toggleMaterial = (id: string) => {
    setSelectedMaterials(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleGenerate = async () => {
    setGenerating(true);
    setGenCompleted(false);
    setGenResult('');
    try {
      const body: any = { count, difficulty, domain: 'general' };
      if (selectedMaterials.size > 0) {
        body.material_version_ids = Array.from(selectedMaterials);
      }
      if (selectedTopics.size > 0) {
        body.concept_ids = Array.from(selectedTopics);
      }
      const data: any = await post(`/api/courses/${courseId}/questions/generate`, body);
      setGenCompleted(true);
      if (data.status === 'completed') {
        setGenResult(`Generated ${data.generated_count} questions`);
        queryClient.invalidateQueries({ queryKey: ['questions', courseId] });
      } else {
        setGenResult(data.detail || data.message || 'Generation failed');
      }
    } catch (err: any) {
      const msg = err?.message || err?.toString() || 'Unknown error';
      setGenResult(`Error: ${msg}`);
    } finally {
      // Small delay so user sees 100% before hiding
      setTimeout(() => {
        setGenerating(false);
        setGenCompleted(false);
      }, 800);
    }
  };

  return (
    <div className="space-y-4">
      {/* Generation controls */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h3 className="text-sm font-medium text-gray-700">Generate Questions</h3>

        {/* Material selector */}
        {materials.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">
              Focus on specific materials (leave unchecked for all):
            </label>
            <div className="space-y-1.5">
              {materials.map((m: any, i: number) => (
                <label key={m.material_id || i} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedMaterials.has(m.material_id || m.material_version_id)}
                    onChange={() => toggleMaterial(m.material_id || m.material_version_id)}
                    className="h-4 w-4 text-blue-600 rounded border-gray-300"
                  />
                  <span className="text-gray-700">{m.display_name || m.file_name}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Topic selector from concept graph */}
        {graphConcepts.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">
              Focus on specific topics (leave unchecked for all):
            </label>
            <div className="flex flex-wrap gap-1.5">
              {graphConcepts.map((c, i) => (
                <button
                  key={i}
                  onClick={() => toggleTopic(c.label)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                    selectedTopics.has(c.label)
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {selectedTopics.has(c.label) && '✓ '}{c.label}
                </button>
              ))}
            </div>
            {selectedTopics.size > 0 && (
              <p className="text-xs text-blue-600 mt-1.5">{selectedTopics.size} topics selected</p>
            )}
          </div>
        )}

        {/* Count + Difficulty */}
        <div className="space-y-4">
          <div className="w-32">
            <label className="block text-xs font-medium text-gray-500 mb-1">Questions</label>
            <input
              type="number" min={1} max={20} value={count}
              onChange={(e) => setCount(+e.target.value)}
              className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">Difficulty</label>
            <div className="flex gap-2" role="radiogroup" aria-label="Difficulty level">
              {([
                { value: 'recall', label: 'Recall', subtitle: 'Definitions & formulas' },
                { value: 'balanced', label: 'Balanced', subtitle: 'Mixed depth' },
                { value: 'deep', label: 'Deep', subtitle: 'Causal chains' },
              ] as const).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  role="radio"
                  aria-checked={difficulty === opt.value}
                  onClick={() => setDifficulty(opt.value)}
                  className={`flex-1 py-2.5 px-4 rounded-lg text-sm font-semibold border-2 transition-all ${
                    difficulty === opt.value
                      ? 'bg-navy text-white border-navy'
                      : 'bg-parchment-dark text-gray-600 border-border hover:border-gold hover:text-gray-800'
                  }`}
                >
                  <span className="block">{opt.label}</span>
                  <span className={`block text-xs mt-0.5 ${
                    difficulty === opt.value ? 'text-white/70' : 'text-gray-400'
                  }`}>
                    {opt.subtitle}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <button
          onClick={handleGenerate}
          disabled={generating}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          <HelpCircle className="w-4 h-4" />
          {generating ? 'Generating...' : 'Generate Questions'}
        </button>

        {generating && (
          <ProgressSteps
            steps={questionGenSteps}
            active={generating}
            completed={genCompleted}
            duration={10000}
          />
        )}
      </div>

      {genResult && (
        <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
          {genResult}
        </div>
      )}
      {questions.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <HelpCircle className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500">No questions yet. Click "Generate Questions" to create Socratic oral exam questions from your course material.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
          {questions.map((q: any, i: number) => {
            const qId = q.question_id || q.id;
            const isEditing = editingId === qId;
            return (
              <div key={qId || i} className="px-5 py-3">
                {isEditing ? (
                  <div className="space-y-3">
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      rows={3}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-y"
                    />
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <label className="text-xs font-medium text-gray-500">Points:</label>
                        <input
                          type="number"
                          min={1}
                          max={10}
                          value={editPoints}
                          onChange={(e) => setEditPoints(Math.max(1, Math.min(10, +e.target.value)))}
                          className="w-16 px-2 py-1 border border-gray-300 rounded text-sm"
                        />
                      </div>
                      <div className="flex items-center gap-2 ml-auto">
                        <button
                          onClick={() => saveEdit(qId)}
                          disabled={saving || !editText.trim()}
                          className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
                        >
                          <Save className="w-3 h-3" />
                          {saving ? 'Saving...' : 'Save'}
                        </button>
                        <button
                          onClick={cancelEditing}
                          className="inline-flex items-center gap-1 px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg text-xs font-medium hover:bg-gray-50 transition-colors"
                        >
                          <X className="w-3 h-3" />
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex items-start gap-3">
                      <span className="text-xs font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded mt-0.5">{q.topic || (q.concept_ids && q.concept_ids[0]) || 'General'}</span>
                      <p className="text-sm text-gray-900 flex-1">{q.question || q.text}</p>
                      <span className="text-xs font-semibold text-gold bg-gold/10 px-2 py-0.5 rounded whitespace-nowrap">
                        {q.points || 1} pt{(q.points || 1) !== 1 ? 's' : ''}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-2 ml-12">
                      <span className="text-xs text-gray-400">{q.difficulty || 'balanced'}</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${q.status === 'approved' ? 'bg-green-50 text-green-700' : q.status === 'rejected' ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-600'}`}>
                        {q.status || 'draft'}
                      </span>
                      <div className="ml-auto flex items-center gap-1.5">
                        {q.status !== 'rejected' && (
                          <button
                            onClick={() => startEditing(q)}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors"
                            title="Edit question"
                          >
                            <Pencil className="w-3 h-3" />
                            Edit
                          </button>
                        )}
                        {q.status === 'draft' && (
                          <>
                            <button
                              onClick={() => handleApprove(qId)}
                              className="inline-flex items-center gap-1 px-2 py-1 text-xs text-success hover:bg-green-50 rounded transition-colors"
                              title="Approve question"
                            >
                              <Check className="w-3 h-3" />
                              Approve
                            </button>
                            <button
                              onClick={() => handleReject(qId)}
                              className="inline-flex items-center gap-1 px-2 py-1 text-xs text-red-600 hover:bg-red-50 rounded transition-colors"
                              title="Reject question"
                            >
                              <X className="w-3 h-3" />
                              Reject
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AssignmentsTab({ assignments, courseId, queryClient }: { assignments: Assignment[]; courseId: string; queryClient: ReturnType<typeof useQueryClient> }) {
  const handleClose = async (assignmentId: string) => {
    if (!confirm('Close this assignment? Students will no longer be able to start new exams.')) return;
    try {
      await post(`/api/assignments/${assignmentId}/close`, {});
      queryClient.invalidateQueries({ queryKey: ['assignments', courseId] });
    } catch (err) {
      alert('Failed to close assignment');
    }
  };

  const handleDelete = async (assignmentId: string) => {
    if (!confirm('Delete this assignment? This will remove all student sessions and grades for this assignment.')) return;
    try {
      await del(`/api/assignments/${assignmentId}`);
      queryClient.invalidateQueries({ queryKey: ['assignments', courseId] });
    } catch (err: any) {
      alert('Failed to delete assignment: ' + (err?.message || 'Unknown error'));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Link
          to={`/professor/assignments/new?course=${courseId}`}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          <ClipboardList className="w-4 h-4" />
          Create Assignment
        </Link>
      </div>
      {assignments.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <ClipboardList className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500">No assignments created yet</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
          {(assignments as any[]).map((a, i) => (
            <div
              key={a.assignment_id || a.id || i}
              className="flex items-center gap-4 px-5 py-3"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900">{a.title}</p>
                <p className="text-xs text-gray-500">
                  {a.created_at ? new Date(a.created_at).toLocaleDateString() : ''} · {a.config?.difficulty || 'balanced'} · {a.config?.duration_minutes || 30} min
                </p>
              </div>
              <StatusBadge status={a.status || 'active'} />
              <Link
                to={`/professor/assignments/${a.assignment_id || a.id}/grades`}
                className="text-xs px-3 py-1 border border-blue-200 text-blue-600 rounded-lg hover:bg-blue-50 transition-colors"
              >
                View Grades
              </Link>
              {(a.status === 'active') && (
                <button
                  onClick={() => handleClose(a.assignment_id || a.id)}
                  className="text-xs px-3 py-1 border border-red-200 text-red-600 rounded-lg hover:bg-red-50 transition-colors"
                >
                  Close
                </button>
              )}
              <button
                onClick={() => handleDelete(a.assignment_id || a.id)}
                className="p-1.5 text-red-600 hover:bg-red-50 rounded transition-colors"
                title="Delete assignment"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface Roster { email: string; password: string; }

// Provision student accounts for this course: each email becomes a Cognito user
// enrolled in the course, with a one-time temp password to hand out (no signup).
function StudentsTab({ courseId }: { courseId: string }) {
  const [emails, setEmails] = useState('');
  const [busy, setBusy] = useState(false);
  const [roster, setRoster] = useState<Roster[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [csvNote, setCsvNote] = useState('');

  // Pull every email-looking token out of a CSV/text file, regardless of column
  // layout or header row, and merge them (deduped) into the textarea for review.
  const loadCsv = async (file?: File) => {
    if (!file) return;
    setCsvNote('');
    const text = await file.text();
    const found = Array.from(new Set(
      (text.match(/[^\s,;"']+@[^\s,;"']+\.[^\s,;"']+/g) || []).map((e) => e.trim().toLowerCase())
    ));
    if (found.length === 0) {
      setCsvNote(`No email addresses found in ${file.name}.`);
      return;
    }
    setEmails((prev) => {
      const have = new Set(prev.split(/[\s,;]+/).map((e) => e.trim().toLowerCase()).filter(Boolean));
      return [...have, ...found.filter((e) => !have.has(e))].join('\n');
    });
    setCsvNote(`Loaded ${found.length} email${found.length === 1 ? '' : 's'} from ${file.name}. Review below, then Add students.`);
  };

  const addStudents = async () => {
    const list = Array.from(new Set(
      emails.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean)
    ));
    if (list.length === 0) return;
    setBusy(true);
    setErrors([]);
    const added: Roster[] = [];
    const failed: string[] = [];
    for (const email of list) {
      try {
        const s = await createStudent(email, courseId);
        added.push({ email: s.email, password: s.password });
      } catch (err: any) {
        failed.push(`${email}: ${err?.message || 'failed'}`);
      }
    }
    setRoster((prev) => [...added, ...prev]);
    setErrors(failed);
    setEmails('');
    setCsvNote('');
    setBusy(false);
  };

  const loginUrl = `${window.location.origin}${import.meta.env.BASE_URL}login`;

  const copyRow = (r: Roster) => {
    navigator.clipboard?.writeText(`Email: ${r.email}\nPassword: ${r.password}\nSign in: ${loginUrl}`);
  };
  const copyAll = () => {
    const text = roster.map((r) =>
      `Email: ${r.email}\nPassword: ${r.password}\nSign in: ${loginUrl}\n`).join('\n');
    navigator.clipboard?.writeText(text);
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
        <div>
          <h3 className="text-sm font-medium text-gray-700">Add Students</h3>
          <p className="text-xs text-gray-500 mt-1">
            Paste student emails (one per line, or comma-separated), or upload a CSV. Each gets an
            account enrolled in this course and a temporary password to share — no signup needed.
          </p>
        </div>
        <textarea
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          rows={4}
          placeholder={"student1@berkeley.edu\nstudent2@berkeley.edu"}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-y font-mono"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={addStudents}
            disabled={busy || !emails.trim()}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Users className="w-4 h-4" />}
            {busy ? 'Adding...' : 'Add students'}
          </button>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            className="inline-flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
          >
            <Upload className="w-4 h-4" /> Upload CSV
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.txt"
            className="hidden"
            onChange={(e) => { loadCsv(e.target.files?.[0] ?? undefined); e.target.value = ''; }}
          />
        </div>
        {csvNote && <p className="text-xs text-gray-500">{csvNote}</p>}
      </div>

      {errors.length > 0 && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 space-y-1">
          {errors.map((e, i) => <p key={i}>{e}</p>)}
        </div>
      )}

      {roster.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-medium text-gray-700">Credentials to share</h3>
              <p className="text-xs text-amber-600 mt-0.5">
                Shown once — copy them now. Students sign in at {loginUrl}
              </p>
            </div>
            <button
              onClick={copyAll}
              className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
            >
              <Copy className="w-3.5 h-3.5" /> Copy all
            </button>
          </div>
          <div className="divide-y divide-gray-100">
            {roster.map((r, i) => (
              <div key={i} className="flex items-center gap-4 py-2">
                <span className="text-sm text-gray-900 flex-1 min-w-0 truncate">{r.email}</span>
                <span className="text-sm font-mono text-gray-600">{r.password}</span>
                <button
                  onClick={() => copyRow(r)}
                  className="p-1.5 text-blue-600 hover:bg-blue-50 rounded transition-colors"
                  title="Copy this student's credentials"
                >
                  <Copy className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
