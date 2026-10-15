"""Vertex AI text-embedding client (GCP-native embedder), the standalone-replica
replacement for Bedrock Titan. Uses the predict REST API with
outputDimensionality set to `dims` so vectors match the existing pgvector columns
(1024) — no re-dimensioning of the schema. ADC on Cloud Run (SA needs
roles/aiplatform.user).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import List


class VertexEmbedder:
    """Calls a Vertex text-embedding model per batch, backing off on throttling."""

    def __init__(self, project: str, location: str, model_id: str,
                 dims: int = 1024, max_retries: int = 5):
        self.project = project
        self.location = location
        self.model_id = model_id
        self.dims = dims
        self.max_retries = max_retries
        self._url = (f"https://{location}-aiplatform.googleapis.com/v1/projects/"
                     f"{project}/locations/{location}/publishers/google/models/"
                     f"{model_id}:predict")

    def _token(self) -> str:
        import google.auth
        from google.auth.transport.requests import Request
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        return creds.token

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed each text; one instance per request (gemini-embedding-001 caps
        at a single instance), throttling retried rather than dropped."""
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> List[float]:
        body = json.dumps({
            "instances": [{"content": text}],
            "parameters": {"outputDimensionality": self.dims},
        }).encode()
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    self._url, data=body, method="POST",
                    headers={"Authorization": f"Bearer {self._token()}",
                             "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = json.load(resp)
                return payload["predictions"][0]["embeddings"]["values"]
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")
