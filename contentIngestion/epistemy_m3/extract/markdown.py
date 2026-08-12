"""Markdown extractor (T7): split on ATX headings, track heading_path."""
from __future__ import annotations
import re
from typing import List, Tuple

from epistemy_m3.models import ChunkPosition
from epistemy_m3.chunking import ExtractedUnit

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


class MarkdownExtractor:
    """One unit per heading section; plain text falls through to paragraphs."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        text = data.decode("utf-8", errors="replace")
        sections = self._split_sections(text)
        if len(sections) > 1:
            return [self._section_unit(p, b) for p, b in sections]
        return self._paragraph_units(text)

    def _split_sections(self, text: str) -> List[Tuple[List[str], str]]:
        """Walk lines, accumulating body under the active heading path."""
        sections: List[Tuple[List[str], str]] = []
        path: List[str] = []
        body: List[str] = []
        for line in text.splitlines():
            m = _HEADING.match(line)
            if not m:
                body.append(line)
                continue
            self._flush(sections, path, body)
            path = self._update_path(path, len(m.group(1)), m.group(2).strip())
            body = []
        self._flush(sections, path, body)
        return sections

    def _flush(self, sections, path, body) -> None:
        """Append the accumulated body as a section when non-empty."""
        text = "\n".join(body).strip()
        if text:
            sections.append((list(path), text))

    def _update_path(self, path: List[str], level: int, title: str) -> List[str]:
        """Truncate the path to the new heading depth, then append the title."""
        return path[: level - 1] + [title]

    def _section_unit(self, path: List[str], body: str) -> ExtractedUnit:
        return ExtractedUnit(text=body, position=ChunkPosition(heading_path=path))

    def _paragraph_units(self, text: str) -> List[ExtractedUnit]:
        """Blank-line separated paragraphs, marked unstructured for windowing."""
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return [ExtractedUnit(text=p, structured=False) for p in paras]
