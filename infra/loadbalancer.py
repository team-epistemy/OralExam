"""Internet-facing ALB + target group + listener for the M3 HTTP container.

The ALB defaults to internet-facing and serves HTTP:80 directly. (In
Amazon-internal accounts the Epoxy control deletes HTTP:80 listeners on
internet-facing ALBs, which forces an internal ALB + CloudFront VPC origin —
not a concern in a standalone account.) All creators here are idempotent.
"""
from __future__ import annotations
import time
from typing import Dict, List

from botocore.exceptions import ClientError

HTTP_PORT = 8080
LISTENER_PORT = 80
HEALTH_PATH = "/health"


def ensure_load_balancer(elb, name: str, subnets: List[str],
                         sg_id: str, scheme: str = "internet-facing") -> Dict:
    """Create the ALB if absent; return its ARN and DNS name.

    Defaults to internet-facing. (The internal+CloudFront design only matters
    in Amazon-internal accounts subject to the Epoxy listener-deletion control;
    a standalone account can serve directly from a public ALB.)
    """
    existing = _find_lb(elb, name)
    if existing:
        return existing
    lb = elb.create_load_balancer(
        Name=name, Subnets=subnets, SecurityGroups=[sg_id],
        Scheme=scheme, Type="application", IpAddressType="ipv4",
    )["LoadBalancers"][0]
    return {"arn": lb["LoadBalancerArn"], "dns": lb["DNSName"]}


def _find_lb(elb, name: str):
    """Return {arn, dns} for an existing ALB by name, else None."""
    try:
        lb = elb.describe_load_balancers(Names=[name])["LoadBalancers"][0]
        return {"arn": lb["LoadBalancerArn"], "dns": lb["DNSName"]}
    except ClientError:
        return None


def ensure_target_group(elb, name: str, vpc_id: str) -> str:
    """Create an ip-target group on the container port; return its ARN."""
    existing = _find_tg(elb, name)
    if existing:
        return existing
    tg = elb.create_target_group(
        Name=name, Protocol="HTTP", Port=HTTP_PORT, VpcId=vpc_id,
        TargetType="ip", HealthCheckProtocol="HTTP", HealthCheckPath=HEALTH_PATH,
        HealthCheckIntervalSeconds=30, HealthyThresholdCount=2,
        UnhealthyThresholdCount=3, Matcher={"HttpCode": "200"},
    )["TargetGroups"][0]
    return tg["TargetGroupArn"]


def _find_tg(elb, name: str):
    """Return an existing target group ARN by name, else None."""
    try:
        tg = elb.describe_target_groups(Names=[name])["TargetGroups"][0]
        return tg["TargetGroupArn"]
    except ClientError:
        return None


def ensure_listener(elb, lb_arn: str, tg_arn: str) -> str:
    """Create the HTTP:80 listener forwarding to the target group."""
    for lst in elb.describe_listeners(LoadBalancerArn=lb_arn).get("Listeners", []):
        if lst["Port"] == LISTENER_PORT:
            return lst["ListenerArn"]
    lst = elb.create_listener(
        LoadBalancerArn=lb_arn, Protocol="HTTP", Port=LISTENER_PORT,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
    )["Listeners"][0]
    return lst["ListenerArn"]


def wait_active(elb, lb_arn: str) -> None:
    """Poll until the load balancer reports the active state."""
    for _ in range(60):
        lb = elb.describe_load_balancers(
            LoadBalancerArns=[lb_arn])["LoadBalancers"][0]
        if lb["State"]["Code"] == "active":
            return
        time.sleep(10)
    raise RuntimeError(f"load balancer {lb_arn} not active in time")
