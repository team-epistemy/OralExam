// Pure functions: no DOM, no network. Shared by the browser UI and the tests.
// Azure reports Offset and Duration in 100-nanosecond ticks.

export const TICKS_PER_SEC = 1e7;
export const PAUSE_MIN_SEC = 0.25; // gaps at or above this count as a pause
export const LONG_PAUSE_SEC = 1.0; // gaps at or above this count as a long pause
export const WEAK_WORD_ACCURACY = 60; // below this a word is flagged as weak

const round1 = (n) => (n == null || Number.isNaN(n) ? null : Math.round(n * 10) / 10);

/**
 * Parse one recognized phrase from Azure's detailed JSON
 * (result.properties.getProperty(PropertyId.SpeechServiceResponse_JsonResult)).
 * Returns null when the phrase has no usable NBest entry (e.g. silence).
 */
export function parsePhrase(json) {
  const nb = json?.NBest?.[0];
  if (!nb) return null;
  const pa = nb.PronunciationAssessment ?? {};
  const words = (nb.Words ?? []).map((w) => ({
    word: w.Word,
    start: (w.Offset ?? 0) / TICKS_PER_SEC,
    duration: (w.Duration ?? 0) / TICKS_PER_SEC,
    accuracy: w.PronunciationAssessment?.AccuracyScore ?? null,
    errorType: w.PronunciationAssessment?.ErrorType ?? "None",
  }));
  return {
    text: nb.Display ?? json.DisplayText ?? words.map((w) => w.word).join(" "),
    scores: {
      accuracy: pa.AccuracyScore ?? null,
      fluency: pa.FluencyScore ?? null,
      completeness: pa.CompletenessScore ?? null,
      pron: pa.PronScore ?? null,
      prosody: pa.ProsodyScore ?? null, // absent for unsupported locales
    },
    words,
  };
}

function weightedMean(phrases, key) {
  let num = 0;
  let den = 0;
  for (const p of phrases) {
    const v = p.scores[key];
    if (v == null) continue;
    const w = Math.max(p.words.length, 1);
    num += v * w;
    den += w;
  }
  return den ? num / den : null;
}

/**
 * Combine all phrases from one answer into overall scores plus
 * temporal fluency metrics derived from word timings.
 */
export function aggregate(phrases) {
  const valid = (phrases ?? []).filter(Boolean);
  const words = valid.flatMap((p) => p.words).sort((a, b) => a.start - b.start);

  const scores = {};
  for (const key of ["accuracy", "fluency", "completeness", "pron", "prosody"]) {
    scores[key] = round1(weightedMean(valid, key));
  }

  const pauses = [];
  for (let i = 1; i < words.length; i++) {
    const prevEnd = words[i - 1].start + words[i - 1].duration;
    const gap = words[i].start - prevEnd;
    if (gap >= PAUSE_MIN_SEC) pauses.push({ before: words[i].word, at: round1(prevEnd), seconds: round1(gap) });
  }

  const speakingTime = words.reduce((s, w) => s + w.duration, 0);
  const span = words.length ? words.at(-1).start + words.at(-1).duration - words[0].start : 0;
  const perMin = (count, secs) => (secs > 0 ? (count / secs) * 60 : null);

  const weakWords = words
    .filter((w) => w.accuracy != null && w.accuracy < WEAK_WORD_ACCURACY)
    .map((w) => ({ word: w.word, accuracy: w.accuracy }));

  return {
    transcript: valid.map((p) => p.text).join(" ").trim(),
    scores,
    words,
    metrics: {
      wordCount: words.length,
      durationSec: round1(span),
      speechRateWpm: round1(perMin(words.length, span)),
      articulationRateWpm: round1(perMin(words.length, speakingTime)),
      pauseCount: pauses.length,
      longPauseCount: pauses.filter((p) => p.seconds >= LONG_PAUSE_SEC).length,
      meanPauseSec: pauses.length ? round1(pauses.reduce((s, p) => s + p.seconds, 0) / pauses.length) : 0,
      pauseRatio: span > 0 ? round1(((span - speakingTime) / span) * 100) : null, // % of time not speaking
    },
    pauses,
    weakWords,
    prosodyAvailable: scores.prosody != null,
  };
}

// ── CEFR estimation ──────────────────────────────────────────────────────
// A fluency-dominant estimate mapped onto a Versant/GSE-style 0–90 proficiency
// axis, then banded. Thresholds are anchored to published research, NOT locally
// validated cut scores — see estimateCEFR() notes. Key sources:
//   • Tavakoli, Nakatsuhara & Hunter (2020), Modern Language Journal 104(1):
//     per-band temporal-fluency means (Aptis monologic). Speed/phonation/MLR
//     separate A2/B1/B2 but NOT B2 from C1 (statistical plateau).
//   • Huang & Gráf (2020): monologic wpm anchors (B2≈118, C1≈142 wpm).
//   • Chen et al. (ETS RR-18-10), SpeechRater v5.0: fluency carries the largest
//     weight; single ~45–60s response is near the reliability floor (R≈.56).
//   • Pearson Versant/GSE standard-set CEFR band boundaries (10–90 axis).
//   • MS Azure Transparency Note: pron/prosody are native-similarity distances
//     with NO built-in CEFR mapping; prosody is native-anchored (accent-bias
//     risk) so it is shown but never used to raise or cap the CEFR number.

// GSE-axis centres for each band (from Versant/GSE band midpoints).
const GSE_CENTRE = { A2: 36, B1: 50, B2: 67, C1: 80 };
const S = [GSE_CENTRE.A2, GSE_CENTRE.B1, GSE_CENTRE.B2, GSE_CENTRE.C1];

// Per-measure value anchors at [A2, B1, B2, C1]. wpm values are syllable anchors
// converted at ~1.4 syllables/word. meanPause is "lower = more proficient".
const FLUENCY_ANCHORS = {
  speechRateWpm:      { label: "Speech rate",      unit: "wpm", w: 0.30, v: [55, 95, 120, 140] },
  phonationPct:       { label: "Phonation",        unit: "%",   w: 0.30, v: [46, 71, 77, 78] },
  articulationRateWpm:{ label: "Articulation rate",unit: "wpm", w: 0.15, v: [113, 134, 160, 168] },
  meanRunWords:       { label: "Mean run",         unit: "words", w: 0.15, v: [2.3, 4.1, 6.1, 6.4] },
  meanPauseSec:       { label: "Mean pause",       unit: "s",   w: 0.10, v: [1.42, 0.63, 0.56, 0.54] },
};
// CEFR levels for the Table-3 rubric dimensions. "B2+" (fluency-only cap) → 3.5.
const CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];
const levelIndex = (b) => (b === "B2+" ? 3.5 : CEFR_LEVELS.indexOf(b));
const isCefr = (b) => CEFR_LEVELS.includes(b);

const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));

/** Map a measured value onto the GSE axis by piecewise-linear interpolation
 *  between band anchors. Handles increasing (rates) or decreasing (pause) v. */
function interpScore(value, v) {
  if (value == null || Number.isNaN(value)) return null;
  const incr = v[v.length - 1] >= v[0];
  const d = incr ? 1 : -1;
  if (d * value <= d * v[0]) return clamp(S[0] - 8, 20, 90); // below A2 anchor
  for (let i = 0; i < v.length - 1; i++) {
    if (d * value <= d * v[i + 1]) {
      const frac = (value - v[i]) / (v[i + 1] - v[i]);
      return clamp(S[i] + frac * (S[i + 1] - S[i]), 20, 90);
    }
  }
  return clamp(S[S.length - 1] + 5, 20, 90); // above C1 anchor
}

/** GSE-axis score → CEFR letter (Versant/GSE standard-set boundaries). */
export function cefrBand(score) {
  if (score == null) return null;
  if (score < 22) return "<A1";
  if (score < 30) return "A1";
  if (score < 43) return "A2";
  if (score < 59) return "B1";
  if (score < 76) return "B2";
  if (score < 85) return "C1";
  return "C2";
}

/**
 * Estimate CEFR from one answer, scored against the CEFR Table 3 rubric
 * (Qualitative aspects of spoken language use). The five Table-3 columns map to:
 *   • FLUENCY     — from the acoustic timing measures (objective; research-anchored)
 *   • RANGE       — LLM, judged against the Table-3 Range descriptors (→ CEFR band)
 *   • ACCURACY    — LLM, grammatical control (NOT Azure pronunciation accuracy)
 *   • COHERENCE   — LLM, cohesion/organisation
 *   • INTERACTION — not assessable from a monologic response (no interlocutor)
 * Overall is a holistic mean of the available dimension bands. Pronunciation
 * (Azure, native-similarity) is shown as context but is NOT a Table-3 column and
 * never sets the band. NOT a validated cut score — calibrate against human
 * ratings (per the same rubric) before any high-stakes use.
 */
export function estimateCEFR(result, contentEval = null) {
  const m = result?.metrics ?? {};
  const sc = result?.scores ?? {};
  const phonationPct = m.pauseRatio == null ? null : round1(100 - m.pauseRatio);
  const meanRunWords =
    m.wordCount && m.pauseCount != null ? round1(m.wordCount / (m.pauseCount + 1)) : null;
  // meanPauseSec is 0 (not null) when there were no pauses — meaningless as a
  // fluency signal, so only use it when at least one pause was measured.
  const meanPauseSec = m.pauseCount ? m.meanPauseSec : null;
  const values = { ...m, phonationPct, meanRunWords, meanPauseSec };

  // FLUENCY from temporal measures: weighted mean of available measures → band.
  const measures = [];
  let fNum = 0, fDen = 0;
  for (const [key, a] of Object.entries(FLUENCY_ANCHORS)) {
    const s = interpScore(values[key], a.v);
    measures.push({ key, label: a.label, unit: a.unit, value: values[key] ?? null, score: s == null ? null : round1(s), band: cefrBand(s) });
    if (s != null) { fNum += s * a.w; fDen += a.w; }
  }
  const fluencyScore = fDen ? round1(fNum / fDen) : null;
  const fluencyBand = cefrBand(fluencyScore);

  // RANGE / ACCURACY / COHERENCE: CEFR bands the LLM assigned against the Table-3
  // descriptors. Null when not graded yet or the value isn't a valid CEFR level.
  const rubricBand = (k) => (contentEval && isCefr(contentEval[k]) ? contentEval[k] : null);
  const range = { band: rubricBand("range"), note: contentEval?.rangeNote ?? null };
  const accuracy = { band: rubricBand("accuracy"), note: contentEval?.accuracyNote ?? null };
  const coherence = { band: rubricBand("coherence"), note: contentEval?.coherenceNote ?? null };
  const interaction = { band: null, note: "Not assessable from a monologic response — the CEFR Interaction column requires an interlocutor and turn-taking." };

  const graded = range.band || accuracy.band || coherence.band;
  const mode = graded ? "full-rubric" : "fluency-only";
  const notes = [];

  // Overall: holistic mean of the available Table-3 dimension bands.
  const bandsForOverall = [fluencyBand, range.band, accuracy.band, coherence.band].filter((b) => levelIndex(b) >= 0);
  let overall = null;
  if (bandsForOverall.length) {
    const mean = bandsForOverall.reduce((s, b) => s + levelIndex(b), 0) / bandsForOverall.length;
    overall = CEFR_LEVELS[clamp(Math.round(mean), 0, CEFR_LEVELS.length - 1)];
  }

  // Fluency alone cannot separate B2 from C1 (Tavakoli et al. 2020): cap at B2+.
  if (overall && mode === "fluency-only" && (overall === "C1" || overall === "C2")) {
    overall = "B2+";
    notes.push("Capped at B2+: fluency alone cannot separate B2 from C1 (Tavakoli et al. 2020). Grade the answer to assess Range, Accuracy and Coherence against the CEFR rubric.");
  }
  if (overall === "C2") {
    notes.push("A C2 rating from a single LLM-graded transcript is low-confidence — confirm with a human rater before relying on it.");
  }
  if (mode === "fluency-only") {
    notes.push("Only Fluency is scored so far — Range, Accuracy and Coherence need the content grade (press “Grade content”). Interaction is not assessable from a monologue.");
  }

  // Pronunciation: supplementary context (Azure native-similarity), NOT a Table-3
  // column and never used to set the band.
  const pronunciation = { accuracy: sc.accuracy ?? null, prosody: sc.prosody ?? null,
    note: "Azure pronunciation/prosody are native-similarity distances (not a CEFR Table-3 column); shown for context only. Prosody can penalise legitimate L2 accent variation, so it never sets the CEFR band." };
  if (sc.accuracy != null && sc.accuracy < 50) {
    notes.push("Low pronunciation accuracy may affect intelligibility — a human should confirm before trusting this estimate.");
  }

  // Reliability: a single short sample is near the usable floor (R≈.56).
  const dur = m.durationSec ?? 0;
  const confidence = dur > 0 && dur < 30 ? "very low" : "low";
  notes.push(`Single ${dur ? Math.round(dur) + "s " : ""}sample — report ±1 sub-level; aggregate ≥6 prompts for a placement-grade CEFR (R≈.56→.77).`);
  notes.push("Rubric-aligned but not locally validated — calibrate against human CEFR ratings (same Table-3 rubric) on your own students before any high-stakes use.");

  return {
    overall, mode, confidence,
    dimensions: {
      fluency: { band: fluencyBand, score: fluencyScore, measures },
      range, accuracy, coherence, interaction,
    },
    pronunciation,
    task: contentEval ? { relevance: contentEval.relevance ?? null, depth: contentEval.depth ?? null } : null,
    notes,
  };
}

// ── Adaptive multi-question flow ─────────────────────────────────────────
// Pure helpers for the adaptive placement test. The UI (app.js) owns I/O; these
// own the decisions, so they can be unit-tested.

export const CEFR_LADDER = CEFR_LEVELS; // A1 … C2

/** Next question's target level from the last answer's overall band:
 *  meet-or-beat the current level → step up (harder); below it → step down. */
export function nextLevel(current, answerBand) {
  const ci = CEFR_LADDER.indexOf(current);
  const ai = levelIndex(answerBand); // B2+ → 3.5
  if (ci < 0) return CEFR_LADDER[2]; // default B1 if current is unknown
  if (ai < 0) return current;        // no usable answer band → hold
  return ai >= ci ? CEFR_LADDER[Math.min(ci + 1, 5)] : CEFR_LADDER[Math.max(ci - 1, 0)];
}

/** Stop the session at `max` answers, or early once the band is stable
 *  (last two overall bands equal) after at least 3 answers. */
export function shouldStop(overalls, max = 5) {
  const n = overalls.length;
  if (n >= max) return true;
  if (n >= 3 && overalls[n - 1] && overalls[n - 1] === overalls[n - 2]) return true;
  return false;
}

/** Aggregate per-answer estimates into a final placement. Averaging across
 *  responses is what lifts reliability above the single-sample floor. */
export function aggregatePlacement(estimates) {
  const list = (estimates ?? []).filter(Boolean);
  const meanBand = (idxs) => (idxs.length ? CEFR_LADDER[clamp(Math.round(idxs.reduce((s, x) => s + x, 0) / idxs.length), 0, 5)] : null);
  const dimensions = {};
  for (const d of ["fluency", "range", "accuracy", "coherence"]) {
    const idxs = list.map((e) => levelIndex(e?.dimensions?.[d]?.band)).filter((i) => i >= 0);
    dimensions[d] = meanBand(idxs);
  }
  const overall = meanBand(list.map((e) => levelIndex(e?.overall)).filter((i) => i >= 0));
  // Reliability grows with n (ETS RR-18-10: R≈.56 at 1 → .77 at ~6).
  const n = list.length;
  const confidence = n >= 5 ? "moderate" : n >= 3 ? "low-moderate" : "low";
  return { overall, dimensions, n, confidence };
}

/** Bucket a 0-100 score for UI colouring. */
export function band(score) {
  if (score == null) return "na";
  if (score >= 80) return "good";
  if (score >= 60) return "ok";
  return "weak";
}
