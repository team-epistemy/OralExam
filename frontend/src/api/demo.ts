import { API_BASE_URL } from '../config';
import { post } from './client';
import type { StartExamResponse, AnswerResponse, SessionStatus, CaseMaterial, CefrOverride, SpeechEvidence, TimedWordPayload, ReliabilityPayload } from './exam';

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
  exam_kind?: string | null;   // 'communication' → the CEFR speaking UI
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

export function demoAnswer(token: string, sessionId: string, questionIndex: number, answerText: string,
  speech?: SpeechEvidence, words?: TimedWordPayload[]): Promise<AnswerResponse> {
  return demoJson('POST', `/api/demo/${token}/answer?session_id=${encodeURIComponent(sessionId)}`,
    { question_index: questionIndex, answer_text: answerText,
      ...(speech ? { speech } : {}), ...(words && words.length ? { words } : {}) });
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

// Public Azure Speech token for the demo (gated by the demo link, server-side).
export function demoSpeechToken(token: string): Promise<{ token: string; region: string }> {
  return demoJson('POST', `/api/demo/${token}/speech-token`, {});
}

// Transcribe a recorded answer clip for a demo subject exam (Google STT, GCP).
export async function demoTranscribe(token: string, blob: Blob): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/demo/${token}/transcribe`, {
    method: 'POST', headers: { 'Content-Type': blob.type || 'audio/webm' }, body: blob });
  if (!res.ok) throw new Error('transcription failed');
  return (await res.json()).transcript || '';
}

// Demo reviewer's per-dimension band overrides (rater scoped to the demo token).
export async function demoGetCefrCalibration(token: string, sessionId: string): Promise<CefrOverride[]> {
  const r = await demoJson<{ overrides: CefrOverride[] }>('GET', `/api/demo/${token}/cefr-calibration?session_id=${encodeURIComponent(sessionId)}`);
  return r.overrides || [];
}
export async function demoSaveCefrCalibration(token: string, body: {
  session_id: string; turn_number: number; dimension: string; human_band: string; machine_band?: string | null;
}): Promise<void> {
  await demoJson('POST', `/api/demo/${token}/cefr-calibration`, body);
}

// Per-attempt transcription-reliability metadata for a demo turn (never affects
// the band; best-effort — see saveCefrReliability in exam.ts).
export async function demoSaveCefrReliability(token: string, body: ReliabilityPayload): Promise<void> {
  await demoJson('POST', `/api/demo/${token}/cefr-reliability`, body);
}

// Anonymous demo-engagement telemetry. Fire-and-forget: we NEVER await this in a
// critical path and swallow every error, so a telemetry failure can't affect the
// demo exam. ANONYMOUS — no PII, no identity; meta carries only {ua, lang, ref}
// on the `opened` event. `keepalive` lets late events (e.g. on page hide) still
// flush; for the hard unload path use demoEventBeacon instead.
export type DemoEventType =
  | 'opened' | 'started' | 'mic_granted' | 'mic_denied' | 'question_shown'
  | 'answer_submitted' | 'completed' | 'abandoned' | 'results_viewed' | 'error';
export interface DemoEventPayload {
  session_id?: string;
  turn_index?: number;
  value_ms?: number;
  meta?: Record<string, unknown>;
}
export function demoEvent(token: string, event_type: DemoEventType, payload?: DemoEventPayload): void {
  try {
    void fetch(`${API_BASE_URL}/api/demo/${token}/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_type, ...(payload || {}) }),
      keepalive: true,
    }).catch(() => { /* best-effort: ignore */ });
  } catch { /* ignore */ }
}

// Unload-safe variant for `abandoned`: navigator.sendBeacon survives page hide /
// unload where fetch may be cancelled. Falls back to demoEvent if unavailable.
export function demoEventBeacon(token: string, event_type: DemoEventType, payload?: DemoEventPayload): void {
  try {
    const body = JSON.stringify({ event_type, ...(payload || {}) });
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      const ok = navigator.sendBeacon(
        `${API_BASE_URL}/api/demo/${token}/event`,
        new Blob([body], { type: 'application/json' }),
      );
      if (ok) return;
    }
    demoEvent(token, event_type, payload);
  } catch { /* ignore */ }
}
