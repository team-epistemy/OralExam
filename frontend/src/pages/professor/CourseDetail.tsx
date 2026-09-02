import { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { useParams, Link, useSearchParams, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Network, ClipboardList, Upload, Trash2, AlertTriangle, Eye, Loader2, Users, Copy, Check, CheckCircle2, Save, KeyRound, ChevronLeft, BarChart3, Calendar, Plus, Lock, BookOpen, Sparkles } from 'lucide-react';
import { get, post, put, del } from '../../api/client';
import type { Material } from '../../api/materials';
import { listMaterials, uploadMaterial, listVersions } from '../../api/materials';
import { createStudentsBatch, dropCourseStudent, resetStudentPassword } from '../../api/students';
import { listStudents, deleteCourse, getSyllabus, setSyllabus, processSyllabus, type ProcessedSession } from '../../api/courses';
import { DEFAULT_ORG } from '../../config';
import FileUpload from '../../components/FileUpload';
import { getCoursePerformance } from '../../api/performance';
import { listSessions, createSession, deleteSession, updateSession, type ClassSession } from '../../api/sessions';
import DocumentViewerModal from '../../components/DocumentViewerModal';
import DocumentGraphModal from '../../components/DocumentGraphModal';

// Cytoscape is heavy — load it only when a graph is actually rendered.
const ConceptGraphCanvas = lazy(() => import('../../components/ConceptGraphCanvas'));
import type { Assignment } from '../../api/assignments';
import { listAssignments } from '../../api/assignments';
import StatusBadge from '../../components/StatusBadge';

interface Course {
  id: string;
  name: string;
  student_count: number;
  // No columns back these yet; the API returns empty values.
  code?: string;
  description?: string;
  join_code?: string;
}

type Tab = 'materials' | 'graph' | 'assignments' | 'students' | 'sessions' | 'performance';

export default function CourseDetail() {
  const { courseId } = useParams<{ courseId: string }>();
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<Tab>(
    (['materials', 'graph', 'assignments', 'students', 'sessions', 'performance'].includes(tabParam || '')
      ? (tabParam as Tab)
      : 'materials'),
  );

  // Keep the active tab in sync with ?tab= so the left-nav "Add Students" link
  // switches to the Students tab even when the course page is already mounted.
  useEffect(() => {
    const t = searchParams.get('tab');
    if (t && ['materials', 'graph', 'assignments', 'students', 'sessions', 'performance'].includes(t)) {
      setActiveTab(t as Tab);
    }
  }, [searchParams]);
  const queryClient = useQueryClient();

  const { data: course } = useQuery({
    queryKey: ['course', courseId],
    queryFn: () => get<Course>(`/api/courses/${courseId}`),
    enabled: !!courseId,
  });

  // Name-based list returns the REAL material_id (+ status); the courseId-only
  // fallback returns version ids, which won't join to a session's materials.
  const { data: materials = [] } = useQuery({
    queryKey: ['materials', courseId, course?.name],
    queryFn: () => listMaterials(DEFAULT_ORG, course!.name),
    enabled: !!course?.name,
  });

  const { data: assignments = [] } = useQuery({
    queryKey: ['assignments', courseId],
    queryFn: () => listAssignments(courseId!),
    enabled: !!courseId,
  });

  // Syllabus gate: until a syllabus is attached, every course action except
  // managing students is locked (mirrors the backend 409). Re-check on mount so
  // the course unlocks as soon as the professor returns from uploading it.
  const { data: syllabus, isLoading: syllabusLoading } = useQuery({
    queryKey: ['syllabus', courseId],
    queryFn: () => getSyllabus(courseId!),
    enabled: !!courseId,
    refetchOnMount: 'always',
  });
  const hasSyllabus = !!syllabus;
  const locked = !syllabusLoading && !hasSyllabus;   // don't lock until we know


  const navigate = useNavigate();
  const handleRemoveCourse = async () => {
    if (!confirm(`Remove course "${course?.name || 'this course'}"? This deletes its materials, questions, exams, and results. This cannot be undone.`)) return;
    try {
      await deleteCourse(courseId!);
      queryClient.invalidateQueries({ queryKey: ['professor-courses'] });
      navigate('/professor/dashboard');
    } catch {
      alert('Could not remove the course. Please try again.');
    }
  };

  const tabs = [
    { id: 'materials' as Tab, label: 'Materials', icon: FileText },
    { id: 'graph' as Tab, label: 'Concept Graph', icon: Network },
    { id: 'assignments' as Tab, label: 'Assignments', icon: ClipboardList },
    { id: 'students' as Tab, label: 'Students', icon: Users },
    { id: 'sessions' as Tab, label: 'Sessions', icon: Calendar },
    { id: 'performance' as Tab, label: 'Performance', icon: BarChart3 },
  ];

  return (
    <div className="space-y-6">
      {/* Back to dashboard + course-level danger action (these lived in the old
          left nav; they now live on the course page itself). */}
      <div className="flex items-center justify-between">
        <Link to="/professor/dashboard" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors">
          <ChevronLeft className="w-4 h-4" /> All courses
        </Link>
        <button
          onClick={handleRemoveCourse}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium text-red-600 hover:bg-red-50 transition-colors"
        >
          <Trash2 className="w-4 h-4" /> Remove course
        </button>
      </div>

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
          {tabs.map((tab) => {
            const tabLocked = locked && tab.id !== 'students';
            return (
              <button
                key={tab.id}
                onClick={() => { if (!tabLocked) setActiveTab(tab.id); }}
                disabled={tabLocked}
                title={tabLocked ? 'Add the course syllabus to unlock this' : undefined}
                className={`flex items-center gap-2 pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.id && !tabLocked
                    ? 'border-blue-600 text-blue-600'
                    : tabLocked
                      ? 'border-transparent text-gray-300 cursor-not-allowed'
                      : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                {tabLocked ? <Lock className="w-3.5 h-3.5" /> : <tab.icon className="w-4 h-4" />}
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab content — gated behind the syllabus for everything but Students */}
      {locked && activeTab !== 'students' ? (
        <SyllabusGate courseId={courseId!} courseName={course?.name || ''} />
      ) : (
        <>
          {activeTab === 'materials' && (
            <MaterialsTab materials={materials} courseId={courseId!} courseName={course?.name || ''} queryClient={queryClient} />
          )}
          {activeTab === 'graph' && <GraphTab courseId={courseId!} />}
          {activeTab === 'assignments' && <AssignmentsTab assignments={assignments} courseId={courseId!} queryClient={queryClient} />}
          {activeTab === 'students' && <StudentsTab courseId={courseId!} />}
          {activeTab === 'sessions' && <SessionsTab courseId={courseId!} courseName={course?.name || ''} hasSyllabus={hasSyllabus} />}
          {activeTab === 'performance' && <PerformanceTab courseId={courseId!} />}
        </>
      )}
    </div>
  );
}

function MaterialsTab({ materials, courseId, courseName, queryClient }: { materials: Material[]; courseId: string; courseName: string; queryClient: ReturnType<typeof useQueryClient> }) {
  const [viewing, setViewing] = useState<{ id: string; name: string } | null>(null);
  const [graphViewing, setGraphViewing] = useState<{ id: string; name: string } | null>(null);
  // Sessions carry which materials belong to them; use that to group the list.
  const { data: sessionsData } = useQuery({
    queryKey: ['course-sessions', courseId],
    queryFn: () => listSessions(courseId),
  });
  const sessions = sessionsData?.sessions ?? [];

  // Which documents have a per-document concept graph (mapping 1) → material_version_id -> concept_count.
  const { data: docGraphData } = useQuery({
    queryKey: ['graph-documents', courseId],
    queryFn: () => get<{ documents: Array<{ material_version_id: string; file_name: string; concept_count: number }> }>(
      `/api/courses/${courseId}/graph/documents`),
    enabled: !!courseId,
  });
  const conceptCounts = new Map<string, number>(
    (docGraphData?.documents ?? []).map((d) => [d.material_version_id, d.concept_count]));

  const handleDelete = async (materialId: string) => {
    if (!confirm('Delete this material? This will also remove its chunks and embeddings.')) return;
    try {
      await del(`/api/materials/${materialId}`);
      queryClient.invalidateQueries({ queryKey: ['materials', courseId] });
    } catch (err: any) {
      alert('Failed to delete material: ' + (err?.message || 'Unknown error'));
    }
  };

  const materialId = (m: any) => m.material_id || m.id;
  const sessionLabel = (s: ClassSession) =>
    s.session_document?.trim() ||
    (s.session_date ? new Date(s.session_date + 'T00:00:00').toLocaleDateString() : 'Untitled session');

  // Build session-ordered groups (by date, nulls last), then an "unassigned" bucket.
  const byMaterial = new Map<string, ClassSession>();
  sessions.forEach((s) => s.materials?.forEach((m) => byMaterial.set(m.material_id, s)));
  const ordered = [...sessions].sort((a, b) =>
    (a.session_date || '9999').localeCompare(b.session_date || '9999'));
  const groups = ordered
    .map((s) => ({
      key: s.session_id,
      title: sessionLabel(s),
      subtitle: s.session_document?.trim() && s.session_date
        ? new Date(s.session_date + 'T00:00:00').toLocaleDateString() : '',
      items: materials.filter((m) => byMaterial.get(materialId(m))?.session_id === s.session_id),
    }))
    .filter((g) => g.items.length > 0);
  const unassigned = materials.filter((m) => !byMaterial.has(materialId(m)));
  if (unassigned.length) groups.push({ key: '__none__', title: 'Not assigned to a session', subtitle: '', items: unassigned });

  const renderRow = (material: any, i: number) => {
    const mvid = materialId(material);
    const name = material.display_name || material.filename || material.file_name || 'Document';
    const conceptCount = conceptCounts.get(mvid);
    return (
    <div key={mvid || i} className="flex items-center gap-4 px-5 py-3">
      <FileText className="w-4 h-4 text-gray-400" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">{name}</p>
        <p className="text-xs text-gray-500">{material.created_at ? new Date(material.created_at).toLocaleDateString() : ''}</p>
      </div>
      <StatusBadge status={material.status || 'ready'} />
      {conceptCount ? (
        <button
          onClick={() => setGraphViewing({ id: mvid, name })}
          className="inline-flex items-center gap-1 p-1.5 text-purple-600 hover:bg-purple-50 rounded transition-colors"
          title={`View concept graph (${conceptCount} concept${conceptCount === 1 ? '' : 's'})`}
        >
          <Network className="w-4 h-4" />
          <span className="text-xs">{conceptCount}</span>
        </button>
      ) : null}
      <button
        onClick={() => setViewing({ id: mvid, name })}
        className="p-1.5 text-blue-600 hover:bg-blue-50 rounded transition-colors"
        title="View document"
      >
        <Eye className="w-4 h-4" />
      </button>
      <button
        onClick={() => handleDelete(mvid)}
        className="p-1.5 text-red-600 hover:bg-red-50 rounded transition-colors"
        title="Delete material"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Link
          to={`/professor/upload?course=${encodeURIComponent(courseName)}&courseId=${courseId}`}
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
        <div className="space-y-4">
          {groups.map((g) => (
            <div key={g.key} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <div className="px-5 py-2.5 bg-gray-50 border-b border-gray-100">
                <p className="text-sm font-semibold text-gray-800">{g.title}</p>
                {g.subtitle && <p className="text-xs text-gray-500">{g.subtitle}</p>}
              </div>
              <div className="divide-y divide-gray-100">{g.items.map(renderRow)}</div>
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
      {graphViewing && (
        <DocumentGraphModal
          materialVersionId={graphViewing.id}
          fallbackName={graphViewing.name}
          onClose={() => setGraphViewing(null)}
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
  const nodeCount = graphData?.node_count || rawConcepts.length;
  const isStale = Boolean(graphData?.is_stale);

  const rebuildMutation = useMutation({
    mutationFn: () => post(`/api/courses/${courseId}/graph/rebuild`, { domain: 'general' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['course-graph', courseId] }),
  });

  // Local state for curating concepts
  const [concepts, setConcepts] = useState<Array<{ id: string; label: string }>>([]);
  const [newConcept, setNewConcept] = useState('');
  const [dirty, setDirty] = useState(false);

  // Sync local state when graphData loads/changes (and clear the dirty flag —
  // the server copy is now the source of truth).
  useEffect(() => {
    if (rawConcepts.length > 0) {
      setConcepts(rawConcepts.map((c: any, i: number) => ({
        id: c.id || c.concept_id || `concept-${i}`,
        label: c.label,
      })));
      setDirty(false);
    }
  }, [rawConcepts]);

  // Persist the curated set so exam generation honors it (removed concepts stop
  // being quizzed; added ones get questions generated per assignment).
  const saveConcepts = useMutation({
    mutationFn: () => put(`/api/courses/${courseId}/graph/concepts`, { concepts }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ['course-graph', courseId] });
    },
  });

  const removeConcept = (id: string) => {
    setConcepts(prev => prev.filter(c => c.id !== id));
    setDirty(true);
  };

  const addConcept = () => {
    const trimmed = newConcept.trim();
    if (!trimmed) return;
    setConcepts(prev => [...prev, { id: `custom-${Date.now()}`, label: trimmed }]);
    setNewConcept('');
    setDirty(true);
  };

  // Per-document concept graph: the cumulative course graph is the union of each
  // document's own graph. This selector lets the professor view one document's
  // graph (mapping: document -> concept-list -> graph). Read-only.
  const [docId, setDocId] = useState('');
  const { data: docsData } = useQuery({
    queryKey: ['graph-documents', courseId],
    queryFn: () => get<{ documents: Array<{ material_version_id: string; file_name: string; concept_count: number }> }>(
      `/api/courses/${courseId}/graph/documents`),
    enabled: !!courseId,
  });
  const documents = docsData?.documents ?? [];
  const { data: docGraph } = useQuery<any>({
    queryKey: ['material-graph', docId],
    queryFn: () => get<any>(`/api/materials/${docId}/graph`),
    enabled: !!docId,
  });
  const viewingDoc = !!docId;
  const view = viewingDoc ? docGraph : graphData;
  const viewConcepts = view?.concepts || [];
  const viewEdges = view?.edges || [];
  const viewNodeCount = view?.node_count ?? viewConcepts.length;

  const DocSelect = documents.length > 0 ? (
    <div className="flex items-center gap-2">
      <label className="text-xs font-medium text-gray-600">Graph for</label>
      <select
        value={docId}
        onChange={(e) => setDocId(e.target.value)}
        className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <option value="">Whole course (cumulative)</option>
        {documents.map((dc) => (
          <option key={dc.material_version_id} value={dc.material_version_id}>
            {dc.file_name} ({dc.concept_count})
          </option>
        ))}
      </select>
    </div>
  ) : null;

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
        {DocSelect && (
          <div className="flex items-center justify-between gap-3 flex-wrap">
            {DocSelect}
            {viewingDoc && (
              <span className="text-xs text-gray-400">Read-only · document graph{view?.source ? ` · ${view.source}` : ''}</span>
            )}
          </div>
        )}
        {!viewingDoc && isStale && (
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
            <p className="text-3xl font-bold text-blue-600">{viewNodeCount}</p>
            <p className="text-xs text-gray-500">Concepts</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-purple-600">{viewEdges.length}</p>
            <p className="text-xs text-gray-500">Connections</p>
          </div>
        </div>

        {/* Zoomable concept map */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Concept Map</h3>
          <Suspense fallback={<div className="h-[520px] flex items-center justify-center text-sm text-gray-400">Loading map…</div>}>
            <ConceptGraphCanvas concepts={viewConcepts} edges={viewEdges} />
          </Suspense>
        </div>

        {viewingDoc && (
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-sm text-gray-600">
            Concepts extracted from this one document. Curation &amp; rebuild apply to the cumulative course graph — switch to <span className="font-medium">Whole course</span> to edit.
          </div>
        )}

        {/* Concept chips - Review & Curate (cumulative course graph only) */}
        {!viewingDoc && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-1">Concept Graph — Review & Curate</h3>
          <p className="text-xs text-gray-500 mb-4 leading-relaxed">
            These concepts were extracted from your materials. Remove anything you don't cover, or add a concept the extractor missed, then <span className="font-medium">Save changes</span> — the saved set is what questions are generated from.
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

          {/* Count footer + Save */}
          <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between gap-3">
            <p className="text-xs text-gray-500">{concepts.length} concept{concepts.length === 1 ? '' : 's'} selected</p>
            <div className="flex items-center gap-3">
              {saveConcepts.isError && (
                <span className="text-xs text-red-600">Couldn't save — try again.</span>
              )}
              {!dirty && saveConcepts.isSuccess && (
                <span className="inline-flex items-center gap-1 text-xs text-green-600"><Check className="w-3.5 h-3.5" /> Saved</span>
              )}
              {dirty && <span className="text-xs text-amber-600">Unsaved changes</span>}
              <button
                onClick={() => saveConcepts.mutate()}
                disabled={!dirty || saveConcepts.isPending || concepts.length === 0}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {saveConcepts.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                {saveConcepts.isPending ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </div>
        </div>
        )}

        {/* Relationships (reflects the active view — course or document) */}
        {viewEdges.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-medium text-gray-700 mb-3">Relationships</h3>
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {viewEdges.slice(0, 15).map((edge: any, i: number) => (
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
      <h3 className="text-lg font-medium text-gray-900 mb-2">No concept graph yet</h3>
      <p className="text-sm text-gray-500 max-w-md mx-auto">
        The concept graph builds automatically after you upload materials. If you've already added materials, you can build it now.
      </p>
      <button
        onClick={() => rebuildMutation.mutate()}
        disabled={rebuildMutation.isPending}
        className="mt-4 inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
      >
        {rebuildMutation.isPending
          ? <><Loader2 className="w-4 h-4 animate-spin" /> Building…</>
          : <><Network className="w-4 h-4" /> Build concept graph</>}
      </button>
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
  const [dropping, setDropping] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: enrolled = [] } = useQuery({
    queryKey: ['course-roster', courseId],
    queryFn: () => listStudents(courseId),
    enabled: !!courseId,
  });

  const handleDrop = async (email: string) => {
    if (!confirm(`Remove ${email} from this course? They'll lose access to its assignments.`)) return;
    setDropping(email);
    setErrors([]);
    try {
      await dropCourseStudent(courseId, email);
      queryClient.invalidateQueries({ queryKey: ['course-roster', courseId] });
    } catch {
      setErrors((e) => [...e, `Could not remove ${email}. Please try again.`]);
    } finally {
      setDropping(null);
    }
  };

  // Reset & reveal: mint a fresh temp password and surface it in the credentials
  // panel (newest first, deduped by email) so a lost password is always recoverable.
  const handleReset = async (email: string) => {
    if (!confirm(`Reset the password for ${email}? Their old temporary password stops working; a new one will be shown to share.`)) return;
    setResetting(email);
    setErrors([]);
    try {
      const res = await resetStudentPassword(email);
      setRoster((prev) => [{ email: res.email, password: res.password }, ...prev.filter((r) => r.email !== res.email)]);
    } catch {
      setErrors((e) => [...e, `Could not reset the password for ${email}.`]);
    } finally {
      setResetting(null);
    }
  };

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
      emails.split(/[\s,;]+/).map((e) => e.trim().toLowerCase()).filter(Boolean)
    ));
    if (list.length === 0) return;
    setBusy(true);
    setErrors([]);
    const added: Roster[] = [];
    const failed: string[] = [];
    let existing = 0;
    let skipped = 0;
    let done = 0;
    // Chunk large rosters so each request stays well under the edge timeout and
    // the professor sees progress; the batch endpoint handles each row resiliently.
    const CHUNK = 25;
    for (let i = 0; i < list.length; i += CHUNK) {
      const chunk = list.slice(i, i + CHUNK);
      try {
        const res = await createStudentsBatch(chunk, courseId);
        for (const r of res.results) {
          if (r.status === 'created' && r.password) added.push({ email: r.email, password: r.password });
          else if (r.status === 'exists') existing += 1;
          else if (r.status === 'skipped') skipped += 1;
          else if (r.status === 'failed') failed.push(`${r.email}: ${r.error || 'failed'}`);
        }
      } catch (err: any) {
        failed.push(...chunk.map((e) => `${e}: ${err?.message || 'request failed'}`));
      }
      done = Math.min(done + chunk.length, list.length);
      if (list.length > CHUNK) setCsvNote(`Provisioning ${done}/${list.length}…`);
    }
    setRoster((prev) => [...added, ...prev]);
    setErrors(failed);
    setEmails('');
    setCsvNote(`Done — ${added.length} new, ${existing} already enrolled${skipped ? `, ${skipped} skipped (already staff)` : ''}, ${failed.length} failed.`);
    queryClient.invalidateQueries({ queryKey: ['course-roster', courseId] });
    queryClient.invalidateQueries({ queryKey: ['course', courseId] });
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

      {enrolled.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Enrolled students ({enrolled.length})</h3>
          <div className="divide-y divide-gray-100 max-h-72 overflow-auto">
            {enrolled.map((s) => (
              <div key={s.email} className="flex items-center gap-3 py-2">
                <span className="flex-1 min-w-0 truncate text-sm text-gray-900">{s.email}</span>
                <button
                  onClick={() => handleReset(s.email)}
                  disabled={resetting === s.email}
                  className="p-1.5 text-blue-600 hover:bg-blue-50 rounded transition-colors disabled:opacity-50"
                  title="Reset & reveal a new temporary password"
                >
                  {resetting === s.email ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
                </button>
                <button
                  onClick={() => handleDrop(s.email)}
                  disabled={dropping === s.email}
                  className="p-1.5 text-red-600 hover:bg-red-50 rounded transition-colors disabled:opacity-50"
                  title="Remove this student from the course"
                >
                  {dropping === s.email ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {roster.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium text-gray-700">Credentials to share</h3>
              <p className="text-xs text-amber-600 mt-0.5">
                Copy and share these now. They stay here until you dismiss them — and you can always
                Reset a student (key icon) to reveal a fresh password. Students sign in at {loginUrl}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                onClick={copyAll}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
              >
                <Copy className="w-3.5 h-3.5" /> Copy all
              </button>
              <button
                onClick={() => setRoster([])}
                className="text-xs px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
              >
                Dismiss
              </button>
            </div>
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

// ── Performance tab: anonymized class practice analytics ─────────────────────
const _pct = (x: number) => `${Math.round((x || 0) * 100)}%`;
const ASPECT_COLOR: Record<string, string> = {
  recall: 'bg-blue-500', application: 'bg-purple-500', depth: 'bg-amber-500', authenticity: 'bg-green-500',
};

function PerformanceTab({ courseId }: { courseId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['course-performance', courseId],
    queryFn: () => getCoursePerformance(courseId),
    enabled: !!courseId,
  });

  if (isLoading) {
    return <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-sm text-gray-500">Loading performance…</div>;
  }

  const n = data?.practice_takers ?? 0;
  if (!data || n === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
        <BarChart3 className="w-12 h-12 text-gray-300 mx-auto mb-4" />
        <h3 className="text-lg font-medium text-gray-900 mb-2">No practice attempts yet</h3>
        <p className="text-sm text-gray-500 max-w-md mx-auto">
          Once students complete a <span className="font-medium">practice test</span>, this shows anonymized class
          performance — topic coverage and how many students reach recall, application, and in-depth understanding.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Class practice performance</h3>
        <span className="text-xs text-gray-500">Anonymized · n = {n} student{n === 1 ? '' : 's'}</span>
      </div>

      {/* Cognitive aspects */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h4 className="text-xs font-bold uppercase tracking-wide text-gray-400 mb-1">Cognitive aspects</h4>
        <p className="text-xs text-gray-500 mb-4">Bar = share of students reaching the aspect; number is the class average.</p>
        <div className="space-y-4">
          {data.aspects.map((a) => (
            <div key={a.key}>
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium text-gray-800">{a.label}</span>
                <span className="text-xs text-gray-500">{_pct(a.pct_students)} of students · avg {_pct(a.avg_score)}</span>
              </div>
              <p className="text-xs text-gray-400 mb-1">{a.description}</p>
              <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${ASPECT_COLOR[a.key] ?? 'bg-blue-500'}`} style={{ width: _pct(a.pct_students) }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Topic coverage */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h4 className="text-xs font-bold uppercase tracking-wide text-gray-400 mb-1">Topic coverage</h4>
        <p className="text-xs text-gray-500 mb-4">% of students who demonstrated each topic on the practice test.</p>
        {data.topics.length === 0 ? (
          <p className="text-sm text-gray-400">No graded topics yet.</p>
        ) : (
          <div className="space-y-3">
            {data.topics.map((t) => (
              <div key={t.label} className="flex items-center gap-3">
                <span className="text-sm text-gray-700 w-44 flex-shrink-0 truncate" title={t.label}>{t.label}</span>
                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-blue-500" style={{ width: _pct(t.pct_students) }} />
                </div>
                <span className="text-xs font-mono text-gray-500 w-10 text-right">{_pct(t.pct_students)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-gray-400 leading-relaxed">
        Aspects reflect the reasoning captured per answer: <span className="font-medium">Recall</span> = concepts named,
        <span className="font-medium"> Application</span> = causal links / case scenarios,
        <span className="font-medium"> In-depth</span> = novel insight,
        <span className="font-medium"> Authenticity</span> = genuine reasoning. Mastery bar = {_pct(data.bar)}. All figures
        are aggregated across students — no individual results are shown.
      </p>
    </div>
  );
}

// Dedicated, simple "Upload Syllabus" shown in place of a locked tab until the
// course has a syllabus. Drop a document -> it's stored, its text is read, and
// class sessions with their topics are created from it. No material-upload chrome.
function SyllabusGate({ courseId, courseName }: { courseId: string; courseName: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  type Phase = 'idle' | 'uploading' | 'reading' | 'creating' | 'done' | 'error';
  const [phase, setPhase] = useState<Phase>('idle');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [created, setCreated] = useState<ProcessedSession[]>([]);
  const [showPaste, setShowPaste] = useState(false);
  const [paste, setPaste] = useState('');

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  const runProcess = async (text?: string) => {
    setPhase('creating');
    setError('');
    try {
      const res = await processSyllabus(courseId, text);
      setCreated(res.sessions || []);
      setPhase('done');
      queryClient.invalidateQueries({ queryKey: ['course-sessions', courseId] });
    } catch (e) {
      setError((e as Error).message || 'Could not create sessions from the syllabus.');
      setShowPaste(true);
      setPhase('error');
    }
  };

  const handleFiles = async (files: File[]) => {
    const file = files[0];
    if (!file) return;
    setError(''); setProgress(0); setPhase('uploading'); setShowPaste(false);
    try {
      const res = await uploadMaterial(DEFAULT_ORG, courseName, file, setProgress,
        undefined, undefined, undefined, true /* isSyllabus */);
      await setSyllabus(courseId, {
        material_id: res.material_id, material_version_id: res.material_version_id, file_name: file.name,
      });
      // The syllabus is attached now — unlock the course tabs.
      queryClient.invalidateQueries({ queryKey: ['syllabus', courseId] });
      // Wait for the text to be extracted, then create sessions.
      setPhase('reading');
      let status = '';
      for (let i = 0; i < 40; i++) {
        const vers = await listVersions(DEFAULT_ORG, res.material_id);
        const v = vers.find((x) => x.material_version_id === res.material_version_id);
        status = v?.status || '';
        if (status === 'ready' || status === 'failed') break;
        await sleep(2000);
      }
      if (status === 'ready') {
        await runProcess();
      } else {
        setError(status === 'failed'
          ? "We couldn't read text from that file (it may be a scanned/image-only PDF). Paste the schedule below to create sessions."
          : "Your syllabus is uploaded, but reading it is taking a while. Paste the schedule below, or come back and use “Auto-create sessions” on the Sessions tab.");
        setShowPaste(true);
        setPhase('error');
      }
    } catch (e) {
      setError((e as Error).message || 'Upload failed. Please try again.');
      setPhase('error');
    }
  };

  const busy = phase === 'uploading' || phase === 'reading' || phase === 'creating';
  const statusText = phase === 'uploading' ? 'Uploading syllabus…'
    : phase === 'reading' ? 'Reading your syllabus…'
    : phase === 'creating' ? 'Creating class sessions…' : '';

  if (phase === 'done') {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 max-w-2xl mx-auto">
        <div className="text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 bg-green-100 rounded-full mb-4">
            <CheckCircle2 className="w-7 h-7 text-green-600" />
          </div>
          <h2 className="text-xl font-bold text-gray-900">Syllabus processed</h2>
          <p className="text-sm text-gray-500 mt-1">Created {created.length} class session{created.length !== 1 ? 's' : ''} with their topics. Your course is unlocked.</p>
        </div>
        <div className="mt-5 space-y-2 max-h-72 overflow-y-auto">
          {created.map((s) => (
            <div key={s.session_id} className="border border-gray-100 rounded-lg p-3">
              <div className="flex items-baseline gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-blue-600">{s.week}</span>
                {s.session_date && <span className="text-xs text-gray-400 font-mono">{s.session_date}</span>}
              </div>
              <p className="text-sm font-medium text-gray-800">{s.title}</p>
              {s.in_scope_concepts?.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-1.5">
                  {s.in_scope_concepts.map((t, i) => (
                    <span key={i} className="text-xs bg-blue-50 text-blue-700 border border-blue-100 rounded px-2 py-0.5">{t}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-6 text-center">
          <button
            onClick={() => { queryClient.invalidateQueries({ queryKey: ['syllabus', courseId] }); navigate(`/professor/courses/${courseId}?tab=sessions`); }}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            Go to your course →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-8 max-w-2xl mx-auto">
      <div className="text-center">
        <div className="inline-flex items-center justify-center w-14 h-14 bg-blue-100 rounded-full mb-4">
          <BookOpen className="w-7 h-7 text-blue-600" />
        </div>
        <h2 className="text-xl font-bold text-gray-900">Upload your course syllabus</h2>
        <p className="text-sm text-gray-500 mt-2 max-w-md mx-auto">
          Drop your syllabus and we'll read its weekly schedule and create your class sessions with their topics. Everything else unlocks once it's added.
        </p>
      </div>

      <div className="mt-6">
        {busy ? (
          <div className="flex flex-col items-center justify-center gap-3 border-2 border-dashed border-blue-200 rounded-xl p-8 bg-blue-50/40">
            <Loader2 className="w-6 h-6 text-blue-600 animate-spin" />
            <p className="text-sm font-medium text-gray-700">{statusText}</p>
            {phase === 'uploading' && (
              <div className="w-56 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                <div className="h-full bg-blue-600 transition-all" style={{ width: `${progress}%` }} />
              </div>
            )}
          </div>
        ) : (
          <FileUpload accept=".pdf,.docx,.doc,.rtf,.txt,.pptx,.md" onFilesSelected={handleFiles} />
        )}
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {showPaste && (
        <div className="mt-3">
          <textarea
            value={paste}
            onChange={(e) => setPaste(e.target.value)}
            rows={6}
            placeholder={'Paste your class schedule, e.g.\nWeek 1 (Sep 3): Intro; supervised learning\nWeek 2 (Sep 10): Linear models; loss; gradient descent'}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={() => runProcess(paste.trim())}
            disabled={!paste.trim() || phase === 'creating'}
            className="mt-2 inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {phase === 'creating' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            Create sessions from pasted schedule
          </button>
        </div>
      )}

      <p className="text-xs text-gray-400 mt-5 text-center">PDF, DOCX, TXT, or PPTX. You can manage students while the syllabus is being set up.</p>
    </div>
  );
}

// Per-session in-scope topic picker (P-S-2.1). Selection persists via updateSession.
function SessionScopeEditor({
  session, concepts, saving, onSave,
}: {
  session: ClassSession;
  concepts: Array<{ id: string; label: string }>;
  saving: boolean;
  onSave: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set(session.in_scope_concepts ?? []));
  const count = session.in_scope_concepts?.length ?? 0;

  // Re-seed from server state whenever it changes (e.g. after a save refetch).
  useEffect(() => { setSel(new Set(session.in_scope_concepts ?? [])); }, [session.in_scope_concepts]);

  const toggle = (id: string) =>
    setSel((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 font-medium"
      >
        <Network className="w-3.5 h-3.5" />
        Topics in scope{count > 0 ? ` (${count})` : ' — not set'}
      </button>
      {open && (
        <div className="mt-2 border border-gray-200 rounded-lg p-3 bg-gray-50">
          {concepts.length === 0 ? (
            <p className="text-xs text-gray-400">No concept graph yet — upload materials and let the graph build first.</p>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-500">{sel.size} of {concepts.length} topics selected</span>
                <div className="flex gap-2">
                  <button onClick={() => setSel(new Set(concepts.map((c) => c.id)))} className="text-xs text-blue-600 hover:underline">All</button>
                  <button onClick={() => setSel(new Set())} className="text-xs text-blue-600 hover:underline">None</button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto grid grid-cols-1 sm:grid-cols-2 gap-1 pr-1">
                {concepts.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-xs text-gray-700 cursor-pointer py-0.5">
                    <input
                      type="checkbox"
                      checked={sel.has(c.id)}
                      onChange={() => toggle(c.id)}
                      className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="truncate">{c.label}</span>
                  </label>
                ))}
              </div>
              <div className="flex items-center gap-2 mt-3">
                <button
                  onClick={() => onSave([...sel])}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded text-xs font-medium hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving && <Loader2 className="w-3 h-3 animate-spin" />}
                  Save scope
                </button>
                <span className="text-[11px] text-gray-400">Exams generated for this week draw only from the selected topics.</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Sessions tab: a course maps to N class sessions ──────────────────────────
function SessionsTab({ courseId, courseName, hasSyllabus }: { courseId: string; courseName: string; hasSyllabus?: boolean }) {
  const queryClient = useQueryClient();
  const [date, setDate] = useState('');
  const [doc, setDoc] = useState('');
  const [error, setError] = useState('');
  const [paste, setPaste] = useState('');
  const [showPaste, setShowPaste] = useState(false);
  const [genError, setGenError] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['course-sessions', courseId],
    queryFn: () => listSessions(courseId),
    enabled: !!courseId,
  });
  const sessions: ClassSession[] = data?.sessions ?? [];

  // Concept-graph nodes for the in-scope topic picker (P-S-2.1).
  const { data: graph } = useQuery<{ concepts?: Array<{ id?: string; label?: string }> }>({
    queryKey: ['course-graph-concepts', courseId],
    queryFn: () => get(`/api/courses/${courseId}/graph`),
    enabled: !!courseId,
  });
  const concepts = (graph?.concepts ?? [])
    .map((c) => ({ id: c.id || c.label || '', label: c.label || c.id || '' }))
    .filter((c) => c.id);

  const addMutation = useMutation({
    mutationFn: () => createSession(courseId, { session_date: date || null, session_document: doc.trim() || null }),
    onSuccess: () => {
      setDate(''); setDoc(''); setError('');
      queryClient.invalidateQueries({ queryKey: ['course-sessions', courseId] });
    },
    onError: (e: Error) => setError(e.message || 'Could not add the session. Please try again.'),
  });

  const removeMutation = useMutation({
    mutationFn: (sessionId: string) => deleteSession(courseId, sessionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['course-sessions', courseId] }),
  });

  const scopeMutation = useMutation({
    mutationFn: ({ s, ids }: { s: ClassSession; ids: string[] }) =>
      updateSession(courseId, s.session_id, {
        session_date: s.session_date, session_document: s.session_document, in_scope_concepts: ids,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['course-sessions', courseId] }),
  });

  // Auto-create sessions from the syllabus. Tries the stored syllabus text; if
  // that isn't ready (or has no schedule) it reveals a paste box as a fallback.
  const genMutation = useMutation({
    mutationFn: (text?: string) => processSyllabus(courseId, text),
    onSuccess: (res) => {
      setGenError(''); setShowPaste(false); setPaste('');
      if (res.status === 'exists') setGenError(res.message || 'This course already has sessions.');
      queryClient.invalidateQueries({ queryKey: ['course-sessions', courseId] });
    },
    onError: (e: Error) => { setGenError(e.message || 'Could not generate sessions.'); setShowPaste(true); },
  });

  return (
    <div className="space-y-4">
      {/* Auto-create from syllabus — the ingestion engine, wired into the flow */}
      {hasSyllabus && sessions.length === 0 && (
        <div className="bg-gradient-to-br from-blue-50 to-indigo-50 rounded-xl border border-blue-100 p-5">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-lg bg-blue-600 text-white grid place-items-center flex-shrink-0">
              <Sparkles className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-gray-900">Auto-create sessions from your syllabus</h3>
              <p className="text-xs text-gray-600 mt-0.5">The engine reads your syllabus's weekly schedule and creates one class session per week with its topics mapped as the in-scope set.</p>
              {genError && <p className="text-xs text-amber-700 mt-2">{genError}</p>}
              {showPaste && (
                <textarea
                  value={paste}
                  onChange={(e) => setPaste(e.target.value)}
                  rows={5}
                  placeholder={'Paste your class schedule, e.g.\nWeek 1 (Sep 3): Intro; supervised learning\nWeek 2 (Sep 10): Linear models; loss; gradient descent'}
                  className="w-full mt-2 px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              )}
              <div className="mt-3">
                <button
                  onClick={() => genMutation.mutate(showPaste ? paste.trim() : undefined)}
                  disabled={genMutation.isPending || (showPaste && !paste.trim())}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
                >
                  {genMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                  {showPaste ? 'Generate from pasted schedule' : 'Generate sessions'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Add a session */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-medium text-gray-700 mb-1">Add a class session</h3>
        <p className="text-xs text-gray-500 mb-4">A session has an optional date and a document (paste notes, an outline, or a link).</p>
        <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Date (optional)</label>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Session document</label>
            <textarea
              value={doc}
              onChange={(e) => setDoc(e.target.value)}
              rows={3}
              placeholder="Session notes, outline, or a link…"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>
        {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
        <div className="mt-3">
          <button
            onClick={() => addMutation.mutate()}
            disabled={addMutation.isPending || (!date && !doc.trim())}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {addMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Add session
          </button>
        </div>
      </div>

      {/* Existing sessions */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-medium text-gray-700 mb-3">Sessions ({sessions.length})</h3>
        {isLoading ? (
          <p className="text-sm text-gray-400">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-gray-400">No sessions yet.</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {sessions.map((s) => (
              <div key={s.session_id} className="flex items-start gap-3 py-3">
                <Calendar className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900">
                    {s.session_date ? new Date(s.session_date + 'T00:00:00').toLocaleDateString() : 'No date'}
                  </p>
                  {s.session_document && (
                    <p className="text-sm text-gray-600 mt-0.5 whitespace-pre-wrap break-words line-clamp-3">{s.session_document}</p>
                  )}
                  {(s.materials?.length ?? 0) > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {s.materials!.map((m) => (
                        <span key={m.material_id} className="inline-flex items-center gap-1 text-xs bg-gray-100 text-gray-700 rounded px-2 py-0.5">
                          <FileText className="w-3 h-3 flex-shrink-0" /> <span className="truncate max-w-[180px]">{m.display_name}</span>
                        </span>
                      ))}
                    </div>
                  )}
                  <Link
                    to={`/professor/upload?course=${encodeURIComponent(courseName)}&courseId=${courseId}&sessionId=${s.session_id}`}
                    className="mt-2 inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium"
                  >
                    <Upload className="w-3.5 h-3.5" /> Upload file to this session
                  </Link>
                  <SessionScopeEditor
                    session={s}
                    concepts={concepts}
                    saving={scopeMutation.isPending}
                    onSave={(ids) => scopeMutation.mutate({ s, ids })}
                  />
                </div>
                <button
                  onClick={() => { if (confirm('Delete this session? Its attached files are detached, not deleted.')) removeMutation.mutate(s.session_id); }}
                  disabled={removeMutation.isPending}
                  className="p-1.5 text-red-600 hover:bg-red-50 rounded transition-colors disabled:opacity-50"
                  title="Delete session"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
