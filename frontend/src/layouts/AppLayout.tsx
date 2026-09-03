import { Outlet, Link, useLocation } from 'react-router-dom';
import { logout } from '../api/auth';
import { GraduationCap, LogOut } from 'lucide-react';

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

function homePath(role?: string): string {
  if (role === 'platform_admin') return '/admin/professors';
  if (role === 'student') return '/student/dashboard';
  return '/professor/dashboard';
}

// Admin header nav — the platform_admin surfaces (Professors, Agent Simulations)
// aren't reachable from a course/dashboard, so they get inline header links.
function AdminNav() {
  const { pathname } = useLocation();
  const items = [
    { to: '/admin/professors', label: 'Professors' },
    { to: '/admin/simulations', label: 'Simulations' },
  ];
  return (
    <nav className="flex items-center gap-1 ml-2">
      {items.map((it) => (
        <Link
          key={it.to}
          to={it.to}
          className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
            pathname === it.to ? 'bg-gold/20 text-gold-light' : 'text-parchment/70 hover:text-parchment hover:bg-parchment/10'
          }`}
        >
          {it.label}
        </Link>
      ))}
    </nav>
  );
}

// No left navigation: a single top header carries the brand (→ dashboard) and
// sign-out. Every action reaches its screen from the dashboard (global actions)
// or the course page (course-scoped actions).
export default function AppLayout() {
  const user = getUser();
  const handleLogout = () => { logout(); };

  return (
    <div className="min-h-screen bg-parchment flex flex-col">
      <header className="sticky top-0 z-30 bg-navy border-b-2 border-gold px-4 lg:px-8 py-3 flex items-center gap-4">
        <Link to={homePath(user?.role)} className="flex items-center gap-2" title="Dashboard">
          <div className="w-7 h-7 bg-gold/20 rounded-lg flex items-center justify-center">
            <GraduationCap className="w-4 h-4 text-gold" />
          </div>
          <span className="font-heading text-base text-parchment"><span className="text-gold">E</span>pistemy</span>
        </Link>
        {user?.role === 'platform_admin' && <AdminNav />}
        <div className="flex-1" />
        <div className="flex items-center gap-3">
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
            user?.role === 'student' ? 'bg-success-bg text-success' : 'bg-gold/20 text-gold-light'
          }`}>
            {ROLE_LABELS[user?.role || ''] || 'User'}
          </span>
          <button
            onClick={handleLogout}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-parchment border border-parchment/30 hover:bg-parchment/10 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" /> Sign out
          </button>
        </div>
      </header>

      <main className="flex-1 w-full max-w-6xl mx-auto p-4 lg:p-8"><Outlet /></main>
    </div>
  );
}
