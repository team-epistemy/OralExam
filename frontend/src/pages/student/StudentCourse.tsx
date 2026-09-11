import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { PlayCircle, ChevronLeft, Dumbbell, ClipboardList, GraduationCap, Check, Clock, Award } from 'lucide-react';
import { get } from '../../api/client';
import type { StudentAssignment } from './Dashboard';
import { typeNoun } from '../../assignmentType';

interface StudentDashboardData {
  courses: { course_id: string; course_name: string }[];
  assignments: StudentAssignment[];
}

// One chip per card covering the student's attempt: untaken shows nothing, a
// practice test ends at Completed (it is never graded), and a graded item moves
// Completed -> Awaiting grade -> Graded. The score stays behind Results.
export function attemptChip(a: StudentAssignment, type: string) {
  if (!a.completed) return null;
  if (type === 'practice') return { text: 'Completed', icon: Check, cls: 'text-green-700 bg-green-50 border-green-200' };
  if (a.grade_status === 'released') return { text: 'Graded', icon: Award, cls: 'text-blue-700 bg-blue-50 border-blue-200' };
  return { text: 'Awaiting grade', icon: Clock, cls: 'text-amber-700 bg-amber-50 border-amber-200' };
}

// Minutes if the item is timed, else null. The difficulty word ('Balanced') is
// deliberately not surfaced to students — a timed item shows its limit instead.
export function timeLimit(a: StudentAssignment): number | null {
  const m = a.config?.time_limit_minutes ?? a.config?.duration_minutes;
  return m && m > 0 ? m : null;
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
                {items.map((a) => {
                  // Verb + the item's own type, never 'Exam' for everything.
                  const startLabel = `${a.completed && type === 'practice' ? 'Retake' : 'Start'} ${typeNoun(type)}`;
                  const chip = attemptChip(a, type);
                  return (
                    <div key={a.id} className="bg-white rounded-xl border-2 border-gray-200 p-5 hover:border-blue-300 transition-colors">
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-semibold text-gray-900">{a.title}</p>
                        {chip && (
                          <span className={`flex-shrink-0 inline-flex items-center gap-1 text-[11px] font-medium border rounded-full px-2 py-0.5 ${chip.cls}`}>
                            <chip.icon className="w-3 h-3" /> {chip.text}
                          </span>
                        )}
                      </div>
                      <div className="flex gap-3 mt-1 text-xs text-gray-400">
                        {timeLimit(a) && <span>{timeLimit(a)} min</span>}
                        {!!a.questions_count && <span>{a.questions_count} questions</span>}
                      </div>
                      <div className="mt-4 flex gap-2">
                        <Link
                          to={`/student/exam/${a.id}`}
                          className="flex items-center justify-center gap-2 flex-1 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
                        >
                          <PlayCircle className="w-4 h-4" />
                          {startLabel}
                        </Link>
                        <Link
                          to={`/student/results/${a.id}`}
                          className="flex items-center justify-center px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
                        >
                          Results
                        </Link>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
