import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Gauge, Loader2, Play, AlertCircle, Zap, Mic, Timer, AlertTriangle } from 'lucide-react';
import { ApiError } from '../../api/client';
import { startPerfProbe, getPerfProbe, type PerfProbe, type PerfStat } from '../../api/adminPerf';

function secs(ms: number) { return (ms / 1000).toFixed(2) + 's'; }

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
  const [runs, setRuns] = useState(3);
  const [probeId, setProbeId] = useState<string | null>(null);
  const [error, setError] = useState('');

  const start = useMutation({
    mutationFn: () => startPerfProbe(runs),
    onSuccess: (res) => { setError(''); setProbeId(res.probe_id); },
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

  useEffect(() => {
    if (probe?.status === 'failed') setError(probe.error || 'Probe failed.');
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
        {running && probe?.progress && (
          <p className="text-xs text-gray-400 -mt-2">
            Round {probe.progress.done}/{probe.progress.total} · each round makes a live eval + TTS call, then one expected-path call.
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

          <p className="text-xs text-gray-400">
            Measured server-side from the app environment (same region/network as production), averaged over {r.runs} run{r.runs === 1 ? '' : 's'}.
          </p>
        </div>
      )}
    </div>
  );
}
