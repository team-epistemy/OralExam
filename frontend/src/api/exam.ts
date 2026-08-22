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
  question_results: {
    question_id: string;
    question_text: string;
    answer: string;
    score: number;
    feedback: string;
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

export interface AssignExamResult {
  status: string;
  assignment_id?: string;
  question_count?: number;
  message?: string;
}

export type AssignmentType = 'practice' | 'assignment' | 'exam';

export async function assignExam(
  courseId: string,
  body: { title: string; questions: ExamVariantQuestion[]; difficulty: string; duration_minutes?: number; assignment_type?: AssignmentType },
): Promise<AssignExamResult> {
  return post<AssignExamResult>(`/api/courses/${courseId}/exams/assign`, body);
}
