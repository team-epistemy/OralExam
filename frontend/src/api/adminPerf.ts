import { get, post } from './client';

// Admin performance probe: times the end-to-end student answer flow (eval LLM +
// TTS per run) plus a one-off expected_path generation, run in the background.

export interface PerfStat {
  min: number;
  p50: number;
  max: number;
  runs: number[];
}

export interface PerfStep {
  name: string;      // "eval_llm" | "tts"
  ms: number;
  detail: string | null;  // model id
}

export interface PerfTrace {
  run: number;
  total_ms: number;
  steps: PerfStep[];
  probe: string;     // the probe the eval produced this run
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
  test_question: string;
  test_answer: string;
  sample_probe: string;
  sample_feedback: string;
  traces: PerfTrace[];
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
