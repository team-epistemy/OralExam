"""Pre-tenant identity chokepoint: access token -> provisioned app_user.

Every authenticated request funnels through resolve(): validate the Cognito
access token, then map its `sub` to an auth.app_user row via the SECURITY DEFINER
resolver (which runs before app.org_id is set). The middleware receives a verified
(user_id, org_id, role). A short TTL cache keeps the DB off the hot path without
letting a disabled user linger past the window.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from threading import Lock

from backend.auth.token import AccessTokenValidator


class IdentityError(Exception):
    """Token is valid but maps to no active app_user (unprovisioned/disabled)."""


@dataclass(frozen=True)
class Identity:
    user_id: str   # email — the value handlers read as x-user-id
    org_id: str    # verified tenant UUID
    role: str
    status: str


class IdentityResolver:
    """Resolve access tokens to identities with a short per-sub cache."""

    def __init__(self, validator: AccessTokenValidator, pool, cache_ttl: float = 30.0):
        self._validator = validator
        self._pool = pool
        self._ttl = cache_ttl
        self._cache: dict[str, tuple[Identity, float]] = {}
        self._lock = Lock()

    def resolve(self, token: str) -> Identity:
        """Validate the token and return its provisioned identity (cached briefly)."""
        sub = self._validator.validate(token)["sub"]  # raises TokenError
        hit = self._cached(sub)
        if hit is not None:
            return hit
        identity = self._load(sub)
        with self._lock:
            self._cache[sub] = (identity, time.monotonic() + self._ttl)
        return identity

    def validate_only(self, token: str) -> dict:
        """Validate signature/claims without requiring a provisioned app_user.

        Used by invitation redemption: the caller has a Cognito account but no
        app_user row yet, so resolve() would reject them.
        """
        return self._validator.validate(token)

    def invalidate(self, sub: str) -> None:
        """Drop a cached identity so a role/status change takes effect at once."""
        with self._lock:
            self._cache.pop(sub, None)

    def _cached(self, sub: str) -> Identity | None:
        with self._lock:
            entry = self._cache.get(sub)
            if entry and entry[1] > time.monotonic():
                return entry[0]
            self._cache.pop(sub, None)
        return None

    def _load(self, sub: str) -> Identity:
        """One SECURITY DEFINER lookup; runs before any tenant GUC is set."""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, org_id, role, status FROM auth.resolve_user(%s)",
                    (sub,))
                row = cur.fetchone()
            conn.rollback()  # end the snapshot; leave the conn tenant-clean
        finally:
            self._pool.putconn(conn)
        if not row:
            raise IdentityError("no active user for this identity")
        _id, email, org_id, role, status = row
        return Identity(user_id=email, org_id=str(org_id), role=role, status=status)
