"""RTF extractor: strip control words to plain text, then shape as markdown.

striprtf is pure-Python (no system libs). The decoded text has no heading
markup, so it falls through to MarkdownExtractor's paragraph splitting.
"""
from __future__ import annotations
from typing import List

from backend.chunking import ExtractedUnit
from backend.extract.markdown import MarkdownExtractor


class RtfExtractor:
    """striprtf → plain text → markdown paragraph units."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        from striprtf.striprtf import rtf_to_text
        text = rtf_to_text(data.decode("utf-8", errors="replace"))
        return MarkdownExtractor().extract(text.encode("utf-8"))
