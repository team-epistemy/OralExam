// Azure Pronunciation Assessment wrapper for the CEFR speaking exam (Phase 2).
// Ported and typed from the azure-prosody-demo: continuous recognition with
// prosody-on pronunciation assessment, plus acoustic metrics derived from word
// timings. The transcript is submitted as the answer (unchanged backend
// contract); the scores/metrics here are DISPLAY-ONLY (pronunciation is never
// folded into the CEFR band — accent-bias safeguard).
import * as SDK from 'microsoft-cognitiveservices-speech-sdk';

const TICKS_PER_SEC = 1e7;      // Azure reports Offset/Duration in 100-ns ticks
const PAUSE_MIN_SEC = 0.25;     // gaps >= this count as a pause
const LONG_PAUSE_SEC = 1.0;     // gaps >= this count as a long pause

// ── Transcription-reliability / intelligibility constants ───────────────────
// These drive result METADATA ONLY. Like pronunciation/prosody, nothing here is
// ever folded into the CEFR band (accent-bias safeguard): reliability tells an
// examiner "was the recognizer sure of what it heard?", it does NOT judge the
// speaker. The weights/thresholds below are PROVISIONAL first guesses and MUST
// be calibrated on real exam audio (store raw NBest + reliability server-side,
// then fit against human "was this transcript correct / comprehensible?" labels)
// before anyone treats a reliability number or a needs-review flag as trustworthy.

// Azure per-word AccuracyScore is 0–100; words below this read as "weakly
// recognized" (low acoustic confidence in that word). Provisional.
const WEAK_WORD_ACCURACY_THRESHOLD = 60;
// transcriptReliability = clamp01(confidenceMean − penalties). confidenceMean
// (Azure NBest[0].Confidence, 0–1) is the PRIMARY signal; the rest are penalties.
const RELIABILITY_WEAK_WORD_PENALTY = 0.25;  // × fraction of weakly-recognized words
const RELIABILITY_ERROR_PENALTY = 0.25;      // × per-word recognition error rate
const RELIABILITY_AMBIGUITY_PENALTY = 0.15;  // × NBest ambiguity (1 − top/2nd gap)
// Below this reliability we route the attempt to a human examiner. Deliberately
// conservative (high) — it is cheaper to over-refer than to let a mis-recognized
// answer be graded as if the words were certain. Provisional.
const RELIABILITY_REVIEW_THRESHOLD = 0.75;

const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

export interface PronScores {
  accuracy: number | null;
  fluency: number | null;
  completeness: number | null;
  pron: number | null;
  prosody: number | null; // absent for unsupported locales
}
export interface SpeechMetrics {
  wordCount: number;
  durationSec: number | null;
  speechRateWpm: number | null;
  articulationRateWpm: number | null;
  pauseCount: number;
  longPauseCount: number;
  meanPauseSec: number;
  pauseRatio: number | null; // % of time not speaking
}
// Each word plus the silent gap (seconds) immediately before it (0 if none/below
// the pause threshold) — lets the transcript show inline pause markers.
export interface TimedWord { word: string; gapBefore: number }

// Counts of Azure per-word recognition ErrorType across the answer.
export interface ErrorTypeCounts {
  omission: number;
  insertion: number;
  mispronunciation: number;
  none: number;
}

// Transcription-reliability / intelligibility METADATA. NEVER fed into the CEFR
// band — this measures how confident the RECOGNIZER was, not how good the
// speaker is. Used only to flag attempts for examiner review. Null (not 0) means
// "unmeasured": Azure returned no confidence signal, so we must not pretend the
// transcript scored zero reliability — we route it to a human instead.
export interface TranscriptReliability {
  confidenceMean: number | null;      // word-weighted mean of NBest[0].Confidence (0–1)
  weakWordRate: number | null;        // fraction of words below the weak-word accuracy threshold
  errorTypeCounts: ErrorTypeCounts;   // per-word ErrorType tally
  nbestSpread: number | null;         // mean top-vs-2nd NBest confidence gap (1 = unambiguous)
  transcriptReliability: number | null; // conservative 0–1 combination (see constants)
  intelligibility: number | null;     // PLACEHOLDER: future learned comprehensibility head; always null now
  needsReview: boolean;               // examiner-routing trigger
  needsReviewReason: string | null;   // short machine reason (e.g. 'low_reliability')
}

export interface AssessResult {
  transcript: string;
  scores: PronScores;
  metrics: SpeechMetrics;
  words: TimedWord[];
  prosodyAvailable: boolean;
  reliability: TranscriptReliability;
  // Raw per-phrase Azure NBest arrays — the ground truth we persist server-side
  // to later calibrate the provisional reliability weights against human labels.
  rawNBest: unknown[];
}
interface Word { word: string; start: number; duration: number; accuracy: number | null; errorType: string | null }
// confidence: NBest[0].Confidence (0–1). nbestSpread: gap between the top and
// second recognition hypotheses' confidence (1 when there is no 2nd hypothesis —
// a single candidate is treated as unambiguous). nbest: the raw NBest array, kept
// for server-side persistence/calibration.
interface Phrase { text: string; scores: PronScores; words: Word[]; confidence: number | null; nbestSpread: number | null; nbest: unknown }

const round1 = (n: number | null): number | null =>
  n == null || Number.isNaN(n) ? null : Math.round(n * 10) / 10;

function parsePhrase(json: any): Phrase | null {
  const nbest = json?.NBest;
  const nb = nbest?.[0];
  if (!nb) return null;
  const pa = nb.PronunciationAssessment ?? {};
  const words: Word[] = (nb.Words ?? []).map((w: any) => ({
    word: w.Word,
    start: (w.Offset ?? 0) / TICKS_PER_SEC,
    duration: (w.Duration ?? 0) / TICKS_PER_SEC,
    accuracy: w.PronunciationAssessment?.AccuracyScore ?? null,
    // ErrorType is Azure's per-word recognition/assessment verdict
    // (None | Omission | Insertion | Mispronunciation). Kept as-is; null when absent.
    errorType: w.PronunciationAssessment?.ErrorType ?? null,
  }));
  // NBest[0].Confidence (0–1): how sure Azure was of the top hypothesis. Requires
  // Detailed output (already enabled). Previously dropped on the floor here.
  const confidence = typeof nb.Confidence === 'number' ? nb.Confidence : null;
  // Ambiguity signal: the confidence gap to the 2nd-best hypothesis. A large gap
  // means one clear winner; a small gap means the recognizer was torn between
  // candidates. With no 2nd hypothesis there is nothing to be torn between → 1.
  const c0 = confidence;
  const c1 = typeof nbest?.[1]?.Confidence === 'number' ? nbest[1].Confidence : null;
  const nbestSpread = c0 == null ? null : (c1 == null ? 1 : clamp01(c0 - c1));
  return {
    text: nb.Display ?? json.DisplayText ?? words.map((w) => w.word).join(' '),
    scores: {
      accuracy: pa.AccuracyScore ?? null,
      fluency: pa.FluencyScore ?? null,
      completeness: pa.CompletenessScore ?? null,
      pron: pa.PronScore ?? null,
      prosody: pa.ProsodyScore ?? null,
    },
    words,
    confidence,
    nbestSpread,
    nbest: nbest ?? null,
  };
}

function weightedMean(phrases: Phrase[], key: keyof PronScores): number | null {
  let num = 0, den = 0;
  for (const p of phrases) {
    const v = p.scores[key];
    if (v == null) continue;
    const w = Math.max(p.words.length, 1);
    num += v * w; den += w;
  }
  return den ? num / den : null;
}

// Word-count-weighted mean of a per-phrase scalar (reuses the weightedMean
// weighting: each phrase weighted by its word count, min 1). Returns null — not
// 0 — when nothing was measured, preserving the unmeasured-vs-zero distinction.
function weightedByWords(phrases: Phrase[], pick: (p: Phrase) => number | null): number | null {
  let num = 0, den = 0;
  for (const p of phrases) {
    const v = pick(p);
    if (v == null) continue;
    const w = Math.max(p.words.length, 1);
    num += v * w; den += w;
  }
  return den ? num / den : null;
}

// Derive the transcription-reliability METADATA from the parsed phrases.
// METADATA ONLY — never folded into the CEFR band (see the constants block).
function computeReliability(valid: Phrase[], words: Word[]): TranscriptReliability {
  // Primary signal: word-weighted mean recognizer confidence (0–1), or null if
  // Azure gave us no Confidence at all on any phrase.
  const confidenceMean = weightedByWords(valid, (p) => p.confidence);

  // Weak words: per-word AccuracyScore below the threshold, over words that HAVE
  // an accuracy score. null when no word carried an accuracy score (unmeasured).
  const scored = words.filter((w) => w.accuracy != null);
  const weakWordRate = scored.length
    ? scored.filter((w) => (w.accuracy as number) < WEAK_WORD_ACCURACY_THRESHOLD).length / scored.length
    : null;

  // Per-word ErrorType tally (counts only; "None" and unknown-but-present both
  // count toward `none`). Words with no ErrorType field at all are skipped.
  const errorTypeCounts: ErrorTypeCounts = { omission: 0, insertion: 0, mispronunciation: 0, none: 0 };
  let typedWords = 0;
  for (const w of words) {
    if (w.errorType == null) continue;
    typedWords++;
    switch (w.errorType) {
      case 'Omission': errorTypeCounts.omission++; break;
      case 'Insertion': errorTypeCounts.insertion++; break;
      case 'Mispronunciation': errorTypeCounts.mispronunciation++; break;
      default: errorTypeCounts.none++; break;
    }
  }
  // Recognition error rate: fraction of typed words that were NOT "None". null
  // when no word carried an ErrorType.
  const errorRate = typedWords
    ? (errorTypeCounts.omission + errorTypeCounts.insertion + errorTypeCounts.mispronunciation) / typedWords
    : null;

  // Mean NBest confidence gap (1 = one clear winner, 0 = fully ambiguous).
  const nbestSpread = weightedByWords(valid, (p) => p.nbestSpread);

  // Conservative combination: start from confidence, subtract penalties. Penalty
  // terms contribute 0 when their signal is unmeasured (null) so a missing signal
  // never silently inflates OR deflates the score.
  let transcriptReliability: number | null = null;
  if (confidenceMean != null) {
    const weakPenalty = RELIABILITY_WEAK_WORD_PENALTY * (weakWordRate ?? 0);
    const errorPenalty = RELIABILITY_ERROR_PENALTY * (errorRate ?? 0);
    // Ambiguity = 1 − spread; with no spread signal, assume no ambiguity (0).
    const ambiguity = nbestSpread == null ? 0 : (1 - nbestSpread);
    const ambiguityPenalty = RELIABILITY_AMBIGUITY_PENALTY * ambiguity;
    transcriptReliability = clamp01(confidenceMean - weakPenalty - errorPenalty - ambiguityPenalty);
  }

  // needsReview: route to a human when reliability is below the conservative
  // threshold, OR when we could not measure reliability at all (unmeasured is NOT
  // treated as reliable — it is treated as needing eyes).
  let needsReview = false;
  let needsReviewReason: string | null = null;
  if (transcriptReliability == null) {
    needsReview = true;
    needsReviewReason = 'reliability_unmeasured';
  } else if (transcriptReliability < RELIABILITY_REVIEW_THRESHOLD) {
    needsReview = true;
    needsReviewReason = 'low_reliability';
  }

  return {
    confidenceMean,
    weakWordRate,
    errorTypeCounts,
    nbestSpread,
    transcriptReliability,
    // Placeholder for the future learned comprehensibility head (NOT built here).
    intelligibility: null,
    needsReview,
    needsReviewReason,
  };
}

function aggregate(phrases: (Phrase | null)[]): AssessResult {
  const valid = phrases.filter((p): p is Phrase => !!p);
  const words = valid.flatMap((p) => p.words).sort((a, b) => a.start - b.start);
  const scores = {} as PronScores;
  (['accuracy', 'fluency', 'completeness', 'pron', 'prosody'] as const).forEach((k) => {
    scores[k] = round1(weightedMean(valid, k));
  });
  const pauses: number[] = [];
  const timedWords: { word: string; gapBefore: number }[] = [];
  for (let i = 0; i < words.length; i++) {
    let gapBefore = 0;
    if (i > 0) {
      const prevEnd = words[i - 1].start + words[i - 1].duration;
      const gap = words[i].start - prevEnd;
      if (gap >= PAUSE_MIN_SEC) { pauses.push(gap); gapBefore = round1(gap)!; }
    }
    timedWords.push({ word: words[i].word, gapBefore });
  }
  const speakingTime = words.reduce((s, w) => s + w.duration, 0);
  const span = words.length ? words[words.length - 1].start + words[words.length - 1].duration - words[0].start : 0;
  const perMin = (count: number, secs: number) => (secs > 0 ? (count / secs) * 60 : null);
  return {
    transcript: valid.map((p) => p.text).join(' ').trim(),
    scores,
    words: timedWords,
    prosodyAvailable: scores.prosody != null,
    reliability: computeReliability(valid, words),
    rawNBest: valid.map((p) => p.nbest),
    metrics: {
      wordCount: words.length,
      durationSec: round1(span),
      speechRateWpm: round1(perMin(words.length, span)),
      articulationRateWpm: round1(perMin(words.length, speakingTime)),
      pauseCount: pauses.length,
      longPauseCount: pauses.filter((g) => g >= LONG_PAUSE_SEC).length,
      meanPauseSec: pauses.length ? round1(pauses.reduce((s, g) => s + g, 0) / pauses.length)! : 0,
      pauseRatio: span > 0 ? round1(((span - speakingTime) / span) * 100) : null,
    },
  };
}

export interface Assessor {
  start(): Promise<void>;
  stop(): Promise<AssessResult>;
}

/** Build an Azure assessor. `getToken` returns {token, region} from our backend. */
export function createAssessor(opts: {
  getToken: () => Promise<{ token: string; region: string }>;
  locale?: string;
  onInterim?: (text: string) => void;   // current phrase, partial (streams live)
  onPhrase?: (text: string) => void;    // a finalized phrase (append to build the transcript)
  onError?: (e: Error) => void;
}): Assessor {
  const { getToken, locale = 'en-US', onInterim = () => {}, onPhrase = () => {}, onError = () => {} } = opts;
  let recognizer: SDK.SpeechRecognizer | null = null;
  let phrases: (Phrase | null)[] = [];
  let stopped: Promise<void> | null = null;

  async function start() {
    phrases = [];
    const { token, region } = await getToken();
    const speechConfig = SDK.SpeechConfig.fromAuthorizationToken(token, region);
    speechConfig.speechRecognitionLanguage = locale;
    speechConfig.outputFormat = SDK.OutputFormat.Detailed;
    const pron = new SDK.PronunciationAssessmentConfig(
      '', SDK.PronunciationAssessmentGradingSystem.HundredMark,
      SDK.PronunciationAssessmentGranularity.Phoneme, false);
    (pron as any).enableProsodyAssessment = true;
    const audioConfig = SDK.AudioConfig.fromDefaultMicrophoneInput();
    recognizer = new SDK.SpeechRecognizer(speechConfig, audioConfig);
    pron.applyTo(recognizer);

    stopped = new Promise<void>((resolve) => {
      recognizer!.sessionStopped = () => resolve();
      recognizer!.canceled = (_s, e) => {
        if (e.reason === SDK.CancellationReason.Error) onError(new Error(e.errorDetails || 'Recognition canceled'));
        resolve();
      };
    });
    // `recognizing` streams the current phrase's partial text in real time.
    recognizer.recognizing = (_s, e) => onInterim(e.result.text);
    // `recognized` carries the finalized phrase AND the full pronunciation JSON
    // (accuracy/fluency/prosody/word timings). We surface the finalized text for
    // live display (onPhrase) and keep the JSON for scoring — the two are
    // independent, so live streaming never affects the acoustic features.
    recognizer.recognized = (_s, e) => {
      if (e.result.reason !== SDK.ResultReason.RecognizedSpeech) return;
      const raw = e.result.properties.getProperty(SDK.PropertyId.SpeechServiceResponse_JsonResult);
      try { phrases.push(parsePhrase(JSON.parse(raw))); } catch { /* skip malformed */ }
      if (e.result.text) onPhrase(e.result.text);
    };
    await new Promise<void>((resolve, reject) =>
      recognizer!.startContinuousRecognitionAsync(() => resolve(), (e) => reject(new Error(e))));
  }

  async function stop(): Promise<AssessResult> {
    if (!recognizer) return aggregate(phrases);
    await new Promise<void>((resolve) => recognizer!.stopContinuousRecognitionAsync(() => resolve(), () => resolve()));
    await Promise.race([stopped ?? Promise.resolve(), new Promise((r) => setTimeout(r, 3000))]);
    recognizer.close();
    recognizer = null;
    return aggregate(phrases);
  }

  return { start, stop };
}
