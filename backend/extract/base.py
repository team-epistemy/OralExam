"""Extractor protocol and a source_type dispatcher."""
from __future__ import annotations
from typing import List, Protocol

from backend.models import SourceType
from backend.chunking import ExtractedUnit


class Extractor(Protocol):
    """Maps raw file bytes to structural units the chunker understands."""

    def extract(self, data: bytes) -> List[ExtractedUnit]: ...


def get_extractor(source_type: SourceType) -> Extractor:
    """Return the extractor registered for a source type."""
    # Lazy imports avoid circular deps and loading unused extractors
    from backend.extract.markdown import MarkdownExtractor
    from backend.extract.pptx import PptxExtractor
    from backend.extract.docx import DocxExtractor
    from backend.extract.doc import DocExtractor
    from backend.extract.rtf import RtfExtractor
    from backend.extract.pdf import PdfExtractor
    from backend.extract.csv_ext import CsvExtractor
    from backend.extract.xlsx_ext import XlsxExtractor
    table = {
        SourceType.MARKDOWN: MarkdownExtractor(),
        # Plain text is structurally valid markdown, so same extractor works
        SourceType.TEXT: MarkdownExtractor(),
        SourceType.PPTX: PptxExtractor(),
        SourceType.DOCX: DocxExtractor(),
        SourceType.DOC: DocExtractor(),
        SourceType.RTF: RtfExtractor(),
        SourceType.PDF: PdfExtractor(),
        SourceType.CSV: CsvExtractor(),
        SourceType.XLSX: XlsxExtractor(),
    }
    if source_type not in table:
        raise ValueError(f"no extractor for source_type={source_type}")
    return table[source_type]
