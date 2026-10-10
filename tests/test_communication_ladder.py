"""Unit tests for the Part 3 ladder rule and the CEFR scoring maths.

Both are pure functions, so these run with no DB, no LLM and no app wiring.
"""
import pytest

from backend.communication import content as C
from backend.communication import ladder as L


# ── Band helpers ─────────────────────────────────────────────────────────────

def test_bands_clamp_to_a1_c2():
    assert L.clamp_band(0) == 1
    assert L.clamp_band(-3) == 1
    assert L.clamp_band(7) == 6
    assert L.clamp_band(4) == 4


def test_band_names_map_to_cefr_labels():
    assert [L.band_name(n) for n in range(1, 7)] == list(C.BANDS)
    # Out-of-range values clamp rather than raise.
    assert L.band_name(0) == "A1"
    assert L.band_name(9) == "C2"


def test_rounding_is_half_up_not_bankers():
    # round(2.5) is 2 in Python, which would push a borderline student down.
    assert L.round_half_up(2.5) == 3
    assert L.round_half_up(3.5) == 4
    assert L.round_half_up(3.49) == 3
    assert L.round_half_up(3.0) == 3


# ── Per-turn band ────────────────────────────────────────────────────────────

def test_turn_band_is_the_rounded_mean_of_criterion_scores():
    scores = {"range": 3, "accuracy": 3, "fluency": 4, "coherence": 3,
              "interaction": 3}
    assert L.turn_band(scores) == 3          # mean 3.2 -> 3


def test_turn_band_ignores_unscored_interaction_on_part_2():
    # A Part 2 turn has no interaction score; averaging over a missing criterion
    # as zero would drag the band down a whole level.
    part2 = {"range": 4, "accuracy": 4, "fluency": 4, "coherence": 4,
             "interaction": None}
    assert L.turn_band(part2) == 4


def test_turn_band_handles_half_points():
    scores = {"range": 3.5, "accuracy": 3.5, "fluency": 3.5, "coherence": 3.5,
              "interaction": 3.5}
    assert L.turn_band(scores) == 4          # 3.5 rounds up


# ── The ladder rule ──────────────────────────────────────────────────────────

def test_part_2_sets_the_starting_band_and_probes_one_up():
    # No target yet: the long turn's own band becomes proven, Part 3 opens above.
    step = L.next_step(result_band=3, target_band=None, proven_band=None)
    assert step == {"target_band": 4, "proven_band": 3, "direction": "up"}


def test_clearly_better_than_target_probes_above_what_was_demonstrated():
    # Aimed at B2, came back C1 — the next probe must go above C1, not to B2.
    step = L.next_step(result_band=5, target_band=4, proven_band=3)
    assert step["target_band"] == 6
    assert step["proven_band"] == 5
    assert step["direction"] == "up"


def test_clearly_worse_than_target_eases_down_one_from_the_target():
    step = L.next_step(result_band=2, target_band=5, proven_band=4)
    assert step["target_band"] == 4
    assert step["proven_band"] == 4          # nothing new proven
    assert step["direction"] == "down"


def test_same_band_records_it_as_proven_then_probes_up():
    step = L.next_step(result_band=4, target_band=4, proven_band=3)
    assert step == {"target_band": 5, "proven_band": 4, "direction": "up"}


def test_up_probe_returning_the_proven_band_settles_back():
    # Proven B1, probed B2, came back B1: the ceiling is B1, so settle there.
    step = L.next_step(result_band=3, target_band=4, proven_band=3)
    assert step == {"target_band": 3, "proven_band": 3, "direction": "down"}


def test_bands_never_leave_a1_c2():
    assert L.next_step(result_band=6, target_band=6, proven_band=6)["target_band"] == 6
    assert L.next_step(result_band=1, target_band=2, proven_band=None)["target_band"] == 1


def test_spec_worked_example_same_band_probe_up_settle_back():
    """Part 2 grades B1; FU1 probes B2; B1 again settles FU2 to B1; FU3 probes B2.

    This is the example in the spec, and it is the case the whole rule exists for.
    """
    steps = L.run_ladder([3, 3, 3])          # B1, B1, B1
    assert [s["target_band"] for s in steps] == [4, 3, 4]
    assert [L.band_name(s["target_band"]) for s in steps] == ["B2", "B1", "B2"]
    assert [s["direction"] for s in steps] == ["up", "down", "up"]
    assert all(s["proven_band"] == 3 for s in steps)


def test_ladder_climbs_with_a_strengthening_student():
    steps = L.run_ladder([3, 4, 5, 6])
    assert [s["target_band"] for s in steps] == [4, 5, 6, 6]
    assert [s["direction"] for s in steps] == ["up", "up", "up", "same"]


def test_ladder_runs_all_five_turns_and_never_ends_early():
    # Part 3 always asks its four follow-ups; the rule only picks the direction.
    steps = L.run_ladder([2, 2, 2, 2, 2])
    assert len(steps) == 5


# ── Final result ─────────────────────────────────────────────────────────────

def _turn(range_=3, accuracy=3, fluency=3, coherence=3, interaction=3):
    return {"range": range_, "accuracy": accuracy, "fluency": fluency,
            "coherence": coherence, "interaction": interaction}


def test_criterion_averages_average_each_criterion_over_its_own_turns():
    per_turn = [
        _turn(interaction=None),             # Part 2: no interaction
        _turn(range_=4, interaction=4),
    ]
    avgs = L.criterion_averages(per_turn)
    assert avgs["range"] == 3.5              # (3 + 4) / 2
    assert avgs["accuracy"] == 3.0
    assert avgs["interaction"] == 4.0        # the only turn it was scored on


def test_overall_score_is_the_mean_of_the_five_criterion_averages():
    per_turn = [_turn(interaction=None)] + [_turn()] * 4
    result = L.final_result(per_turn)
    assert result["overall_score"] == 3.0
    assert result["overall_band"] == "B1"
    assert result["overall_band_num"] == 3


def test_overall_band_rounds_the_overall_score():
    per_turn = [_turn(range_=4, accuracy=4, fluency=4, coherence=3,
                      interaction=4)]
    result = L.final_result(per_turn)
    assert result["overall_score"] == pytest.approx(3.8)
    assert result["overall_band"] == "B2"    # 3.8 -> 4


def test_final_result_of_no_turns_is_the_floor_not_a_crash():
    result = L.final_result([])
    assert result["overall_score"] == 0.0
    assert result["overall_band"] == "A1"


def test_released_grade_equals_overall_score_over_six():
    # Grade release divides the summed eds_score by max_questions (1), so the
    # handler writes overall / 6 and the released grade is exactly that.
    per_turn = [_turn(interaction=None)] + [_turn()] * 4
    result = L.final_result(per_turn)
    assert round(result["overall_score"] / C.MAX_BAND, 4) == 0.5


# ── Grader-output normalisation ───────────────────────────────────────────────

def test_normalise_scores_drops_interaction_on_part_2():
    out = L.normalise_scores({"range": 4, "accuracy": 4, "fluency": 4,
                              "coherence": 4, "interaction": 4},
                             is_part3=False)
    assert out["interaction"] is None
    assert out["range"] == 4.0


def test_normalise_scores_keeps_interaction_on_part_3():
    out = L.normalise_scores({"range": 4, "accuracy": 4, "fluency": 4,
                              "coherence": 4, "interaction": 5},
                             is_part3=True)
    assert out["interaction"] == 5.0


def test_normalise_scores_clamps_out_of_range_and_defaults_the_unusable_scores():
    out = L.normalise_scores({"range": 11, "accuracy": -4, "fluency": "4.5",
                              "coherence": None, "interaction": "nonsense"},
                             is_part3=True)
    assert out["range"] == 6.0
    assert out["accuracy"] == 1.0
    assert out["fluency"] == 4.5             # numeric strings are fine
    assert out["coherence"] == 3.0           # missing -> B1
    assert out["interaction"] == 3.0
