import { Outlet, NavLink } from 'react-router-dom';
import { logout } from '../api/auth';
import {
  GraduationCap,
  LayoutDashboard,
  Upload,
  ClipboardList,
  FileText,
  Network,
  UserPlus,
  LogOut,
  Menu,
  X,
} from 'lucide-react';
import { useState } from 'react';

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

const professorLinks = [
  { to: '/professor/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/professor/upload', label: 'Upload Material', icon: Upload },
  { to: '/professor/graph', label: 'Concept Graph', icon: Network },
  { to: '/professor/exam-builder', label: 'Build Exam', icon: FileText },
  { to: '/professor/assignments/new', label: 'Create Assignment', icon: ClipboardList },
];

const studentLinks = [
  { to: '/student/dashboard', label: 'Dashboard', icon: LayoutDashboard },
];

const adminLinks = [
  { to: '/admin/professors', label: 'Add Professor', icon: UserPlus },
];

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const user = getUser();
  const links =
    user?.role === 'platform_admin' ? adminLinks
    : user?.role === 'professor' ? professorLinks
    : studentLinks;

  const handleLogout = () => { logout(); };

  return (
    <div className="min-h-screen bg-parchment flex">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div className="fixed inset-0 bg-black/30 z-40 lg:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-border transform transition-transform lg:translate-x-0 lg:static lg:z-auto
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        <div className="flex items-center gap-3 px-6 py-5 border-b border-border">
          <div className="w-8 h-8 bg-navy rounded-lg flex items-center justify-center">
            <GraduationCap className="w-5 h-5 text-gold" />
          </div>
          <span className="font-heading text-lg text-navy"><span className="text-gold">E</span>pistemy</span>
          <button className="ml-auto lg:hidden" onClick={() => setSidebarOpen(false)}>
            <X className="w-5 h-5 text-muted" />
          </button>
        </div>

        <nav className="p-4 space-y-1">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  isActive ? 'bg-parchment-dark text-navy' : 'text-ink-light hover:bg-parchment hover:text-ink'
                }`
              }
            >
              <link.icon className="w-4 h-4" />
              {link.label}
            </NavLink>
          ))}
        </nav>

        <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-border">
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="w-8 h-8 rounded-full bg-parchment-dark flex items-center justify-center">
              <span className="text-xs font-medium text-ink-light">
                {user?.email?.charAt(0).toUpperCase() || '?'}
              </span>
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

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="sticky top-0 z-30 bg-navy border-b-2 border-gold px-4 lg:px-8 py-3 flex items-center gap-4">
          <button className="lg:hidden p-1" onClick={() => setSidebarOpen(true)}>
            <Menu className="w-5 h-5 text-parchment" />
          </button>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
              user?.role === 'student' ? 'bg-success-bg text-success' : 'bg-gold/20 text-gold-light'
            }`}>
              {ROLE_LABELS[user?.role || ''] || 'User'}
            </span>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 p-4 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
