"""The Part 2 card bank and how a professor's choice reaches the examiner.

The theme is load-bearing: Part 3 follow-ups must stay on it, so a card's theme
has to survive from the create call through to the grading prompt. These cover
the resolution rules and that propagation.
"""
import pytest

from backend.communication import content as C


# ── The bank ─────────────────────────────────────────────────────────────────

def test_every_card_has_an_id_theme_and_question():
    assert len(C.CARDS) >= 2
    for card in C.CARDS:
        assert card.card_id and card.theme and card.question


def test_card_ids_are_unique():
    ids = [c.card_id for c in C.CARDS]
    assert len(ids) == len(set(ids))


def test_no_bank_card_collides_with_the_custom_sentinel():
    assert C.CUSTOM_CARD_ID not in {c.card_id for c in C.CARDS}


def test_the_default_card_is_in_the_bank():
    assert C.card_by_id(C.DEFAULT_CARD_ID) is not None


def test_card_lookup_misses_return_none():
    assert C.card_by_id("not-a-card") is None
    assert C.card_by_id(None) is None


# ── Resolution ───────────────────────────────────────────────────────────────

def test_no_choice_falls_back_to_the_default_card():
    card = C.resolve_card(None, None)
    assert card.card_id == C.DEFAULT_CARD_ID


def test_a_card_id_selects_that_card():
    card = C.resolve_card("technology", None)
    assert card.card_id == "technology"
    assert card.theme == "Technology in daily life"


def test_a_custom_question_wins_over_a_card_id():
    card = C.resolve_card("technology", "Describe your morning routine.")
    assert card.card_id == C.CUSTOM_CARD_ID
    assert card.question == "Describe your morning routine."


def test_a_custom_question_is_trimmed():
    card = C.resolve_card(None, "  Describe your street.  ")
    assert card.question == "Describe your street."


def test_a_blank_custom_question_is_ignored_not_accepted():
    # Falls through to the default rather than creating an exam with no question.
    card = C.resolve_card(None, "   ")
    assert card.card_id == C.DEFAULT_CARD_ID
    assert card.question


def test_an_unknown_card_id_raises_rather_than_silently_defaulting():
    with pytest.raises(ValueError, match="unknown speaking card"):
        C.resolve_card("made-up-card", None)


def test_the_custom_sentinel_as_a_card_id_is_not_a_bank_lookup():
    # The UI sends card_id="custom" alongside question_text; on its own it must
    # not blow up as an unknown card.
    card = C.resolve_card(C.CUSTOM_CARD_ID, None)
    assert card.card_id == C.DEFAULT_CARD_ID


# ── The Part 3 frame ─────────────────────────────────────────────────────────

def test_a_themed_frame_names_the_theme_and_forbids_new_topics():
    frame = C.part3_frame("Technology in daily life", "")
    assert '"Technology in daily life"' in frame
    assert "Never introduce a new topic" in frame


def test_a_custom_question_frames_on_the_question_itself():
    # There is no theme to name, so the question is the constraint.
    frame = C.part3_frame("", "")
    assert "the topic of the Part 2 question" in frame
    assert "Never introduce a new topic" in frame


def test_the_high_band_frame_is_offered_only_when_the_card_has_one():
    with_frame = C.part3_frame("Free time", "Do people have less free time?")
    assert "Do people have less free time?" in with_frame
    assert "higher-band" in with_frame
    assert "higher-band" not in C.part3_frame("Free time", "")


# ── Propagation into the grading prompt ──────────────────────────────────────

def test_the_exams_theme_and_question_reach_the_prompt():
    from backend.communication import turn as T
    card = C.resolve_card("travel", None)
    prompt = T._build_prompt(card.question, 2, 4, True, False,
                             card.theme, card.high_band_frame)
    assert card.question in prompt
    assert card.theme in prompt
    assert card.high_band_frame in prompt


def test_a_custom_questions_prompt_names_no_theme():
    from backend.communication import turn as T
    card = C.custom_card("Describe a decision you regret.")
    prompt = T._build_prompt(card.question, 2, 4, True, False,
                             card.theme, card.high_band_frame)
    assert "Describe a decision you regret." in prompt
    assert "the topic of the Part 2 question" in prompt
    # No bank card's theme should leak in.
    assert "Where you live" not in prompt


def test_handle_turn_reads_the_theme_off_the_assignment_config(monkeypatch):
    """The theme is per-exam, so it must come from config, not a constant."""
    from backend.communication import turn as T
    from tests.test_communication_turn import _Repo, _Req, _SROW

    seen = {}

    def fake_call(settings, system, user, **kw):
        seen["system"] = system
        return {c: 3 for c in C.CORE_CRITERIA} | {"follow_up": "and then?"}

    monkeypatch.setattr(T, "call_bedrock", fake_call)
    repo = _Repo()
    T.handle_turn(repo, object(), "ssss", _SROW, _Req("my answer"),
                  {"cefr_theme": "Work and study",
                   "cefr_high_band_frame": "Are exams useful?"})
    assert "Work and study" in seen["system"]
    assert "Are exams useful?" in seen["system"]


def test_handle_turn_without_a_config_still_grades(monkeypatch):
    # Exams created before the card bank have no cefr_theme on their config.
    from backend.communication import turn as T
    from tests.test_communication_turn import _Repo, _Req, _SROW

    monkeypatch.setattr(
        T, "call_bedrock",
        lambda *a, **k: {c: 3 for c in C.CORE_CRITERIA} | {"follow_up": "go on?"})
    repo = _Repo()
    resp = T.handle_turn(repo, object(), "ssss", _SROW, _Req("my answer"))
    assert resp["probe"] == "go on?"
