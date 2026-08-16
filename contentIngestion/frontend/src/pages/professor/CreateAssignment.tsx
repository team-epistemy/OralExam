import { useState, useEffect, useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, CheckCircle, GraduationCap, Copy, Check, ChevronDown, ChevronUp, ArrowLeft } from 'lucide-react';
import { get, post } from '../../api/client';
import { listQuestions } from '../../api/questions';

interface Course {
  course_id: string;
  course_name: string;
}

interface TopicDistribution {
  id: string;
  label: string;
  count: number;
}

interface ExamVariant {
  id: string;
  title: string;
  badge: string;
  description: string;
  distribution: TopicDistribution[];
}

// ── Distribute qCount questions across topics using largest-remainder method ──
function distributeQuestions(
  topics: { id: string; label: string }[],
  qCount: number,
  mode: 'even' | 'front' | 'back'
): TopicDistribution[] {
  const n = topics.length;
  if (n === 0) return [];
  let w: number[];
  if (mode === 'front') w = topics.map((_, i) => n - i);
  else if (mode === 'back') w = topics.map((_, i) => i + 1);
  else w = topics.map(() => 1);
  const wSum = w.reduce((s, x) => s + x, 0) || 1;
  const counts = w.map(x => Math.floor((x / wSum) * qCount));
  let total = counts.reduce((s, x) => s + x, 0);
  const rema = w.map((x, i) => ({ i, frac: (x / wSum) * qCount - counts[i] }))
    .sort((a, b) => b.frac - a.frac);
  let r = 0;
  while (total < qCount) { counts[rema[r % n].i]++; total++; r++; }
  return topics.map((t, i) => ({ id: t.id, label: t.label, count: counts[i] }))
    .filter(d => d.count > 0);
}

const VARIANT_ANGLES: { key: string; suffix: string; mode: 'even' | 'front' | 'back'; desc: (n: number) => string }[] = [
  {
    key: 'even',
    suffix: 'Even Coverage',
    mode: 'even',
    desc: (n) => `Questions distributed evenly across all ${n} selected topics. Best when every topic should carry equal weight.`,
  },
  {
    key: 'core',
    suffix: 'Core Emphasis',
    mode: 'front',
    desc: (_n) => `Weighted toward the foundational concepts so gaps in prerequisites surface first.`,
  },
  {
    key: 'frontier',
    suffix: 'Frontier Emphasis',
    mode: 'back',
    desc: (_n) => `Weighted toward the advanced concepts to stretch stronger students.`,
  },
];

const DIFFICULTY_LABELS: Record<string, { name: string; badge: string }> = {
  recall: { name: 'Recall', badge: 'Foundational' },
  balanced: { name: 'Balanced', badge: 'Intermediate' },
  deep: { name: 'Deep', badge: 'Advanced' },
};

function buildVariants(
  selectedQs: any[],
  difficulty: string,
  qCount: number
): ExamVariant[] {
  const topicMap = new Map<string, string>();
  selectedQs.forEach((q) => {
    const topic = q.topic || 'General';
    if (!topicMap.has(topic)) topicMap.set(topic, topic);
  });
  const topics = Array.from(topicMap.entries()).map(([id, label]) => ({ id, label }));
  const meta = DIFFICULTY_LABELS[difficulty] || DIFFICULTY_LABELS.balanced;

  return VARIANT_ANGLES.map((a) => ({
    id: `${difficulty}-${a.key}`,
    title: `${meta.name} · ${a.suffix}`,
    badge: meta.badge,
    description: a.desc(topics.length),
    distribution: distributeQuestions(topics, qCount, a.mode),
  }));
}

export default function CreateAssignment() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const preselectedCourse = searchParams.get('course') || '';

  const [courseId, setCourseId] = useState(preselectedCourse);
  const [title, setTitle] = useState('');
  const [duration, setDuration] = useState(30);
  const [difficulty, setDifficulty] = useState('balanced');
  const [adaptive, setAdaptive] = useState(true);
  const [selectedQuestions, setSelectedQuestions] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(false);
  const [createdData, setCreatedData] = useState<any>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [showQuestions, setShowQuestions] = useState(false);
  const [error, setError] = useState('');

  // Variant step state
  const [showVariants, setShowVariants] = useState(false);
  const [selectedVariant, setSelectedVariant] = useState<string | null>(null);

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<Course[]>('/api/professor/courses'),
  });

  const { data: questions = [] } = useQuery({
    queryKey: ['questions', courseId],
    queryFn: () => listQuestions(courseId),
    enabled: !!courseId,
  });

  // Auto-select approved questions, or all if none approved
  useEffect(() => {
    if (questions.length > 0) {
      const approved = questions.filter((q: any) => q.status === 'approved');
      if (approved.length > 0) {
        setSelectedQuestions(new Set(approved.map((q: any) => q.question_id)));
      } else {
        setSelectedQuestions(new Set(questions.map((q: any) => q.question_id)));
      }
    }
  }, [questions]);

  const toggleQuestion = (id: string) => {
    setSelectedQuestions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Build variants from selected questions
  const selectedQsList = useMemo(
    () => questions.filter((q: any) => selectedQuestions.has(q.question_id)),
    [questions, selectedQuestions]
  );

  const variants = useMemo(
    () => buildVariants(selectedQsList, difficulty, selectedQuestions.size),
    [selectedQsList, difficulty, selectedQuestions.size]
  );

  const handleBuildExam = () => {
    if (!courseId || !title || selectedQuestions.size === 0) return;
    setShowVariants(true);
    setSelectedVariant(null);
  };

  const handleBackToConfig = () => {
    setShowVariants(false);
    setSelectedVariant(null);
  };

  const handleCreate = async () => {
    if (!courseId || !title || selectedQuestions.size === 0 || !selectedVariant) return;
    setCreating(true);
    setError('');
    try {
      const chosenVariant = variants.find((v) => v.id === selectedVariant);
      const response = await post(`/api/courses/${courseId}/assignments`, {
        title,
        question_ids: Array.from(selectedQuestions),
        config: {
          difficulty,
          duration_minutes: duration,
          adaptive,
          variant: selectedVariant,
          distribution: chosenVariant?.distribution,
        },
      });
      setCreatedData(response);
      setCreated(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create assignment');
    } finally {
      setCreating(false);
    }
  };

  const handleReset = () => {
    setCreated(false);
    setCreatedData(null);
    setTitle('');
    setCourseId(preselectedCourse);
    setDuration(30);
    setDifficulty('balanced');
    setAdaptive(true);
    setSelectedQuestions(new Set());
    setLinkCopied(false);
    setShowQuestions(false);
    setShowVariants(false);
    setSelectedVariant(null);
    setError('');
  };

  const copyLink = () => {
    const assignmentId = createdData?.assignment_id || createdData?.id || 'unknown';
    const link = `${window.location.origin}/student/exam/${assignmentId}`;
    navigator.clipboard.writeText(link);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const canSubmit = title && courseId && selectedQuestions.size > 0;

  // Group selected questions by topic for the completion screen
  const questionsByTopic = () => {
    const grouped: Record<string, { question: string; id: string }[]> = {};
    questions
      .filter((q: any) => selectedQuestions.has(q.question_id))
      .forEach((q: any) => {
        const topic = q.topic || 'General';
        if (!grouped[topic]) grouped[topic] = [];
        grouped[topic].push({ question: q.question || q.text, id: q.question_id });
      });
    return grouped;
  };

  const getCourseName = () => {
    const course = courses.find((c) => c.course_id === courseId);
    return course?.course_name || courseId;
  };

  const getDifficultyLabel = () => {
    switch (difficulty) {
      case 'recall': return 'Recall';
      case 'balanced': return 'Balanced';
      case 'deep': return 'Deep Reasoning';
      default: return difficulty;
    }
  };

  // Completion screen
  if (created && createdData) {
    const assignmentId = createdData?.assignment_id || createdData?.id || 'unknown';
    const studentLink = `${window.location.origin}/student/exam/${assignmentId}`;
    const grouped = questionsByTopic();

    return (
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Success header */}
        <div className="text-center pt-4">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-green-100 rounded-full mb-4">
            <GraduationCap className="w-8 h-8 text-green-600" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Exam Ready</h1>
          <p className="text-sm text-gray-500 mt-2">
            Your <span className="font-semibold text-gray-700">{title}</span> has been saved and is ready for student access.
          </p>
        </div>

        {/* Exam summary card */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Exam Summary</h2>
          <div className="space-y-3">
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <span className="text-sm text-gray-600">Course</span>
              <span className="text-sm font-semibold text-gray-900">{getCourseName()}</span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <span className="text-sm text-gray-600">Assignment</span>
              <span className="text-sm font-semibold text-gray-900">{title}</span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <span className="text-sm text-gray-600">Questions</span>
              <span className="text-sm font-semibold text-gray-900">{selectedQuestions.size}</span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <span className="text-sm text-gray-600">Duration</span>
              <span className="text-sm font-semibold text-gray-900">{duration} minutes</span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <span className="text-sm text-gray-600">Difficulty</span>
              <span className="text-sm font-semibold text-gray-900">{getDifficultyLabel()}</span>
            </div>
            {selectedVariant && (
              <div className="flex justify-between items-center py-2">
                <span className="text-sm text-gray-600">Variant</span>
                <span className="text-sm font-semibold text-gray-900">
                  {variants.find(v => v.id === selectedVariant)?.title || selectedVariant}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Share link */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Student Exam Link</h2>
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
              <code className="text-sm text-gray-700 break-all">{studentLink}</code>
            </div>
            <button
              onClick={copyLink}
              className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors whitespace-nowrap"
            >
              {linkCopied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              {linkCopied ? 'Copied!' : 'Copy Link'}
            </button>
          </div>
        </div>

        {/* Question list — collapsible */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <button
            onClick={() => setShowQuestions((s) => !s)}
            className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-gray-50 transition-colors"
          >
            <span className="text-sm font-semibold text-gray-700">Questions by Topic</span>
            {showQuestions ? (
              <ChevronUp className="w-4 h-4 text-gray-400" />
            ) : (
              <ChevronDown className="w-4 h-4 text-gray-400" />
            )}
          </button>
          {showQuestions && (
            <div className="px-6 pb-6 border-t border-gray-100 pt-4 space-y-5">
              {Object.entries(grouped).map(([topic, qs]) => (
                <div key={topic}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-blue-600 bg-blue-50 px-2 py-0.5 rounded">
                      {topic}
                    </span>
                    <span className="text-xs text-gray-400">{qs.length} question{qs.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className="space-y-2">
                    {qs.map((q) => (
                      <div
                        key={q.id}
                        className="flex items-start gap-2 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2"
                      >
                        <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 flex-shrink-0" />
                        <span className="text-sm text-gray-800">{q.question}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              <div className="pt-3 border-t border-gray-100 text-sm text-gray-600">
                Total: <span className="font-semibold text-gray-900">{selectedQuestions.size}</span> questions
              </div>
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 justify-center pb-8">
          <button
            onClick={handleReset}
            className="px-5 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            Create Another
          </button>
          <button
            onClick={() => navigate(`/professor/assignments/${assignmentId}/grades`)}
            className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            View Grades →
          </button>
          <button
            onClick={() => navigate('/professor/dashboard')}
            className="px-5 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Create Assignment</h1>
        <p className="text-sm text-gray-500 mt-1">Set up an oral examination from your generated questions</p>
      </div>

      {/* ── Configuration Step ── */}
      {!showVariants && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-6">
          {/* Course */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Course</label>
            <select
              value={courseId}
              onChange={(e) => { setCourseId(e.target.value); setSelectedQuestions(new Set()); }}
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

          {/* Duration + Difficulty */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Duration (minutes)</label>
              <input
                type="number"
                value={duration}
                onChange={(e) => setDuration(+e.target.value)}
                min={5} max={120}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Difficulty Focus</label>
              <select
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="recall">Recall — definitions & facts</option>
                <option value="balanced">Balanced — recall + reasoning</option>
                <option value="deep">Deep — causal reasoning</option>
              </select>
            </div>
          </div>

          {/* Adaptive toggle */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setAdaptive(!adaptive)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${adaptive ? 'bg-blue-600' : 'bg-gray-200'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${adaptive ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
            <div>
              <p className="text-sm font-medium text-gray-700">Adaptive Difficulty</p>
              <p className="text-xs text-gray-500">AI adjusts follow-up probing based on student performance</p>
            </div>
          </div>

          {/* Question selection */}
          {courseId && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <label className="text-sm font-medium text-gray-700">
                  Questions ({selectedQuestions.size} of {questions.length} selected)
                </label>
                <button
                  onClick={() => {
                    if (selectedQuestions.size === questions.length) setSelectedQuestions(new Set());
                    else setSelectedQuestions(new Set(questions.map((q: any) => q.question_id)));
                  }}
                  className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                >
                  {selectedQuestions.size === questions.length ? 'Deselect All' : 'Select All'}
                </button>
              </div>
              {questions.length === 0 ? (
                <p className="text-sm text-gray-500 p-4 bg-gray-50 rounded-lg text-center">
                  No questions available. Generate questions from the course materials first.
                </p>
              ) : (
                <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-80 overflow-y-auto">
                  {questions.map((q: any, i: number) => (
                    <label
                      key={q.question_id || i}
                      className="flex items-start gap-3 p-3 hover:bg-gray-50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedQuestions.has(q.question_id)}
                        onChange={() => toggleQuestion(q.question_id)}
                        className="mt-0.5 h-4 w-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-gray-900">{q.question || q.text}</p>
                        <div className="flex gap-2 mt-1">
                          <span className="text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">{q.topic || 'General'}</span>
                          <span className={`text-xs px-1.5 py-0.5 rounded ${q.status === 'approved' ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                            {q.status || 'draft'}
                          </span>
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}

          {/* Build Exam button */}
          <button
            onClick={handleBuildExam}
            disabled={!canSubmit}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Build Exam
          </button>
        </div>
      )}

      {/* ── Variant Selection Step ── */}
      {showVariants && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-6">
          {/* Header */}
          <div>
            <button
              onClick={handleBackToConfig}
              className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-3 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to configuration
            </button>
            <h2 className="text-lg font-semibold text-gray-900">Choose Your Exam Variant</h2>
            <p className="text-sm text-gray-500 mt-1">
              Three variations of your <span className="font-medium capitalize">{difficulty}</span> exam,
              each distributing your {selectedQuestions.size} questions differently across topics.
            </p>
          </div>

          {/* Summary stats */}
          <div className="flex flex-wrap gap-4 text-sm text-gray-600 pb-2 border-b border-gray-100">
            <span>{selectedQuestions.size} questions</span>
            <span>{duration} min</span>
            <span>{new Set(selectedQsList.map((q: any) => q.topic || 'General')).size} topics</span>
            <span className="capitalize">{difficulty} focus</span>
          </div>

          {/* Variant cards */}
          <div className="space-y-4">
            {variants.map((variant) => (
              <div
                key={variant.id}
                onClick={() => setSelectedVariant(variant.id)}
                className={`relative cursor-pointer rounded-lg border-2 p-5 transition-all ${
                  selectedVariant === variant.id
                    ? 'border-blue-500 bg-blue-50/30 shadow-sm'
                    : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/50'
                }`}
              >
                {/* Radio indicator */}
                <div className="absolute top-5 right-5">
                  <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                    selectedVariant === variant.id
                      ? 'border-blue-500 bg-blue-500'
                      : 'border-gray-300'
                  }`}>
                    {selectedVariant === variant.id && (
                      <div className="w-2 h-2 rounded-full bg-white" />
                    )}
                  </div>
                </div>

                {/* Card header */}
                <div className="flex items-center gap-3 mb-2 pr-8">
                  <h3 className="text-sm font-semibold text-gray-900">{variant.title}</h3>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    variant.badge === 'Foundational' ? 'bg-green-100 text-green-700' :
                    variant.badge === 'Intermediate' ? 'bg-amber-100 text-amber-700' :
                    'bg-purple-100 text-purple-700'
                  }`}>
                    {variant.badge}
                  </span>
                </div>

                {/* Description */}
                <p className="text-sm text-gray-600 mb-3 leading-relaxed">
                  {variant.description}
                </p>

                {/* Question distribution tags */}
                <div>
                  <p className="text-xs font-medium text-gray-500 mb-2">Question Distribution</p>
                  <div className="flex flex-wrap gap-1.5">
                    {variant.distribution.map((d) => (
                      <span
                        key={d.id}
                        className="inline-flex items-center text-xs bg-gray-100 text-gray-700 px-2 py-1 rounded-md"
                      >
                        {d.label} <span className="ml-1 font-semibold text-gray-900">&times;{d.count}</span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Error */}
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}

          {/* Assign button */}
          <button
            onClick={handleCreate}
            disabled={!selectedVariant || creating || created}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {creating && <Loader2 className="w-4 h-4 animate-spin" />}
            {created && <CheckCircle className="w-4 h-4" />}
            {created ? 'Assignment Created!' : creating ? 'Creating...' : 'Assign This Exam'}
          </button>
        </div>
      )}
    </div>
  );
}
