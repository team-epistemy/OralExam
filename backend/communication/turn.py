"""One turn of a communication (CEFR speaking) exam.

`handle_turn` is the whole exam type: `submit_answer` hands it the request when
the assignment's config says `exam_kind == "communication"`, and it records the
turn, grades it on the CEFR grid, applies the Part 3 ladder to pick the next
follow-up, and answers in the same shape the student exam page already reads.

Grading and examining are one LLM call: the grader has to read the answer anyway,
and the follow-up has to be built from the same reading, so splitting them would
double the latency the student waits through for no gain.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from backend.bedrock_helper import call_bedrock
from backend.communication import content as C
from backend.communication import ladder as L

logger = logging.getLogger(__name__)

# Bounded like the Socratic evaluator: CloudFront cuts the student off at 60s,
# so one attempt with a 20s ceiling, then degrade to a fallback score.
_LLM_TIMEOUT_S = 20
# Five criterion scores plus a one-line note.
_LLM_MAX_TOKENS = 500
# One question. Written by a fast model: picking the band and the question type
# is already done in Python, so this call only has to phrase it.
_FOLLOW_UP_MAX_TOKENS = 200
_FOLLOW_UP_MODEL = "claude-haiku-4-5-20251001"


def handle_turn(repo, settings, session_id: str, srow, req, config=None) -> dict:
    """Record, grade and ladder one communication-exam turn.

    `srow` is the exam_session row `submit_answer` already fetched:
    (session_id, assignment_id, student_id, course_id, status,
     current_turn_index, org_id).

    `config` is that assignment's config, which carries the theme the Part 3
    follow-ups must stay on. The theme is per-exam (a professor picks a card or
    writes their own question), so it cannot be read off a constant.
    """
    config = config or {}
    theme = config.get("cefr_theme") or ""
    high_band_frame = config.get("cefr_high_band_frame") or ""
    assignment_id = str(srow[1])
    student_id = srow[2]
    course_id = str(srow[3])
    org_id = str(srow[6])
    question_index = req.question_index

    question_id, question_text = _load_question(
        repo, assignment_id, question_index)

    turn_id, turn_number = _record_turn(
        repo, session_id, org_id, question_index, question_id, req.answer_text)

    prior = _prior_turns(repo, session_id)
    target_band, proven_band = _ladder_state(prior)
    asked_type_id = prior[-1]["next_type_id"] if prior else None
    used_type_ids = [p["next_type_id"] for p in prior if p.get("next_type_id")]
    is_part3 = turn_number >= C.PART3_FIRST_TURN
    is_final = turn_number >= C.TOTAL_TURNS

    # Client-reported: the Azure assessment runs in the browser under a
    # short-lived token, so these measurements are as trustworthy as the
    # transcript beside them and no more. Fine for a prototype; a graded exam
    # would have to assess server-side from the audio.
    speech = _clean_speech(getattr(req, "speech", None))

    graded = _grade(settings, question_text, prior, req.answer_text,
                    turn_number, target_band, is_part3, theme, speech)

    scores = L.normalise_scores(graded, is_part3)
    band = L.turn_band(scores)
    repeat_request = bool(graded.get("repeat_request"))

    if repeat_request:
        # The student asked for a repeat rather than answering. It still burns a
        # turn (the exam page counts turns client-side), but nothing was
        # demonstrated: hold the ladder where it is and re-ask the same type.
        step = {"target_band": target_band if target_band is not None else band,
                "proven_band": proven_band, "direction": "same"}
    else:
        step = L.next_step(band, target_band, proven_band)

    # Running result over the answers given so far, so the live gauge on the exam
    # page tracks the CEFR score instead of sitting at zero until the last turn.
    scored_turns = [p["scores"] for p in prior if not p["repeat_request"]]
    if not repeat_request:
        scored_turns.append(scores)
    result = L.final_result(scored_turns)
    eds_question = round(result["overall_score"] / float(C.MAX_BAND), 4)

    # The next question is chosen from THIS turn's grade, which only exists once
    # the call above has returned — hence a second, much smaller call to write it.
    # Folding both into one call is what put the ladder a turn behind: the
    # follow-up had to be written before the answer it was supposed to react to
    # had been scored.
    follow_up, next_type_id = "", None
    if not is_final:
        # On a repeat request nothing was demonstrated, so re-ask the same type
        # rather than advancing through the bank.
        if repeat_request and asked_type_id:
            instruction, next_type_id = C.follow_up_instruction(
                step["target_band"], "same",
                [t for t in used_type_ids if t != asked_type_id],
                high_band_frame)
        else:
            instruction, next_type_id = C.follow_up_instruction(
                step["target_band"], step["direction"], used_type_ids,
                high_band_frame)
        follow_up = _write_follow_up(settings, question_text, prior,
                                     req.answer_text, theme, instruction,
                                     repeat_request)

    asked_type = C.question_type_by_id(asked_type_id)
    next_type = C.question_type_by_id(next_type_id)
    cefr = {
        "turn_number": turn_number,
        "part": "part3" if is_part3 else "part2",
        "scores": scores,
        "turn_band": band,
        "turn_band_name": L.band_name(band),
        "target_band": target_band,
        "target_band_name": L.band_name(target_band) if target_band else None,
        # The PISA question type this turn's answer was responding to, and the
        # one the next question uses. Stored so the ladder and the type rotation
        # are both auditable from the evaluation rows alone.
        "asked_type_id": asked_type_id,
        "asked_type_label": asked_type.label if asked_type else None,
        "next_target_band": step["target_band"],
        "next_target_band_name": L.band_name(step["target_band"]),
        "next_type_id": next_type_id,
        "next_type_label": next_type.label if next_type else None,
        "proven_band": step["proven_band"],
        "direction": step["direction"],
        "repeat_request": repeat_request,
        # The acoustic measurements this turn was graded with, stored so a band
        # can be audited against the evidence behind it — and so it is visible
        # when a turn was graded blind because the student used text mode.
        "speech": speech,
        # True when the grader call failed and this turn was scored at its target
        # band instead. Surfaced so a professor never reads a fallback score as a
        # real judgement.
        "degraded": bool(graded.get("degraded")),
        "follow_up": follow_up,
        "criterion_averages": result["criterion_averages"],
        "overall_score": result["overall_score"],
        "overall_band": result["overall_band"],
        "final": is_final,
    }

    # Grade release sums eds_score across the session's evaluations and divides
    # by max_questions (1 for this exam type), so writing the overall score once
    # on the last turn makes the released grade equal overall / 6 with no change
    # to grade release at all.
    eds_score = eds_question if is_final else 0.0

    _write_evaluation(repo, turn_id, org_id, course_id, student_id, question_id,
                      eds_score, cefr, graded.get("note", ""), follow_up,
                      eds_question)

    return {
        "answered": not repeat_request,
        # Never true: the exam page advances on adequate=true, and this exam type
        # always runs the full five turns.
        "adequate": False,
        "feedback": (graded.get("note") or "").strip(),
        "probe": follow_up,
        "eds_delta": 0,
        "eds_question": eds_question,
        "eds_components": None,
        # Per-turn CEFR grid for the dedicated CEFR speaking UI (band scores,
        # ladder target/direction, running overall). Additive: the Socratic/EDS
        # path never sets this, so that flow is unaffected.
        "cefr": cefr,
    }


def _clean_speech(raw) -> Optional[dict]:
    """Keep only the known acoustic fields, as finite numbers.

    The payload is client-supplied, so it is filtered rather than trusted: an
    unexpected key never reaches the prompt, and a string or NaN never reaches
    the rounding in `speech_evidence_block`.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for key, _label in C.SPEECH_FIELDS:
        value = raw.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if num != num or num in (float("inf"), float("-inf")):
            continue
        out[key] = num
    return out or None


# ── Persistence ──────────────────────────────────────────────────────────────

def _load_question(repo, assignment_id: str, question_index: int) -> Tuple[str, str]:
    """Return (question_id, text) for the assignment's question at this index."""
    with repo.conn.cursor() as cur:
        cur.execute(
            """SELECT q.question_id, q.text
               FROM assignment a
               JOIN question_set_membership qsm
                 ON qsm.question_set_id = a.question_set_id
               JOIN question q ON q.question_id = qsm.question_id
               WHERE a.assignment_id = %s::uuid AND qsm.position = %s""",
            (assignment_id, question_index),
        )
        row = cur.fetchone()
    if not row:
        from backend.api.service import AuthorizationError
        raise AuthorizationError(
            f"no question at index {question_index} in this assignment")
    return str(row[0]), row[1]


def _record_turn(repo, session_id: str, org_id: str, question_index: int,
                 question_id: str, answer_text: str) -> Tuple[str, int]:
    """Insert this sub-turn and return (turn_id, 1-based turn number)."""
    now = datetime.now(timezone.utc)
    with repo.conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM session_turn
               WHERE session_id = %s::uuid AND turn_index = %s""",
            (session_id, question_index),
        )
        sub_turn_index = cur.fetchone()[0]

    with repo.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO session_turn
               (turn_id, session_id, org_id, turn_index, sub_turn_index,
                question_id, student_answer, answered_at)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::uuid, %s, %s)
               ON CONFLICT (session_id, turn_index, sub_turn_index)
               DO UPDATE SET student_answer = EXCLUDED.student_answer,
                             answered_at = EXCLUDED.answered_at
               RETURNING turn_id""",
            (str(uuid.uuid4()), session_id, org_id, question_index,
             sub_turn_index, question_id, answer_text, now),
        )
        turn_id = str(cur.fetchone()[0])

    with repo.conn.cursor() as cur:
        cur.execute(
            """UPDATE exam_session
               SET current_turn_index = GREATEST(current_turn_index, %s + 1)
               WHERE session_id = %s::uuid""",
            (question_index, session_id),
        )
    repo.conn.commit()
    return turn_id, sub_turn_index + 1


def _prior_turns(repo, session_id: str) -> List[dict]:
    """Return the already-graded turns of this session, oldest first.

    Each entry carries the student's answer plus the `cefr` block written by the
    turn that graded it, which is where the ladder state lives.
    """
    with repo.conn.cursor() as cur:
        cur.execute(
            """SELECT st.student_answer, e.raw_llm_output
               FROM session_turn st
               JOIN evaluation e ON e.turn_id = st.turn_id
               WHERE st.session_id = %s::uuid
               ORDER BY st.turn_index, st.sub_turn_index""",
            (session_id,),
        )
        rows = cur.fetchall()

    out: List[dict] = []
    for answer, raw in rows:
        raw = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        cefr = raw.get("cefr") or {}
        if not cefr:
            continue  # not a communication turn; nothing to recover
        out.append({
            "answer": answer or "",
            "scores": cefr.get("scores") or {},
            "turn_band": cefr.get("turn_band"),
            "next_target_band": cefr.get("next_target_band"),
            # The question type this turn's follow-up used, so the next turn can
            # avoid repeating it while the student stays at one band.
            "next_type_id": cefr.get("next_type_id"),
            "proven_band": cefr.get("proven_band"),
            "follow_up": cefr.get("follow_up") or "",
            "repeat_request": bool(cefr.get("repeat_request")),
        })
    return out


def _ladder_state(prior: List[dict]) -> Tuple[Optional[int], Optional[int]]:
    """Recover (target_band, proven_band) for the turn about to be graded.

    Both are None on the Part 2 long turn, which has no target to be measured
    against and sets the starting band itself.
    """
    if not prior:
        return None, None
    last = prior[-1]
    return last.get("next_target_band"), last.get("proven_band")


def _write_evaluation(repo, turn_id: str, org_id: str, course_id: str,
                      student_id: str, question_id: str, eds_score: float,
                      cefr: dict, note: str, follow_up: str,
                      eds_question: float) -> None:
    """Write this turn's evaluation row, CEFR detail included."""
    payload = {
        "answered": not cefr["repeat_request"],
        "adequate": False,
        "feedback": note,
        "probe": follow_up,
        "eds_delta": 0,
        "eds_question": eds_question,
        "exam_kind": "communication",
        "cefr": cefr,
    }
    bucket = ("high" if eds_question >= 0.7
              else ("medium" if eds_question >= 0.3 else "low"))
    with repo.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO evaluation
               (evaluation_id, turn_id, org_id, course_id, student_id,
                question_id, eds_score, eds_bucket, raw_llm_output)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s,
                       %s::uuid, %s, %s, %s::jsonb)
               ON CONFLICT (turn_id) DO UPDATE
               SET eds_score = EXCLUDED.eds_score,
                   eds_bucket = EXCLUDED.eds_bucket,
                   raw_llm_output = EXCLUDED.raw_llm_output""",
            (str(uuid.uuid4()), turn_id, org_id, course_id, student_id,
             question_id, eds_score, bucket, json.dumps(payload)),
        )
    repo.conn.commit()


# ── Grading (call 1: scores only) ────────────────────────────────────────────

def _grade(settings, question_text: str, prior: List[dict], answer_text: str,
           turn_number: int, target_band: Optional[int],
           is_part3: bool, theme: str = "",
           speech: Optional[dict] = None) -> dict:
    """Score this turn on the CEFR grid. Writes no question."""
    system = _build_grade_prompt(question_text, turn_number, is_part3, theme,
                                 speech)
    user = _build_transcript(question_text, prior, answer_text)
    try:
        return call_bedrock(settings, system, user,
                            max_tokens=_LLM_MAX_TOKENS, temperature=0.2,
                            retries=1, timeout=_LLM_TIMEOUT_S)
    except Exception as err:
        # A failed grade must not end the exam. Score the turn at whatever band
        # the question was aimed at — the least-information guess — and record
        # that this happened so nobody reads it as a real judgement.
        logger.warning("CEFR grading failed on turn %d: %s", turn_number, err)
        band = float(target_band or 3)
        fallback = {c: band for c in C.CORE_CRITERIA}
        if is_part3:
            fallback[C.INTERACTION] = band
        fallback["note"] = (f"grading unavailable ({type(err).__name__}); "
                            "scored at target band")
        fallback["degraded"] = True
        return fallback


def _build_grade_prompt(question_text: str, turn_number: int, is_part3: bool,
                        theme: str = "", speech: Optional[dict] = None) -> str:
    """The grader's system prompt: the rubric and nothing about asking questions.

    `speech` is the client's acoustic measurements for this turn, when the student
    answered by voice through the Azure assessor. Supplying them is what lets
    Fluency be graded on real pausing and tempo instead of a transcript that has
    already thrown both away.
    """
    subject = f' on the theme "{theme}"' if theme else ""
    criteria = list(C.CORE_CRITERIA) + ([C.INTERACTION] if is_part3 else [])
    lines = [
        "You are an Epistemy CEFR speaking grader marking one turn of a short "
        f"oral exam{subject}.",
        "",
        f'PART 2 QUESTION (the long turn): "{question_text}"',
        "",
        f"THIS TURN: turn {turn_number} of {C.TOTAL_TURNS} — "
        + (f"Part 3 follow-up {turn_number - C.PART3_FIRST_TURN + 1} of "
           f"{C.TOTAL_TURNS - C.PART3_FIRST_TURN + 1}."
           if is_part3 else "the Part 2 long turn."),
        "",
        "Score the student's latest answer on the CEFR grid: "
        + ", ".join(criteria) + ".",
        "Each score is 1-6 where 1=A1, 2=A2, 3=B1, 4=B2, 5=C1, 6=C2. Half points "
        "are allowed (e.g. 3.5). The expected working range is 2-5.",
    ]
    if not is_part3:
        lines.append("Do NOT score interaction on this turn — it is a long turn "
                     "with no exchange to judge.")
    evidence = C.speech_evidence_block(speech)
    if evidence:
        # The audio was measured, so the old "you are reading a transcript"
        # caveat would now understate what the grader can see.
        lines += ["Judge only this latest answer.", "", evidence, ""]
    else:
        lines += [
            "Judge only this latest answer, from the transcript as given. You are "
            "reading a speech-to-text transcript, so do not infer pronunciation. "
            "It also strips pauses and hesitations, so treat Fluency with caution.",
            "",
        ]
    lines += [
        "If the student is not answering but asking you to repeat, rephrase or "
        "clarify, set repeat_request to true and still score what little they "
        "produced.",
        "",
        "CEFR GRID (Council of Europe, qualitative aspects of spoken language use):",
        C.CEFR_DESCRIPTORS,
        "",
        "Respond ONLY with minified JSON, no prose and no code fences:",
        "{" + ", ".join(f'"{c}": number' for c in criteria)
        + ', "repeat_request": bool, "note": "one internal sentence on what '
          'drove these scores"}',
    ]
    return "\n".join(lines)


# ── Examining (call 2: the next question only) ───────────────────────────────

def _write_follow_up(settings, question_text: str, prior: List[dict],
                     answer_text: str, theme: str, instruction: str,
                     repeat_request: bool) -> str:
    """Write the next follow-up for the already-chosen band and question type.

    A separate call because the type depends on the grade that call 1 just
    produced. Small prompt, small output, and a fast model — the student is
    waiting on this, and writing one question needs far less of the model than
    applying the rubric does.
    """
    system = _build_examiner_prompt(question_text, theme, instruction,
                                    repeat_request)
    user = _build_transcript(question_text, prior, answer_text)
    try:
        out = call_bedrock(settings, system, user, max_tokens=_FOLLOW_UP_MAX_TOKENS,
                           temperature=0.4, model=_FOLLOW_UP_MODEL,
                           retries=1, timeout=_LLM_TIMEOUT_S)
        text = (out.get("follow_up") or "").strip()
    except Exception as err:
        logger.warning("CEFR follow-up generation failed: %s", err)
        text = ""
    if text:
        return text
    # Last resort, deliberately theme-neutral: the model that knows this exam's
    # topic is the one that just failed.
    logger.warning("CEFR examiner produced no follow-up; using the generic probe")
    return "Can you tell me a little more about that?"


def _build_examiner_prompt(question_text: str, theme: str, instruction: str,
                           repeat_request: bool) -> str:
    """The examiner's system prompt: conduct, the topic frame, and the question to ask."""
    subject = f' on the theme "{theme}"' if theme else ""
    lines = [
        "You are an Epistemy CEFR speaking examiner running a short oral "
        f"exam{subject}. Write the next question for the student.",
        "",
        C.EXAMINER_CONDUCT,
        "",
        f'PART 2 QUESTION (the long turn): "{question_text}"',
        C.part3_frame(theme),
        "",
        instruction,
    ]
    if repeat_request:
        lines += [
            "",
            "The student did not answer — they asked you to repeat or rephrase. "
            "Re-ask the SAME question in simpler words. Do not move on and do "
            "not change the question type.",
        ]
    lines += [
        "",
        "Respond ONLY with minified JSON, no prose and no code fences:",
        '{"follow_up": "the question to ask, one or two sentences, speakable aloud"}',
    ]
    return "\n".join(lines)


def _build_transcript(question_text: str, prior: List[dict],
                      answer_text: str) -> str:
    """The exam so far, as the grader sees it."""
    parts = [f"Examiner (Part 2): {question_text}"]
    for p in prior:
        parts.append(f"Student: {p['answer']}")
        if p["follow_up"]:
            parts.append(f"Examiner: {p['follow_up']}")
    parts.append(f"Student's latest answer: {answer_text}")
    return "\n".join(parts)
