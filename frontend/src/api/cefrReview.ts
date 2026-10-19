import { get, post } from './client';

// CEFR speaking labeling + examiner review (professor / platform_admin).
//
// An examiner lists the speaking attempts that have a reliability row
// (needs-review first), opens one to see its transcript + acoustic metrics +
// reliability signals + the machine (auto) CEFR band, and records a full
// multi-rater human CEFR rating. Reuses the backend's cefr_calibration (per-rater
// band rows) + speaking_reliability (metadata) tables.
//
// NO AUDIO: exam audio is NOT persisted (only the Azure NBest JSON + transcript),
// so the review rates from the transcript + metrics + reliability signals — never
// audio playback. `audio_url` is always null today; it is the forward hook for
// when attempt audio is stored (see the backend TODO(audio-playback)).

export type CefrBand = 'A1' | 'A2' | 'B1' | 'B2' | 'C1' | 'C2';
export const CEFR_BANDS: CefrBand[] = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
// The four CEFR Table 3 feature dimensions the examiner rates, plus overall.
export const CEFR_DIMENSIONS = ['fluency', 'range', 'accuracy', 'coherence'] as const;
export type CefrDimension = (typeof CEFR_DIMENSIONS)[number];

export interface CefrReviewAttempt {
  session_id: string;
  turn_number: number;
  confidence_mean: number | null;
  transcript_reliability: number | null;
  needs_review: boolean;
  needs_review_reason: string | null;
  has_correction: boolean;
  student_id: string | null;
  course_id: string | null;
  session_status: string | null;
  transcript_preview: string;
  auto_band: string | null;
  rater_count: number;
  rated_by_me: boolean;
}

// One rater's rating of an attempt (per-dimension bands collapsed into one object).
export interface CefrRating {
  rater_id: string;
  bands: Partial<Record<CefrDimension | 'overall', CefrBand>>;
  machine_bands: Partial<Record<string, string>>;
  notes: string | null;
  corrected_transcript: string | null;
  created_at: string | null;
  updated_at: string | null;
  is_me: boolean;
}

export interface CefrReviewDetail {
  session_id: string;
  turn_number: number;
  transcript: string;
  // Acoustic metrics / reliability signals — METADATA ONLY, never feed the band.
  confidence_mean: number | null;
  transcript_reliability: number | null;
  intelligibility: number | null;
  needs_review: boolean;
  needs_review_reason: string | null;
  attempt_corrected_transcript: string | null;
  reviewed_by: string | null;
  rescored_bands: Record<string, unknown> | null;
  raw_nbest_json: unknown;
  // Machine (auto) CEFR band block from the grader, if present.
  auto_cefr: {
    overall_band?: string;
    turn_band_name?: string;
    criterion_averages?: Record<string, number>;
    [k: string]: unknown;
  } | null;
  ratings: CefrRating[];
  audio_url: string | null; // always null today — see NO AUDIO note above.
}

export interface SaveCefrRatingBody {
  fluency: CefrBand;
  range: CefrBand;
  accuracy: CefrBand;
  coherence: CefrBand;
  overall: CefrBand;
  corrected_transcript?: string | null;
  notes?: string | null;
}

export async function listCefrReviewAttempts(): Promise<CefrReviewAttempt[]> {
  const r = await get<{ attempts: CefrReviewAttempt[] }>('/api/cefr/review/attempts');
  return r.attempts || [];
}

export async function getCefrReviewAttempt(sessionId: string, turnNumber: number): Promise<CefrReviewDetail> {
  return get<CefrReviewDetail>(
    `/api/cefr/review/attempts/${encodeURIComponent(sessionId)}/${turnNumber}`,
  );
}

export async function saveCefrReviewRating(
  sessionId: string,
  turnNumber: number,
  body: SaveCefrRatingBody,
): Promise<{ status: string; rater_id: string }> {
  return post<{ status: string; rater_id: string }>(
    `/api/cefr/review/attempts/${encodeURIComponent(sessionId)}/${turnNumber}/rating`,
    body,
  );
}
