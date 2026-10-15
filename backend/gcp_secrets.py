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


def resolve(settings, aws_secret_id: str) -> str:
    """Provider-aware secret read so helpers stay cloud-agnostic: GCP Secret
    Manager when Settings.secrets_provider == "gcp" (id is the AWS id minus the
    "epistemy/" prefix), else AWS Secrets Manager. Returns the raw string.
    """
    if getattr(settings, "secrets_provider", "aws") == "gcp":
        return get_secret(settings.gcp_project, aws_secret_id.split("/", 1)[-1])
    import boto3
    sm = boto3.client("secretsmanager", region_name=settings.region)
    return sm.get_secret_value(SecretId=aws_secret_id)["SecretString"]
