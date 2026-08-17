"""Idempotent boto3 creators for the M3 resources (KMS, S3, SQS, IAM)."""
from __future__ import annotations
from typing import Dict

from botocore.exceptions import ClientError

from infra import policies


def ensure_kms_key(kms, alias: str) -> str:
    """Create a rotating CMK and alias if the alias is not present."""
    existing = _find_alias_target(kms, alias)
    if existing:
        return existing
    key_id = kms.create_key(Description="Epistemy M3 materials",
                            KeyUsage="ENCRYPT_DECRYPT")["KeyMetadata"]["Arn"]
    kms.enable_key_rotation(KeyId=key_id.split("/")[-1])
    kms.create_alias(AliasName=alias, TargetKeyId=key_id.split("/")[-1])
    return key_id


def _find_alias_target(kms, alias: str):
    """Return the key ARN behind an alias, or None when absent."""
    for entry in kms.list_aliases().get("Aliases", []):
        if entry.get("AliasName") == alias and entry.get("TargetKeyId"):
            meta = kms.describe_key(KeyId=entry["TargetKeyId"])["KeyMetadata"]
            return meta["Arn"]
    return None


def ensure_bucket(s3, bucket: str, region: str, key_arn: str) -> None:
    """Create the materials bucket with SSE-KMS, versioning, and lockdown."""
    _create_bucket(s3, bucket, region)
    _block_public_access(s3, bucket)
    _enable_versioning(s3, bucket)
    _set_default_encryption(s3, bucket, key_arn)
    _set_cors(s3, bucket)
    s3.put_bucket_policy(Bucket=bucket, Policy=policies.bucket_policy(bucket))


def _set_cors(s3, bucket: str) -> None:
    """Allow browser PUT/GET from any origin for presigned demo uploads."""
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
        "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedOrigins": ["*"],
        "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000}]})


def _create_bucket(s3, bucket: str, region: str) -> None:
    """Create the bucket, tolerating an existing owned bucket."""
    try:
        s3.create_bucket(Bucket=bucket,
                         CreateBucketConfiguration={"LocationConstraint": region})
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",
                                                 "BucketAlreadyExists"):
            raise


def _block_public_access(s3, bucket: str) -> None:
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True})


def _enable_versioning(s3, bucket: str) -> None:
    s3.put_bucket_versioning(Bucket=bucket,
                             VersioningConfiguration={"Status": "Enabled"})


def _set_default_encryption(s3, bucket: str, key_arn: str) -> None:
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {
            "SSEAlgorithm": "aws:kms", "KMSMasterKeyID": key_arn}}]})


def ensure_queue(sqs, name: str) -> str:
    """Create the ingest queue if missing and return its URL."""
    try:
        return sqs.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError:
        return sqs.create_queue(QueueName=name,
                                Attributes={"VisibilityTimeout": "300"})["QueueUrl"]


def queue_arn(sqs, url: str) -> str:
    """Resolve a queue's ARN from its URL."""
    attrs = sqs.get_queue_attributes(QueueUrl=url,
                                     AttributeNames=["QueueArn"])
    return attrs["Attributes"]["QueueArn"]


def ensure_task_role(iam, name: str, bucket: str, q_arn: str, key_arn: str) -> str:
    """Create the prefix-scoped task role with an inline permission policy."""
    arn = _ensure_role(iam, name)
    iam.put_role_policy(RoleName=name, PolicyName="m3-task-policy",
                        PolicyDocument=policies.task_permission_policy(
                            bucket, q_arn, key_arn))
    return arn


def _ensure_role(iam, name: str) -> str:
    """Create the role with the ECS trust policy, tolerating existence."""
    try:
        resp = iam.create_role(RoleName=name,
                               AssumeRolePolicyDocument=policies.ecs_trust_policy())
        return resp["Role"]["Arn"]
    except ClientError:
        return iam.get_role(RoleName=name)["Role"]["Arn"]


def ensure_execution_role(iam, name: str) -> str:
    """Task execution role: pull from ECR and ship logs to CloudWatch."""
    arn = _ensure_role(iam, name)
    iam.attach_role_policy(RoleName=name, PolicyArn=(
        "arn:aws:iam::aws:policy/service-role/"
        "AmazonECSTaskExecutionRolePolicy"))
    return arn


def ensure_service_linked_roles(iam) -> None:
    """Create the service-linked roles a fresh account needs (idempotent).

    ECS Fargate RunTask, the ALB, and RDS each require their service-linked
    role; new accounts may not have them until first use.
    """
    for service in ("ecs.amazonaws.com", "elasticloadbalancing.amazonaws.com",
                    "rds.amazonaws.com"):
        try:
            iam.create_service_linked_role(AWSServiceName=service)
        except ClientError:
            pass  # already exists ("has been taken") or created concurrently
