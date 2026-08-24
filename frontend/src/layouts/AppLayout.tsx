import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { logout } from '../api/auth';
import {
  GraduationCap,
  LayoutDashboard,
  Upload,
  ScrollText,
  Users,
  CalendarPlus,
  FileText,
  ListChecks,
  BookOpen,
  Plus,
  Trash2,
  ChevronLeft,
  UserPlus,
  LogOut,
  Menu,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get } from '../api/client';
import { deleteCourse, type CourseRef } from '../api/courses';
import AddCourseModal from '../components/AddCourseModal';

interface User {
  email: string;
  role: 'professor' | 'student' | 'platform_admin';
  name?: string;
}

const ROLE_LABELS: Record<string, string> = {
  professor: 'Professor',
  student: 'Student',
  platform_admin: 'Admin',
};

function getUser(): User | null {
  const raw = localStorage.getItem('user');
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

const navItemClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
    isActive ? 'bg-parchment-dark text-navy' : 'text-ink-light hover:bg-parchment hover:text-ink'
  }`;

const staticItemClass = 'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-ink-light hover:bg-parchment hover:text-ink transition-colors w-full text-left';

const adminLinks = [
  { to: '/admin/professors', label: 'Add Professor', icon: UserPlus },
];

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showAddCourse, setShowAddCourse] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const user = getUser();
  const isProfessor = user?.role === 'professor';
  const isAdmin = user?.role === 'platform_admin';
  // Students have a single view (their dashboard) — no left nav; brand + sign-out
  // move into the header so the student screen is uncluttered.
  const hideSidebar = user?.role === 'student';

  const { data: courses = [] } = useQuery({
    queryKey: ['professor-courses'],
    queryFn: () => get<CourseRef[]>('/api/professor/courses'),
    enabled: isProfessor,
  });

  // Course-scoped sub-pages (Create Assignment, Upload, Build Exam) pass the
  // course via ?courseId=/?course= — honour that so they keep the course's left
  // nav instead of dropping to the global professor nav.
  const courseMatch = location.pathname.match(/\/professor\/courses\/([^/]+)/);
  const qs = new URLSearchParams(location.search);
  const queryCourse = qs.get('courseId') || qs.get('course') || '';
  const activeCourseId = courseMatch?.[1] || courses.find((c) => c.course_id === queryCourse)?.course_id;
  const activeCourse = courses.find((c) => c.course_id === activeCourseId);

  const handleLogout = () => { logout(); };

  const handleRemoveCourse = async () => {
    if (!activeCourse) return;
    if (!confirm(`Remove course "${activeCourse.course_name}"? This deletes its materials, questions, exams, and results. This cannot be undone.`)) return;
    await deleteCourse(activeCourse.course_id);
    queryClient.invalidateQueries({ queryKey: ['professor-courses'] });
    navigate('/professor/dashboard');
  };

  const close = () => setSidebarOpen(false);

  const renderNav = () => {
    // Platform admin: provisioning-only navigation
    if (isAdmin) {
      return adminLinks.map((l) => (
        <NavLink key={l.to} to={l.to} onClick={close} className={navItemClass}>
          <l.icon className="w-4 h-4" /> {l.label}
        </NavLink>
      ));
    }

    if (!isProfessor) {
      return (
        <NavLink to="/student/dashboard" onClick={close} className={navItemClass}>
          <LayoutDashboard className="w-4 h-4" /> Dashboard
        </NavLink>
      );
    }

    // Course-scoped operations (after a course is selected)
    if (activeCourse) {
      const cid = activeCourse.course_id;
      const cname = encodeURIComponent(activeCourse.course_name);
      return (
        <>
          <NavLink to="/professor/dashboard" onClick={close} className={staticItemClass}>
            <ChevronLeft className="w-4 h-4" /> All courses
          </NavLink>
          <div className="px-3 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted truncate">{activeCourse.course_name}</div>
          <NavLink to={`/professor/courses/${cid}`} end onClick={close} className={navItemClass}>
            <BookOpen className="w-4 h-4" /> Overview
          </NavLink>
          <NavLink to={`/professor/upload?course=${cname}`} onClick={close} className={staticItemClass}>
            <Upload className="w-4 h-4" /> Add Material
          </NavLink>
          <NavLink to={`/professor/upload?course=${cname}&syllabus=1&courseId=${cid}`} onClick={close} className={staticItemClass}>
            <ScrollText className="w-4 h-4" /> Upload Syllabus
          </NavLink>
          <NavLink to={`/professor/courses/${cid}?tab=students`} onClick={close} className={staticItemClass}>
            <Users className="w-4 h-4" /> Add Students
          </NavLink>
          <button
            onClick={() => { close(); alert('Class sessions are coming soon.'); }}
            className={`${staticItemClass} justify-between`}
          >
            <span className="flex items-center gap-3"><CalendarPlus className="w-4 h-4" /> Add Session</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-parchment-dark text-muted">soon</span>
          </button>
          <NavLink to={`/professor/assignments/new?course=${cid}`} onClick={close} className={staticItemClass}>
            <FileText className="w-4 h-4" /> Create Assignment
          </NavLink>
          <div className="pt-2 mt-2 border-t border-border">
            <button onClick={handleRemoveCourse} className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-red-600 hover:bg-red-50 transition-colors w-full text-left">
              <Trash2 className="w-4 h-4" /> Remove Course
            </button>
          </div>
        </>
      );
    }

    // Global professor navigation
    return (
      <>
        <NavLink to="/professor/dashboard" onClick={close} className={navItemClass}>
          <LayoutDashboard className="w-4 h-4" /> Dashboard
        </NavLink>
        {courses.length > 0 && (
          <NavLink to="/professor/assignments" end onClick={close} className={navItemClass}>
            <ListChecks className="w-4 h-4" /> Active Assignments
          </NavLink>
        )}
        <button onClick={() => { close(); setShowAddCourse(true); }} className={staticItemClass}>
          <Plus className="w-4 h-4" /> Add Course
        </button>
        <div className="px-3 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Courses</div>
        {courses.length === 0 ? (
          <p className="px-3 py-1 text-xs text-muted">No courses yet.</p>
        ) : (
          courses.map((c) => (
            <NavLink key={c.course_id} to={`/professor/courses/${c.course_id}`} onClick={close} className={navItemClass}>
              <BookOpen className="w-4 h-4 flex-shrink-0" /> <span className="truncate">{c.course_name}</span>
            </NavLink>
          ))
        )}
      </>
    );
  };

  return (
    <div className="min-h-screen bg-parchment flex">
      {sidebarOpen && !hideSidebar && <div className="fixed inset-0 bg-black/30 z-40 lg:hidden" onClick={close} />}

      {!hideSidebar && (
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-border transform transition-transform lg:translate-x-0 lg:static lg:z-auto flex flex-col
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <div className="flex items-center gap-3 px-6 py-5 border-b border-border">
          <div className="w-8 h-8 bg-navy rounded-lg flex items-center justify-center">
            <GraduationCap className="w-5 h-5 text-gold" />
          </div>
          <span className="font-heading text-lg text-navy"><span className="text-gold">E</span>pistemy</span>
          <button className="ml-auto lg:hidden" onClick={close}><X className="w-5 h-5 text-muted" /></button>
        </div>

        <nav className="p-4 space-y-1 overflow-y-auto flex-1">{renderNav()}</nav>

        <div className="p-4 border-t border-border">
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="w-8 h-8 rounded-full bg-parchment-dark flex items-center justify-center">
              <span className="text-xs font-medium text-ink-light">{user?.email?.charAt(0).toUpperCase() || '?'}</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-ink truncate">{user?.email || 'User'}</p>
              <p className="text-xs text-muted capitalize">{user?.role || 'unknown'}</p>
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-ink-light border border-border hover:bg-parchment hover:text-ink transition-colors"
          >
            <LogOut className="w-4 h-4" />
            Sign out
          </button>
        </div>
      </aside>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <header className="sticky top-0 z-30 bg-navy border-b-2 border-gold px-4 lg:px-8 py-3 flex items-center gap-4">
          {hideSidebar ? (
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 bg-gold/20 rounded-lg flex items-center justify-center">
                <GraduationCap className="w-4 h-4 text-gold" />
              </div>
              <span className="font-heading text-base text-parchment"><span className="text-gold">E</span>pistemy</span>
            </div>
          ) : (
            <button className="lg:hidden p-1" onClick={() => setSidebarOpen(true)}><Menu className="w-5 h-5 text-parchment" /></button>
          )}
          <div className="flex-1" />
          <div className="flex items-center gap-3">
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
              user?.role === 'student' ? 'bg-success-bg text-success' : 'bg-gold/20 text-gold-light'
            }`}>
              {ROLE_LABELS[user?.role || ''] || 'User'}
            </span>
            {hideSidebar && (
              <button
                onClick={handleLogout}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-parchment border border-parchment/30 hover:bg-parchment/10 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" /> Sign out
              </button>
            )}
          </div>
        </header>

        <main className="flex-1 p-4 lg:p-8"><Outlet /></main>
      </div>

      {showAddCourse && <AddCourseModal onClose={() => setShowAddCourse(false)} onCreated={(id) => { setShowAddCourse(false); queryClient.invalidateQueries({ queryKey: ['professor-courses'] }); navigate(`/professor/courses/${id}`); }} />}
    </div>
  );
}

