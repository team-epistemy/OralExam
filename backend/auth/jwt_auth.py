"""Validate Cognito-issued JWTs and resolve them into a `Caller`.

This is the HLD "Option B" defense-in-depth gate: even though API Gateway / ALB
validates the token at the edge, the FastAPI process re-verifies it and is the
single place where a token becomes the identity (user_id, org_id, role) used for
RBAC and Postgres RLS.

The link to Cognito is the token alone — we never call Cognito per request. We
fetch its public signing keys once from the JWKS endpoint (cached by
``PyJWKClient``) and verify every token's signature locally.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict

import jwt
from jwt import PyJWKClient

from backend.models import Caller, Role


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or untrusted."""


@dataclass(frozen=True)
class CognitoSettings:
    """Identifiers that link this service to a Cognito User Pool.

    No shared secret is involved — only public identifiers and the JWKS URL
    derived from them.
    """

    region: str
    user_pool_id: str
    app_client_id: str
    # Custom claim names Cognito carries in the token. org_id/role are written
    # into the pool as custom attributes during M1 signup.
    org_claim: str = "custom:org_id"
    role_claim: str = "custom:role"

    @property
    def issuer(self) -> str:
        """The `iss` value Cognito stamps on every token from this pool."""
        return (f"https://cognito-idp.{self.region}.amazonaws.com/"
                f"{self.user_pool_id}")

    @property
    def jwks_url(self) -> str:
        """Where Cognito publishes the public keys that verify signatures."""
        return f"{self.issuer}/.well-known/jwks.json"

    @classmethod
    def from_env(cls) -> "CognitoSettings":
        """Build from env vars; mirrors the rest of the service's config style."""
        region = os.getenv("AWS_REGION", "us-west-2")
        pool = os.getenv("EPISTEMY_COGNITO_POOL_ID", "")
        client = os.getenv("EPISTEMY_COGNITO_CLIENT_ID", "")
        if not pool or not client:
            raise TokenError("Cognito pool/client id not configured")
        return cls(region=region, user_pool_id=pool, app_client_id=client)


class CognitoValidator:
    """Verifies access tokens and maps verified claims to a `Caller`.

    Construct once and reuse: ``PyJWKClient`` caches the fetched JWKS so we do
    not hit the network on every request.
    """

    def __init__(self, settings: CognitoSettings,
                 jwk_client: PyJWKClient | None = None):
        self._s = settings
        # Injectable for tests; defaults to the real cached JWKS client.
        self._jwks = jwk_client or PyJWKClient(settings.jwks_url)

    def caller_from_token(self, token: str) -> Caller:
        """Validate a raw bearer token and return the resolved Caller."""
        claims = self.verify(token)
        return caller_from_claims(claims, self._s)

    def verify(self, token: str) -> Dict[str, Any]:
        """Verify signature, issuer, audience, and expiry; return claims."""
        if not token:
            raise TokenError("missing token")
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._s.issuer,
                # Cognito access tokens carry client_id (checked below) rather
                # than aud; verify audience only when present (id tokens).
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:  # expired, bad signature, wrong issuer…
            raise TokenError(f"invalid token: {exc}") from exc


def _audience_ok(claims: Dict[str, Any], client_id: str) -> bool:
    """Accept the token if either client_id (access) or aud (id) matches."""
    return claims.get("client_id") == client_id or claims.get("aud") == client_id


def caller_from_claims(claims: Dict[str, Any],
                       settings: CognitoSettings) -> Caller:
    """Map verified Cognito claims to the shared `Caller` identity.

    Raises ``TokenError`` if the token is for another app client or is missing
    the org/role our RBAC + RLS require.
    """
    if not _audience_ok(claims, settings.app_client_id):
        raise TokenError("token was not issued for this app client")

    user_id = claims.get("sub")
    org_id = claims.get(settings.org_claim)
    role_raw = claims.get(settings.role_claim)
    if not user_id or not org_id or not role_raw:
        raise TokenError("token missing sub/org_id/role claims")

    try:
        role = Role(role_raw)
    except ValueError as exc:
        raise TokenError(f"unknown role claim: {role_raw}") from exc

    return Caller(user_id=user_id, org_id=org_id, role=role)
