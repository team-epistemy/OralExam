"""Database layer: repository protocols and an in-memory implementation."""
from backend.db.repository import Repository
from backend.db.memory import InMemoryRepository

__all__ = ["Repository", "InMemoryRepository"]
