import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getDemoMetrics, type DemoSince } from '../../api/demoMetrics';

const SINCE_OPTIONS: { v: DemoSince; label: string }[] = [
  { v: '24h', label: '24h' },
  { v: '7d', label: '7 days' },
  { v: '30d', label: '30 days' },
  { v: 'all', label: 'All time' },
];

const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${Math.round(v * 100)}%`;
const secs = (ms: number | null | undefined) =>
  ms == null ? '—' : `${(ms / 1000).toFixed(1)}s`;
const mmss = (ms: number | null | undefined) => {
  if (ms == null) return '—';
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const num = (v: number | null | undefined) => (v == null ? '—' : `${v}`);

function RateCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-white rounded-xl border border-border p-5">
      <div className="text-3xl font-heading text-navy">{value}</div>
      <div className="text-sm text-ink mt-1">{label}</div>
      {hint && <div className="text-xs text-muted mt-0.5">{hint}</div>}
    </div>
  );
}

export default function DemoMetrics() {
  const [since, setSince] = useState<DemoSince>('7d');
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['demo-metrics', since],
    queryFn: () => getDemoMetrics(since),
  });

  const funnelMax = data ? Math.max(data.funnel.opened, 1) : 1;
  const stages = data
    ? [
        { k: 'Opened', n: data.funnel.opened },
        { k: 'Started', n: data.funnel.started },
        { k: 'Mic granted', n: data.funnel.mic_granted },
        { k: 'Completed', n: data.funnel.completed },
      ]
    : [];

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-heading text-navy">Demo engagement</h1>
          <p className="text-muted text-sm mt-1">
            Anonymous funnel &amp; engagement for the credential-free speaking demos. Accrues going forward.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-border overflow-hidden">
            {SINCE_OPTIONS.map((o) => (
              <button
                key={o.v}
                onClick={() => setSince(o.v)}
                className={`px-3 py-1.5 text-sm ${
                  since === o.v ? 'bg-navy text-white' : 'bg-white text-ink hover:bg-parchment-dark'
                }`}
              >
                {o.label}
              </button>
            ))}
          </div>
          <button onClick={() => refetch()} className="btn-ghost">
            {isFetching ? '…' : 'Refresh'}
          </button>
        </div>
      </div>

      {isLoading && <div className="text-muted text-sm">Loading…</div>}
      {isError && <div className="text-rust text-sm">Couldn’t load demo metrics.</div>}

      {data && (data.status === 'no_data' || data.sessions === 0) && (
        <div className="bg-white rounded-xl border border-border p-8 text-center">
          <div className="font-heading text-navy text-lg">No demo activity yet</div>
          <p className="text-muted text-sm mt-1">
            Metrics appear here as credential-free demo exams are run (this window: {SINCE_OPTIONS.find((o) => o.v === since)?.label}).
          </p>
        </div>
      )}

      {data && data.status === 'ok' && data.sessions > 0 && (
        <>
          <div className="text-xs text-muted">{data.sessions} demo session{data.sessions === 1 ? '' : 's'} in this window</div>

          {/* rate cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <RateCard label="Completion rate" value={pct(data.rates.completion_rate)} hint="completed / started" />
            <RateCard label="Start rate" value={pct(data.rates.start_rate)} hint="started / opened" />
            <RateCard label="Mic-grant rate" value={pct(data.rates.mic_grant_rate)} hint="granted / asked" />
            <RateCard label="Reached end" value={pct(data.rates.pct_reached_completion)} hint="of started sessions" />
          </div>

          {/* funnel */}
          <div className="bg-white rounded-xl border border-border p-5">
            <div className="text-xs font-medium text-gold uppercase tracking-wide mb-3">Funnel</div>
            <div className="space-y-2.5">
              {stages.map((s, i) => {
                const prev = i > 0 ? stages[i - 1].n : null;
                const drop = prev && prev > 0 ? Math.round((1 - s.n / prev) * 100) : null;
                return (
                  <div key={s.k} className="flex items-center gap-3">
                    <div className="w-28 text-sm text-ink shrink-0">{s.k}</div>
                    <div className="flex-1 bg-parchment-dark rounded h-6 overflow-hidden">
                      <div
                        className="h-6 bg-navy rounded flex items-center px-2"
                        style={{ width: `${Math.max((s.n / funnelMax) * 100, s.n > 0 ? 6 : 0)}%` }}
                      >
                        <span className="text-xs text-white font-medium">{s.n}</span>
                      </div>
                    </div>
                    <div className="w-16 text-right text-xs text-muted shrink-0">
                      {drop != null ? `−${drop}%` : ''}
                    </div>
                  </div>
                );
              })}
            </div>
            {data.funnel.mic_denied > 0 && (
              <div className="text-xs text-muted mt-3">Mic denied: {data.funnel.mic_denied}</div>
            )}
          </div>

          {/* medians */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <RateCard label="Median session time" value={mmss(data.medians.session_duration_ms)} hint="open → last event" />
            <RateCard label="Median speaking time" value={secs(data.medians.active_speaking_ms)} hint="Azure-mode answers only" />
            <RateCard label="Median questions answered" value={num(data.medians.questions_answered)} />
          </div>

          {/* raw event counts */}
          {Object.keys(data.event_counts || {}).length > 0 && (
            <div className="bg-white rounded-xl border border-border p-5">
              <div className="text-xs font-medium text-gold uppercase tracking-wide mb-3">Events</div>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
                {Object.entries(data.event_counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm border-b border-border/60 py-1">
                      <span className="text-muted">{k}</span>
                      <span className="text-ink font-medium tabular-nums">{v}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
