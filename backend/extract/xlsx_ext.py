"""XLSX extractor: read each sheet via openpyxl, linearise rows, shape as markdown.

openpyxl is pure-Python (no system libs). Each worksheet becomes a titled block
whose rows are rendered like the CSV extractor, so both tabular formats read
identically downstream. Like CSV, XLSX uploads are ingested/searchable but
EXCLUDED from concept-graph generation (see the ingest pipeline).
"""
from __future__ import annotations

import io
from typing import List

from backend.chunking import ExtractedUnit
from backend.extract.csv_ext import rows_to_text
from backend.extract.markdown import MarkdownExtractor


class XlsxExtractor:
    """XLSX bytes → per-sheet linearised text → markdown paragraph units."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        blocks: List[str] = []
        try:
            for ws in wb.worksheets:
                rows: List[List[str]] = []
                for row in ws.iter_rows(values_only=True):
                    cells = ["" if c is None else str(c).strip() for c in row]
                    if any(cells):
                        rows.append(cells)
                body = rows_to_text(rows)
                if body:
                    blocks.append(f"# {ws.title}\n\n{body}")
        finally:
            wb.close()
        return MarkdownExtractor().extract("\n\n".join(blocks).encode("utf-8"))
