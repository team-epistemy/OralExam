import { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { useParams, Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Network, ClipboardList, Upload, Trash2, AlertTriangle, Eye, Loader2, Users, Copy } from 'lucide-react';
import { get, post, del } from '../../api/client';
import type { Material } from '../../api/materials';
import { listMaterials } from '../../api/materials';
import { createStudentsBatch } from '../../api/students';
import { listStudents } from '../../api/courses';
import DocumentViewerModal from '../../components/DocumentViewerModal';

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

type Tab = 'materials' | 'graph' | 'assignments' | 'students';

export default function CourseDetail() {
  const { courseId } = useParams<{ courseId: string }>();
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<Tab>(
    (['materials', 'graph', 'assignments', 'students'].includes(tabParam || '')
      ? (tabParam as Tab)
      : 'materials'),
  );

  // Keep the active tab in sync with ?tab= so the left-nav "Add Students" link
  // switches to the Students tab even when the course page is already mounted.
  useEffect(() => {
    const t = searchParams.get('tab');
    if (t && ['materials', 'graph', 'assignments', 'students'].includes(t)) {
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

  const { data: assignments = [] } = useQuery({
    queryKey: ['assignments', courseId],
    queryFn: () => listAssignments(courseId!),
    enabled: !!courseId,
  });


  const tabs = [
    { id: 'materials' as Tab, label: 'Materials', icon: FileText },
    { id: 'graph' as Tab, label: 'Concept Graph', icon: Network },
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
        <MaterialsTab materials={materials} courseId={courseId!} courseName={course?.name || ''} queryClient={queryClient} />
      )}
      {activeTab === 'graph' && <GraphTab courseId={courseId!} />}
      {activeTab === 'assignments' && <AssignmentsTab assignments={assignments} courseId={courseId!} queryClient={queryClient} />}
      {activeTab === 'students' && <StudentsTab courseId={courseId!} />}
    </div>
  );
}

function MaterialsTab({ materials, courseId, courseName, queryClient }: { materials: Material[]; courseId: string; courseName: string; queryClient: ReturnType<typeof useQueryClient> }) {
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
  const queryClient = useQueryClient();

  const { data: enrolled = [] } = useQuery({
    queryKey: ['course-roster', courseId],
    queryFn: () => listStudents(courseId),
    enabled: !!courseId,
  });

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
              <div key={s.email} className="py-2 text-sm text-gray-900 truncate">{s.email}</div>
            ))}
          </div>
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
