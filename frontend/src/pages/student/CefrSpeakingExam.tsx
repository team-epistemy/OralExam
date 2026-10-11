import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Mic, Square, PlayCircle, Send, Volume2, VolumeX, Loader2, RotateCcw } from 'lucide-react';
import { API_BASE_URL } from '../../config';
import { post } from '../../api/client';
import { startExamSession, submitAnswer, completeSession, getCefrCalibration, saveCefrCalibration } from '../../api/exam';
import type { CefrTurn, CefrBandName, SpeechEvidence } from '../../api/exam';
import { createAssessor } from '../../speech/azureAssessor';
import type { Assessor, AssessResult } from '../../speech/azureAssessor';

// Dedicated CEFR speaking-test flow. Reuses the tested backend communication/
// engine (Table-3 grading + A1-C2 ladder). Capture is record-only (Azure
// transcription; Web-Speech text fallback when Azure isn't configured) — no
// typing. Pronunciation/acoustic metrics are display-only (never fold into the
// CEFR band). The final transcript lists every prompt with response, pauses,
// per-dimension scores, a human calibration control, the band, and the rationale.

type Phase = 'loading' | 'ready' | 'taking' | 'done' | 'error';
interface AnswerRecord {
  turnNumber: number;
  prompt: string;
  pron: AssessResult | null; // transcript + words/pauses + pronunciation
  cefr: CefrTurn | null;
  feedback: string;          // grader's one-line "why this score"
  audioUrl?: string;
}

const BAND_NAMES: CefrBandName[] = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
const numToBand = (n: number | null | undefined): CefrBandName | '—' =>
  n == null ? '—' : (BAND_NAMES[Math.min(5, Math.max(0, Math.round(n) - 1))] ?? '—');

function bandClasses(band: string | null | undefined): string {
  if (!band || band === '—') return 'bg-gray-100 text-gray-500 border-gray-200';
  if (band.startsWith('C') || band === 'B2') return 'bg-green-50 text-green-700 border-green-200';
  if (band === 'B1') return 'bg-blue-50 text-blue-700 border-blue-200';
  return 'bg-amber-50 text-amber-700 border-amber-200';
}
const DIRECTION_LABEL: Record<CefrTurn['direction'], string> = { up: '↑ harder', same: '→ holding', down: '↓ easier' };
const CRITERIA: Array<keyof CefrTurn['scores']> = ['range', 'accuracy', 'fluency', 'coherence', 'interaction'];
const CRITERION_LABEL: Record<string, string> = {
  range: 'Range', accuracy: 'Accuracy', fluency: 'Fluency', coherence: 'Coherence', interaction: 'Interaction',
};
const METRIC_DEFS: Array<[string, string]> = [
  ['Range', 'The variety of vocabulary and grammar you draw on.'],
  ['Accuracy', 'How grammatically correct your speech is.'],
  ['Fluency', 'How smoothly you speak — your pace and pausing.'],
  ['Coherence', 'How well-organised and connected your ideas are.'],
  ['Interaction', 'How you handle the back-and-forth (assessed on the follow-ups).'],
];
const TOTAL_TURNS = 5;

// The examiner's spoken welcome. Shown on screen and read aloud by the same TTS
// voice that asks the questions, so the student hears the examiner before the
// first question rather than meeting the voice mid-test. Returned as paragraphs
// for layout and joined for speech.
function welcomeScript(topic: string): string[] {
  const subject = topic ? topic.toLowerCase() : 'a familiar topic';
  return [
    'Hello, and welcome. I’m the examiner for today’s speaking test.',
    `Over the next ten to fifteen minutes, I’ll ask you questions about ${subject}. ` +
    'Some will be short. Others will ask you to explain your thinking in more detail, ' +
    'and I may follow up on what you say.',
    'There’s no need to rush. Take a moment to think before you answer. ' +
    'Try to answer in full sentences and say as much as you can.',
    'This session is being recorded so your responses can be reviewed.',
    'When you’re ready, click Begin and we’ll start with the first question.',
  ];
}

// Flatten the assessor's result into the shape the grader reads. Sent with the
// answer so Fluency is judged on measured pausing and tempo rather than on a
// transcript, which has already discarded both. Returns undefined in text mode,
// and the backend then falls back to transcript-only grading.
function toSpeechEvidence(pron: AssessResult | null): SpeechEvidence | undefined {
  if (!pron) return undefined;
  const { scores: s, metrics: m } = pron;
  return {
    pron_overall: s.pron, pron_accuracy: s.accuracy, pron_fluency: s.fluency,
    pron_completeness: s.completeness, prosody: s.prosody,
    word_count: m.wordCount, duration_sec: m.durationSec,
    speech_rate_wpm: m.speechRateWpm,
    articulation_rate_wpm: m.articulationRateWpm,
    pause_count: m.pauseCount, long_pause_count: m.longPauseCount,
    mean_pause_sec: m.meanPauseSec, pause_ratio: m.pauseRatio,
  };
}

// MediaRecorder WebM has no duration header, so <audio>.duration is Infinity and
// playback looks truncated; force the browser to compute it once on load.
function AudioReplay({ src }: { src: string }) {
  const onMeta = (e: React.SyntheticEvent<HTMLAudioElement>) => {
    const el = e.currentTarget;
    if (el.duration === Infinity || Number.isNaN(el.duration)) {
      const onSeek = () => { el.removeEventListener('timeupdate', onSeek); el.currentTime = 0; };
      el.addEventListener('timeupdate', onSeek);
      el.currentTime = 1e7;
    }
  };
  return <audio src={src} controls preload="auto" onLoadedMetadata={onMeta} className="h-8 mt-1" />;
}

// Response with inline pause markers (#6c).
function ResponseWithPauses({ pron }: { pron: AssessResult | null }) {
  if (!pron || !pron.words.length) return <p className="text-sm text-gray-500 italic">No word timings.</p>;
  return (
    <p className="text-sm leading-7">
      {pron.words.map((w, i) => (
        <span key={i}>
          {w.gapBefore > 0 && <span className={`mx-1 px-1 rounded text-[11px] ${w.gapBefore >= 1 ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-500'}`}>⏸ {w.gapBefore}s</span>}
          {w.word}{' '}
        </span>
      ))}
    </p>
  );
}

export default function CefrSpeakingExam() {
  const { assignmentId } = useParams<{ assignmentId: string }>();
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>('loading');
  const [error, setError] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [topic, setTopic] = useState('');
  // Browsers block audio that plays without a user gesture, so the welcome may
  // be refused on arrival. When that happens the student gets a button to play
  // it rather than silently missing the examiner's instructions.
  const [welcomeBlocked, setWelcomeBlocked] = useState(false);
  const [history, setHistory] = useState<CefrTurn[]>([]);
  const [answers, setAnswers] = useState<AnswerRecord[]>([]);
  const [captured, setCaptured] = useState<AssessResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recording, setRecording] = useState(false);
  const [azureMode, setAzureMode] = useState(false);
  const [ttsOn, setTtsOn] = useState(true);
  const [ttsAvailable, setTtsAvailable] = useState(true);
  const [micNotice, setMicNotice] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const assessorRef = useRef<Assessor | null>(null);
  const recognitionRef = useRef<any>(null);
  const webTextRef = useRef('');
  const mediaRecRef = useRef<MediaRecorder | null>(null);
  const recChunksRef = useRef<Blob[]>([]);
  const recStreamRef = useRef<MediaStream | null>(null);
  const pendingAudioUrl = useRef<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number>(0);

  // ── TTS (ElevenLabs proxy) ───────────────────────────────────────────────
  // Returns false when nothing was spoken — TTS unavailable, or the browser
  // refused playback for want of a user gesture. The welcome needs to know,
  // because a student who silently never hears it has lost the instructions.
  const speak = useCallback(async (text: string): Promise<boolean> => {
    if (!ttsOn || !text) return false;
    try {
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_BASE_URL}/api/tts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) { if (resp.status === 503) setTtsAvailable(false); return false; }
      setTtsAvailable(true);
      const audio = new Audio(URL.createObjectURL(await resp.blob()));
      audioRef.current?.pause();
      audioRef.current = audio;
      try {
        await audio.play();
        return true;
      } catch {
        return false;      // autoplay refused
      }
    } catch {
      return false;
    }
  }, [ttsOn]);
  const stopSpeech = useCallback(() => { if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; } }, []);

  // ── Local audio capture for replay (session-only) ──────────────────────────
  const startLocalRecorder = useCallback(async () => {
    recChunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recStreamRef.current = stream;
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => { if (e.data?.size) recChunksRef.current.push(e.data); };
      mr.start();
      mediaRecRef.current = mr;
    } catch { mediaRecRef.current = null; }
  }, []);
  const stopLocalRecorder = useCallback((): Promise<string | null> => new Promise((resolve) => {
    const mr = mediaRecRef.current;
    const cleanup = () => { recStreamRef.current?.getTracks().forEach((t) => t.stop()); recStreamRef.current = null; };
    if (!mr || mr.state === 'inactive') { cleanup(); resolve(null); return; }
    mr.onstop = () => {
      const blob = new Blob(recChunksRef.current, { type: mr.mimeType || 'audio/webm' });
      cleanup();
      resolve(blob.size ? URL.createObjectURL(blob) : null);
    };
    mr.stop();
  }), []);

  // A lightweight live waveform driven by the mic level — tells the student their
  // speech is being heard/processed, without the distracting partial transcript.
  // Recognition still runs underneath for scoring; we just don't render the text.
  const startWaveform = useCallback(() => {
    const stream = recStreamRef.current;
    if (!stream) return;
    try {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      audioCtxRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 128;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const draw = () => {
        rafRef.current = requestAnimationFrame(draw);
        const cv = canvasRef.current; const g = cv?.getContext('2d');
        if (!cv || !g) return;
        analyser.getByteFrequencyData(data);
        const W = cv.width, H = cv.height;
        g.clearRect(0, 0, W, H);
        const bars = 28, step = Math.max(1, Math.floor(data.length / bars)), bw = W / bars;
        g.fillStyle = '#2563eb';
        for (let i = 0; i < bars; i++) {
          const h = Math.max(2, (data[i * step] / 255) * H);
          g.fillRect(i * bw + 1, (H - h) / 2, bw - 2, h);
        }
      };
      draw();
    } catch { /* no AudioContext: skip the waveform, recording still works */ }
  }, []);
  const stopWaveform = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = 0;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    const cv = canvasRef.current; cv?.getContext('2d')?.clearRect(0, 0, cv.width, cv.height);
  }, []);

  // ── Web Speech fallback (text only; used when Azure isn't configured) ──────
  const startWebSpeech = () => {
    const SR = window as unknown as { SpeechRecognition?: new () => any; webkitSpeechRecognition?: new () => any };
    const Ctor = SR.SpeechRecognition || SR.webkitSpeechRecognition;
    if (!Ctor) { setMicNotice("Voice input isn't supported in this browser — try Chrome, Edge, or Safari."); return false; }
    const rec: any = new Ctor();
    rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = true;
    webTextRef.current = '';
    rec.onresult = (e: any) => {
      let interim = '', finalT = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalT += t; else interim += t;
      }
      if (finalT) webTextRef.current = (webTextRef.current ? webTextRef.current + ' ' : '') + finalT.trim();
    };
    rec.onerror = () => { setRecording(false); setMicNotice('Voice input had a problem — press Record to try again.'); };
    recognitionRef.current = rec;
    try { rec.start(); return true; } catch { setMicNotice('Could not start the microphone.'); return false; }
  };

  // ── Record start/stop ──────────────────────────────────────────────────────
  const startRecording = async () => {
    stopSpeech(); setMicNotice(null); setCaptured(null);
    if (pendingAudioUrl.current) { URL.revokeObjectURL(pendingAudioUrl.current); pendingAudioUrl.current = null; }
    if (azureMode) {
      try {
        const assessor = createAssessor({
          getToken: async () => { const r = await post<{ token: string; region: string }>('/api/speech/token'); return { token: r.token, region: r.region }; },
          // Recognition still runs for scoring; we no longer render the partial
          // transcript (the waveform indicates speech is being heard instead).
          onError: (e) => setMicNotice(`Speech error: ${e.message}`),
        });
        await assessor.start();
        assessorRef.current = assessor;
        await startLocalRecorder();
        startWaveform();
        setRecording(true);
      } catch (e: any) { setMicNotice(`Could not start recording (${e?.message || 'error'}). Press Record to try again.`); }
    } else {
      if (startWebSpeech()) { await startLocalRecorder(); startWaveform(); setRecording(true); }
    }
  };

  const stopRecording = async () => {
    setRecording(false);
    stopWaveform();
    pendingAudioUrl.current = await stopLocalRecorder();
    if (azureMode && assessorRef.current) {
      try {
        const res = await assessorRef.current.stop();
        assessorRef.current = null;
        if (res.transcript) setCaptured(res);
        else setMicNotice('No speech was captured — press Record to try again.');
      } catch (e: any) { setMicNotice(`Transcription failed (${e?.message || 'error'}) — press Record to try again.`); }
    } else if (recognitionRef.current) {
      try { recognitionRef.current.onend = null; recognitionRef.current.stop(); } catch { /* ok */ }
      recognitionRef.current = null;
      const text = webTextRef.current.trim();
      if (text) setCaptured({ transcript: text, scores: { accuracy: null, fluency: null, completeness: null, pron: null, prosody: null },
        words: text.split(/\s+/).map((w) => ({ word: w, gapBefore: 0 })), prosodyAvailable: false,
        metrics: { wordCount: text.split(/\s+/).length, durationSec: null, speechRateWpm: null, articulationRateWpm: null, pauseCount: 0, longPauseCount: 0, meanPauseSec: 0, pauseRatio: null } });
      else setMicNotice('No speech was captured — press Record to try again.');
    }
  };

  // ── Session ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!assignmentId) return;
    let cancelled = false;
    startExamSession(assignmentId)
      .then((res) => {
        if (cancelled) return;
        setSessionId(res.session_id);
        setPrompt(res.questions[0]?.text || 'Please speak on the topic.');
        setTopic(res.theme || '');
        setPhase('ready');
      })
      .catch((e) => { if (!cancelled) { setError(e?.message || 'Could not start the exam.'); setPhase('error'); } });
    return () => { cancelled = true; };
  }, [assignmentId]);
  useEffect(() => () => { stopSpeech(); recStreamRef.current?.getTracks().forEach((t) => t.stop()); }, [stopSpeech]);

  // Read the welcome aloud once the session is ready. Spoken by the same voice
  // that asks the questions, so the examiner is established before turn 1.
  const spokenWelcomeRef = useRef(false);
  useEffect(() => {
    if (phase !== 'ready' || spokenWelcomeRef.current) return;
    spokenWelcomeRef.current = true;
    speak(welcomeScript(topic).join(' ')).then((ok) => setWelcomeBlocked(!ok));
  }, [phase, topic, speak]);

  const begin = async () => {
    // Cut the welcome off cleanly: the student clicked Begin, so the examiner
    // must not still be reading the instructions over the first question.
    stopSpeech();
    try { await post('/api/speech/token'); setAzureMode(true); } catch { setAzureMode(false); }
    setPhase('taking');
    speak(prompt);
  };

  const submit = async () => {
    if (!captured?.transcript || submitting || recording) return;
    stopSpeech();
    setSubmitting(true);
    const answer = captured.transcript;
    const pron = captured;
    const audioUrl = pendingAudioUrl.current ?? undefined;
    const thisPrompt = prompt;
    pendingAudioUrl.current = null;
    setCaptured(null);
    try {
      const resp = await submitAnswer(sessionId, 0, answer, toSpeechEvidence(pron),
                                      pron?.words?.map((w) => ({ w: w.word, g: w.gapBefore })));
      if (resp.cefr) setHistory((h) => [...h, resp.cefr!]);
      setAnswers((a) => [...a, {
        turnNumber: resp.cefr?.turn_number ?? a.length + 1,
        prompt: thisPrompt, pron, cefr: resp.cefr ?? null, feedback: resp.feedback || '', audioUrl,
      }]);
      if (!resp.probe) {
        try { await completeSession(sessionId); } catch { /* best-effort */ }
        try { const ov = await getCefrCalibration(sessionId); setOverrides(Object.fromEntries(ov.map((o) => [`${o.turn_number}:${o.dimension}`, o.human_band]))); } catch { /* ignore */ }
        setPhase('done');
      } else {
        setPrompt(resp.probe);
        speak(resp.probe);
      }
    } catch (e: any) { setError(e?.message || 'Could not submit your answer.'); setPhase('error'); }
    finally { setSubmitting(false); }
  };

  const saveOverride = async (turnNumber: number, dimension: string, human: string, machine: string | null) => {
    setOverrides((o) => ({ ...o, [`${turnNumber}:${dimension}`]: human }));
    try { await saveCefrCalibration({ session_id: sessionId, turn_number: turnNumber, dimension, human_band: human, machine_band: machine }); }
    catch { setMicNotice('Could not save the calibration — check your connection.'); }
  };

  const latest = history[history.length - 1];
  const turnNo = answers.length;

  // ── Render ───────────────────────────────────────────────────────────────
  if (phase === 'loading') return <div className="flex items-center gap-2 text-gray-500 p-8"><Loader2 className="w-5 h-5 animate-spin" /> Loading your speaking test…</div>;
  if (phase === 'error') return (
    <div className="max-w-xl mx-auto p-8 space-y-4">
      <p className="text-red-600">{error}</p>
      <button onClick={() => navigate(-1)} className="px-4 py-2 border border-gray-300 rounded-lg text-sm">Go back</button>
    </div>
  );

  // Screen 1 — instructions + metric definitions only (no prompt).
  if (phase === 'ready') return (
    <div className="max-w-2xl mx-auto p-6 space-y-5">
      <h1 className="text-2xl font-bold text-gray-900">CEFR Speaking Test</h1>

      {/* The examiner's welcome, displayed and spoken together. */}
      <div className="bg-white rounded-xl border-2 border-blue-200 p-6 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="inline-flex items-center gap-2 text-xs uppercase tracking-wide text-blue-600">
            <Volume2 className="w-3.5 h-3.5" /> The examiner
          </h2>
          {welcomeBlocked && ttsAvailable && (
            <button
              onClick={() => speak(welcomeScript(topic).join(' ')).then((ok) => setWelcomeBlocked(!ok))}
              className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 border border-blue-300 text-blue-700 rounded-lg hover:bg-blue-50"
            >
              <PlayCircle className="w-3.5 h-3.5" /> Play instructions
            </button>
          )}
        </div>
        {welcomeScript(topic).map((para, i) => (
          <p key={i} className="text-[15px] leading-relaxed text-gray-800">{para}</p>
        ))}
      </div>

      <div className="bg-white rounded-xl border-2 border-gray-200 p-5 space-y-3">
        <h2 className="text-xs uppercase tracking-wide text-gray-500">How this works</h2>
        <p className="text-sm text-gray-700">
          You'll answer one longer question, then four short follow-ups that adapt up or down the CEFR scale based on how you do.
          Each answer is <b>recorded and transcribed</b> (there's no typing) and graded on the CEFR grid below.
        </p>
        <h2 className="text-xs uppercase tracking-wide text-gray-500 pt-2">What's assessed</h2>
        <dl className="space-y-1.5">
          {METRIC_DEFS.map(([k, v]) => (
            <div key={k} className="text-sm"><dt className="inline font-semibold text-gray-900">{k}: </dt><dd className="inline text-gray-600">{v}</dd></div>
          ))}
        </dl>
        <p className="text-xs text-gray-400 pt-1">Pronunciation is also shown for reference but does not set your CEFR level.</p>
      </div>
      <button onClick={begin} className="inline-flex items-center gap-2 px-5 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700">
        <PlayCircle className="w-5 h-5" /> Begin
      </button>
    </div>
  );

  // Done — detailed per-prompt transcript (#6).
  if (phase === 'done') {
    const final = history[history.length - 1];
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Speaking test complete</h1>
        {final && (
          <div className="bg-white rounded-xl border-2 border-gray-200 p-5">
            <div className="flex items-center gap-4">
              <div className={`text-4xl font-bold px-5 py-3 rounded-xl border ${bandClasses(final.overall_band)}`}>{final.overall_band}</div>
              <div className="text-sm text-gray-600"><div>Overall CEFR (averaged across {answers.length} answers)</div></div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-4">
              {CRITERIA.map((c) => (
                <div key={c} className={`rounded-lg border px-3 py-2 text-center ${bandClasses(numToBand(final.criterion_averages[c]))}`}>
                  <div className="text-lg font-semibold">{numToBand(final.criterion_averages[c])}</div>
                  <div className="text-[11px]">{CRITERION_LABEL[c]}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {answers.map((a, i) => {
          const sc = a.cefr?.scores ?? {};
          const machineFor = (dim: string): string => dim === 'overall'
            ? (a.cefr?.turn_band_name ?? '—')
            : (numToBand((sc as any)[dim]) ?? '—');
          return (
            <div key={i} className="bg-white rounded-xl border-2 border-gray-200 p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-900">Prompt {i + 1}</h2>
                <span className={`text-sm font-bold border rounded-lg px-3 py-1 ${bandClasses(a.cefr?.turn_band_name)}`}>Band {a.cefr?.turn_band_name ?? '—'}</span>
              </div>
              <p className="text-sm text-blue-700">{a.prompt}</p>

              <div><div className="text-[11px] uppercase tracking-wide text-gray-400 mb-0.5">Response</div>
                <p className="text-sm text-gray-800">{a.pron?.transcript || '(no speech captured)'}</p></div>

              <div><div className="text-[11px] uppercase tracking-wide text-gray-400 mb-0.5">Response with pauses</div>
                <ResponseWithPauses pron={a.pron} /></div>

              {a.pron && (a.pron.scores.accuracy != null || a.pron.metrics.speechRateWpm != null) && (
                <div className="text-[11px] text-gray-500">
                  <span className="uppercase tracking-wide text-gray-400">Pronunciation (Azure, reference): </span>
                  {a.pron.scores.accuracy != null && <>accuracy {Math.round(a.pron.scores.accuracy)} · </>}
                  {a.pron.scores.fluency != null && <>fluency {Math.round(a.pron.scores.fluency)} · </>}
                  {a.pron.scores.prosody != null && <>prosody {Math.round(a.pron.scores.prosody)} · </>}
                  {a.pron.metrics.speechRateWpm != null && <>{a.pron.metrics.speechRateWpm} wpm · </>}
                  {a.pron.metrics.pauseCount != null && <>{a.pron.metrics.pauseCount} pauses</>}
                  <span className="text-gray-400"> — not part of the CEFR level</span>
                </div>
              )}

              <div><div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Scores &amp; calibration</div>
                <div className="space-y-1">
                  {(['overall', ...CRITERIA] as string[]).map((dim) => {
                    const machine = machineFor(dim);
                    const key = `${a.turnNumber}:${dim}`;
                    const human = overrides[key] ?? '';
                    const na = dim !== 'overall' && (sc as any)[dim] == null;
                    return (
                      <div key={dim} className="flex items-center gap-2 text-sm">
                        <span className="w-24 text-gray-600">{dim === 'overall' ? 'Overall' : CRITERION_LABEL[dim]}</span>
                        <span className={`text-xs font-semibold border rounded px-2 ${bandClasses(na ? '—' : machine)}`}>{na ? 'n/a' : machine}</span>
                        <span className="text-[11px] text-gray-400">machine</span>
                        <span className="ml-auto text-[11px] text-gray-400">your rating:</span>
                        <select value={human} disabled={na}
                          onChange={(e) => saveOverride(a.turnNumber, dim, e.target.value, na ? null : machine)}
                          className="text-xs border border-gray-300 rounded px-1 py-0.5 bg-white disabled:opacity-40">
                          <option value="">—</option>
                          {BAND_NAMES.map((b) => <option key={b}>{b}</option>)}
                        </select>
                      </div>
                    );
                  })}
                </div>
              </div>

              {a.feedback && <div><div className="text-[11px] uppercase tracking-wide text-gray-400 mb-0.5">Why this score</div>
                <p className="text-sm text-gray-600 italic">{a.feedback}</p></div>}

              {a.audioUrl && <div><div className="text-[11px] uppercase tracking-wide text-gray-400">Recording</div><AudioReplay src={a.audioUrl} /></div>}
            </div>
          );
        })}
        <button onClick={() => navigate(`/student/results/${assignmentId}`)} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">View results</button>
      </div>
    );
  }

  // Screen 2+ — taking: prompt + record-only capture + read-only transcript.
  return (
    <div className="max-w-5xl mx-auto p-4">
      <div className="bg-blue-600 text-white rounded-t-xl px-5 py-3 flex items-center justify-between">
        <span className="font-semibold">CEFR Speaking Test</span>
        <span className="text-sm text-blue-100">Turn {Math.min(turnNo + 1, TOTAL_TURNS)} of {TOTAL_TURNS}{azureMode ? '' : ' · text mode'}</span>
      </div>
      <div className="flex gap-5 bg-white rounded-b-xl border-2 border-t-0 border-gray-200 p-5">
        <div className="flex-1 min-w-0 space-y-4">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Question</div>
            <p className="text-lg text-gray-900 font-medium">{prompt}</p>
          </div>

          {/* Read-only transcription box (appears after the prompt) */}
          <div className="border border-gray-200 rounded-lg p-3 bg-gray-50 min-h-[5rem]">
            <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Your response</div>
            {recording ? (
              // While speaking we show a live waveform (your speech is being heard
              // and processed) rather than the streaming transcript.
              <div className="flex flex-col items-center gap-1 py-2">
                <canvas ref={canvasRef} width={320} height={44} className="w-full max-w-sm h-11" />
                <span className="text-[11px] text-gray-400">Listening… your answer is being transcribed</span>
              </div>
            ) : captured?.transcript ? (
              <p className="text-sm text-gray-800">{captured.transcript}</p>
            ) : (
              <p className="text-sm text-gray-400">Press Record and speak your answer. Your transcribed response will appear here (you can't edit it).</p>
            )}
          </div>
          {micNotice && <p className="text-xs text-amber-600">{micNotice}</p>}

          <div className="flex items-center gap-2">
            {!recording ? (
              <button onClick={startRecording} disabled={submitting} className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm border border-gray-300 text-gray-700">
                {captured ? <RotateCcw className="w-4 h-4" /> : <Mic className="w-4 h-4" />} {captured ? 'Re-record' : 'Record'}
              </button>
            ) : (
              <button onClick={stopRecording} className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm border border-red-300 bg-red-50 text-red-700">
                <Square className="w-4 h-4" /> Stop
              </button>
            )}
            <button onClick={() => { setTtsOn((v) => !v); stopSpeech(); }} title={ttsAvailable ? 'Toggle examiner audio' : 'Audio unavailable'}
              className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm border border-gray-300 text-gray-700">
              {ttsOn && ttsAvailable ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
            </button>
            <button onClick={submit} disabled={submitting || recording || !captured?.transcript}
              className="ml-auto inline-flex items-center gap-1 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Submit answer
            </button>
          </div>
        </div>

        {/* Live scorecard — "This answer" vs "Running overall" kept distinct (#5 fix) */}
        <aside className="w-64 flex-shrink-0 hidden lg:block border-l border-gray-100 pl-4">
          {!latest ? <p className="text-sm text-gray-400">Scores appear after your first answer.</p> : (
            <div className="space-y-4">
              <div>
                <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-1">This answer</h3>
                <div className="flex items-center justify-between text-xs text-gray-600">
                  <span>Band <b>{latest.turn_band_name}</b></span><span>{DIRECTION_LABEL[latest.direction]}</span>
                </div>
                <div className="space-y-1 mt-1">
                  {CRITERIA.map((c) => {
                    const v = latest.scores[c]; const b = v == null ? '—' : numToBand(v);
                    return (
                      <div key={c} className="flex items-center justify-between gap-2">
                        <span className="text-xs text-gray-600">{CRITERION_LABEL[c]}</span>
                        <span className={`text-xs font-semibold border rounded px-2 ${bandClasses(b)}`}>{b}{v != null ? ` · ${v}` : ''}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div>
                <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-1">Running overall</h3>
                <div className={`rounded-xl border px-4 py-2 text-center ${bandClasses(latest.overall_band)}`}>
                  <div className="text-2xl font-bold leading-none">{latest.overall_band}</div>
                  <div className="text-[11px] mt-1">avg across {history.length} answer(s)</div>
                </div>
                <p className="text-[11px] text-gray-400 mt-1">Next pitched at <b>{latest.next_target_band_name}</b>.</p>
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
