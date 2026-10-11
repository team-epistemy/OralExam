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
export interface AssessResult {
  transcript: string;
  scores: PronScores;
  metrics: SpeechMetrics;
  prosodyAvailable: boolean;
}
interface Word { word: string; start: number; duration: number; accuracy: number | null }
interface Phrase { text: string; scores: PronScores; words: Word[] }

const round1 = (n: number | null): number | null =>
  n == null || Number.isNaN(n) ? null : Math.round(n * 10) / 10;

function parsePhrase(json: any): Phrase | null {
  const nb = json?.NBest?.[0];
  if (!nb) return null;
  const pa = nb.PronunciationAssessment ?? {};
  const words: Word[] = (nb.Words ?? []).map((w: any) => ({
    word: w.Word,
    start: (w.Offset ?? 0) / TICKS_PER_SEC,
    duration: (w.Duration ?? 0) / TICKS_PER_SEC,
    accuracy: w.PronunciationAssessment?.AccuracyScore ?? null,
  }));
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

function aggregate(phrases: (Phrase | null)[]): AssessResult {
  const valid = phrases.filter((p): p is Phrase => !!p);
  const words = valid.flatMap((p) => p.words).sort((a, b) => a.start - b.start);
  const scores = {} as PronScores;
  (['accuracy', 'fluency', 'completeness', 'pron', 'prosody'] as const).forEach((k) => {
    scores[k] = round1(weightedMean(valid, k));
  });
  const pauses: number[] = [];
  for (let i = 1; i < words.length; i++) {
    const prevEnd = words[i - 1].start + words[i - 1].duration;
    const gap = words[i].start - prevEnd;
    if (gap >= PAUSE_MIN_SEC) pauses.push(gap);
  }
  const speakingTime = words.reduce((s, w) => s + w.duration, 0);
  const span = words.length ? words[words.length - 1].start + words[words.length - 1].duration - words[0].start : 0;
  const perMin = (count: number, secs: number) => (secs > 0 ? (count / secs) * 60 : null);
  return {
    transcript: valid.map((p) => p.text).join(' ').trim(),
    scores,
    prosodyAvailable: scores.prosody != null,
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
  onInterim?: (text: string) => void;
  onError?: (e: Error) => void;
}): Assessor {
  const { getToken, locale = 'en-US', onInterim = () => {}, onError = () => {} } = opts;
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
    recognizer.recognizing = (_s, e) => onInterim(e.result.text);
    recognizer.recognized = (_s, e) => {
      if (e.result.reason !== SDK.ResultReason.RecognizedSpeech) return;
      const raw = e.result.properties.getProperty(SDK.PropertyId.SpeechServiceResponse_JsonResult);
      try { phrases.push(parsePhrase(JSON.parse(raw))); } catch { /* skip malformed */ }
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
