import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ClipboardList, ChevronRight, Plus, Loader2, Share2, Copy, Check, X } from 'lucide-react';
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
    <div className="fixed inset-0 bg-navy/40 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl ring-1 ring-black/5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold to-gold-light flex items-center justify-center">
              <Share2 className="w-4 h-4 text-white" />
            </div>
            <h2 className="text-lg font-heading text-navy">Demo link ready</h2>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink transition-colors"><X className="w-5 h-5" /></button>
        </div>
        <p className="text-sm text-muted mb-4 leading-relaxed">
          Anyone with this link can take <span className="font-medium text-ink">{link.title || 'the demo'}</span> with
          <span className="font-medium text-ink"> no login</span> — practice mode, nothing saved. Expires in
          {' '}{link.days ?? 10} days and stops after {link.max_attempts ?? 10} attempts.
        </p>
        <div className="flex gap-2">
          <input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()}
            className="flex-1 px-3 py-2.5 border border-border rounded-xl text-sm font-mono bg-parchment/60 text-ink focus:outline-none focus:ring-2 focus:ring-gold/30" />
          <button onClick={copy}
            className="inline-flex items-center gap-1.5 px-4 py-2.5 bg-navy text-white rounded-xl text-sm font-medium hover:bg-navy-light transition-colors">
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
    <div className="space-y-8">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-heading text-navy tracking-tight">Active Assignments</h1>
          <p className="text-muted text-sm mt-1.5">All active assignments across your courses. Open one to review submissions and scores.</p>
        </div>
        <Link
          to="/professor/assignments/new"
          className="inline-flex items-center gap-2 px-4 py-2.5 bg-navy text-white rounded-xl text-sm font-medium shadow-sm hover:bg-navy-light hover:shadow transition-all"
        >
          <Plus className="w-4 h-4" /> Create Assignment
        </Link>
      </div>

      {isLoading ? (
        <div className="p-16 flex items-center justify-center bg-white rounded-2xl border border-border/60">
          <Loader2 className="w-6 h-6 text-navy animate-spin" />
        </div>
      ) : assignments.length === 0 ? (
        <div className="p-16 text-center bg-white rounded-2xl border border-border/60">
          <div className="w-14 h-14 rounded-2xl bg-parchment flex items-center justify-center mx-auto mb-4">
            <ClipboardList className="w-7 h-7 text-gold" />
          </div>
          <p className="text-sm text-ink font-medium">No active assignments yet.</p>
          <Link to="/professor/assignments/new" className="text-sm text-gold hover:text-gold-light font-medium mt-2 inline-block">
            Create an assignment to get started →
          </Link>
        </div>
      ) : (
        <div className="grid gap-3">
          {assignments.map((a) => (
            <div
              key={a.assignment_id}
              className="group relative flex items-center gap-3 bg-white rounded-2xl border border-border/60 shadow-sm px-4 py-3.5 overflow-hidden transition-all duration-200 hover:shadow-md hover:border-gold/50 hover:-translate-y-0.5"
            >
              {/* accent bar reveals on hover */}
              <span className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-gold to-gold-light opacity-0 group-hover:opacity-100 transition-opacity" />

              <Link to={`/professor/assignments/${a.assignment_id}/grades`} className="flex items-center gap-4 flex-1 min-w-0">
                <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-navy to-navy-light flex items-center justify-center shadow-sm flex-shrink-0">
                  <ClipboardList className="w-5 h-5 text-gold-light" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[15px] font-semibold text-navy truncate">{a.title}</p>
                  <p className="text-xs text-muted mt-0.5 truncate">{a.course_name}</p>
                </div>
                <StatusBadge status={a.status} />
              </Link>

              <button
                onClick={() => mint.mutate(a)}
                disabled={mint.isPending}
                title="Create a credential-free demo link"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gold/40 text-xs font-medium text-gold hover:bg-gold hover:text-white hover:border-gold disabled:opacity-50 transition-colors flex-shrink-0"
              >
                {mint.isPending && mint.variables?.assignment_id === a.assignment_id
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Share2 className="w-3.5 h-3.5" />}
                Share as demo
              </button>

              <ChevronRight className="w-4 h-4 text-muted flex-shrink-0 transition-all group-hover:text-gold group-hover:translate-x-0.5" />
            </div>
          ))}
        </div>
      )}

      {demoLink && <DemoLinkModal link={demoLink} onClose={() => setDemoLink(null)} />}
    </div>
  );
}
