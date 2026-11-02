import { useEffect, useState, type ReactNode } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, CheckCircle, GraduationCap, Copy, Check, Pencil, Trash2, RefreshCw, Eye, AlertTriangle, Network, Plus, ArrowUp, ArrowDown, Search } from 'lucide-react';
import { get, post } from '../../api/client';
import { buildExam, regenerateExam, assignExam, discardDraft, getAssignmentPreview, type ExamVariantQuestion, type AssignmentType } from '../../api/exam';
import { Breadcrumbs } from '../../components/Breadcrumbs';
import { useToast } from '../../components/Toast';
import { listMaterials } from '../../api/materials';
import { getSyllabus } from '../../api/courses';
import { DEFAULT_ORG } from '../../config';
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

// A labeled, numbered section card used to group the create-assignment form into
// clear steps (S1). Purely presentational — no state of its own.
function FormSection({ step, title, desc, children }: {
  step: number; title: string; desc?: string; children: ReactNode;
}) {
  return (
    <section className="bg-white rounded-xl border border-border p-6 space-y-4">
      <div className="flex items-start gap-3">
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-navy text-white text-xs font-bold flex-shrink-0 mt-0.5">{step}</span>
        <div>
          <h2 className="text-base font-heading text-navy leading-tight">{title}</h2>
          {desc && <p className="text-xs text-muted mt-0.5">{desc}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

export default function CreateAssignment() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const preselectedCourse = searchParams.get('course') || '';
  // When present, we're continuing an existing Draft assignment (U6): its stored
  // questions are loaded into the preview, and publishing replaces the draft.
  const draftParam = searchParams.get('draft') || '';

  const [courseId, setCourseId] = useState(preselectedCourse);
  const [title, setTitle] = useState('');
  const [qCount, setQCount] = useState(8);
  const [duration, setDuration] = useState(30);
  // Difficulty is fixed to "balanced" (recall + reasoning) for now — no picker.
  const difficulty: Difficulty = 'balanced';
  const [assignmentType, setAssignmentType] = useState<AssignmentType>('assignment');
  // Topics (concept-graph labels) this assignment draws from. Empty = whole
  // course (all topics). This is the ONLY scoping control — class sessions/weeks
  // are no longer part of assignment creation.
  const [selectedTopics, setSelectedTopics] = useState<string[]>([]);
  // Topic scope mode (S2): 'whole' course (default) vs 'specific' topics. The
  // topic picker only appears in 'specific' mode; 'whole' keeps selectedTopics
  // empty (empty ⇒ whole course — the existing semantics are preserved).
  const [scopeMode, setScopeMode] = useState<'whole' | 'specific'>('whole');
  const [topicSearch, setTopicSearch] = useState('');
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
  // Index of the single question currently being re-authored (per-row regenerate).
  const [regenIdx, setRegenIdx] = useState<number | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [draftAssignmentId, setDraftAssignmentId] = useState<string | null>(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [error, setError] = useState('');
  // The existing Draft being continued (U6). Null for a fresh assignment. Its
  // stored questions are loaded into the preview; on publish it's discarded and
  // replaced by the newly-curated assignment.
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<Course[]>('/api/professor/courses'),
  });

  // U6 — continuing a Draft: load its stored questions + settings into the
  // preview once, so the professor resumes exactly where they left off.
  const { data: draftData } = useQuery({
    queryKey: ['assignment-preview', draftParam],
    queryFn: () => getAssignmentPreview(draftParam),
    enabled: !!draftParam && !editingDraftId && !created,
    retry: false,
  });
  useEffect(() => {
    if (!draftData || editingDraftId) return;
    setTitle(draftData.title || '');
    if (draftData.duration_minutes) setDuration(draftData.duration_minutes);
    setAssignmentType(draftData.assignment_type || 'assignment');
    setIncludeCase(!!draftData.include_case);
    setPreviewQuestions(
      (draftData.questions || []).map((q) => ({ topic: q.topic || 'General', concept_id: '', q: q.text })),
    );
    setEditingDraftId(draftParam);
    setInPreview(true);
  }, [draftData, editingDraftId, draftParam]);

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
  // Concept nodes (id + label) for the topic picker.
  const allConcepts = (graph?.concepts ?? [])
    .map((c) => ({ id: c.id || c.label || '', label: c.label || c.id || '' }))
    .filter((c) => c.id);

  // Effective scope for THIS assignment: the selected topics. Empty ⇒ whole graph.
  const effectiveConcepts = selectedTopics;
  const hasScope = effectiveConcepts.length > 0;
  // Topics filtered by the search box (S2). Case-insensitive label match.
  const filteredConcepts = allConcepts.filter((c) =>
    c.label.toLowerCase().includes(topicSearch.trim().toLowerCase()),
  );

  // Reset the topic selection + scope mode when the course changes (its topics differ).
  useEffect(() => { setSelectedTopics([]); setScopeMode('whole'); setTopicSearch(''); }, [courseId]);

  const toggleConcept = (id: string) => {
    const next = new Set(effectiveConcepts);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelectedTopics([...next]);
  };

  const courseName = courses.find((c) => c.course_id === courseId)?.course_name || '';

  // Gate assignment creation on the course actually having (non-syllabus)
  // material — you can't generate questions from nothing. Distinct from
  // "graph still building" so we can show the right message.
  const { data: syllabus } = useQuery({
    queryKey: ['syllabus', courseId],
    queryFn: () => getSyllabus(courseId!),
    enabled: !!courseId && !created,
  });
  const { data: materials = [], isSuccess: materialsLoaded } = useQuery({
    queryKey: ['materials', courseId, courseName],
    queryFn: () => listMaterials(DEFAULT_ORG, courseName),
    enabled: !!courseId && !!courseName && !created,
  });
  const nonSyllabusMaterials = (materials as any[]).filter((m) => {
    const mid = m.material_id || m.id;
    const vid = m.current_version_id || mid;
    return !(syllabus && (
      (syllabus.material_id && mid === syllabus.material_id) ||
      (syllabus.version_id && (vid === syllabus.version_id || mid === syllabus.version_id))
    ));
  });
  const hasMaterials = nonSyllabusMaterials.length > 0;
  const noMaterials = materialsLoaded && !hasMaterials;

  const backTo = courseId ? `/professor/courses/${courseId}?tab=assignments` : '/professor/dashboard';
  const canSubmit = !!courseId && !!title.trim() && qCount >= 1 && hasMaterials && graphReady;

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
        // Snapshot the selected topics so the exam stays attributed to the scope
        // chosen now. Empty ⇒ whole course.
        scope_concepts: hasScope ? effectiveConcepts : undefined,
      });
      if (res.status !== 'completed' || !res.assignment_id) {
        throw new Error(res.message || 'Failed to create assignment.');
      }
      // Continuing a draft: the curated set is now published as a fresh
      // assignment, so discard the original draft (best-effort).
      if (editingDraftId) { await discardDraft(editingDraftId).catch(() => {}); setEditingDraftId(null); }
      setBuiltQuestions(previewQuestions);
      setCreatedData({ assignment_id: res.assignment_id, question_count: res.question_count ?? previewQuestions.length });
      setCreated(true);
      setInPreview(false);
      toast.success('Assignment published');
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
  // Cancel the editor. A freshly-added, still-blank question is dropped so an
  // empty row can't survive into the published set.
  const cancelEdit = () => {
    setPreviewQuestions((qs) =>
      editIdx !== null && !qs[editIdx]?.q?.trim() ? qs.filter((_, idx) => idx !== editIdx) : qs,
    );
    setEditIdx(null);
  };
  // Reorder with up/down (no drag-and-drop dependency).
  const moveQuestion = (i: number, dir: -1 | 1) => {
    setPreviewQuestions((qs) => {
      const j = i + dir;
      if (j < 0 || j >= qs.length) return qs;
      const next = qs.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
    setEditIdx(null);
  };
  // Add a manual question: append a blank row and open it for editing.
  const addQuestion = () => {
    setEditIdx(previewQuestions.length);
    setEditDraft('');
    setPreviewQuestions((qs) => [...qs, { topic: 'General', concept_id: '', q: '' }]);
  };
  // Regenerate a SINGLE question (distinct from "Regenerate whole assessment").
  // Reuses regenerateExam scoped to this row's concept (q_count 1) and swaps in
  // the fresh question — the rest of the set is untouched.
  const regenerateOne = async (i: number) => {
    setRegenIdx(i);
    setError('');
    try {
      const target = previewQuestions[i];
      const res = await regenerateExam(courseId, {
        q_count: 1, exam_len: duration, difficulty,
        concept_ids: target.concept_id ? [target.concept_id] : (hasScope ? effectiveConcepts : undefined),
      });
      const fresh = res.variants?.[0]?.questions?.[0];
      if (res.status !== 'completed' || !fresh) {
        throw new Error(res.message || 'Could not regenerate this question.');
      }
      setPreviewQuestions((qs) => qs.map((old, idx) => (idx === i ? { ...fresh } : old)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not regenerate this question.');
    } finally {
      setRegenIdx(null);
    }
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
    <Breadcrumbs
      items={[
        { label: 'Courses', to: '/professor/dashboard' },
        ...(courseName ? [{ label: courseName, to: backTo }] : []),
        { label: editingDraftId ? (title || 'Edit draft') : (title || 'New assignment') },
      ]}
    />
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
            disabled={regenerating || building || regenIdx !== null}
            title="Re-author every question in this assessment with AI"
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink hover:bg-parchment disabled:opacity-50"
          >
            {regenerating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {regenerating ? 'Authoring fresh questions…' : 'Regenerate whole assessment'}
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
                  <div className="flex items-center gap-0.5 flex-shrink-0">
                    {/* Reorder — up/down buttons (no drag-and-drop dependency) */}
                    <div className="flex flex-col">
                      <button onClick={() => moveQuestion(i, -1)} disabled={i === 0} title="Move up" className="p-0.5 text-muted hover:text-navy disabled:opacity-25 disabled:cursor-not-allowed rounded transition-colors">
                        <ArrowUp className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => moveQuestion(i, 1)} disabled={i === previewQuestions.length - 1} title="Move down" className="p-0.5 text-muted hover:text-navy disabled:opacity-25 disabled:cursor-not-allowed rounded transition-colors">
                        <ArrowDown className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <button onClick={() => startEdit(i)} title="Edit question" className="p-1.5 text-muted hover:text-navy hover:bg-parchment rounded transition-colors">
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button onClick={() => regenerateOne(i)} disabled={regenIdx !== null || regenerating} title="Regenerate this question" className="p-1.5 text-muted hover:text-gold hover:bg-parchment rounded transition-colors disabled:opacity-50">
                      {regenIdx === i ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    </button>
                    <button onClick={() => removeQuestion(i)} title="Delete question" className="p-1.5 text-muted hover:text-red-600 hover:bg-red-50 rounded transition-colors">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
              {editIdx === i && (
                <div className="flex items-center gap-2 mt-2 pl-9">
                  <button onClick={saveEdit} disabled={!editDraft.trim()} className="px-3 py-1 bg-navy text-white rounded text-xs font-medium hover:bg-navy-light disabled:opacity-40 disabled:cursor-not-allowed">Save</button>
                  <button onClick={cancelEdit} className="px-3 py-1 border border-border text-ink rounded text-xs font-medium hover:bg-parchment">Cancel</button>
                </div>
              )}
            </div>
          ))}
          {previewQuestions.length === 0 && (
            <div className="bg-white rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted">
              All questions removed. Add one manually below, regenerate to build a fresh set, or go back to change the settings.
            </div>
          )}
          {/* Add a manual question (S3) */}
          <button
            onClick={addQuestion}
            disabled={editIdx !== null}
            className="w-full inline-flex items-center justify-center gap-1.5 rounded-xl border border-dashed border-border py-3 text-sm font-medium text-muted hover:text-navy hover:border-navy/40 hover:bg-parchment transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Plus className="w-4 h-4" /> Add a question
          </button>
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
  // The form is grouped into clear, numbered sections (S1): Assignment Details →
  // Choose Content → Settings → Preview. All state + build/publish logic below is
  // unchanged — this is layout + progressive disclosure only.
  const inputCls = 'w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-navy/25';
  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <Breadcrumb />
      <div>
        <h1 className="text-2xl font-heading text-navy">Create Assignment</h1>
        <p className="text-sm text-muted mt-1">
          Questions are assembled from this course's concept graph and turned into an oral exam. Work through the steps below, then preview before publishing.
        </p>
      </div>

      {/* ── Step 1 — Assignment Details ─────────────────────────────────────── */}
      <FormSection step={1} title="Assignment Details">
        {/* Assignment Title — first field. The course is fixed (this page is
            reached from a course), so no course picker is shown; only when
            there's no course context do we fall back to a selector below. */}
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Assignment Title</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className={inputCls}
            placeholder="e.g., Operations Midterm — Oral Exam"
          />
        </div>

        {/* Course — only when arrived without a course context (fallback). */}
        {!preselectedCourse && (
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Course</label>
            <select
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              className={inputCls}
            >
              <option value="">Select a course...</option>
              {courses.map((c) => (
                <option key={c.course_id} value={c.course_id}>{c.course_name}</option>
              ))}
            </select>
          </div>
        )}

        {/* Type — determines which section students see it under */}
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Type</label>
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
                  assignmentType === val ? 'border-navy bg-parchment ring-1 ring-navy' : 'border-border hover:bg-parchment'
                }`}
              >
                <div className="text-sm font-semibold text-navy">{label}</div>
                <div className="text-xs text-muted">{desc}</div>
              </button>
            ))}
          </div>
        </div>
      </FormSection>

      {/* ── Step 2 — Choose Content (topic scope, S2) ───────────────────────── */}
      <FormSection step={2} title="Choose Content" desc="Draw questions from the whole course, or scope this assignment to specific topics.">
        {!courseId ? (
          <p className="text-sm text-muted">Select a course first to choose its topics.</p>
        ) : !graphReady ? (
          <p className="text-sm text-muted">Topics appear once this course's concept graph has finished building.</p>
        ) : (
          <div className="space-y-3">
            {/* Explicit Whole course vs Specific topics choice (segmented control) */}
            <div className="inline-flex rounded-lg border border-border p-0.5 bg-parchment">
              {([['whole', 'Whole course'], ['specific', 'Specific topics']] as const).map(([val, label]) => (
                <button
                  key={val}
                  type="button"
                  onClick={() => { setScopeMode(val); if (val === 'whole') setSelectedTopics([]); }}
                  className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    scopeMode === val ? 'bg-white text-navy shadow-sm' : 'text-muted hover:text-navy'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {scopeMode === 'whole' ? (
              <p className="text-xs text-muted inline-flex items-center gap-1.5">
                <Network className="w-3.5 h-3.5" />
                Questions will be drawn from all {allConcepts.length} topic{allConcepts.length !== 1 ? 's' : ''} in this course.
              </p>
            ) : (
              <div className="border border-border rounded-lg p-3 bg-parchment/40">
                {allConcepts.length === 0 ? (
                  <p className="text-xs text-muted">No topics yet — upload materials and let the concept graph build first.</p>
                ) : (
                  <>
                    {/* Search box filters the topic list by label (S2) */}
                    <div className="relative mb-2">
                      <Search className="w-4 h-4 text-muted absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                      <input
                        type="text"
                        value={topicSearch}
                        onChange={(e) => setTopicSearch(e.target.value)}
                        placeholder="Search topics…"
                        className="w-full pl-8 pr-3 py-1.5 border border-border rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-navy/25"
                      />
                    </div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs text-muted">
                        {effectiveConcepts.length === 0
                          ? 'No topics selected — defaults to the whole course'
                          : `${effectiveConcepts.length} of ${allConcepts.length} topics selected`}
                      </span>
                      <div className="flex gap-2">
                        <button type="button" onClick={() => setSelectedTopics(allConcepts.map((c) => c.id))} className="text-xs text-gold hover:underline">Select all</button>
                        <button type="button" onClick={() => setSelectedTopics([])} className="text-xs text-gold hover:underline">Clear</button>
                      </div>
                    </div>
                    <div className="max-h-48 overflow-y-auto grid grid-cols-1 sm:grid-cols-2 gap-1 pr-1">
                      {filteredConcepts.map((c) => (
                        <label key={c.id} className="flex items-center gap-2 text-xs text-ink cursor-pointer py-0.5">
                          <input
                            type="checkbox"
                            checked={effectiveConcepts.includes(c.id)}
                            onChange={() => toggleConcept(c.id)}
                            className="h-3.5 w-3.5 rounded border-border text-navy focus:ring-navy/30"
                          />
                          <span className="truncate">{c.label}</span>
                        </label>
                      ))}
                      {filteredConcepts.length === 0 && (
                        <p className="text-xs text-muted col-span-full py-1">No topics match "{topicSearch}".</p>
                      )}
                    </div>
                    <p className="text-[11px] text-muted mt-2">
                      Selecting none draws questions from the whole course's topics.
                    </p>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </FormSection>

      {/* ── Step 3 — Settings ───────────────────────────────────────────────── */}
      <FormSection step={3} title="Settings">
        {/* Number of questions + Duration. Difficulty is hard-coded to
            "balanced" (recall + reasoning) for now — no picker. */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Number of Questions</label>
            <input
              type="number"
              value={qCount}
              onChange={(e) => setQCount(Math.max(1, Math.min(50, +e.target.value || 1)))}
              min={1} max={50}
              className={inputCls}
            />
            <p className="text-xs text-muted mt-1">Assembled from the concept graph (max 50).</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Duration (minutes)</label>
            <input
              type="number"
              value={duration}
              onChange={(e) => setDuration(Math.max(5, Math.min(180, +e.target.value || 5)))}
              min={5} max={180}
              className={inputCls}
            />
          </div>
        </div>

        {/* Case-based toggle — only case assessments expose "View Case" to students */}
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={includeCase}
            onChange={(e) => setIncludeCase(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border text-navy focus:ring-navy/30"
          />
          <span>
            <span className="block text-sm font-medium text-ink">Case-based assessment</span>
            <span className="block text-xs text-muted">Let students open the course reference materials ("View Case") during the exam. Leave off for standard tests.</span>
          </span>
        </label>
      </FormSection>

      {/* ── Step 4 — Preview ────────────────────────────────────────────────── */}
      <FormSection step={4} title="Preview" desc="Build the questions and review them before anything is published to students.">
        {/* No material → can't generate. Distinct, explicit message. */}
        {courseId && noMaterials && (
          <div className="flex items-start gap-3 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>
              No Material is uploaded for Assignment generation. Upload course materials from the
              course's <span className="font-medium">Materials</span> tab, then come back.
            </span>
          </div>
        )}

        {/* Concept-graph readiness — only relevant once material exists. */}
        {courseId && hasMaterials && !graphReady && (
          <div className="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
            <Loader2 className="w-4 h-4 animate-spin flex-shrink-0 mt-0.5" />
            <span>
              Preparing this course's concept graph — questions become available once it's built. This runs automatically after you upload materials and can take a minute.
            </span>
          </div>
        )}

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
        )}

        <button
          onClick={handleBuild}
          disabled={!canSubmit || building}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {building && <Loader2 className="w-4 h-4 animate-spin" />}
          {building ? 'Generating preview…'
            : courseId && noMaterials ? 'No material uploaded'
            : courseId && !graphReady ? 'Waiting for concept graph…'
            : 'Generate Preview'}
        </button>
        <p className="text-xs text-muted text-center">You'll review and can edit the questions before anything is published to students.</p>
      </FormSection>
    </div>
  );
}
