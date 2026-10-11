"""Content for the speaking exam: the Part 2 card bank, the theme frame for
Part 3, the follow-up question types, and the CEFR grading rubric.

A Part 2 card is a long-turn prompt plus the theme it sits on. The theme is not
decoration: Part 3 follow-ups must stay on it, so a card carries its own theme
and an optional discussion frame for the one question type that widens out to
people in general. A professor can pick a card or write their own question, in
which case the question itself is the theme.

Each Part 3 follow-up asks a fixed TYPE of question for its target band, so two
students at the same band face the same kind of task rather than whatever the
model felt like asking. The CEFR descriptors are condensed from Council of
Europe Table 3, *Qualitative aspects of spoken language use*, and are pasted
into the grading prompt as the rubric.
"""
from __future__ import annotations

from typing import NamedTuple, Optional


class SpeakingCard(NamedTuple):
    """A Part 2 long-turn prompt and the theme its follow-ups must stay on."""
    card_id: str
    theme: str
    question: str
    # A general-interest angle on the theme, offered only to the "abstract
    # judgement" question type — the one type whose job is to widen out from the
    # student's own experience. Empty when the card has no natural angle.
    high_band_frame: str = ""


# ── Part 2 card bank ─────────────────────────────────────────────────────────
# Long-turn prompts in the CEFR Part 2 shape: one thing to describe plus three
# things to cover, so every student has enough to talk about for a minute or two.

CARDS = (
    SpeakingCard(
        "where-you-live", "Where you live",
        "Describe a place in your town you like to visit. Say where it is, "
        "what you do there, and why you like it.",
        "Should towns spend more on spaces for young people?",
    ),
    SpeakingCard(
        "a-person", "People who influence us",
        "Describe someone who taught you something important. Say who they are, "
        "what they taught you, and why it mattered to you.",
        "Do people learn more from their family or from their teachers?",
    ),
    SpeakingCard(
        "work-study", "Work and study",
        "Describe a subject or skill you enjoyed learning. Say what it was, "
        "how you learned it, and why you enjoyed it.",
        "Should schools spend more time on practical skills than on exams?",
    ),
    SpeakingCard(
        "technology", "Technology in daily life",
        "Describe a piece of technology you use every day. Say what it is, "
        "how you use it, and how your life would be different without it.",
        "Has technology made people more connected or less connected?",
    ),
    SpeakingCard(
        "travel", "Travel and places",
        "Describe a journey you remember well. Say where you went, who you were "
        "with, and why you still remember it.",
        "Should people travel less in order to protect the environment?",
    ),
    SpeakingCard(
        "free-time", "Free time",
        "Describe something you like to do in your free time. Say what it is, "
        "how often you do it, and why you enjoy it.",
        "Do people have less free time than they used to?",
    ),
)

DEFAULT_CARD_ID = "where-you-live"
CUSTOM_CARD_ID = "custom"

# Kept as the default so an exam created with no card named still works.
PART2_QUESTION = CARDS[0].question
THEME = CARDS[0].theme


def card_by_id(card_id: Optional[str]) -> Optional[SpeakingCard]:
    """Look up a bank card. None for an unknown id (callers decide the error)."""
    if not card_id:
        return None
    for card in CARDS:
        if card.card_id == card_id:
            return card
    return None


def custom_card(question: str) -> SpeakingCard:
    """Wrap a professor's own question as a card.

    There is no separate theme to name, so the theme is the question itself —
    `part3_frame` then tells the examiner to stay on whatever that question is
    about, which is the same constraint a bank card's theme imposes.
    """
    return SpeakingCard(CUSTOM_CARD_ID, "", question.strip(), "")


def resolve_card(card_id: Optional[str], question_text: Optional[str]) -> SpeakingCard:
    """Pick the card for a new exam: a custom question wins, then an id, then the default.

    Raises ValueError for a card_id that is not in the bank, so a typo surfaces
    instead of silently handing the student the default question.
    """
    if question_text and question_text.strip():
        return custom_card(question_text)
    if card_id and card_id != CUSTOM_CARD_ID:
        card = card_by_id(card_id)
        if card is None:
            raise ValueError(f"unknown speaking card: {card_id}")
        return card
    return card_by_id(DEFAULT_CARD_ID) or CARDS[0]


def part3_frame(theme: str = "") -> str:
    """The Part 3 topic constraint for the grading prompt.

    Deliberately says nothing about the card's high_band_frame: that is a widening
    of the topic, and handing it over on every turn is a standing licence to pivot.
    `follow_up_instruction` offers it only to the one type that wants it.
    """
    subject = f'the theme "{theme}"' if theme else "the topic of the Part 2 question"
    return (
        f"Part 3 follow-ups stay on {subject}. Every follow-up must build on "
        "something the student actually said. Never introduce a new topic."
    )


# ── Turn structure ───────────────────────────────────────────────────────────
# The student exam page already allots 5 turns per question (MAX_TURNS in
# TakeExam.tsx), which maps exactly onto turn 1 = Part 2 long turn and turns
# 2-5 = the four Part 3 follow-ups. Keep these in step with the client.

TOTAL_TURNS = 5
PART3_FIRST_TURN = 2

# ── CEFR bands ───────────────────────────────────────────────────────────────
# Numeric scale used throughout: 1 = A1 ... 6 = C2. Half-points are allowed in
# criterion scores; the expected working range is 2-5.

BANDS = ("A1", "A2", "B1", "B2", "C1", "C2")
MIN_BAND = 1
MAX_BAND = 6

# Criteria scored on every turn, plus Interaction which only applies to the
# Part 3 turns (there is nothing to interact with during the Part 2 long turn).
CORE_CRITERIA = ("range", "accuracy", "fluency", "coherence")
INTERACTION = "interaction"
ALL_CRITERIA = CORE_CRITERIA + (INTERACTION,)

# Condensed CEFR Table 3 descriptors, one line per criterion per band. These are
# the rubric the grader scores against, so they are deliberately terse and
# comparable across bands rather than quoted verbatim.
CEFR_DESCRIPTORS = """\
RANGE
1 (A1): Very basic phrases and memorised expressions about personal details.
2 (A2): Basic sentence patterns, memorised phrases and short formulaic chunks.
3 (B1): Enough language to describe, explain and give opinions on familiar topics,
        with hesitation and some circumlocution.
4 (B2): Clear descriptions and viewpoints on most general topics; varied vocabulary
        and some complex sentence forms, without obvious searching for words.
5 (C1): Broad range allowing fluent, precise expression of fine shades of meaning;
        formulations are reworded easily.
6 (C2): Fully flexible range; consistently precise and idiomatic.

ACCURACY
1 (A1): Only limited control of a few simple memorised structures.
2 (A2): Simple structures used correctly but with systematic basic errors.
3 (B1): Reasonably accurate on familiar, predictable language; errors occur but
        meaning stays clear.
4 (B2): Good grammatical control; occasional slips, non-systematic errors, and
        self-correction.
5 (C1): Consistently high accuracy; errors are rare and hard to spot.
6 (C2): Consistent control of complex language, even while attending to other things.

FLUENCY
1 (A1): Very short, isolated utterances with much pausing.
2 (A2): Short exchanges, very noticeable pausing, false starts and reformulation.
3 (B1): Keeps going comprehensibly; pausing for grammar and repair is evident,
        especially in longer stretches.
4 (B2): Fairly even tempo at length; some hesitation while searching for patterns,
        but few noticeably long pauses.
5 (C1): Fluent and spontaneous, almost effortless; only a conceptually difficult
        subject slows the flow.
6 (C2): Natural, effortless flow at any length; pauses are rhetorical, not search.

COHERENCE
1 (A1): Links words with very basic connectors like "and" or "then".
2 (A2): Links groups of words with simple connectors ("and", "but", "because").
3 (B1): Links a series of shorter elements into a connected linear sequence.
4 (B2): Clear, connected discourse with a limited number of cohesive devices;
        some jumpiness in a long contribution.
5 (C1): Clear, smoothly flowing, well-structured speech with controlled use of
        organisational patterns and connectors.
6 (C2): Coherent, cohesive discourse with full, appropriate use of organisational
        patterns.

INTERACTION (Part 3 turns only)
1 (A1): Asks and answers simple personal questions; relies heavily on repetition.
2 (A2): Answers questions and responds to simple statements; can indicate
        comprehension but rarely sustains the exchange.
3 (B1): Initiates, maintains and closes simple face-to-face exchanges on familiar
        topics; repeats back to confirm understanding.
4 (B2): Initiates discourse, takes turns appropriately, and helps the discussion
        along on familiar ground.
5 (C1): Selects a suitable phrase to take or hold the floor; relates own
        contribution skilfully to what others have said.
6 (C2): Interacts with ease and skill, picking up and using non-verbal and
        intonational cues effortlessly."""

# ── Examiner conduct (PRD) ───────────────────────────────────────────────────
# The grader doubles as the examiner, so the conduct rules live in the same
# prompt as the rubric.

EXAMINER_CONDUCT = """\
EXAMINER CONDUCT — these are absolute:
- Never correct a mistake, never praise, never model better language. Neutral
  acknowledgments only ("I see.", "Right.", "Thank you.").
- Every follow-up must reference something the student actually said — quote or
  paraphrase it. A follow-up that would make sense to any student is wrong.
- Stay inside the theme. Never introduce a new topic.
- Ask exactly ONE question. Keep it to one or two sentences, speakable aloud.
- Ask the QUESTION TYPE you are given. Do not swap it for a harder or easier
  kind of question, even if the student seems stronger or weaker."""

# Why the ladder moved, given to the examiner as context for the next follow-up.
# The ladder decides the target band; the question type decides the shape of the
# question. These strings explain the move, they do not choose the question.
PROBE_STYLE = {
    "up": "The student met the last level, so this follow-up is pitched one level HIGHER.",
    "same": "This follow-up stays at the SAME level to confirm it.",
    "down": "The student struggled at the last level, so this follow-up is pitched one level LOWER.",
}

# ── Follow-up question types ─────────────────────────────────────────────────
# Each Part 3 follow-up uses a fixed TYPE of question for its target band, so
# two students at the same band get the same kind of task. The types are the
# question shapes in the PISA 2025 released speaking items (FLA25SP103, SP203,
# SP305, SP403).
#
# `form` is a SHAPE, not a question: a skeleton with <slots> the examiner fills
# from the student's own last answer. The PISA items are picture-based, so their
# literal wording ("Compare the two photographs") presupposes a stimulus this
# exam does not have — handing that to the model invites it to invent
# photographs. The shape is what transfers; the wording is not.
#
# PISA uses these types in a fixed order and does not label them by level. The
# band assigned to each type is our reading of the CEFR descriptors (A1: single
# concrete facts; A2: routines and past events; B1: reasons and advice; B2:
# comparing and weighing options; C1: speculation and abstract argument) and
# should be validated.


class QuestionType(NamedTuple):
    """One kind of follow-up question and the band it targets."""
    type_id: str
    band: int          # 1 = A1 ... 5 = C1 (C2 reuses the C1 types)
    label: str
    instruction: str   # what the examiner must ask for
    form: str          # skeleton with <slots> filled from the student's answer


QUESTION_TYPES = (
    # ── A1: one concrete fact at a time ──
    QuestionType(
        "fact", 1, "Simple fact",
        "Ask for one simple, concrete fact about something the student mentioned.",
        "Where/when is <thing they mentioned>?",
    ),
    QuestionType(
        "name_things", 1, "Name things",
        "Ask the student to name one or two more things of the same kind as "
        "something they mentioned.",
        "What other <kind of thing they mentioned> do you know?",
    ),
    QuestionType(
        "who_with", 1, "Who with",
        "Ask who the student does something with, or who else is involved in "
        "something they mentioned.",
        "Who do you <do the thing they mentioned> with?",
    ),
    # ── A2: routines and past events ──
    QuestionType(
        "habit", 2, "Habit or routine",
        "Ask what the student usually does, or how often, connected to something "
        "they mentioned.",
        "How often do you <do the thing they mentioned>?",
    ),
    QuestionType(
        "past_event", 2, "Past event",
        "Ask the student to tell you about one specific time in the past connected "
        "to something they mentioned.",
        "Tell me about one time you <did the thing they mentioned>.",
    ),
    QuestionType(
        "simple_preference", 2, "Simple preference",
        "Ask which of two simple options the student prefers. Do not ask why.",
        "Do you prefer <one thing they mentioned> or <another thing they mentioned>?",
    ),
    # ── B1: reasons and advice ──
    QuestionType(
        "preference_why", 3, "Preference with a reason",
        "Ask which of two options the student prefers AND why. End the question "
        "with \"Why?\".",
        "Do you prefer <one thing they mentioned> or <another thing they mentioned>? Why?",
    ),
    QuestionType(
        "advice", 3, "Advice",
        "Ask what advice the student would give a friend in a situation connected "
        "to what they said.",
        "What advice would you give a friend who <situation from what they said>?",
    ),
    QuestionType(
        "simple_hypothetical", 3, "Simple hypothetical",
        "Ask what the student would do in a simple imagined change to something "
        "they mentioned.",
        "What would you do if <simple change to something they mentioned>?",
    ),
    # ── B2: comparing and weighing options ──
    QuestionType(
        "compare", 4, "Compare",
        "Ask the student to compare two things. Use two things they mentioned; if "
        "they mentioned only one, pair it with one other thing from the theme.",
        "How does <one thing they mentioned> compare with <another thing they mentioned>?",
    ),
    QuestionType(
        "pros_cons", 4, "Advantages and disadvantages",
        "Ask for the advantages and disadvantages of something the student mentioned.",
        "What are the advantages and disadvantages of <thing they mentioned>?",
    ),
    QuestionType(
        "choose_justify", 4, "Choose and justify",
        "Ask the student to choose the best option among things discussed and "
        "justify the choice. End the question with \"Why?\".",
        "Which is better, <one thing they mentioned> or <another thing they mentioned>? Why?",
    ),
    # ── C1: speculation and abstract argument ──
    QuestionType(
        "speculate", 5, "Speculate",
        "Ask the student to speculate about causes or consequences they cannot "
        "know for sure.",
        "Why do you think <something they described> happens?",
    ),
    QuestionType(
        "abstract_judgement", 5, "Abstract judgement",
        "Move from the student's own experience to people in general, and ask how "
        "important it is and why.",
        "How important is <topic they raised> for people in general? Why?",
    ),
    QuestionType(
        "persuade", 5, "Persuade",
        "Ask how the student would convince someone who disagrees with a view they "
        "expressed.",
        "How would you convince someone who thinks <the opposite of what they said>?",
    ),
)

# The highest band that has its own question types. C2 reuses these.
TOP_TYPED_BAND = 5

# The one type whose job is to widen out from the student's own experience, so
# the only one offered the card's general-interest angle.
WIDENING_TYPE_ID = "abstract_judgement"


def _clamp_band(band: int) -> int:
    """Clamp a band into A1-C2. Local copy: ladder imports content, not the reverse."""
    return max(MIN_BAND, min(int(band), MAX_BAND))


def question_type_by_id(type_id: Optional[str]) -> Optional[QuestionType]:
    """Look up a question type by id, for rendering a past turn."""
    if not type_id:
        return None
    for qtype in QUESTION_TYPES:
        if qtype.type_id == type_id:
            return qtype
    return None


def question_types_for_band(band: int) -> tuple:
    """The question types for a target band, in bank order."""
    typed = min(_clamp_band(band), TOP_TYPED_BAND)
    return tuple(q for q in QUESTION_TYPES if q.band == typed)


def pick_question_type(band: int, used_type_ids=()) -> QuestionType:
    """Choose the question type for the next follow-up.

    Deterministic, so the same exam history always gives the same type. Prefers
    a type this student has not had yet in this exam, so a student who stays at
    one band is tested on different kinds of question. Never repeats the
    previous turn's type when the band offers an alternative.
    """
    options = question_types_for_band(band)
    used = tuple(used_type_ids or ())
    last = used[-1] if used else None
    unused = [q for q in options if q.type_id not in used]
    not_last = [q for q in options if q.type_id != last]
    return (unused or not_last or list(options))[0]


def follow_up_instruction(target_band: int, direction: str, used_type_ids=(),
                          high_band_frame: str = "") -> tuple:
    """The prompt block for the next follow-up, and the chosen type's id.

    Returns (instruction_text, type_id). Store type_id with the turn and pass the
    exam's type_ids so far as `used_type_ids` next time, or the same type repeats
    for a student who never moves band.

    The level named here is the band the chosen type actually belongs to, not the
    ladder's raw target. They differ only at C2, which has no task shapes of its
    own: claiming "target C2" while handing over a C1 type contradicts the conduct
    rule forbidding the examiner from swapping the type for a harder one, and an
    instruction the model has to reconcile is one it can reconcile inconsistently.
    """
    band = _clamp_band(target_band)
    qtype = pick_question_type(band, used_type_ids)
    typed_band = min(band, TOP_TYPED_BAND)
    # Suppress the ladder's move when the target was clamped: the task did not
    # actually get harder, so saying it was "pitched one level HIGHER" is false.
    move = "" if band > typed_band else PROBE_STYLE.get(direction, "")
    lines = [
        "NEXT FOLLOW-UP",
        f"Target level: {BANDS[typed_band - 1]}." + (f" {move}" if move else ""),
    ]
    if band > typed_band:
        # The ladder wants to push past the top of the bank. There is no harder
        # task to give: what separates C2 from C1 in the CEFR descriptors is the
        # manner of production (precise, idiomatic, effortless), which the grader
        # reads off the answer rather than something a question can demand.
        lines.append(
            f"The student may be beyond {BANDS[typed_band - 1]}. There is no "
            f"harder task than this one — {BANDS[band - 1]} is awarded on how "
            "well they answer, not by asking something different. Ask this type "
            "and let their answer show its own level.")
    lines += [
        f"Question type: {qtype.label}. {qtype.instruction}",
        f'Question form — fill every <slot> from the student\'s last answer and '
        f'never say the slot names aloud: "{qtype.form}"',
        "The form is a shape, not a script: the words must be your own and must "
        "fit what this student actually said.",
    ]
    if high_band_frame and qtype.type_id == WIDENING_TYPE_ID:
        lines.append(f'You may widen the topic using: "{high_band_frame}"')
    return "\n".join(lines), qtype.type_id
