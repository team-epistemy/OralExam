import { get, post } from './client';

// Exam camera snapshots (identity / proctor / response). Authed exams only —
// demo links never record. The browser PUTs each JPEG straight to the bucket via
// a short-lived signed URL; the row is recorded server-side at presign time.

export interface ProctorMedia {
  media_id: string;
  kind: 'identity' | 'proctor' | 'response';
  turn_index: number | null;
  captured_at: string | null;
  url: string;
}

export async function uploadSnapshot(
  sessionId: string,
  kind: 'identity' | 'proctor' | 'response',
  blob: Blob,
  turnIndex?: number,
): Promise<void> {
  const { url } = await post<{ url: string }>('/api/proctoring/presign', {
    session_id: sessionId, kind, turn_index: turnIndex ?? null,
  });
  await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'image/jpeg' }, body: blob });
}

// Professor/admin review: the session's snapshots with signed view URLs.
export async function listProctoring(sessionId: string): Promise<ProctorMedia[]> {
  const r = await get<{ media: ProctorMedia[] }>(`/api/exams/${sessionId}/proctoring`);
  return r.media || [];
}
