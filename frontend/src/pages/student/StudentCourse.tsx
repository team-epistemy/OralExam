import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { PlayCircle, ChevronLeft, Dumbbell, ClipboardList, GraduationCap } from 'lucide-react';
import { get } from '../../api/client';
import type { StudentAssignment } from './Dashboard';

interface StudentDashboardData {
  courses: { course_id: string; course_name: string }[];
  assignments: StudentAssignment[];
}

const SECTIONS = [
  { type: 'practice', label: 'Practice Tests', desc: 'Ungraded — take them as many times as you like.', icon: Dumbbell },
  { type: 'assignment', label: 'Assignments', desc: 'Graded coursework.', icon: ClipboardList },
  { type: 'exam', label: 'Exams', desc: 'Formal assessments.', icon: GraduationCap },
] as const;

export default function StudentCourse() {
  const { courseId } = useParams<{ courseId: string }>();
  const { data, isLoading } = useQuery({
    queryKey: ['student-dashboard'],
    queryFn: () => get<StudentDashboardData>('/api/student/dashboard'),
    retry: false,
  });

  const all = (data?.assignments || []).filter((a) => a.course_id === courseId);
  const courseName =
    all[0]?.course_name ||
    data?.courses?.find((c) => c.course_id === courseId)?.course_name ||
    'Course';

  if (isLoading) {
    return (
      <div className="animate-pulse space-y-4">
        <div className="h-8 w-56 bg-gray-200 rounded" />
        <div className="h-40 bg-gray-200 rounded-xl" />
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-4xl">
      <div>
        <Link to="/student/dashboard" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
          <ChevronLeft className="w-4 h-4" /> All courses
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 mt-2">{courseName}</h1>
      </div>

      {SECTIONS.map(({ type, label, desc, icon: Icon }) => {
        const items = all.filter((a) => (a.assignment_type || 'assignment') === type);
        return (
          <section key={type} className="space-y-3">
            <div className="flex items-center gap-2">
              <Icon className="w-5 h-5 text-blue-600" />
              <h2 className="text-lg font-semibold text-gray-900">{label}</h2>
              <span className="text-xs text-gray-400">{items.length}</span>
            </div>
            {items.length === 0 ? (
              <p className="text-sm text-gray-400">{desc} Nothing here yet.</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {items.map((a) => (
                  <div key={a.id} className="bg-white rounded-xl border-2 border-gray-200 p-5 hover:border-blue-300 transition-colors">
                    <p className="font-semibold text-gray-900">{a.title}</p>
                    <div className="flex gap-3 mt-1 text-xs text-gray-400">
                      {a.config?.duration_minutes && <span>{a.config.duration_minutes} min</span>}
                      {a.config?.difficulty && <span className="capitalize">{a.config.difficulty}</span>}
                      {a.questions_count && <span>{a.questions_count} questions</span>}
                    </div>
                    <div className="mt-4 flex gap-2">
                      <Link
                        to={`/student/exam/${a.id}`}
                        className="flex items-center justify-center gap-2 flex-1 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
                      >
                        <PlayCircle className="w-4 h-4" />
                        {type === 'practice' ? 'Start Practice' : type === 'exam' ? 'Start Exam' : 'Start'}
                      </Link>
                      <Link
                        to={`/student/results/${a.id}`}
                        className="flex items-center justify-center px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
                      >
                        Results
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
