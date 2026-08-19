"""Unit tests for AccessTokenValidator — signature + claim enforcement, no network."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.auth.token import AccessTokenValidator, TokenError

ISS = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_test"
CID = "test-client-id"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture
def validator(keypair, monkeypatch):
    """Validator whose JWKS lookup returns the in-memory public key (no network)."""
    _, pub = keypair

    class _Key:
        key = pub

    v = AccessTokenValidator(ISS, CID)
    monkeypatch.setattr(v._jwks, "get_signing_key_from_jwt", lambda token: _Key())
    return v


def _mint(priv, **overrides):
    claims = {
        "sub": "cognito-sub-123", "iss": ISS, "client_id": CID,
        "token_use": "access", "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": KID})


def test_valid_token(validator, keypair):
    priv, _ = keypair
    claims = validator.validate(_mint(priv))
    assert claims["sub"] == "cognito-sub-123"


def test_wrong_issuer(validator, keypair):
    priv, _ = keypair
    with pytest.raises(TokenError):
        validator.validate(_mint(priv, iss="https://evil.example.com"))


def test_wrong_client_id(validator, keypair):
    priv, _ = keypair
    with pytest.raises(TokenError):
        validator.validate(_mint(priv, client_id="other-client"))


def test_id_token_rejected(validator, keypair):
    priv, _ = keypair
    with pytest.raises(TokenError):
        validator.validate(_mint(priv, token_use="id"))


def test_expired(validator, keypair):
    priv, _ = keypair
    with pytest.raises(TokenError):
        validator.validate(_mint(priv, exp=int(time.time()) - 10))


def test_tampered_signature(validator, keypair):
    priv, _ = keypair
    token = _mint(priv)
    tampered = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
    with pytest.raises(TokenError):
        validator.validate(tampered)
