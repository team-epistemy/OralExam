# End-to-End Testing Playbook (PDF-driven)

This document drives a **testing agent** (or a human) to validate the full Epistemy
platform against the **deployed app**, using a real subject **PDF** as the course
material. It exercises every professor and student user-journey and reports what
works and what's broken.

## How it works

1. **Drop a subject PDF** into `contentIngestion/test_materials/` (e.g.
   `operations.pdf`, `biology.pdf`, `finance.pdf`). The filename becomes the
   course/subject name. A sample (`sample-operations.pdf`) is included.
2. **Run the harness:**
   ```bash
   cd contentIngestion
   python journey_e2e.py                       # uses the first *.pdf in test_materials/
   python journey_e2e.py path/to/subject.pdf   # or an explicit PDF
   ```
3. **Read the report.** The harness prints a PASS/FAIL table per journey and exits
   non-zero if any step failed. Every FAIL row names the endpoint and HTTP status.

## Environment

- **Target** (override with `BASE`): `http://epistemy-m3-int-571630445.us-west-2.elb.amazonaws.com`
- **Seed logins:** professor `prof1@univ.edu` / `epistemy123`; students
  `student1@univ.edu` … / `student123`. Org (tenant): `epistemy`.
- Stdlib only — no `pip install` needed.

## What the harness covers

**Professor journey**
- Login → dashboard → list courses
- Upload the **PDF** → register → **ingest to `ready`** (exercises the PDF extractor)
- **Concept graph:** rebuild (Claude) → GET graph (verifies `edges` are relation
  objects the UI renders) → neighbors
- **Questions:** generate (Claude) → list → approve
- **Exam builder:** build 3 variants (even/core/frontier)
- **Assignment:** create → list → monitor sessions

**Student journey (student1, full multi-turn)**
- Login → dashboard → **list my assignments**
- Start exam → **answer every question** (multi-turn / adaptive pacing) → status →
  **SSE stream** reachable → complete → **results** (per-question scores)

**Grading flow (professor)**
- Get session grades → release grades → grade override → get per-turn evaluation

**Multi-student isolation**
- student2 takes the same assignment and sees **only their own** results

## Exam-taker agent (LLM-driven student)

`journey_e2e.py` proves the endpoints work but answers every question with one
canned string. `exam_taker_agent.py` is an **agent that actually takes the oral
exam**: it logs in as a student, answers each question in free-form prose, and
follows the examiner's adaptive Socratic **probes** turn by turn — like a real
student — then reads back its grade.

It runs three competence personas as three seeded students and checks the grader
**discriminates** quality:

```bash
export ANTHROPIC_API_KEY=sk-...              # answers are Claude-authored
python exam_taker_agent.py                   # auto-discovers an assignment
python exam_taker_agent.py --assignment <assignment_id>
python exam_taker_agent.py --levels strong,weak --max-followups 2
```

- **Personas:** STRONG / MEDIUM / WEAK, mapped to `student1/2/3@univ.edu`. The
  agent is never shown the answer key, so scores are earned.
- **Discrimination check:** prints `strong ≥ medium ≥ weak` and exits non-zero if
  the grader does **not** rank quality monotonically (so it can gate CI).
- **No key?** It still runs with persona-templated answers (clearly flagged) to
  exercise the plumbing offline.
- **Side effects:** it writes real exam sessions/turns/evaluations to the target
  backend and spends LLM tokens (its own answers + the backend's per-turn eval).

## Interpreting results / fixing

- A **FAIL** with HTTP `404`/`405` → a missing or misrouted endpoint (add/route it).
- A **FAIL** with HTTP `422` → request/response contract mismatch (align the model).
- A **FAIL** with HTTP `500` → server error; check the ECS `http` logs in CloudWatch
  log group `/epistemy/m3/dev`.
- `ingest -> ready` failing → the extractor or embedding step; check the `worker` logs.
- After fixing backend code, **redeploy** (rebuild image via CodeBuild + force a new
  ECS deployment) and re-run this harness to confirm.

## Notes

- The harness is **idempotent** on re-runs (courses/materials are name-resolved and
  re-created as new versions).
- `test_materials/*.pdf` are **git-ignored** except the bundled sample — drop your
  own subject PDFs freely.
- The PDF extractor, chunker, and PPTX/Markdown extractors also have **offline unit
  tests** in `tests/` that run in CI on every push.
