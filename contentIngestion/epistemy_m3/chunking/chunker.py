"""Structure-aware chunker (T5). Pure Python: no IO, no AWS, no DB."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional

from epistemy_m3.models import Chunk, ChunkPosition
from epistemy_m3.chunking.tokenizer import Tokenizer, ApproxTokenizer

_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ExtractedUnit:
    """One structural unit emitted by an extractor (slide, heading, paragraph)."""

    text: str
    position: ChunkPosition = field(default_factory=ChunkPosition)
    structured: bool = True


class Chunker:
    """Turns extracted units into contiguous, token-sized chunks."""

    def __init__(self, tokenizer: Optional[Tokenizer] = None,
                 window: int = 800, overlap: int = 100):
        self.tok = tokenizer or ApproxTokenizer()
        # 800 tokens fits ~3 chunks per Titan embed call; 100-token overlap
        # preserves sentence context across chunk boundaries
        self.window = window
        self.overlap = overlap

    def chunk(self, units: List[ExtractedUnit]) -> List[Chunk]:
        """Emit chunks with a contiguous chunk_index across all units."""
        out: List[Chunk] = []
        for unit in units:
            for text in self._split_unit(unit):
                out.append(self._make_chunk(text, unit.position, len(out)))
        return out

    def _split_unit(self, unit: ExtractedUnit) -> List[str]:
        """Keep structured units (slides, headings) whole when they fit."""
        text = unit.text.strip()
        if not text:
            return []
        # Structured units (e.g. a single slide) stay intact to preserve meaning
        if unit.structured and self.tok.count(text) <= self.window:
            return [text]
        return self._window(text)

    def _window(self, text: str) -> List[str]:
        """Split on sentence boundaries, not mid-word, for embedding quality."""
        sentences = [s for s in _SENT.split(text) if s]
        windows, buf = [], []
        for sent in sentences:
            buf.append(sent)
            if self.tok.count(" ".join(buf)) >= self.window:
                windows.append(" ".join(buf))
                buf = self._overlap_tail(buf)
        if buf:
            windows.append(" ".join(buf))
        return windows

    def _overlap_tail(self, buf: List[str]) -> List[str]:
        """Retain trailing sentences up to the overlap token budget."""
        tail, total = [], 0
        for sent in reversed(buf):
            total += self.tok.count(sent)
            if total > self.overlap:
                break
            tail.insert(0, sent)
        return tail

    def _make_chunk(self, text: str, pos: ChunkPosition, index: int) -> Chunk:
        """Build a Chunk stub; tenant ids filled by stamp_tenant() after chunking."""
        return Chunk(material_version_id="", course_id="", org_id="",
                     chunk_index=index, text=text,
                     token_count=self.tok.count(text), position=pos)
