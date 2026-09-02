import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Clock, Mic, Volume2, VolumeX, BookOpen, Eye, Loader2, AlertTriangle } from 'lucide-react';
import { getAssignmentPreview } from '../../api/exam';
import { API_BASE_URL } from '../../config';
import DocumentViewerModal from '../../components/DocumentViewerModal';

const TYPE_LABEL: Record<string, string> = {
  practice: 'Practice Test',
  exam: 'Exam',
  assignment: 'Assignment',
};

// Mirror TakeExam's speechFriendly-lite: strip a few symbols so TTS reads cleanly.
function speakable(text: string): string {
  return (text || '')
    .replace(/[*_`#>]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Professor-facing, read-only preview of the exact student exam view.
 * Renders the same chrome/layout as pages/student/TakeExam.tsx (`taking` phase)
 * but never starts a session, submits an answer, or grades: the answer box and
 * mic are inert, the timer is frozen, and TTS is opt-in (off by default).
 */
export default function AssignmentPreview() {
  const { assignmentId } = useParams<{ assignmentId: string }>();
  const navigate = useNavigate();
  const [current, setCurrent] = useState(0);
  const [caseViewIdx, setCaseViewIdx] = useState<number | null>(null);
  const [ttsOn, setTtsOn] = useState(false);
  const [ttsAvailable, setTtsAvailable] = useState(true);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const spokenRef = useRef<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['assignment-preview', assignmentId],
    queryFn: () => getAssignmentPreview(assignmentId!),
    enabled: !!assignmentId,
    retry: false,
  });

  const questions = data?.questions ?? [];
  const caseMaterials = data?.case_materials ?? [];
  const N = questions.length;
  const q = questions[current];
  const typeLabel = TYPE_LABEL[data?.assignment_type || 'assignment'] || 'Assignment';

  const speak = useCallback(async (text: string) => {
    const spoken = speakable(text);
    if (!spoken) return;
    try {
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_BASE_URL}/api/tts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ text: spoken }),
      });
      if (!resp.ok) {
        if (resp.status === 503) setTtsAvailable(false);
        return;
      }
      setTtsAvailable(true);
      const url = URL.createObjectURL(await resp.blob());
      audioRef.current?.pause();
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.play().catch(() => { /* autoplay may be blocked until a gesture */ });
    } catch { /* network / unsupported -> silent */ }
  }, []);

  // Speak the current question only while TTS is opted in (never auto-plays on load).
  useEffect(() => {
    if (!ttsOn || !q) return;
    const line = `Question ${current + 1}. ${q.text}`;
    if (line === spokenRef.current) return;
    spokenRef.current = line;
    speak(line);
  }, [ttsOn, q, current, speak]);

  // Always stop audio when leaving the page or muting.
  useEffect(() => () => { audioRef.current?.pause(); }, []);
  const toggleTts = () => {
    setTtsOn((v) => {
      const next = !v;
      if (!next) { audioRef.current?.pause(); spokenRef.current = null; }
      return next;
    });
  };

  const goTo = (i: number) => { if (i >= 0 && i < N) setCurrent(i); };

  const durationLabel = data?.duration_minutes
    ? `${String(data.duration_minutes).padStart(2, '0')}:00`
    : 'Untimed';

  if (isLoading) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="max-w-lg mx-auto mt-16 bg-white border border-gray-200 rounded-xl p-8 text-center">
        <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto mb-3" />
        <p className="text-sm text-gray-600">Couldn't load this assignment for preview.</p>
        <button onClick={() => navigate(-1)} className="mt-4 text-sm text-blue-600 hover:underline">Go back</button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto flex flex-col min-h-[80vh]">
      {/* Preview banner (professor-only chrome; not part of the student view) */}
      <div className="flex items-center gap-2 bg-purple-50 border border-purple-200 text-purple-800 rounded-xl px-4 py-2.5 mb-3 text-sm">
        <Eye className="w-4 h-4 flex-shrink-0" />
        <span className="flex-1">
          <span className="font-semibold">Student preview</span> — this is exactly what students see for
          <span className="font-medium"> “{data.title}”</span>. Answering, the timer, and grading are disabled.
          Audio narration is off by default; turn it on to hear a question.
        </span>
        <button onClick={() => navigate(-1)} className="px-3 py-1 border border-purple-300 rounded-lg hover:bg-purple-100 text-purple-700 whitespace-nowrap">
          Close preview
        </button>
      </div>

      {/* Header bar — mirrors TakeExam taking-phase header */}
      <div className="bg-blue-600 rounded-t-xl px-5 py-3 flex items-center justify-between">
        <div>
          <p className="text-blue-200 text-[11px] font-semibold uppercase tracking-wide">{typeLabel}</p>
          <p className="text-white font-semibold text-lg">{q?.topic || 'Assessment'}</p>
        </div>
        <div className="flex items-center gap-3">
          {caseMaterials.length > 0 && (
            <button
              onClick={() => setCaseViewIdx(0)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-white/15 text-white rounded-lg text-sm font-medium hover:bg-white/25"
              title="View the case document"
            >
              <BookOpen className="w-4 h-4" /> View Case
            </button>
          )}
          <div
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-mono font-bold bg-white/10 text-white/90"
            title="Timer is frozen in preview"
          >
            <Clock className="w-4 h-4" /> {durationLabel}
          </div>
          <button
            onClick={() => navigate(-1)}
            className="px-3 py-1.5 bg-white/10 text-white rounded-lg text-sm hover:bg-white/20"
          >
            <ChevronLeft className="w-4 h-4 inline -mt-0.5" /> Back
          </button>
          <button
            disabled
            title="Disabled in preview"
            className="px-4 py-1.5 bg-white/60 text-blue-700/60 rounded-lg text-sm font-semibold cursor-not-allowed"
          >
            Submit Exam
          </button>
        </div>
      </div>

      {caseViewIdx !== null && caseMaterials[caseViewIdx] && (
        <DocumentViewerModal
          materialId={caseMaterials[caseViewIdx].version_id}
          fallbackName={caseMaterials[caseViewIdx].file_name}
          onClose={() => setCaseViewIdx(null)}
        />
      )}

      {/* Question navigation grid */}
      <div className="bg-white border-x border-gray-200 px-5 py-3">
        <div className="flex justify-between text-xs text-gray-400 mb-2">
          <span>Question {current + 1} of {N}</span>
          <span>{typeLabel} · {data.difficulty}</span>
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {questions.map((_, i) => {
            const isCur = i === current;
            const bg = isCur ? 'bg-amber-400 text-white border-amber-400' : 'bg-gray-100 text-gray-600 border-gray-200';
            return (
              <button
                key={i}
                onClick={() => goTo(i)}
                title={`Question ${i + 1}`}
                className={`w-8 h-8 rounded-lg border text-xs font-bold cursor-pointer transition-colors ${bg} ${isCur ? 'ring-2 ring-blue-600 ring-offset-1' : ''}`}
              >
                {i + 1}
              </button>
            );
          })}
        </div>
      </div>

      {/* Two-column layout */}
      <div className="flex-1 flex gap-5 bg-white border border-gray-200 border-t-0 rounded-b-xl p-5">
        {/* Left: the question, as the student sees it */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex-1 max-h-[400px] overflow-y-auto pr-2 mb-4 space-y-4">
            {q ? (
              <div className="flex flex-col items-start">
                <span className="text-[10px] font-bold uppercase tracking-wide mb-1 text-blue-700">Evaluator</span>
                <div className="max-w-[85%] px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap rounded-xl bg-gray-50 border border-gray-200 text-gray-800 rounded-bl-sm">
                  Question {current + 1}. {q.text}
                </div>
              </div>
            ) : (
              <p className="text-sm text-gray-400">This assignment has no questions.</p>
            )}
          </div>

          {/* Inert answer input — shows the student layout but nothing submits */}
          <div className="mb-3">
            <textarea
              value=""
              readOnly
              disabled
              placeholder="Answer the question above... (Cmd+Enter to submit)"
              rows={3}
              className="w-full px-4 py-3 border border-gray-300 rounded-xl text-sm leading-relaxed resize-none bg-gray-50 opacity-60 cursor-not-allowed"
            />
            <div className="flex gap-2 mt-2">
              <button
                disabled
                title="Voice answering is disabled in preview"
                className="flex items-center justify-center px-3 py-2.5 rounded-lg border border-gray-300 text-gray-400 cursor-not-allowed"
              >
                <Mic className="w-4 h-4" />
              </button>
              <button
                onClick={toggleTts}
                title={ttsOn ? 'Turn question audio off' : 'Hear this question (TTS)'}
                className={`flex items-center justify-center px-3 py-2.5 rounded-lg border text-sm font-medium transition-colors ${
                  ttsOn ? 'bg-blue-50 border-blue-300 text-blue-600' : 'border-gray-300 text-gray-700 hover:bg-gray-50'
                }`}
              >
                {ttsOn ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
              </button>
              <button
                disabled
                title="Disabled in preview"
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600/50 text-white rounded-lg text-sm font-medium cursor-not-allowed"
              >
                Submit Answer
              </button>
            </div>
            {ttsOn && !ttsAvailable && (
              <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
                <VolumeX className="w-3.5 h-3.5 flex-shrink-0" />
                Audio narration is unavailable right now — questions are shown on screen.
              </div>
            )}
          </div>

          {/* Prev / Next navigation */}
          <div className="flex items-center gap-3 mt-auto pt-2">
            <button
              onClick={() => goTo(current - 1)}
              disabled={current === 0}
              className="flex items-center gap-1 px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" /> Previous
            </button>
            <div className="flex-1" />
            <button
              onClick={() => goTo(current + 1)}
              disabled={current >= N - 1}
              className="flex items-center gap-1 px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Right sidebar: preview facts (replaces the live EDS gauge students see) */}
        <div className="w-64 flex-shrink-0 hidden lg:block">
          <div className="border border-gray-200 rounded-xl overflow-hidden">
            <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
              <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400">Preview details</p>
            </div>
            <dl className="p-5 space-y-3 text-sm">
              <div className="flex justify-between"><dt className="text-gray-500">Type</dt><dd className="font-medium text-gray-800">{typeLabel}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">Questions</dt><dd className="font-medium text-gray-800">{N}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">Difficulty</dt><dd className="font-medium text-gray-800 capitalize">{data.difficulty}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">Duration</dt><dd className="font-medium text-gray-800">{data.duration_minutes ? `${data.duration_minutes} min` : 'Untimed'}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">Case-based</dt><dd className="font-medium text-gray-800">{data.include_case ? 'Yes' : 'No'}</dd></div>
            </dl>
            <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
              In the live exam this panel shows the student's running EDS score.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
