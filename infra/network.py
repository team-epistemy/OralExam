"""Minimal networking lookup: reuse the account's default VPC for the pilot."""
from __future__ import annotations
from typing import List, Tuple


def default_network(ec2) -> Tuple[List[str], List[str]]:
    """Return (subnet_ids, [default_sg_id]) for the default VPC."""
    vpc_id = _default_vpc_id(ec2)
    subnets = _subnets_for_vpc(ec2, vpc_id)
    sg = _default_sg(ec2, vpc_id)
    return subnets, [sg]


def _default_vpc_id(ec2) -> str:
    """Find the account's default VPC id in this region."""
    resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        raise RuntimeError("no default VPC; supply subnets explicitly")
    return vpcs[0]["VpcId"]


def _subnets_for_vpc(ec2, vpc_id: str) -> List[str]:
    """List all subnet ids in the given VPC."""
    resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    return [s["SubnetId"] for s in resp.get("Subnets", [])]


def _default_sg(ec2, vpc_id: str) -> str:
    """Return the default security group id for the VPC."""
    resp = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": "group-name", "Values": ["default"]}])
    return resp["SecurityGroups"][0]["GroupId"]


def allow_self_postgres(ec2, sg_id: str) -> None:
    """Permit Postgres (5432) traffic within the security group itself."""
    from botocore.exceptions import ClientError
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432,
                "UserIdGroupPairs": [{"GroupId": sg_id}]}])
    except ClientError:
        pass


def allow_self_port(ec2, sg_id: str, port: int) -> None:
    """Permit TCP `port` traffic within the security group itself.

    Used so the ALB (which shares the default SG with the tasks) can reach the
    container port, and later so the CloudFront VPC-origin ENIs can reach :80.
    """
    from botocore.exceptions import ClientError
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                "UserIdGroupPairs": [{"GroupId": sg_id}]}])
    except ClientError:
        pass


def vpc_id_for_subnet(ec2, subnet_id: str) -> str:
    """Resolve the VPC id that owns a given subnet."""
    resp = ec2.describe_subnets(SubnetIds=[subnet_id])
    return resp["Subnets"][0]["VpcId"]


def allow_public_tcp(ec2, sg_id: str, port: int) -> None:
    """Open `port` to the internet (0.0.0.0/0) on the security group.

    Used for the internet-facing ALB listener so browsers can reach :80.
    """
    from botocore.exceptions import ClientError
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                "IpRanges": [{"CidrIp": "0.0.0.0/0",
                              "Description": "public ALB listener"}]}])
    except ClientError:
        pass
