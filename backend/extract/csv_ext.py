"""CSV extractor: linearise rows to readable text, then shape as markdown.

Tabular data has no prose structure, so each row is rendered as
"header: value; header: value" (or the raw cells when there's no usable header)
and handed to MarkdownExtractor for paragraph-unit splitting. The upload is
stored, chunked, embedded and searchable — but spreadsheet/CSV uploads are
deliberately EXCLUDED from concept-graph generation (see the ingest pipeline).
"""
from __future__ import annotations

import csv
import io
from typing import List

from backend.chunking import ExtractedUnit
from backend.extract.markdown import MarkdownExtractor


def rows_to_text(rows: List[List[str]]) -> str:
    """Render table rows as text: a header line, then one paragraph per row.

    Shared with the XLSX extractor so both formats read identically downstream."""
    cleaned = [[(c or "").strip() for c in r] for r in rows]
    cleaned = [r for r in cleaned if any(r)]
    if not cleaned:
        return ""
    header = cleaned[0]
    # A header row is usable if every cell is a non-empty, non-numeric label.
    def _numeric(x: str) -> bool:
        try:
            float(x.replace(",", ""))
            return True
        except ValueError:
            return False
    headed = len(cleaned) > 1 and all(h and not _numeric(h) for h in header)

    lines: List[str] = []
    if headed:
        lines.append(" | ".join(header))
        for row in cleaned[1:]:
            pairs = [f"{header[i] if i < len(header) else f'col{i + 1}'}: {val}"
                     for i, val in enumerate(row) if val]
            if pairs:
                lines.append("; ".join(pairs))
    else:
        for row in cleaned:
            cells = [c for c in row if c]
            if cells:
                lines.append(" | ".join(cells))
    return "\n\n".join(lines)


class CsvExtractor:
    """CSV bytes → linearised text → markdown paragraph units."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        text = data.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        return MarkdownExtractor().extract(rows_to_text(rows).encode("utf-8"))
