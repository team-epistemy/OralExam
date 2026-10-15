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


class FirebaseTokenValidator:
    """Validate a GCIP / Firebase ID token (RS256), the GCP-side analogue of the
    Cognito access-token validator. Same .validate(token)->claims surface so the
    IdentityResolver is provider-agnostic.

    issuer = https://securetoken.google.com/{project}; aud = {project}. Unlike
    Cognito there's no token_use/client_id — the audience is the project id.
    """

    _JWK_URL = ("https://www.googleapis.com/service_accounts/v1/jwk/"
                "securetoken@system.gserviceaccount.com")

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.issuer = f"https://securetoken.google.com/{project_id}"
        self._jwks = PyJWKClient(self._JWK_URL, lifespan=3600)

    def validate(self, token: str) -> dict:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                issuer=self.issuer, audience=self.project_id,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:  # signature, exp, iss, aud, kid, malformed
            raise TokenError(f"invalid token: {exc}") from exc
        if not claims.get("sub"):
            raise TokenError("no subject")
        return claims


def validator_from_config(cfg: dict):
    """Build the token validator for the configured IdP.

    provider=="gcip" → Firebase/GCIP ID-token validator (project id);
    otherwise the Cognito access-token validator (issuer + client_id).
    """
    if cfg.get("provider") == "gcip":
        return FirebaseTokenValidator(cfg["project_id"])
    return AccessTokenValidator(cfg["issuer"], cfg["client_id"])
