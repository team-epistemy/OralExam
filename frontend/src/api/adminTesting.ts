import { get, post, put } from './client';

// ── Question-Generation (QG) quality test bench ──────────────────────────────
// Sources a real subject's concept graph, live-generates a question batch with
// the same generator students get, and grades it against QG-01..QG-06.

export interface TestingSubject {
  course_id: string;
  course_name: string;
  node_count: number;
  edge_count: number;
  testable: boolean;   // has a concept graph to grade against
}

export type QGStatus = 'pass' | 'fail' | 'skip';

export interface QGResult {
  criterion: string;              // e.g. "QG-01"
  id: string;                     // question_id or "BATCH"
  status: QGStatus;
  reasoning: string;
  question: string | null;        // null for batch-level checks
  declared: string | null;        // declared difficulty (recall/balanced/deep)
  classified: string | null;      // structure-classified difficulty
}

export type QGSummary = Record<string, { pass: number; fail: number; skip: number }>;

export interface QGReport {
  subject: string;
  results: QGResult[];
  summary: QGSummary;
  graph: { node_count: number; edge_count: number };
  thresholds: Record<string, number>;
}

export interface TestRunResult {
  status: string;                 // "completed" | "error"
  message?: string;               // present when status === "error"
  run_id?: string;
  course_id?: string;
  course_name?: string;
  difficulty?: string;
  generated_count?: number;
  report?: QGReport;
}

export interface TestRunListItem {
  run_id: string;
  course_id: string;
  course_name: string;
  difficulty: string;
  generated_count: number;
  status: string;
  fail_count: number;
  created_at: string | null;
}

export interface CreateTestRunBody {
  course_id: string;
  count: number;
  difficulty: 'recall' | 'balanced' | 'deep';
  concept_ids?: string[];
}

// The criteria labels, mirrored client-side so the report always shows all six
// cards in order (even criteria with only skips). Kept in sync with qg_bench.py.
export const CRITERIA_META: Record<string, string> = {
  'QG-01': 'Every recall question grounds to a listed concept',
  'QG-02': 'Every balanced question asserts a causal edge that exists in the graph',
  'QG-03': 'Every deep question carries a multi-hop expected reasoning path',
  'QG-04': 'Declared difficulty matches the structure-classified difficulty (≥95% target)',
  'QG-05': 'Naive baseline accuracy on deep questions (≤40% target)',
  'QG-06': 'No near-duplicate questions in the batch (<0.85 similarity)',
};

export function listTestingSubjects(): Promise<{ subjects: TestingSubject[] }> {
  return get('/api/admin/testing/subjects');
}

export function createTestRun(body: CreateTestRunBody): Promise<TestRunResult> {
  return post('/api/admin/testing/runs', body);
}

export function listTestRuns(): Promise<{ runs: TestRunListItem[] }> {
  return get('/api/admin/testing/runs');
}

export function getTestRun(runId: string): Promise<TestRunResult> {
  return get(`/api/admin/testing/runs/${runId}`);
}

// ── Saved generated questions + human evaluation ─────────────────────────────
export type HumanVerdict = 'good' | 'needs_edit' | 'reject';

export interface QGAutoResult {
  criterion: string;
  status: QGStatus;
  reasoning: string;
}

export interface QGQuestion {
  question_id: string;
  position: number;
  question_text: string | null;
  declared_difficulty: string | null;
  classified_difficulty: string | null;
  concept_ids: string[];
  expected_path: { nodes?: unknown[]; edges?: unknown[] } | null;
  auto_results: QGAutoResult[];
  auto_pass: number;
  auto_fail: number;
  human_verdict: HumanVerdict | null;
  human_rating: number | null;
  human_notes: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface HumanEvalBody {
  verdict?: HumanVerdict | null;
  rating?: number | null;
  notes?: string | null;
}

export function listRunQuestions(runId: string): Promise<{ questions: QGQuestion[] }> {
  return get(`/api/admin/testing/runs/${runId}/questions`);
}

export function saveQuestionEval(questionId: string, body: HumanEvalBody):
  Promise<{ status: string; question_id: string }> {
  return put(`/api/admin/testing/questions/${questionId}/eval`, body);
}
