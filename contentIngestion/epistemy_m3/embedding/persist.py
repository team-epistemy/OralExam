"""Chunk persistence (T6): embed then bulk-upsert, idempotently."""
from __future__ import annotations
from typing import List

from epistemy_m3.models import Chunk, MaterialVersion
from epistemy_m3.db.repository import Repository
from epistemy_m3.embedding.embedder import Embedder


def stamp_tenant(chunks: List[Chunk], version: MaterialVersion) -> List[Chunk]:
    """Copy tenant + version ids from the version onto each chunk."""
    for ch in chunks:
        ch.material_version_id = version.material_version_id
        ch.course_id = version.course_id
        ch.org_id = version.org_id
    return chunks


def embed_chunks(embedder: Embedder, chunks: List[Chunk]) -> List[Chunk]:
    """Attach an embedding vector to every chunk in place."""
    vectors = embedder.embed([c.text for c in chunks])
    for ch, vec in zip(chunks, vectors):
        ch.embedding = vec
    return chunks


def persist_chunks(repo: Repository, version: MaterialVersion,
                   chunks: List[Chunk]) -> int:
    """Set RLS tenant then bulk-upsert; idempotent on (version, index)."""
    repo.set_tenant(version.org_id)
    return repo.upsert_chunks(chunks)
