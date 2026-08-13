"""Rebuild the container image and force a new ECS deployment (no DB changes).

Use after editing application code or static UI files to push them live without
re-running schema migration.

  AWS_PROFILE=personal EPISTEMY_ACCOUNT=883353268066 \
    PYTHONPATH=. python -m infra.redeploy_app
"""
from __future__ import annotations
import pathlib

import boto3

from backend.config import Settings
from infra import imagebuild

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    """Rebuild :latest from current source, then roll the Fargate service."""
    settings = Settings()
    session = boto3.Session(region_name=settings.region)
    _rebuild_image(session, settings)
    _force_new_deployment(session, settings)
    print(f"redeployed {settings.service_name} on {settings.cluster_name}")


def _rebuild_image(session, settings: Settings) -> None:
    """Repackage source → CodeBuild → push :latest to ECR."""
    s3, cb = session.client("s3"), session.client("codebuild")
    key = f"build/{settings.env}/source.zip"
    imagebuild.upload_source(s3, settings.bucket, key, ROOT)
    status = imagebuild.run_build(cb, settings.image_project)
    if status != "SUCCEEDED":
        raise RuntimeError(f"image rebuild ended {status}")


def _force_new_deployment(session, settings: Settings) -> None:
    """Trigger a rolling deployment so tasks pull the refreshed image."""
    ecs = session.client("ecs")
    ecs.update_service(cluster=settings.cluster_name,
                       service=settings.service_name, forceNewDeployment=True)


if __name__ == "__main__":
    main()
