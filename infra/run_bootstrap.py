"""Run the one-off `bootstrap` Fargate task: seed platform org + platform_admin.

  AWS_PROFILE=personal EPISTEMY_ACCOUNT=<id> EPISTEMY_ADMIN_EMAIL=<email> \
    PYTHONPATH=. python -m infra.run_bootstrap

Reuses the running service's network config and the same DB env the migrate
task uses. Cognito config is read from Secrets Manager by the task itself.
"""
from __future__ import annotations
import os
import time

import boto3

from backend.config import Settings
from infra.deploy_full import _discover_network


def main() -> None:
    s = Settings()
    email = os.environ["EPISTEMY_ADMIN_EMAIL"]
    org_name = os.environ.get("EPISTEMY_ORG_NAME", "epistemy")
    sess = boto3.Session(region_name=s.region)
    ecs, ec2, sm = sess.client("ecs"), sess.client("ec2"), sess.client("secretsmanager")

    db_secret_arn = s.db_secret_arn or sm.describe_secret(
        SecretId=s.db_secret_name)["ARN"]
    env = [
        {"name": "EPISTEMY_DB_SECRET_ARN", "value": db_secret_arn},
        {"name": "EPISTEMY_DB_NAME", "value": s.db_name},
        {"name": "AWS_REGION", "value": s.region},
        {"name": "EPISTEMY_ACCOUNT", "value": s.account_id},
        {"name": "EPISTEMY_ADMIN_EMAIL", "value": email},
        {"name": "EPISTEMY_ORG_NAME", "value": org_name},
        {"name": "PYTHONUNBUFFERED", "value": "1"},
    ]
    if os.environ.get("EPISTEMY_ADMIN_SUB"):
        env.append({"name": "EPISTEMY_ADMIN_SUB",
                    "value": os.environ["EPISTEMY_ADMIN_SUB"]})

    family = f"epistemy-m3-bootstrap-{s.env}"
    task_def = ecs.register_task_definition(
        family=family, networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
        executionRoleArn=s.role_arn(s.exec_role),
        taskRoleArn=s.role_arn(s.task_role),
        containerDefinitions=[{
            "name": "bootstrap", "image": s.ecr_image(), "essential": True,
            "command": ["bootstrap"], "environment": env,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": s.log_group, "awslogs-region": s.region,
                "awslogs-stream-prefix": "bootstrap"}}}],
    )["taskDefinition"]["taskDefinitionArn"]

    subnets, sg = _discover_network(ec2, ecs, s)
    arn = ecs.run_task(cluster=s.cluster_name, taskDefinition=task_def,
                       launchType="FARGATE", count=1,
                       networkConfiguration={"awsvpcConfiguration": {
                           "subnets": subnets, "securityGroups": [sg],
                           "assignPublicIp": "ENABLED"}})["tasks"][0]["taskArn"]
    print(f"bootstrap task started: admin={email} org={org_name}")

    for _ in range(60):
        task = ecs.describe_tasks(cluster=s.cluster_name, tasks=[arn])["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            code = task["containers"][0].get("exitCode")
            print(f"bootstrap {'SUCCEEDED' if code == 0 else 'FAILED'} "
                  f"exitCode={code} reason={task.get('stoppedReason')}")
            return
        time.sleep(10)
    raise RuntimeError("bootstrap task did not stop in time")


if __name__ == "__main__":
    main()
