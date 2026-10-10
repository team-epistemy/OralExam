"""Communication (CEFR speaking) exam type — v0.1 prototype.

A second exam kind that runs alongside the Socratic/EDS exam. An assignment opts
in via config `exam_kind == "communication"`; `submit_answer` then hands the turn
to `handle_turn` here instead of the Socratic evaluator. Everything else — the
student exam page, speech capture, TTS, the timer, session handling and grade
release — is reused unchanged.

See docs/superpowers/specs/communication/2026-10-04-speaking-prototype-v0.1-design.md
"""
from backend.communication.turn import handle_turn

__all__ = ["handle_turn"]
