import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BookOpen, PlayCircle } from 'lucide-react';
import { get } from '../../api/client';

interface Course {
  course_id: string;
  course_name: string;
}

interface StudentAssignment {
  id: string;
  title: string;
  course_name: string;
  status: string;
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

  const courses = data?.courses || [];
  const assignments = data?.assignments || [];

  if (isLoading) {
    return (
      <div className="animate-pulse space-y-6">
        <div className="h-8 w-48 bg-gray-200 rounded" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2].map((i) => (
            <div key={i} className="h-40 bg-gray-200 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Student Dashboard</h1>
        <p className="text-gray-500 text-sm mt-1">Your courses and exams</p>
      </div>

      {/* Active Assignments */}
      {assignments.length > 0 ? (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
            Active Exams
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {assignments.map((assignment) => (
              <div
                key={assignment.id}
                className="bg-white rounded-xl border-2 border-blue-200 p-5 hover:border-blue-400 transition-colors"
              >
                <div className="flex flex-col h-full">
                  <div className="flex-1">
                    <p className="font-semibold text-gray-900 text-base">{assignment.title}</p>
                    <p className="text-sm text-gray-500 mt-1">{assignment.course_name}</p>
                    <div className="flex gap-3 mt-2 text-xs text-gray-400">
                      {assignment.config?.duration_minutes && (
                        <span>{assignment.config.duration_minutes} min</span>
                      )}
                      {assignment.config?.difficulty && (
                        <span className="capitalize">{assignment.config.difficulty}</span>
                      )}
                      {assignment.questions_count && (
                        <span>{assignment.questions_count} questions</span>
                      )}
                    </div>
                  </div>
                  <div className="mt-4 flex gap-2">
                    <Link
                      to={`/student/exam/${assignment.id}`}
                      className="flex items-center justify-center gap-2 flex-1 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
                    >
                      <PlayCircle className="w-4 h-4" />
                      Start Exam
                    </Link>
                    <Link
                      to={`/student/results/${assignment.id}`}
                      className="flex items-center justify-center px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
                    >
                      Results
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <div className="w-16 h-16 bg-blue-50 rounded-full flex items-center justify-center mx-auto mb-4">
            <BookOpen className="w-8 h-8 text-blue-400" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">No exams assigned yet</h3>
          <p className="text-sm text-gray-500 max-w-sm mx-auto">
            Your professor hasn't assigned any exams yet. Check back later.
          </p>
        </div>
      )}

      {/* Enrolled Courses */}
      {courses.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200">
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900 flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-gray-400" />
              Courses
            </h2>
          </div>
          <div className="divide-y divide-gray-100">
            {courses.map((course) => (
              <div key={course.course_id} className="flex items-center gap-4 px-5 py-3">
                <div className="w-8 h-8 bg-blue-50 rounded flex items-center justify-center">
                  <BookOpen className="w-4 h-4 text-blue-600" />
                </div>
                <p className="text-sm font-medium text-gray-900">{course.course_name}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
