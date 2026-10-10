# Speaking Prototype Spec (v0.1)

**Status:** Draft for review
**Based on:** *Epistemy Speaking PRD and Test Script* and the current code on `main` (commit `c1fc359`, 2026-09-20)
**Owner:** Uthira

---

## 1. Goal

Prove the core idea of the speaking product in the smallest possible build: an AI examiner that **adapts its follow-up questions to the student's level, turn by turn, and grades every turn against the CEFR speaking grid.**

This is a new exam type that runs **alongside** the existing Socratic exam. Nothing about existing assignments, the student exam page, or demo links changes.

## 2. Scope

### In v0.1

- One fixed Part 2 question (long turn), answered orally.
- Part 3: exactly 4 adaptive follow-ups, answered orally, laddered up or down the CEFR scale.
- Every turn graded on the CEFR grid.
- A final CEFR band per student, visible through the existing grades flow.

### Deferred

| Item | Why deferred |
|---|---|
| Part 1 interview and level routing | Not needed to test the adaptive loop |
| Two Part 2 cards (lower / higher) | One card is enough for v0.1 |
| Pronunciation criterion | The grader only sees a transcript in v0.1 |
| Demo-link support | Demo endpoints and `DemoExam.tsx` stay untouched |
| "Exam type" option in the professor UI | Exam is created by one API call in v0.1 |
| Marker view with criterion scores and evidence links | Scores are stored; a dedicated view comes later |
| Repeat-request handling (PRD C-3) | See Known limits |

## 3. Test flow

The existing student exam page already gives each question **5 turns** (`MAX_TURNS = 5` in `TakeExam.tsx`). That maps exactly onto this test:

| Turn | Part | What happens |
|---|---|---|
| 1 | Part 2: long turn | Student answers the fixed question. Graded; sets the starting band. |
| 2 | Part 3: follow-up 1 | Follow-up targeted by the ladder rule. Graded. |
| 3 | Part 3: follow-up 2 | Same. |
| 4 | Part 3: follow-up 3 | Same. |
| 5 | Part 3: follow-up 4 | Same. Final scores computed; exam page moves on. |

### Content (theme: "Where you live")

- **Part 2 question:** "Describe a place in your town you like to visit. Say where it is, what you do there, and why you like it."
- **Part 3 frame:** follow-ups stay on this theme and must build on what the student just said. The PRD opener "Should towns spend more on spaces for young people?" is available as a frame for higher-band follow-ups.

## 4. Ladder rule (Part 3)

After each turn, the examiner compares that turn's band with the band the follow-up was aimed at (the **target band**):

| Turn result | Next follow-up |
|---|---|
| Clearly **better** than the target band | One band **up** |
| Clearly **worse** | One band **down** |
| **Same** band | Record it as a proven band, then probe one band **up** |
| An up-probe comes back at the **same** proven band | Settle back to the proven band for the next follow-up |

- Part 3 always runs the full **4 follow-ups**. The rule only decides the direction of each question; it never ends Part 3 early.
- Bands are clamped to A1 to C2.
- **Example:** Part 2 grades B1. Follow-up 1 probes B2. The answer comes back B1, so follow-up 2 settles back at B1. If that is B1 again, follow-up 3 probes B2 again.

### What "probe up" and "probe down" mean

- **Up:** ask the student to justify, compare, hypothesise or persuade ("What might happen if…?", "How would you convince someone who disagrees?").
- **Down:** a simpler, more concrete question or a rephrase ("Do you have a place like that near you?").

### Examiner conduct (from the PRD)

- Never correct mistakes, praise, or model better language. Neutral acknowledgments only.
- Every follow-up must reference something the student actually said.
- Stay within the theme; never introduce new topics.

## 5. Scoring

### Criteria

| Criterion | Scored on | Scale |
|---|---|---|
| Range | Every turn | 1–6 |
| Accuracy | Every turn | 1–6 |
| Fluency | Every turn | 1–6 |
| Coherence | Every turn | 1–6 |
| Interaction | Part 3 turns only (turns 2–5) | 1–6 |

- **Scale:** 1 = A1, 2 = A2, 3 = B1, 4 = B2, 5 = C1, 6 = C2. Half-points allowed. Expected working range 2–5.
- **Descriptors:** CEFR Table 3, *Qualitative aspects of spoken language use* (Council of Europe), used as the grading rubric in the prompt.

### Per-turn band (drives the ladder)

Average of that turn's criterion scores, rounded to the nearest whole number, mapped to a band.

### Final result

- Each criterion = average of its per-turn scores.
- **Overall score** = average of the five criterion averages.
- **Overall band** = overall score rounded, mapped to A1–C2.

## 6. Implementation

### Principle: add, don't modify

The new exam type reuses the existing exam page, speech capture, TTS, timer, session handling and grade release. It adds new backend code and touches existing code in exactly one guarded place.

### New files

| File | Contents |
|---|---|
| `backend/communication/__init__.py` | Package marker |
| `backend/communication/content.py` | Part 2 question, theme frame, CEFR descriptors |
| `backend/communication/ladder.py` | Pure functions for the ladder rule and score aggregation (unit-testable, no I/O) |
| `backend/communication/turn.py` | `handle_turn(...)`: builds the grading prompt, calls `call_bedrock`, applies the ladder, writes the `evaluation` row, returns the response |
| `tests/test_communication_ladder.py` | Unit tests for the ladder and scoring math |

### New endpoint

`POST /api/courses/{course_id}/communication-assignments`

Body: `{ "title": "..." }`

1. Inserts the Part 2 prompt as a normal `question` row (`question_type = 'oral'`, `status = 'approved'`).
2. Creates a `question_set` with that one question.
3. Creates the `assignment` with status `active` and config `{"exam_kind": "communication", "max_questions": 1, ...}`.

The existing create-assignment endpoint is not modified.

### The one guarded edit

In `submit_answer` (`backend/app/http_app.py`, `POST /api/sessions/{session_id}/answer`), right after the assignment lookup:

```python
if (assignment_config or {}).get("exam_kind") == "communication":
    return communication.handle_turn(repo, settings, session_id, srow, req)
```

Assignments without `exam_kind` never enter this branch, so the existing Socratic/EDS flow is unchanged.

### Response contract

`handle_turn` returns the same shape the exam page already reads:

```json
{
  "answered": true,
  "adequate": false,
  "feedback": "internal grader note (never shown to the student)",
  "probe": "next follow-up question (spoken via TTS)",
  "eds_delta": 0,
  "eds_question": 0.58,
  "eds_components": null
}
```

- `probe`: the laddered follow-up. On turn 5 the page shows "Answer recorded." itself.
- `eds_question`: the running overall CEFR score scaled to 0–1 (score ÷ 6), so the live gauge on the exam page means something.

### Storage

- **Per turn:** one `evaluation` row. `raw_llm_output` holds the criterion scores, turn band, target band, next direction (up / same / down) and the follow-up text.
- **Grade:** grade release sums `eds_score` across the session's evaluations. The handler writes `eds_score = 0` on turns 1–4 and the overall score ÷ 6 on turn 5, so the released grade equals the overall CEFR score with no change to grade release.

### Not touched

`TakeExam.tsx`, `DemoExam.tsx`, the demo endpoints, `CreateAssignment.tsx`, the existing create-assignment endpoint, the Socratic/EDS evaluator, question generation, ingestion, `exam_taker_agent.py`, `journey_e2e.py`.

## 7. Known limits in v0.1

1. **Fluency is graded from a transcript.** Browser speech-to-text removes pauses and hesitations, so Fluency scores will run generous.
2. **Repeat requests count as a turn.** The exam page counts turns on the client, so a "can you repeat that?" uses one of the 5 turns. The handler should rephrase and keep the same target band.
3. **CEFR stored in EDS columns.** Writing the CEFR score into `eds_score` is a prototype shortcut. The PRD lists reusing EDS as a v1 non-goal, so a dedicated store is needed before a pilot.
4. **Professor grade view** shows the final score only; criterion detail lives in `raw_llm_output` until a marker view exists.

## 8. Acceptance checks

- [ ] Creating a communication assignment via the new endpoint returns an active assignment with `exam_kind: "communication"`.
- [ ] A student completes the exam on the existing exam page by voice: 1 Part 2 answer + 4 follow-ups.
- [ ] Each follow-up references the student's previous answer and follows the ladder rule.
- [ ] Five `evaluation` rows are written, each with criterion scores and band data.
- [ ] Released grade equals the overall CEFR score ÷ 6.
- [ ] Ladder unit tests pass, including the "same band → probe up → settle back" case.
- [ ] Existing Socratic assignments and demo links behave exactly as before (existing test suite passes unchanged).

## 9. Open questions for v0.2

- Add Part 1 and routing between two Part 2 cards?
- Move CEFR results out of EDS columns into their own table?
- Capture audio alongside the transcript so Fluency and Pronunciation can be graded properly?
- Add the "Exam type" option to `CreateAssignment.tsx`?
