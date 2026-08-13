"""IAM and S3 policy documents for the M3 stack."""
from __future__ import annotations
import json
from typing import Dict


def bucket_policy(bucket: str) -> str:
    """Deny put/get unless the object key matches the caller's tenant prefix."""
    arn = f"arn:aws:s3:::{bucket}"
    tenant = f"{arn}/${{aws:PrincipalTag/org_id}}/${{aws:PrincipalTag/course_id}}/*"
    statements = [
        _deny_insecure_transport(arn),
        _deny_cross_tenant(arn, tenant),
    ]
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def _deny_insecure_transport(arn: str) -> Dict:
    """Reject any non-TLS request to the bucket."""
    return {"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*",
            "Action": "s3:*", "Resource": [arn, f"{arn}/*"],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}}}


def _deny_cross_tenant(arn: str, tenant: str) -> Dict:
    """Deny object access outside the principal's prefix, only for tagged sessions."""
    return {"Sid": "DenyCrossTenant", "Effect": "Deny", "Principal": "*",
            "Action": ["s3:GetObject", "s3:PutObject"],
            "NotResource": tenant,
            "Condition": {"Null": {"aws:PrincipalTag/org_id": "false"}}}


def ecs_trust_policy() -> str:
    """Allow ECS tasks to assume the role."""
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
        "Action": "sts:AssumeRole"}]})


def task_permission_policy(bucket: str, queue_arn: str, key_arn: str) -> str:
    """Least-privilege permissions for the HTTP/Worker task role."""
    statements = [
        _s3_statement(bucket),
        _sqs_statement(queue_arn),
        _kms_statement(key_arn),
        _bedrock_statement(),
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
         "Resource": "*"},
    ]
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def _s3_statement(bucket: str) -> Dict:
    return {"Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]}


def _sqs_statement(queue_arn: str) -> Dict:
    return {"Effect": "Allow",
            "Action": ["sqs:SendMessage", "sqs:ReceiveMessage",
                       "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
            "Resource": queue_arn}


def _kms_statement(key_arn: str) -> Dict:
    return {"Effect": "Allow",
            "Action": ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"],
            "Resource": key_arn}


def _bedrock_statement() -> Dict:
    return {"Effect": "Allow", "Action": ["bedrock:InvokeModel"],
            "Resource": "*"}


def codebuild_trust() -> str:
    """Allow CodeBuild to assume the build role."""
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "codebuild.amazonaws.com"},
        "Action": "sts:AssumeRole"}]})


def codebuild_policy(bucket: str) -> str:
    """ECR push, S3 source, logs, Secrets, KMS, and VPC ENI for the build role."""
    statements = [_ecr_statement(), _build_s3_statement(bucket),
                  _logs_statement(), _secrets_statement(), _eni_statement(),
                  _build_kms_statement()]
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def _build_kms_statement() -> Dict:
    """Decrypt the KMS-encrypted S3 source and data keys."""
    return {"Effect": "Allow", "Resource": "*",
            "Action": ["kms:Decrypt", "kms:GenerateDataKey"]}


def _build_s3_statement(bucket: str) -> Dict:
    return {"Effect": "Allow", "Action": ["s3:GetObject"],
            "Resource": f"arn:aws:s3:::{bucket}/*"}


def _logs_statement() -> Dict:
    return {"Effect": "Allow", "Resource": "*", "Action": [
        "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]}


def _secrets_statement() -> Dict:
    return {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
            "Resource": "*"}


def _eni_statement() -> Dict:
    """VPC networking permissions CodeBuild needs to run inside a VPC."""
    return {"Effect": "Allow", "Resource": "*", "Action": [
        "ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface", "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups", "ec2:DescribeVpcs",
        "ec2:CreateNetworkInterfacePermission"]}


def _ecr_statement() -> Dict:
    """Permissions to authenticate and push images to ECR."""
    return {"Effect": "Allow", "Resource": "*", "Action": [
        "ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage"]}
