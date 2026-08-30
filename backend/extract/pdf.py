"""PDF extractor: one unit per page (text-based PDFs).

Uses pypdf for pure-Python text extraction — no system libraries. Scanned or
image-only PDFs yield little/no text (no OCR); those pages are skipped.
"""
from __future__ import annotations
import io
from typing import List

from backend.constants import MAX_PDF_PAGES
from backend.models import ChunkPosition
from backend.chunking import ExtractedUnit


class PdfExtractor:
    """pypdf per-page extraction; empty/image-only pages are dropped."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES:
            raise ValueError(
                f"PDF has {page_count} pages, over the {MAX_PDF_PAGES}-page limit. "
                "Split it into smaller readings and upload them separately.")
        units: List[ExtractedUnit] = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                units.append(ExtractedUnit(
                    text=text, position=ChunkPosition(page_no=page_no),
                    structured=False))
        return units
