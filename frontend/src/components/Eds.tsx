import type { EDSComponents } from '../api/exam';

// Shared Epistemic Depth Score (EDS) vocabulary — one gauge, one breakdown, one
// explanation, used identically in-exam (TakeExam) and on the Results page so the
// student never sees two different score languages for the same thing.

export function edsBand(score: number): { label: string; color: string } {
  return score >= 85
    ? { label: 'Distinction', color: '#22c55e' }
    : score >= 70
    ? { label: 'Proficient', color: '#2563eb' }
    : score >= 50
    ? { label: 'Developing', color: '#f59e0b' }
    : { label: 'Starting', color: '#6b7280' };
}

// ── EDS Arc Gauge ────────────────────────────────────────────────────────────

export function EDSGauge({ score }: { score: number }) {
  const r = 54, cx = 70, cy = 70;
  const circ = Math.PI * r; // half-circle arc length
  const pct = Math.min(score / 100, 1);
  const dash = pct * circ;
  const band = edsBand(score);

  return (
    <div className="flex flex-col items-center">
      <svg width={140} height={80} viewBox="0 0 140 80">
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none" stroke="#e5e7eb" strokeWidth={10} strokeLinecap="round"
        />
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none" stroke={band.color} strokeWidth={10} strokeLinecap="round"
          strokeDasharray={`${dash} ${circ}`}
          style={{ transition: 'stroke-dasharray 0.6s ease, stroke 0.4s' }}
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

// ── EDS Breakdown ────────────────────────────────────────────────────────────

const EDS_PARTS: { key: keyof EDSComponents; label: string; color: string }[] = [
  { key: 'node_score', label: 'Concepts', color: 'bg-blue-500' },
  { key: 'edge_score', label: 'Causal Links', color: 'bg-purple-500' },
  { key: 'r_gate', label: 'Authenticity', color: 'bg-green-500' },
  { key: 'gen_score', label: 'Novel Insight', color: 'bg-amber-500' },
];

export function EDSBreakdown({ components }: { components: EDSComponents | null }) {
  if (!components) return null;
  return (
    <div className="px-3 py-2 space-y-1.5">
      <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400">Score Breakdown</p>
      {EDS_PARTS.map(({ key, label, color }) => {
        const value = components[key] ?? 0;
        return (
          <div key={label} className="flex items-center gap-2">
            <span className="text-[10px] text-gray-500 w-16 truncate">{label}</span>
            <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.round(value * 100)}%` }} />
            </div>
            <span className="text-[10px] font-mono text-gray-500 w-7 text-right">
              {Math.round(value * 100)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── One-line explainer — defines the acronym in context ──────────────────────

export function EDSExplainer({ className = '' }: { className?: string }) {
  return (
    <p className={`text-xs text-gray-500 leading-relaxed ${className}`}>
      <span className="font-semibold text-gray-600">Epistemic Depth Score (EDS)</span> rates the
      reasoning in your answers out of 100 — the concepts you covered, the causal links you
      explained, how authentic the reasoning was, and any novel insight.
    </p>
  );
}
