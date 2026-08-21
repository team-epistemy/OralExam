import { post } from './client';

export interface Student {
  id: string;
  email: string;
  role: string;
  password: string; // temp password, returned once to share with the student
}

// professor/admin provisions a student (Cognito user + enrollment in the course).
// Omitting password lets the server mint a temp one, returned so it can be shared.
export function createStudent(email: string, courseId?: string, password?: string): Promise<Student> {
  return post<Student>('/api/auth/students', {
    email,
    course_id: courseId || null,
    password: password || null,
  });
}

export interface BatchStudentResult {
  email: string;
  status: 'created' | 'exists' | 'skipped' | 'failed';
  password?: string | null;
  error?: string;
}

export interface BatchResult {
  results: BatchStudentResult[];
  count: number;
  created: number;
  existing: number;
  skipped: number;
  failed: number;
}

// Provision many students in one request. Resilient per row; new students'
// temp passwords are returned once. Chunk large rosters at the call site.
export function createStudentsBatch(emails: string[], courseId?: string): Promise<BatchResult> {
  return post<BatchResult>('/api/auth/students/batch', {
    emails,
    course_id: courseId || null,
  });
}
