"""Pure functions for the Part 3 ladder rule and CEFR score aggregation.

No I/O and no LLM calls — everything here is a function of the turn scores
already recorded, so the adaptive rule and the grading maths are unit-testable
on their own (tests/test_communication_ladder.py).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from backend.communication.content import (
    ALL_CRITERIA, BANDS, CORE_CRITERIA, INTERACTION, MAX_BAND, MIN_BAND,
)


# ── Band helpers ─────────────────────────────────────────────────────────────

def clamp_band(band: int) -> int:
    """Clamp a numeric band into A1-C2 (1-6)."""
    return max(MIN_BAND, min(MAX_BAND, int(band)))


def band_name(band: int) -> str:
    """Map a numeric band to its CEFR label."""
    return BANDS[clamp_band(band) - 1]


def round_half_up(value: float) -> int:
    """Round to the nearest whole number, halves going up.

    Python's built-in round() is banker's rounding, so round(2.5) == 2 — which
    would quietly push a student sitting exactly between two bands down. Grading
    needs the conventional half-up rule.
    """
    import math
    return int(math.floor(float(value) + 0.5))


def score_to_band(score: float) -> int:
    """Map a 1-6 criterion score (halves allowed) to a whole band."""
    return clamp_band(round_half_up(score))


# ── Per-turn band ────────────────────────────────────────────────────────────

def turn_band(scores: Dict[str, float]) -> int:
    """The band a single turn demonstrated: mean of its criterion scores, rounded.

    Only the criteria actually present are averaged, so a Part 2 turn (no
    Interaction score) is not penalised for the missing criterion.
    """
    present = [float(scores[c]) for c in ALL_CRITERIA if scores.get(c) is not None]
    if not present:
        return MIN_BAND
    return score_to_band(sum(present) / len(present))


# ── The ladder rule ──────────────────────────────────────────────────────────

def next_step(result_band: int, target_band: Optional[int],
              proven_band: Optional[int]) -> dict:
    """Decide the next follow-up's target band from the turn just graded.

    `target_band` is the band the question just answered was aimed at (None for
    the Part 2 long turn, which sets its own starting band). `proven_band` is the
    highest band the student has confirmed by hitting a target squarely.

    Returns {"target_band", "proven_band", "direction"} where direction is
    up / same / down *relative to the target just used* — that is what gets
    stored and what tells the prompt how to pitch the next question.

    "One band up" is measured from the band the student just demonstrated, not
    from the target: when a turn comes back above its target, target+1 is at or
    below what they already showed, so it would not be a probe up at all. "One
    band down" is measured from the target, which is what makes the worked
    example in the spec settle back onto the proven band.
    """
    result_band = clamp_band(result_band)
    # The Part 2 long turn has nothing to be compared against, so it is treated
    # as having hit its own level: it becomes the proven band and Part 3 opens by
    # probing one above it.
    effective_target = clamp_band(target_band) if target_band is not None else result_band

    if (proven_band is not None
            and effective_target > proven_band
            and result_band == proven_band):
        # An up-probe came back at the band already proven: the ceiling is here.
        # Settle back onto it rather than keep pushing.
        next_target = clamp_band(proven_band)
        new_proven = clamp_band(proven_band)
    elif result_band >= effective_target:
        # Met or beat the target: that band is now proven, so probe one above it.
        new_proven = result_band
        next_target = clamp_band(result_band + 1)
    else:
        # Fell short: ease the next question down one step from where this one
        # was aimed. Nothing new is proven.
        new_proven = clamp_band(proven_band) if proven_band is not None else None
        next_target = clamp_band(effective_target - 1)

    if next_target > effective_target:
        direction = "up"
    elif next_target < effective_target:
        direction = "down"
    else:
        direction = "same"

    return {"target_band": next_target, "proven_band": new_proven,
            "direction": direction}


def run_ladder(turn_bands: Sequence[int]) -> List[dict]:
    """Replay the ladder over a sequence of per-turn bands.

    Returns one step dict per turn, in order. Used by the tests and by the turn
    handler to recover ladder state from the evaluation rows already written.
    """
    steps: List[dict] = []
    target: Optional[int] = None
    proven: Optional[int] = None
    for band in turn_bands:
        step = next_step(band, target, proven)
        steps.append(step)
        target = step["target_band"]
        proven = step["proven_band"]
    return steps


# ── Final result ─────────────────────────────────────────────────────────────

def criterion_averages(per_turn: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """Average each criterion across the turns on which it was scored.

    Interaction is only scored on the Part 3 turns, so it averages over fewer
    turns than the other four. That is intended — the five criteria then carry
    equal weight in the overall score.
    """
    out: Dict[str, float] = {}
    for criterion in ALL_CRITERIA:
        vals = [float(t[criterion]) for t in per_turn
                if t.get(criterion) is not None]
        if vals:
            out[criterion] = round(sum(vals) / len(vals), 3)
    return out


def final_result(per_turn: Sequence[Dict[str, float]]) -> dict:
    """Aggregate the per-turn criterion scores into the student's final result.

    Overall score = mean of the five criterion averages; overall band = that
    score rounded and mapped to A1-C2.
    """
    averages = criterion_averages(per_turn)
    if not averages:
        return {"criterion_averages": {}, "overall_score": 0.0,
                "overall_band": band_name(MIN_BAND), "overall_band_num": MIN_BAND}
    overall = sum(averages.values()) / len(averages)
    band = score_to_band(overall)
    return {
        "criterion_averages": averages,
        "overall_score": round(overall, 3),
        "overall_band": band_name(band),
        "overall_band_num": band,
    }


def normalise_scores(raw: dict, is_part3: bool) -> Dict[str, Optional[float]]:
    """Coerce whatever the grader returned into a clean criterion-score dict.

    Anything missing or unparseable falls back to the middle of the working
    range (B1) rather than failing the turn: a degraded score still lets the
    exam finish, which matters more in a prototype than a perfect one.
    Interaction is dropped entirely on the Part 2 turn.
    """
    out: Dict[str, Optional[float]] = {}
    for criterion in CORE_CRITERIA:
        out[criterion] = _clean_score(raw.get(criterion))
    out[INTERACTION] = _clean_score(raw.get(INTERACTION)) if is_part3 else None
    return out


def _clean_score(value) -> float:
    """Clamp one criterion score into 1-6, defaulting to B1 when unusable."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 3.0
    if num != num:  # NaN
        return 3.0
    return max(float(MIN_BAND), min(float(MAX_BAND), num))
