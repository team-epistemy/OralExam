import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FileText, Loader2, Sparkles, AlertCircle } from 'lucide-react';
import { get } from '../../api/client';
import { buildExam, assignExam, type BuildExamResponse, type ExamVariant } from '../../api/exam';

interface Course {
  course_id: string;
  course_name: string;
}

interface GraphData {
  concepts?: { id?: string; node_id?: string; label: string }[];
  node_count?: number;
}

const DIFFICULTIES = [
  { key: 'recall', label: 'Recall', desc: 'Definitions & formula recall' },
  { key: 'balanced', label: 'Balanced', desc: 'Recall + causal reasoning' },
  { key: 'deep', label: 'Deep', desc: 'Causal chains & prerequisites' },
] as const;

type Difficulty = (typeof DIFFICULTIES)[number]['key'];

export default function BuildExam() {
  const [params] = useSearchParams();
  const [courseId, setCourseId] = useState(params.get('course') || '');
  const [selectedTopics, setSelectedTopics] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<Difficulty>('balanced');
  const [qCount, setQCount] = useState(12);
  const [examLen, setExamLen] = useState(30);
  const [variants, setVariants] = useState<ExamVariant[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assigning, setAssigning] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);
  const navigate = useNavigate();

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<Course[]>('/api/professor/courses'),
    retry: false,
  });

  const { data: graph } = useQuery({
    queryKey: ['graph-for-exam', courseId],
    queryFn: () => get<GraphData>(`/api/courses/${courseId}/graph`),
    enabled: !!courseId,
    retry: false,
  });

  const conceptCount = graph?.node_count || graph?.concepts?.length || 0;
  const selected = variants.find((v) => v.id === selectedId) || null;

  const handleBuild = async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    setVariants([]);
    setSelectedId(null);
    try {
      const res: BuildExamResponse = await buildExam(courseId, {
        q_count: qCount,
        exam_len: examLen,
        difficulty,
        concept_ids: selectedTopics.length ? selectedTopics : undefined,
      });
      if (res.status !== 'completed' || !res.variants?.length) {
        setError(res.message || 'No concept graph found. Build the concept graph for this course first.');
      } else {
        setVariants(res.variants);
        setSelectedId(res.variants[0].id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to build exam.');
    } finally {
      setLoading(false);
    }
  };

  const handleAssign = async () => {
    if (!selected || !courseId) return;
    const title = window.prompt('Name this assignment:', `${selected.title}`);
    if (!title) return;
    setAssigning(true);
    setAssignError(null);
    try {
      const res = await assignExam(courseId, {
        title,
        questions: selected.questions,
        difficulty,
        duration_minutes: examLen,
      });
      if (res.status === 'completed' && res.assignment_id) {
        navigate(`/professor/assignments/${res.assignment_id}/grades`);
      } else {
        setAssignError(res.message || 'Failed to create assignment.');
      }
    } catch (e) {
      setAssignError(e instanceof Error ? e.message : 'Failed to create assignment.');
    } finally {
      setAssigning(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <FileText className="w-6 h-6 text-indigo-600" /> Build Exam
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Generate three exam variants from the course concept graph — evenly, foundation-weighted, or advanced-weighted.
        </p>
      </div>

      {/* Config panel */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-sm font-medium text-gray-700">Course</span>
            <select
              value={courseId}
              onChange={(e) => { setCourseId(e.target.value); setVariants([]); setError(null); setSelectedTopics([]); }}
              className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            >
              <option value="">Select a course…</option>
              {courses.map((c) => (
                <option key={c.course_id} value={c.course_id}>{c.course_name}</option>
              ))}
            </select>
          </label>
          <div className="flex items-end text-sm text-gray-500">
            {courseId ? `${conceptCount} concept${conceptCount === 1 ? '' : 's'} in the graph` : 'Pick a course to load its concept graph'}
          </div>
        </div>

        <div>
          <span className="text-sm font-medium text-gray-700">Difficulty</span>
          <div className="mt-1 grid grid-cols-3 gap-2">
            {DIFFICULTIES.map((d) => (
              <button
                key={d.key}
                type="button"
                onClick={() => setDifficulty(d.key)}
                className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                  difficulty === d.key
                    ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500'
                    : 'border-gray-300 hover:bg-gray-50'
                }`}
              >
                <div className="text-sm font-semibold text-gray-900">{d.label}</div>
                <div className="text-xs text-gray-500">{d.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {courseId && (graph?.concepts?.length ?? 0) > 0 && (
          <div>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700">
                Topics <span className="text-gray-400 font-normal">(optional — all topics if none selected)</span>
              </span>
              <div className="flex gap-3 text-xs">
                <button type="button" onClick={() => setSelectedTopics((graph?.concepts || []).map((c) => c.label))} className="text-indigo-600 hover:text-indigo-700">Select all</button>
                <button type="button" onClick={() => setSelectedTopics([])} className="text-gray-500 hover:text-gray-700">Clear</button>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {(graph?.concepts || []).map((c) => {
                const on = selectedTopics.includes(c.label);
                return (
                  <button
                    key={c.id || c.node_id || c.label}
                    type="button"
                    onClick={() => setSelectedTopics((prev) => (on ? prev.filter((t) => t !== c.label) : [...prev, c.label]))}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                      on ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {c.label}
                  </button>
                );
              })}
            </div>
            {selectedTopics.length > 0 && (
              <p className="mt-1.5 text-xs text-gray-500">{selectedTopics.length} topic{selectedTopics.length === 1 ? '' : 's'} selected — the exam will focus on these.</p>
            )}
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-sm font-medium text-gray-700">Questions: {qCount}</span>
            <input type="range" min={5} max={25} value={qCount}
              onChange={(e) => setQCount(Number(e.target.value))}
              className="mt-2 w-full accent-indigo-600" />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-gray-700">Exam length: {examLen} min</span>
            <input type="range" min={10} max={60} step={5} value={examLen}
              onChange={(e) => setExamLen(Number(e.target.value))}
              className="mt-2 w-full accent-indigo-600" />
          </label>
        </div>

        <button
          onClick={handleBuild}
          disabled={!courseId || loading}
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
          {loading ? 'Building…' : 'Build 3 exam variants'}
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Variant cards */}
      {variants.length > 0 && (
        <div className="grid gap-4 md:grid-cols-3">
          {variants.map((v) => (
            <button
              key={v.id}
              onClick={() => setSelectedId(v.id)}
              className={`text-left rounded-xl border p-4 transition-all ${
                selectedId === v.id
                  ? 'border-indigo-500 ring-2 ring-indigo-500 bg-indigo-50/40'
                  : 'border-gray-200 hover:border-gray-300 bg-white'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wide text-indigo-600">{v.angle_label || v.badge_label}</span>
                <span className="text-xs text-gray-400">{v.duration}</span>
              </div>
              <h3 className="mt-1 font-semibold text-gray-900">{v.title}</h3>
              <p className="mt-1 text-xs text-gray-500 leading-relaxed">{v.description}</p>
              <div className="mt-3 text-xs text-gray-600">
                <span className="font-medium">{v.q_count} questions</span> · {v.eds_focus}
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Selected variant detail */}
      {selected && (
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-gray-900">{selected.title} — {selected.questions.length} questions</h2>
            <span className="text-xs text-gray-500">
              per-concept: {selected.distribution.map((d) => `${d.label} ×${d.count}`).join(' · ')}
            </span>
          </div>
          <ol className="space-y-2">
            {selected.questions.map((q, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="text-gray-400 w-6 flex-shrink-0">{i + 1}.</span>
                <span>
                  <span className="inline-block text-xs font-medium text-indigo-700 bg-indigo-50 rounded px-1.5 py-0.5 mr-2">{q.topic}</span>
                  <span className="text-gray-800">{q.q}</span>
                </span>
              </li>
            ))}
          </ol>
          <div className="mt-4 pt-3 border-t border-gray-100 flex flex-wrap items-center gap-3">
            <button
              onClick={handleAssign}
              disabled={assigning}
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {assigning ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Assign this exam to students →
            </button>
            {assignError && <span className="text-sm text-red-600">{assignError}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
