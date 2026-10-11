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
  // Set only for a CEFR speaking assignment; the examiner's spoken welcome names
  // the theme, so the student hears what the test is about before it starts.
  exam_kind?: string | null;
  theme?: string;
}

export interface EDSComponents {
  node_score: number;
  edge_score: number;
  r_gate: number;
  gen_score: number;
}

// Per-turn CEFR grid returned by the communication (speaking) exam. Mirrors the
// `cefr` block built in backend/communication/turn.py. Bands are 1-6 (A1-C2);
// band *names* are provided for display.
export type CefrBandName = 'A1' | 'A2' | 'B1' | 'B2' | 'C1' | 'C2';

export interface CefrTurn {
  turn_number: number;
  part: 'part2' | 'part3';
  scores: Partial<Record<'range' | 'accuracy' | 'fluency' | 'coherence' | 'interaction', number>>;
  turn_band: number;
  turn_band_name: CefrBandName;
  target_band: number | null;
  target_band_name: CefrBandName | null;
  next_target_band: number;
  next_target_band_name: CefrBandName;
  proven_band: number | null;
  direction: 'up' | 'same' | 'down';
  repeat_request: boolean;
  degraded: boolean;
  follow_up: string;
  criterion_averages: Partial<Record<string, number>>;
  overall_score: number;
  overall_band: CefrBandName;
  final: boolean;
  // Evidence the turn was graded with. Null when the student used text mode,
  // which is itself worth showing — it means Fluency was graded blind.
  speech?: SpeechEvidenceStored | null;
  words?: TimedWordPayload[] | null;
}

// As stored on the turn: the same fields the client sent, all optional.
export type SpeechEvidenceStored = SpeechEvidence;

export interface AnswerResponse {
  feedback: string;
  probe: string;
  adequate: boolean;
  answered: boolean;
  eds_delta: number;
  eds_question?: number;
  eds_components?: EDSComponents;
  cefr?: CefrTurn; // present only for the communication (CEFR speaking) exam
}

// Human per-dimension band overrides for the CEFR speaking exam (calibration).
export interface CefrOverride {
  turn_number: number;
  dimension: string;
  machine_band: string | null;
  human_band: string;
}

export async function getCefrCalibration(sessionId: string): Promise<CefrOverride[]> {
  const r = await get<{ overrides: CefrOverride[] }>(`/api/cefr/calibration?session_id=${encodeURIComponent(sessionId)}`);
  return r.overrides || [];
}

export async function saveCefrCalibration(body: {
  session_id: string; turn_number: number; dimension: string; human_band: string; machine_band?: string | null;
}): Promise<void> {
  await post('/api/cefr/calibration', body);
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

// Acoustic measurements the CEFR speaking exam sends alongside the transcript,
// so Fluency can be graded on real pausing and tempo. Flat and snake_cased to
// match the backend's SPEECH_FIELDS; omitted entirely in text mode.
export interface SpeechEvidence {
  pron_overall?: number | null;
  pron_accuracy?: number | null;
  pron_fluency?: number | null;
  pron_completeness?: number | null;
  prosody?: number | null;
  word_count?: number | null;
  duration_sec?: number | null;
  speech_rate_wpm?: number | null;
  articulation_rate_wpm?: number | null;
  pause_count?: number | null;
  long_pause_count?: number | null;
  mean_pause_sec?: number | null;
  pause_ratio?: number | null;
}

// One word and the silent gap (seconds) immediately before it. Sent so the
// professor sees where the hesitation fell, not just how much there was.
export interface TimedWordPayload { w: string; g: number }

export async function submitAnswer(
  sessionId: string,
  questionIndex: number,
  answerText: string,
  speech?: SpeechEvidence,
  words?: TimedWordPayload[],
): Promise<AnswerResponse> {
  return post<AnswerResponse>(`/api/sessions/${sessionId}/answer`, {
    question_index: questionIndex,
    answer_text: answerText,
    ...(speech ? { speech } : {}),
    ...(words && words.length ? { words } : {}),
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
  // null until the professor releases the grade on a graded item — the draft EDS
  // is never shown to the student as a mark. Practice tests are always released.
  score: number | null;
  grade_released?: boolean;
  total_questions: number;
  questions_answered: number;
  feedback: string;
  components?: EDSComponents | null;
  // Present for CEFR speaking exams; the UI shows this band instead of the EDS score.
  cefr?: { overall_band: string | null; overall_score: number | null; criterion_averages: Record<string, number> | null } | null;
  question_results: {
    question_id: string;
    question_text: string;
    answer: string;
    score?: number;
    band_name?: string | null; // per-turn CEFR band for speaking exams
    feedback?: string;
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
