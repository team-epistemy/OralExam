"""Embedding package (T6): Bedrock Titan v2 client and a fake for tests."""
from backend.embedding.embedder import Embedder, BedrockEmbedder
from backend.embedding.fake import FakeEmbedder

__all__ = ["Embedder", "BedrockEmbedder", "FakeEmbedder"]
