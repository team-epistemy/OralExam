import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Users, ChevronDown, ChevronRight, BookOpen, GraduationCap, Loader2,
  AlertTriangle, UserCog, RefreshCw,
} from 'lucide-react';
import { listProfessors, type DirCourse, type DirProfessor } from '../../api/adminProfessors';

function StatusChip({ status }: { status: string }) {
  const tone = status === 'active' ? 'bg-green-50 text-green-700'
    : status === 'invited' ? 'bg-amber-50 text-amber-700'
    : 'bg-gray-100 text-gray-500';
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${tone}`}>{status}</span>;
}

function CourseRow({ c }: { c: DirCourse }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-gray-100 first:border-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 py-1.5 px-2 text-sm text-left hover:bg-gray-50 rounded"
      >
        {c.students.length > 0
          ? (open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400" />)
          : <span className="w-3.5" />}
        <BookOpen className="w-3.5 h-3.5 text-navy/60 shrink-0" />
        <span className="flex-1 min-w-0 truncate text-gray-800">{c.course_name}</span>
        <span className="text-xs text-gray-500 inline-flex items-center gap-1">
          <GraduationCap className="w-3.5 h-3.5" /> {c.student_count}
        </span>
        {c.mismatch && (
          <span className="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5 inline-flex items-center gap-1"
                title={`Authoritative enrollment (${c.student_count}) differs from the professor-facing roster mirror (${c.roster_count}).`}>
            <AlertTriangle className="w-3 h-3" /> roster {c.roster_count}
          </span>
        )}
      </button>
      {open && c.students.length > 0 && (
        <ul className="pl-9 pr-2 pb-2 space-y-0.5">
          {c.students.map((s) => (
            <li key={s} className="text-xs text-gray-600 truncate">{s}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProfessorCard({ p }: { p: DirProfessor }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-white border border-gray-200 rounded-lg">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 rounded-lg"
      >
        {open ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
        <UserCog className="w-4 h-4 text-navy shrink-0" />
        <span className="flex-1 min-w-0 truncate font-medium text-gray-900">{p.email}</span>
        <StatusChip status={p.status} />
        <span className="text-xs text-gray-500 tabular-nums">{p.course_count} course{p.course_count === 1 ? '' : 's'}</span>
        <span className="text-xs text-gray-500 tabular-nums">{p.student_total} student{p.student_total === 1 ? '' : 's'}</span>
      </button>
      {open && (
        <div className="px-4 pb-3">
          {p.courses.length === 0
            ? <p className="text-xs text-gray-400 pl-2">No courses owned by this professor.</p>
            : p.courses.map((c) => <CourseRow key={c.course_id} c={c} />)}
        </div>
      )}
    </div>
  );
}

export default function AdminDirectory() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['admin-professors'],
    queryFn: listProfessors,
  });

  const professors = data?.professors ?? [];
  const unassigned = data?.unassigned_courses ?? [];
  const totalCourses = professors.reduce((a, p) => a + p.course_count, 0) + unassigned.length;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <Users className="w-5 h-5 text-gold" />
        </div>
        <div className="flex-1">
          <h1 className="font-heading text-2xl text-navy">Directory</h1>
          <p className="text-sm text-muted">
            Every professor in your org mapped to their courses and enrolled students — for debugging and analysis.
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {/* Stats */}
      {data && (
        <div className="grid grid-cols-3 gap-3">
          {[['Professors', professors.length], ['Courses', totalCourses], ['Students', data.org_student_total]].map(([k, v]) => (
            <div key={k} className="bg-white border border-gray-200 rounded-lg p-3">
              <div className="text-xs text-gray-500">{k}</div>
              <div className="text-2xl font-bold text-gray-900">{v}</div>
            </div>
          ))}
        </div>
      )}

      {isLoading && (
        <div className="text-sm text-gray-400 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>
      )}
      {isError && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">Failed to load the directory.</div>
      )}

      {/* Professors */}
      <div className="space-y-2">
        {professors.map((p) => <ProfessorCard key={p.email} p={p} />)}
        {data && professors.length === 0 && (
          <p className="text-sm text-gray-400">No professors in this org yet.</p>
        )}
      </div>

      {/* Orphan / unassigned courses — a debugging signal */}
      {unassigned.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-amber-700 flex items-center gap-1.5 mb-2">
            <AlertTriangle className="w-4 h-4" /> Unassigned courses ({unassigned.length})
          </h2>
          <p className="text-xs text-gray-500 mb-2">
            Courses with no owning professor (created_by is empty or doesn't match a professor account) — often a sign of a removed/re-created course.
          </p>
          <div className="bg-white border border-amber-200 rounded-lg px-2 py-1">
            {unassigned.map((c) => <CourseRow key={c.course_id} c={c} />)}
          </div>
        </div>
      )}
    </div>
  );
}
