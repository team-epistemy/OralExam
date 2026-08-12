"""Database layer: repository protocols and an in-memory implementation."""
from epistemy_m3.db.repository import Repository
from epistemy_m3.db.memory import InMemoryRepository

__all__ = ["Repository", "InMemoryRepository"]
