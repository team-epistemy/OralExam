import { API_BASE_URL } from '../config';
import { post } from './client';
import type { StartExamResponse, AnswerResponse, SessionStatus, CaseMaterial } from './exam';

// Professor mints a credential-free demo link for one of their assignments (authed).
export interface DemoLinkCreated {
  url: string; token: string; title?: string; days?: number; max_attempts?: number;
  status?: string; message?: string;
}
export function createAssignmentDemoLink(assignmentId: string): Promise<DemoLinkCreated> {
  return post(`/api/assignments/${assignmentId}/demo-link`, {});
}

// Credential-free demo API. These hit the public /api/demo/<token>/* endpoints with
// NO Authorization header (the token in the path is the auth). Kept separate from
// exam.ts so the authenticated flow is never touched.

async function demoJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); if (j?.detail) msg = j.detail; } catch { /* keep */ }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface DemoMeta {
  assignment_id: string;
  title: string;
  question_count: number;
  attempts_remaining: number;
  text_first: boolean;
  status?: string;     // set on error responses (not_found | expired | exhausted)
  message?: string;
}

export function demoMeta(token: string): Promise<DemoMeta> {
  return demoJson('GET', `/api/demo/${token}`);
}

export async function demoCase(token: string): Promise<CaseMaterial[]> {
  const r = await demoJson<{ materials: CaseMaterial[] }>('GET', `/api/demo/${token}/case`);
  return r.materials || [];
}

export function demoStart(token: string): Promise<StartExamResponse & { status?: string; message?: string }> {
  return demoJson('POST', `/api/demo/${token}/start`, {});
}

export function demoAnswer(token: string, sessionId: string, questionIndex: number, answerText: string):
  Promise<AnswerResponse> {
  return demoJson('POST', `/api/demo/${token}/answer?session_id=${encodeURIComponent(sessionId)}`,
    { question_index: questionIndex, answer_text: answerText });
}

export function demoStatus(token: string, sessionId: string): Promise<SessionStatus> {
  return demoJson('GET', `/api/demo/${token}/status?session_id=${encodeURIComponent(sessionId)}`);
}

export async function demoComplete(token: string, sessionId: string): Promise<void> {
  await demoJson('POST', `/api/demo/${token}/complete?session_id=${encodeURIComponent(sessionId)}`, {});
}

// Returns an <audio>-playable object URL, or null if TTS is unavailable.
export async function demoTts(token: string, text: string): Promise<HTMLAudioElement | null> {
  const res = await fetch(`${API_BASE_URL}/api/demo/${token}/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) return null;
  return new Audio(URL.createObjectURL(await res.blob()));
}
