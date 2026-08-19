"""Build the SPA, rebuild the container image, and force a new ECS deployment
(no DB changes).

Use after editing application code or UI to push it live without re-running the
schema migration. The frontend is compiled here (fail-loud) so a broken build
aborts the deploy instead of silently shipping the previously-built assets.

  AWS_PROFILE=personal EPISTEMY_ACCOUNT=883353268066 \
    PYTHONPATH=. python -m infra.redeploy_app
"""
from __future__ import annotations
import pathlib

import boto3

from backend.config import Settings
from infra import imagebuild, frontend_build

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    """Build the SPA, rebuild :latest from current source, then roll the service."""
    settings = Settings()
    session = boto3.Session(region_name=settings.region)
    _build_frontend()
    _rebuild_image(session, settings)
    _force_new_deployment(session, settings)
    print(f"redeployed {settings.service_name} on {settings.cluster_name}")


def _build_frontend() -> None:
    """Compile the SPA into the served path. Raises (aborting the deploy) on any
    build error, so a broken frontend build can never ship stale assets."""
    bundles = frontend_build.build_frontend(ROOT)
    print(f"frontend built: {', '.join(bundles) or 'NO js bundle found'}")


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
