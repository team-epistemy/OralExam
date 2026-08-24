import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { BookOpen, Upload, ClipboardList, FileText, ArrowRight, Eye, Plus } from 'lucide-react';
import { get } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';
import DocumentViewerModal from '../../components/DocumentViewerModal';
import AddCourseModal from '../../components/AddCourseModal';

interface Course {
  course_id: string;
  course_name: string;
}

interface RecentUpload {
  material_version_id: string;
  file_name: string;
  display_name: string;
  course_name: string;
  status: string;
  created_at: string;
}

interface ActiveAssignment {
  assignment_id: string;
  title: string;
  course_name: string;
  status: string;
  created_at: string;
}

interface DashboardData {
  courses: Course[];
  recent_uploads: RecentUpload[];
  active_assignments: ActiveAssignment[];
}

export default function ProfessorDashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['professor-dashboard'],
    queryFn: () => get<DashboardData>('/api/professor/dashboard'),
    retry: false,
  });

  const [viewing, setViewing] = useState<{ id: string; name: string } | null>(null);
  const [showAddCourse, setShowAddCourse] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const courses = data?.courses || [];
  const recentUploads = data?.recent_uploads || [];
  const activeAssignments = data?.active_assignments || [];

  if (isLoading) {
    return (
      <div className="animate-pulse space-y-6">
        <div className="h-8 w-48 bg-parchment-dark rounded" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-32 bg-parchment-dark rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-heading text-navy">Dashboard</h1>
        <p className="text-muted text-sm mt-1">Manage your courses and assessments</p>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-border p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-parchment-dark rounded-lg flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-navy" />
            </div>
            <div>
              <p className="text-2xl font-bold text-ink">{courses.length}</p>
              <p className="text-sm text-muted">Active Courses</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl border border-border p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-success-bg rounded-lg flex items-center justify-center">
              <Upload className="w-5 h-5 text-success" />
            </div>
            <div>
              <p className="text-2xl font-bold text-ink">{recentUploads.length}</p>
              <p className="text-sm text-muted">Recent Uploads</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl border border-border p-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-parchment-dark rounded-lg flex items-center justify-center">
              <ClipboardList className="w-5 h-5 text-gold" />
            </div>
            <div>
              <p className="text-2xl font-bold text-ink">{activeAssignments.length}</p>
              <p className="text-sm text-muted">Active Assignments</p>
            </div>
          </div>
        </div>
      </div>

      {/* Courses */}
      <div className="bg-white rounded-xl border border-border">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h2 className="font-heading text-lg text-navy">Your Courses</h2>
          <button
            onClick={() => setShowAddCourse(true)}
            className="inline-flex items-center gap-1 text-sm text-gold hover:text-gold-light font-medium"
          >
            <Plus className="w-4 h-4" /> Create Course
          </button>
        </div>
        <div className="divide-y divide-border">
          {courses.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted">
              No courses yet.{' '}
              <button onClick={() => setShowAddCourse(true)} className="text-gold hover:text-gold-light font-medium">
                Create your first course
              </button>{' '}
              to get started — or upload materials to auto-create one.
            </div>
          ) : (
            courses.map((course) => (
              <Link
                key={course.course_id}
                to={`/professor/courses/${course.course_id}`}
                className="flex items-center gap-4 px-5 py-3 hover:bg-parchment transition-colors"
              >
                <div className="w-8 h-8 bg-parchment-dark rounded flex items-center justify-center">
                  <BookOpen className="w-4 h-4 text-navy" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-ink">{course.course_name}</p>
                </div>
                <ArrowRight className="w-4 h-4 text-muted" />
              </Link>
            ))
          )}
        </div>
      </div>

      {/* Recent uploads */}
      <div className="bg-white rounded-xl border border-border">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h2 className="font-heading text-lg text-navy">Recent Uploads</h2>
          <Link to="/professor/upload" className="text-sm text-gold hover:text-gold-light font-medium">
            Upload New
          </Link>
        </div>
        <div className="divide-y divide-border">
          {recentUploads.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted">
              No uploads yet. Upload course materials to generate questions.
            </div>
          ) : (
            recentUploads.map((upload) => (
              <div key={upload.material_version_id} className="flex items-center gap-4 px-5 py-3">
                <FileText className="w-4 h-4 text-muted" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-ink truncate">{upload.file_name}</p>
                  <p className="text-xs text-muted">{upload.course_name}</p>
                </div>
                <StatusBadge status={upload.status} />
                <button
                  onClick={() => setViewing({ id: upload.material_version_id, name: upload.file_name || upload.display_name || 'Document' })}
                  className="p-1.5 text-blue-600 hover:bg-blue-50 rounded transition-colors"
                  title="View document"
                >
                  <Eye className="w-4 h-4" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Active assignments */}
      <div className="bg-white rounded-xl border border-border">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h2 className="font-heading text-lg text-navy">Active Assignments</h2>
          <div className="flex items-center gap-4">
            <Link to="/professor/assignments" className="text-sm text-gold hover:text-gold-light font-medium">
              View all
            </Link>
            <Link to="/professor/assignments/new" className="text-sm text-gold hover:text-gold-light font-medium">
              Create New
            </Link>
          </div>
        </div>
        <div className="divide-y divide-border">
          {activeAssignments.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted">
              No active assignments. Create one to start assessing students.
            </div>
          ) : (
            activeAssignments.map((assignment) => (
              <Link
                key={assignment.assignment_id}
                to={`/professor/assignments/${assignment.assignment_id}/grades`}
                className="flex items-center gap-4 px-5 py-3 hover:bg-parchment transition-colors"
              >
                <ClipboardList className="w-4 h-4 text-muted" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-ink">{assignment.title}</p>
                  <p className="text-xs text-muted">{assignment.course_name}</p>
                </div>
                <StatusBadge status={assignment.status} />
                <ArrowRight className="w-4 h-4 text-muted" />
              </Link>
            ))
          )}
        </div>
      </div>

      {viewing && (
        <DocumentViewerModal
          materialId={viewing.id}
          fallbackName={viewing.name}
          onClose={() => setViewing(null)}
        />
      )}

      {showAddCourse && (
        <AddCourseModal
          onClose={() => setShowAddCourse(false)}
          onCreated={(id) => {
            setShowAddCourse(false);
            queryClient.invalidateQueries({ queryKey: ['professor-dashboard'] });
            navigate(`/professor/courses/${id}`);
          }}
        />
      )}
    </div>
  );
}
