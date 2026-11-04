import { get } from './client';

export interface AspectStat {
  key: string;
  label: string;
  description: string;
  pct_students: number; // 0..1 — share of students at/above the mastery bar
  avg_score: number;    // 0..1 — class average for this aspect
}

export interface TopicStat {
  label: string;
  pct_students: number; // back-compat alias for pct_named
  avg_score: number;    // back-compat alias for avg_named
  pct_named: number;    // share who recalled the topic's concepts (node_score >= bar)
  avg_named: number;
  pct_mastered: number; // share who demonstrated it on the full EDS rubric (>= bar)
  avg_mastered: number;
  n_attempted: number;
}

export interface CoursePerformance {
  practice_takers: number;
  bar: number;
  aspects: AspectStat[];
  topics: TopicStat[];
}

// Anonymized class performance on the course's practice tests (professor only).
export function getCoursePerformance(courseId: string): Promise<CoursePerformance> {
  return get<CoursePerformance>(`/api/courses/${courseId}/performance`);
}
