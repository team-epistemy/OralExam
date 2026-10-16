"""Google Cloud Speech-to-Text (v2, Chirp 2) — batch transcription for the
subject exam's answer dictation, replacing the browser Web Speech API on the GCP
build. Uses the implicit recognizer with inline config + autoDecodingConfig so
the browser's WEBM/OPUS clip is accepted as-is. ADC on Cloud Run (SA needs the
Speech user role). Transcription only — no pronunciation/timings (not needed).
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


class GoogleSpeech:
    def __init__(self, project: str, location: str = "us-central1",
                 model: str = "chirp_2", language: str = "en-US"):
        self.project = project
        self.model = model
        self.language = language
        self._url = (f"https://{location}-speech.googleapis.com/v2/projects/"
                     f"{project}/locations/{location}/recognizers/_:recognize")

    def _token(self) -> str:
        import google.auth
        from google.auth.transport.requests import Request
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        return creds.token

    def transcribe(self, audio: bytes) -> str:
        """Return the concatenated transcript for an audio clip (empty if none)."""
        body = json.dumps({
            "config": {
                "model": self.model,
                "languageCodes": [self.language],
                "features": {"enableAutomaticPunctuation": True},
                "autoDecodingConfig": {},
            },
            "content": base64.b64encode(audio).decode(),
        }).encode()
        req = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self._token()}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
        parts = [r["alternatives"][0]["transcript"]
                 for r in payload.get("results", [])
                 if r.get("alternatives") and r["alternatives"][0].get("transcript")]
        return " ".join(p.strip() for p in parts).strip()
