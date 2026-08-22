import { useState } from 'react';
import { useSearchParams, useNavigate, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, CheckCircle, GraduationCap, Copy, Check, ChevronLeft } from 'lucide-react';
import { get } from '../../api/client';
import { buildExam, assignExam, type ExamVariantQuestion, type AssignmentType } from '../../api/exam';

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
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(false);
  const [createdData, setCreatedData] = useState<{ assignment_id: string; question_count: number } | null>(null);
  const [builtQuestions, setBuiltQuestions] = useState<ExamVariantQuestion[]>([]);
  const [linkCopied, setLinkCopied] = useState(false);
  const [error, setError] = useState('');

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<Course[]>('/api/professor/courses'),
  });

  // Questions are assembled from the concept graph, which builds automatically
  // (and asynchronously) after materials are uploaded. Poll until it's ready so
  // we only enable assignment creation once there's something to assemble from.
  const { data: graph } = useQuery<{ node_count?: number }>({
    queryKey: ['assignment-graph', courseId],
    queryFn: () => get(`/api/courses/${courseId}/graph`),
    enabled: !!courseId && !created,
    refetchInterval: (q) => ((q.state.data?.node_count || 0) > 0 ? false : 6000),
  });
  const conceptCount = graph?.node_count || 0;
  const graphReady = conceptCount > 0;
  const maxQuestions = Math.min(50, conceptCount * 4);

  const courseName = courses.find((c) => c.course_id === courseId)?.course_name || '';
  const backTo = courseId ? `/professor/courses/${courseId}?tab=assignments` : '/professor/dashboard';
  const canSubmit = !!courseId && !!title.trim() && qCount >= 1 && graphReady;

  const handleCreate = async () => {
    if (!canSubmit) return;
    setCreating(true);
    setError('');
    try {
      // Assemble N questions from the course's concept graph (generated at graph
      // build — no separate step), then persist them as the assignment.
      const built = await buildExam(courseId, { q_count: qCount, exam_len: duration, difficulty });
      if (built.status !== 'completed' || !built.variants?.length) {
        throw new Error(built.message || 'Could not build questions — make sure this course has a concept graph (upload materials first).');
      }
      const questions = built.variants[0].questions || [];
      if (questions.length === 0) {
        throw new Error('No questions could be assembled from the concept graph yet. Upload materials and let the graph build, then try again.');
      }
      const res = await assignExam(courseId, {
        title: title.trim(),
        questions,
        difficulty,
        duration_minutes: duration,
        assignment_type: assignmentType,
      });
      if (res.status !== 'completed' || !res.assignment_id) {
        throw new Error(res.message || 'Failed to create assignment.');
      }
      setBuiltQuestions(questions);
      setCreatedData({ assignment_id: res.assignment_id, question_count: res.question_count ?? questions.length });
      setCreated(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create assignment');
    } finally {
      setCreating(false);
    }
  };

  const resetForm = () => {
    setCreated(false);
    setCreatedData(null);
    setBuiltQuestions([]);
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
              onChange={(e) => setDuration(+e.target.value)}
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
            <span>{conceptCount} concept{conceptCount !== 1 ? 's' : ''} ready — up to {maxQuestions} questions available.</span>
          </div>
        )}

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
        )}

        <button
          onClick={handleCreate}
          disabled={!canSubmit || creating}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {creating && <Loader2 className="w-4 h-4 animate-spin" />}
          {creating ? 'Generating & creating…'
            : courseId && !graphReady ? 'Waiting for concept graph…'
            : 'Generate Questions & Create Assignment'}
        </button>
      </div>
    </div>
  );
}
