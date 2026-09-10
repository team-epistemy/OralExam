import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FlaskConical, Loader2, Play, AlertCircle, CheckCircle2, XCircle, MinusCircle, History,
  Star, Save, ClipboardList,
} from 'lucide-react';
import { ApiError } from '../../api/client';
import {
  listTestingSubjects, createTestRun, listTestRuns, getTestRun,
  listRunQuestions, saveQuestionEval,
  CRITERIA_META, type QGReport, type QGResult, type QGStatus, type TestRunResult,
  type QGQuestion, type HumanVerdict,
} from '../../api/adminTesting';

const VERDICTS: { id: HumanVerdict; label: string; active: string }[] = [
  { id: 'good', label: 'Good', active: 'bg-green-600 text-white border-green-600' },
  { id: 'needs_edit', label: 'Needs edit', active: 'bg-amber-500 text-white border-amber-500' },
  { id: 'reject', label: 'Reject', active: 'bg-red-600 text-white border-red-600' },
];

// One generated question: its automated QG verdicts + editable human evaluation.
function QuestionCard({ q }: { q: QGQuestion }) {
  const queryClient = useQueryClient();
  const [verdict, setVerdict] = useState<HumanVerdict | null>(q.human_verdict);
  const [rating, setRating] = useState<number | null>(q.human_rating);
  const [notes, setNotes] = useState<string>(q.human_notes ?? '');

  // Reseed when a different run's questions load into the same card slot.
  useEffect(() => {
    setVerdict(q.human_verdict); setRating(q.human_rating); setNotes(q.human_notes ?? '');
  }, [q.question_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: () => saveQuestionEval(q.question_id, { verdict, rating, notes: notes || null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['testing-questions'] }),
  });

  const dirty = verdict !== q.human_verdict || rating !== q.human_rating
    || (notes || '') !== (q.human_notes ?? '');

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-start gap-2 px-4 py-2.5 bg-gray-50 border-b border-gray-200">
        <span className="mt-0.5 text-xs font-semibold text-gray-400 shrink-0">Q{q.position}</span>
        <p className="flex-1 text-sm text-gray-900">{q.question_text}</p>
        <span className="text-[10px] text-gray-500 shrink-0 mt-0.5">
          {q.declared_difficulty}
          {q.classified_difficulty && q.classified_difficulty !== q.declared_difficulty
            && <span className="text-amber-600"> → {q.classified_difficulty}</span>}
        </span>
      </div>

      <div className="p-4 grid md:grid-cols-2 gap-4">
        {/* Automated eval */}
        <div>
          <div className="text-[11px] font-semibold text-gray-500 mb-1.5">
            Automated eval · <span className="text-green-700">{q.auto_pass} pass</span>
            {q.auto_fail > 0 && <span className="text-red-700"> · {q.auto_fail} fail</span>}
          </div>
          <div className="space-y-1">
            {q.auto_results.length === 0 && <p className="text-xs text-gray-400">No per-question checks applied.</p>}
            {q.auto_results.map((a, i) => (
              <div key={i} className="text-[11px] flex gap-1.5">
                {a.status === 'pass' && <CheckCircle2 className="w-3 h-3 text-green-600 mt-0.5 shrink-0" />}
                {a.status === 'fail' && <XCircle className="w-3 h-3 text-red-600 mt-0.5 shrink-0" />}
                {a.status === 'skip' && <MinusCircle className="w-3 h-3 text-gray-300 mt-0.5 shrink-0" />}
                <div>
                  <span className="font-mono text-gray-400">{a.criterion}</span>{' '}
                  <span className="text-gray-600">{a.reasoning}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Human eval */}
        <div className="space-y-2">
          <div className="text-[11px] font-semibold text-gray-500">Human eval</div>
          <div className="flex gap-1">
            {VERDICTS.map((v) => (
              <button
                key={v.id}
                onClick={() => setVerdict(verdict === v.id ? null : v.id)}
                className={`px-2 py-1 rounded border text-xs ${
                  verdict === v.id ? v.active : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
              >
                {v.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-0.5">
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} onClick={() => setRating(rating === n ? null : n)} title={`${n}/5`}>
                <Star className={`w-4 h-4 ${rating && n <= rating ? 'fill-amber-400 text-amber-400' : 'text-gray-300'}`} />
              </button>
            ))}
            {rating && <span className="text-[11px] text-gray-400 ml-1">{rating}/5</span>}
          </div>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)…"
            rows={2}
            className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-2 focus:ring-navy/20"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => save.mutate()}
              disabled={!dirty || save.isPending}
              className="inline-flex items-center gap-1 px-2.5 py-1 bg-navy text-white rounded text-xs font-medium hover:bg-navy-light disabled:opacity-40"
            >
              {save.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />} Save
            </button>
            {!dirty && q.reviewed_at && (
              <span className="text-[11px] text-gray-400">saved{q.reviewed_by ? ` · ${q.reviewed_by}` : ''}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function QuestionReview({ runId }: { runId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['testing-questions', runId],
    queryFn: () => listRunQuestions(runId),
  });
  const questions = data?.questions ?? [];
  if (isLoading) {
    return <div className="text-sm text-gray-400 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading questions…</div>;
  }
  if (questions.length === 0) {
    return (
      <p className="text-xs text-gray-400 border border-gray-200 rounded-lg p-3">
        No saved per-question records for this run (runs created before human-eval was added only have the criterion report above).
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <ClipboardList className="w-4 h-4" /> Generated questions — human review ({questions.length})
      </h3>
      {questions.map((q) => <QuestionCard key={q.question_id} q={q} />)}
    </div>
  );
}

const DIFFICULTIES = [
  { id: 'recall' as const, label: 'Recall', hint: 'definitions & facts' },
  { id: 'balanced' as const, label: 'Balanced', hint: 'recall + causal reasoning' },
  { id: 'deep' as const, label: 'Deep', hint: 'multi-hop mechanisms' },
];

function StatusPill({ status }: { status: QGStatus }) {
  const map = {
    pass: 'bg-green-100 text-green-800',
    fail: 'bg-red-100 text-red-800',
    skip: 'bg-gray-100 text-gray-500',
  } as const;
  const label = status === 'pass' ? 'PASS' : status === 'fail' ? 'FAIL' : 'N/A';
  return <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${map[status]}`}>{label}</span>;
}

function SummaryCard({ cid, counts }: { cid: string; counts: { pass: number; fail: number; skip: number } }) {
  const bad = counts.fail > 0;
  const ok = !bad && counts.pass > 0;
  const border = bad ? 'border-l-red-500' : ok ? 'border-l-green-500' : 'border-l-gray-300';
  return (
    <div className={`bg-white border border-gray-200 border-l-4 ${border} rounded-lg p-3`}>
      <div className="text-sm font-semibold text-gray-900">{cid}</div>
      <div className="text-[11px] text-gray-500 leading-snug min-h-[32px] mt-0.5">{CRITERIA_META[cid]}</div>
      <div className="text-xs mt-1.5 flex gap-3">
        <span className="text-green-700 font-semibold">{counts.pass} pass</span>
        <span className="text-red-700 font-semibold">{counts.fail} fail</span>
        <span className="text-gray-400">{counts.skip} n/a</span>
      </div>
    </div>
  );
}

function CriterionSection({ cid, rows }: { cid: string; rows: QGResult[] }) {
  return (
    <section className="border border-gray-200 rounded-lg overflow-hidden">
      <h3 className="text-sm font-medium text-navy bg-gray-50 px-4 py-2.5 border-b border-gray-200">
        <span className="text-xs text-gray-400 font-mono mr-2">{cid}</span>{CRITERIA_META[cid]}
      </h3>
      <div className="divide-y divide-gray-100">
        {rows.map((r, i) => (
          <div key={i} className={`px-4 py-2.5 text-xs flex gap-3 ${r.status === 'fail' ? 'bg-red-50/60' : ''}`}>
            <div className="w-14 shrink-0 pt-0.5">
              {r.status === 'pass' && <CheckCircle2 className="w-3.5 h-3.5 text-green-600 inline" />}
              {r.status === 'fail' && <XCircle className="w-3.5 h-3.5 text-red-600 inline" />}
              {r.status === 'skip' && <MinusCircle className="w-3.5 h-3.5 text-gray-300 inline" />}
              <span className="ml-1"><StatusPill status={r.status} /></span>
            </div>
            <div className="flex-1 min-w-0">
              {r.question
                ? <div className="text-gray-800">{r.question}
                    {r.declared && <span className="text-gray-400 ml-1">({r.declared})</span>}
                  </div>
                : <div className="text-gray-400 italic">batch-level check</div>}
              <div className="text-gray-500 mt-0.5">{r.reasoning}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ReportView({ report, count }: { report: QGReport; count: number }) {
  const totalFail = Object.values(report.summary).reduce((a, s) => a + (s.fail || 0), 0);
  return (
    <div className="space-y-5">
      <div className={`flex items-center gap-2 p-3 rounded-lg text-sm border ${
        totalFail > 0 ? 'bg-red-50 border-red-200 text-red-800' : 'bg-green-50 border-green-200 text-green-800'}`}>
        {totalFail > 0 ? <XCircle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
        <span>
          Graded <strong>{count}</strong> generated question{count === 1 ? '' : 's'} against{' '}
          <strong>{report.graph.node_count}</strong> concepts / <strong>{report.graph.edge_count}</strong> edges —{' '}
          {totalFail > 0 ? <strong>{totalFail} check(s) failed</strong> : 'all checks passed'}.
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {Object.keys(CRITERIA_META).map((cid) => (
          <SummaryCard key={cid} cid={cid} counts={report.summary[cid] || { pass: 0, fail: 0, skip: 0 }} />
        ))}
      </div>

      <div className="text-xs text-gray-500 border border-gray-200 border-l-4 border-l-navy/40 bg-gray-50 rounded-lg p-3">
        <strong className="text-gray-700">QG-07 &amp; QG-08 not shown:</strong> answerability agreement and
        item-discrimination need human-rater and pilot-cohort data. QG-08 can later reuse the agent-simulation
        cohort under <em>Simulations</em>.
      </div>

      {Object.keys(CRITERIA_META).map((cid) => {
        const rows = report.results.filter((r) => r.criterion === cid);
        return rows.length ? <CriterionSection key={cid} cid={cid} rows={rows} /> : null;
      })}
    </div>
  );
}

export default function AdminTesting() {
  const queryClient = useQueryClient();
  const [courseId, setCourseId] = useState('');
  const [difficulty, setDifficulty] = useState<'recall' | 'balanced' | 'deep'>('balanced');
  const [count, setCount] = useState(6);
  const [error, setError] = useState('');
  const [result, setResult] = useState<TestRunResult | null>(null);

  const { data: subjectsData } = useQuery({
    queryKey: ['testing-subjects'],
    queryFn: listTestingSubjects,
  });
  const subjects = subjectsData?.subjects || [];

  const { data: historyData } = useQuery({
    queryKey: ['testing-runs'],
    queryFn: listTestRuns,
  });
  const history = historyData?.runs || [];

  const runMutation = useMutation({
    mutationFn: () => createTestRun({ course_id: courseId, difficulty, count }),
    onSuccess: (res) => {
      setResult(res);
      if (res.status === 'error') setError(res.message || 'Generation failed.');
      queryClient.invalidateQueries({ queryKey: ['testing-runs'] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Test run failed.'),
  });

  const openRun = useMutation({
    mutationFn: (runId: string) => getTestRun(runId),
    onSuccess: (res) => { setResult(res); setError(''); },
  });

  const selected = subjects.find((s) => s.course_id === courseId);
  const busy = runMutation.isPending || openRun.isPending;

  const run = () => {
    if (!courseId) { setError('Pick a subject first.'); return; }
    setError(''); setResult(null);
    runMutation.mutate();
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <FlaskConical className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">Question-Generation Test Bench</h1>
          <p className="text-sm text-muted">
            Source a subject's concept graph, generate a fresh question batch with the real generator,
            and grade it against QG-01…QG-06.
          </p>
        </div>
      </div>

      {/* Run setup */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
          <select
            value={courseId}
            onChange={(e) => setCourseId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
          >
            <option value="">Select a subject…</option>
            {subjects.map((s) => (
              <option key={s.course_id} value={s.course_id} disabled={!s.testable}>
                {s.course_name} {s.testable ? `· ${s.node_count} concepts` : '· no graph yet'}
              </option>
            ))}
          </select>
          {selected && !selected.testable && (
            <p className="text-xs text-amber-700 mt-1">This subject has no concept graph — build it before testing.</p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Difficulty</label>
            <select
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value as 'recall' | 'balanced' | 'deep')}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
            >
              {DIFFICULTIES.map((d) => (
                <option key={d.id} value={d.id}>{d.label} — {d.hint}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Questions to generate</label>
            <input
              type="number" min={1} max={20} value={count}
              onChange={(e) => setCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
            />
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0" /> {error}
          </div>
        )}

        <button
          onClick={run}
          disabled={busy || !courseId || (selected && !selected.testable)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50"
        >
          {runMutation.isPending
            ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating &amp; grading…</>
            : <><Play className="w-4 h-4" /> Run tests</>}
        </button>
        {runMutation.isPending && (
          <p className="text-xs text-gray-400">Calling the live generator, then embedding + grading — this can take ~10–30s.</p>
        )}
      </div>

      {/* Report */}
      {result?.report && (
        <ReportView report={result.report} count={result.generated_count || result.report.results.length} />
      )}

      {/* Per-question human review */}
      {result?.status === 'completed' && result.run_id && (
        <QuestionReview runId={result.run_id} />
      )}

      {/* History */}
      {history.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <History className="w-4 h-4" /> Recent runs
          </h2>
          <div className="bg-white border border-gray-200 rounded-lg divide-y divide-gray-100">
            {history.map((r) => (
              <button
                key={r.run_id}
                onClick={() => openRun.mutate(r.run_id)}
                className="w-full text-left px-4 py-2.5 text-sm flex items-center gap-3 hover:bg-gray-50"
              >
                <span className="flex-1 min-w-0 truncate text-gray-800">{r.course_name}</span>
                <span className="text-xs text-gray-400">{r.difficulty}</span>
                <span className="text-xs text-gray-400 tabular-nums">{r.generated_count} q</span>
                <span className={`text-xs font-medium tabular-nums ${r.fail_count > 0 ? 'text-red-600' : 'text-green-600'}`}>
                  {r.fail_count > 0 ? `${r.fail_count} fail` : 'all pass'}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
