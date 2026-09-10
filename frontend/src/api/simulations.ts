import { get, post } from './client';

export interface AdminAssignment {
  id: string;
  title: string;
  status: string;
  course_name: string;
  question_count: number;
}

export interface SimTurn {
  round: number;
  prompt: string;
  is_probe: boolean;
  answer: string;
  answered: boolean;
  adequate: boolean;
  nodes_demonstrated: string[];
  edges_demonstrated: number[];
  novel_extensions: string[];
  recitation_score: number | null;
  probe: string;
}

export interface SimRubricEdge {
  src: string;
  dst: string;
  link_type: string;
  explanation: string;
}

export interface SimRubric {
  nodes: string[];
  edges: SimRubricEdge[];
  extensions: string[];
  has_eds: boolean;
}

export interface SimBreakdown {
  kind: 'eds' | 'legacy';
  note?: string;
  nodes_expected?: string[];
  nodes_demonstrated?: string[];
  edges_expected?: number;
  edges_demonstrated?: string[];
  node_score?: number;
  edge_score?: number;
  recitation_min?: number;
  authenticity_R?: number;
  generativity?: number;
  novel_extensions?: string[];
  formula?: string;
}

export interface SimPerQuestion {
  topic: string;
  question?: string;
  turns: number;
  answered: boolean;
  adequate: boolean;
  score: number; // 0-100
  rubric?: SimRubric;
  transcript?: SimTurn[];
  breakdown?: SimBreakdown;
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

export interface SimTurnRow {
  agent_index: number;
  agent_skill: number | null;
  question_index: number;
  question_text: string | null;
  topic: string | null;
  round: number;
  is_probe: boolean;
  prompt: string | null;
  answer: string | null;
  probe: string | null;
  answered: boolean | null;
  adequate: boolean | null;
  recitation_score: number | null;
  nodes_demonstrated: string[];
  edges_demonstrated: number[];
  novel_extensions: string[];
  question_score: number | null;
}

// Normalized per-turn transcript for a simulation (the queryable analysis surface).
export function listSimulationTurns(id: string): Promise<{ turns: SimTurnRow[] }> {
  return get(`/api/admin/simulations/${id}/turns`);
}
