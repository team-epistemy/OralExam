"""ECS Fargate cluster, task definition, and service creators for M3."""
from __future__ import annotations
from typing import Dict, List

from botocore.exceptions import ClientError


def ensure_cluster(ecs, name: str) -> str:
    """Create the ECS cluster if absent and return its ARN."""
    found = ecs.describe_clusters(clusters=[name]).get("clusters", [])
    active = [c for c in found if c.get("status") == "ACTIVE"]
    if active:
        return active[0]["clusterArn"]
    return ecs.create_cluster(clusterName=name)["cluster"]["clusterArn"]


def ensure_log_group(logs, name: str) -> None:
    """Create the CloudWatch log group, tolerating prior existence."""
    try:
        logs.create_log_group(logGroupName=name)
    except ClientError:
        pass


def register_task_def(ecs, family: str, image: str, role_arn: str,
                      exec_arn: str, region: str, log_group: str,
                      env: List[Dict] = None) -> str:
    """Register a Fargate task def running HTTP and worker containers."""
    resp = ecs.register_task_definition(
        family=family, networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"], cpu="512", memory="1024",
        executionRoleArn=exec_arn, taskRoleArn=role_arn,
        containerDefinitions=_containers(image, region, log_group, env or []))
    return resp["taskDefinition"]["taskDefinitionArn"]


def _containers(image: str, region: str, log_group: str,
                env: List[Dict]) -> List[Dict]:
    """HTTP process (port 8080) plus the SQS worker process."""
    return [
        _container("http", image, region, log_group, ["serve"], env, port=8080),
        _container("worker", image, region, log_group, ["worker"], env),
    ]


def _container(name: str, image: str, region: str, log_group: str,
               command: List[str], env: List[Dict], port: int = None) -> Dict:
    """Build one container definition with env and awslogs configured."""
    spec = {"name": name, "image": image, "essential": True,
            "command": command, "environment": env,
            "logConfiguration": _log_config(region, log_group, name)}
    if port:
        spec["portMappings"] = [{"containerPort": port, "protocol": "tcp"}]
    return spec


def _log_config(region: str, log_group: str, stream: str) -> Dict:
    return {"logDriver": "awslogs", "options": {
        "awslogs-group": log_group, "awslogs-region": region,
        "awslogs-stream-prefix": stream}}


def ensure_service(ecs, cluster: str, service: str, task_def: str,
                   subnets: List[str], security_groups: List[str],
                   target_group_arn: str = None) -> str:
    """Create or update a Fargate service running one task.

    When `target_group_arn` is supplied the service registers the `http`
    container (port 8080) behind the ALB target group.
    """
    load_balancers = _load_balancers(target_group_arn)
    if _service_active(ecs, cluster, service):
        ecs.update_service(cluster=cluster, service=service, taskDefinition=task_def,
                           networkConfiguration=_net(subnets, security_groups))
        return service
    kwargs = dict(cluster=cluster, serviceName=service, taskDefinition=task_def,
                  desiredCount=1, launchType="FARGATE",
                  networkConfiguration=_net(subnets, security_groups))
    if load_balancers:
        kwargs["loadBalancers"] = load_balancers
        kwargs["healthCheckGracePeriodSeconds"] = 120
    ecs.create_service(**kwargs)
    return service


def _load_balancers(target_group_arn: str) -> List[Dict]:
    """Build the loadBalancers block binding the http container to the TG."""
    if not target_group_arn:
        return []
    return [{"targetGroupArn": target_group_arn,
             "containerName": "http", "containerPort": 8080}]


def _service_active(ecs, cluster: str, service: str) -> bool:
    """True when a non-inactive service already exists."""
    found = ecs.describe_services(cluster=cluster,
                                  services=[service]).get("services", [])
    return any(s.get("status") != "INACTIVE" for s in found)


def _net(subnets: List[str], security_groups: List[str]) -> Dict:
    """Public IP so tasks in the default VPC can reach ECR and AWS APIs."""
    return {"awsvpcConfiguration": {
        "subnets": subnets, "securityGroups": security_groups,
        "assignPublicIp": "ENABLED"}}
