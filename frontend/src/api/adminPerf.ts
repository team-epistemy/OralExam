import { get, post, put } from './client';

// Answer-flow evaluation mode: 'sonnet' (single Sonnet call, higher fidelity, ~3s) or
// 'hybrid' (fast Haiku spoken probe + async Sonnet EDS scoring, ~1.5–2s).
export interface EvalMode {
  eval_mode: 'sonnet' | 'hybrid';
  text_first: boolean;
  updated_by: string | null;
  updated_at: string | null;
  available: string[];
}

export function getEvalMode(): Promise<EvalMode> {
  return get('/api/admin/examiner/eval-mode');
}

export function setEvalMode(eval_mode: 'sonnet' | 'hybrid'):
  Promise<{ eval_mode: string; status: string; message?: string }> {
  return put('/api/admin/examiner/eval-mode', { eval_mode });
}

// Text-first render toggle: true = student UI shows the probe immediately and plays
// TTS async (~instant text); false = probe text is revealed together with the audio.
export function setTextFirst(text_first: boolean):
  Promise<{ text_first: boolean; status: string; message?: string }> {
  return put('/api/admin/examiner/text-first', { text_first });
}

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
  params?: PerfParams;
}

export interface PerfParams {
  runs: number;
  eval_model: string;
  eval_max_tokens: number;
  eval_temperature: number;
  tts_model: string;
  tts_voice: string;
  provider?: string;
  expected_path_max_tokens?: number;
}

export interface PerfProbe {
  probe_id: string;
  status: 'running' | 'completed' | 'failed' | 'not_found';
  result?: PerfResult | null;
  error?: string | null;
  title?: string | null;
  params?: PerfParams | null;
}

export interface PerfListItem {
  probe_id: string;
  title: string | null;
  runs: number;
  status: string;
  provider: string | null;
  eval_model: string | null;
  tts_model: string | null;
  eval_p50_ms: number | null;
  tts_p50_ms: number | null;
  total_p50_ms: number | null;
  path_penalty_ms: number | null;
  created_by: string | null;
  created_at: string | null;
}

export function getPerfDefaults(): Promise<{ defaults: PerfParams }> {
  return get('/api/admin/perf/defaults');
}

export function startPerfProbe(body: { title?: string; params?: Partial<PerfParams> }):
  Promise<{ probe_id?: string; status: string; message?: string }> {
  return post('/api/admin/perf/probe', body);
}

export function getPerfProbe(id: string): Promise<PerfProbe> {
  return get(`/api/admin/perf/probe/${id}`);
}

export function listPerfProbes(): Promise<{ probes: PerfListItem[] }> {
  return get('/api/admin/perf/probes');
}
