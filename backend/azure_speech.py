"""Azure AI Speech helper — mints short-lived browser tokens for the CEFR
speaking exam's pronunciation assessment.

The Azure key never leaves the server: the browser receives only a ~10-minute
authorization token (cached here for 8 minutes) plus the region, which the
Speech SDK uses for continuous recognition + pronunciation assessment. Mirrors
tts_helper: key from env (dev) else Secrets Manager; returns None (never raises)
when unavailable, so the speaking exam falls back to text capture.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Optional

from backend.config import Settings

logger = logging.getLogger(__name__)
_key_cache: dict = {}
# Azure tokens are valid ~10 min; refresh a little early so an in-flight session
# never uses an almost-expired token.
_TOKEN_TTL_S = 8 * 60
_token_cache: dict = {"token": None, "at": 0.0}


def _resolve_key(settings: Settings) -> Optional[str]:
    """AZURE_SPEECH_KEY env (dev) else Secrets Manager. Cached only on success."""
    key = os.getenv("AZURE_SPEECH_KEY")
    if key:
        return key
    secret = getattr(settings, "azure_speech_secret_name", "")
    if not secret:
        return None
    if secret in _key_cache:
        return _key_cache[secret]
    try:
        if getattr(settings, "secrets_provider", "aws") == "gcp":
            from backend import gcp_secrets
            raw = gcp_secrets.resolve(settings, secret).strip()
        else:
            import boto3
            sm = boto3.client("secretsmanager", region_name=settings.region)
            raw = sm.get_secret_value(SecretId=secret)["SecretString"].strip()
        # Stored as a raw key string (matches the demo); tolerate a JSON object too.
        val = raw
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                val = parsed.get("AZURE_SPEECH_KEY") or parsed.get("key") or raw
        except (ValueError, TypeError):
            pass
        _key_cache[secret] = val
        return val
    except Exception as exc:  # noqa: BLE001 - missing/inaccessible secret -> text-only
        logger.info("Azure Speech key unavailable (pronunciation disabled): %s", exc)
        return None


def issue_token(settings: Settings, now=time.time) -> Optional[dict]:
    """Return {token, region, expires_in_sec} for the browser, or None if not
    configured / the mint failed. `now` is injectable for tests."""
    key = _resolve_key(settings)
    if not key:
        return None
    region = getattr(settings, "azure_speech_region", "eastus")
    t = now()
    cached = _token_cache.get("token")
    if cached and (t - _token_cache.get("at", 0.0)) < _TOKEN_TTL_S:
        return {"token": cached, "region": region, "expires_in_sec": _TOKEN_TTL_S}
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    req = urllib.request.Request(url, data=b"", method="POST", headers={
        "Ocp-Apim-Subscription-Key": key, "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            token = resp.read().decode()
    except urllib.error.HTTPError as exc:
        logger.warning("Azure issueToken %s: %s", exc.code, exc.read()[:200])
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Azure issueToken error: %s", exc)
        return None
    _token_cache["token"] = token
    _token_cache["at"] = t
    return {"token": token, "region": region, "expires_in_sec": _TOKEN_TTL_S}
