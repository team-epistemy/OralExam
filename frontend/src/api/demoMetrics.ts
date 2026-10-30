import { get } from './client';

// Anonymous engagement telemetry for the credential-free CEFR speaking demos.
// Rolls up the demo_event funnel into rates + medians (professor/platform_admin,
// org-scoped by RLS). Accrues going forward — no backfill of past demos.

export interface DemoMetrics {
  status: 'ok' | 'no_data';
  sessions: number;
  funnel: {
    opened: number;
    started: number;
    mic_granted: number;
    mic_denied: number;
    completed: number;
  };
  rates: {
    start_rate: number | null;        // started / opened
    mic_grant_rate: number | null;    // granted / (granted + denied)
    completion_rate: number | null;   // completed / started
    pct_reached_completion: number | null;
  };
  medians: {
    session_duration_ms: number | null;
    active_speaking_ms: number | null;
    questions_answered: number | null;
  };
  event_counts: Record<string, number>;
}

export type DemoSince = '24h' | '7d' | '30d' | 'all';

export function getDemoMetrics(since: DemoSince = '7d', token?: string): Promise<DemoMetrics> {
  const qs = new URLSearchParams();
  if (since && since !== 'all') qs.set('since', since);
  if (token) qs.set('token', token);
  const q = qs.toString();
  return get<DemoMetrics>(`/api/admin/demo-metrics${q ? `?${q}` : ''}`);
}
