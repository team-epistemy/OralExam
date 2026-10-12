import { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Users, BarChart3, CheckCircle2, ChevronDown, ChevronUp, X, Save, Loader2 } from 'lucide-react';
import { get, post } from '../../api/client';
import { getCefrCalibration, saveCefrCalibration } from '../../api/exam';

interface SessionSummary {
  session_id: string;
  student_id: string;
  student_email: string;
  status: string;
  overall_eds: number | null;
  completed_at: string | null;
  anonymized?: boolean;
}

interface AssignmentDetail {
  assignment_id: string;
  course_id: string;
  title: string;
  question_ids: string[];
  config: Record<string, unknown>;
  status: string;
  created_at: string | null;
}

interface SessionGrade {
  session_id: string;
  status: string;
  total_eds: number;
  turns_evaluated: number;
  evaluations: TurnEvaluation[];
  grade_id?: string | null;
  final_score?: number;
  overall_comment?: string;
  component_scores?: Record<string, unknown>;
  released_at?: string | null;
  anonymized?: boolean;
}

// Written only by the communication (CEFR speaking) exam type; null on a
// Socratic turn, which scores on the EDS components instead.
interface Cefr {
  turn_number: number;
  part: 'part2' | 'part3';
  scores: Record<string, number | null>;
  turn_band: number;
  turn_band_name: string;
  target_band?: number | null;
  target_band_name?: string | null;
  next_target_band_name?: string | null;
  // The PISA question type this turn answered, and the one the next question uses.
  asked_type_label?: string | null;
  next_type_label?: string | null;
  direction?: 'up' | 'same' | 'down';
  // Evidence the turn was graded with. speech is null when the student answered
  // in text mode — worth showing, because it means Fluency was graded blind.
  speech?: Record<string, number> | null;
  words?: Array<{ w: string; g: number }> | null;
  proven_band?: number | null;
  repeat_request?: boolean;
  degraded?: boolean;
  criterion_averages?: Record<string, number>;
  overall_score?: number;
  overall_band?: string;
  final?: boolean;
  follow_up?: string; // the next question this turn generated (= next turn's prompt)
  // Deterministic acoustic Fluency band (hybrid A/B) computed alongside the LLM
  // grade — evidence only, never fed into the grade or ladder.
  acoustic_fluency?: { band: number; band_name: string; score: number } | null;
  acoustic_fluency_delta?: number | null;
}

interface TurnEvaluation {
  turn_index: number;
  sub_turn_index?: number;
  question_text?: string;
  student_answer?: string;
  eds_score: number;
  eds_bucket: string;
  answered: boolean;
  adequate: boolean;
  eds_delta: number;
  feedback?: string;
  rationale?: string;
  cefr?: Cefr | null;
  components?: {
    node_coverage?: number | null;
    edge_coverage?: number | null;
    recitation_gate?: number | null;
    nodes_detected?: string[];
    edges_demonstrated?: number[];
  };
}

interface GradeReleaseResponse {
  assignment_id: string;
  grades_released: number;
  grades: Array<{ session_id: string; student_id: string; grade_id: string; final_score?: number; status: string }>;
}

function edsColor(score: number | null): string {
  if (score === null) return 'text-gray-400';
  if (score >= 85) return 'text-green-600';
  if (score >= 70) return 'text-blue-600';
  if (score >= 50) return 'text-yellow-600';
  return 'text-red-600';
}

function edsBgColor(score: number | null): string {
  if (score === null) return 'bg-gray-50';
  if (score >= 85) return 'bg-green-50';
  if (score >= 70) return 'bg-blue-50';
  if (score >= 50) return 'bg-yellow-50';
  return 'bg-red-50';
}

function edsBand(score: number | null): string {
  if (score === null) return 'N/A';
  if (score >= 85) return 'Distinction';
  if (score >= 70) return 'Proficient';
  if (score >= 50) return 'Developing';
  return 'Starting';
}

// ── CEFR speaking panel ──────────────────────────────────────────────────────
// The marker's view of a speaking exam: what each turn scored on the CEFR grid,
// where the examiner aimed the next question, and the final band. Rendered only
// for turns that carry a `cefr` block, so Socratic grading is untouched.

const CRITERIA: Array<[string, string]> = [
  ['range', 'Range'], ['accuracy', 'Accuracy'], ['fluency', 'Fluency'],
  ['coherence', 'Coherence'], ['interaction', 'Interaction'],
];

function bandStyle(band: number | null | undefined): string {
  if (band == null) return 'bg-gray-50 text-gray-400';
  if (band >= 5) return 'bg-green-50 text-green-700';   // C1-C2
  if (band >= 4) return 'bg-blue-50 text-blue-700';     // B2
  if (band >= 3) return 'bg-yellow-50 text-yellow-700'; // B1
  return 'bg-orange-50 text-orange-700';                // A1-A2
}

const CEFR_BANDS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
const cefrName = (n: number | null | undefined): string =>
  n == null ? '—' : (CEFR_BANDS[Math.min(5, Math.max(0, Math.round(n) - 1))] ?? '—');

function ScoreCell({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className={`flex-1 min-w-[72px] rounded px-2 py-1 text-center ${bandStyle(value == null ? null : Math.round(value))}`}>
      <p className="text-[10px] uppercase tracking-wide opacity-70">{label}</p>
      <p className="text-sm font-semibold">
        {cefrName(value)}
      </p>
    </div>
  );
}

// The acoustics the turn was graded with. The student sees these on their own
// result screen; without them here the professor is reading a band with the
// evidence behind it hidden.
function CefrAcoustics({ cefr }: { cefr: Cefr }) {
  const s = cefr.speech;
  if (!s) {
    return (
      <p className="mt-1 text-[11px] text-amber-700">
        No audio measured for this turn (text mode) — Fluency was graded from the
        transcript alone, which drops pauses and hesitations.
      </p>
    );
  }
  const n = (k: string, digits = 0) =>
    s[k] == null ? null : s[k].toFixed(digits);
  const chips: Array<[string, string | null]> = [
    ['pronunciation', n('pron_overall')],
    ['prosody', n('prosody')],
    ['Azure fluency', n('pron_fluency')],
    ['pauses', n('pause_count')],
    ['long pauses', n('long_pause_count')],
    ['mean pause', s.mean_pause_sec == null ? null : `${n('mean_pause_sec', 2)}s`],
    ['silence', s.pause_ratio == null ? null : `${(s.pause_ratio * 100).toFixed(0)}%`],
    ['rate', s.speech_rate_wpm == null ? null : `${n('speech_rate_wpm')} wpm`],
  ];
  return (
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
      {chips.filter(([, v]) => v != null).map(([label, v]) => (
        <span key={label}>{label} <span className="font-medium text-gray-700">{v}</span></span>
      ))}
    </div>
  );
}

// The answer with the measured pauses marked, as the student's own screen shows
// it. A summary count says a turn had eleven pauses; this says whether they fell
// at clause boundaries or mid-phrase, which is what Fluency actually turns on.
function AnswerWithPauses({ words }: { words: Array<{ w: string; g: number }> }) {
  return (
    <span>
      {words.map((tw, i) => (
        <span key={i}>
          {tw.g >= 0.3 && (
            <span className={`mx-0.5 font-medium tabular-nums ${tw.g >= 1 ? 'text-red-500' : 'text-amber-500'}`}
                  title={`${tw.g.toFixed(2)}s pause`}>
              ({tw.g.toFixed(1)}s)
            </span>
          )}
          {i > 0 && tw.g < 0.3 ? ' ' : ''}{tw.w}
        </span>
      ))}
    </span>
  );
}

function CefrTurnScores({ cefr }: { cefr: Cefr }) {
  const arrow = cefr.direction === 'up' ? '↑' : cefr.direction === 'down' ? '↓' : '→';
  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-medium text-gray-600">
          Turn {cefr.turn_number} · {cefr.part === 'part2' ? 'Part 2 long turn' : 'Part 3 follow-up'}
        </span>
        <span className={`px-1.5 py-0.5 rounded font-semibold ${bandStyle(cefr.turn_band)}`}>
          {cefr.turn_band_name}
        </span>
        {cefr.asked_type_label && (
          <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
            {cefr.asked_type_label}
          </span>
        )}
        {cefr.target_band_name && (
          <span className="text-gray-400">aimed at {cefr.target_band_name}</span>
        )}
        {!cefr.final && cefr.next_target_band_name && (
          <span className="text-gray-400">
            {arrow} next: {cefr.next_target_band_name}
            {cefr.next_type_label ? ` · ${cefr.next_type_label}` : ''}
          </span>
        )}
        {cefr.repeat_request && (
          <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
            asked for a repeat — ladder held
          </span>
        )}
        {cefr.degraded && (
          <span className="px-1.5 py-0.5 rounded bg-red-50 text-red-700 font-medium">
            grader unavailable — scored at target, not a real judgement
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {CRITERIA.map(([key, label]) => (
          <ScoreCell key={key} label={label} value={cefr.scores?.[key]} />
        ))}
      </div>
      {cefr.acoustic_fluency && (
        <p className="text-[11px] text-gray-500">
          Acoustic fluency (deterministic, A/B): <span className="font-semibold">{cefr.acoustic_fluency.band_name}</span> ({cefr.acoustic_fluency.score.toFixed(1)}/6)
          {cefr.scores?.fluency != null && <> · LLM fluency {cefr.scores.fluency.toFixed(1)}</>}
          {cefr.acoustic_fluency_delta != null && Math.abs(cefr.acoustic_fluency_delta) >= 1 && (
            <span className="text-amber-600"> · Δ {cefr.acoustic_fluency_delta > 0 ? '+' : ''}{cefr.acoustic_fluency_delta} — review</span>
          )}
          <span className="text-gray-400"> · evidence only, not in the grade</span>
        </p>
      )}
    </div>
  );
}

const CEFR_BAND_NAMES = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'] as const;

// CEFR bands are 1–6 for arithmetic (averaging criteria) and named for display.
const bandToNum = (b?: string | null): number | null => {
  if (!b) return null;
  const i = (CEFR_BAND_NAMES as readonly string[]).indexOf(b);
  return i < 0 ? null : i + 1;
};
const numToBand = (n: number | null | undefined): string | null =>
  n == null ? null : (CEFR_BAND_NAMES[Math.max(0, Math.min(5, Math.round(n) - 1))] ?? null);

// Apply a reviewer's per-dimension band overrides to one turn: an overridden
// criterion replaces the machine score, and the turn band is re-derived — from
// an explicit "overall" (Turn band) override if the reviewer set one, otherwise
// by re-averaging the effective criteria. Turns with no override are unchanged.
function effectiveTurn(cefr: Cefr, ov: Record<string, string>): Cefr {
  const t = cefr.turn_number;
  const scores: Record<string, number | null> = { ...(cefr.scores || {}) };
  for (const [key] of CRITERIA) {
    const o = ov[`${t}:${key}`];
    if (o) scores[key] = bandToNum(o);
  }
  const direct = ov[`${t}:overall`];
  let turnBandNum: number | null;
  if (direct) {
    turnBandNum = bandToNum(direct);
  } else {
    const vals = CRITERIA.map(([k]) => scores[k]).filter((v): v is number => v != null);
    turnBandNum = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : cefr.turn_band;
  }
  return {
    ...cefr, scores,
    turn_band: turnBandNum ?? cefr.turn_band,
    turn_band_name: numToBand(turnBandNum) ?? cefr.turn_band_name,
  };
}

// Re-derive the session result from every graded turn with overrides applied:
// per-criterion averages across turns, overall = mean of those averages. This is
// what makes a reviewer's rubric edit flow up into the overall band on Save.
function effectiveResult(evals: TurnEvaluation[], ov: Record<string, string>): Cefr | null {
  const base = finalCefr(evals);
  if (!base) return null;
  const turns = evals.filter((e) => e.cefr).map((e) => effectiveTurn(e.cefr!, ov));
  const averages: Record<string, number> = {};
  for (const [key] of CRITERIA) {
    const vals = turns.map((t) => t.scores?.[key]).filter((v): v is number => v != null);
    if (vals.length) averages[key] = vals.reduce((a, b) => a + b, 0) / vals.length;
  }
  const avgVals = Object.values(averages);
  const overallScore = avgVals.length
    ? avgVals.reduce((a, b) => a + b, 0) / avgVals.length
    : (base.overall_score ?? 0);
  return {
    ...base,
    criterion_averages: averages,
    overall_score: overallScore,
    overall_band: numToBand(overallScore) ?? base.overall_band,
  };
}

// Per-dimension band override. Saved against the professor as the rater, so two
// markers on the same session keep their own judgements and neither overwrites
// the machine band — the point is to compare them, not replace them. This does
// not move the released grade; that stays the overall override below.
// One rubric dimension's band: the model's band on the left, the reviewer's
// chosen band in the dropdown. Picking a band only STAGES it — the recompute and
// persistence happen when the reviewer presses Save below.
function CefrOverrideRow({
  label, machineBand, value, onChange,
}: {
  label: string; machineBand: string | null; value?: string;
  onChange: (band: string) => void;
}) {
  const disagrees = value && machineBand && value !== machineBand;
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-20 text-gray-500">{label}</span>
      <span className={`w-8 text-center rounded ${bandStyle(bandToNum(machineBand))}`}>
        {machineBand || '—'}
      </span>
      <select
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        className="border border-gray-300 rounded px-1 py-0.5 text-[11px]"
      >
        <option value="">your band…</option>
        {CEFR_BAND_NAMES.map((b) => <option key={b} value={b}>{b}</option>)}
      </select>
      {disagrees && <span className="text-amber-600">differs from the model</span>}
    </div>
  );
}

function CefrCalibrationPanel({
  cefr, draft, onChange,
}: {
  cefr: Cefr;
  draft: Record<string, string>;
  onChange: (turnNumber: number, dimension: string, band: string) => void;
}) {
  const dims: Array<[string, string, string | null]> = [
    ['overall', 'Turn band', cefr.turn_band_name || null],
    ...CRITERIA.map(([key, label]) => {
      const v = cefr.scores?.[key];
      return [key, label, numToBand(v)] as [string, string, string | null];
    }).filter(([, , band]) => band !== null),
  ];
  return (
    <div className="mt-2 rounded border border-gray-200 bg-gray-50 p-2 space-y-1">
      <p className="text-[10px] uppercase tracking-wide text-gray-400">
        Your band vs the model's — on Save this re-grades the turn and the overall band
      </p>
      {dims.map(([key, label, machine]) => (
        <CefrOverrideRow
          key={key} label={label} machineBand={machine}
          value={draft[`${cefr.turn_number}:${key}`]}
          onChange={(b) => onChange(cefr.turn_number, key, b)}
        />
      ))}
    </div>
  );
}

function CefrSummary({ cefr }: { cefr: Cefr }) {
  const averages = cefr.criterion_averages || {};
  return (
    <div className="rounded-lg bg-white border border-purple-200 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            CEFR speaking result
          </h4>
          <p className="text-[11px] text-gray-400">
            Each criterion averaged across its turns; overall is the mean of the five.
          </p>
        </div>
        <div className={`px-3 py-1.5 rounded-lg text-center ${bandStyle(cefr.turn_band && cefr.overall_band ? Math.round(cefr.overall_score ?? 0) : null)}`}>
          <p className="text-xl font-bold leading-none">{cefr.overall_band || '—'}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        {CRITERIA.map(([key, label]) => (
          <ScoreCell key={key} label={label} value={averages[key]} />
        ))}
      </div>
      <p className="text-[11px] text-gray-400">
        {/* Only true for a turn with no measured audio — with the acoustics the
            grader sees real pausing and tempo, so the old blanket caveat would
            now be misleading. */}
        {cefr.speech
          ? 'Fluency is graded from the measured audio — pausing, tempo and silence. Pronunciation is shown but not scored.'
          : 'No audio was measured, so Fluency came from the transcript alone, which drops pauses and hesitations and reads generous. Pronunciation is not scored.'}
      </p>
    </div>
  );
}

// The last turn carries the completed aggregate, so that row is the result.
function finalCefr(evals: TurnEvaluation[]): Cefr | null {
  const withCefr = evals.filter((e) => e.cefr);
  if (withCefr.length === 0) return null;
  return withCefr[withCefr.length - 1].cefr || null;
}

function groupByQuestion(evals: TurnEvaluation[]) {
  const map = new Map<number, TurnEvaluation[]>();
  for (const ev of evals) {
    const arr = map.get(ev.turn_index) || [];
    arr.push(ev);
    map.set(ev.turn_index, arr);
  }
  return Array.from(map.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([turn_index, attempts]) => {
      attempts.sort((a, b) => (a.sub_turn_index ?? 0) - (b.sub_turn_index ?? 0));
      const best = attempts.reduce((m, e) => (e.eds_score > m.eds_score ? e : m), attempts[0]);
      return {
        turn_index,
        question_text: attempts.find((a) => a.question_text)?.question_text || '',
        eds_score: best.eds_score,
        eds_bucket: best.eds_bucket,
        attempts,
      };
    });
}

function statusBadge(status: string) {
  if (status === 'completed') {
    return <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Completed</span>;
  }
  if (status === 'active') {
    return <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">In Progress</span>;
  }
  return <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 font-medium">{status}</span>;
}

export default function GradeView() {
  const { assignmentId } = useParams<{ assignmentId: string }>();
  const queryClient = useQueryClient();
  const [expandedSession, setExpandedSession] = useState<string | null>(null);
  const [showReleasePreview, setShowReleasePreview] = useState(false);

  const { data: assignment, isLoading: loadingAssignment } = useQuery({
    queryKey: ['assignment', assignmentId],
    queryFn: () => get<AssignmentDetail>(`/api/assignments/${assignmentId}`),
    enabled: !!assignmentId,
  });

  const { data: sessions = [], isLoading: loadingSessions } = useQuery({
    queryKey: ['assignment-sessions', assignmentId],
    queryFn: () => get<SessionSummary[]>(`/api/assignments/${assignmentId}/sessions`),
    enabled: !!assignmentId,
  });

  const releaseMutation = useMutation({
    mutationFn: () => post<GradeReleaseResponse>(`/api/assignments/${assignmentId}/grades/release`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assignment-sessions', assignmentId] });
      setShowReleasePreview(false);
    },
  });

  // Speaking (CEFR) exams are scored as bands, not EDS — so the EDS average,
  // score column and band column are hidden for them (they'd be meaningless).
  const isSpeaking = (assignment?.config as { exam_kind?: string } | undefined)?.exam_kind === 'communication';

  const completedSessions = sessions.filter((s) => s.status === 'completed');
  const totalStudents = sessions.length;
  const completionRate = totalStudents > 0 ? Math.round((completedSessions.length / totalStudents) * 100) : 0;
  const averageEds = completedSessions.length > 0
    ? Math.round(completedSessions.reduce((sum, s) => sum + (s.overall_eds || 0), 0) / completedSessions.length)
    : 0;

  if (loadingAssignment || loadingSessions) {
    return (
      <div className="flex items-center justify-center py-16">
        <p className="text-sm text-gray-500">Loading grade data...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <Link
            to={assignment ? `/professor/courses/${assignment.course_id}` : '/professor/dashboard'}
            className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-2 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Course
          </Link>
          <h1 className="text-xl font-bold text-gray-900">{assignment?.title || 'Assignment Grades'}</h1>
          <p className="text-sm text-gray-500 mt-1">Review responses, adjust grades &amp; comments, then release.</p>
        </div>
        <button
          onClick={() => setShowReleasePreview(true)}
          disabled={completedSessions.length === 0}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <CheckCircle2 className="w-4 h-4" />
          Release Grades
        </button>
      </div>

      {releaseMutation.isSuccess && (
        <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
          Grades released for {releaseMutation.data.grades_released} session(s).
        </div>
      )}

      {/* Summary Stats */}
      <div className={`grid grid-cols-1 gap-4 ${isSpeaking ? 'sm:grid-cols-2' : 'sm:grid-cols-3'}`}>
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center"><Users className="w-5 h-5 text-blue-600" /></div>
            <div><p className="text-2xl font-bold text-gray-900">{totalStudents}</p><p className="text-xs text-gray-500">Total Students</p></div>
          </div>
        </div>
        {!isSpeaking && (
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center"><BarChart3 className="w-5 h-5 text-green-600" /></div>
              <div><p className="text-2xl font-bold text-gray-900">{averageEds}</p><p className="text-xs text-gray-500">Average EDS</p></div>
            </div>
          </div>
        )}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-50 rounded-lg flex items-center justify-center"><CheckCircle2 className="w-5 h-5 text-purple-600" /></div>
            <div><p className="text-2xl font-bold text-gray-900">{completionRate}%</p><p className="text-xs text-gray-500">Completion Rate</p></div>
          </div>
        </div>
      </div>

      {/* Sessions Table */}
      {sessions.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <Users className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500">No students have started this assignment yet.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">Student</th>
                <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">Status</th>
                {!isSpeaking && <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">EDS Score</th>}
                {!isSpeaking && <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">Band</th>}
                <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">Completed</th>
                <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sessions.map((session) => (
                <SessionRow
                  key={session.session_id}
                  session={session}
                  assignmentId={assignmentId!}
                  isSpeaking={isSpeaking}
                  expanded={expandedSession === session.session_id}
                  onToggle={() => setExpandedSession(expandedSession === session.session_id ? null : session.session_id)}
                  queryClient={queryClient}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showReleasePreview && (
        <ReleasePreviewModal
          title={assignment?.title || 'Assignment'}
          sessions={completedSessions}
          onClose={() => setShowReleasePreview(false)}
          onConfirm={() => releaseMutation.mutate()}
          releasing={releaseMutation.isPending}
        />
      )}
    </div>
  );
}

function SessionRow({
  session,
  assignmentId,
  isSpeaking,
  expanded,
  onToggle,
  queryClient,
}: {
  session: SessionSummary;
  assignmentId: string;
  isSpeaking: boolean;
  expanded: boolean;
  onToggle: () => void;
  queryClient: ReturnType<typeof useQueryClient>;
}) {
  const { data: sessionGrade } = useQuery({
    queryKey: ['session-grade', session.session_id],
    queryFn: () => get<SessionGrade>(`/api/grades/${session.session_id}`),
    enabled: expanded,
  });

  return (
    <>
      <tr className="hover:bg-gray-50 cursor-pointer transition-colors" onClick={onToggle}>
        <td className="px-5 py-3"><p className="text-sm font-medium text-gray-900">{session.student_email}</p></td>
        <td className="px-5 py-3">{statusBadge(session.status)}</td>
        {!isSpeaking && (
          <td className="px-5 py-3">
            <span className={`text-sm font-bold ${edsColor(session.overall_eds)}`}>
              {session.overall_eds !== null ? `${session.overall_eds}` : '--'}
            </span>
          </td>
        )}
        {!isSpeaking && (
          <td className="px-5 py-3">
            <span className={`text-xs px-2 py-0.5 rounded ${edsBgColor(session.overall_eds)} ${edsColor(session.overall_eds)} font-medium`}>
              {edsBand(session.overall_eds)}
            </span>
          </td>
        )}
        <td className="px-5 py-3">
          <span className="text-xs text-gray-500">{session.completed_at ? new Date(session.completed_at).toLocaleDateString() : '--'}</span>
        </td>
        <td className="px-5 py-3">
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={isSpeaking ? 4 : 6} className="px-5 py-4 bg-gray-50 border-t border-gray-100">
            <SessionDetail sessionId={session.session_id} sessionGrade={sessionGrade} assignmentId={assignmentId} queryClient={queryClient} />
          </td>
        </tr>
      )}
    </>
  );
}

function SessionDetail({
  sessionId,
  sessionGrade,
  assignmentId,
  queryClient,
}: {
  sessionId: string;
  sessionGrade: SessionGrade | undefined;
  assignmentId: string;
  queryClient: ReturnType<typeof useQueryClient>;
}) {
  // This reviewer's own band overrides for the session, keyed "turn:dimension".
  // Scoped per rater server-side, so two markers never see each other's.
  //   draft   — what the dropdowns currently show (edited freely)
  //   applied — what the transcript is re-graded against (committed on Save)
  // Both start from what was previously saved, so an earlier calibration is
  // already reflected in the transcript on open.
  const { data: savedOverrides } = useQuery({
    queryKey: ['cefr-calibration', sessionId],
    queryFn: () => getCefrCalibration(sessionId),
    enabled: !!sessionId,
  });
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [applied, setApplied] = useState<Record<string, string>>({});
  const [savingCal, setSavingCal] = useState(false);
  useEffect(() => {
    if (savedOverrides) {
      const m = Object.fromEntries(
        savedOverrides.map((o) => [`${o.turn_number}:${o.dimension}`, o.human_band]));
      setDraft(m);
      setApplied(m);
    }
  }, [savedOverrides]);
  const noteDraft = (turnNumber: number, dimension: string, band: string) =>
    setDraft((d) => ({ ...d, [`${turnNumber}:${dimension}`]: band }));

  // The model's band for every dimension of every turn — needed when persisting
  // an override (we record what the reviewer disagreed with) and to recompute.
  const machineByKey = useMemo(() => {
    const m: Record<string, string | null> = {};
    for (const e of sessionGrade?.evaluations || []) {
      const c = e.cefr;
      if (!c) continue;
      m[`${c.turn_number}:overall`] = c.turn_band_name || null;
      for (const [key] of CRITERIA) m[`${c.turn_number}:${key}`] = numToBand(c.scores?.[key]);
    }
    return m;
  }, [sessionGrade]);

  const calDirty = useMemo(() => {
    const keys = new Set([...Object.keys(draft), ...Object.keys(applied)]);
    for (const k of keys) if ((draft[k] || '') !== (applied[k] || '')) return true;
    return false;
  }, [draft, applied]);

  // Persist every staged band that changed, then apply it so the transcript
  // (per-turn and overall bands) re-grades against the reviewer's calibration.
  const saveCalibration = async () => {
    setSavingCal(true);
    try {
      const changed = Object.entries(draft).filter(([k, v]) => v && v !== applied[k]);
      await Promise.all(changed.map(([k, band]) => {
        const sep = k.indexOf(':');
        const turnNumber = Number(k.slice(0, sep));
        const dimension = k.slice(sep + 1);
        return saveCefrCalibration({
          session_id: sessionId, turn_number: turnNumber, dimension,
          human_band: band, machine_band: machineByKey[k] ?? null,
        });
      }));
      setApplied(draft);
    } catch (err: any) {
      alert(err?.message || 'Could not save the calibration.');
      throw err;
    } finally {
      setSavingCal(false);
    }
  };

  if (!sessionGrade) {
    return <p className="text-sm text-gray-500">Loading session details...</p>;
  }

  // The result re-graded against the committed overrides — a reviewer's rubric
  // edit flows up into the overall band here once they Save.
  const cefrResult = effectiveResult(sessionGrade.evaluations || [], applied);

  return (
    <div className="space-y-4">
      {sessionGrade.anonymized && (
        <div className="rounded-lg bg-blue-50 border border-blue-100 px-3 py-2 text-xs text-blue-800">
          Practice test — anonymized. Student identity and verbatim answers are hidden; only scores and reasoning are shown.
        </div>
      )}
      {cefrResult && <CefrSummary cefr={cefrResult} />}
      {/* Per-question responses, scores, and reasoning */}
      {sessionGrade.evaluations && sessionGrade.evaluations.length > 0 ? (
        <div>
          <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Responses &amp; Scoring</h4>
          <div className="space-y-3">
            {groupByQuestion(sessionGrade.evaluations).map((group) => (
              <div key={group.turn_index} className="bg-white rounded-lg border border-gray-100 p-4">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <p className="text-sm font-medium text-gray-900">Q{group.turn_index + 1}. {group.question_text || 'Question'}</p>
                  {(() => {
                    // A speaking exam's score is a CEFR band, not an EDS bucket —
                    // show the band so the header matches the panel below it.
                    // Re-graded against the reviewer's committed overrides.
                    const gc = effectiveResult(group.attempts, applied);
                    if (gc) {
                      return (
                        <span className={`text-xs font-semibold whitespace-nowrap px-2 py-0.5 rounded ${bandStyle(Math.round(gc.overall_score ?? 0))}`}>
                          {gc.overall_band}
                        </span>
                      );
                    }
                    return (
                      <span className={`text-xs font-semibold whitespace-nowrap px-2 py-0.5 rounded ${edsBgColor(group.eds_score * 100)} ${edsColor(group.eds_score * 100)}`}>
                        {Math.round(group.eds_score * 100)} · {group.eds_bucket}
                      </span>
                    );
                  })()}
                </div>
                <div className="space-y-2">
                  {group.attempts.map((ev, i) => (
                    <div key={i} className="border-l-2 border-gray-100 pl-3">
                      {/* CefrTurnScores labels its own turn ("Turn 2 · Part 3
                          follow-up"), so the generic probe label would duplicate it. */}
                      {group.attempts.length > 1 && !ev.cefr && (
                        <p className="text-[11px] uppercase tracking-wide text-gray-400">{i === 0 ? 'Initial answer' : `Probe response ${i}`}</p>
                      )}
                      {/* Follow-up prompt the examiner asked for this turn (the
                          previous turn's generated follow_up). Turn 1's prompt is
                          the Part 2 card already shown in the group header. */}
                      {i > 0 && group.attempts[i - 1]?.cefr?.follow_up && (
                        <p className="text-sm text-blue-700"><span className="text-gray-400">Follow-up:</span>{' '}{group.attempts[i - 1].cefr!.follow_up}</p>
                      )}
                      <p className="text-sm text-gray-700">
                        <span className="text-gray-400">Answer:</span>{' '}
                        {/* With measured word timings, show the answer with its
                            pauses marked — the same view the student gets. */}
                        {ev.cefr?.words?.length && !sessionGrade.anonymized
                          ? <AnswerWithPauses words={ev.cefr.words} />
                          : (ev.student_answer || <em className="text-gray-400">{sessionGrade.anonymized ? 'hidden (anonymized)' : 'no answer'}</em>)}
                      </p>
                      {(ev.rationale || ev.feedback) && (
                        <p className="mt-1 text-xs text-gray-500"><span className="font-medium text-gray-600">Why this score:</span>{' '}{ev.rationale || ev.feedback}</p>
                      )}
                      {ev.cefr && (
                        <>
                          {/* Turn scores re-graded against committed overrides;
                              the calibration panel still shows the model's bands. */}
                          <CefrTurnScores cefr={effectiveTurn(ev.cefr, applied)} />
                          <CefrAcoustics cefr={ev.cefr} />
                          <CefrCalibrationPanel
                            cefr={ev.cefr} draft={draft} onChange={noteDraft} />
                        </>
                      )}
                      {ev.components && (
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-400">
                          {ev.components.recitation_gate != null && <span>authenticity R={ev.components.recitation_gate.toFixed(2)}</span>}
                          {ev.components.node_coverage != null && <span>concepts {Math.round(ev.components.node_coverage * 100)}%</span>}
                          {ev.components.edge_coverage != null && <span>causal links {Math.round(ev.components.edge_coverage * 100)}%</span>}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-sm text-gray-500">No evaluations recorded for this session.</p>
      )}

      <GradeEditor
        sessionId={sessionId} sessionGrade={sessionGrade} assignmentId={assignmentId}
        queryClient={queryClient}
        isSpeaking={!!cefrResult}
        overallBand={cefrResult?.overall_band ?? null}
        calDirty={calDirty}
        savingCal={savingCal}
        onSaveCalibration={calDirty ? saveCalibration : undefined}
      />
    </div>
  );
}

function GradeEditor({
  sessionId,
  sessionGrade,
  assignmentId,
  queryClient,
  isSpeaking = false,
  overallBand = null,
  calDirty = false,
  savingCal = false,
  onSaveCalibration,
}: {
  sessionId: string;
  sessionGrade: SessionGrade;
  assignmentId: string;
  queryClient: ReturnType<typeof useQueryClient>;
  // Speaking (CEFR) exams carry no numeric grade — the overall band stands in,
  // and Save also commits the reviewer's rubric calibration.
  isSpeaking?: boolean;
  overallBand?: string | null;
  calDirty?: boolean;
  savingCal?: boolean;
  onSaveCalibration?: () => Promise<void>;
}) {
  const [score, setScore] = useState('');
  const [comment, setComment] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setScore(sessionGrade.final_score != null ? String(Math.round(sessionGrade.final_score * 100)) : '');
    setComment(sessionGrade.overall_comment || '');
  }, [sessionGrade.final_score, sessionGrade.overall_comment]);

  const released = sessionGrade.status === 'released';

  const mutation = useMutation({
    mutationFn: () => post(`/api/grades/${sessionId}`, {
      // Speaking exams are band-only — never write a numeric score for them.
      score: isSpeaking ? null : (score === '' ? null : Number(score)),
      comment,
    }),
    onSuccess: () => {
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ['session-grade', sessionId] });
      queryClient.invalidateQueries({ queryKey: ['assignment-sessions', assignmentId] });
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const handleSave = async () => {
    if (!isSpeaking && score !== '') {
      const n = Number(score);
      if (Number.isNaN(n) || n < 0 || n > 100) {
        setError('Score must be between 0 and 100');
        return;
      }
    }
    setError('');
    // Commit any staged rubric calibration first, so the re-graded bands are
    // applied before (and regardless of) the comment save.
    if (onSaveCalibration) {
      try { await onSaveCalibration(); } catch { return; }
    }
    mutation.mutate();
  };

  const busy = mutation.isPending || savingCal;
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700">Grade &amp; comments</h4>
        {released && <span className="text-[11px] px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Released</span>}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-3">
        <div>
          {isSpeaking ? (
            <>
              <label className="block text-xs font-medium text-gray-500 mb-1">Overall CEFR band</label>
              <div className={`inline-flex items-center justify-center min-w-[3rem] px-3 py-1.5 rounded-lg text-sm font-bold ${bandStyle(bandToNum(overallBand))}`}>
                {overallBand || '—'}
              </div>
              {calDirty && <p className="mt-1 text-[11px] text-amber-600">Unsaved calibration — Save to apply.</p>}
            </>
          ) : (
            <>
              <label className="block text-xs font-medium text-gray-500 mb-1">Grade (0–100)</label>
              <input
                type="number" min={0} max={100} value={score}
                onChange={(e) => setScore(e.target.value)}
                className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="e.g. 75"
              />
            </>
          )}
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Overall comments (shown to the student)</label>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={3}
            className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
            placeholder="Summarize strengths, gaps, and next steps for this student…"
          />
        </div>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={busy}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          {busy ? 'Saving…' : 'Save'}
        </button>
        {saved && <span className="text-xs text-green-600">Saved</span>}
        {released && <span className="text-[11px] text-gray-400">Edits update what the student sees.</span>}
      </div>
    </div>
  );
}

function ReleasePreviewModal({
  title,
  sessions,
  onClose,
  onConfirm,
  releasing,
}: {
  title: string;
  sessions: SessionSummary[];
  onClose: () => void;
  onConfirm: () => void;
  releasing: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl h-[88vh] flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Release preview</h3>
            <p className="text-xs text-gray-500">Exactly what each student will see once released.</p>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-500 hover:bg-gray-100 rounded" title="Close"><X className="w-4 h-4" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4 bg-gray-50">
          {sessions.length === 0 ? (
            <p className="text-sm text-gray-500">No completed sessions to release.</p>
          ) : (
            sessions.map((s) => <ReleasePreviewCard key={s.session_id} title={title} session={s} />)
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-gray-200">
          <button onClick={onClose} className="text-sm px-4 py-2 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors">Cancel</button>
          <button
            onClick={onConfirm}
            disabled={releasing || sessions.length === 0}
            className="inline-flex items-center gap-2 text-sm px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {releasing ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            {releasing ? 'Releasing…' : `Confirm & Release (${sessions.length})`}
          </button>
        </div>
      </div>
    </div>
  );
}

function ReleasePreviewCard({ title, session }: { title: string; session: SessionSummary }) {
  const { data: grade, isLoading } = useQuery({
    queryKey: ['session-grade', session.session_id],
    queryFn: () => get<SessionGrade>(`/api/grades/${session.session_id}`),
  });

  const scorePct = grade?.final_score != null ? Math.round(grade.final_score * 100) : session.overall_eds;
  const groups = grade?.evaluations ? groupByQuestion(grade.evaluations) : [];
  // A speaking exam's grade is a CEFR band — shown band-only, no numeric score.
  const cefrResult = finalCefr(grade?.evaluations || []);
  const isSpeaking = !!cefrResult;

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
        <p className="text-xs text-gray-500">{session.student_email}</p>
      </div>
      <div className="p-4 space-y-3">
        {/* Subject / Test title */}
        <div>
          <p className="text-[11px] uppercase tracking-wide text-gray-400">Assessment</p>
          <p className="text-sm font-semibold text-gray-900">{title}</p>
        </div>
        {/* Grade */}
        <div>
          <p className="text-[11px] uppercase tracking-wide text-gray-400">Grade</p>
          {isSpeaking ? (
            <p className={`text-2xl font-bold ${bandStyle(Math.round(cefrResult!.overall_score ?? 0)).split(' ').find((c) => c.startsWith('text-')) || 'text-gray-700'}`}>
              CEFR {cefrResult!.overall_band || '—'}
            </p>
          ) : (
            <p className={`text-2xl font-bold ${edsColor(scorePct ?? null)}`}>
              {scorePct != null ? scorePct : '--'}<span className="text-sm text-gray-400">/100</span>
              <span className={`ml-2 text-xs px-2 py-0.5 rounded align-middle ${edsBgColor(scorePct ?? null)} ${edsColor(scorePct ?? null)}`}>{edsBand(scorePct ?? null)}</span>
            </p>
          )}
        </div>
        {/* Overall comments */}
        <div>
          <p className="text-[11px] uppercase tracking-wide text-gray-400">Overall comments</p>
          {isLoading ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : grade?.overall_comment ? (
            <p className="text-sm text-gray-700 whitespace-pre-wrap">{grade.overall_comment}</p>
          ) : (
            <p className="text-sm text-gray-400 italic">No overall comment — students will see the auto summary.</p>
          )}
        </div>
        {/* Detailed feedback */}
        <div>
          <p className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Detailed feedback</p>
          {groups.length === 0 ? (
            <p className="text-sm text-gray-400">No per-question feedback.</p>
          ) : (
            <ol className="space-y-2">
              {groups.map((g) => (
                <li key={g.turn_index} className="text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-gray-800 font-medium">Q{g.turn_index + 1}. {g.question_text || 'Question'}</p>
                    {(() => {
                      const gc = finalCefr(g.attempts);
                      return gc ? (
                        <span className={`text-xs font-semibold whitespace-nowrap px-2 py-0.5 rounded ${bandStyle(Math.round(gc.overall_score ?? 0))}`}>{gc.overall_band}</span>
                      ) : (
                        <span className={`text-xs font-semibold whitespace-nowrap ${edsColor(g.eds_score * 100)}`}>{Math.round(g.eds_score * 100)}/100</span>
                      );
                    })()}
                  </div>
                  {(() => {
                    const last = g.attempts[g.attempts.length - 1];
                    const why = last?.rationale || last?.feedback;
                    return why ? <p className="text-xs text-gray-500 mt-0.5">{why}</p> : null;
                  })()}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
