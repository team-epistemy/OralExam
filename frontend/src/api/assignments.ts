import { get, post } from './client';

export interface Assignment {
  id: string;
  course_id: string;
  title: string;
  question_ids: string[];
  available_from: string;
  available_until: string;
  adaptive: boolean;
  status: 'draft' | 'active' | 'closed';
  created_at: string;
}

export interface AssignmentSession {
  id: string;
  assignment_id: string;
  student_id: string;
  student_name: string;
  status: 'in_progress' | 'completed' | 'abandoned';
  score?: number;
  started_at: string;
  completed_at?: string;
}

export interface CreateAssignmentPayload {
  course_id: string;
  title: string;
  question_ids: string[];
  available_from: string;
  available_until: string;
  adaptive: boolean;
}

export async function createAssignment(payload: CreateAssignmentPayload): Promise<Assignment> {
  return post(`/api/courses/${payload.course_id}/assignments`, payload);
}

export async function listAssignments(courseId: string): Promise<Assignment[]> {
  return get(`/api/courses/${courseId}/assignments`);
}

export interface CommunicationAssignment extends Assignment {
  assignment_id: string;
  question_text: string;
  card_id: string;
  theme: string;
  config: Record<string, unknown>;
}

export interface SpeakingCard {
  card_id: string;
  theme: string;
  question: string;
  high_band_frame: string;
}

// The Part 2 card bank lives server-side so the prompts and their themes have
// one source of truth — the theme is what constrains the Part 3 follow-ups.
export async function listCommunicationCards(): Promise<{
  default_card_id: string;
  cards: SpeakingCard[];
}> {
  return get('/api/communication/cards');
}

// Creates a CEFR speaking exam. Pass a cardId to use a bank prompt, or
// questionText to use the professor's own (which wins if both are given). The
// five-turn structure and the adaptive ladder are fixed either way.
export async function createCommunicationAssignment(
  courseId: string,
  title: string,
  opts: { durationMinutes?: number; cardId?: string; questionText?: string } = {},
): Promise<CommunicationAssignment> {
  return post(`/api/courses/${courseId}/communication-assignments`, {
    title,
    duration_minutes: opts.durationMinutes,
    card_id: opts.cardId,
    question_text: opts.questionText,
  });
}

export async function getAssignment(assignmentId: string): Promise<Assignment> {
  return get(`/api/assignments/${assignmentId}`);
}

export async function listStudentAssignments(): Promise<Assignment[]> {
  return get('/api/student/assignments');
}

export async function monitorAssignment(assignmentId: string): Promise<AssignmentSession[]> {
  return get(`/api/assignments/${assignmentId}/sessions`);
}
