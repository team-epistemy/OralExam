"""Read secrets from GCP Secret Manager — the GCP-side analogue of AWS Secrets
Manager, used when Settings.secrets_provider == "gcp" so the same image runs on
Cloud Run against GCP-managed secrets. Cached per (project, name).
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=64)
def get_secret(project: str, name: str, version: str = "latest") -> str:
    """Return the secret payload as text. Raises if the secret is unavailable."""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{project}/secrets/{name}/versions/{version}"
    return client.access_secret_version(name=path).payload.data.decode("utf-8")
