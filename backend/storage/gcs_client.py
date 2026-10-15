"""GCS storage mirroring the S3Storage surface (presign PUT/GET, exists, read).

Signed URLs use the IAM signBlob API (service-account email + a live access
token) rather than a private key, so the Cloud Run runtime SA signs them
directly — it needs roles/iam.serviceAccountTokenCreator on itself. Object keys
are identical to the S3 keys, so stored references resolve unchanged.
"""
from __future__ import annotations

import datetime


class GcsStorage:
    """Real GCS-backed storage implementing the S3Storage protocol."""

    def __init__(self, bucket: str, ttl: int = 300):
        from google.cloud import storage
        self._client = storage.Client()
        self.bucket = bucket
        self._bucket = self._client.bucket(bucket)
        self.ttl = ttl

    def _signer(self):
        """(service_account_email, access_token) for keyless V4 signing."""
        import google.auth
        from google.auth.transport import requests as g_requests
        creds, _ = google.auth.default()
        creds.refresh(g_requests.Request())
        return getattr(creds, "service_account_email", None), creds.token

    def presign_put(self, key: str, mime_type: str, max_bytes: int) -> str:
        """V4 signed PUT locked to key + content-type (client must send it)."""
        email, token = self._signer()
        return self._bucket.blob(key).generate_signed_url(
            version="v4", method="PUT",
            expiration=datetime.timedelta(seconds=self.ttl),
            content_type=mime_type,
            service_account_email=email, access_token=token)

    def presign_get(self, key: str, file_name: str = "", ttl: int = 600) -> str:
        """V4 signed GET; inline disposition so browsers render PDFs/images."""
        email, token = self._signer()
        extra = {}
        if file_name:
            extra["response_disposition"] = f'inline; filename="{file_name}"'
        return self._bucket.blob(key).generate_signed_url(
            version="v4", method="GET",
            expiration=datetime.timedelta(seconds=ttl),
            service_account_email=email, access_token=token, **extra)

    def object_exists(self, key: str) -> bool:
        """True when the object is present in the bucket."""
        return self._bucket.blob(key).exists()

    def get_bytes(self, key: str) -> bytes:
        """Download an object body as bytes."""
        return self._bucket.blob(key).download_as_bytes()
