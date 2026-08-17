"""Shared S2 foundations: RDS PostgreSQL instance and Cognito user pool."""
from __future__ import annotations
import json
import secrets

from botocore.exceptions import ClientError


def ensure_db_secret(sm, name: str, db_name: str, db_user: str) -> str:
    """Create a Secrets Manager secret holding generated DB credentials."""
    try:
        payload = json.dumps({"username": db_user,
                              "password": secrets.token_urlsafe(24),
                              "dbname": db_name})
        return sm.create_secret(Name=name, SecretString=payload)["ARN"]
    except ClientError:
        return sm.describe_secret(SecretId=name)["ARN"]


def add_host_to_secret(sm, secret_arn: str, host: str) -> None:
    """Write the resolved DB host/port into the DB secret JSON."""
    creds = _secret_value(sm, secret_arn)
    creds.update(host=host, port=5432)
    sm.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(creds))


def ensure_postgres(rds, instance_id: str, sm, secret_arn: str,
                    subnet_group: str, sg_id: str) -> dict:
    """Create a single-AZ RDS PostgreSQL instance if absent.

    Standard RDS (not Aurora) so it works on free-plan accounts with password
    auth, a custom database, and our VPC security group — what the app expects.
    """
    existing = _find_instance(rds, instance_id)
    if existing:
        return existing
    creds = _secret_value(sm, secret_arn)
    _create_postgres(rds, instance_id, creds, subnet_group, sg_id)
    return _find_instance(rds, instance_id)


def _create_postgres(rds, instance_id: str, creds: dict,
                     subnet_group: str, sg_id: str) -> None:
    """Provision the RDS PostgreSQL instance (db.t4g.micro, gp3, encrypted)."""
    rds.create_db_instance(
        DBInstanceIdentifier=instance_id, Engine="postgres",
        DBInstanceClass="db.t4g.micro", AllocatedStorage=20, StorageType="gp3",
        MasterUsername=creds["username"], MasterUserPassword=creds["password"],
        DBName=creds["dbname"], DBSubnetGroupName=subnet_group,
        VpcSecurityGroupIds=[sg_id], PubliclyAccessible=False,
        StorageEncrypted=True, BackupRetentionPeriod=1)


def _find_instance(rds, instance_id: str):
    """Return instance summary dict, or None when it does not exist."""
    try:
        i = rds.describe_db_instances(
            DBInstanceIdentifier=instance_id)["DBInstances"][0]
        endpoint = (i.get("Endpoint") or {}).get("Address")
        return {"endpoint": endpoint, "status": i["DBInstanceStatus"],
                "instance_id": instance_id}
    except ClientError:
        return None


def _secret_value(sm, secret_arn: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])


def ensure_user_pool(idp, name: str) -> dict:
    """Create a Cognito user pool + client if one by this name is absent."""
    pool = _find_user_pool(idp, name)
    if not pool:
        pool = idp.create_user_pool(PoolName=name,
                                    AutoVerifiedAttributes=["email"])["UserPool"]
    client = _ensure_pool_client(idp, pool["Id"])
    return {"user_pool_id": pool["Id"], "client_id": client}


def _find_user_pool(idp, name: str):
    """Locate an existing user pool by name."""
    for p in idp.list_user_pools(MaxResults=60).get("UserPools", []):
        if p["Name"] == name:
            return idp.describe_user_pool(UserPoolId=p["Id"])["UserPool"]
    return None


def _ensure_pool_client(idp, pool_id: str) -> str:
    """Return an existing app client id or create one."""
    clients = idp.list_user_pool_clients(UserPoolId=pool_id,
                                         MaxResults=60).get("UserPoolClients", [])
    if clients:
        return clients[0]["ClientId"]
    return idp.create_user_pool_client(
        UserPoolId=pool_id, ClientName="epistemy-web",
        GenerateSecret=False)["UserPoolClient"]["ClientId"]


def ensure_db_subnet_group(rds, name: str, subnets: list) -> str:
    """Create a DB subnet group spanning the supplied subnets."""
    try:
        rds.create_db_subnet_group(
            DBSubnetGroupName=name, DBSubnetGroupDescription="Epistemy M3",
            SubnetIds=subnets)
    except ClientError:
        pass
    return name
