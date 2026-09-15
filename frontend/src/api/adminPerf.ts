import { get, post } from './client';

// Admin performance probe: times the end-to-end student answer flow (eval LLM +
// TTS per run) plus a one-off expected_path generation, run in the background.

export interface PerfStat {
  min: number;
  p50: number;
  max: number;
  runs: number[];
}

export interface PerfResult {
  runs: number;
  provider: string | null;
  eval_model: string | null;
  tts_model: string | null;
  eval_ms: PerfStat;
  tts_ms: PerfStat;
  total_per_answer_ms: PerfStat;
  first_answer_path_penalty_ms: number;
  tts_audio_bytes: number;
}

export interface PerfProbe {
  probe_id: string;
  status: 'running' | 'completed' | 'failed' | 'not_found';
  progress?: { done: number; total: number };
  result?: PerfResult;
  error?: string;
}

export function startPerfProbe(runs: number): Promise<PerfProbe> {
  return post('/api/admin/perf/probe', { runs });
}

export function getPerfProbe(id: string): Promise<PerfProbe> {
  return get(`/api/admin/perf/probe/${id}`);
}
