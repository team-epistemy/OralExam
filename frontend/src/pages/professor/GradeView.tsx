import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Users, BarChart3, CheckCircle2, ChevronDown, ChevronUp, X, Save, Loader2 } from 'lucide-react';
import { get, post } from '../../api/client';

interface SessionSummary {
  session_id: string;
  student_id: string;
  student_email: string;
  status: string;
  overall_eds: number | null;
  completed_at: string | null;
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
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center"><Users className="w-5 h-5 text-blue-600" /></div>
            <div><p className="text-2xl font-bold text-gray-900">{totalStudents}</p><p className="text-xs text-gray-500">Total Students</p></div>
          </div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center"><BarChart3 className="w-5 h-5 text-green-600" /></div>
            <div><p className="text-2xl font-bold text-gray-900">{averageEds}</p><p className="text-xs text-gray-500">Average EDS</p></div>
          </div>
        </div>
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
                <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">EDS Score</th>
                <th className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide px-5 py-3">Band</th>
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
  expanded,
  onToggle,
  queryClient,
}: {
  session: SessionSummary;
  assignmentId: string;
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
        <td className="px-5 py-3">
          <span className={`text-sm font-bold ${edsColor(session.overall_eds)}`}>
            {session.overall_eds !== null ? `${session.overall_eds}` : '--'}
          </span>
        </td>
        <td className="px-5 py-3">
          <span className={`text-xs px-2 py-0.5 rounded ${edsBgColor(session.overall_eds)} ${edsColor(session.overall_eds)} font-medium`}>
            {edsBand(session.overall_eds)}
          </span>
        </td>
        <td className="px-5 py-3">
          <span className="text-xs text-gray-500">{session.completed_at ? new Date(session.completed_at).toLocaleDateString() : '--'}</span>
        </td>
        <td className="px-5 py-3">
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="px-5 py-4 bg-gray-50 border-t border-gray-100">
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
  if (!sessionGrade) {
    return <p className="text-sm text-gray-500">Loading session details...</p>;
  }

  return (
    <div className="space-y-4">
      {/* Per-question responses, scores, and reasoning */}
      {sessionGrade.evaluations && sessionGrade.evaluations.length > 0 ? (
        <div>
          <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Responses &amp; Scoring</h4>
          <div className="space-y-3">
            {groupByQuestion(sessionGrade.evaluations).map((group) => (
              <div key={group.turn_index} className="bg-white rounded-lg border border-gray-100 p-4">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <p className="text-sm font-medium text-gray-900">Q{group.turn_index + 1}. {group.question_text || 'Question'}</p>
                  <span className={`text-xs font-semibold whitespace-nowrap px-2 py-0.5 rounded ${edsBgColor(group.eds_score * 100)} ${edsColor(group.eds_score * 100)}`}>
                    {Math.round(group.eds_score * 100)} · {group.eds_bucket}
                  </span>
                </div>
                <div className="space-y-2">
                  {group.attempts.map((ev, i) => (
                    <div key={i} className="border-l-2 border-gray-100 pl-3">
                      {group.attempts.length > 1 && (
                        <p className="text-[11px] uppercase tracking-wide text-gray-400">{i === 0 ? 'Initial answer' : `Probe response ${i}`}</p>
                      )}
                      <p className="text-sm text-gray-700"><span className="text-gray-400">Answer:</span>{' '}{ev.student_answer || <em className="text-gray-400">no answer</em>}</p>
                      {(ev.rationale || ev.feedback) && (
                        <p className="mt-1 text-xs text-gray-500"><span className="font-medium text-gray-600">Why this score:</span>{' '}{ev.rationale || ev.feedback}</p>
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

      <GradeEditor sessionId={sessionId} sessionGrade={sessionGrade} assignmentId={assignmentId} queryClient={queryClient} />
    </div>
  );
}

function GradeEditor({
  sessionId,
  sessionGrade,
  assignmentId,
  queryClient,
}: {
  sessionId: string;
  sessionGrade: SessionGrade;
  assignmentId: string;
  queryClient: ReturnType<typeof useQueryClient>;
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
      score: score === '' ? null : Number(score),
      comment,
    }),
    onSuccess: () => {
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ['session-grade', sessionId] });
      queryClient.invalidateQueries({ queryKey: ['assignment-sessions', assignmentId] });
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const handleSave = () => {
    if (score !== '') {
      const n = Number(score);
      if (Number.isNaN(n) || n < 0 || n > 100) {
        setError('Score must be between 0 and 100');
        return;
      }
    }
    setError('');
    mutation.mutate();
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700">Grade &amp; comments</h4>
        {released && <span className="text-[11px] px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Released</span>}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Grade (0–100)</label>
          <input
            type="number" min={0} max={100} value={score}
            onChange={(e) => setScore(e.target.value)}
            className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="e.g. 75"
          />
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
          disabled={mutation.isPending}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {mutation.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          {mutation.isPending ? 'Saving…' : 'Save'}
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
          <p className={`text-2xl font-bold ${edsColor(scorePct ?? null)}`}>
            {scorePct != null ? scorePct : '--'}<span className="text-sm text-gray-400">/100</span>
            <span className={`ml-2 text-xs px-2 py-0.5 rounded align-middle ${edsBgColor(scorePct ?? null)} ${edsColor(scorePct ?? null)}`}>{edsBand(scorePct ?? null)}</span>
          </p>
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
                    <span className={`text-xs font-semibold whitespace-nowrap ${edsColor(g.eds_score * 100)}`}>{Math.round(g.eds_score * 100)}/100</span>
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
