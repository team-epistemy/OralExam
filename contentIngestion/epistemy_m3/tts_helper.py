"""ElevenLabs TTS helper — resolves the key from env/Secrets Manager and synthesizes audio.

Returns None (never raises) when the key is missing or the call fails, so the exam
degrades gracefully to text-only until the ElevenLabs key secret is populated.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

from epistemy_m3.config import Settings

logger = logging.getLogger(__name__)
_key_cache: dict = {}


def _resolve_key(settings: Settings) -> Optional[str]:
    """ELEVENLABS_API_KEY env (dev) else Secrets Manager. Cached only on success."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if key:
        return key
    secret = getattr(settings, "elevenlabs_secret_name", "")
    if not secret:
        return None
    if secret in _key_cache:
        return _key_cache[secret]
    try:
        import boto3
        sm = boto3.client("secretsmanager", region_name=settings.region)
        val = sm.get_secret_value(SecretId=secret)["SecretString"].strip()
        _key_cache[secret] = val
        return val
    except Exception as exc:  # noqa: BLE001 - missing/inaccessible secret -> text-only
        logger.info("ElevenLabs key unavailable (TTS disabled): %s", exc)
        return None


def synthesize(settings: Settings, text: str, voice_id: Optional[str] = None) -> Optional[bytes]:
    """Return MP3 audio bytes for `text`, or None if TTS is not configured / failed."""
    key = _resolve_key(settings)
    if not key:
        return None
    vid = voice_id or getattr(settings, "elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM")
    model = getattr(settings, "elevenlabs_model", "eleven_turbo_v2_5")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    body = json.dumps({
        "text": text[:2500],
        "model_id": model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": key, "content-type": "application/json", "accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        logger.warning("ElevenLabs TTS %s: %s", exc.code, exc.read()[:200])
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("ElevenLabs TTS error: %s", exc)
        return None
