import { get, post, createSSEConnection } from './client';

// ── Types ────────────────────────────────────────────────────────────────────

export interface ExamQuestion {
  question_id: string;
  topic: string;
  text: string;
}

export interface StartExamResponse {
  session_id: string;
  questions: ExamQuestion[];
}

export interface EDSComponents {
  node_score: number;
  edge_score: number;
  r_gate: number;
  gen_score: number;
}

export interface AnswerResponse {
  feedback: string;
  probe: string;
  adequate: boolean;
  answered: boolean;
  eds_delta: number;
  eds_question?: number;
  eds_components?: EDSComponents;
}

export interface SessionStatus {
  session_id: string;
  status: 'active' | 'completed';
  current_turn: number;
  total_questions: number;
  eds_score: number;
  turns: Array<{ index: number; answered: boolean; score: number }>;
}

// ── API calls ────────────────────────────────────────────────────────────────

export interface CaseMaterial {
  material_id: string;
  version_id: string;
  file_name: string;
  source_type: string;
}

export async function getAssignmentCase(assignmentId: string): Promise<CaseMaterial[]> {
  const res = await get<{ materials: CaseMaterial[] }>(`/api/assignments/${assignmentId}/case`);
  return res.materials || [];
}

export async function startExamSession(assignmentId: string): Promise<StartExamResponse> {
  return post<StartExamResponse>(`/api/assignments/${assignmentId}/start`, {});
}

// Read-only view of what a student sees, for a professor. Never starts a session.
export interface AssignmentPreview {
  assignment_id: string;
  title: string;
  assignment_type: AssignmentType;
  status: string;
  difficulty: string;
  duration_minutes: number | null;
  include_case: boolean;
  question_count: number;
  questions: Array<{ question_id: string; topic: string; text: string; index: number }>;
  case_materials: CaseMaterial[];
}

export async function getAssignmentPreview(assignmentId: string): Promise<AssignmentPreview> {
  return get<AssignmentPreview>(`/api/assignments/${assignmentId}/preview`);
}

export async function submitAnswer(
  sessionId: string,
  questionIndex: number,
  answerText: string,
): Promise<AnswerResponse> {
  return post<AnswerResponse>(`/api/sessions/${sessionId}/answer`, {
    question_index: questionIndex,
    answer_text: answerText,
  });
}

export async function getSessionStatus(sessionId: string): Promise<SessionStatus> {
  return get<SessionStatus>(`/api/sessions/${sessionId}/status`);
}

export async function completeSession(sessionId: string): Promise<void> {
  await post(`/api/sessions/${sessionId}/complete`, {});
}

// ── Results ──────────────────────────────────────────────────────────────────

export interface ExamResult {
  session_id: string;
  assignment_id: string;
  score: number;
  total_questions: number;
  questions_answered: number;
  feedback: string;
  components?: EDSComponents | null;
  question_results: {
    question_id: string;
    question_text: string;
    answer: string;
    score: number;
    feedback: string;
    components?: EDSComponents | null;
  }[];
  completed_at: string;
}

export async function getExamResults(assignmentId: string): Promise<ExamResult> {
  return get<ExamResult>(`/api/assignments/${assignmentId}/results`);
}

// ── SSE (kept for SSEProvider compatibility) ─────────────────────────────────

export function connectToExamSSE(sessionId: string): EventSource {
  return createSSEConnection(`/api/sessions/${sessionId}/stream`);
}

// ── Exam builder: 3 deterministic variants from the concept graph ────────────

export interface ExamVariantQuestion {
  topic: string;
  concept_id: string;
  q: string;
}

export interface ExamVariantDistribution {
  id: string;
  label: string;
  count: number;
}

export interface ExamVariant {
  id: string;
  bank_key: string;
  title: string;
  badge: string;
  badge_label: string;
  angle_label?: string;
  description: string;
  q_count: number;
  duration: string;
  eds_focus: string;
  distribution: ExamVariantDistribution[];
  questions: ExamVariantQuestion[];
}

export interface BuildExamResponse {
  status: string;
  concept_count?: number;
  variants?: ExamVariant[];
  message?: string;
  needs_rebuild?: boolean;   // stored banks were empty -> generic templates used
}

export interface BuildExamConfig {
  q_count: number;
  exam_len: number;
  difficulty: 'recall' | 'balanced' | 'deep';
  concept_ids?: string[];
}

export async function buildExam(courseId: string, cfg: BuildExamConfig): Promise<BuildExamResponse> {
  return post<BuildExamResponse>(`/api/courses/${courseId}/exams/build`, cfg);
}

// Author FRESH questions with the LLM at the chosen difficulty (synchronous —
// slower than buildExam, used when the professor presses Regenerate).
export async function regenerateExam(courseId: string, cfg: BuildExamConfig): Promise<BuildExamResponse> {
  return post<BuildExamResponse>(`/api/courses/${courseId}/exams/regenerate`, cfg);
}

export interface AssignExamResult {
  status: string;
  assignment_id?: string;
  question_count?: number;
  message?: string;
}

export type AssignmentType = 'practice' | 'assignment' | 'exam';

export async function assignExam(
  courseId: string,
  body: { title: string; questions: ExamVariantQuestion[]; difficulty: string; duration_minutes?: number; assignment_type?: AssignmentType; include_case?: boolean; session_id?: string; scope_concepts?: string[]; draft?: boolean },
): Promise<AssignExamResult> {
  return post<AssignExamResult>(`/api/courses/${courseId}/exams/assign`, body);
}

export async function publishAssignment(assignmentId: string): Promise<{ status: string; assignment_id: string }> {
  return post<{ status: string; assignment_id: string }>(`/api/assignments/${assignmentId}/publish`);
}

export async function discardDraft(assignmentId: string): Promise<{ status: string; assignment_id: string }> {
  return post<{ status: string; assignment_id: string }>(`/api/assignments/${assignmentId}/discard`);
}
