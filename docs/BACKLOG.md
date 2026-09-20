# Backlog

Deferred work, with enough context to pick up cold. Grouped by theme, roughly
ordered by value per unit of effort within each group.

## Observability

Current state: one CloudWatch log group (`/epistemy/m3/dev`, 7-day retention as of
2026-09-19). Zero metric filters, zero alarms, zero dashboards, no SNS topic,
Container Insights disabled. Logs are plain text, not structured, so every query is
substring matching. The only way to notice a problem today is to suspect one first
and go read raw logs.

This is not hypothetical: an invalid Anthropic API key degraded grading for hours on
2026-09-18 while the app returned HTTP 200s. Nothing alerted.

### 1. Metric filter + alarm on eval failures

`http_app.py:4300` logs `Bedrock Socratic eval failed: ...` whenever the evaluation
LLM call fails (bad key, 429, timeout, outage, unparseable JSON). Turn it into a
CloudWatch metric and alarm on it.

Would have caught the 2026-09-18 key outage within minutes. Pure infra — no
application change. Now more valuable than before, because a metric derived from a
log line persists ~15 months even after the log event is deleted at 7 days, whereas
the corrupted `eds_components` rows stay in the database indefinitely.

### 2. Alarm on ALB latency and error rate

`TargetResponseTime` p95, `HTTPCode_Target_5XX_Count`, `HTTPCode_Target_4XX_Count`.
Already collected by the ALB at no cost; nothing consumes them. The latency alarm is
directly relevant to the exam-speed work — it tells you if a deploy made turns
slower. The 4XX alarm also covers credential-scanner traffic (see below).

### 3. Enable Container Insights on `epistemy-dev`

Currently `disabled`, so there are no CPU/memory/task-count metrics. Worth doing once
background rubric-generation threads are doing real work in the request process.

### 4. Structured (JSON) logging

The app uses a bare `logging.getLogger(__name__)` with no formatter. Substring
matching is fragile — a filter for `perf-trace` also matches HTTP access lines
containing `/api/admin/perf/`. JSON fields would make metric filters and queries
reliable.

## Correctness

### 5. Eval-failure path writes false zeros — BUG

`use_eds_formula` is decided at `http_app.py:4205` from the rubric alone, *before* the
LLM call. When `call_bedrock` raises, the `except` at 4300 recovers feedback text from
`_heuristic_eval` but never resets `parsed` (still `{}`) and never flips
`use_eds_formula`. So line 4353 reads `parsed.get("eds", {})` → empty lists →
`node_score = 0`, `edge_score = 0`, and a near-zero EDS score is committed with
`eds_components` populated.

An infrastructure failure is therefore recorded as a confident measurement that the
student demonstrated nothing. Indistinguishable from a genuinely poor answer. It
corrupts three consumers: `_prior_coverage` (next probe sees no coverage), the stored
score, and the professor's analytics (`aggregate_performance` counts it as real).

Fix is ~5 lines at 4300: on failure, either fall through to the legacy bucket branch
or skip the evaluation write entirely.

### 6. Audit and clean evaluations written during the key outage

Rows in `evaluation` with non-null `eds_components` but all-zero scores, written
between roughly 2026-09-18 and the key rotation at 21:35 PT. They are polluting the
Performance dashboard now.

Blocked on database access: `epistemy-process-db` is not publicly accessible
(`PubliclyAccessible: false`), so the query has to run from inside the VPC or through
an admin endpoint.

### 7. Degraded-grading marker

When a turn is graded without a rubric, the no-path branch (`http_app.py:4308-4330`)
inserts without `eds_components`, leaving NULL. `_prior_coverage` skips NULL rows
silently (`http_app.py:93-94`), so the turn contributes no coverage and the next probe
can re-ask ground the student already covered.

Write `{"degraded": true}` instead of NULL so these turns are findable later rather
than indistinguishable from rows on an un-migrated database.

### 8. Retroactive `eds_components` backfill

`student_answer` is persisted in `session_turn` (`http_app.py:4172`), so a turn graded
without a rubric can be re-scored once the rubric lands: re-run extraction against the
stored answer text and update the evaluation row. ~40 lines plus tests, hooked into
`_store_expected_path`. Also repairs rows already damaged by the old inline-generation
behaviour.

### 9. Decide whether a failed eval should surface to the student

Today a failed LLM call silently produces a keyword-matched probe from
`_heuristic_eval` (scans for `"because"`, `"therefore"`, length > 40) and the exam
advances. The student is never told. Arguably the turn should fail and be retryable
instead. Product decision, not just a code fix: a mid-exam error is bad UX, but so is
being graded by a regex.

Made worse by item 17: nothing short-circuits the flow, so during an outage the
student is probed the full five times per question with the same generic fallback
line, because advancement is a turn count and ignores the (meaningless) verdict.

As of `eb0d15a` the fallback copy at least says the system struggled rather than
grading the answer ("I had difficulty processing your response."), but the flow is
unchanged.

## Durability

### 10. Lease column + sweeper for rubric generation

Background rubric fills run on plain `threading.Thread`. If the container restarts
mid-flight the work is lost with no retry. The work item is already durable (a
`question` row with an empty `expected_path` *is* the pending job) — what is missing is
a retry trigger and cross-process deduplication.

Add `question.path_claimed_at timestamptz` plus a partial index, then claim atomically:

```sql
UPDATE question SET path_claimed_at = now()
WHERE question_id IN (
  SELECT question_id FROM question
  WHERE (expected_path IS NULL OR jsonb_array_length(expected_path->'nodes') = 0)
    AND (path_claimed_at IS NULL OR path_claimed_at < now() - interval '5 minutes')
  LIMIT 10 FOR UPDATE SKIP LOCKED)
RETURNING question_id, text, concept_ids;
```

A crashed task leaves a stale claim that expires and is re-picked up. `FOR UPDATE SKIP
LOCKED` gives concurrent tasks disjoint rows, replacing the `_INFLIGHT_PATHS` set which
only dedupes within one process. A ~60s sweeper thread also delivers the backfill for
existing questions for free.

Trade-off: a sweeper in the web process competes with request handling for CPU and DB
connections. Fine at current scale, wrong under real traffic — at that point this
belongs in a worker service consuming SQS (`backend/async_jobs/` and the
`epistemy-ingest-dev` queue already exist, but no worker service is deployed and
`SqsQueue` is typed to `IngestMessage`).

## Latency (not yet scoped into a branch)

### 11. Save a round-trip on TTS

TTS is a separate endpoint (`http_app.py:1372`), not part of `submit_answer`. Each turn
is therefore two sequential browser round-trips: submit the answer, get probe text
back, then request audio. Either return audio with the answer or start synthesis while
the eval response streams.

### 12. Real per-turn timings in the Performance dashboard

The admin perf probe is synthetic — fixed short question, admin-triggered, stored in
`perf_probe`. Real student turns are never measured. Stopwatch logging around the
rubric wait, eval call, and TTS call gives the true split via CloudWatch; surfacing it
in the dashboard needs a new table, an endpoint, and frontend work.

Note that server-side timers cannot see the gap *between* the two HTTP calls, nor
browser upload and audio-playback time. If felt latency exceeds logged numbers, the
remainder is in the frontend and needs timing in `TakeExam.tsx`.

### 13. Pre-synthesize question audio

Question text is known at assign time. Synthesizing and caching its audio removes TTS
latency from the first turn of every question entirely.

## Security / hygiene

### 14. Consider restricting dev ALB ingress

The dev ALB is publicly reachable and is being swept by credential scanners — 140
probes for `/.env`, `/.aws/credentials`, `/.docker/config.json`,
`/.anthropic/config.json` and similar in a single window. **All returned 401; none
returned 200, so nothing leaked.** This is routine background noise for any public
endpoint, not evidence of targeting.

If dev does not need to be internet-facing, restricting the ALB security group to a
known IP range removes the exposure. Otherwise item 2's 4XX alarm at least makes a
change in volume visible.

### 15. Anthropic key is cached for the process lifetime

`_anthropic_client` is a module-level global (`bedrock_helper.py:69-79`), so the key is
read from Secrets Manager once per process. Rotating the secret has no effect until the
task restarts — `aws ecs update-service --cluster epistemy-dev --service epistemy-m3-dev
--force-new-deployment`. Worth either documenting prominently or adding a TTL/refresh so
a rotation takes effect on its own.

### 16. Pre-existing broken test file

`tests/test_spreadsheet_and_reference_types.py` fails collection with
`ImportError: cannot import name 'REFERENCE_ONLY_TYPES' from 'backend.models'`. Untracked
and unrelated to any current work; currently excluded with `--ignore` when running the
suite. Either finish it or delete it.

## Flow and semantics of `adequate`

### 17. `adequate` does not gate advancement — the turn count does

`TakeExam.tsx:781-782` decides advancement purely on attempts:

```js
const maxed = attempt >= MAX_TURNS;   // MAX_TURNS = 5
const advance = maxed;
```

`adequate` is never consulted. A student gets up to five sub-turns per question
whether the first answer was excellent or empty — the examiner keeps probing and only
stops at the cap. Decide whether that is intended. If a genuinely complete answer
should end the question early, the gate has to be added; if five probes is the design,
the naming below should stop implying otherwise.

### 18. `adequate` means different things in the two scoring branches

Defined in the prompts (`http_app.py:4233`, `4263-4265`) as "the student showed clear
mechanistic/causal reasoning", defaulting to false unless genuinely thorough — so a
factually correct but shallow answer is `adequate=false`.

- **No rubric (legacy):** it *is* the grade. `http_app.py:4308-4315` maps it to a
  three-valued score — not answered → 0, adequate → 10, otherwise → 4.
- **With a rubric (EDS):** it does not affect the numeric score at all. The score comes
  from `node_score`, `edge_score`, `r_gate`, `gen_score`. Here `adequate` only sets the
  `eds_bucket` label (`"high"` / `"medium"` / `"low"`, line 4339) and steers the model's
  own probe generation.

The name reads like a pass/fail threshold but under EDS it is a label, not a gate. If
it is ever surfaced to professors as "passed", it will not mean what they assume.
Worth either renaming or documenting at the point of display.

### 19. Fallback "adequate" branch returns empty feedback and probe

`_heuristic_eval` returns `""` for both when the keyword matcher judges the answer
adequate, so the student gets silence and the turn advances with no acknowledgement.
During an outage, an answer that merely happens to contain `"because"` gets a silent
pass. Arguably this branch should also say the system had difficulty, since the
fallback's "adequate" verdict is exactly as meaningless as its "inadequate" one.

Related: the keyword matching is naive substring matching, so `"since"` matches inside
`"sincerely"` — *"Sincerely, I have absolutely no recollection of this topic at all."*
is 65 characters and contains `since`, so it scores as adequate.
