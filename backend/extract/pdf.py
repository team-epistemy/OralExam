"""PDF extractor: one unit per page (text-based PDFs).

Uses pypdf for pure-Python text extraction — no system libraries. Scanned or
image-only PDFs yield little/no text (no OCR); those pages are skipped.
"""
from __future__ import annotations
import io
from typing import List

from backend.models import ChunkPosition
from backend.chunking import ExtractedUnit


class PdfExtractor:
    """pypdf per-page extraction; empty/image-only pages are dropped."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        units: List[ExtractedUnit] = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                units.append(ExtractedUnit(
                    text=text, position=ChunkPosition(page_no=page_no),
                    structured=False))
        return units
