"""FastAPI glue: extract the bearer token and resolve it to a Caller.

Routes depend on ``require_caller`` to get an authenticated identity. Wiring it
in replaces the dev-only `x-user-id`/`x-role`/`x-org-name` headers in
``http_app.py`` with a real, verified `Caller`.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException

from backend.auth.jwt_auth import CognitoSettings, CognitoValidator, TokenError
from backend.models import Caller


@lru_cache(maxsize=1)
def _validator() -> CognitoValidator:
    """Build the validator once; the JWKS client caches the public keys."""
    return CognitoValidator(CognitoSettings.from_env())


def _bearer_token(authorization: str = Header(default="")) -> str:
    """Pull the raw JWT out of an `Authorization: Bearer <token>` header."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


def require_caller(token: str = Depends(_bearer_token)) -> Caller:
    """FastAPI dependency: verified `Caller` or 401.

    Usage::

        @app.get("/whoami")
        def whoami(caller: Caller = Depends(require_caller)):
            return {"user_id": caller.user_id, "org_id": caller.org_id,
                    "role": caller.role}
    """
    try:
        return _validator().caller_from_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
