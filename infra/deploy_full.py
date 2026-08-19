"""Full deployment: build frontend, rebuild image, migrate schema, redeploy ECS.

Orchestrates the four steps needed after code + schema changes:
  1. Compile the React frontend into the path the container serves.
  2. Build a fresh container image via CodeBuild.
  3. Run the schema migration task against RDS (skipped unless SKIP_MIGRATE=0).
  4. Force a new ECS deployment so tasks pull the updated image.

Usage:
  AWS_PROFILE=personal python -m infra.deploy_full
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import boto3

from backend.config import Settings
from infra import imagebuild, schema_apply, frontend_build

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend"
FRONTEND_DIST = FRONTEND_SRC / "dist"
FRONTEND_SERVED = ROOT / "backend" / "app" / "static" / "frontend"


def main() -> None:
    """Run the full deploy pipeline: frontend -> image -> migrate -> redeploy."""
    settings = Settings()
    session = boto3.Session(region_name=settings.region)

    print(f"[deploy_full] target: env={settings.env}  "
          f"cluster={settings.cluster_name}  service={settings.service_name}")

    # --- Step 1: Compile the frontend into the path the container serves ---
    _step_build_frontend()

    # --- Step 2: Rebuild container image ---
    _step_build_image(session, settings)

    # --- Step 3: Apply schema migration (skip if tables exist) ---
    skip_migrate = os.environ.get("SKIP_MIGRATE", "1") == "1"
    if skip_migrate:
        print("[3/4] skipping migration (SKIP_MIGRATE=1, tables already exist)")
    else:
        _step_migrate(session, settings)

    # --- Step 4: Force new ECS deployment ---
    _step_redeploy(session, settings)

    print("[deploy_full] all steps completed successfully")


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def _step_build_frontend() -> None:
    """Compile React and replace the served bundle (fail-loud, shared helper).

    The Dockerfile copies only backend/, so compiled assets must already sit at
    static/frontend before the image is built.
    """
    print("[1/4] building frontend ...")
    try:
        bundles = frontend_build.build_frontend(ROOT)
    except RuntimeError as exc:
        _abort(str(exc))
    print(f"[1/4] frontend built: {', '.join(bundles) or 'no js bundle found'}")


def _step_build_image(session, settings: Settings) -> None:
    """Package source and run CodeBuild to push :latest to ECR."""
    print("[2/4] building container image via CodeBuild ...")
    s3 = session.client("s3")
    cb = session.client("codebuild")
    key = f"build/{settings.env}/source.zip"
    imagebuild.upload_source(s3, settings.bucket, key, ROOT)
    status = imagebuild.run_build(cb, settings.image_project)
    if status != "SUCCEEDED":
        _abort(f"image build ended with status: {status}")
    print("[2/4] image build SUCCEEDED")


def _step_migrate(session, settings: Settings) -> None:
    """Run a one-off Fargate task that applies schema.sql to RDS."""
    print("[3/4] applying schema migration via Fargate task ...")
    ecs = session.client("ecs")
    ec2 = session.client("ec2")

    image = settings.ecr_image()
    task_role_arn = settings.role_arn(settings.task_role)
    exec_role_arn = settings.role_arn(settings.exec_role)

    # Resolve the secret ARN from Secrets Manager if not explicitly set
    db_secret_arn = settings.db_secret_arn
    if not db_secret_arn:
        sm = session.client("secretsmanager")
        db_secret_arn = sm.describe_secret(
            SecretId=settings.db_secret_name
        )["ARN"]

    env = [
        {"name": "EPISTEMY_DB_SECRET_ARN", "value": db_secret_arn},
        {"name": "EPISTEMY_DB_NAME", "value": settings.db_name},
        {"name": "AWS_REGION", "value": settings.region},
        {"name": "EPISTEMY_ACCOUNT", "value": settings.account_id},
    ]

    task_def_arn = schema_apply.register_migrate_task(
        ecs, settings.migrate_family, image, task_role_arn,
        exec_role_arn, settings.region, settings.log_group, env)

    subnets, sg_id = _discover_network(ec2, ecs, settings)

    result = schema_apply.run_migrate_task(
        ecs, settings.cluster_name, task_def_arn, subnets, sg_id)

    if result != "SUCCEEDED":
        _abort(f"migration task result: {result}")
    print("[3/4] schema migration SUCCEEDED")


def _step_redeploy(session, settings: Settings) -> None:
    """Force a rolling deployment so tasks pull the refreshed image."""
    print("[4/4] forcing new ECS deployment ...")
    ecs = session.client("ecs")
    ecs.update_service(
        cluster=settings.cluster_name,
        service=settings.service_name,
        forceNewDeployment=True,
    )
    print(f"[4/4] redeployed {settings.service_name} on {settings.cluster_name}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _discover_network(ec2, ecs, settings: Settings):
    """Retrieve the subnets and security group from the running service config.

    The ECS service already holds the awsvpc network configuration with the
    correct subnets and security groups -- reuse those for the migration task.
    """
    svc = ecs.describe_services(
        cluster=settings.cluster_name,
        services=[settings.service_name],
    )["services"][0]
    net_cfg = svc["networkConfiguration"]["awsvpcConfiguration"]
    subnets = net_cfg["subnets"]
    sg_id = net_cfg["securityGroups"][0]
    return subnets, sg_id


def _abort(msg: str) -> None:
    """Print error and exit."""
    print(f"[deploy_full] ABORT: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
