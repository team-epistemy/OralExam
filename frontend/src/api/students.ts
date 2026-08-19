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
