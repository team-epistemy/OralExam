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
  pct_students: number; // share of all practice-takers who demonstrated the topic
  avg_score: number;
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
