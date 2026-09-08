import { get } from './client';

export interface ActiveJob {
  kind: string;            // ingest | simulation | ...
  status: string;
  progress_pct: number;
  detail: string;
  updated_at: string | null;
}

export interface DeploymentStatus {
  available: boolean;
  reason?: string;
  service?: string;
  desired?: number;
  running?: number;
  pending?: number;
  rollout_state?: string;         // IN_PROGRESS | COMPLETED | FAILED
  rollout_started?: string | null;
  deployments?: number;
  running_image_digest?: string;
  ecr_latest_digest?: string;
  latest_pushed_at?: string | null;
  on_latest?: boolean;
  image_check_error?: string;
}

export interface ActiveTasks {
  jobs: ActiveJob[];
  deployment: DeploymentStatus;
}

export function getActiveTasks(): Promise<ActiveTasks> {
  return get('/api/admin/active-tasks');
}
