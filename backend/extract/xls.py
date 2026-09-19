"""Legacy XLS extractor: one unit per sheet via xlrd (reads .xls only).

Mirrors the XLSX extractor's shape — each sheet is a structural unit whose
heading_path is the sheet name.
"""
from __future__ import annotations
from typing import List

from backend.models import ChunkPosition
from backend.chunking import ExtractedUnit


class XlsExtractor:
    """xlrd sheet walk; one ExtractedUnit per non-empty sheet."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        units: List[ExtractedUnit] = []
        for sheet in book.sheets():
            lines = []
            for r in range(sheet.nrows):
                cells = [str(c) for c in sheet.row_values(r)]
                if any(c.strip() for c in cells):
                    lines.append("\t".join(cells).rstrip("\t"))
            text = "\n".join(lines)
            if text.strip():
                units.append(ExtractedUnit(
                    text=text, position=ChunkPosition(heading_path=[sheet.name])))
        return units
