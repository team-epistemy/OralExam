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


# ── Deterministic acoustic fluency (hybrid, A/B against the LLM grade) ────────
# A transparent, research-anchored acoustic Fluency band computed ALONGSIDE the
# LLM's holistic grade, so the two can be compared and the anchors tuned against
# human re-bands over time. It does NOT feed the grade or the ladder — it is
# evidence for calibration only. Anchors are per-CEFR-band central tendencies
# from Tavakoli, Nakatsuhara & Hunter (2020) and Huang & Gráf (2020): indicative,
# not validated cut-scores, so treat this as a prototype signal, not a verdict.

def _clampf(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _interp_band(value: float, anchors: Sequence[float]) -> float:
    """Map a measure value to a continuous 1-6 band via the four band anchors at
    A2/B1/B2/C1 (bands 2-5). Handles increasing (rates) and decreasing (pause)
    measures, extrapolating past the ends with the nearest segment's slope."""
    bands = (2.0, 3.0, 4.0, 5.0)
    d = 1.0 if anchors[-1] >= anchors[0] else -1.0
    if d * value <= d * anchors[0]:
        slope = (bands[1] - bands[0]) / (anchors[1] - anchors[0])
        return _clampf(bands[0] + (value - anchors[0]) * slope, 1.0, 6.0)
    for i in range(len(anchors) - 1):
        if d * value <= d * anchors[i + 1]:
            frac = (value - anchors[i]) / (anchors[i + 1] - anchors[i])
            return _clampf(bands[i] + frac * (bands[i + 1] - bands[i]), 1.0, 6.0)
    slope = (bands[-1] - bands[-2]) / (anchors[-1] - anchors[-2])
    return _clampf(bands[-1] + (value - anchors[-1]) * slope, 1.0, 6.0)


# (derived key, [A2, B1, B2, C1] anchors, weight). Speech rate, phonation ratio
# and mean length of run are the strongest fluency predictors (Kormos & Dénes);
# pause DURATION is the weakest, so it carries the least weight.
_ACOUSTIC_SPECS = (
    ("speech_rate_wpm", (55.0, 95.0, 120.0, 140.0), 0.30),
    ("phonation_pct", (46.0, 71.0, 77.0, 78.0), 0.30),
    ("articulation_rate_wpm", (113.0, 134.0, 160.0, 168.0), 0.15),
    ("mean_run_words", (2.3, 4.1, 6.1, 6.4), 0.15),
    ("mean_pause_sec", (1.42, 0.63, 0.56, 0.54), 0.10),
)


def _num(v) -> Optional[float]:
    """A finite float, or None (bools are not numbers here)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def acoustic_fluency(speech: Optional[dict]) -> Optional[dict]:
    """A deterministic Fluency band from the Azure acoustic metrics, or None when
    there is no usable audio evidence (text mode / nothing measured).

    Phonation ratio (% speaking) is derived from pause_ratio and mean length of
    run from word_count / (pauses + 1). Mean pause is used only when at least one
    pause was measured (0 would read as 'no pauses', not 'not measured')."""
    if not speech:
        return None
    pr = _num(speech.get("pause_ratio"))
    wc = _num(speech.get("word_count"))
    pc = _num(speech.get("pause_count"))
    derived = {
        "speech_rate_wpm": _num(speech.get("speech_rate_wpm")),
        "articulation_rate_wpm": _num(speech.get("articulation_rate_wpm")),
        "phonation_pct": (100.0 * (1.0 - pr)) if pr is not None else None,
        "mean_run_words": (wc / (pc + 1.0)) if (wc and pc is not None) else None,
        "mean_pause_sec": _num(speech.get("mean_pause_sec")) if (pc and pc > 0) else None,
    }
    num = den = 0.0
    measures = []
    for key, anchors, weight in _ACOUSTIC_SPECS:
        val = derived.get(key)
        if val is None:
            continue
        b = _interp_band(val, anchors)
        measures.append({"measure": key, "value": round(val, 2), "band": round(b, 2)})
        num += b * weight
        den += weight
    if den == 0:
        return None
    score = num / den
    band = clamp_band(round_half_up(score))
    return {"band": band, "band_name": band_name(band),
            "score": round(score, 2), "measures": measures}


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
