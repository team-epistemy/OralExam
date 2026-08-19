import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { Send, Loader2, CheckCircle, ChevronLeft, ChevronRight, Clock, Mic, Volume2, VolumeX, BookOpen } from 'lucide-react';
import { jsPDF } from 'jspdf';
import { startExamSession, submitAnswer, getSessionStatus, completeSession, getAssignmentCase } from '../../api/exam';
import type { CaseMaterial } from '../../api/exam';
import { get } from '../../api/client';
import { API_BASE_URL } from '../../config';
import DocumentViewerModal from '../../components/DocumentViewerModal';
import type { ExamQuestion, AnswerResponse, EDSComponents } from '../../api/exam';

// ── Types ────────────────────────────────────────────────────────────────────

interface Turn {
  role: 'evaluator' | 'student';
  text: string;
}

interface QuestionState {
  turns: Turn[];
  attempts: number;
  attempted: boolean;
  done: boolean;
  score: number;
  edsComponents: EDSComponents | null;
}

type Phase = 'loading' | 'taking' | 'review' | 'done';

const MAX_TURNS = 5;
const TIMER_WARNING_SECONDS = 5 * 60;
const TIMER_CRITICAL_SECONDS = 60;
const STORAGE_KEY_PREFIX = 'epistemy_exam_';

// ── Session Persistence ─────────────────────────────────────────────────────

const EXAM_STATE_VERSION = 2;

interface SavedExamState {
  version: number;
  sessionId: string;
  questions: ExamQuestion[];
  qData: QuestionState[];
  current: number;
  startTime: number;
  durationMinutes: number | null;
}

function getStorageKey(assignmentId: string): string {
  return `${STORAGE_KEY_PREFIX}${assignmentId}`;
}

function saveExamState(assignmentId: string, state: SavedExamState): boolean {
  try {
    localStorage.setItem(getStorageKey(assignmentId), JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}

function loadExamState(assignmentId: string): SavedExamState | null {
  try {
    const raw = localStorage.getItem(getStorageKey(assignmentId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed.version !== EXAM_STATE_VERSION) {
      clearExamState(assignmentId);
      return null;
    }
    return parsed as SavedExamState;
  } catch {
    clearExamState(assignmentId);
    return null;
  }
}

function clearExamState(assignmentId: string): void {
  localStorage.removeItem(getStorageKey(assignmentId));
}

// ── Assignment metadata ─────────────────────────────────────────────────────

interface AssignmentMeta {
  duration_minutes: number | null;
}

async function fetchAssignmentMeta(assignmentId: string): Promise<AssignmentMeta> {
  const data = await get<{ config?: { time_limit_minutes?: number } }>(`/api/assignments/${assignmentId}`);
  return { duration_minutes: data.config?.time_limit_minutes ?? null };
}

// ── Concept Graph SVG ────────────────────────────────────────────────────────

interface ConceptNode {
  id: string;
  label: string;
  x: number;
  y: number;
}

interface ConceptEdge {
  from: string;
  to: string;
}

function ConceptGraphSVG({
  questions,
  qData,
  currentIndex,
}: {
  questions: ExamQuestion[];
  qData: QuestionState[];
  currentIndex: number;
}) {
  // Derive unique topics and map question indices to them
  const topicOrder: string[] = [];
  const topicQuestionMap: Map<string, number[]> = new Map();

  questions.forEach((q, i) => {
    const topic = q.topic || `Q${i + 1}`;
    if (!topicOrder.includes(topic)) {
      topicOrder.push(topic);
      topicQuestionMap.set(topic, []);
    }
    topicQuestionMap.get(topic)!.push(i);
  });

  // Position nodes in a 3-column grid
  const COLS = 3;
  const NODE_R = 24;
  const COL_GAP = 84;
  const ROW_GAP = 72;
  const PAD_X = 46;
  const PAD_Y = 40;

  const nodes: ConceptNode[] = topicOrder.map((topic, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    return {
      id: topic,
      label: topic,
      x: PAD_X + col * COL_GAP,
      y: PAD_Y + row * ROW_GAP,
    };
  });

  // Edges: connect sequential unique topics
  const edges: ConceptEdge[] = [];
  for (let i = 0; i < topicOrder.length - 1; i++) {
    edges.push({ from: topicOrder[i], to: topicOrder[i + 1] });
  }

  // Determine traversed topics (any question for that topic attempted)
  const traversed = new Set<string>();
  topicOrder.forEach((topic) => {
    const indices = topicQuestionMap.get(topic) || [];
    if (indices.some((idx) => qData[idx]?.attempted)) {
      traversed.add(topic);
    }
  });

  // Current topic
  const currentTopic = questions[currentIndex]?.topic || `Q${currentIndex + 1}`;

  // SVG dimensions based on grid
  const totalRows = Math.ceil(topicOrder.length / COLS);
  const W = PAD_X * 2 + (COLS - 1) * COL_GAP;
  const H = PAD_Y * 2 + Math.max(0, totalRows - 1) * ROW_GAP;

  const coveredCount = traversed.size;
  const totalTopics = topicOrder.length;

  if (totalTopics === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
      >
        <defs>
          <marker id="edge-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L0,6 L6,3 z" fill="#a78bfa" />
          </marker>
          <marker id="edge-arrow-active" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L0,6 L6,3 z" fill="#7c3aed" />
          </marker>
        </defs>

        {/* Edges */}
        {edges.map((edge, i) => {
          const fromNode = nodes.find((n) => n.id === edge.from);
          const toNode = nodes.find((n) => n.id === edge.to);
          if (!fromNode || !toNode) return null;

          const active = traversed.has(edge.from) && traversed.has(edge.to);

          // Shorten line to avoid overlapping circles
          const dx = toNode.x - fromNode.x;
          const dy = toNode.y - fromNode.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const offsetRatio = NODE_R / dist;
          const x1 = fromNode.x + dx * offsetRatio;
          const y1 = fromNode.y + dy * offsetRatio;
          const x2 = toNode.x - dx * offsetRatio;
          const y2 = toNode.y - dy * offsetRatio;

          return (
            <line
              key={i}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={active ? '#7c3aed' : '#d1d5db'}
              strokeWidth={active ? 2 : 1.5}
              markerEnd={active ? 'url(#edge-arrow-active)' : 'url(#edge-arrow)'}
              style={{ transition: 'stroke 0.4s' }}
            />
          );
        })}

        {/* Nodes */}
        {nodes.map((n) => {
          const isTraversed = traversed.has(n.id);
          const isCurrent = n.id === currentTopic;

          let fill = '#f3f4f6'; // gray-100
          let stroke = '#d1d5db'; // gray-300
          let textFill = '#6b7280'; // gray-500
          let strokeWidth = 1.5;

          if (isTraversed) {
            fill = '#dbeafe'; // blue-100
            stroke = '#2563eb'; // blue-600
            textFill = '#1e40af'; // blue-800
            strokeWidth = 2;
          }
          if (isCurrent) {
            fill = isTraversed ? '#bfdbfe' : '#fef3c7'; // blue-200 or amber-100
            stroke = isTraversed ? '#2563eb' : '#f59e0b'; // blue-600 or amber-500
            strokeWidth = 2.5;
          }

          // Truncate label for display
          const displayLabel =
            n.label.length > 14 ? n.label.slice(0, 12) + '...' : n.label;
          const words = displayLabel.split(' ');

          return (
            <g key={n.id}>
              <circle
                cx={n.x}
                cy={n.y}
                r={NODE_R}
                fill={fill}
                stroke={stroke}
                strokeWidth={strokeWidth}
                style={{ transition: 'all 0.4s' }}
              />
              <text
                x={n.x}
                y={n.y}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={9}
                fontWeight={600}
                fill={textFill}
                style={{ transition: 'fill 0.4s', userSelect: 'none' }}
              >
                {words.length === 1 ? (
                  words[0]
                ) : (
                  words.map((word, wi) => (
                    <tspan
                      key={wi}
                      x={n.x}
                      dy={wi === 0 ? -(words.length - 1) * 5 : 11}
                    >
                      {word}
                    </tspan>
                  ))
                )}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Coverage progress bar */}
      <div className="px-1">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-gray-500 font-semibold uppercase tracking-wide text-[10px]">
            Coverage
          </span>
          <span className="text-gray-900 font-bold text-[10px]">
            {coveredCount}/{totalTopics} concepts
          </span>
        </div>
        <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-green-400 to-emerald-500 rounded-full transition-all duration-500"
            style={{ width: `${(coveredCount / Math.max(totalTopics, 1)) * 100}%` }}
          />
        </div>
      </div>
    </div>
  );
}

// ── EDS Arc Gauge ────────────────────────────────────────────────────────────

function EDSGauge({ score }: { score: number }) {
  const r = 54, cx = 70, cy = 70;
  const circ = Math.PI * r; // half-circle arc length
  const pct = Math.min(score / 100, 1);
  const dash = pct * circ;

  const band = score >= 85
    ? { label: "Distinction", color: "#22c55e" }
    : score >= 70
    ? { label: "Proficient", color: "#2563eb" }
    : score >= 50
    ? { label: "Developing", color: "#f59e0b" }
    : { label: "Starting", color: "#6b7280" };

  return (
    <div className="flex flex-col items-center">
      <svg width={140} height={80} viewBox="0 0 140 80">
        {/* Background arc */}
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none"
          stroke="#e5e7eb"
          strokeWidth={10}
          strokeLinecap="round"
        />
        {/* Score arc */}
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none"
          stroke={band.color}
          strokeWidth={10}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circ}`}
          style={{ transition: "stroke-dasharray 0.6s ease, stroke 0.4s" }}
        />
        <text x={cx} y={cy - 8} textAnchor="middle" fontSize={26} fontWeight={700}
          fill="#1e293b" fontFamily="DM Serif Display, serif">{Math.round(score)}</text>
        <text x={cx} y={cy + 8} textAnchor="middle" fontSize={10} fill="#6b7280"
          fontFamily="Inter, sans-serif">EDS</text>
      </svg>
      <div className="-mt-1 text-xs font-bold" style={{ color: band.color }}>
        {band.label}
      </div>
    </div>
  );
}

// ── EDS Breakdown ───────────────────────────────────────────────────────────

function EDSBreakdown({ components }: { components: EDSComponents | null }) {
  if (!components) return null;

  const items = [
    { label: 'Concepts', value: components.node_score, color: 'bg-blue-500' },
    { label: 'Causal Links', value: components.edge_score, color: 'bg-purple-500' },
    { label: 'Authenticity', value: components.r_gate, color: 'bg-green-500' },
    { label: 'Novel Insight', value: components.gen_score, color: 'bg-amber-500' },
  ];

  return (
    <div className="px-3 py-2 space-y-1.5">
      <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400">Score Breakdown</p>
      {items.map(item => (
        <div key={item.label} className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 w-16 truncate">{item.label}</span>
          <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${item.color}`}
              style={{ width: `${Math.round(item.value * 100)}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-gray-500 w-7 text-right">
            {Math.round(item.value * 100)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────────────

export default function TakeExam() {
  const { assignmentId } = useParams<{ assignmentId: string }>();
  const navigate = useNavigate();

  // Session state
  const [phase, setPhase] = useState<Phase>('loading');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [questions, setQuestions] = useState<ExamQuestion[]>([]);
  const [current, setCurrent] = useState(0);
  const [qData, setQData] = useState<QuestionState[]>([]);
  const [draft, setDraft] = useState('');
  const [showTranscript, setShowTranscript] = useState(false);
  const [error, setError] = useState('');

  // Timer state
  const [startTime, setStartTime] = useState<number | null>(null);
  const [durationMinutes, setDurationMinutes] = useState<number | null>(null);
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Voice: TTS (ElevenLabs proxy) speaks questions/probes; ASR (Web Speech API) dictates answers.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const recognitionRef = useRef<{ stop: () => void; start: () => void } | null>(null);
  const spokenRef = useRef<string>('');
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [ttsOn, setTtsOn] = useState(true);

  // Case context: the source document(s) this exam is drawn from, kept viewable
  // throughout so the student can always re-read the case while answering.
  const [caseMaterials, setCaseMaterials] = useState<CaseMaterial[]>([]);
  const [caseViewIdx, setCaseViewIdx] = useState<number | null>(null);

  useEffect(() => {
    if (!assignmentId) return;
    let cancelled = false;
    getAssignmentCase(assignmentId)
      .then((mats) => { if (!cancelled) setCaseMaterials(mats); })
      .catch(() => { if (!cancelled) setCaseMaterials([]); });
    return () => { cancelled = true; };
  }, [assignmentId]);

  const speak = useCallback(async (text: string) => {
    if (!ttsOn || !text) return;
    try {
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_BASE_URL}/api/tts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) return; // 503 = TTS not configured -> stay silent
      const url = URL.createObjectURL(await resp.blob());
      audioRef.current?.pause();
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.play().catch(() => { /* autoplay may be blocked until a gesture */ });
    } catch { /* network / unsupported -> silent */ }
  }, [ttsOn]);

  // Fully stop dictation. Called on toggle-off, on submit, and on question
  // change so one answer's speech never bleeds into the next (issue: spillover).
  const stopMic = useCallback(() => {
    const rec = recognitionRef.current as any;
    recognitionRef.current = null;
    if (rec) { try { rec.onend = null; rec.stop(); } catch { /* already stopped */ } }
    setListening(false);
    setSpeaking(false);
  }, []);

  const toggleMic = () => {
    const SR = (window as unknown as { SpeechRecognition?: new () => any; webkitSpeechRecognition?: new () => any });
    const Ctor = SR.SpeechRecognition || SR.webkitSpeechRecognition;
    if (!Ctor) { setError('Speech recognition is not supported here — try Chrome, Edge, or Safari.'); return; }
    if (listening) { stopMic(); return; }
    const rec: any = new Ctor();
    rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = true;
    // Anchor dictation to whatever is in the box *now*; a fresh recognizer per
    // answer means a stale transcript can't carry over into a new question.
    let base = draft;
    rec.onresult = (e: any) => {
      let interim = '', finalT = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalT += t; else interim += t;
      }
      if (finalT) base = (base ? base + ' ' : '') + finalT.trim();
      setDraft((base + (interim ? ' ' + interim : '')).trim());
    };
    rec.onspeechstart = () => setSpeaking(true);
    rec.onspeechend = () => setSpeaking(false);
    rec.onend = () => { setListening(false); setSpeaking(false); };
    rec.onerror = () => { setListening(false); setSpeaking(false); };
    recognitionRef.current = rec;
    rec.start();
    setListening(true);
  };

  // Prevent two-tab desync via BroadcastChannel
  useEffect(() => {
    if (!assignmentId) return;
    const channel = new BroadcastChannel(`exam_${assignmentId}`);
    channel.postMessage('open');
    channel.onmessage = () => {
      setError('This exam is already open in another tab. Please close one to avoid data loss.');
    };
    return () => channel.close();
  }, [assignmentId]);

  // Derived
  const N = questions.length;
  const cur = qData[current];

  // Auto-speak the latest examiner line (question or probe) as it appears.
  useEffect(() => {
    if (phase !== 'taking' || !cur) return;
    const evals = cur.turns.filter((t) => t.role === 'evaluator');
    const last = evals[evals.length - 1]?.text;
    if (last && last !== spokenRef.current) {
      spokenRef.current = last;
      speak(last);
    }
  }, [cur, phase, speak]);

  const attemptedQuestions = qData.filter(q => q.attempted);
  const edsScore = attemptedQuestions.length > 0
    ? Math.round((attemptedQuestions.reduce((s, q) => s + q.score, 0) / attemptedQuestions.length) * 100)
    : 0;
  const attemptedCount = qData.filter((q) => q.attempted).length;

  // ── Initialize exam session (with persistence restore) ─────────────────────

  useEffect(() => {
    if (!assignmentId) return;

    let cancelled = false;
    (async () => {
      try {
        // Check for saved state first
        const saved = loadExamState(assignmentId);
        if (saved) {
          // Verify session is still valid server-side
          try {
            const status = await getSessionStatus(saved.sessionId);
            if (status.status === 'completed') {
              clearExamState(assignmentId);
              // Fall through to start fresh
            } else {
              if (cancelled) return;
              setSessionId(saved.sessionId);
              setQuestions(saved.questions);
              setQData(saved.qData);
              setCurrent(saved.current);
              setStartTime(saved.startTime);
              setDurationMinutes(saved.durationMinutes);
              setPhase('taking');
              return;
            }
          } catch {
            // Session check failed (network error, 404, etc) - discard saved state
            clearExamState(assignmentId);
          }
        }

        // Fresh start: fetch assignment metadata and start session in parallel
        const [res, meta] = await Promise.all([
          startExamSession(assignmentId),
          fetchAssignmentMeta(assignmentId),
        ]);
        if (cancelled) return;

        const now = Date.now();
        setSessionId(res.session_id);
        setQuestions(res.questions);
        const initialQData = res.questions.map((q, i) => ({
          turns: [{ role: 'evaluator' as const, text: `Question ${i + 1}. ${q.text}` }],
          attempts: 0,
          attempted: false,
          done: false,
          score: 0,
          edsComponents: null,
        }));
        setQData(initialQData);
        setStartTime(now);
        setDurationMinutes(meta.duration_minutes);
        setPhase('taking');

        saveExamState(assignmentId, {
          version: EXAM_STATE_VERSION,
          sessionId: res.session_id,
          questions: res.questions,
          qData: initialQData,
          current: 0,
          startTime: now,
          durationMinutes: meta.duration_minutes,
        });
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to start exam');
      }
    })();

    return () => { cancelled = true; };
  }, [assignmentId]);

  // ── Persist state on meaningful changes ─────────────────────────────────────

  const [persistWarning, setPersistWarning] = useState(false);

  useEffect(() => {
    if (!assignmentId || !sessionId || phase !== 'taking') return;
    const ok = saveExamState(assignmentId, {
      version: EXAM_STATE_VERSION,
      sessionId,
      questions,
      qData,
      current,
      startTime: startTime ?? Date.now(),
      durationMinutes,
    });
    if (!ok) setPersistWarning(true);
  }, [assignmentId, sessionId, questions, qData, current, phase, startTime, durationMinutes]);

  // ── Countdown timer ────────────────────────────────────────────────────────

  useEffect(() => {
    if (startTime === null || durationMinutes === null || phase !== 'taking') {
      setSecondsRemaining(null);
      return;
    }

    const totalMs = durationMinutes * 60 * 1000;

    function tick() {
      const elapsed = Date.now() - startTime!;
      const remaining = Math.max(0, Math.ceil((totalMs - elapsed) / 1000));
      setSecondsRemaining(remaining);
      return remaining;
    }

    // Initial calculation
    if (tick() === 0) return;

    timerRef.current = setInterval(() => {
      const remaining = tick();
      if (remaining <= 0) {
        if (timerRef.current) clearInterval(timerRef.current);
      }
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [startTime, durationMinutes, phase]);

  // ── Auto-submit when timer expires ─────────────────────────────────────────

  const autoSubmitTriggeredRef = useRef(false);
  useEffect(() => {
    if (secondsRemaining === 0 && phase === 'taking' && !autoSubmitTriggeredRef.current) {
      autoSubmitTriggeredRef.current = true;
      setPhase('review');
    }
  }, [secondsRemaining, phase]);

  const timerExpired = durationMinutes !== null && secondsRemaining === 0;

  // ── Scroll to bottom of chat ───────────────────────────────────────────────

  const scrollBottom = useCallback(() => {
    setTimeout(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    }, 60);
  }, []);

  // ── Focus textarea on question change ──────────────────────────────────────

  useEffect(() => {
    if (phase === 'taking' && textareaRef.current && cur && !cur.done) {
      textareaRef.current.focus();
    }
  }, [current, phase, cur?.done]);

  // ── Submit answer mutation ─────────────────────────────────────────────────

  const answerMutation = useMutation({
    mutationFn: async ({ questionIndex, text }: { questionIndex: number; text: string }) => {
      if (!sessionId) throw new Error('No session');
      return submitAnswer(sessionId, questionIndex, text);
    },
    onSuccess: (data: AnswerResponse, variables) => {
      const { questionIndex } = variables;
      const attempt = (qData[questionIndex]?.attempts || 0) + 1;
      const maxed = attempt >= MAX_TURNS;
      const advance = maxed;

      let bubble: string;
      if (advance) {
        bubble = data.feedback || 'Answer recorded.';
      } else {
        bubble = data.feedback && data.probe
          ? `${data.feedback}\n\n${data.probe}`
          : data.probe || data.feedback || 'Tell me more about your reasoning.';
      }

      setQData((prev) =>
        prev.map((q, idx) => {
          if (idx !== questionIndex) return q;
          return {
            ...q,
            turns: [...q.turns, { role: 'evaluator', text: bubble }],
            attempts: attempt,
            attempted: true,
            done: advance,
            score: data.eds_question ?? Math.min(1, q.score + data.eds_delta / 10),
            edsComponents: data.eds_components ?? null,
          };
        }),
      );
      scrollBottom();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // ── Handle answer submit ───────────────────────────────────────────────────

  const handleAnswer = useCallback(() => {
    if (!draft.trim() || answerMutation.isPending || timerExpired) return;
    const studentText = draft.trim();
    stopMic();          // end dictation so it can't spill into the next turn
    setDraft('');

    // Append student message immediately
    setQData((prev) =>
      prev.map((q, idx) =>
        idx === current
          ? { ...q, turns: [...q.turns, { role: 'student', text: studentText }] }
          : q,
      ),
    );
    scrollBottom();

    answerMutation.mutate({ questionIndex: current, text: studentText });
  }, [draft, current, answerMutation, scrollBottom, timerExpired, stopMic]);

  // ── Keyboard shortcut ──────────────────────────────────────────────────────

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleAnswer();
    }
  };

  // ── Navigation ─────────────────────────────────────────────────────────────

  const goTo = (i: number) => {
    if (i < 0 || i >= N || i === current) return;
    stopMic();          // discard any in-flight dictation before switching questions
    setDraft('');
    setCurrent(i);
  };

  // ── Submit exam ────────────────────────────────────────────────────────────

  const submitExam = () => setPhase('review');
  const confirmSubmit = async () => {
    if (sessionId) {
      try { await completeSession(sessionId); } catch { /* best-effort */ }
    }
    if (assignmentId) clearExamState(assignmentId);
    if (timerRef.current) clearInterval(timerRef.current);
    setPhase('done');
  };
  const backToExam = () => {
    if (timerExpired) return;
    setPhase('taking');
  };

  // ── Error state ────────────────────────────────────────────────────────────

  if (error) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="text-center max-w-md">
          <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <span className="text-red-600 text-xl font-bold">!</span>
          </div>
          <h2 className="text-lg font-semibold text-gray-900 mb-2">Unable to Start Exam</h2>
          <p className="text-sm text-gray-500 mb-4">{error}</p>
          <button
            onClick={() => navigate('/student/dashboard')}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            Return to Dashboard
          </button>
        </div>
      </div>
    );
  }

  // ── Loading state ──────────────────────────────────────────────────────────

  if (phase === 'loading') {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-blue-600 animate-spin mx-auto mb-4" />
          <h2 className="text-lg font-semibold text-gray-900 mb-1">Preparing your exam...</h2>
          <p className="text-sm text-gray-500">Your questions will appear shortly</p>
        </div>
      </div>
    );
  }

  // ── Status list (shared between review and done) ───────────────────────────

  const statusList = (
    <div className="text-left max-w-lg mx-auto mb-5">
      {qData.map((q, i) => (
        <div
          key={i}
          className="flex justify-between items-center gap-4 py-2 border-b border-gray-100 text-sm"
        >
          <span className="text-gray-700 truncate">
            Q{i + 1}. {questions[i]?.topic || questions[i]?.text}
          </span>
          <span
            className={`font-bold flex-shrink-0 ${
              q.attempted ? 'text-green-600' : 'text-gray-400'
            }`}
          >
            {q.attempted ? 'Answered' : 'Skipped'}
          </span>
        </div>
      ))}
    </div>
  );

  // ── Review phase ───────────────────────────────────────────────────────────

  if (phase === 'review') {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="bg-white border border-gray-200 rounded-2xl p-10 text-center">
          <div className="text-4xl mb-2">&#128221;</div>
          <h1 className="text-2xl font-bold text-gray-900 mb-1">Exam Ready to Submit</h1>
          <p className="text-gray-500 text-sm mb-5">
            {attemptedCount} of {N} questions answered &middot; {N - attemptedCount} skipped.
          </p>
          <div className="flex justify-center mb-6">
            <EDSGauge score={edsScore} />
          </div>
          {statusList}

          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 max-w-lg mx-auto mb-6 text-sm text-gray-700">
            Once you submit, your responses cannot be changed.
          </div>

          <div className="flex gap-3 justify-center flex-wrap">
            {!timerExpired && (
              <button
                onClick={backToExam}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                <ChevronLeft className="w-4 h-4 inline -mt-0.5 mr-1" />
                Back to exam
              </button>
            )}
            <button
              onClick={confirmSubmit}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
            >
              Submit Exam
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Transcript helpers (for done phase) ────────────────────────────────────

  const transcript = qData.map((q, i) => ({
    n: i + 1,
    topic: questions[i]?.topic || '',
    question: questions[i]?.text || '',
    attempted: q.attempted,
    score: Math.round(q.score),
    exchange: q.turns.slice(1).map((t) => ({
      who: t.role === 'student' ? 'Student' : 'Evaluator',
      text: t.text,
    })),
  }));

  function downloadTranscript() {
    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const margin = 20;
    const contentWidth = pageWidth - margin * 2;
    let y = margin;

    const NAVY = '#1B2A4A';
    const GOLD = '#9B7530';
    const GRAY = '#6B6355';
    const BLACK = '#333333';

    function addFooters() {
      const totalPages = doc.getNumberOfPages();
      for (let i = 1; i <= totalPages; i++) {
        doc.setPage(i);
        doc.setFontSize(8);
        doc.setFont('helvetica', 'normal');
        doc.setTextColor(GRAY);
        doc.text(`Page ${i} of ${totalPages}`, margin, pageHeight - 10);
        doc.text('Generated by Epistemy', pageWidth - margin, pageHeight - 10, { align: 'right' });
      }
    }

    function checkPageBreak(needed: number) {
      if (y + needed > pageHeight - 20) {
        doc.addPage();
        y = margin;
      }
    }

    // Header
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(22);
    doc.setTextColor(NAVY);
    doc.text('Epistemy — Exam Transcript', margin, y);
    y += 10;

    // Gold accent line
    doc.setDrawColor(GOLD);
    doc.setLineWidth(0.8);
    doc.line(margin, y, pageWidth - margin, y);
    y += 12;

    // Metadata
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.setTextColor(GRAY);
    const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    doc.text(`Date: ${dateStr}`, margin, y);
    y += 5;
    doc.text(`Total Questions: ${N}`, margin, y);
    y += 5;
    doc.text(`Questions Answered: ${attemptedCount} of ${N}`, margin, y);
    y += 5;
    doc.text(`Overall EDS Score: ${Math.round(edsScore)}`, margin, y);
    y += 12;

    // Per-question sections
    transcript.forEach((t) => {
      checkPageBreak(40);

      // Question header
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(12);
      doc.setTextColor(NAVY);
      doc.text(`Question ${t.n} — ${t.topic}`, margin, y);
      y += 6;

      // Question text
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      doc.setTextColor(BLACK);
      const questionLines = doc.splitTextToSize(t.question, contentWidth);
      questionLines.forEach((line: string) => {
        checkPageBreak(6);
        doc.text(line, margin, y);
        y += 5;
      });
      y += 3;

      // Exchange
      if (t.exchange.length === 0) {
        checkPageBreak(6);
        doc.setFont('helvetica', 'italic');
        doc.setTextColor(GRAY);
        doc.text('(Skipped, no response)', margin + 5, y);
        y += 6;
      } else {
        t.exchange.forEach((e) => {
          checkPageBreak(10);
          // Speaker label
          doc.setFont('helvetica', 'bold');
          doc.setFontSize(9);
          doc.setTextColor(e.who === 'Student' ? '#2563eb' : NAVY);
          doc.text(`${e.who}:`, margin + 5, y);
          y += 4;

          // Speaker text
          doc.setFont('helvetica', 'normal');
          doc.setFontSize(9);
          doc.setTextColor(BLACK);
          const lines = doc.splitTextToSize(e.text, contentWidth - 10);
          lines.forEach((line: string) => {
            checkPageBreak(5);
            doc.text(line, margin + 10, y);
            y += 4;
          });
          y += 3;
        });
      }

      // Score for this question
      checkPageBreak(8);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(GRAY);
      doc.text(`Score: +${t.score} EDS  •  ${t.attempted ? 'Answered' : 'Skipped'}`, margin, y);
      y += 6;

      // Separator line
      checkPageBreak(6);
      doc.setDrawColor('#E5E5E5');
      doc.setLineWidth(0.3);
      doc.line(margin, y, pageWidth - margin, y);
      y += 8;
    });

    addFooters();
    doc.save(`Epistemy_Transcript_${new Date().toISOString().slice(0, 10)}.pdf`);
  }

  // ── Done phase ─────────────────────────────────────────────────────────────

  if (phase === 'done') {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="bg-white border border-gray-200 rounded-2xl p-10 text-center">
          <div className="w-14 h-14 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <CheckCircle className="w-7 h-7 text-green-600" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900 mb-1">Exam Submitted</h1>
          <p className="text-gray-500 text-sm mb-5">
            Your responses have been recorded. {attemptedCount} of {N} questions answered.
          </p>
          <div className="flex justify-center mb-6">
            <EDSGauge score={edsScore} />
          </div>
          {statusList}

          <div className="flex gap-3 justify-center flex-wrap mb-5">
            <button
              onClick={() => setShowTranscript((s) => !s)}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              {showTranscript ? 'Hide Transcript' : 'View Transcript'}
            </button>
            <button
              onClick={downloadTranscript}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Download Transcript
            </button>
          </div>

          {showTranscript && (
            <div className="text-left max-w-xl mx-auto mb-6 max-h-[420px] overflow-y-auto border border-gray-200 rounded-xl p-4 bg-gray-50">
              {transcript.map((t) => (
                <div key={t.n} className="mb-4 pb-4 border-b border-gray-200 last:border-b-0 last:mb-0 last:pb-0">
                  <div className="text-[10px] font-bold uppercase tracking-wide text-blue-600 mb-1">
                    Question {t.n} · {t.topic}
                  </div>
                  <div className="text-sm font-semibold text-gray-900 mb-2 leading-relaxed">
                    {t.question}
                  </div>
                  {t.exchange.length === 0 ? (
                    <div className="text-sm text-gray-400 italic">Skipped, no response.</div>
                  ) : (
                    t.exchange.map((e, ei) => (
                      <div key={ei} className="mb-1.5 text-sm leading-relaxed">
                        <span className={`font-bold ${e.who === 'Student' ? 'text-blue-600' : 'text-gray-700'}`}>
                          {e.who}:
                        </span>{' '}
                        <span className="text-gray-800 whitespace-pre-wrap">{e.text}</span>
                      </div>
                    ))
                  )}
                  <div className="text-xs text-gray-400 mt-2">
                    {t.attempted ? 'Answered' : 'Skipped'} · EDS +{t.score}
                  </div>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={() => navigate('/student/dashboard')}
            className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <ChevronLeft className="w-4 h-4 inline -mt-0.5 mr-1" />
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  // ── Taking phase (core exam interface) ─────────────────────────────────────

  return (
    <div className="max-w-5xl mx-auto flex flex-col min-h-[80vh]">
      {/* Header bar */}
      <div className="bg-blue-600 rounded-t-xl px-5 py-3 flex items-center justify-between">
        <div>
          <p className="text-blue-200 text-[11px] font-semibold uppercase tracking-wide">
            Oral Exam
          </p>
          <p className="text-white font-semibold text-lg">
            {questions[current]?.topic || 'Assessment'}
          </p>
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
          {secondsRemaining !== null && (
            <div
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-mono font-bold ${
                secondsRemaining <= TIMER_CRITICAL_SECONDS
                  ? 'bg-red-500/20 text-red-100 animate-pulse'
                  : secondsRemaining <= TIMER_WARNING_SECONDS
                  ? 'bg-amber-500/20 text-amber-100'
                  : 'bg-white/10 text-white'
              }`}
            >
              <Clock className="w-4 h-4" />
              {String(Math.floor(secondsRemaining / 60)).padStart(2, '0')}:
              {String(secondsRemaining % 60).padStart(2, '0')}
            </div>
          )}
          <button
            onClick={() => navigate('/student/dashboard')}
            className="px-3 py-1.5 bg-white/10 text-white rounded-lg text-sm hover:bg-white/20"
          >
            <ChevronLeft className="w-4 h-4 inline -mt-0.5" /> Back
          </button>
          <button
            onClick={submitExam}
            className="px-4 py-1.5 bg-white text-blue-700 rounded-lg text-sm font-semibold hover:bg-blue-50"
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

      {persistWarning && (
        <div className="bg-amber-50 border-x border-amber-200 px-5 py-2 text-xs text-amber-700">
          Unable to save progress locally. If you refresh, some answers may be lost.
        </div>
      )}

      {/* Question navigation grid */}
      <div className="bg-white border-x border-gray-200 px-5 py-3">
        <div className="flex justify-between text-xs text-gray-400 mb-2">
          <span>Question {current + 1} of {N}</span>
          <span>{attemptedCount} answered &middot; {N - attemptedCount} remaining</span>
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {qData.map((q, i) => {
            const isCur = i === current;
            let bg = 'bg-gray-100 text-gray-600 border-gray-200';
            if (q.attempted) bg = 'bg-green-500 text-white border-green-500';
            else if (isCur) bg = 'bg-amber-400 text-white border-amber-400';

            return (
              <button
                key={i}
                onClick={() => goTo(i)}
                title={`Question ${i + 1}${q.attempted ? ' (answered)' : ''}`}
                className={`w-8 h-8 rounded-lg border text-xs font-bold cursor-pointer transition-colors ${bg} ${
                  isCur ? 'ring-2 ring-blue-600 ring-offset-1' : ''
                }`}
              >
                {i + 1}
              </button>
            );
          })}
        </div>
      </div>

      {/* Two-column layout */}
      <div className="flex-1 flex gap-5 bg-white border border-gray-200 border-t-0 rounded-b-xl p-5">
        {/* Left: Chat thread */}
        <div className="flex-1 min-w-0 flex flex-col">
          {/* Scrollable conversation */}
          <div
            ref={scrollRef}
            className="flex-1 max-h-[400px] overflow-y-auto pr-2 mb-4 space-y-4"
          >
            {cur?.turns.map((t, i) => (
              <div
                key={i}
                className={`flex flex-col ${t.role === 'student' ? 'items-end' : 'items-start'}`}
              >
                <span
                  className={`text-[10px] font-bold uppercase tracking-wide mb-1 ${
                    t.role === 'student' ? 'text-blue-500' : 'text-blue-700'
                  }`}
                >
                  {t.role === 'student' ? 'You' : 'Evaluator'}
                </span>
                <div
                  className={`max-w-[85%] px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap rounded-xl ${
                    t.role === 'student'
                      ? 'bg-blue-600 text-white rounded-br-sm'
                      : 'bg-gray-50 border border-gray-200 text-gray-800 rounded-bl-sm'
                  }`}
                >
                  {t.text}
                </div>
              </div>
            ))}
            {answerMutation.isPending && (
              <div className="flex items-center gap-2 py-2">
                <div className="flex gap-1">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="w-2 h-2 rounded-full bg-blue-400 animate-bounce"
                      style={{ animationDelay: `${i * 0.15}s` }}
                    />
                  ))}
                </div>
                <span className="text-xs text-gray-400">Evaluating...</span>
              </div>
            )}
          </div>

          {/* Input area or done indicator */}
          {cur?.done ? (
            <div className="bg-green-50 border border-green-200 rounded-xl px-4 py-3 text-sm text-green-700 font-medium mb-3">
              <CheckCircle className="w-4 h-4 inline -mt-0.5 mr-2" />
              Answer recorded. Use the navigation to continue.
            </div>
          ) : (
            <div className="mb-3">
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Answer the question above... (Cmd+Enter to submit)"
                disabled={answerMutation.isPending}
                rows={3}
                className="w-full px-4 py-3 border border-gray-300 rounded-xl text-sm leading-relaxed resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:bg-gray-50"
              />
              <div className="flex gap-2 mt-2">
                <button
                  onClick={toggleMic}
                  title={listening ? 'Stop dictation' : 'Answer by voice'}
                  className={`relative flex items-center justify-center px-3 py-2.5 rounded-lg border text-sm font-medium transition-colors ${
                    listening
                      ? `bg-green-50 border-green-400 text-green-600 ${speaking ? 'ring-2 ring-green-300 animate-pulse' : ''}`
                      : 'border-gray-300 text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <Mic className="w-4 h-4" />
                  {listening && (
                    <span className={`ml-1.5 inline-block w-2 h-2 rounded-full bg-green-500 ${speaking ? 'animate-ping' : 'opacity-60'}`} />
                  )}
                </button>
                <button
                  onClick={() => { setTtsOn((v) => !v); if (ttsOn) audioRef.current?.pause(); }}
                  title={ttsOn ? 'Mute question audio' : 'Unmute question audio'}
                  className="flex items-center justify-center px-3 py-2.5 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 text-sm font-medium transition-colors"
                >
                  {ttsOn ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
                </button>
                <button
                  onClick={handleAnswer}
                  disabled={!draft.trim() || answerMutation.isPending}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {answerMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Evaluating...
                    </>
                  ) : (
                    <>
                      <Send className="w-4 h-4" />
                      Submit Answer
                    </>
                  )}
                </button>
              </div>
            </div>
          )}

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
            {current < N - 1 ? (
              <button
                onClick={() => goTo(current + 1)}
                className="flex items-center gap-1 px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                {cur?.attempted ? 'Next' : 'Skip'} <ChevronRight className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={submitExam}
                className="flex items-center gap-1 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              >
                Submit Exam <ChevronRight className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        {/* Right sidebar: EDS score */}
        <div className="w-64 flex-shrink-0 hidden lg:block">
          <div className="border border-gray-200 rounded-xl overflow-hidden">
            {/* Score display */}
            <div className="p-5 text-center">
              <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400 mb-3">
                Epistemic Depth Score
              </p>
              <EDSGauge score={edsScore} />
              <EDSBreakdown components={cur?.edsComponents ?? null} />
              <p className="text-xs text-gray-400 mt-3 leading-relaxed">
                Accumulates as responses demonstrate causal understanding.
              </p>
            </div>

            {/* Progress summary */}
            <div className="border-t border-gray-100 px-4 py-3">
              <div className="flex justify-between text-xs mb-1.5">
                <span className="text-gray-500 font-semibold uppercase tracking-wide">
                  Progress
                </span>
                <span className="text-gray-900 font-bold">
                  {attemptedCount}/{N}
                </span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-blue-500 to-blue-600 rounded-full transition-all duration-500"
                  style={{ width: `${(attemptedCount / Math.max(N, 1)) * 100}%` }}
                />
              </div>
            </div>

            {/* Current question info */}
            <div className="border-t border-gray-100 px-4 py-3">
              <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400 mb-1">
                Current Question
              </p>
              <p className="text-sm text-gray-700 font-medium">
                {questions[current]?.topic || `Question ${current + 1}`}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {cur?.attempts || 0} / {MAX_TURNS} turns used
              </p>
            </div>
          </div>

          {/* Concept Graph */}
          <div className="border border-gray-200 rounded-xl mt-3 p-3">
            <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400 mb-2 text-center">
              Concept Map
            </p>
            <ConceptGraphSVG
              questions={questions}
              qData={qData}
              currentIndex={current}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
