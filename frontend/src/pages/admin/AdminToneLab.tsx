import { useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FlaskConical, Play, Square, Download, Loader2, RadioTower, AlertTriangle,
  History, GitPullRequestArrow, CheckCircle2, XCircle, RotateCcw, ShieldCheck,
} from 'lucide-react';
import { ApiError } from '../../api/client';
import {
  fetchExaminerPrompt, fetchEvalCases, saveExperiment, listExperiments,
  createOverride, listOverrides, approveOverride, activateOverride, revertOverride,
  rejectOverride,
  type ArmMetrics, type Recommendation, type SavedExperiment, type PromptOverride,
} from '../../api/adminToneLab';
import {
  SHARED_PREAMBLE, TURN_RULES, CASES as SAMPLE_CASES, EVALUATIVE, NEUTRAL_RECEIPT,
  CONFIRM_PATTERNS, COMPOUND_PATTERNS, ARMS, type CaseDef,
} from './toneLabData';

// ── Types ────────────────────────────────────────────────────────────────────
interface TurnRow {
  arm: string; question: string; persona: string; rep: number; turn: number;
  examiner_words: number; receipt_words: number; probe_words: number;
  empty_probe: boolean; repaired: boolean; parse_ok: boolean;
  receipt_class: string; leaked: string; n_leaks: number;
  confirms: boolean; compound: boolean; scaffold: boolean; target: string;
  receipt: string; probe: string; spoken: string; student_terms: string;
}
interface CallResult { text: string; in: number; out: number }

const ARM_ORDER = ['A0', 'A1', 'A2', 'A3'];

// ── Pure engine (ported verbatim from the standalone lab) ─────────────────────
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const wordCount = (t: string) => (t.match(/[A-Za-z0-9'’-]+/g) || []).length;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function cumulativeTerms(script: { terms: string[] }[], upto: number): string[] {
  const seen: string[] = [];
  for (let i = 0; i <= upto; i++)
    for (const t of (script[i].terms || [])) if (!seen.includes(t)) seen.push(t);
  return seen;
}
function renderGraph(c: CaseDef): string {
  return c.graph.map((e) => `  ${e.id}  epsilon ${e.eps.toFixed(2)}  ${e.text}`).join('\n');
}
function renderTranscript(script: { text: string }[], ex: string[], upto: number): string {
  const L: string[] = [];
  for (let i = 0; i <= upto; i++) {
    L.push(`  STUDENT: ${script[i].text}`);
    if (i < ex.length) L.push(`  EXAMINER: ${ex[i]}`);
  }
  return L.length ? L.join('\n') : '  (none)';
}
function buildMessages(inputMode: string, c: CaseDef, script: { text: string; terms: string[] }[],
                       ex: string[], i: number): { role: string; content: string }[] {
  if (inputMode === 'chat') {
    const msgs: { role: string; content: string }[] = [];
    for (let j = 0; j <= i; j++) {
      msgs.push({ role: 'user', content: script[j].text });
      if (j < ex.length && j < i) msgs.push({ role: 'assistant', content: ex[j] });
    }
    const merged: { role: string; content: string }[] = [];
    for (const m of msgs) {
      if (merged.length && merged[merged.length - 1].role === m.role)
        merged[merged.length - 1].content += '\n\n' + m.content;
      else merged.push({ ...m });
    }
    return merged;
  }
  const terms = cumulativeTerms(script, i);
  const block =
    `QUESTION\n  ${c.stem}\n\n` +
    `CONTEXT_GRAPH\n${renderGraph(c)}\n\n` +
    `STUDENT_TERMS\n  ${terms.length ? terms.join(', ') : '(none)'}\n\n` +
    `TRANSCRIPT\n${renderTranscript(script, ex, i - 1)}\n\n` +
    `LATEST STUDENT TURN\n  ${script[i].text}\n\n` +
    `TURNS_REMAINING\n  ${script.length - i - 1}\n`;
  return [{ role: 'user', content: block }];
}
function repairJson(s: string) { return s.replace(/:\s*""([^"]*?)""?\s*([,}])/g, ': "$1"$2'); }
function parseReply(raw: string, expectsJson: boolean) {
  if (!expectsJson) return { receipt: '', probe: raw.trim(), scaffold: false, target: '', ok: true, repaired: false };
  const m = raw.trim().match(/\{[\s\S]*\}/);
  let obj: Record<string, unknown> | null = null, repaired = false;
  if (m) {
    for (const [cand, flag] of [[m[0], false], [repairJson(m[0]), true]] as [string, boolean][]) {
      try { obj = JSON.parse(cand); repaired = flag; break; } catch { /* try the repair */ }
    }
  }
  if (!obj) return { receipt: '', probe: '', scaffold: false, target: '', ok: false, repaired: false };
  const o: Record<string, unknown> = {};
  for (const k of Object.keys(obj)) o[String(k).trim()] = obj[k];
  return {
    receipt: String(o.receipt || '').trim(),
    probe: String(o.probe || '').trim(),
    scaffold: !!o.scaffold_used,
    target: String(o.target || '').trim(),
    ok: true, repaired,
  };
}
function findLeaks(text: string, c: CaseDef, terms: string[]): string[] {
  const hits: string[] = [];
  for (const t of c.watch_terms) {
    if (terms.includes(t.label)) continue;
    if (t.patterns.some((p) => new RegExp(p, 'i').test(text))) hits.push(t.label);
  }
  return hits;
}
function classifyReceipt(receipt: string, whole: string, arm: string): string {
  let head = receipt.trim();
  if (!head) {
    const first = whole.trim().split('.')[0].slice(0, 80);
    const any = [...EVALUATIVE, ...NEUTRAL_RECEIPT].some((w) => new RegExp('\\b' + esc(w) + '\\b', 'i').test(first));
    if (!any) return 'absent';
    head = first;
  }
  if (EVALUATIVE.some((w) => new RegExp('\\b' + esc(w) + '\\b', 'i').test(head)))
    return (arm === 'A1' || arm === 'A2') ? 'evaluative' : 'warm';
  if (NEUTRAL_RECEIPT.some((w) => new RegExp('\\b' + esc(w) + '\\b', 'i').test(head)))
    return 'neutral';
  return head ? 'neutral' : 'absent';
}
const confirmsClaim = (t: string) => CONFIRM_PATTERNS.some((p) => new RegExp(p, 'i').test(t));
const isCompound = (p: string) => (p.match(/\?/g) || []).length > 1 ||
  COMPOUND_PATTERNS.some((x) => new RegExp(x, 'i').test(p));

// ── Component ─────────────────────────────────────────────────────────────────
export default function AdminToneLab() {
  const qc = useQueryClient();

  // Config
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('claude-sonnet-4-5');
  const [temp, setTemp] = useState(0);
  const [reps, setReps] = useState(1);
  const [pool, setPool] = useState(4);

  // Selections
  const [armsOn, setArmsOn] = useState<Record<string, boolean>>({ A0: false, A1: true, A2: true, A3: false });
  const [qsOn, setQsOn] = useState<Record<string, boolean>>({ Q1: true, Q2: true, Q3: true });
  const [personasOn, setPersonasOn] = useState<Record<string, boolean>>({ S: true, G: true });

  // Prompts
  const [a0, setA0] = useState('');
  const [preamble, setPreamble] = useState(SHARED_PREAMBLE);
  const [a1, setA1] = useState(TURN_RULES.A1);
  const [a2, setA2] = useState(TURN_RULES.A2);
  const [a3, setA3] = useState(TURN_RULES.A3);
  const [a0InputMode, setA0InputMode] = useState<'chat' | 'block'>('chat');

  // Cases (start from sample fixtures; Fetch live cases merges real stems/graphs)
  const [cases, setCases] = useState<Record<string, CaseDef>>(SAMPLE_CASES);
  const liveCaseIds = useRef<Set<string>>(new Set());

  // Live source
  const [promptVersion, setPromptVersion] = useState<string | null>(null);
  const [epStatus, setEpStatus] = useState('');
  const [epMeta, setEpMeta] = useState('');

  // Run
  const [running, setRunning] = useState(false);
  const abortRef = useRef(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [log, setLog] = useState<string[]>([]);
  const [rows, setRows] = useState<TurnRow[]>([]);
  const [showAll, setShowAll] = useState(false);

  // Save
  const [title, setTitle] = useState('');
  const [savedRec, setSavedRec] = useState<Recommendation | null>(null);

  const a0Ready = a0.trim().length > 40;
  const appendLog = (s: string) => setLog((L) => [...L, s]);

  // ── Live fetch ──────────────────────────────────────────────────────────────
  async function onFetchPrompt() {
    setEpStatus('fetching prompt…');
    try {
      const j = await fetchExaminerPrompt();
      if (!j.system?.trim()) { setEpStatus('prompt endpoint returned no system string'); return; }
      setA0(j.system);
      if (j.model) setModel(j.model);
      if (typeof j.temperature === 'number') setTemp(j.temperature);
      if (j.input_mode === 'chat' || j.input_mode === 'block') setA0InputMode(j.input_mode);
      setArmsOn((a) => ({ ...a, A0: true }));
      setPromptVersion(j.prompt_version);
      setEpStatus('prompt loaded');
      setEpMeta([
        j.prompt_version && `version ${j.prompt_version}`,
        j.source && `source ${j.source}`,
        `${j.system.length} chars`,
        `input mode ${j.input_mode}`,
      ].filter(Boolean).join(' · '));
    } catch (e) {
      setEpStatus(`prompt failed: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  }
  async function onFetchCases() {
    setEpStatus('fetching cases…');
    try {
      const j = await fetchEvalCases();
      const live = j.cases || {};
      const merged: Record<string, CaseDef> = JSON.parse(JSON.stringify(cases));
      const took: string[] = [], skipped: string[] = [];
      const liveIds = new Set<string>();
      for (const qid of Object.keys(live)) {
        const L = live[qid];
        if (!L.graph || !L.stem) { skipped.push(`${qid} (no stem or graph)`); continue; }
        if (merged[qid]) {
          merged[qid].stem = L.stem; merged[qid].graph = L.graph;
          if (L.domain) merged[qid].domain = L.domain;
          took.push(qid); liveIds.add(qid);
        } else {
          skipped.push(`${qid} (no student script authored)`);
        }
      }
      setCases(merged);
      liveCaseIds.current = liveIds;
      setEpStatus('cases loaded');
      setEpMeta(`updated ${took.length ? took.join(', ') : 'nothing'}` +
        (skipped.length ? ` · skipped ${skipped.length}` : '') +
        ' · scripts + watch terms are authored in-lab, not fetched');
    } catch (e) {
      setEpStatus(`cases failed: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  }

  // ── Model call (browser → Anthropic, in-tab key) ─────────────────────────────
  // No assistant-message prefill: some models (e.g. newer Sonnet) reject a trailing
  // assistant turn ("conversation must end with a user message"). The JSON arms are
  // instructed to emit minified JSON in their prompt, and parseReply extracts the
  // first {...} block, so a prefix is unnecessary and this stays model-agnostic.
  async function callModel(system: string, messages: { role: string; content: string }[]): Promise<CallResult> {
    const msgs = messages.map((m) => ({ ...m }));
    const body: Record<string, unknown> = { model: model.trim(), max_tokens: 400, temperature: temp, messages: msgs };
    if (system && system.trim()) body.system = system;
    let last: Error | null = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (abortRef.current) throw new Error('stopped');
      try {
        const r = await fetch('https://api.anthropic.com/v1/messages', {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-api-key': apiKey.trim(),
            'anthropic-version': '2023-06-01',
            'anthropic-dangerous-direct-browser-access': 'true',
          },
          body: JSON.stringify(body),
        });
        if (r.status === 429 || r.status >= 500) { last = new Error('HTTP ' + r.status); await sleep(1000 * 2 ** attempt); continue; }
        const j = await r.json();
        if (!r.ok) throw new Error(j?.error?.message || ('HTTP ' + r.status));
        const text = (j.content || []).filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join('');
        return { text: text.trim(), in: j.usage?.input_tokens || 0, out: j.usage?.output_tokens || 0 };
      } catch (e) {
        last = e as Error;
        if (String((e as Error).message).includes('stopped')) throw e;
        if (attempt === 3) break;
        await sleep(1000 * 2 ** attempt);
      }
    }
    throw new Error('call failed: ' + (last && last.message));
  }

  function systemFor(arm: string, a0System: string): string {
    if (arm === 'A0') return a0System;
    const rules = arm === 'A1' ? a1 : arm === 'A2' ? a2 : a3;
    return preamble + '\n' + rules;
  }

  // Faithful A0: for a live (real) question, fetch its exact rendered system; otherwise
  // reuse the base fetched A0 prompt. Cached per qid for the run.
  async function resolveA0System(qid: string, cache: Record<string, string>): Promise<string> {
    if (cache[qid] !== undefined) return cache[qid];
    if (liveCaseIds.current.has(qid)) {
      try { cache[qid] = (await fetchExaminerPrompt({ question_id: qid })).system; }
      catch { cache[qid] = a0; }
    } else { cache[qid] = a0; }
    return cache[qid];
  }

  async function run() {
    if (!apiKey.trim()) { alert('Paste an Anthropic API key first.'); return; }
    const arms = ARM_ORDER.filter((a) => armsOn[a] && (a !== 'A0' || a0Ready));
    const qs = Object.keys(cases).filter((q) => qsOn[q]);
    const ps = ['S', 'G'].filter((p) => personasOn[p]);
    if (!arms.length || !qs.length || !ps.length) { alert('Pick at least one arm, question and persona.'); return; }

    const jobs: { arm: string; qid: string; persona: string; rep: number }[] = [];
    for (const arm of arms) for (const qid of qs) for (const persona of ps) {
      if (!cases[qid].scripts[persona]) continue;
      for (let r = 1; r <= reps; r++) jobs.push({ arm, qid, persona, rep: r });
    }
    const totalTurns = jobs.reduce((n, j) => n + cases[j.qid].scripts[j.persona].length, 0);

    abortRef.current = false; setRunning(true); setLog([]); setRows([]); setSavedRec(null);
    setProgress({ done: 0, total: totalTurns });
    appendLog(`${jobs.length} transcripts · ${totalTurns} calls · model ${model.trim()}`);

    const a0Cache: Record<string, string> = {};
    const rowsAcc: TurnRow[] = [];
    let done = 0;

    const runJob = async (job: { arm: string; qid: string; persona: string; rep: number }) => {
      const { arm, qid, persona } = job;
      const c = cases[qid], script = c.scripts[persona];
      const inputMode = arm === 'A0' ? a0InputMode : ARMS[arm].input_mode;
      const a0System = arm === 'A0' ? await resolveA0System(qid, a0Cache) : '';
      const system = systemFor(arm, a0System);
      const ex: string[] = [];
      for (let i = 0; i < script.length; i++) {
        if (abortRef.current) return;
        const reply = await callModel(system, buildMessages(inputMode, c, script, ex, i));
        const p = parseReply(reply.text, ARMS[arm].expects_json);
        const spoken = (p.receipt + ' ' + p.probe).trim();
        ex.push(spoken);
        const terms = cumulativeTerms(script, i);
        const leaks = findLeaks(spoken, c, terms);
        rowsAcc.push({
          arm, question: qid, persona, rep: job.rep, turn: i + 1,
          examiner_words: wordCount(spoken), receipt_words: wordCount(p.receipt), probe_words: wordCount(p.probe),
          empty_probe: !p.probe, repaired: p.repaired, parse_ok: p.ok,
          receipt_class: classifyReceipt(p.receipt, spoken, arm),
          leaked: leaks.join('; '), n_leaks: leaks.length,
          confirms: confirmsClaim(spoken), compound: isCompound(p.probe),
          scaffold: p.scaffold, target: p.target,
          receipt: p.receipt, probe: p.probe, spoken, student_terms: terms.join('; '),
        });
        done++; setProgress({ done, total: totalTurns });
      }
    };

    const queue = jobs.slice();
    const poolSize = Math.max(1, Math.min(12, pool || 4));
    const workers = Array.from({ length: poolSize }, async () => {
      while (queue.length && !abortRef.current) {
        const job = queue.shift()!;
        try { await runJob(job); appendLog(`ok   ${job.arm} ${job.qid}/${job.persona} rep ${job.rep}`); }
        catch (e) {
          appendLog(`FAIL ${job.arm} ${job.qid}/${job.persona} rep ${job.rep} — ${(e as Error).message}`);
          if (String((e as Error).message).includes('Failed to fetch'))
            appendLog('     browser blocked the request (CSP or network).');
          abortRef.current = true;
        }
      }
    });
    await Promise.all(workers);
    setRows(rowsAcc);
    setRunning(false);
  }

  // ── Derived per-arm metrics ───────────────────────────────────────────────────
  const armMetrics: ArmMetrics[] = useMemo(() => {
    const arms = [...new Set(rows.map((r) => r.arm))];
    const mean = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
    return arms.map((a) => {
      const s = rows.filter((r) => r.arm === a);
      const ok = s.filter((r) => r.parse_ok); const w = ok.length ? ok : s;
      const cnt = (f: (r: TurnRow) => boolean) => s.filter(f).length;
      return {
        arm: a, turns: s.length,
        leak_rate: s.length ? s.filter((r) => r.n_leaks > 0).length / s.length : 0,
        mean_words: +mean(w.map((r) => r.examiner_words)).toFixed(1),
        mean_probe: +mean(w.map((r) => r.probe_words)).toFixed(1),
        mean_receipt: +mean(w.map((r) => r.receipt_words)).toFixed(1),
        evaluative: cnt((r) => r.receipt_class === 'evaluative'),
        confirms: cnt((r) => r.confirms), compound: cnt((r) => r.compound),
        empty_probe: cnt((r) => r.empty_probe), parse_fails: cnt((r) => !r.parse_ok),
      };
    });
  }, [rows]);

  const leakConcepts = useMemo(() => {
    const arms = [...new Set(rows.map((r) => r.arm))];
    const labels: Record<string, Record<string, number>> = {};
    rows.filter((r) => r.leaked).forEach((r) => r.leaked.split('; ').forEach((l) => {
      labels[l] = labels[l] || {}; labels[l][r.arm] = (labels[l][r.arm] || 0) + 1;
    }));
    return { arms, labels, keys: Object.keys(labels).sort() };
  }, [rows]);

  const flaggedTurns = rows.filter((r) => showAll || r.n_leaks > 0 || r.confirms ||
    r.receipt_class === 'evaluative' || !r.parse_ok || r.empty_probe);

  // ── Save experiment ───────────────────────────────────────────────────────────
  const saveMut = useMutation({
    mutationFn: () => saveExperiment({
      title: title || undefined,
      prompt_version: promptVersion || undefined,
      config: {
        arms: ARM_ORDER.filter((a) => armsOn[a]), questions: Object.keys(cases).filter((q) => qsOn[q]),
        personas: ['S', 'G'].filter((p) => personasOn[p]), model, temperature: temp, reps,
      },
      summary: { arms: armMetrics },
    }),
    onSuccess: (res) => {
      if (res.recommendation) setSavedRec(res.recommendation);
      qc.invalidateQueries({ queryKey: ['tone-experiments'] });
    },
  });

  // ── Export ─────────────────────────────────────────────────────────────────────
  function save(name: string, text: string, mime: string) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = name; a.click(); URL.revokeObjectURL(a.href);
  }
  function downloadCsv() {
    if (!rows.length) return;
    const cols = Object.keys(rows[0]) as (keyof TurnRow)[];
    const q = (v: unknown) => `"${String(v).replace(/"/g, '""')}"`;
    save('tally.csv', [cols.join(','), ...rows.map((r) => cols.map((c) => q(r[c])).join(','))].join('\n'), 'text/csv');
  }

  // ── History + governance queries ─────────────────────────────────────────────
  const historyQ = useQuery({ queryKey: ['tone-experiments'], queryFn: listExperiments });
  const overridesQ = useQuery({ queryKey: ['prompt-overrides'], queryFn: listOverrides });

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <FlaskConical className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">Examiner Tone Lab</h1>
          <p className="text-sm text-muted">
            Play fixed student scripts against examiner-prompt arms and count concept leakage + tone
            signals. A0 fetches the live production prompt so the baseline never drifts. Model calls
            run browser→Anthropic with an in-tab key.
          </p>
        </div>
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-[13px] text-amber-900 flex gap-2">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
        <span>Your Anthropic key stays in this tab (state only, never persisted). It is sent only to
          <code className="mx-1">api.anthropic.com</code>. Use a rotatable workspace-scoped key.</span>
      </div>

      {/* Live source */}
      <Panel title="Live source · pull from the backend" icon={<RadioTower className="w-4 h-4" />}>
        <p className="text-xs text-gray-500 mb-3">Fetches the examiner prompt the answer flow actually
          renders (same code path), so A0 can't drift. Auth carries via your admin session.</p>
        <div className="flex flex-wrap gap-2">
          <button onClick={onFetchPrompt} className="btn-ghost">Fetch live prompt</button>
          <button onClick={onFetchCases} className="btn-ghost">Fetch live cases</button>
          {epStatus && <span className="text-xs text-gray-600 self-center">{epStatus}</span>}
        </div>
        {epMeta && <p className="text-[11px] text-gray-400 mt-2">{epMeta}</p>}
      </Panel>

      {/* Config */}
      <Panel title="Configuration">
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <Field label="Anthropic API key">
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-ant-..." className="inp" autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Model"><input className="inp" value={model} onChange={(e) => setModel(e.target.value)} /></Field>
          <Field label="Temperature"><input type="number" min={0} max={1} step={0.1} className="inp"
            value={temp} onChange={(e) => setTemp(parseFloat(e.target.value) || 0)} /></Field>
          <Field label="Reps per cell"><input type="number" min={1} max={10} className="inp"
            value={reps} onChange={(e) => setReps(parseInt(e.target.value) || 1)} /></Field>
          <Field label="Parallel transcripts"><input type="number" min={1} max={12} className="inp"
            value={pool} onChange={(e) => setPool(parseInt(e.target.value) || 4)} /></Field>
        </div>

        <div className="mt-4">
          <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Arms</div>
          <div className="flex flex-wrap gap-2">
            {ARM_ORDER.map((a) => (
              <Chip key={a} on={!!armsOn[a]} disabled={a === 'A0' && !a0Ready}
                onClick={() => setArmsOn((s) => ({ ...s, [a]: !s[a] }))}>
                {a} · {ARMS[a].label}
              </Chip>
            ))}
          </div>
          {!a0Ready && <p className="text-[11px] text-gray-400 mt-1">A0 stays disabled until you fetch (or paste) the production prompt.</p>}
        </div>

        <div className="mt-3">
          <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Questions</div>
          <div className="flex flex-wrap gap-2">
            {Object.keys(cases).map((q) => (
              <Chip key={q} on={!!qsOn[q]} onClick={() => setQsOn((s) => ({ ...s, [q]: !s[q] }))}>
                {q} · {cases[q].domain}{liveCaseIds.current.has(q) ? ' · live' : ''}
              </Chip>
            ))}
          </div>
        </div>

        <div className="mt-3">
          <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Personas</div>
          <div className="flex flex-wrap gap-2">
            {['S', 'G'].map((p) => (
              <Chip key={p} on={!!personasOn[p]} onClick={() => setPersonasOn((s) => ({ ...s, [p]: !s[p] }))}>
                {p === 'S' ? 'S · fluent, shallow' : 'G · genuine, plain'}
              </Chip>
            ))}
          </div>
        </div>
      </Panel>

      {/* Prompts */}
      <Panel title="Prompts">
        <Editor label="A0 · production examiner prompt (fetched or pasted)" value={a0} onChange={setA0} rows={8} open />
        <Editor label="Shared preamble · A1–A3" value={preamble} onChange={setPreamble} rows={10} />
        <Editor label="A1 turn rules · probe only" value={a1} onChange={setA1} rows={6} />
        <Editor label="A2 turn rules · neutral receipt" value={a2} onChange={setA2} rows={8} />
        <Editor label="A3 turn rules · warm receipt" value={a3} onChange={setA3} rows={8} />
      </Panel>

      {/* Run bar */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 flex flex-wrap items-center gap-3 sticky bottom-2 shadow-sm">
        <button onClick={run} disabled={running} className="btn-primary flex items-center gap-2">
          {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run eval
        </button>
        <button onClick={() => { abortRef.current = true; appendLog('stopping after in-flight calls'); }}
          disabled={!running} className="btn-ghost flex items-center gap-2"><Square className="w-4 h-4" /> Stop</button>
        <div className="flex-1 min-w-[160px] h-1.5 bg-gray-100 rounded overflow-hidden">
          <div className="h-full bg-gold transition-all" style={{ width: `${progress.total ? (100 * progress.done / progress.total) : 0}%` }} />
        </div>
        <span className="text-xs text-gray-500 tabular-nums">{progress.done}/{progress.total} calls</span>
      </div>

      {log.length > 0 && (
        <pre className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-[11px] text-gray-600 max-h-48 overflow-auto whitespace-pre-wrap">{log.join('\n')}</pre>
      )}

      {/* Results */}
      {rows.length > 0 && (
        <Panel title="Results">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-gray-400 border-b">
                {['arm', 'turns', 'leak rate', 'words', 'probe', 'receipt', 'evaluative', 'confirms', 'compound', 'empty', 'parse fails'].map((h) => <th key={h} className="py-1.5 pr-3">{h}</th>)}
              </tr></thead>
              <tbody>
                {armMetrics.map((m) => (
                  <tr key={m.arm} className="border-b last:border-0">
                    <td className="py-1.5 pr-3 font-mono font-semibold text-gold">{m.arm}</td>
                    <td className="pr-3 tabular-nums">{m.turns}</td>
                    <td className={`pr-3 tabular-nums ${m.leak_rate > 0 ? 'text-red-600 font-semibold' : ''}`}>{(m.leak_rate * 100).toFixed(0)}%</td>
                    <td className="pr-3 tabular-nums">{m.mean_words}</td>
                    <td className="pr-3 tabular-nums">{m.mean_probe}</td>
                    <td className="pr-3 tabular-nums">{m.mean_receipt}</td>
                    {[m.evaluative, m.confirms, m.compound, m.empty_probe, m.parse_fails].map((v, i) => (
                      <td key={i} className={`pr-3 tabular-nums ${v > 0 ? 'text-red-600 font-semibold' : ''}`}>{v}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {leakConcepts.keys.length > 0 && (
            <div className="mt-4">
              <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Which concepts leak</div>
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead><tr className="text-left text-[10px] uppercase tracking-wide text-gray-400 border-b">
                    <th className="py-1.5 pr-3">concept</th>{leakConcepts.arms.map((a) => <th key={a} className="pr-3">{a}</th>)}
                  </tr></thead>
                  <tbody>
                    {leakConcepts.keys.map((l) => (
                      <tr key={l} className="border-b last:border-0"><td className="py-1.5 pr-3">{l}</td>
                        {leakConcepts.arms.map((a) => <td key={a} className={`pr-3 tabular-nums ${leakConcepts.labels[l][a] ? 'text-red-600 font-semibold' : ''}`}>{leakConcepts.labels[l][a] || 0}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <div className="text-[11px] uppercase tracking-wide text-gray-400">Flagged turns</div>
            <label className="text-xs flex items-center gap-1.5 text-gray-600">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show all turns
            </label>
          </div>
          <div className="space-y-2 mt-2">
            {flaggedTurns.length === 0 && <p className="text-xs text-gray-400">No flagged turns.</p>}
            {flaggedTurns.map((r, i) => {
              const flags = [
                r.leaked && `leak: ${r.leaked}`, r.confirms && 'confirms a claim',
                r.receipt_class === 'evaluative' && 'evaluative receipt', r.compound && 'compound question',
                r.empty_probe && 'empty probe', r.repaired && 'malformed, repaired', !r.parse_ok && 'unparseable',
              ].filter(Boolean);
              return (
                <div key={i} className={`border rounded p-2.5 text-[13px] ${r.n_leaks ? 'border-l-2 border-l-red-500 bg-red-50/40' : 'border-gray-200'}`}>
                  <div className="text-[11px] text-gray-400 flex flex-wrap gap-2 mb-1">
                    <span className="font-mono font-semibold text-gold">{r.arm}</span>
                    <span>{r.question}/{r.persona} rep {r.rep} · turn {r.turn}</span>
                    <span>{r.examiner_words}w · receipt {r.receipt_class}{r.target ? ` · target ${r.target}` : ''}</span>
                    {flags.length > 0 && <span className="text-red-600">{flags.join(' · ')}</span>}
                  </div>
                  <div>{r.spoken || '(nothing spoken)'}</div>
                </div>
              );
            })}
          </div>

          {/* Save + recommendation */}
          <div className="mt-5 border-t pt-4">
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Experiment title" className="flex-1 min-w-[220px]">
                <input className="inp" value={title} onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. A2 neutral receipt vs shipped baseline" />
              </Field>
              <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending} className="btn-primary flex items-center gap-2">
                {saveMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <History className="w-4 h-4" />} Save experiment
              </button>
              <button onClick={downloadCsv} className="btn-ghost flex items-center gap-2"><Download className="w-4 h-4" /> tally.csv</button>
            </div>
            {savedRec && <RecommendationCard rec={savedRec} />}
          </div>
        </Panel>
      )}

      {/* Experiment history */}
      <Panel title="Experiment history" icon={<History className="w-4 h-4" />}>
        {historyQ.isLoading && <p className="text-xs text-gray-400">Loading…</p>}
        {historyQ.data && historyQ.data.experiments.length === 0 && <p className="text-xs text-gray-400">No experiments saved yet.</p>}
        <div className="space-y-3">
          {historyQ.data?.experiments.map((e: SavedExperiment) => (
            <div key={e.experiment_id} className="border border-gray-200 rounded-lg p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium text-navy">{e.title || '(untitled)'}</span>
                {e.prompt_version && <span className="text-[11px] font-mono bg-gray-100 rounded px-1.5 py-0.5">{e.prompt_version}</span>}
                {e.recommendation?.pick && <span className="text-[11px] bg-gold/15 text-gold rounded px-1.5 py-0.5">pick: {e.recommendation.pick}</span>}
                <span className="text-[11px] text-gray-400 ml-auto">{e.created_at ? new Date(e.created_at).toLocaleString() : ''} · {e.created_by}</span>
              </div>
              {e.summary?.arms && (
                <div className="text-[11px] text-gray-500 mt-1 font-mono">
                  {e.summary.arms.map((a) => `${a.arm} leak ${(a.leak_rate * 100).toFixed(0)}%`).join('  ·  ')}
                </div>
              )}
              {e.recommendation?.rationale && <p className="text-[12px] text-gray-600 mt-1">{e.recommendation.rationale}</p>}
            </div>
          ))}
        </div>
      </Panel>

      {/* Governance */}
      <GovernancePanel
        overrides={overridesQ.data?.overrides || []}
        defaultTemplate={overridesQ.data?.default_template || ''}
        activeVersion={overridesQ.data?.active_version || ''}
        defaultVersion={overridesQ.data?.default_version || ''}
        onChanged={() => qc.invalidateQueries({ queryKey: ['prompt-overrides'] })}
      />
    </div>
  );
}

// ── Small presentational helpers ──────────────────────────────────────────────
function Panel({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg">
      <div className="px-4 py-2.5 border-b border-gray-100 flex items-center gap-2 text-[11px] uppercase tracking-wide text-gray-500">
        {icon}{title}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}
function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`flex flex-col gap-1 ${className || ''}`}>
      <span className="text-[11px] uppercase tracking-wide text-gray-400">{label}</span>
      {children}
    </label>
  );
}
function Chip({ on, disabled, onClick, children }: { on: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}
      className={`text-xs font-mono px-2.5 py-1.5 rounded border transition-colors disabled:opacity-40 ${
        on ? 'border-gold bg-gold/10 text-gold' : 'border-gray-300 bg-gray-50 text-gray-600 hover:border-gray-400'}`}>
      {children}
    </button>
  );
}
function Editor({ label, value, onChange, rows, open }: { label: string; value: string; onChange: (v: string) => void; rows: number; open?: boolean }) {
  return (
    <details open={open} className="border-t border-gray-100 first:border-t-0">
      <summary className="cursor-pointer py-2 text-xs text-gray-600">{label}</summary>
      <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={rows}
        className="w-full font-mono text-[12px] border border-gray-300 rounded p-2 mb-2 resize-y" spellCheck={false} />
    </details>
  );
}
function RecommendationCard({ rec }: { rec: Recommendation }) {
  return (
    <div className="mt-3 border border-gold/40 bg-gold/5 rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-wide text-gold mb-1 flex items-center gap-1.5">
        <ShieldCheck className="w-3.5 h-3.5" /> Recommendation{rec.pick ? ` · ${rec.pick}` : ''}
      </div>
      <p className="text-[13px] text-gray-700">{rec.rationale}</p>
      {rec.ranking?.length > 0 && (
        <div className="text-[11px] text-gray-500 mt-1 font-mono">
          {rec.ranking.map((r) => `${r.arm} penalty ${r.penalty}`).join('  ·  ')}
        </div>
      )}
    </div>
  );
}

// ── Governance ─────────────────────────────────────────────────────────────────
function GovernancePanel({ overrides, defaultTemplate, activeVersion, defaultVersion, onChanged }: {
  overrides: PromptOverride[]; defaultTemplate: string; activeVersion: string; defaultVersion: string; onChanged: () => void;
}) {
  const [drafting, setDrafting] = useState(false);
  const [template, setTemplate] = useState('');
  const [notes, setNotes] = useState('');

  const createMut = useMutation({
    mutationFn: () => createOverride({ template, notes: notes || undefined }),
    onSuccess: (res) => {
      if (res.status === 'invalid' || res.status === 'error') { alert(res.message); return; }
      setDrafting(false); setTemplate(''); setNotes(''); onChanged();
    },
  });
  const onActed = (r: { status: string; message?: string }) => {
    if (r.message && (r.status === 'conflict' || r.status === 'error' || r.status === 'not_found')) alert(r.message);
    onChanged();
  };
  const approveMut = useMutation({ mutationFn: approveOverride, onSuccess: onActed });
  const activateMut = useMutation({ mutationFn: activateOverride, onSuccess: onActed });
  const revertMut = useMutation({ mutationFn: revertOverride, onSuccess: onActed });
  const rejectMut = useMutation({ mutationFn: rejectOverride, onSuccess: onActed });

  const badge: Record<string, string> = {
    active: 'bg-green-100 text-green-700', approved: 'bg-blue-100 text-blue-700',
    draft: 'bg-gray-100 text-gray-600', rejected: 'bg-red-50 text-red-600', archived: 'bg-gray-100 text-gray-400',
  };

  return (
    <Panel title="Prompt update · review & approval" icon={<GitPullRequestArrow className="w-4 h-4" />}>
      <div className="flex flex-wrap items-center gap-3 text-[13px]">
        <span className="text-gray-600">Live examiner prompt:</span>
        <span className="font-mono text-xs bg-gray-100 rounded px-1.5 py-0.5">{activeVersion || '—'}</span>
        {activeVersion === defaultVersion && <span className="text-[11px] text-gray-400">(shipped default)</span>}
        <button className="btn-ghost ml-auto" onClick={() => { setTemplate(defaultTemplate); setDrafting(true); }}>
          Propose override
        </button>
      </div>

      {drafting && (
        <div className="mt-3 border border-gray-200 rounded-lg p-3">
          <p className="text-[11px] text-gray-500 mb-2">Template must keep the tokens
            <code className="mx-1">{'{{QUESTION_TEXT}}'}</code><code className="mr-1">{'{{EXPECTED_PATH_JSON}}'}</code>
            <code>{'{{PROBE_DIRECTIVE}}'}</code>. Tune the tone/probe guidance only.</p>
          <textarea value={template} onChange={(e) => setTemplate(e.target.value)} rows={12}
            className="w-full font-mono text-[12px] border border-gray-300 rounded p-2 resize-y" spellCheck={false} />
          <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="notes / rationale (optional)"
            className="inp mt-2" />
          <div className="flex gap-2 mt-2">
            <button className="btn-primary" disabled={createMut.isPending} onClick={() => createMut.mutate()}>Save draft</button>
            <button className="btn-ghost" onClick={() => setDrafting(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="space-y-2 mt-3">
        {overrides.length === 0 && <p className="text-xs text-gray-400">No overrides yet. The answer flow uses the shipped default.</p>}
        {overrides.map((o) => (
          <div key={o.override_id} className="border border-gray-200 rounded-lg p-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{o.override_id.slice(0, 8)}</span>
              <span className={`text-[11px] rounded px-1.5 py-0.5 ${badge[o.status] || 'bg-gray-100'}`}>{o.status}</span>
              {o.notes && <span className="text-gray-600">{o.notes}</span>}
              <span className="text-[11px] text-gray-400 ml-auto">{o.created_by} · {o.created_at ? new Date(o.created_at).toLocaleString() : ''}</span>
            </div>
            <div className="flex flex-wrap gap-2 mt-2">
              {o.status === 'draft' && <>
                <button className="btn-mini" onClick={() => approveMut.mutate(o.override_id)}><CheckCircle2 className="w-3.5 h-3.5" /> Approve</button>
                <button className="btn-mini text-red-600" onClick={() => rejectMut.mutate(o.override_id)}><XCircle className="w-3.5 h-3.5" /> Reject</button>
              </>}
              {o.status === 'approved' && <button className="btn-mini text-green-700" onClick={() => activateMut.mutate(o.override_id)}><ShieldCheck className="w-3.5 h-3.5" /> Activate</button>}
              {o.status === 'active' && <button className="btn-mini" onClick={() => revertMut.mutate(o.override_id)}><RotateCcw className="w-3.5 h-3.5" /> Revert to default</button>}
              {o.reviewed_by && <span className="text-[11px] text-gray-400 self-center">reviewed by {o.reviewed_by}</span>}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
