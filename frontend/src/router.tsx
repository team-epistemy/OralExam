import { createBrowserRouter, Navigate } from 'react-router-dom';
import AuthLayout from './layouts/AuthLayout';
import AppLayout from './layouts/AppLayout';
import Login from './pages/Login';
import Callback from './pages/Callback';
import ProfessorDashboard from './pages/professor/Dashboard';
import CourseDetail from './pages/professor/CourseDetail';
import CourseStudents from './pages/professor/CourseStudents';
import UploadMaterial from './pages/professor/UploadMaterial';
import ConceptGraph from './pages/professor/ConceptGraph';
import BuildExam from './pages/professor/BuildExam';
import CreateAssignment from './pages/professor/CreateAssignment';
import ProfessorAssignments from './pages/professor/Assignments';
import GradeView from './pages/professor/GradeView';
import StudentDashboard from './pages/student/Dashboard';
import TakeExam from './pages/student/TakeExam';
import Results from './pages/student/Results';
import AddProfessor from './pages/admin/AddProfessor';

function getUser() {
  const raw = localStorage.getItem('user');
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function RequireRole({ role, children }: { role: 'professor' | 'student' | 'platform_admin'; children: React.ReactNode }) {
  const user = getUser();
  if (!user || user.role !== role) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function RootRedirect() {
  const user = getUser();
  if (!user) return <Navigate to="/login" replace />;
  if (user.role === 'platform_admin') return <Navigate to="/admin/professors" replace />;
  if (user.role === 'professor') return <Navigate to="/professor/dashboard" replace />;
  return <Navigate to="/student/dashboard" replace />;
}

const basename = import.meta.env.BASE_URL || '/';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootRedirect />,
  },
  {
    element: <AuthLayout />,
    children: [
      { path: '/login', element: <Login /> },
      { path: '/callback', element: <Callback /> },
    ],
  },
  {
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      // Admin routes
      {
        path: '/admin/professors',
        element: <RequireRole role="platform_admin"><AddProfessor /></RequireRole>,
      },
      // Professor routes
      {
        path: '/professor/dashboard',
        element: <RequireRole role="professor"><ProfessorDashboard /></RequireRole>,
      },
      {
        path: '/professor/courses/:courseId',
        element: <RequireRole role="professor"><CourseDetail /></RequireRole>,
      },
      {
        path: '/professor/courses/:courseId/students',
        element: <RequireRole role="professor"><CourseStudents /></RequireRole>,
      },
      {
        path: '/professor/upload',
        element: <RequireRole role="professor"><UploadMaterial /></RequireRole>,
      },
      {
        path: '/professor/graph',
        element: <RequireRole role="professor"><ConceptGraph /></RequireRole>,
      },
      {
        path: '/professor/exam-builder',
        element: <RequireRole role="professor"><BuildExam /></RequireRole>,
      },
      {
        path: '/professor/assignments',
        element: <RequireRole role="professor"><ProfessorAssignments /></RequireRole>,
      },
      {
        path: '/professor/assignments/new',
        element: <RequireRole role="professor"><CreateAssignment /></RequireRole>,
      },
      {
        path: '/professor/assignments/:assignmentId/grades',
        element: <RequireRole role="professor"><GradeView /></RequireRole>,
      },
      // Student routes
      {
        path: '/student/dashboard',
        element: <RequireRole role="student"><StudentDashboard /></RequireRole>,
      },
      {
        path: '/student/exam/:assignmentId',
        element: <RequireRole role="student"><TakeExam /></RequireRole>,
      },
      {
        path: '/student/results/:assignmentId',
        element: <RequireRole role="student"><Results /></RequireRole>,
      },
    ],
  },
], { basename });
