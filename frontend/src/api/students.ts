import { get, post, del } from './client';

export interface Student {
  id: string;
  email: string;
  role: string;
  password: string; // temp password, returned once to share with the student
}

export interface EnrolledStudent {
  id: string;
  email: string;
  status: string;
  enrolled_at: string | null;
}

// Roster of students enrolled in a course (owning professor only).
export function listCourseStudents(courseId: string): Promise<EnrolledStudent[]> {
  return get<EnrolledStudent[]>(`/api/courses/${courseId}/students`);
}

// Drop (unenroll) a student from a course by their app_user id.
export function dropCourseStudent(courseId: string, studentId: string): Promise<{ dropped: number }> {
  return del<{ dropped: number }>(`/api/courses/${courseId}/students/${studentId}`);
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
