"""Embedding package (T6): Bedrock Titan v2 client and a fake for tests."""
from epistemy_m3.embedding.embedder import Embedder, BedrockEmbedder
from epistemy_m3.embedding.fake import FakeEmbedder

__all__ = ["Embedder", "BedrockEmbedder", "FakeEmbedder"]
