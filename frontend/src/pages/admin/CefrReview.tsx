import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ClipboardCheck, AlertTriangle, Loader2, Save, Users, Mic, Gauge, FileText, Info,
} from 'lucide-react';
import { ApiError } from '../../api/client';
import {
  listCefrReviewAttempts, getCefrReviewAttempt, saveCefrReviewRating,
  CEFR_BANDS, CEFR_DIMENSIONS, type CefrBand, type CefrDimension,
  type CefrReviewDetail,
} from '../../api/cefrReview';

const DIM_LABEL: Record<CefrDimension, string> = {
  fluency: 'Fluency', range: 'Range', accuracy: 'Accuracy', coherence: 'Coherence',
};

// A 0–1 metric → percent string, or "—" when unmeasured (null, never 0).
function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`;
}

// ── Rating form: four feature dimensions + overall, transcript edit, notes ───
function RatingForm({ detail }: { detail: CefrReviewDetail }) {
  const queryClient = useQueryClient();
  const mine = detail.ratings.find((r) => r.is_me);

  const [bands, setBands] = useState<Record<string, CefrBand | ''>>({});
  const [overall, setOverall] = useState<CefrBand | ''>('');
  const [corrected, setCorrected] = useState('');
  const [notes, setNotes] = useState('');

  // Reseed from my existing rating whenever a different attempt opens.
  useEffect(() => {
    setBands({
      fluency: (mine?.bands.fluency as CefrBand) ?? '',
      range: (mine?.bands.range as CefrBand) ?? '',
      accuracy: (mine?.bands.accuracy as CefrBand) ?? '',
      coherence: (mine?.bands.coherence as CefrBand) ?? '',
    });
    setOverall((mine?.bands.overall as CefrBand) ?? '');
    setCorrected(mine?.corrected_transcript ?? '');
    setNotes(mine?.notes ?? '');
  }, [detail.session_id, detail.turn_number]); // eslint-disable-line react-hooks/exhaustive-deps

  const complete = CEFR_DIMENSIONS.every((d) => bands[d]) && overall;

  const save = useMutation({
    mutationFn: () => saveCefrReviewRating(detail.session_id, detail.turn_number, {
      fluency: bands.fluency as CefrBand,
      range: bands.range as CefrBand,
      accuracy: bands.accuracy as CefrBand,
      coherence: bands.coherence as CefrBand,
      overall: overall as CefrBand,
      corrected_transcript: corrected.trim() || null,
      notes: notes.trim() || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cefr-review-attempt', detail.session_id, detail.turn_number] });
      queryClient.invalidateQueries({ queryKey: ['cefr-review-attempts'] });
    },
  });

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
      <div className="flex items-center gap-2">
        <ClipboardCheck className="w-4 h-4 text-navy" />
        <h3 className="font-heading text-navy">Your CEFR rating</h3>
        {mine && <span className="text-[11px] text-gray-500">(editing your saved rating)</span>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {CEFR_DIMENSIONS.map((d) => (
          <label key={d} className="block">
            <span className="block text-xs font-medium text-gray-700 mb-1">{DIM_LABEL[d]}</span>
            <select
              value={bands[d] ?? ''}
              onChange={(e) => setBands((b) => ({ ...b, [d]: e.target.value as CefrBand }))}
              className="w-full px-2 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
            >
              <option value="">—</option>
              {CEFR_BANDS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
        ))}
        <label className="block">
          <span className="block text-xs font-semibold text-navy mb-1">Overall</span>
          <select
            value={overall}
            onChange={(e) => setOverall(e.target.value as CefrBand)}
            className="w-full px-2 py-1.5 border border-navy/40 rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-navy/20"
          >
            <option value="">—</option>
            {CEFR_BANDS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">
          Corrected transcript <span className="text-gray-400">(optional — your correction, kept per rater)</span>
        </label>
        <textarea
          value={corrected}
          onChange={(e) => setCorrected(e.target.value)}
          rows={3}
          placeholder="Leave blank to accept the recognized transcript as-is."
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">Notes <span className="text-gray-400">(optional)</span></label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Rationale for the bands, or anything a second rater should know."
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
        />
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={() => save.mutate()}
          disabled={!complete || save.isPending}
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy text-white rounded-lg text-sm font-medium disabled:opacity-50 hover:bg-navy/90"
        >
          {save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {mine ? 'Update rating' : 'Save rating'}
        </button>
        {!complete && <span className="text-xs text-gray-500">All four dimensions + overall are required.</span>}
        {save.isSuccess && <span className="text-xs text-green-700">Saved.</span>}
        {save.isError && (
          <span className="text-xs text-red-700">
            {save.error instanceof ApiError ? save.error.message : 'Save failed.'}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Other raters' bands (inter-rater view) ──────────────────────────────────
function OtherRaters({ detail }: { detail: CefrReviewDetail }) {
  const others = detail.ratings.filter((r) => !r.is_me);
  if (others.length === 0) return null;
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
      <div className="flex items-center gap-2">
        <Users className="w-4 h-4 text-navy" />
        <h3 className="font-heading text-navy">Other raters ({others.length})</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase text-gray-400">
              <th className="py-1 pr-4">Rater</th>
              {CEFR_DIMENSIONS.map((d) => <th key={d} className="py-1 pr-4">{DIM_LABEL[d]}</th>)}
              <th className="py-1 pr-4">Overall</th>
              <th className="py-1">Notes</th>
            </tr>
          </thead>
          <tbody>
            {others.map((r) => (
              <tr key={r.rater_id} className="border-t border-gray-100 align-top">
                <td className="py-1.5 pr-4 font-mono text-xs text-gray-500">{r.rater_id}</td>
                {CEFR_DIMENSIONS.map((d) => <td key={d} className="py-1.5 pr-4">{r.bands[d] ?? '—'}</td>)}
                <td className="py-1.5 pr-4 font-semibold text-navy">{r.bands.overall ?? '—'}</td>
                <td className="py-1.5 text-xs text-gray-600 max-w-xs">{r.notes || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Attempt detail panel ─────────────────────────────────────────────────────
function AttemptDetail({ sessionId, turnNumber }: { sessionId: string; turnNumber: number }) {
  const { data: detail, isLoading } = useQuery({
    queryKey: ['cefr-review-attempt', sessionId, turnNumber],
    queryFn: () => getCefrReviewAttempt(sessionId, turnNumber),
  });

  if (isLoading || !detail) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        <Loader2 className="w-6 h-6 animate-spin" />
      </div>
    );
  }

  const autoBand = detail.auto_cefr?.overall_band ?? detail.auto_cefr?.turn_band_name ?? null;

  return (
    <div className="space-y-4">
      {/* Reliability banner */}
      {detail.needs_review && (
        <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg p-3">
          <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
          <div className="text-sm text-amber-800">
            <span className="font-medium">Flagged for review.</span>{' '}
            {detail.needs_review_reason || 'Low recognizer confidence — verify the transcript before rating.'}
          </div>
        </div>
      )}

      {/* Transcript */}
      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-2">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-navy" />
          <h3 className="font-heading text-navy">Transcript</h3>
          <span className="text-[11px] text-gray-400">· turn {detail.turn_number}</span>
        </div>
        <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
          {detail.transcript || <span className="text-gray-400">No transcript recorded for this attempt.</span>}
        </p>
        {detail.attempt_corrected_transcript && (
          <div className="mt-2 pt-2 border-t border-gray-100">
            <div className="text-[11px] font-semibold text-gray-500 mb-1">Attempt correction (shared)</div>
            <p className="text-sm text-gray-700 whitespace-pre-wrap">{detail.attempt_corrected_transcript}</p>
          </div>
        )}
      </div>

      {/* Acoustic metrics + reliability + auto band */}
      <div className="grid md:grid-cols-3 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Gauge className="w-4 h-4 text-navy" />
            <h4 className="text-sm font-heading text-navy">Reliability signals</h4>
          </div>
          <dl className="text-sm space-y-1">
            <div className="flex justify-between"><dt className="text-gray-500">Confidence</dt><dd>{pct(detail.confidence_mean)}</dd></div>
            <div className="flex justify-between"><dt className="text-gray-500">Transcript reliability</dt><dd>{pct(detail.transcript_reliability)}</dd></div>
            <div className="flex justify-between"><dt className="text-gray-500">Intelligibility</dt><dd>{pct(detail.intelligibility)}</dd></div>
          </dl>
          <p className="mt-2 text-[10px] text-gray-400 leading-snug flex gap-1">
            <Info className="w-3 h-3 shrink-0 mt-0.5" />
            Metadata only — recognizer confidence, never part of the band.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Mic className="w-4 h-4 text-navy" />
            <h4 className="text-sm font-heading text-navy">Machine (auto) band</h4>
          </div>
          <div className="text-3xl font-heading text-navy">{autoBand ?? '—'}</div>
          {detail.auto_cefr?.criterion_averages && (
            <div className="mt-2 text-[11px] text-gray-500 space-y-0.5">
              {Object.entries(detail.auto_cefr.criterion_averages).map(([k, v]) => (
                <div key={k} className="flex justify-between"><span className="capitalize">{k}</span><span>{typeof v === 'number' ? v.toFixed(1) : String(v)}</span></div>
              ))}
            </div>
          )}
          <p className="mt-2 text-[10px] text-gray-400">Weak/provisional label — your rating is the ground truth.</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Users className="w-4 h-4 text-navy" />
            <h4 className="text-sm font-heading text-navy">Raters</h4>
          </div>
          <div className="text-3xl font-heading text-navy">{detail.ratings.length}</div>
          <p className="mt-2 text-[10px] text-gray-400">
            {detail.ratings.some((r) => r.is_me) ? 'Includes your rating.' : 'You have not rated this yet.'}
          </p>
        </div>
      </div>

      <RatingForm detail={detail} />
      <OtherRaters detail={detail} />

      {/* No-audio note (forward hook; see api/cefrReview.ts). */}
      <p className="text-[11px] text-gray-400 flex items-center gap-1 px-1">
        <Info className="w-3 h-3" />
        Audio is not stored for this exam — rate from the transcript, metrics and reliability signals above.
      </p>
    </div>
  );
}

// ── Page: attempt list + detail ──────────────────────────────────────────────
export default function CefrReview() {
  const { data: attempts, isLoading } = useQuery({
    queryKey: ['cefr-review-attempts'],
    queryFn: listCefrReviewAttempts,
  });
  const [selected, setSelected] = useState<{ s: string; t: number } | null>(null);

  // Default to the first attempt (needs-review sorts first) once loaded.
  const first = attempts?.[0];
  useEffect(() => {
    if (!selected && first) setSelected({ s: first.session_id, t: first.turn_number });
  }, [first, selected]);

  const selectedAttempt = useMemo(
    () => attempts?.find((a) => selected && a.session_id === selected.s && a.turn_number === selected.t),
    [attempts, selected],
  );

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <ClipboardCheck className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">CEFR Speaking Review</h1>
          <p className="text-sm text-muted">
            Label speaking attempts against the CEFR Table 3 dimensions. Flagged attempts sort first.
            Multiple raters can score the same attempt for inter-rater agreement.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-64 text-gray-400"><Loader2 className="w-6 h-6 animate-spin" /></div>
      ) : !attempts || attempts.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-lg p-10 text-center text-gray-500">
          No speaking attempts to rate yet. Attempts appear here once a CEFR speaking exam records reliability metadata.
        </div>
      ) : (
        <div className="grid lg:grid-cols-[320px_1fr] gap-6">
          {/* Attempt list */}
          <div className="space-y-2">
            {attempts.map((a) => {
              const active = selected?.s === a.session_id && selected?.t === a.turn_number;
              return (
                <button
                  key={`${a.session_id}:${a.turn_number}`}
                  onClick={() => setSelected({ s: a.session_id, t: a.turn_number })}
                  className={`w-full text-left p-3 rounded-lg border transition-colors ${
                    active ? 'border-navy bg-navy/5' : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-mono text-gray-500 truncate">{a.session_id.slice(0, 8)}… · t{a.turn_number}</span>
                    {a.needs_review && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded shrink-0">
                        <AlertTriangle className="w-3 h-3" /> review
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-gray-800 line-clamp-2">{a.transcript_preview || <span className="text-gray-400">No transcript</span>}</p>
                  <div className="mt-1.5 flex items-center gap-3 text-[11px] text-gray-500">
                    <span>auto {a.auto_band ?? '—'}</span>
                    <span className="inline-flex items-center gap-1"><Users className="w-3 h-3" />{a.rater_count}</span>
                    {a.rated_by_me && <span className="text-green-700">rated by you</span>}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Detail */}
          <div>
            {selected ? (
              <AttemptDetail
                key={`${selected.s}:${selected.t}`}
                sessionId={selected.s}
                turnNumber={selected.t}
              />
            ) : (
              <div className="text-gray-400 text-sm p-6">Select an attempt to review.</div>
            )}
            {selectedAttempt?.student_id && (
              <p className="mt-3 text-[11px] text-gray-400 px-1">Student: <span className="font-mono">{selectedAttempt.student_id}</span></p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
