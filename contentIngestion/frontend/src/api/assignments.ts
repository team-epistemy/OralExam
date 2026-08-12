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

export async function getAssignment(assignmentId: string): Promise<Assignment> {
  return get(`/api/assignments/${assignmentId}`);
}

export async function listStudentAssignments(): Promise<Assignment[]> {
  return get('/api/student/assignments');
}

export async function monitorAssignment(assignmentId: string): Promise<AssignmentSession[]> {
  return get(`/api/assignments/${assignmentId}/sessions`);
}
