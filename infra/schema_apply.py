"""Apply schema.sql by running a one-off Fargate task with the `migrate` command."""
from __future__ import annotations
import time
from typing import List, Dict


def register_migrate_task(ecs, family: str, image: str, role_arn: str,
                          exec_arn: str, region: str, log_group: str,
                          env: List[Dict]) -> str:
    """Register a single-container task definition that runs `migrate`."""
    resp = ecs.register_task_definition(
        family=family, networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
        executionRoleArn=exec_arn, taskRoleArn=role_arn,
        containerDefinitions=[_migrate_container(image, region, log_group, env)])
    return resp["taskDefinition"]["taskDefinitionArn"]


def _migrate_container(image: str, region: str, log_group: str,
                       env: List[Dict]) -> Dict:
    """One-shot migrate container with awslogs configured."""
    return {"name": "migrate", "image": image, "essential": True,
            "command": ["migrate"], "environment": env,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": log_group, "awslogs-region": region,
                "awslogs-stream-prefix": "migrate"}}}


def run_migrate_task(ecs, cluster: str, task_def: str, subnets: List[str],
                     sg_id: str) -> str:
    """Run the migrate task with a public IP and wait for it to stop."""
    arn = ecs.run_task(cluster=cluster, taskDefinition=task_def,
                       launchType="FARGATE", count=1,
                       networkConfiguration=_net(subnets, sg_id)
                       )["tasks"][0]["taskArn"]
    return _wait_stopped(ecs, cluster, arn)


def _net(subnets: List[str], sg_id: str) -> Dict:
    """Public-IP awsvpc config so the task can pull image and reach Secrets."""
    return {"awsvpcConfiguration": {
        "subnets": subnets, "securityGroups": [sg_id],
        "assignPublicIp": "ENABLED"}}


def _wait_stopped(ecs, cluster: str, task_arn: str) -> str:
    """Poll until the task stops; return its container exit summary."""
    for _ in range(60):
        task = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            return _exit_summary(task)
        time.sleep(10)
    raise RuntimeError("migrate task did not stop in time")


def _exit_summary(task: Dict) -> str:
    """Return 'SUCCEEDED' on exit code 0, else a failure description."""
    container = task["containers"][0]
    code = container.get("exitCode")
    if code == 0:
        return "SUCCEEDED"
    return f"FAILED exitCode={code} reason={task.get('stoppedReason')}"
