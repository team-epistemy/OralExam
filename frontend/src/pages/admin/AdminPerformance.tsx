import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Gauge, Loader2, Play, AlertCircle, Zap, Mic, Timer, AlertTriangle, History } from 'lucide-react';
import { ApiError } from '../../api/client';
import {
  startPerfProbe, getPerfProbe, listPerfProbes,
  type PerfProbe, type PerfStat, type PerfTrace,
} from '../../api/adminPerf';

function secs(ms: number) { return (ms / 1000).toFixed(2) + 's'; }

const STEP_COLORS: Record<string, string> = { eval_llm: 'bg-amber-500', tts: 'bg-blue-500' };
const STEP_LABELS: Record<string, string> = { eval_llm: 'Eval LLM', tts: 'TTS' };

// Per-run waterfall: each run's total split into its step segments (eval + TTS),
// scaled to the slowest run so runs are visually comparable.
function TraceWaterfall({ traces }: { traces: PerfTrace[] }) {
  const maxTotal = Math.max(1, ...traces.map((t) => t.total_ms));
  return (
    <div className="space-y-1.5">
      {traces.map((t) => (
        <div key={t.run} className="flex items-center gap-3 text-xs">
          <span className="w-8 text-gray-400 tabular-nums">#{t.run}</span>
          <div className="flex-1 flex h-6 rounded overflow-hidden bg-gray-100">
            {t.steps.map((s, i) => (
              <div key={i}
                   className={`${STEP_COLORS[s.name] ?? 'bg-gray-400'} h-full flex items-center justify-center text-[10px] text-white whitespace-nowrap`}
                   style={{ width: `${(s.ms / maxTotal) * 100}%` }}
                   title={`${STEP_LABELS[s.name] ?? s.name}: ${s.ms} ms`}>
                {s.ms / maxTotal > 0.12 ? `${(s.ms / 1000).toFixed(1)}s` : ''}
              </div>
            ))}
          </div>
          <span className="w-14 text-right tabular-nums text-gray-700">{secs(t.total_ms)}</span>
        </div>
      ))}
    </div>
  );
}

function StatCard({ icon: Icon, label, stat, tone = 'neutral', hint }: {
  icon: typeof Zap; label: string; stat: PerfStat; tone?: 'good' | 'warn' | 'bad' | 'neutral'; hint?: string;
}) {
  const ring = tone === 'bad' ? 'border-l-red-500' : tone === 'warn' ? 'border-l-amber-500'
    : tone === 'good' ? 'border-l-green-500' : 'border-l-navy/40';
  return (
    <div className={`bg-white border border-gray-200 border-l-4 ${ring} rounded-lg p-4`}>
      <div className="flex items-center gap-2 text-sm font-medium text-gray-700">
        <Icon className="w-4 h-4" /> {label}
      </div>
      <div className="mt-2 text-3xl font-bold text-gray-900 tabular-nums">{secs(stat.p50)}</div>
      <div className="text-xs text-gray-500 mt-0.5">median · min {secs(stat.min)} · max {secs(stat.max)}</div>
      {hint && <div className="text-[11px] text-gray-400 mt-1.5 leading-snug">{hint}</div>}
    </div>
  );
}

export default function AdminPerformance() {
  const queryClient = useQueryClient();
  const [runs, setRuns] = useState(3);
  const [probeId, setProbeId] = useState<string | null>(null);
  const [error, setError] = useState('');

  const start = useMutation({
    mutationFn: () => startPerfProbe(runs),
    onSuccess: (res) => {
      if (res.status === 'error' || !res.probe_id) { setError(res.message || 'Could not start probe.'); return; }
      setError(''); setProbeId(res.probe_id);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Failed to start probe.'),
  });

  const { data: probe } = useQuery<PerfProbe>({
    queryKey: ['perf-probe', probeId],
    queryFn: () => getPerfProbe(probeId as string),
    enabled: !!probeId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s && s !== 'running' ? false : 1500;
    },
  });

  const { data: history } = useQuery({
    queryKey: ['perf-probes'],
    queryFn: listPerfProbes,
  });

  useEffect(() => {
    if (probe && probe.status !== 'running') {
      if (probe.status === 'failed') setError(probe.error || 'Probe failed.');
      queryClient.invalidateQueries({ queryKey: ['perf-probes'] });
    }
  }, [probe?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const running = start.isPending || probe?.status === 'running';
  const r = probe?.status === 'completed' ? probe.result : undefined;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <Gauge className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">Performance</h1>
          <p className="text-sm text-muted">
            Time the end-to-end student answer flow — the evaluation LLM call and the ElevenLabs TTS
            round-trip — plus the one-off expected-path generation a first answer pays.
          </p>
        </div>
      </div>

      {/* Controls */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Timed rounds: <span className="font-bold text-navy">{runs}</span>
          </label>
          <input type="range" min={1} max={8} value={runs}
            onChange={(e) => setRuns(Number(e.target.value))}
            className="w-full accent-navy" disabled={running} />
          <div className="flex justify-between text-[10px] text-gray-400 px-0.5">
            {Array.from({ length: 8 }, (_, i) => <span key={i}>{i + 1}</span>)}
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0" /> {error}
          </div>
        )}

        <button
          onClick={() => start.mutate()}
          disabled={running}
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50"
        >
          {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          {running ? 'Probing…' : 'Run probe'}
        </button>
        {running && (
          <p className="text-xs text-gray-400 -mt-2">
            Each round makes a live eval + TTS call; one expected-path call runs at the end. Result is saved for history.
          </p>
        )}
      </div>

      {/* Results */}
      {r && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <StatCard icon={Timer} label="Total per answer" stat={r.total_per_answer_ms}
              tone={r.total_per_answer_ms.p50 > 5000 ? 'bad' : r.total_per_answer_ms.p50 > 3000 ? 'warn' : 'good'}
              hint="What a student waits after submitting (eval + TTS), when the question's path is pre-built." />
            <StatCard icon={Zap} label="Answer-eval LLM" stat={r.eval_ms}
              tone={r.eval_ms.p50 > 4000 ? 'warn' : 'neutral'}
              hint={`${r.provider ?? ''} · ${r.eval_model ?? ''}`} />
            <StatCard icon={Mic} label="TTS round-trip" stat={r.tts_ms}
              tone={r.tts_ms.p50 > 2000 ? 'warn' : 'good'}
              hint={`${r.tts_model ?? ''} · ${(r.tts_audio_bytes / 1024).toFixed(0)} KB audio`} />
          </div>

          {/* First-answer penalty callout */}
          <div className={`rounded-lg p-4 border flex items-start gap-3 ${
            r.first_answer_path_penalty_ms > 4000 ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200'}`}>
            <AlertTriangle className={`w-5 h-5 shrink-0 mt-0.5 ${
              r.first_answer_path_penalty_ms > 4000 ? 'text-red-600' : 'text-gray-400'}`} />
            <div className="text-sm">
              <div className="font-semibold text-gray-900">
                First-answer expected-path penalty: {secs(r.first_answer_path_penalty_ms)}
              </div>
              <p className="text-gray-600 mt-0.5">
                This LLM call fires only on the <em>first</em> answer to a question whose expected reasoning
                path wasn't pre-built — added <strong>on top of</strong> the eval above. Pre-computing paths at
                assignment creation removes it from the live answer flow.
              </p>
            </div>
          </div>

          {/* Per-run trace breakdown */}
          {r.traces?.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
              <h3 className="text-sm font-medium text-gray-700">Trace breakdown (per run)</h3>
              <div className="flex items-center gap-4 text-[11px] text-gray-500">
                <span className="inline-flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-amber-500 inline-block" /> Eval LLM</span>
                <span className="inline-flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-blue-500 inline-block" /> TTS</span>
              </div>
              <TraceWaterfall traces={r.traces} />
              <div className="border-t border-gray-100 pt-3 text-xs text-gray-600 space-y-1.5">
                <div><span className="text-gray-400">Test question:</span> {r.test_question}</div>
                <div><span className="text-gray-400">Simulated answer:</span> {r.test_answer}</div>
                {r.sample_probe && <div><span className="text-gray-400">Examiner probe (from eval):</span> {r.sample_probe}</div>}
              </div>
              <p className="text-[11px] text-gray-400">
                Each step's latency is also logged to CloudWatch (filter <code>perf-trace</code>) for offline analysis.
              </p>
            </div>
          )}

          <p className="text-xs text-gray-400">
            Measured server-side from the app environment (same region/network as production), averaged over {r.runs} run{r.runs === 1 ? '' : 's'}.
          </p>
        </div>
      )}

      {/* Saved history */}
      {history?.probes && history.probes.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <History className="w-4 h-4" /> Saved probes
          </h2>
          <div className="bg-white border border-gray-200 rounded-lg divide-y divide-gray-100">
            {history.probes.map((p) => (
              <button
                key={p.probe_id}
                onClick={() => { setProbeId(p.probe_id); setError(''); }}
                className={`w-full text-left px-4 py-2.5 text-sm flex items-center gap-3 hover:bg-gray-50 ${p.probe_id === probeId ? 'bg-gray-50' : ''}`}
              >
                <span className="text-xs text-gray-400 w-36 shrink-0">{p.created_at ? new Date(p.created_at).toLocaleString() : ''}</span>
                <span className="text-xs text-gray-400 w-14">{p.runs} run{p.runs === 1 ? '' : 's'}</span>
                {p.status === 'running' && <span className="text-xs text-blue-600 inline-flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> running</span>}
                {p.status === 'failed' && <span className="text-xs text-red-600">failed</span>}
                <span className="flex-1" />
                {p.status === 'completed' && (
                  <>
                    <span className="text-xs text-gray-700 tabular-nums">total {p.total_p50_ms != null ? (p.total_p50_ms / 1000).toFixed(2) + 's' : '—'}</span>
                    <span className="text-xs text-gray-400 tabular-nums">eval {p.eval_p50_ms != null ? (p.eval_p50_ms / 1000).toFixed(1) + 's' : '—'}</span>
                    <span className="text-xs text-gray-400 tabular-nums">tts {p.tts_p50_ms != null ? (p.tts_p50_ms / 1000).toFixed(1) + 's' : '—'}</span>
                    <span className="text-xs text-amber-700 tabular-nums">path {p.path_penalty_ms != null ? (p.path_penalty_ms / 1000).toFixed(1) + 's' : '—'}</span>
                  </>
                )}
              </button>
            ))}
          </div>
          <p className="text-[11px] text-gray-400 mt-1">Click a saved run to load its full trace breakdown above.</p>
        </div>
      )}
    </div>
  );
}
