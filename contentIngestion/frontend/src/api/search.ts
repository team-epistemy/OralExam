import { post } from './client';

export interface SearchResult {
  id: string;
  content: string;
  material_id: string;
  material_name: string;
  relevance_score: number;
  concept_tags: string[];
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
}

export async function searchCorpus(courseId: string, query: string, limit = 10): Promise<SearchResponse> {
  return post('/api/search', { course_id: courseId, query, limit });
}
