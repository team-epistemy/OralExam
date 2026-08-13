"""Build and push the container image via CodeBuild (no local Docker needed)."""
from __future__ import annotations
import io
import time
import zipfile
import pathlib
from typing import List

from botocore.exceptions import ClientError

from infra import policies

_BUILDSPEC = """version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ECR_URI
  build:
    commands:
      - docker build -t $ECR_URI:latest .
  post_build:
    commands:
      - docker push $ECR_URI:latest
"""

_SOURCE_FILES = ["Dockerfile", "requirements-runtime.txt", "buildspec.yml",
                 "backend"]


def upload_source(s3, bucket: str, key: str, root: pathlib.Path) -> None:
    """Zip the build context and upload it to S3 for CodeBuild."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("buildspec.yml", _BUILDSPEC)
        _add_paths(zf, root)
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())


def _add_paths(zf: zipfile.ZipFile, root: pathlib.Path) -> None:
    """Add Dockerfile, requirements, and the full package tree to the zip."""
    for name in ["Dockerfile", "requirements-runtime.txt"]:
        zf.write(root / name, name)
    for path in (root / "backend").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            zf.write(path, str(path.relative_to(root)))


def ensure_build_role(iam, name: str, bucket: str) -> str:
    """Create the CodeBuild service role with ECR + S3 + logs permissions."""
    arn = _ensure_role(iam, name)
    iam.put_role_policy(RoleName=name, PolicyName="m3-build-policy",
                        PolicyDocument=policies.codebuild_policy(bucket))
    return arn


def _ensure_role(iam, name: str) -> str:
    """Create the role with the CodeBuild trust policy if absent."""
    try:
        resp = iam.create_role(RoleName=name,
                               AssumeRolePolicyDocument=policies.codebuild_trust())
        return resp["Role"]["Arn"]
    except ClientError:
        return iam.get_role(RoleName=name)["Role"]["Arn"]


def ensure_project(cb, name: str, role_arn: str, ecr_uri: str,
                   bucket: str, key: str, region: str) -> None:
    """Create/update a privileged CodeBuild project sourced from S3."""
    spec = _project_spec(name, role_arn, ecr_uri, bucket, key, region)
    if _project_exists(cb, name):
        cb.update_project(**spec)
    else:
        _create_with_retry(cb, spec)


def _project_exists(cb, name: str) -> bool:
    """True when a CodeBuild project with this name is present."""
    found = cb.batch_get_projects(names=[name]).get("projects", [])
    return bool(found)


def _create_with_retry(cb, spec: dict) -> None:
    """Create the project, retrying while the new IAM role propagates.

    A freshly created service role is not immediately assumable by CodeBuild;
    IAM is eventually consistent, so retry on the assume-role races.
    """
    import time
    for attempt in range(12):
        try:
            cb.create_project(**spec)
            return
        except ClientError as exc:
            if not _is_role_propagation(exc) or attempt == 11:
                raise
            time.sleep(10)


def _is_role_propagation(exc: "ClientError") -> bool:
    """True when the error is the transient new-role-not-assumable-yet race."""
    msg = str(exc)
    return ("not be assumed" in msg
            or "sts:AssumeRole" in msg
            or "service role" in msg)


def _project_spec(name, role_arn, ecr_uri, bucket, key, region) -> dict:
    """Assemble the CodeBuild project definition."""
    return {"name": name, "serviceRole": role_arn,
            "source": {"type": "S3", "location": f"{bucket}/{key}"},
            "artifacts": {"type": "NO_ARTIFACTS"},
            "environment": _build_env(ecr_uri, region)}


def _build_env(ecr_uri: str, region: str) -> dict:
    """Privileged Linux container env with ECR_URI and region vars."""
    return {"type": "LINUX_CONTAINER", "computeType": "BUILD_GENERAL1_SMALL",
            "image": "aws/codebuild/amazonlinux2-x86_64-standard:5.0",
            "privilegedMode": True,
            "environmentVariables": [
                {"name": "ECR_URI", "value": ecr_uri},
                {"name": "AWS_DEFAULT_REGION", "value": region}]}


def run_build(cb, project: str) -> str:
    """Start a build and block until it completes; return the final status."""
    build_id = cb.start_build(projectName=project)["build"]["id"]
    while True:
        build = cb.batch_get_builds(ids=[build_id])["builds"][0]
        if build["buildStatus"] != "IN_PROGRESS":
            return build["buildStatus"]
        time.sleep(10)
