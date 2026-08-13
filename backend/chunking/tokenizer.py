"""Thin tokenizer wrapper so the counter can be swapped later."""
from __future__ import annotations
import re
from typing import Protocol

_WORD = re.compile(r"\w+|[^\w\s]")


class Tokenizer(Protocol):
    """Anything that can count tokens in a string."""

    def count(self, text: str) -> int: ...


class ApproxTokenizer:
    """Whitespace/punctuation token approximation; good enough for sizing."""

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(_WORD.findall(text))
