import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ClipboardList, ArrowRight, Plus, Loader2, Share2, Copy, Check, X } from 'lucide-react';
import { get } from '../../api/client';
import { createAssignmentDemoLink, type DemoLinkCreated } from '../../api/demo';
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

// Modal showing the freshly-minted, credential-free demo URL with a copy button.
function DemoLinkModal({ link, onClose }: { link: DemoLinkCreated & { title?: string }; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(link.url); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* clipboard blocked — the URL is selectable in the field */ }
  };
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl max-w-lg w-full p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <h2 className="text-lg font-heading text-navy">Demo link ready</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X className="w-5 h-5" /></button>
        </div>
        <p className="text-sm text-muted mb-4">
          Anyone with this link can take <span className="font-medium text-ink">{link.title || 'the demo'}</span> with
          <span className="font-medium text-ink"> no login</span> — practice mode, nothing saved. It expires in
          {' '}{link.days ?? 10} days and stops after {link.max_attempts ?? 10} attempts.
        </p>
        <div className="flex gap-2">
          <input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()}
            className="flex-1 px-3 py-2 border border-border rounded-lg text-sm font-mono bg-parchment" />
          <button onClick={copy}
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy/90">
            {copied ? <><Check className="w-4 h-4" /> Copied</> : <><Copy className="w-4 h-4" /> Copy</>}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ProfessorAssignments() {
  const { data, isLoading } = useQuery({
    queryKey: ['professor-dashboard'],
    queryFn: () => get<DashboardData>('/api/professor/dashboard'),
    retry: false,
  });

  const [demoLink, setDemoLink] = useState<(DemoLinkCreated & { title?: string }) | null>(null);
  const mint = useMutation({
    mutationFn: (a: ActiveAssignment) =>
      createAssignmentDemoLink(a.assignment_id).then((r) => ({ ...r, title: r.title || a.title })),
    onSuccess: (r) => {
      if (r.status === 'error') { alert(r.message || 'Could not create a demo link.'); return; }
      setDemoLink(r);
    },
    onError: (e: Error) => alert(e.message || 'Could not create a demo link.'),
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
              <div key={a.assignment_id} className="flex items-center gap-3 px-5 py-3.5 hover:bg-parchment transition-colors">
                <Link to={`/professor/assignments/${a.assignment_id}/grades`} className="flex items-center gap-4 flex-1 min-w-0">
                  <div className="w-8 h-8 bg-parchment-dark rounded flex items-center justify-center flex-shrink-0">
                    <ClipboardList className="w-4 h-4 text-navy" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-ink truncate">{a.title}</p>
                    <p className="text-xs text-muted">{a.course_name}</p>
                  </div>
                  <StatusBadge status={a.status} />
                </Link>
                <button
                  onClick={() => mint.mutate(a)}
                  disabled={mint.isPending}
                  title="Create a credential-free demo link"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-border rounded-lg text-xs font-medium text-navy hover:bg-parchment-dark disabled:opacity-50 flex-shrink-0"
                >
                  {mint.isPending && mint.variables?.assignment_id === a.assignment_id
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <Share2 className="w-3.5 h-3.5" />}
                  Share as demo
                </button>
                <ArrowRight className="w-4 h-4 text-muted flex-shrink-0" />
              </div>
            ))}
          </div>
        )}
      </div>

      {demoLink && <DemoLinkModal link={demoLink} onClose={() => setDemoLink(null)} />}
    </div>
  );
}
