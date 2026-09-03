import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, CheckCircle, GraduationCap, Copy, Check, ChevronLeft, Pencil, Trash2, RefreshCw, Eye, AlertTriangle, Network } from 'lucide-react';
import { get, post } from '../../api/client';
import { buildExam, regenerateExam, assignExam, discardDraft, type ExamVariantQuestion, type AssignmentType } from '../../api/exam';
import { listSessions } from '../../api/sessions';
import TakeExam from '../student/TakeExam';

interface Course {
  course_id: string;
  course_name: string;
}

type Difficulty = 'recall' | 'balanced' | 'deep';

const DIFFICULTY_LABEL: Record<Difficulty, string> = {
  recall: 'Recall',
  balanced: 'Balanced',
  deep: 'Deep Reasoning',
};

export default function CreateAssignment() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const preselectedCourse = searchParams.get('course') || '';

  const [courseId, setCourseId] = useState(preselectedCourse);
  const [title, setTitle] = useState('');
  const [qCount, setQCount] = useState(8);
  const [duration, setDuration] = useState(30);
  const [difficulty, setDifficulty] = useState<Difficulty>('balanced');
  const [assignmentType, setAssignmentType] = useState<AssignmentType>('assignment');
  const [weekSessionId, setWeekSessionId] = useState('');   // scope to a class session (week)
  // Per-assignment topic override: null = follow the week's saved scope; an
  // array = this assignment's own picks (does NOT write back to the session).
  const [topicOverride, setTopicOverride] = useState<string[] | null>(null);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [includeCase, setIncludeCase] = useState(false);
  const [building, setBuilding] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [created, setCreated] = useState(false);
  const [createdData, setCreatedData] = useState<{ assignment_id: string; question_count: number } | null>(null);
  const [builtQuestions, setBuiltQuestions] = useState<ExamVariantQuestion[]>([]);
  // Preview/curation state: the built questions the professor reviews (edit /
  // remove) before publishing. Empty until a preview is generated (P-S-3.1/3.4).
  const [previewQuestions, setPreviewQuestions] = useState<ExamVariantQuestion[]>([]);
  const [inPreview, setInPreview] = useState(false);
  const [needsRebuild, setNeedsRebuild] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildFired, setRebuildFired] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState('');
  const [linkCopied, setLinkCopied] = useState(false);
  const [draftAssignmentId, setDraftAssignmentId] = useState<string | null>(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [error, setError] = useState('');

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<Course[]>('/api/professor/courses'),
  });

  // Questions are assembled from the concept graph, which builds automatically
  // (and asynchronously) after materials are uploaded. Poll until it's ready so
  // we only enable assignment creation once there's something to assemble from.
  const { data: graph } = useQuery<{ node_count?: number; concepts?: Array<{ id?: string; label?: string }> }>({
    queryKey: ['assignment-graph', courseId],
    queryFn: () => get(`/api/courses/${courseId}/graph`),
    enabled: !!courseId && !created,
    refetchInterval: (q) => ((q.state.data?.node_count || 0) > 0 ? false : 6000),
  });
  const conceptCount = graph?.node_count || 0;
  const graphReady = conceptCount > 0;
  const maxQuestions = Math.min(50, conceptCount * 4);
  // Concept nodes (id + label) for the topic picker.
  const allConcepts = (graph?.concepts ?? [])
    .map((c) => ({ id: c.id || c.label || '', label: c.label || c.id || '' }))
    .filter((c) => c.id);

  // Class sessions (weeks) for optional scoping. A session with an in-scope
  // concept set constrains generation to just those topics (P-S-2.2).
  const { data: sessionsData } = useQuery({
    queryKey: ['course-sessions', courseId],
    queryFn: () => listSessions(courseId),
    enabled: !!courseId && !created,
  });
  const sessions = sessionsData?.sessions ?? [];
  const selectedWeek = sessions.find((s) => s.session_id === weekSessionId);
  const weekConcepts = selectedWeek?.in_scope_concepts ?? [];
  // Effective scope for THIS assignment: the professor's override if they made
  // one, else the selected week's saved scope. Empty ⇒ whole graph.
  const effectiveConcepts = topicOverride ?? weekConcepts;
  const hasScope = effectiveConcepts.length > 0;
  const isOverridden = topicOverride !== null;

  // A new week starts from that week's saved scope — drop any prior override.
  useEffect(() => { setTopicOverride(null); }, [weekSessionId]);

  const toggleConcept = (id: string) => {
    const next = new Set(effectiveConcepts);
    next.has(id) ? next.delete(id) : next.add(id);
    setTopicOverride([...next]);
  };

  const courseName = courses.find((c) => c.course_id === courseId)?.course_name || '';
  const backTo = courseId ? `/professor/courses/${courseId}?tab=assignments` : '/professor/dashboard';
  const canSubmit = !!courseId && !!title.trim() && qCount >= 1 && graphReady;

  // Step 1: assemble questions from the concept graph and show them for review —
  // nothing is published to students yet (P-S-3.1: preview before publishing).
  const handleBuild = async () => {
    if (!canSubmit) return;
    setBuilding(true);
    setError('');
    setRebuildFired(false);
    try {
      const built = await buildExam(courseId, {
        q_count: qCount, exam_len: duration, difficulty,
        // Scope to this assignment's effective topics, if any — else the whole graph.
        concept_ids: hasScope ? effectiveConcepts : undefined,
      });
      if (built.status !== 'completed' || !built.variants?.length) {
        throw new Error(built.message || 'Could not build questions — make sure this course has a concept graph (upload materials first).');
      }
      const questions = built.variants[0].questions || [];
      if (questions.length === 0) {
        throw new Error('No questions could be assembled from the concept graph yet. Upload materials and let the graph build, then try again.');
      }
      setPreviewQuestions(questions.map((q) => ({ ...q })));
      setNeedsRebuild(!!built.needs_rebuild);
      setEditIdx(null);
      setInPreview(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate preview');
    } finally {
      setBuilding(false);
    }
  };

  // Step 2: publish only the curated questions. Removed questions were never
  // sent, so they can't reappear in the student's session (P-S-3.4).
  const handlePublish = async () => {
    if (previewQuestions.length === 0) {
      setError('Add at least one question before publishing.');
      return;
    }
    setPublishing(true);
    setError('');
    try {
      const res = await assignExam(courseId, {
        title: title.trim(),
        questions: previewQuestions,
        difficulty,
        duration_minutes: duration,
        assignment_type: assignmentType,
        include_case: includeCase,
        // Snapshot the week + this assignment's effective scope so the exam stays
        // attributed to the scope in effect now, even if the week changes later (P-S-2.3).
        session_id: weekSessionId || undefined,
        scope_concepts: hasScope ? effectiveConcepts : undefined,
      });
      if (res.status !== 'completed' || !res.assignment_id) {
        throw new Error(res.message || 'Failed to create assignment.');
      }
      setBuiltQuestions(previewQuestions);
      setCreatedData({ assignment_id: res.assignment_id, question_count: res.question_count ?? previewQuestions.length });
      setCreated(true);
      setInPreview(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish assignment');
    } finally {
      setPublishing(false);
    }
  };

  // Create a draft from the current curated questions and open the student dry-run.
  const handleDryRun = async () => {
    if (previewQuestions.length === 0) { setError('Add at least one question first.'); return; }
    setError('');
    try {
      if (draftAssignmentId) { await discardDraft(draftAssignmentId).catch(() => {}); }  // replace any stale draft
      const res = await assignExam(courseId, {
        title: title.trim() || 'Untitled',
        questions: previewQuestions,
        difficulty,
        duration_minutes: duration,
        assignment_type: assignmentType,
        include_case: includeCase,
        session_id: weekSessionId || undefined,
        scope_concepts: hasScope ? effectiveConcepts : undefined,
        draft: true,
      });
      if (!res.assignment_id) throw new Error(res.message || 'Could not start preview.');
      setDraftAssignmentId(res.assignment_id);
      setDryRunOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start preview.');
    }
  };

  // Exit the overlay. If the draft still exists (not published), auto-discard it.
  const handleDryRunExit = async () => {
    setDryRunOpen(false);
    const id = draftAssignmentId;
    setDraftAssignmentId(null);
    if (id) { await discardDraft(id).catch(() => {}); }  // no-op if already published
  };

  // Author real (case-based) questions: kicks off the async concept-graph
  // rebuild. Runs in the background (~a minute); the professor regenerates the
  // preview once it's done.
  const rebuildGraph = async () => {
    if (!courseId) return;
    setRebuilding(true);
    try {
      await post(`/api/courses/${courseId}/graph/rebuild`, { domain: 'general' });
      setRebuildFired(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start the graph rebuild.');
    } finally {
      setRebuilding(false);
    }
  };

  // Regenerate = author a fresh set with the LLM (not a reshuffle of the stored
  // bank), at the current difficulty and week scope.
  const handleRegenerate = async () => {
    setRegenerating(true);
    setError('');
    try {
      const res = await regenerateExam(courseId, {
        q_count: qCount, exam_len: duration, difficulty,
        concept_ids: hasScope ? effectiveConcepts : undefined,
      });
      if (res.status !== 'completed' || !res.variants?.length) {
        throw new Error(res.message || 'Could not regenerate questions.');
      }
      setPreviewQuestions((res.variants[0].questions || []).map((q) => ({ ...q })));
      setEditIdx(null);
      setNeedsRebuild(false);   // these are freshly authored, not templates
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not regenerate questions.');
    } finally {
      setRegenerating(false);
    }
  };

  const removeQuestion = (i: number) => {
    setPreviewQuestions((qs) => qs.filter((_, idx) => idx !== i));
    setEditIdx(null);
  };
  const startEdit = (i: number) => { setEditIdx(i); setEditDraft(previewQuestions[i].q); };
  const saveEdit = () => {
    if (editIdx === null) return;
    const text = editDraft.trim();
    if (!text) return;
    setPreviewQuestions((qs) => qs.map((q, idx) => (idx === editIdx ? { ...q, q: text } : q)));
    setEditIdx(null);
  };

  const resetForm = () => {
    setCreated(false);
    setCreatedData(null);
    setBuiltQuestions([]);
    setPreviewQuestions([]);
    setInPreview(false);
    setEditIdx(null);
    setTitle('');
    setLinkCopied(false);
    setError('');
  };

  const copyLink = (assignmentId: string) => {
    navigator.clipboard.writeText(`${window.location.origin}/student/exam/${assignmentId}`);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const Breadcrumb = () => (
    <Link to={backTo} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors">
      <ChevronLeft className="w-4 h-4" />
      {courseName ? `${courseName} · Assignments` : 'Back to dashboard'}
    </Link>
  );

  // ── Success screen ──────────────────────────────────────────────────────────
  if (created && createdData) {
    const studentLink = `${window.location.origin}/student/exam/${createdData.assignment_id}`;
    const byTopic = builtQuestions.reduce<Record<string, string[]>>((acc, q) => {
      const t = q.topic || 'General';
      (acc[t] ||= []).push(q.q);
      return acc;
    }, {});

    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <Breadcrumb />
        <div className="text-center pt-2">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-green-100 rounded-full mb-4">
            <GraduationCap className="w-8 h-8 text-green-600" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Assignment Ready</h1>
          <p className="text-sm text-gray-500 mt-2">
            <span className="font-semibold text-gray-700">{title}</span> is saved and ready for students.
          </p>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Summary</h2>
          <div className="space-y-3">
            {[
              ['Course', courseName || courseId],
              ['Assignment', title],
              ['Questions', String(createdData.question_count)],
              ['Duration', `${duration} minutes`],
              ['Difficulty', DIFFICULTY_LABEL[difficulty]],
            ].map(([k, v], i, arr) => (
              <div key={k} className={`flex justify-between items-center py-2 ${i < arr.length - 1 ? 'border-b border-gray-100' : ''}`}>
                <span className="text-sm text-gray-600">{k}</span>
                <span className="text-sm font-semibold text-gray-900">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Student Exam Link</h2>
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
              <code className="text-sm text-gray-700 break-all">{studentLink}</code>
            </div>
            <button
              onClick={() => copyLink(createdData.assignment_id)}
              className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors whitespace-nowrap"
            >
              {linkCopied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              {linkCopied ? 'Copied!' : 'Copy Link'}
            </button>
          </div>
        </div>

        {Object.keys(byTopic).length > 0 && (
          <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Questions by Topic</h2>
            {Object.entries(byTopic).map(([topic, qs]) => (
              <div key={topic}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-blue-600 bg-blue-50 px-2 py-0.5 rounded">{topic}</span>
                  <span className="text-xs text-gray-400">{qs.length} question{qs.length !== 1 ? 's' : ''}</span>
                </div>
                <div className="space-y-2">
                  {qs.map((q, i) => (
                    <div key={i} className="flex items-start gap-2 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
                      <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 flex-shrink-0" />
                      <span className="text-sm text-gray-800">{q}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap gap-3 justify-center pb-8">
          <button onClick={resetForm} className="px-5 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors">
            Create Another
          </button>
          <button onClick={() => navigate(`/professor/assignments/${createdData.assignment_id}/grades`)} className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
            View Grades →
          </button>
          <button onClick={() => navigate(backTo)} className="px-5 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors">
            Back to Course
          </button>
        </div>
      </div>
    );
  }

  // ── Preview & curate ────────────────────────────────────────────────────────
  if (inPreview) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <Breadcrumb />
        <div className="flex items-start gap-3">
          <div className="inline-flex items-center justify-center w-10 h-10 bg-blue-100 rounded-lg flex-shrink-0">
            <Eye className="w-5 h-5 text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Preview &amp; review</h1>
            <p className="text-sm text-gray-500 mt-1">
              This is the full sample session students will get. Edit or remove any question — nothing is published until you press <span className="font-medium text-gray-700">Publish to Students</span>.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-between bg-white rounded-xl border border-gray-200 px-5 py-3">
          <div className="text-sm text-gray-600">
            <span className="font-semibold text-gray-900">{title || 'Untitled'}</span> · {previewQuestions.length} question{previewQuestions.length !== 1 ? 's' : ''} · {duration} min · {DIFFICULTY_LABEL[difficulty]}
          </div>
          <button
            onClick={handleRegenerate}
            disabled={regenerating || building}
            title="Author a fresh set of questions with AI"
            className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50"
          >
            {regenerating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {regenerating ? 'Authoring fresh questions…' : 'Regenerate'}
          </button>
        </div>

        {needsRebuild && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-amber-900">These are placeholder questions</p>
                <p className="text-xs text-amber-800 mt-0.5">
                  This course's concept graph doesn't have authored questions yet, so generic templates were used. Rebuild the graph to author real, case-based questions from your materials — then regenerate the preview.
                </p>
                {rebuildFired ? (
                  <p className="text-xs text-amber-800 mt-2 inline-flex items-center gap-1.5">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Rebuilding in the background (~a minute). Press <span className="font-medium">Regenerate</span> once it's done.
                  </p>
                ) : (
                  <button
                    onClick={rebuildGraph}
                    disabled={rebuilding}
                    className="mt-2 inline-flex items-center gap-2 px-3 py-1.5 bg-amber-600 text-white rounded-lg text-xs font-medium hover:bg-amber-700 disabled:opacity-50 transition-colors"
                  >
                    {rebuilding ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Network className="w-3.5 h-3.5" />}
                    Rebuild concept graph
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        <div className="space-y-3">
          {previewQuestions.map((q, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-200 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3 min-w-0">
                  <span className="text-sm font-bold text-blue-600 mt-0.5">Q{i + 1}</span>
                  <div className="min-w-0">
                    {q.topic && <span className="inline-block text-[11px] font-semibold uppercase tracking-wide text-blue-600 bg-blue-50 px-2 py-0.5 rounded mb-1">{q.topic}</span>}
                    {editIdx === i ? (
                      <textarea
                        value={editDraft}
                        onChange={(e) => setEditDraft(e.target.value)}
                        rows={3}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        autoFocus
                      />
                    ) : (
                      <p className="text-sm text-gray-800">{q.q}</p>
                    )}
                  </div>
                </div>
                {editIdx !== i && (
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button onClick={() => startEdit(i)} title="Edit question" className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors">
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button onClick={() => removeQuestion(i)} title="Remove question" className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
              {editIdx === i && (
                <div className="flex items-center gap-2 mt-2 pl-9">
                  <button onClick={saveEdit} className="px-3 py-1 bg-blue-600 text-white rounded text-xs font-medium hover:bg-blue-700">Save</button>
                  <button onClick={() => setEditIdx(null)} className="px-3 py-1 border border-gray-300 text-gray-700 rounded text-xs font-medium hover:bg-gray-50">Cancel</button>
                </div>
              )}
            </div>
          ))}
          {previewQuestions.length === 0 && (
            <div className="bg-white rounded-xl border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">
              All questions removed. Regenerate to build a fresh set, or go back to change the settings.
            </div>
          )}
        </div>

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
        )}

        <div className="flex flex-wrap gap-3 justify-between pb-8">
          <button
            onClick={() => { setInPreview(false); setError(''); }}
            className="px-5 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            ← Back to settings
          </button>
          <button
            onClick={handleDryRun}
            disabled={previewQuestions.length === 0}
            className="inline-flex items-center gap-2 px-5 py-2.5 border border-blue-300 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-50 disabled:opacity-50"
          >
            Take it as a student
          </button>
          <button
            onClick={handlePublish}
            disabled={publishing || previewQuestions.length === 0}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {publishing && <Loader2 className="w-4 h-4 animate-spin" />}
            {publishing ? 'Publishing…' : 'Publish to Students'}
          </button>
        </div>

        {dryRunOpen && draftAssignmentId && (
          <div className="fixed inset-0 z-50 bg-white overflow-auto p-4">
            <TakeExam assignmentId={draftAssignmentId} preview onExit={handleDryRunExit} />
          </div>
        )}
      </div>
    );
  }

  // ── Config form ─────────────────────────────────────────────────────────────
  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <Breadcrumb />
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Create Assignment</h1>
        <p className="text-sm text-gray-500 mt-1">
          Pick how many questions you want — they're assembled from this course's concept graph and turned into an oral exam.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-6">
        {/* Course */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Course</label>
          <select
            value={courseId}
            onChange={(e) => setCourseId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Select a course...</option>
            {courses.map((c) => (
              <option key={c.course_id} value={c.course_id}>{c.course_name}</option>
            ))}
          </select>
        </div>

        {/* Class session (week) scope — optional */}
        {courseId && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Class session (week) <span className="text-gray-400 font-normal">— optional scope</span></label>
            <select
              value={weekSessionId}
              onChange={(e) => setWeekSessionId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Whole course — all topics</option>
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {s.session_date ? new Date(s.session_date + 'T00:00:00').toLocaleDateString() : 'Undated session'}
                  {s.session_document ? ` — ${s.session_document.slice(0, 40)}` : ''}
                  {/* Empty scope means "no restriction" — draw from the whole
                      course — not "no content". Only show a count when one is set. */}
                  {s.in_scope_concepts?.length
                    ? ` (${s.in_scope_concepts.length} topic${s.in_scope_concepts.length !== 1 ? 's' : ''} in scope)`
                    : ' (whole course)'}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-400 mt-1">
              {isOverridden
                ? `Custom scope for this assignment — ${effectiveConcepts.length} topic${effectiveConcepts.length !== 1 ? 's' : ''} selected below.`
                : weekSessionId
                ? weekConcepts.length > 0
                  ? `Pre-filled from this week's ${weekConcepts.length} in-scope topic${weekConcepts.length !== 1 ? 's' : ''} — adjust below for this assignment only.`
                  : 'This week has no in-scope topics set — pick a subset below, or leave scope to the whole course.'
                : 'Leave as-is to draw from the whole concept graph, or pick a week / a subset of topics below.'}
            </p>

            {/* Per-assignment topic picker — edits this assignment only; the
                week's saved scope is untouched. */}
            {graphReady && (
              <div className="mt-2">
                <button
                  type="button"
                  onClick={() => setScopeOpen((v) => !v)}
                  className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 font-medium"
                >
                  <Network className="w-3.5 h-3.5" />
                  Topics for this assignment{hasScope ? ` (${effectiveConcepts.length})` : ' — all topics'}
                </button>
                {scopeOpen && (
                  <div className="mt-2 border border-gray-200 rounded-lg p-3 bg-gray-50">
                    {allConcepts.length === 0 ? (
                      <p className="text-xs text-gray-400">No concept graph yet — upload materials and let the graph build first.</p>
                    ) : (
                      <>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs text-gray-500">{effectiveConcepts.length} of {allConcepts.length} topics selected</span>
                          <div className="flex gap-2">
                            <button type="button" onClick={() => setTopicOverride(allConcepts.map((c) => c.id))} className="text-xs text-blue-600 hover:underline">All</button>
                            <button type="button" onClick={() => setTopicOverride([])} className="text-xs text-blue-600 hover:underline">None</button>
                            {isOverridden && (
                              <button type="button" onClick={() => setTopicOverride(null)} className="text-xs text-blue-600 hover:underline">Reset to week</button>
                            )}
                          </div>
                        </div>
                        <div className="max-h-48 overflow-y-auto grid grid-cols-1 sm:grid-cols-2 gap-1 pr-1">
                          {allConcepts.map((c) => (
                            <label key={c.id} className="flex items-center gap-2 text-xs text-gray-700 cursor-pointer py-0.5">
                              <input
                                type="checkbox"
                                checked={effectiveConcepts.includes(c.id)}
                                onChange={() => toggleConcept(c.id)}
                                className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                              />
                              <span className="truncate">{c.label}</span>
                            </label>
                          ))}
                        </div>
                        <p className="text-[11px] text-gray-400 mt-2">
                          Applies to this assignment only. Selecting none draws from the whole course. The week's saved scope stays unchanged.
                        </p>
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Title */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Assignment Title</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="e.g., Operations Midterm — Oral Exam"
          />
        </div>

        {/* Type — determines which section students see it under */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Type</label>
          <div className="grid grid-cols-3 gap-2">
            {([
              ['practice', 'Practice Test', 'Ungraded, retryable'],
              ['assignment', 'Assignment', 'Graded coursework'],
              ['exam', 'Exam', 'Formal assessment'],
            ] as const).map(([val, label, desc]) => (
              <button
                key={val}
                type="button"
                onClick={() => setAssignmentType(val)}
                className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                  assignmentType === val ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500' : 'border-gray-300 hover:bg-gray-50'
                }`}
              >
                <div className="text-sm font-semibold text-gray-900">{label}</div>
                <div className="text-xs text-gray-500">{desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Case-based toggle — only case assessments expose "View Case" to students */}
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={includeCase}
            onChange={(e) => setIncludeCase(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <span>
            <span className="block text-sm font-medium text-gray-700">Case-based assessment</span>
            <span className="block text-xs text-gray-500">Let students open the course reference materials ("View Case") during the exam. Leave off for standard tests.</span>
          </span>
        </label>

        {/* Number of questions + Duration + Difficulty */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Number of Questions</label>
            <input
              type="number"
              value={qCount}
              onChange={(e) => setQCount(Math.max(1, Math.min(50, +e.target.value || 1)))}
              min={1} max={50}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">Assembled from the concept graph (max 50).</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Duration (minutes)</label>
            <input
              type="number"
              value={duration}
              onChange={(e) => setDuration(Math.max(5, Math.min(180, +e.target.value || 5)))}
              min={5} max={180}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Difficulty Focus</label>
            <select
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value as Difficulty)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="recall">Recall — definitions & facts</option>
              <option value="balanced">Balanced — recall + reasoning</option>
              <option value="deep">Deep — causal reasoning</option>
            </select>
          </div>
        </div>

        {/* Concept-graph readiness — questions come from it */}
        {courseId && !graphReady && (
          <div className="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
            <Loader2 className="w-4 h-4 animate-spin flex-shrink-0 mt-0.5" />
            <span>
              Preparing this course's concept graph — questions become available once it's built. This runs automatically after you upload materials and can take a minute. If you haven't added materials yet, upload them from the course's <span className="font-medium">Materials</span> tab.
            </span>
          </div>
        )}
        {courseId && graphReady && (
          <div className="flex items-center gap-2 p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-800">
            <CheckCircle className="w-4 h-4 flex-shrink-0" />
            {/* "topics" here matches the scope picker's vocabulary — same units as
                a week's "in scope" count — so the two figures don't read as a
                concepts-vs-topics contradiction. */}
            <span>{conceptCount} topic{conceptCount !== 1 ? 's' : ''} ready — up to {maxQuestions} questions available.</span>
          </div>
        )}

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
        )}

        <button
          onClick={handleBuild}
          disabled={!canSubmit || building}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {building && <Loader2 className="w-4 h-4 animate-spin" />}
          {building ? 'Generating preview…'
            : courseId && !graphReady ? 'Waiting for concept graph…'
            : 'Generate Preview'}
        </button>
        <p className="text-xs text-gray-400 text-center -mt-2">You'll review and can edit the questions before anything is published to students.</p>
      </div>
    </div>
  );
}
