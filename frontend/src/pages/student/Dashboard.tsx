import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BookOpen, ChevronRight } from 'lucide-react';
import { get } from '../../api/client';

interface Course {
  course_id: string;
  course_name: string;
}

export interface StudentAssignment {
  id: string;
  title: string;
  course_id: string;
  course_name: string;
  status: string;
  assignment_type: 'practice' | 'assignment' | 'exam';
  config: { difficulty?: string; duration_minutes?: number; max_questions?: number };
  questions_count?: number;
  created_at: string;
}

interface StudentDashboardData {
  courses: Course[];
  assignments: StudentAssignment[];
}

export default function StudentDashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['student-dashboard'],
    queryFn: () => get<StudentDashboardData>('/api/student/dashboard'),
    retry: false,
  });

  const assignments = data?.assignments || [];

  if (isLoading) {
    return (
      <div className="animate-pulse space-y-6">
        <div className="h-8 w-48 bg-gray-200 rounded" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2].map((i) => (
            <div key={i} className="h-24 bg-gray-200 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  // The student's courses are those with items visible to them, keyed by course.
  const byCourse = new Map<string, { course_id: string; course_name: string; count: number }>();
  for (const a of assignments) {
    const key = a.course_id || a.course_name;
    const e = byCourse.get(key) || { course_id: a.course_id, course_name: a.course_name, count: 0 };
    e.count += 1;
    byCourse.set(key, e);
  }
  const courses = [...byCourse.values()];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Your Courses</h1>
        <p className="text-gray-500 text-sm mt-1">Open a course to see its practice tests, assignments, and exams.</p>
      </div>

      {courses.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {courses.map((c) => (
            <Link
              key={c.course_id}
              to={`/student/courses/${c.course_id}`}
              className="bg-white rounded-xl border-2 border-gray-200 p-5 hover:border-blue-400 transition-colors flex items-center gap-4"
            >
              <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center flex-shrink-0">
                <BookOpen className="w-5 h-5 text-blue-600" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-gray-900 truncate">{c.course_name}</p>
                <p className="text-sm text-gray-500">{c.count} active item{c.count !== 1 ? 's' : ''}</p>
              </div>
              <ChevronRight className="w-5 h-5 text-gray-300 flex-shrink-0" />
            </Link>
          ))}
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <div className="w-16 h-16 bg-blue-50 rounded-full flex items-center justify-center mx-auto mb-4">
            <BookOpen className="w-8 h-8 text-blue-400" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">Nothing assigned yet</h3>
          <p className="text-sm text-gray-500 max-w-sm mx-auto">
            Your professor hasn't assigned anything yet. Check back later.
          </p>
        </div>
      )}
    </div>
  );
}
