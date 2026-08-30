import { get, post, put, del } from './client';

export interface SessionMaterial {
  material_id: string;
  display_name: string;
}

export interface ClassSession {
  session_id: string;
  session_date: string | null;      // ISO date (YYYY-MM-DD) or null
  session_document: string | null;
  created_at?: string | null;
  in_scope_concepts?: string[];      // concept-graph node ids in scope for this week
  materials?: SessionMaterial[];     // files attached to this session
}

export interface SessionBody {
  session_date: string | null;
  session_document: string | null;
  in_scope_concepts?: string[];      // omit to leave the week's scope unchanged
}

export function listSessions(courseId: string): Promise<{ sessions: ClassSession[] }> {
  return get<{ sessions: ClassSession[] }>(`/api/courses/${courseId}/sessions`);
}

export function createSession(courseId: string, body: SessionBody): Promise<ClassSession> {
  return post<ClassSession>(`/api/courses/${courseId}/sessions`, body);
}

export function updateSession(courseId: string, sessionId: string, body: SessionBody): Promise<{ status: string }> {
  return put<{ status: string }>(`/api/courses/${courseId}/sessions/${sessionId}`, body);
}

export function deleteSession(courseId: string, sessionId: string): Promise<{ status: string }> {
  return del<{ status: string }>(`/api/courses/${courseId}/sessions/${sessionId}`);
}
