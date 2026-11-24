"""Pure follow-up detection helpers for the oral-exam answer loop.

Import-light (stdlib only — difflib, re), like performance.py / agent_sim.py, so
the unit tests can import these WITHOUT pulling in fastapi and the rest of
http_app. http_app re-exports them; tests import from here.
"""
from __future__ import annotations

import difflib
import re

# When a student keeps giving the same answer, nudge the examiner to reword the
# question instead of probing deeper (pilot: Matteo repeated "time value of money"
# ~3x and the examiner kept digging). Threaded into the prompt builders' probe_directive.
STUCK_REPHRASE_DIRECTIVE = (
    "IMPORTANT: the student has repeated essentially the same answer and seems stuck. "
    "Do NOT probe deeper or ask for more detail. Instead, REPHRASE the original question "
    "in simpler, different words — a fresh angle to unstick them — as the probe."
)

# Spoken line when the examiner gives up probing a stuck question and advances.
MOVE_ON_LINE = "That's alright — let's move on to the next question."

# Phrases that signal distress / inability to continue, as a deterministic fallback
# so the stress path fires even if the model misses it or on the heuristic eval path.
STRESS_PHRASES = (
    "i'm stressed", "i am stressed", "im stressed", "i'm so stressed",
    "i don't know this", "i dont know this", "i don't know any of this",
    "i can't do this", "i cant do this", "i give up", "i can't think",
    "can we skip", "can i skip", "please skip", "too hard", "this is too hard",
    "i'm panicking", "i am panicking", "i'm freaking out", "i'm overwhelmed",
)


def normalize_answer(text):
    """Lowercase + collapse whitespace for similarity/keyword comparison."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def count_trailing_repeats(prior_answers, answer_text, threshold=0.82):
    """How many of the immediately-preceding answers are ~the same as this one.

    Walks backward through ``prior_answers`` (oldest→newest list of the student's
    earlier answers on THIS question) counting consecutive entries whose
    SequenceMatcher ratio against the current answer is >= threshold. 0 = this
    answer is novel; 1 = one prior near-duplicate (2nd time saying it); etc.
    """
    cur = normalize_answer(answer_text)
    if not cur:
        return 0
    n = 0
    for prev in reversed(prior_answers or []):
        p = normalize_answer(prev)
        if p and difflib.SequenceMatcher(None, cur, p).ratio() >= threshold:
            n += 1
        else:
            break
    return n


def looks_stressed(text):
    """True if the answer plainly expresses distress / inability to continue."""
    t = normalize_answer(text)
    return any(phrase in t for phrase in STRESS_PHRASES)
