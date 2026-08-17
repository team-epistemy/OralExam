"""Bedrock Titan Text Embeddings v2 client with throttling backoff (T6)."""
from __future__ import annotations
import json
import time
from typing import Protocol, List


class Embedder(Protocol):
    """Maps chunk texts to fixed-dimension embedding vectors."""

    def embed(self, texts: List[str]) -> List[List[float]]: ...


_RETRYABLE = ("ThrottlingException", "ModelTimeoutException")


class BedrockEmbedder:
    """Calls Titan v2 per text, backing off on throttling/timeout errors."""

    def __init__(self, client, model_id: str, dims: int = 1024,
                 max_retries: int = 5):
        self.client = client
        self.model_id = model_id
        self.dims = dims
        self.max_retries = max_retries

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed each text; throttling does not drop chunks."""
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> List[float]:
        """Invoke the model with bounded exponential backoff."""
        for attempt in range(self.max_retries):
            try:
                return self._invoke(text)
            except Exception as exc:
                if not self._is_retryable(exc) or attempt == self.max_retries - 1:
                    raise
                # Exponential backoff: 1s, 2s, 4s, 8s, 16s for Bedrock throttling
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    def _invoke(self, text: str) -> List[float]:
        body = json.dumps({"inputText": text, "dimensions": self.dims,
                           "normalize": True})
        resp = self.client.invoke_model(modelId=self.model_id, body=body)
        payload = json.loads(resp["body"].read())
        return payload["embedding"]

    def _is_retryable(self, exc: Exception) -> bool:
        """Treat Bedrock throttling and timeouts as retryable."""
        name = type(exc).__name__
        return name in _RETRYABLE or "Throttl" in str(exc)
