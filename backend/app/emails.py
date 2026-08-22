"""Web-free helpers, importable by unit tests without pulling in FastAPI."""
from __future__ import annotations
import re


def parse_emails(raws) -> list:
    """Unique, lowercased emails from a list that may contain comma/semicolon/
    whitespace-joined strings (so a pasted CSV works even if not pre-split)."""
    out = []
    for raw in raws or []:
        for tok in re.findall(r"[^\s,;]+@[^\s,;]+", raw or ""):
            e = tok.strip().lower().rstrip(".")
            if e and e not in out:
                out.append(e)
    return out
