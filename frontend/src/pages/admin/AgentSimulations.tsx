import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, Loader2, Play, AlertCircle, Users, BarChart3 } from 'lucide-react';
import { ApiError } from '../../api/client';
import {
  listAdminAssignments, createSimulation, getSimulation, listSimulations,
  type SimReport,
} from '../../api/simulations';

const CURVES = [
  { id: 'linear' as const, label: 'Even spread', hint: 'agents span weak → strong evenly' },
  { id: 'bell' as const, label: 'Bell curve', hint: 'most agents average, few at the extremes' },
];

function scoreColor(s: number) {
  if (s >= 80) return 'bg-green-500';
  if (s >= 60) return 'bg-blue-500';
  if (s >= 40) return 'bg-amber-500';
  return 'bg-red-500';
}

// Horizontal 0-100 bar.
function Bar({ value, className = '' }: { value: number; className?: string }) {
  return (
    <div className={`h-2 bg-gray-100 rounded-full overflow-hidden ${className}`}>
      <div className={`h-full rounded-full ${scoreColor(value)}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

function Report({ report }: { report: SimReport }) {
  const agg = report.aggregate;
  const maxBucket = Math.max(1, ...Object.values(agg.distribution));
  return (
    <div className="space-y-6">
      {/* Aggregate tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[['Mean', agg.mean], ['Min', agg.min], ['Max', agg.max], ['Std dev', agg.stdev]].map(([k, v]) => (
          <div key={k} className="bg-white border border-gray-200 rounded-lg p-3">
            <div className="text-xs text-gray-500">{k}</div>
            <div className="text-2xl font-bold text-gray-900">{v}</div>
          </div>
        ))}
      </div>

      {/* Score distribution */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">Score distribution</h3>
        <div className="space-y-2">
          {Object.entries(agg.distribution).map(([bucket, count]) => (
            <div key={bucket} className="flex items-center gap-3">
              <span className="w-16 text-xs text-gray-500 tabular-nums">{bucket}</span>
              <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
                <div className="h-full bg-blue-500/80" style={{ width: `${(count / maxBucket) * 100}%` }} />
              </div>
              <span className="w-6 text-xs text-gray-600 text-right tabular-nums">{count}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Per-agent table */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
          <Users className="w-4 h-4" /> Agents ({report.num_agents}) — competence vs. score
        </h3>
        <div className="space-y-2">
          {report.agents.map((a) => (
            <div key={a.index} className="flex items-center gap-3 text-sm">
              <span className="w-8 text-gray-400 tabular-nums">#{a.index}</span>
              <span className="w-24 text-xs text-gray-500">skill {a.skill.toFixed(2)}</span>
              <div className="flex-1"><Bar value={a.score} /></div>
              <span className="w-10 text-right font-medium text-gray-900 tabular-nums">{a.score}</span>
              <span className="w-24 text-right text-xs text-gray-400">
                {a.adequate}/{a.questions} adequate
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Per-question difficulty */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
          <BarChart3 className="w-4 h-4" /> Per-question difficulty (avg score across agents)
        </h3>
        <div className="space-y-2">
          {report.per_question.map((q) => (
            <div key={q.index} className="flex items-center gap-3 text-sm">
              <span className="w-8 text-gray-400 tabular-nums">Q{q.index}</span>
              <span className="flex-1 min-w-0 truncate text-gray-600" title={q.text}>{q.text || q.topic}</span>
              <div className="w-40"><Bar value={q.avg_score} /></div>
              <span className="w-10 text-right font-medium text-gray-900 tabular-nums">{q.avg_score}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function AgentSimulations() {
  const [assignmentId, setAssignmentId] = useState('');
  const [numAgents, setNumAgents] = useState(5);
  const [curve, setCurve] = useState<'linear' | 'bell'>('linear');
  const [runningId, setRunningId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [launching, setLaunching] = useState(false);

  const { data: aData } = useQuery({
    queryKey: ['admin-assignments'],
    queryFn: listAdminAssignments,
  });
  const assignments = aData?.assignments ?? [];

  const { data: recent, refetch: refetchRecent } = useQuery({
    queryKey: ['admin-simulations'],
    queryFn: listSimulations,
  });

  // Poll the active run until it reaches a terminal status.
  const { data: sim } = useQuery({
    queryKey: ['admin-simulation', runningId],
    queryFn: () => getSimulation(runningId as string),
    enabled: !!runningId,
    refetchInterval: (q) => (q.state.data && q.state.data.status !== 'running' ? false : 2000),
  });

  useEffect(() => {
    if (sim && sim.status !== 'running') refetchRecent();
  }, [sim?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const launch = async () => {
    if (!assignmentId) return;
    setError('');
    setLaunching(true);
    try {
      const res = await createSimulation({ assignment_id: assignmentId, num_agents: numAgents, curve });
      if ((res as { status?: string }).status === 'error') {
        setError((res as { message?: string }).message || 'Could not start the simulation.');
      } else {
        setRunningId(res.simulation_id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to launch simulation.');
    } finally {
      setLaunching(false);
    }
  };

  const running = sim?.status === 'running';
  const done = sim && sim.status !== 'running';

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <Bot className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">Agent Simulations</h1>
          <p className="text-sm text-muted">
            Spawn 1–10 student agents on a competence curve, have them take an assignment, and read a performance report.
          </p>
        </div>
      </div>

      {/* Launch form */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Assignment</label>
          <select
            value={assignmentId}
            onChange={(e) => setAssignmentId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
          >
            <option value="">Select an assignment…</option>
            {assignments.map((a) => (
              <option key={a.id} value={a.id}>
                {a.course_name} — {a.title} ({a.question_count} q, {a.status})
              </option>
            ))}
          </select>
          {assignments.length === 0 && (
            <p className="text-xs text-gray-400 mt-1">No assignments found in your organization yet.</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Number of agents: <span className="font-bold text-navy">{numAgents}</span>
          </label>
          <input
            type="range" min={1} max={10} value={numAgents}
            onChange={(e) => setNumAgents(Number(e.target.value))}
            className="w-full accent-navy"
          />
          <div className="flex justify-between text-[10px] text-gray-400 px-0.5">
            {Array.from({ length: 10 }, (_, i) => <span key={i}>{i + 1}</span>)}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Competence curve</label>
          <div className="grid grid-cols-2 gap-2">
            {CURVES.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => setCurve(c.id)}
                className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                  curve === c.id ? 'border-navy bg-navy/5 ring-1 ring-navy' : 'border-gray-300 hover:bg-gray-50'
                }`}
              >
                <div className="text-sm font-semibold text-gray-900">{c.label}</div>
                <div className="text-xs text-gray-500">{c.hint}</div>
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0" /> {error}
          </div>
        )}

        <button
          onClick={launch}
          disabled={launching || running || !assignmentId}
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50"
        >
          {launching || running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          {running ? 'Simulation running…' : 'Run simulation'}
        </button>
        <p className="text-xs text-gray-400 -mt-2">
          Each agent answers every question (with adaptive follow-ups) using the same grader as real students — this spends LLM calls and can take a couple of minutes.
        </p>
      </div>

      {/* Live progress */}
      {running && sim?.progress && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <div className="flex items-center gap-2 text-sm text-blue-800 mb-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            Agents completed {sim.progress.agents_done}/{sim.progress.agents_total}
          </div>
          <div className="h-2 bg-blue-100 rounded-full overflow-hidden">
            <div className="h-full bg-blue-600 transition-all"
              style={{ width: `${(sim.progress.agents_done / Math.max(1, sim.progress.agents_total)) * 100}%` }} />
          </div>
        </div>
      )}

      {/* Failure */}
      {done && sim?.status === 'failed' && (
        <div className="flex items-center gap-2 p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <AlertCircle className="w-4 h-4 shrink-0" /> Simulation failed: {sim.error || 'unknown error'}
        </div>
      )}

      {/* Report */}
      {done && sim?.status === 'completed' && sim.report && (
        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-3">Performance report</h2>
          <Report report={sim.report} />
        </div>
      )}

      {/* Recent runs */}
      {recent?.simulations && recent.simulations.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Recent runs</h3>
          <div className="divide-y divide-gray-100">
            {recent.simulations.map((s) => (
              <button
                key={s.simulation_id}
                onClick={() => setRunningId(s.simulation_id)}
                className="w-full flex items-center gap-3 py-2 text-sm text-left hover:bg-gray-50 rounded px-1"
              >
                <span className="text-gray-400">{s.num_agents} agents · {s.curve}</span>
                <span className={`px-1.5 py-0.5 rounded text-xs ${
                  s.status === 'completed' ? 'bg-green-50 text-green-700'
                    : s.status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'
                }`}>{s.status}</span>
                <span className="flex-1" />
                <span className="text-gray-600">{s.mean_score != null ? `mean ${s.mean_score}` : '—'}</span>
                <span className="text-xs text-gray-400">
                  {s.created_at ? new Date(s.created_at).toLocaleString() : ''}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
