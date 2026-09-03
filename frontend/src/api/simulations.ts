import { get, post } from './client';

export interface AdminAssignment {
  id: string;
  title: string;
  status: string;
  course_name: string;
  question_count: number;
}

export interface SimPerQuestion {
  topic: string;
  turns: number;
  answered: boolean;
  adequate: boolean;
  score: number; // 0-100
}

export interface SimAgent {
  index: number;
  skill: number; // 0-1
  score: number; // 0-100
  answered: number;
  adequate: number;
  questions: number;
  per_q: SimPerQuestion[];
}

export interface SimReport {
  num_agents: number;
  curve: string;
  questions: number;
  agents: SimAgent[];
  aggregate: {
    mean: number;
    min: number;
    max: number;
    stdev: number;
    distribution: Record<string, number>;
  };
  per_question: Array<{ index: number; topic: string; text: string; avg_score: number }>;
}

export interface Simulation {
  simulation_id: string;
  assignment_id: string;
  num_agents: number;
  curve: string;
  status: 'running' | 'completed' | 'failed';
  progress: { agents_done: number; agents_total: number } | null;
  report: SimReport | null;
  error: string | null;
  created_at: string | null;
}

export interface SimListItem {
  simulation_id: string;
  assignment_id: string;
  num_agents: number;
  curve: string;
  status: string;
  mean_score: number | null;
  created_at: string | null;
}

export function listAdminAssignments(): Promise<{ assignments: AdminAssignment[] }> {
  return get('/api/admin/assignments');
}

export interface CreateSimulationBody {
  assignment_id: string;
  num_agents: number;
  curve: 'linear' | 'bell';
  max_followups?: number;
}

export function createSimulation(body: CreateSimulationBody): Promise<{
  simulation_id: string; status: string; num_agents: number; questions: number;
}> {
  return post('/api/admin/simulations', body);
}

export function getSimulation(id: string): Promise<Simulation> {
  return get(`/api/admin/simulations/${id}`);
}

export function listSimulations(): Promise<{ simulations: SimListItem[] }> {
  return get('/api/admin/simulations');
}
