import { get } from './client';

// Admin directory: every professor in the org → their courses → enrolled students.
// Read-only, for debugging/analysis. Surfaces roster mismatches and orphan courses.

export interface DirCourse {
  course_id: string;
  course_name: string;
  created_by?: string | null;
  created_at: string | null;
  student_count: number;   // authoritative (auth.enrollment)
  roster_count: number;    // public.enrollment mirror the professor UI reads
  mismatch: boolean;       // student_count !== roster_count
  students: string[];      // enrolled student emails
}

export interface DirProfessor {
  email: string;
  status: string;          // active | invited | disabled
  created_at: string | null;
  course_count: number;
  student_total: number;
  courses: DirCourse[];
}

export interface ProfessorsResponse {
  professors: DirProfessor[];
  unassigned_courses: DirCourse[];
  org_student_total: number;
}

export function listProfessors(): Promise<ProfessorsResponse> {
  return get('/api/admin/professors');
}
