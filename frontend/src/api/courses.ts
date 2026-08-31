import { get, post, del } from './client';

export interface CourseRef {
  course_id: string;
  course_name: string;
}

export async function createCourse(name: string): Promise<CourseRef> {
  return post<CourseRef>('/api/professor/courses', { name });
}

export async function deleteCourse(courseId: string): Promise<unknown> {
  return del(`/api/courses/${courseId}`);
}

export interface Student {
  email: string;
  enrolled_at?: string;
}

export async function listStudents(courseId: string): Promise<Student[]> {
  const r = await get<{ students: Student[] }>(`/api/courses/${courseId}/students`);
  return r.students || [];
}

export interface EnrollResult {
  added: number;
  skipped: number;
  count: number;
  students: Student[];
}

export async function enrollStudents(courseId: string, emails: string[]): Promise<EnrollResult> {
  return post<EnrollResult>(`/api/courses/${courseId}/students`, { emails });
}

export async function unenrollStudent(courseId: string, email: string): Promise<unknown> {
  return del(`/api/courses/${courseId}/students?email=${encodeURIComponent(email)}`);
}

export interface Syllabus {
  material_id: string | null;
  version_id: string | null;
  file_name: string | null;
}

export async function getSyllabus(courseId: string): Promise<Syllabus | null> {
  const r = await get<{ syllabus: Syllabus | null }>(`/api/courses/${courseId}/syllabus`);
  return r.syllabus;
}

export async function setSyllabus(
  courseId: string,
  mat: { material_id?: string; material_version_id?: string; file_name?: string },
): Promise<unknown> {
  return post(`/api/courses/${courseId}/syllabus`, mat);
}

export interface ProcessedSession {
  session_id: string;
  week: string;
  title: string;
  session_date: string | null;
  in_scope_concepts: string[];
}

export interface ProcessSyllabusResult {
  status: 'created' | 'exists';
  created: number;
  sessions?: ProcessedSession[];
  message?: string;
}

// Parse the course syllabus into class sessions + in-scope topics. Pass `text`
// to parse pasted schedule text; omit to use the stored syllabus's own text.
export async function processSyllabus(courseId: string, text?: string): Promise<ProcessSyllabusResult> {
  return post(`/api/courses/${courseId}/syllabus/process`, { text: text || undefined });
}

/** Parse a pasted/loaded list of emails (CSV, newline, or space separated). */
export function parseEmails(raw: string): string[] {
  const found = (raw.match(/[^\s,;]+@[^\s,;]+/g) || []).map((e) => e.trim().toLowerCase());
  return Array.from(new Set(found));
}
