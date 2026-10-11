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
    frame = C.part3_frame("Technology in daily life")
    assert '"Technology in daily life"' in frame
    assert "Never introduce a new topic" in frame


def test_a_custom_question_frames_on_the_question_itself():
    # There is no theme to name, so the question is the constraint.
    frame = C.part3_frame("")
    assert "the topic of the Part 2 question" in frame
    assert "Never introduce a new topic" in frame


def test_the_frame_never_offers_the_widening_angle():
    """Offering it every turn is a standing licence to pivot off topic."""
    for card in C.CARDS:
        assert card.high_band_frame not in C.part3_frame(card.theme)


# ── Question types ───────────────────────────────────────────────────────────

def test_every_question_type_has_a_band_label_instruction_and_form():
    for q in C.QUESTION_TYPES:
        assert q.type_id and q.label and q.instruction and q.form
        assert C.MIN_BAND <= q.band <= C.TOP_TYPED_BAND


def test_question_type_ids_are_unique():
    ids = [q.type_id for q in C.QUESTION_TYPES]
    assert len(ids) == len(set(ids))


def test_every_band_has_at_least_two_types_so_a_stuck_student_gets_variety():
    for band in range(C.MIN_BAND, C.MAX_BAND + 1):
        assert len(C.question_types_for_band(band)) >= 2


def test_c2_reuses_the_c1_types():
    assert C.question_types_for_band(6) == C.question_types_for_band(5)


def test_forms_carry_slots_not_finished_questions():
    """The form is a shape to fill from the student's answer, not a script."""
    for q in C.QUESTION_TYPES:
        assert "<" in q.form and ">" in q.form, q.type_id


def test_no_form_presupposes_a_visual_stimulus():
    """PISA's items are picture-based; this exam has no pictures to refer to."""
    banned = ("photograph", "photo", "picture", "image", "this person")
    for q in C.QUESTION_TYPES:
        low = q.form.lower()
        for word in banned:
            assert word not in low, f"{q.type_id} form mentions {word!r}"


def test_no_form_hardcodes_an_unrelated_topic():
    # The PISA originals talk about doing exercise, which is none of our themes.
    for q in C.QUESTION_TYPES:
        assert "exercise" not in q.form.lower(), q.type_id


def test_question_type_lookup_by_id():
    assert C.question_type_by_id("compare").label == "Compare"
    assert C.question_type_by_id("nope") is None
    assert C.question_type_by_id(None) is None


# ── Type rotation ────────────────────────────────────────────────────────────

def test_a_student_stuck_at_one_band_gets_different_question_types():
    used = []
    picked = []
    for _ in range(3):
        q = C.pick_question_type(3, used)
        used.append(q.type_id)
        picked.append(q.type_id)
    assert len(set(picked)) == 3


def test_rotation_wraps_once_every_type_at_the_band_is_used():
    options = [q.type_id for q in C.question_types_for_band(3)]
    nxt = C.pick_question_type(3, options)
    assert nxt.type_id in options


def test_rotation_never_repeats_the_immediately_previous_type():
    options = [q.type_id for q in C.question_types_for_band(4)]
    # All used, last one is options[0]: the next pick must not be options[0].
    used = options[1:] + [options[0]]
    assert C.pick_question_type(4, used).type_id != options[0]


def test_picking_is_deterministic_for_the_same_history():
    assert (C.pick_question_type(4, ["compare"]).type_id
            == C.pick_question_type(4, ["compare"]).type_id)


# ── The follow-up instruction block ──────────────────────────────────────────

def test_the_instruction_names_the_band_the_type_and_the_form():
    text, type_id = C.follow_up_instruction(4, "up")
    qtype = C.question_type_by_id(type_id)
    assert "Target level: B2" in text
    assert qtype.label in text
    assert qtype.form in text
    assert "shape, not a script" in text


def test_the_instruction_explains_which_way_the_ladder_moved():
    assert "HIGHER" in C.follow_up_instruction(4, "up")[0]
    assert "LOWER" in C.follow_up_instruction(4, "down")[0]
    assert "SAME" in C.follow_up_instruction(4, "same")[0]


def test_the_widening_angle_goes_only_to_the_widening_type():
    frame = "Has technology made people more connected?"
    # abstract_judgement is a C1 type; reach it by exhausting the others.
    text, type_id = C.follow_up_instruction(
        5, "up", ["speculate"], frame)
    assert type_id == C.WIDENING_TYPE_ID
    assert frame in text
    # A B2 comparison has no business widening to society in general.
    other, other_id = C.follow_up_instruction(4, "up", (), frame)
    assert other_id != C.WIDENING_TYPE_ID
    assert frame not in other


def test_the_instruction_clamps_a_band_outside_a1_c2():
    assert "Target level: C2" in C.follow_up_instruction(99, "up")[0]
    assert "Target level: A1" in C.follow_up_instruction(-5, "down")[0]


# ── Propagation into the prompts ─────────────────────────────────────────────

def test_the_exams_theme_and_question_reach_the_examiner_prompt():
    from backend.communication import turn as T
    card = C.resolve_card("travel", None)
    instruction, _ = C.follow_up_instruction(4, "up", (), card.high_band_frame)
    prompt = T._build_examiner_prompt(card.question, card.theme, instruction,
                                      False)
    assert card.question in prompt
    assert card.theme in prompt
    assert "Target level: B2" in prompt


def test_a_custom_questions_prompt_names_no_theme():
    from backend.communication import turn as T
    card = C.custom_card("Describe a decision you regret.")
    instruction, _ = C.follow_up_instruction(3, "same")
    prompt = T._build_examiner_prompt(card.question, card.theme, instruction,
                                      False)
    assert "Describe a decision you regret." in prompt
    assert "the topic of the Part 2 question" in prompt
    # No bank card's theme should leak in.
    assert "Where you live" not in prompt


def test_handle_turn_reads_the_theme_off_the_assignment_config(monkeypatch):
    """The theme is per-exam, so it must come from config, not a constant."""
    from backend.communication import turn as T
    from tests.test_communication_turn import _Repo, _Req, _SROW, _stub_llm, _is_grader

    seen = []

    def fake_call(settings, system, user, **kw):
        seen.append(system)
        if _is_grader(system):
            return {c: 3 for c in C.CORE_CRITERIA}
        return {"follow_up": "and then?"}

    monkeypatch.setattr(T, "call_bedrock", fake_call)
    repo = _Repo()
    T.handle_turn(repo, object(), "ssss", _SROW, _Req("my answer"),
                  {"cefr_theme": "Work and study",
                   "cefr_high_band_frame": "Are exams useful?"})
    assert any("Work and study" in s for s in seen)


def test_handle_turn_without_a_config_still_grades(monkeypatch):
    # Exams created before the card bank have no cefr_theme on their config.
    from backend.communication import turn as T
    from tests.test_communication_turn import _Repo, _Req, _SROW, _stub_llm

    _stub_llm(monkeypatch, [3])
    repo = _Repo()
    resp = T.handle_turn(repo, object(), "ssss", _SROW, _Req("my answer"))
    assert resp["probe"]
