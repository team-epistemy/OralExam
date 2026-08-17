import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Users, BarChart3, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-react';
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
  grade_id?: string;
  final_score?: number;
  component_scores?: Record<string, unknown>;
  released_at?: string | null;
}

interface TurnEvaluation {
  turn_index: number;
  eds_score: number;
  eds_bucket: string;
  answered: boolean;
  adequate: boolean;
  eds_delta: number;
}

interface GradeReleaseResponse {
  assignment_id: string;
  grades_released: number;
  grades: Array<{
    session_id: string;
    student_id: string;
    grade_id: string;
    final_score?: number;
    status: string;
  }>;
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
  const [overrideSessionId, setOverrideSessionId] = useState<string | null>(null);
  const [overrideScore, setOverrideScore] = useState('');
  const [overrideReason, setOverrideReason] = useState('');
  const [overrideError, setOverrideError] = useState('');

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
    },
  });

  const overrideMutation = useMutation({
    mutationFn: ({ gradeId, newScore, reason }: { gradeId: string; newScore: number; reason: string }) =>
      post(`/api/grades/${gradeId}/override`, { new_score: newScore, reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assignment-sessions', assignmentId] });
      setOverrideSessionId(null);
      setOverrideScore('');
      setOverrideReason('');
    },
  });

  const completedSessions = sessions.filter(s => s.status === 'completed');
  const totalStudents = sessions.length;
  const completionRate = totalStudents > 0 ? Math.round((completedSessions.length / totalStudents) * 100) : 0;
  const averageEds = completedSessions.length > 0
    ? Math.round(completedSessions.reduce((sum, s) => sum + (s.overall_eds || 0), 0) / completedSessions.length)
    : 0;

  const handleReleaseGrades = () => {
    if (!confirm('Release grades for all completed sessions? Students will be able to see their scores.')) return;
    releaseMutation.mutate();
  };

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
          <p className="text-sm text-gray-500 mt-1">View student submissions and scores</p>
        </div>
        <button
          onClick={handleReleaseGrades}
          disabled={releaseMutation.isPending || completedSessions.length === 0}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <CheckCircle2 className="w-4 h-4" />
          {releaseMutation.isPending ? 'Releasing...' : 'Release Grades'}
        </button>
      </div>

      {releaseMutation.isSuccess && (
        <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
          Grades released for {releaseMutation.data.grades_released} session(s).
        </div>
      )}

      {releaseMutation.isError && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          Failed to release grades. Please try again.
        </div>
      )}

      {/* Summary Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
              <Users className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{totalStudents}</p>
              <p className="text-xs text-gray-500">Total Students</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
              <BarChart3 className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{averageEds}</p>
              <p className="text-xs text-gray-500">Average EDS</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-50 rounded-lg flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{completionRate}%</p>
              <p className="text-xs text-gray-500">Completion Rate</p>
            </div>
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
                  expanded={expandedSession === session.session_id}
                  onToggle={() => setExpandedSession(expandedSession === session.session_id ? null : session.session_id)}
                  onOverride={() => setOverrideSessionId(session.session_id)}
                  overrideActive={overrideSessionId === session.session_id}
                  overrideScore={overrideScore}
                  overrideReason={overrideReason}
                  onOverrideScoreChange={setOverrideScore}
                  onOverrideReasonChange={setOverrideReason}
                  onOverrideSubmit={(gradeId) => {
                    const score = parseFloat(overrideScore);
                    if (isNaN(score) || score < 0 || score > 100) {
                      setOverrideError('Score must be between 0 and 100');
                      return;
                    }
                    if (!overrideReason.trim()) {
                      setOverrideError('Reason is required');
                      return;
                    }
                    setOverrideError('');
                    overrideMutation.mutate({ gradeId, newScore: score / 100, reason: overrideReason });
                  }}
                  onOverrideCancel={() => {
                    setOverrideSessionId(null);
                    setOverrideScore('');
                    setOverrideReason('');
                    setOverrideError('');
                  }}
                  overrideError={overrideError}
                  overridePending={overrideMutation.isPending}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SessionRow({
  session,
  expanded,
  onToggle,
  onOverride,
  overrideActive,
  overrideScore,
  overrideReason,
  onOverrideScoreChange,
  onOverrideReasonChange,
  onOverrideSubmit,
  onOverrideCancel,
  overridePending,
  overrideError,
}: {
  session: SessionSummary;
  expanded: boolean;
  onToggle: () => void;
  onOverride: () => void;
  overrideActive: boolean;
  overrideScore: string;
  overrideReason: string;
  onOverrideScoreChange: (v: string) => void;
  onOverrideReasonChange: (v: string) => void;
  onOverrideSubmit: (gradeId: string) => void;
  onOverrideCancel: () => void;
  overridePending: boolean;
  overrideError: string;
}) {
  const { data: sessionGrade } = useQuery({
    queryKey: ['session-grade', session.session_id],
    queryFn: () => get<SessionGrade>(`/api/grades/${session.session_id}`),
    enabled: expanded,
  });

  return (
    <>
      <tr
        className="hover:bg-gray-50 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-5 py-3">
          <p className="text-sm font-medium text-gray-900">{session.student_email}</p>
        </td>
        <td className="px-5 py-3">
          {statusBadge(session.status)}
        </td>
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
          <span className="text-xs text-gray-500">
            {session.completed_at ? new Date(session.completed_at).toLocaleDateString() : '--'}
          </span>
        </td>
        <td className="px-5 py-3">
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="px-5 py-4 bg-gray-50 border-t border-gray-100">
            <SessionDetail
              sessionGrade={sessionGrade}
              onOverride={onOverride}
              overrideActive={overrideActive}
              overrideScore={overrideScore}
              overrideReason={overrideReason}
              onOverrideScoreChange={onOverrideScoreChange}
              onOverrideReasonChange={onOverrideReasonChange}
              onOverrideSubmit={onOverrideSubmit}
              onOverrideCancel={onOverrideCancel}
              overridePending={overridePending}
              overrideError={overrideError}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function SessionDetail({
  sessionGrade,
  onOverride,
  overrideActive,
  overrideScore,
  overrideReason,
  onOverrideScoreChange,
  onOverrideReasonChange,
  onOverrideSubmit,
  onOverrideCancel,
  overridePending,
  overrideError,
}: {
  sessionGrade: SessionGrade | undefined;
  onOverride: () => void;
  overrideActive: boolean;
  overrideScore: string;
  overrideReason: string;
  onOverrideScoreChange: (v: string) => void;
  onOverrideReasonChange: (v: string) => void;
  onOverrideSubmit: (gradeId: string) => void;
  onOverrideCancel: () => void;
  overridePending: boolean;
  overrideError: string;
}) {
  if (!sessionGrade) {
    return <p className="text-sm text-gray-500">Loading session details...</p>;
  }

  const gradeId = sessionGrade.grade_id;
  const finalScore = sessionGrade.final_score !== undefined
    ? Math.round(sessionGrade.final_score * 100)
    : null;

  return (
    <div className="space-y-4">
      {/* Per-turn evaluations */}
      {sessionGrade.evaluations && sessionGrade.evaluations.length > 0 ? (
        <div>
          <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
            Turn-by-Turn Evaluation
          </h4>
          <div className="space-y-2">
            {sessionGrade.evaluations.map((ev) => (
              <div
                key={ev.turn_index}
                className="flex items-center gap-4 bg-white rounded-lg border border-gray-100 px-4 py-2"
              >
                <span className="text-xs font-mono text-gray-400 w-8">Q{ev.turn_index + 1}</span>
                <span className={`text-xs font-medium px-2 py-0.5 rounded ${ev.adequate ? 'bg-green-50 text-green-700' : ev.answered ? 'bg-yellow-50 text-yellow-700' : 'bg-red-50 text-red-700'}`}>
                  {ev.adequate ? 'Adequate' : ev.answered ? 'Partial' : 'Not Answered'}
                </span>
                <span className="text-xs text-gray-500">EDS: +{ev.eds_delta}</span>
                <span className={`text-xs font-medium ${edsColor(ev.eds_score * 100)}`}>
                  {ev.eds_bucket}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-sm text-gray-500">No evaluations recorded for this session.</p>
      )}

      {/* Grade override */}
      <div className="flex items-center gap-3 pt-2 border-t border-gray-200">
        {finalScore !== null && (
          <span className="text-sm text-gray-600">
            Final Score: <span className={`font-bold ${edsColor(finalScore)}`}>{finalScore}</span>
          </span>
        )}
        {gradeId && !overrideActive && (
          <button
            onClick={(e) => { e.stopPropagation(); onOverride(); }}
            className="text-xs px-3 py-1 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
          >
            Override Grade
          </button>
        )}
      </div>

      {overrideActive && gradeId && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
          <h4 className="text-sm font-medium text-gray-700">Grade Override</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">New Score (0-100)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={overrideScore}
                onChange={(e) => onOverrideScoreChange(e.target.value)}
                className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="e.g. 75"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Reason</label>
              <input
                type="text"
                value={overrideReason}
                onChange={(e) => onOverrideReasonChange(e.target.value)}
                className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Reason for override"
              />
            </div>
          </div>
          {overrideError && (
            <p className="text-xs text-red-600">{overrideError}</p>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => onOverrideSubmit(gradeId)}
              disabled={overridePending || !overrideScore || !overrideReason.trim()}
              className="text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {overridePending ? 'Saving...' : 'Save Override'}
            </button>
            <button
              onClick={onOverrideCancel}
              className="text-xs px-3 py-1.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
