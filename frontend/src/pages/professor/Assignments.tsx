import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ClipboardList, ArrowRight, Plus, Loader2 } from 'lucide-react';
import { get } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';

interface ActiveAssignment {
  assignment_id: string;
  title: string;
  course_name: string;
  status: string;
  created_at: string;
}

interface DashboardData {
  active_assignments: ActiveAssignment[];
}

export default function ProfessorAssignments() {
  const { data, isLoading } = useQuery({
    queryKey: ['professor-dashboard'],
    queryFn: () => get<DashboardData>('/api/professor/dashboard'),
    retry: false,
  });

  const assignments = data?.active_assignments || [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-heading text-navy">Active Assignments</h1>
          <p className="text-muted text-sm mt-1">All active assignments across your courses. Open one to review submissions and scores.</p>
        </div>
        <Link
          to="/professor/assignments/new"
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy/90 transition-colors"
        >
          <Plus className="w-4 h-4" /> Create Assignment
        </Link>
      </div>

      <div className="bg-white rounded-xl border border-border">
        {isLoading ? (
          <div className="p-10 flex items-center justify-center">
            <Loader2 className="w-6 h-6 text-navy animate-spin" />
          </div>
        ) : assignments.length === 0 ? (
          <div className="p-10 text-center">
            <ClipboardList className="w-8 h-8 text-muted mx-auto mb-3" />
            <p className="text-sm text-muted">No active assignments yet.</p>
            <Link to="/professor/assignments/new" className="text-sm text-gold hover:text-gold-light font-medium mt-2 inline-block">
              Create an assignment to get started
            </Link>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {assignments.map((a) => (
              <Link
                key={a.assignment_id}
                to={`/professor/assignments/${a.assignment_id}/grades`}
                className="flex items-center gap-4 px-5 py-3.5 hover:bg-parchment transition-colors"
              >
                <div className="w-8 h-8 bg-parchment-dark rounded flex items-center justify-center flex-shrink-0">
                  <ClipboardList className="w-4 h-4 text-navy" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-ink truncate">{a.title}</p>
                  <p className="text-xs text-muted">{a.course_name}</p>
                </div>
                <StatusBadge status={a.status} />
                <ArrowRight className="w-4 h-4 text-muted" />
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
