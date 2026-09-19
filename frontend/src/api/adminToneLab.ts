import { get, post } from './client';

// Admin Examiner Tone Lab: live-fetch the production examiner prompt + curated cases,
// persist experiment results with a server-side recommendation, and run the governed
// examiner-prompt override lifecycle (draft → approve → activate / revert).

// ── Live source ────────────────────────────────────────────────────────────
export interface ExaminerPrompt {
  system: string;
  prompt_version: string;
  model: string | null;
  temperature: number;
  input_mode: 'chat' | 'block';
  source: string;
  captured_at: string;
  context: Record<string, unknown>;
}

export interface EvalCaseEdge { id: string; eps: number; text: string }
export interface EvalCase { domain: string; stem: string; graph: EvalCaseEdge[] }
export interface EvalCases { cases: Record<string, EvalCase> }

// ── Experiments ────────────────────────────────────────────────────────────
export interface ArmMetrics {
  arm: string;
  turns: number;
  leak_rate: number;
  mean_words: number;
  mean_probe: number;
  mean_receipt: number;
  evaluative: number;
  confirms: number;
  compound: number;
  empty_probe: number;
  parse_fails: number;
}

export interface Recommendation {
  pick: string | null;
  rationale: string;
  ranking: { arm: string; penalty: number; leak_rate: number; evaluative: number; confirms: number }[];
}

export interface ExperimentSummary { arms: ArmMetrics[] }

export interface ExperimentConfig {
  arms: string[];
  questions: string[];
  personas: string[];
  model: string;
  temperature: number;
  reps: number;
}

export interface SaveExperimentBody {
  title?: string;
  prompt_version?: string;
  config: ExperimentConfig;
  summary: ExperimentSummary;
}

export interface SavedExperiment {
  experiment_id: string;
  title: string | null;
  prompt_version: string | null;
  summary: ExperimentSummary | null;
  recommendation: Recommendation | null;
  config?: ExperimentConfig | null;
  created_by: string | null;
  created_at: string | null;
}

// ── Prompt override governance ───────────────────────────────────────────────
export type OverrideStatus = 'draft' | 'approved' | 'active' | 'rejected' | 'archived';

export interface PromptOverride {
  override_id: string;
  notes: string | null;
  status: OverrideStatus;
  based_on_experiment_id: string | null;
  created_by: string | null;
  created_at: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  activated_at: string | null;
}

export interface OverrideList {
  overrides: PromptOverride[];
  default_version: string;
  active_version: string;
  default_template: string;
}

// ── Calls ────────────────────────────────────────────────────────────────────
export function fetchExaminerPrompt(params?: { course?: string; instructor?: string; question_id?: string }):
  Promise<ExaminerPrompt> {
  const q = new URLSearchParams();
  if (params?.course) q.set('course', params.course);
  if (params?.instructor) q.set('instructor', params.instructor);
  if (params?.question_id) q.set('question_id', params.question_id);
  const qs = q.toString();
  return get(`/api/admin/eval/examiner-prompt${qs ? `?${qs}` : ''}`);
}

export function fetchEvalCases(): Promise<EvalCases> {
  return get('/api/admin/eval/cases');
}

export function saveExperiment(body: SaveExperimentBody):
  Promise<{ experiment_id?: string; status: string; recommendation?: Recommendation; message?: string }> {
  return post('/api/admin/eval/experiments', body);
}

export function listExperiments(): Promise<{ experiments: SavedExperiment[] }> {
  return get('/api/admin/eval/experiments');
}

export function getExperiment(id: string): Promise<SavedExperiment & { status?: string }> {
  return get(`/api/admin/eval/experiments/${id}`);
}

export function createOverride(body: { template: string; notes?: string; based_on_experiment_id?: string }):
  Promise<{ override_id?: string; status: string; message?: string }> {
  return post('/api/admin/eval/prompt-override', body);
}

export function listOverrides(): Promise<OverrideList> {
  return get('/api/admin/eval/prompt-override');
}

export function approveOverride(id: string): Promise<{ status: string; message?: string }> {
  return post(`/api/admin/eval/prompt-override/${id}/approve`);
}

export function activateOverride(id: string): Promise<{ status: string; message?: string }> {
  return post(`/api/admin/eval/prompt-override/${id}/activate`);
}

export function revertOverride(id: string): Promise<{ status: string; message?: string }> {
  return post(`/api/admin/eval/prompt-override/${id}/revert`);
}

export function rejectOverride(id: string): Promise<{ status: string; message?: string }> {
  return post(`/api/admin/eval/prompt-override/${id}/reject`);
}
