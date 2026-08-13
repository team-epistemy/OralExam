"""Provision the full M3 stack end-to-end in AWS.

Order: core (KMS/S3/SQS/IAM) → image build (ECR via CodeBuild) →
foundations (Aurora + Cognito + Secrets) → schema apply →
internal ALB (load balancer + target group + listener) → ECS Fargate service.

  PYTHONPATH=. python -m infra.provision --account 883353268066 --env dev
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib

import boto3

from epistemy_m3.config import Settings
from infra import (resources, ecs, network, foundations, imagebuild,
                   schema_apply, loadbalancer)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def provision(settings: Settings) -> dict:
    """Run every provisioning phase and return a summary of identifiers."""
    session = boto3.Session(region_name=settings.region)
    core = _core(session, settings)
    image = _build_image(session, settings, core)
    found = _foundations(session, settings, core)
    cluster = _ensure_cluster(session, settings)
    _apply_schema(session, settings, image, core, found, cluster)
    lb = _loadbalancer(session, settings, found)
    service = _compute(session, settings, image, core, found, cluster, lb)
    return {**core, "image": image, **found, "service": service,
            "alb_dns": lb["dns"], "target_group": lb["target_group"]}


def _core(session, settings: Settings) -> dict:
    """KMS key, S3 bucket, SQS queue, task + execution roles."""
    kms, s3, sqs, iam = (session.client(s) for s in ("kms", "s3", "sqs", "iam"))
    resources.ensure_service_linked_roles(iam)
    key_arn = resources.ensure_kms_key(kms, settings.kms_alias)
    resources.ensure_bucket(s3, settings.bucket, settings.region, key_arn)
    url = resources.ensure_queue(sqs, settings.queue_name)
    q_arn = resources.queue_arn(sqs, url)
    task_role = resources.ensure_task_role(iam, settings.task_role,
                                           settings.bucket, q_arn, key_arn)
    exec_role = resources.ensure_execution_role(iam, settings.exec_role)
    return {"key_arn": key_arn, "queue_url": url, "task_role": task_role,
            "exec_role": exec_role}


def _build_image(session, settings: Settings, core: dict) -> str:
    """Package source, build via CodeBuild, push to ECR; return image URI."""
    s3, iam, cb = (session.client(s) for s in ("s3", "iam", "codebuild"))
    ecr_uri = _ecr_uri(session, settings)
    key = f"build/{settings.env}/source.zip"
    imagebuild.upload_source(s3, settings.bucket, key, ROOT)
    role = imagebuild.ensure_build_role(iam, settings.build_role, settings.bucket)
    imagebuild.ensure_project(cb, settings.image_project, role,
                              ecr_uri, settings.bucket, key, settings.region)
    _await_build(cb, settings.image_project)
    return f"{ecr_uri}:latest"


def _ecr_uri(session, settings: Settings) -> str:
    """Ensure the ECR repository exists and return its URI."""
    ecr = session.client("ecr")
    try:
        repo = ecr.create_repository(repositoryName=settings.ecr_repo)["repository"]
    except ecr.exceptions.RepositoryAlreadyExistsException:
        repo = ecr.describe_repositories(
            repositoryNames=[settings.ecr_repo])["repositories"][0]
    return repo["repositoryUri"]


def _await_build(cb, project: str) -> None:
    """Run the image build and fail loudly if it does not succeed."""
    status = imagebuild.run_build(cb, project)
    if status != "SUCCEEDED":
        raise RuntimeError(f"image build {project} ended {status}")


def _foundations(session, settings: Settings, core: dict) -> dict:
    """Aurora Serverless v2, Cognito user pool, and the DB secret."""
    rds, sm, idp, ec2 = (session.client(s) for s in ("rds", "secretsmanager",
                                                     "cognito-idp", "ec2"))
    subnets, groups = network.default_network(ec2)
    network.allow_self_postgres(ec2, groups[0])
    secret = foundations.ensure_db_secret(sm, settings.db_secret_name,
                                          settings.db_name, settings.db_user)
    db = _database(rds, sm, settings, secret, subnets, groups[0])
    foundations.add_host_to_secret(sm, secret, db["endpoint"])
    pool = foundations.ensure_user_pool(idp, settings.user_pool_name)
    return {"db_secret_arn": secret, "db_endpoint": db["endpoint"], **pool,
            "subnets": subnets, "sg_id": groups[0]}


def _database(rds, sm, settings, secret, subnets, sg_id) -> dict:
    """Create the DB subnet group and RDS PostgreSQL instance, then wait."""
    foundations.ensure_db_subnet_group(rds, settings.db_subnet_group, subnets)
    foundations.ensure_postgres(rds, settings.db_cluster_id, sm, secret,
                                settings.db_subnet_group, sg_id)
    return _wait_for_db(rds, settings.db_cluster_id)


def _wait_for_db(rds, instance_id: str) -> dict:
    """Poll until the DB instance reports an endpoint and available status."""
    import time
    for _ in range(90):
        info = foundations._find_instance(rds, instance_id)
        if info and info["endpoint"] and info["status"] == "available":
            return info
        time.sleep(20)
    raise RuntimeError(f"RDS instance {instance_id} not available in time")


def _ensure_cluster(session, settings: Settings) -> str:
    """Create the ECS cluster and its log group up front."""
    ecs_c, logs = session.client("ecs"), session.client("logs")
    cluster = ecs.ensure_cluster(ecs_c, settings.cluster_name)
    ecs.ensure_log_group(logs, settings.log_group)
    return cluster


def _apply_schema(session, settings: Settings, image: str, core: dict,
                  found: dict, cluster: str) -> None:
    """Run schema.sql via a one-off Fargate migrate task in the cluster."""
    ecs_c = session.client("ecs")
    task_def = schema_apply.register_migrate_task(
        ecs_c, settings.migrate_family, image, core["task_role"],
        core["exec_role"], settings.region, settings.log_group,
        _task_env(settings, core, found))
    status = schema_apply.run_migrate_task(ecs_c, cluster, task_def,
                                           found["subnets"], found["sg_id"])
    if status != "SUCCEEDED":
        raise RuntimeError(f"schema migrate ended {status}")


def _loadbalancer(session, settings: Settings, found: dict) -> dict:
    """Internet-facing ALB, target group, and HTTP:80 listener.

    The ALB shares the default SG with the tasks, so it needs inbound :80 from
    the internet (for browsers) and inbound :8080 from the SG itself (to reach
    the container targets).
    """
    elb, ec2 = session.client("elbv2"), session.client("ec2")
    network.allow_self_port(ec2, found["sg_id"], loadbalancer.HTTP_PORT)
    network.allow_public_tcp(ec2, found["sg_id"], loadbalancer.LISTENER_PORT)
    vpc_id = network.vpc_id_for_subnet(ec2, found["subnets"][0])
    lb = loadbalancer.ensure_load_balancer(
        elb, settings.alb_name, found["subnets"], found["sg_id"])
    tg = loadbalancer.ensure_target_group(elb, settings.target_group_name, vpc_id)
    loadbalancer.ensure_listener(elb, lb["arn"], tg)
    loadbalancer.wait_active(elb, lb["arn"])
    return {"arn": lb["arn"], "dns": lb["dns"], "target_group": tg}


def _compute(session, settings: Settings, image: str, core: dict,
             found: dict, cluster: str, lb: dict) -> str:
    """Task definition and Fargate service on the existing cluster."""
    ecs_c = session.client("ecs")
    task_def = _register_task(ecs_c, settings, image, core, found,
                              settings.log_group)
    return ecs.ensure_service(ecs_c, cluster, settings.service_name, task_def,
                              found["subnets"], [found["sg_id"]],
                              target_group_arn=lb["target_group"])


def _register_task(ecs_c, settings, image, core, found, log_group) -> str:
    """Register the HTTP+worker task with runtime environment variables."""
    return ecs.register_task_def(
        ecs_c, settings.service_name, image, core["task_role"],
        core["exec_role"], settings.region, log_group,
        env=_task_env(settings, core, found))


def _task_env(settings: Settings, core: dict, found: dict) -> list:
    """Environment variables consumed by the container at runtime."""
    # Store the Anthropic key in Secrets Manager; the container fetches it at
    # runtime via the task role. The plaintext key never enters the task def.
    _ensure_anthropic_secret(settings)
    pairs = {"AWS_REGION": settings.region, "EPISTEMY_BUCKET": settings.bucket,
             "EPISTEMY_QUEUE_URL": core["queue_url"],
             "EPISTEMY_DB_SECRET_ARN": found["db_secret_arn"],
             "EPISTEMY_BEDROCK_REGION": settings.bedrock_region,
             "EPISTEMY_USE_BEDROCK": "1",
             "EPISTEMY_LLM_PROVIDER": settings.llm_provider,
             "EPISTEMY_ANTHROPIC_MODEL": settings.anthropic_model,
             "EPISTEMY_ANTHROPIC_SECRET": settings.anthropic_secret_name}
    return [{"name": k, "value": v} for k, v in pairs.items()]


def _ensure_anthropic_secret(settings: Settings) -> None:
    """Create/update the Anthropic key secret from the deploy host's env, if set."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return  # assume the secret was provisioned out-of-band
    sm = boto3.client("secretsmanager", region_name=settings.region)
    name = settings.anthropic_secret_name
    try:
        sm.create_secret(Name=name, SecretString=key)
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=name, SecretString=key)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision Epistemy M3 stack")
    parser.add_argument("--account", default="883353268066")
    parser.add_argument("--env", default="dev")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint: build settings from args and provision everything."""
    args = _parse_args()
    settings = Settings(account_id=args.account, env=args.env)
    print(json.dumps(provision(settings), indent=2, default=str))


if __name__ == "__main__":
    main()
