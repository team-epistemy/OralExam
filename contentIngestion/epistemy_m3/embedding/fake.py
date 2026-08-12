"""Deterministic offline embedder for tests (no Bedrock network calls)."""
from __future__ import annotations
import hashlib
import struct
from typing import List


class FakeEmbedder:
    """Hash-seeded pseudo-embeddings; stable for a given text and dims."""

    def __init__(self, dims: int = 1024):
        self.dims = dims

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> List[float]:
        """Derive a normalized vector from repeated SHA-256 digests."""
        raw = self._raw_floats(text)
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        return [x / norm for x in raw]

    def _raw_floats(self, text: str) -> List[float]:
        """Expand the text hash into `dims` float components."""
        out: List[float] = []
        counter = 0
        while len(out) < self.dims:
            digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
            out.extend(struct.unpack("8f", digest[:32]))
            counter += 1
        return out[: self.dims]
