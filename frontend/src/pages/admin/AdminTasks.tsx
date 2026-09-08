import { useQuery } from '@tanstack/react-query';
import { Activity, Loader2, Rocket, CheckCircle, AlertTriangle } from 'lucide-react';
import { getActiveTasks, type ActiveJob, type DeploymentStatus } from '../../api/adminTasks';

const short = (d?: string) => (d ? d.replace('sha256:', '').slice(0, 12) : '—');
const time = (t?: string | null) => (t ? new Date(t).toLocaleString() : '—');

function JobRow({ job }: { job: ActiveJob }) {
  const pct = Math.max(0, Math.min(100, job.progress_pct || 0));
  return (
    <div className="border border-gray-200 rounded-lg p-3">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
        <span className="font-medium text-gray-900 capitalize">{job.kind}</span>
        <span className="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">{job.status}</span>
        <span className="flex-1" />
        <span className="text-xs text-gray-400">{time(job.updated_at)}</span>
      </div>
      <p className="text-xs text-gray-600 mt-1 truncate" title={job.detail}>{job.detail}</p>
      <div className="mt-1.5 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Deployment({ dep }: { dep: DeploymentStatus }) {
  if (!dep?.available) {
    return (
      <div className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <span>Deployment status unavailable: {dep?.reason || 'no access'}</span>
      </div>
    );
  }
  const rolling = dep.rollout_state && dep.rollout_state !== 'COMPLETED';
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2">
        {rolling ? <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
          : <CheckCircle className="w-4 h-4 text-green-600" />}
        <span className="text-sm font-medium text-gray-900">{dep.service}</span>
        <span className={`text-xs px-1.5 py-0.5 rounded ${rolling ? 'bg-blue-50 text-blue-700' : 'bg-green-50 text-green-700'}`}>
          {dep.rollout_state || 'unknown'}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-sm">
        {[['Running', dep.running], ['Desired', dep.desired], ['Pending', dep.pending]].map(([k, v]) => (
          <div key={k as string}><div className="text-xs text-gray-500">{k}</div><div className="font-semibold text-gray-900">{v ?? '—'}</div></div>
        ))}
      </div>
      <div className="text-xs text-gray-600 space-y-1 border-t border-gray-100 pt-2">
        <div className="flex items-center gap-2">
          <span className="text-gray-500 w-28">On latest image</span>
          {dep.on_latest
            ? <span className="text-green-700 font-medium">yes</span>
            : <span className="text-amber-700 font-medium">no — rolling / behind</span>}
        </div>
        <div className="flex gap-2"><span className="text-gray-500 w-28">Running digest</span><code>{short(dep.running_image_digest)}</code></div>
        <div className="flex gap-2"><span className="text-gray-500 w-28">ECR :latest</span><code>{short(dep.ecr_latest_digest)}</code></div>
        <div className="flex gap-2"><span className="text-gray-500 w-28">Latest pushed</span><span>{time(dep.latest_pushed_at)}</span></div>
        <div className="flex gap-2"><span className="text-gray-500 w-28">Rollout started</span><span>{time(dep.rollout_started)}</span></div>
        {dep.image_check_error && <div className="text-amber-700">image check: {dep.image_check_error}</div>}
      </div>
    </div>
  );
}

export default function AdminTasks() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-active-tasks'],
    queryFn: getActiveTasks,
    refetchInterval: 5000,   // live panel
  });

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <Activity className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">Active Deployment Tasks</h1>
          <p className="text-sm text-muted">Live app background jobs and the current backend deployment. Refreshes every 5s.</p>
        </div>
      </div>

      {isLoading && <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>}
      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">Could not load tasks.</div>}

      {data && (
        <>
          <section>
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
              <Activity className="w-4 h-4" /> Background jobs
            </h2>
            {data.jobs.length === 0 ? (
              <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm text-gray-500">No active jobs.</div>
            ) : (
              <div className="space-y-2">{data.jobs.map((j, i) => <JobRow key={i} job={j} />)}</div>
            )}
          </section>

          <section>
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
              <Rocket className="w-4 h-4" /> Backend deployment
            </h2>
            <Deployment dep={data.deployment} />
          </section>
        </>
      )}
    </div>
  );
}
