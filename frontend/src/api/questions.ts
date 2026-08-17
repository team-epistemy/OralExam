import { get, post, put } from './client';

export interface Question {
  question_id: string;
  course_id: string;
  text: string;
  concept_ids: string[];
  difficulty: string;
  status: 'draft' | 'approved' | 'rejected';
  points: number;
  question_type: string;
  created_by: string;
  created_at: string;
  // Legacy aliases
  id?: string;
  topic?: string;
  question?: string;
}

export async function listQuestions(courseId: string, status?: string): Promise<Question[]> {
  const params = status ? `?status=${status}` : '';
  const resp: any = await get(`/api/courses/${courseId}/questions${params}`);
  return resp?.questions || resp || [];
}

export async function approveQuestion(questionId: string): Promise<any> {
  return post(`/api/questions/${questionId}/approve`);
}

export async function rejectQuestion(questionId: string): Promise<any> {
  return post(`/api/questions/${questionId}/reject`);
}

export async function updateQuestion(questionId: string, data: { text?: string; points?: number }): Promise<any> {
  return put(`/api/questions/${questionId}`, data);
}

export async function generateQuestions(courseId: string, materialId: string): Promise<{ task_id: string }> {
  return post(`/api/courses/${courseId}/questions/generate`, { material_id: materialId });
}
