"""Unit tests for the stuck-answer + stress-fallback detection helpers.

Imports from the import-light backend.app.followup module (NOT http_app) so the
suite collects without fastapi — matching how CI installs only the lean deps.
"""
from backend.app.followup import count_trailing_repeats as _count_trailing_repeats
from backend.app.followup import looks_stressed as _looks_stressed


def test_repeats_novel_answer_is_zero():
    prior = ["the queue grows because arrival exceeds service rate"]
    assert _count_trailing_repeats(prior, "throughput is the exit rate") == 0


def test_repeats_counts_trailing_near_duplicates():
    # The student says the same thing twice before this third identical attempt.
    prior = ["time value of money", "time value of money"]
    assert _count_trailing_repeats(prior, "time value of money") == 2


def test_repeats_paraphrase_counts_as_repeat():
    prior = ["It is the time value of money."]
    # Near-identical phrasing should clear the 0.82 similarity bar.
    assert _count_trailing_repeats(prior, "it is the time value of money") == 1


def test_repeats_only_trailing_run_is_counted():
    # A distinct answer breaks the run, so earlier duplicates don't count.
    prior = ["time value of money", "throughput and cycle time", "time value of money"]
    assert _count_trailing_repeats(prior, "time value of money") == 1


def test_repeats_empty_answer_is_zero():
    assert _count_trailing_repeats(["anything"], "") == 0
    assert _count_trailing_repeats([], "something") == 0


def test_stress_phrases_detected():
    for t in ["I'm stressed, I don't know this", "i am stressed",
              "I don't know this at all", "can we skip this one?",
              "this is too hard", "I give up"]:
        assert _looks_stressed(t), t


def test_normal_answers_not_flagged_as_stress():
    for t in ["The queue grows when arrival rate exceeds service rate.",
              "Little's Law relates L, lambda and W.",
              "I think throughput stays constant here."]:
        assert not _looks_stressed(t), t
