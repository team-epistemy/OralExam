"""search_corpus tool (T15): retrieval primitive for M5 and M7."""
from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from backend.models import Caller
from backend.search.corpus_search import CorpusSearcher, SearchResult
from backend.api.service import AuthorizationError


class SearchTools:
    """Course-scoped semantic search, enforcing membership and RLS."""

    def __init__(self, searcher: CorpusSearcher, is_member):
        """
        Parameters
        ----------
        searcher : CorpusSearcher
            Configured corpus searcher (conn + embedder).
        is_member : callable(Caller, str) -> bool
            Membership check; any course role suffices for search.
        """
        self.searcher = searcher
        self.is_member = is_member

    def search_corpus(
        self,
        caller: Caller,
        course_id: str,
        query: str,
        k: int = 10,
        material_version_ids: Optional[List[str]] = None,
    ) -> List[dict]:
        """Embed query and return top-k matching chunks from the course.

        Parameters
        ----------
        caller : Caller
            Authenticated identity with org_id for RLS.
        course_id : str
            Course UUID to scope the search.
        query : str
            Natural-language query to embed and match.
        k : int
            Number of results (default 10, capped at 50).
        material_version_ids : list[str] | None
            Pin to specific versions for reproducibility (M7).
            When None, only current versions are searched.

        Returns
        -------
        list[dict]
            Each dict has: text, position, material_version_id, score,
            chunk_index.

        Raises
        ------
        AuthorizationError
            If caller is not a member of the course.
        ValueError
            If query is empty or k is out of range.
        """
        self._validate(query, k)
        self._require_member(caller, course_id)
        k = min(k, 50)  # hard cap to prevent abuse

        results = self.searcher.search(
            org_id=caller.org_id,
            course_id=course_id,
            query=query,
            k=k,
            material_version_ids=material_version_ids,
        )
        return [asdict(r) for r in results]

    def _require_member(self, caller: Caller, course_id: str) -> None:
        """Any course membership suffices for search."""
        if not self.is_member(caller, course_id):
            raise AuthorizationError("course membership required")

    def _validate(self, query: str, k: int) -> None:
        """Reject obviously bad inputs before hitting the embedder."""
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if k < 1:
            raise ValueError("k must be at least 1")
