"""Cached RS256 validator for Cognito access tokens (no shared secret)."""
from __future__ import annotations

import jwt
from jwt import PyJWKClient


class TokenError(Exception):
    """Raised for any token that fails signature or claim validation."""


class AccessTokenValidator:
    """Validate a Cognito access token against the pool's rotating JWKS.

    issuer = https://cognito-idp.{region}.amazonaws.com/{pool_id}
    Cognito access tokens carry `client_id` (not `aud`) and `token_use=access`.
    """

    def __init__(self, issuer: str, client_id: str):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        # PyJWKClient caches keys and refetches on unknown kid (rotation-safe).
        self._jwks = PyJWKClient(f"{self.issuer}/.well-known/jwks.json", lifespan=3600)

    def validate(self, token: str) -> dict:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer,
                options={"require": ["exp", "iss"], "verify_aud": False},
            )
        except Exception as exc:  # signature, exp, iss, kid, malformed
            raise TokenError(f"invalid token: {exc}") from exc
        if claims.get("token_use") != "access":
            raise TokenError("not an access token")
        if claims.get("client_id") != self.client_id:
            raise TokenError("client_id mismatch")
        return claims


def validator_from_config(cfg: dict) -> AccessTokenValidator:
    """Build a validator from the epistemy/cognito-{env} secret dict."""
    return AccessTokenValidator(cfg["issuer"], cfg["client_id"])
