import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Mic, MicOff, PlayCircle, Send, Volume2, VolumeX, Loader2 } from 'lucide-react';
import { API_BASE_URL } from '../../config';
import { startExamSession, submitAnswer, completeSession } from '../../api/exam';
import type { CefrTurn, CefrBandName } from '../../api/exam';

// Dedicated CEFR speaking-test flow (distinct from the EDS TakeExam page). It
// reuses the tested backend communication/ engine: one Part-2 long turn + four
// adaptive Part-3 follow-ups, graded on the CEFR Table-3 grid, laddered A1-C2.
// Phase 1 captures the answer as text (Web Speech / typing); Azure speech +
// pronunciation replace this capture layer in Phase 2. Scoring and the adaptive
// follow-ups are produced server-side; this page only displays and advances.

type Phase = 'loading' | 'ready' | 'taking' | 'done' | 'error';
type Role = 'evaluator' | 'student';
interface ThreadTurn { role: Role; text: string; cefr?: CefrTurn }

const BAND_NAMES: CefrBandName[] = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
const numToBand = (n: number | null | undefined): CefrBandName | '—' =>
  n == null ? '—' : (BAND_NAMES[Math.min(5, Math.max(0, Math.round(n) - 1))] ?? '—');

// A2/<=A2 → amber, B1 → blue, B2+ → green. Colour only; never a pass/fail gate.
function bandClasses(band: string | null | undefined): string {
  if (!band || band === '—') return 'bg-gray-100 text-gray-500 border-gray-200';
  if (band.startsWith('C') || band === 'B2') return 'bg-green-50 text-green-700 border-green-200';
  if (band === 'B1') return 'bg-blue-50 text-blue-700 border-blue-200';
  return 'bg-amber-50 text-amber-700 border-amber-200';
}

const DIRECTION_LABEL: Record<CefrTurn['direction'], string> = {
  up: '↑ harder', same: '→ holding', down: '↓ easier',
};

const CRITERIA: Array<keyof CefrTurn['scores']> = ['range', 'accuracy', 'fluency', 'coherence', 'interaction'];
const CRITERION_LABEL: Record<string, string> = {
  range: 'Range', accuracy: 'Accuracy', fluency: 'Fluency', coherence: 'Coherence', interaction: 'Interaction',
};

const TOTAL_TURNS = 5; // 1 Part-2 long turn + 4 Part-3 follow-ups (matches backend C.TOTAL_TURNS)

export default function CefrSpeakingExam() {
  const { assignmentId } = useParams<{ assignmentId: string }>();
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>('loading');
  const [error, setError] = useState<string>('');
  const [sessionId, setSessionId] = useState<string>('');
  const [prompt, setPrompt] = useState<string>('');        // current evaluator prompt to answer
  const [thread, setThread] = useState<ThreadTurn[]>([]);  // full exchange for the transcript
  const [history, setHistory] = useState<CefrTurn[]>([]);  // per-turn CEFR, newest last
  const [draft, setDraft] = useState<string>('');
  const [listening, setListening] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [ttsOn, setTtsOn] = useState(true);
  const [ttsAvailable, setTtsAvailable] = useState(true);
  const [micNotice, setMicNotice] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const recognitionRef = useRef<any>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);

  // ── TTS (ElevenLabs proxy) ───────────────────────────────────────────────
  const speak = useCallback(async (text: string) => {
    if (!ttsOn || !text) return;
    try {
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_BASE_URL}/api/tts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) { if (resp.status === 503) setTtsAvailable(false); return; }
      setTtsAvailable(true);
      const audio = new Audio(URL.createObjectURL(await resp.blob()));
      audioRef.current?.pause();
      audioRef.current = audio;
      audio.play().catch(() => { /* autoplay may need a gesture */ });
    } catch { /* network/unsupported → silent */ }
  }, [ttsOn]);

  const stopSpeech = useCallback(() => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
  }, []);

  // ── Web Speech dictation (Phase 1 capture; Azure replaces this in Phase 2) ─
  const stopMic = useCallback(() => {
    const rec = recognitionRef.current;
    recognitionRef.current = null;
    if (rec) { try { rec.onend = null; rec.stop(); } catch { /* already stopped */ } }
    setListening(false);
  }, []);

  const toggleMic = () => {
    const SR = window as unknown as { SpeechRecognition?: new () => any; webkitSpeechRecognition?: new () => any };
    const Ctor = SR.SpeechRecognition || SR.webkitSpeechRecognition;
    if (!Ctor) { setMicNotice("Voice input isn't supported here — try Chrome, Edge, or Safari, or type your answer."); return; }
    if (listening) { stopMic(); return; }
    stopSpeech(); // student takes the floor; silence the examiner audio
    const rec: any = new Ctor();
    rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = true;
    let base = draft;
    rec.onresult = (e: any) => {
      let interim = '', finalT = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalT += t; else interim += t;
      }
      if (finalT) base = (base ? base + ' ' : '') + finalT.trim();
      setDraft((base + (interim ? ' ' + interim : '')).trim());
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => { setListening(false); setMicNotice('Voice input had a problem — you can type your answer instead.'); };
    recognitionRef.current = rec;
    try { rec.start(); setMicNotice(null); setListening(true); }
    catch { setMicNotice('Could not start the microphone. You can type your answer instead.'); }
  };

  // ── Session start ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!assignmentId) return;
    let cancelled = false;
    startExamSession(assignmentId)
      .then((res) => {
        if (cancelled) return;
        setSessionId(res.session_id);
        setPrompt(res.questions[0]?.text || 'Please speak on the topic.');
        setPhase('ready');
      })
      .catch((e) => { if (!cancelled) { setError(e?.message || 'Could not start the exam.'); setPhase('error'); } });
    return () => { cancelled = true; };
  }, [assignmentId]);

  useEffect(() => () => { stopSpeech(); stopMic(); }, [stopSpeech, stopMic]);
  useEffect(() => { threadEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [thread]);

  const begin = () => {
    setThread([{ role: 'evaluator', text: prompt }]);
    setPhase('taking');
    speak(prompt); // first play is user-gesture-initiated, so autoplay is allowed
  };

  // ── Submit one answer → score → next follow-up (ladder runs server-side) ───
  const submit = async () => {
    const answer = draft.trim();
    if (!answer || submitting) return;
    stopMic(); stopSpeech();
    setSubmitting(true);
    setThread((t) => [...t, { role: 'student', text: answer }]);
    setDraft('');
    try {
      const resp = await submitAnswer(sessionId, 0, answer);
      if (resp.cefr) setHistory((h) => [...h, resp.cefr!]);
      const isFinal = !resp.probe; // backend sends an empty follow-up on the last turn
      if (isFinal) {
        try { await completeSession(sessionId); } catch { /* best-effort */ }
        setPhase('done');
      } else {
        setThread((t) => [...t, { role: 'evaluator', text: resp.probe, cefr: resp.cefr }]);
        setPrompt(resp.probe);
        speak(resp.probe);
      }
    } catch (e: any) {
      setError(e?.message || 'Could not submit your answer.');
      setPhase('error');
    } finally {
      setSubmitting(false);
    }
  };

  const latest = history[history.length - 1];
  const turnNo = latest?.turn_number ?? thread.filter((t) => t.role === 'student').length;

  // ── Render ───────────────────────────────────────────────────────────────
  if (phase === 'loading') {
    return <div className="flex items-center gap-2 text-gray-500 p-8"><Loader2 className="w-5 h-5 animate-spin" /> Loading your speaking test…</div>;
  }
  if (phase === 'error') {
    return (
      <div className="max-w-xl mx-auto p-8 space-y-4">
        <p className="text-red-600">{error}</p>
        <button onClick={() => navigate(-1)} className="px-4 py-2 border border-gray-300 rounded-lg text-sm">Go back</button>
      </div>
    );
  }
  if (phase === 'ready') {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-5">
        <h1 className="text-2xl font-bold text-gray-900">CEFR Speaking Test</h1>
        <div className="bg-white rounded-xl border-2 border-gray-200 p-5 space-y-3">
          <h2 className="text-xs uppercase tracking-wide text-gray-500">How this works</h2>
          <p className="text-sm text-gray-700">
            You'll give one longer answer, then four short follow-ups that adapt up or down the CEFR scale based on how you do.
            Each answer is graded on the CEFR grid — <b>Range, Accuracy, Fluency, Coherence</b> and (on follow-ups) <b>Interaction</b>.
            You can speak (your voice is transcribed) or type.
          </p>
          <p className="text-sm text-gray-500">Opening question:</p>
          <p className="text-base text-gray-900 font-medium">{prompt}</p>
        </div>
        <button onClick={begin} className="inline-flex items-center gap-2 px-5 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700">
          <PlayCircle className="w-5 h-5" /> Start speaking test
        </button>
      </div>
    );
  }
  if (phase === 'done') {
    const final = history[history.length - 1];
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Speaking test complete</h1>
        {final && (
          <div className="bg-white rounded-xl border-2 border-gray-200 p-5">
            <div className="flex items-center gap-4">
              <div className={`text-4xl font-bold px-5 py-3 rounded-xl border ${bandClasses(final.overall_band)}`}>{final.overall_band}</div>
              <div className="text-sm text-gray-600">
                <div>Overall CEFR (holistic across the exam)</div>
                <div className="text-gray-400">Averaged over {history.length} graded answers.</div>
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-4">
              {CRITERIA.map((c) => (
                <div key={c} className={`rounded-lg border px-3 py-2 text-center ${bandClasses(numToBand(final.criterion_averages[c]))}`}>
                  <div className="text-lg font-semibold">{numToBand(final.criterion_averages[c])}</div>
                  <div className="text-[11px]">{CRITERION_LABEL[c]}</div>
                </div>
              ))}
            </div>
            <p className="text-xs text-gray-400 mt-3">
              Graded from the transcript (Range/Accuracy/Fluency/Coherence/Interaction). Pronunciation is not assessed in this version.
            </p>
          </div>
        )}
        <div className="bg-white rounded-xl border-2 border-gray-200 p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-3">Transcript</h2>
          <div className="space-y-3">
            {thread.map((t, i) => (
              <div key={i}>
                <div className="flex items-center gap-2">
                  <span className={`text-[11px] font-semibold ${t.role === 'evaluator' ? 'text-blue-600' : 'text-gray-500'}`}>
                    {t.role === 'evaluator' ? 'Examiner' : 'You'}
                  </span>
                  {t.cefr && (
                    <span className={`text-[11px] border rounded-full px-2 ${bandClasses(t.cefr.turn_band_name)}`}>
                      {t.cefr.turn_band_name} · {DIRECTION_LABEL[t.cefr.direction]}
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-800">{t.text}</p>
              </div>
            ))}
          </div>
        </div>
        <button onClick={() => navigate(`/student/results/${assignmentId}`)} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">
          View results
        </button>
      </div>
    );
  }

  // phase === 'taking'
  return (
    <div className="max-w-5xl mx-auto p-4">
      <div className="bg-blue-600 text-white rounded-t-xl px-5 py-3 flex items-center justify-between">
        <span className="font-semibold">CEFR Speaking Test</span>
        <span className="text-sm text-blue-100">Turn {Math.min(turnNo + (submitting ? 0 : 1), TOTAL_TURNS)} of {TOTAL_TURNS}</span>
      </div>
      <div className="flex gap-5 bg-white rounded-b-xl border-2 border-t-0 border-gray-200 p-5">
        {/* Left: conversation + input */}
        <div className="flex-1 min-w-0">
          <div className="space-y-3 max-h-[46vh] overflow-y-auto pr-1">
            {thread.map((t, i) => (
              <div key={i} className={t.role === 'evaluator' ? 'text-left' : 'text-right'}>
                <span className={`inline-block max-w-[90%] rounded-2xl px-4 py-2 text-sm ${t.role === 'evaluator' ? 'bg-gray-100 text-gray-900' : 'bg-blue-600 text-white'}`}>
                  {t.text}
                </span>
              </div>
            ))}
            {submitting && <div className="text-xs text-gray-400 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> Scoring…</div>}
            <div ref={threadEndRef} />
          </div>

          <div className="mt-4 border-t border-gray-100 pt-3">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Speak (tap the mic) or type your answer…"
              rows={3}
              disabled={submitting}
              className="w-full border border-gray-300 rounded-lg p-3 text-sm resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {micNotice && <p className="text-xs text-amber-600 mt-1">{micNotice}</p>}
            <div className="flex items-center gap-2 mt-2">
              <button onClick={toggleMic} disabled={submitting}
                className={`inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm border ${listening ? 'bg-red-50 border-red-300 text-red-700' : 'border-gray-300 text-gray-700'}`}>
                {listening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />} {listening ? 'Stop' : 'Speak'}
              </button>
              <button onClick={() => { setTtsOn((v) => !v); stopSpeech(); }}
                title={ttsAvailable ? 'Toggle examiner audio' : 'Audio narration unavailable'}
                className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm border border-gray-300 text-gray-700">
                {ttsOn && ttsAvailable ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
              </button>
              <button onClick={submit} disabled={submitting || !draft.trim()}
                className="ml-auto inline-flex items-center gap-1 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                <Send className="w-4 h-4" /> Submit answer
              </button>
            </div>
            {!ttsAvailable && <p className="text-xs text-gray-400 mt-1">Audio narration is unavailable; the question is shown above.</p>}
          </div>
        </div>

        {/* Right: per-question CEFR scorecard */}
        <aside className="w-64 flex-shrink-0 hidden lg:block border-l border-gray-100 pl-4">
          <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-2">CEFR scorecard</h3>
          {!latest ? (
            <p className="text-sm text-gray-400">Scores appear after your first answer.</p>
          ) : (
            <div className="space-y-3">
              <div className={`rounded-xl border px-4 py-3 text-center ${bandClasses(latest.overall_band)}`}>
                <div className="text-3xl font-bold leading-none">{latest.overall_band}</div>
                <div className="text-[11px] mt-1">Running overall</div>
              </div>
              <div className="text-xs text-gray-500 flex items-center justify-between">
                <span>Last turn: <b>{latest.turn_band_name}</b></span>
                <span>{DIRECTION_LABEL[latest.direction]}</span>
              </div>
              <div className="space-y-1">
                {CRITERIA.map((c) => {
                  const v = latest.scores[c];
                  const b = v == null ? '—' : numToBand(v);
                  return (
                    <div key={c} className="flex items-center justify-between gap-2">
                      <span className="text-xs text-gray-600">{CRITERION_LABEL[c]}</span>
                      <span className={`text-xs font-semibold border rounded px-2 ${bandClasses(b)}`}>
                        {b}{v != null ? ` · ${v}` : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
              {latest.degraded && <p className="text-[11px] text-amber-600">Auto-scored (grader fallback) — not a final judgement.</p>}
              <p className="text-[11px] text-gray-400">
                Next follow-up pitched at <b>{latest.next_target_band_name}</b>. Fluency here is judged from the transcript; pronunciation isn't assessed in this version.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
