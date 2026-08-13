"""Rebuild the image and run the `smoke` command as a one-off Fargate task.

  PYTHONPATH=. python -m infra.run_smoke
"""
from __future__ import annotations
import pathlib

import boto3

from backend.config import Settings
from infra import imagebuild, schema_apply


ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    """Push latest image, then run the smoke task and stream its result."""
    settings = Settings()
    session = boto3.Session(region_name=settings.region)
    _rebuild_image(session, settings)
    status = _run_task(session, settings)
    print(f"\nsmoke task ended: {status}")
    print(f"logs: aws logs tail {settings.log_group} "
          f"--log-stream-name-prefix smoke --region {settings.region}")


def _rebuild_image(session, settings: Settings) -> None:
    """Repackage source and rebuild so the image includes smoke_test."""
    s3, cb = session.client("s3"), session.client("codebuild")
    key = f"build/{settings.env}/source.zip"
    imagebuild.upload_source(s3, settings.bucket, key, ROOT)
    status = imagebuild.run_build(cb, settings.image_project)
    if status != "SUCCEEDED":
        raise RuntimeError(f"image rebuild ended {status}")


def _run_task(session, settings: Settings) -> str:
    """Register a smoke task def and run it inside the VPC."""
    ecs = session.client("ecs")
    found = _network(session)
    task_def = _register(ecs, settings, found)
    return schema_apply.run_migrate_task(ecs, settings.cluster_name, task_def,
                                         found["subnets"], found["sg_id"])


def _register(ecs, settings: Settings, found: dict) -> str:
    """Register a single-container task running the `smoke` command."""
    image = settings.ecr_image()
    return ecs.register_task_definition(
        family=settings.smoke_family, networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
        executionRoleArn=found["exec"], taskRoleArn=found["task"],
        containerDefinitions=[_container(image, settings, found)]
    )["taskDefinition"]["taskDefinitionArn"]


def _container(image: str, settings: Settings, found: dict) -> dict:
    """Smoke container with env + awslogs configured."""
    return {"name": "smoke", "image": image, "essential": True,
            "command": ["smoke"], "environment": found["env"],
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": settings.log_group,
                "awslogs-region": settings.region,
                "awslogs-stream-prefix": "smoke"}}}


def _network(session) -> dict:
    """Resolve subnets/SG/roles/env for the smoke task from live resources."""
    s = Settings()
    sm, sqs = session.client("secretsmanager"), session.client("sqs")
    ec2 = session.client("ec2")
    from infra import network
    subnets, groups = network.default_network(ec2)
    return _network_dict(s, sm, sqs, subnets, groups[0])


def _network_dict(s: Settings, sm, sqs, subnets, sg_id) -> dict:
    """Build the env + identifiers the smoke task needs."""
    secret = sm.describe_secret(SecretId=s.db_secret_name)["ARN"]
    url = sqs.get_queue_url(QueueName=s.queue_name)["QueueUrl"]
    env = {"AWS_REGION": s.region, "EPISTEMY_BUCKET": s.bucket,
           "EPISTEMY_QUEUE_URL": url, "EPISTEMY_DB_SECRET_ARN": secret,
           "EPISTEMY_USE_BEDROCK": "0"}
    return {"subnets": subnets, "sg_id": sg_id,
            "task": s.role_arn(s.task_role), "exec": s.role_arn(s.exec_role),
            "env": [{"name": k, "value": v} for k, v in env.items()]}


if __name__ == "__main__":
    main()
