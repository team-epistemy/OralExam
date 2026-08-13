"""Put CloudFront (HTTPS) in front of the ALB and lock the ALB to CloudFront.

  PYTHONPATH=. python -m infra.expose_cloudfront
"""
from __future__ import annotations

import boto3

from backend.config import Settings
from infra import cloudfront

_CF_PREFIX = "pl-82a045eb"  # com.amazonaws.global.cloudfront.origin-facing


def main() -> None:
    """Create the distribution, restrict the ALB SG, print the HTTPS URL."""
    settings = Settings()
    session = boto3.Session(region_name=settings.region)
    alb_dns, alb_sg = _alb_facts(session, settings)
    dist = cloudfront.ensure_distribution(
        boto3.client("cloudfront"), alb_dns, "epistemy-m3 demo")
    _lock_alb_to_cloudfront(session, alb_sg)
    print(f"\nDistribution {dist['id']} deploying (~5-10 min).")
    print(f"HTTPS demo URL:  https://{dist['domain']}")


def _alb_facts(session, settings: Settings) -> tuple:
    """Return the internal ALB DNS name and its security group id."""
    elb = session.client("elbv2")
    lb = elb.describe_load_balancers(Names=[settings.alb_name])["LoadBalancers"][0]
    return lb["DNSName"], lb["SecurityGroups"][0]


def _lock_alb_to_cloudfront(session, alb_sg: str) -> None:
    """Allow port 80 from the CloudFront prefix list on the ALB SG."""
    ec2 = session.client("ec2")
    try:
        ec2.authorize_security_group_ingress(GroupId=alb_sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
            "PrefixListIds": [{"PrefixListId": _CF_PREFIX,
                               "Description": "CloudFront origin-facing"}]}])
        print("ALB now accepts traffic from CloudFront")
    except Exception as exc:
        print("ingress:", str(exc)[:100])


if __name__ == "__main__":
    main()
