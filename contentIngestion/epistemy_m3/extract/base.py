"""Extractor protocol and a source_type dispatcher."""
from __future__ import annotations
from typing import List, Protocol

from epistemy_m3.models import SourceType
from epistemy_m3.chunking import ExtractedUnit


class Extractor(Protocol):
    """Maps raw file bytes to structural units the chunker understands."""

    def extract(self, data: bytes) -> List[ExtractedUnit]: ...


def get_extractor(source_type: SourceType) -> Extractor:
    """Return the extractor registered for a source type."""
    # Lazy imports avoid circular deps and loading unused extractors
    from epistemy_m3.extract.markdown import MarkdownExtractor
    from epistemy_m3.extract.pptx import PptxExtractor
    from epistemy_m3.extract.docx import DocxExtractor
    from epistemy_m3.extract.pdf import PdfExtractor
    table = {
        SourceType.MARKDOWN: MarkdownExtractor(),
        # Plain text is structurally valid markdown, so same extractor works
        SourceType.TEXT: MarkdownExtractor(),
        SourceType.PPTX: PptxExtractor(),
        SourceType.DOCX: DocxExtractor(),
        SourceType.PDF: PdfExtractor(),
    }
    if source_type not in table:
        raise ValueError(f"no extractor for source_type={source_type}")
    return table[source_type]
