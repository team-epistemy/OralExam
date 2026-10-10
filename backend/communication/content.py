"""Content for the speaking exam: the Part 2 card bank, the theme frame for
Part 3, and the CEFR grading rubric.

A Part 2 card is a long-turn prompt plus the theme it sits on. The theme is not
decoration: Part 3 follow-ups must stay on it, so a card carries its own theme
and an optional discussion frame for higher-band probes. A professor can pick a
card or write their own question, in which case the question itself is the theme.

The CEFR descriptors are condensed from Council of Europe Table 3, *Qualitative
aspects of spoken language use*, and are pasted into the grading prompt as the
rubric.
"""
from __future__ import annotations

from typing import NamedTuple, Optional


class SpeakingCard(NamedTuple):
    """A Part 2 long-turn prompt and the theme its follow-ups must stay on."""
    card_id: str
    theme: str
    question: str
    # An opener the grader may use to frame a higher-band probe. Empty when the
    # card has no natural discussion angle.
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


def part3_frame(theme: str = "", high_band_frame: str = "") -> str:
    """The Part 3 constraint for the grading prompt, for this exam's theme."""
    subject = f'the theme "{theme}"' if theme else "the topic of the Part 2 question"
    frame = (
        f"Part 3 follow-ups stay on {subject}. Every follow-up must build on "
        "something the student actually said. Never introduce a new topic."
    )
    if high_band_frame:
        frame += (" For higher-band probes you may frame the question around: "
                  f'"{high_band_frame}"')
    return frame

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
- Ask exactly ONE question. Keep it to one or two sentences, speakable aloud."""

# How to pitch the next follow-up for each ladder direction.
PROBE_STYLE = {
    "up": (
        "Pitch the follow-up HIGHER: ask the student to justify, compare, "
        "hypothesise or persuade (\"What might happen if...?\", \"How would you "
        "convince someone who disagrees?\")."
    ),
    "same": (
        "Pitch the follow-up at the SAME level: another question of comparable "
        "demand on the same theme."
    ),
    "down": (
        "Pitch the follow-up LOWER: a simpler, more concrete question about "
        "something they already mentioned, or a rephrase (\"Can you give me "
        "one example of that?\", \"Do you do that often?\")."
    ),
}
