"""Central settings for M3. Values come from env with safe dev defaults."""
from __future__ import annotations
import os
from dataclasses import dataclass

from epistemy_m3.constants import LLM_MODEL_ID, EMBED_MODEL_ID


def _region_slug(region: str) -> str:
    """Abbreviate a region like 'us-west-2' to 'usw2' for resource names."""
    parts = region.split("-")
    if len(parts) == 3:
        return f"{parts[0]}{parts[1][0]}{parts[2]}"
    return region.replace("-", "")


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration for the M3 layer."""

    account_id: str = os.getenv("EPISTEMY_ACCOUNT", "")
    region: str = os.getenv("AWS_REGION", "us-west-2")
    env: str = os.getenv("EPISTEMY_ENV", "dev")
    # Empty default means "derive from account/region/env" in __post_init__ so
    # the globally-unique S3 name follows the account, not a hardcoded id.
    bucket: str = os.getenv("EPISTEMY_BUCKET", "")
    queue_name: str = os.getenv("EPISTEMY_QUEUE", "epistemy-ingest-dev")
    kms_alias: str = os.getenv("EPISTEMY_KMS_ALIAS", "alias/epistemy-materials-dev")
    llm_model: str = os.getenv("EPISTEMY_LLM_MODEL", LLM_MODEL_ID)
    embed_model: str = os.getenv("EPISTEMY_EMBED_MODEL", EMBED_MODEL_ID)
    embed_dims: int = int(os.getenv("EPISTEMY_EMBED_DIMS", "1024"))
    bedrock_region: str = os.getenv("EPISTEMY_BEDROCK_REGION", "us-west-2")
    presign_ttl: int = int(os.getenv("EPISTEMY_PRESIGN_TTL", "300"))

    queue_url: str = os.getenv("EPISTEMY_QUEUE_URL", "")
    db_secret_arn: str = os.getenv("EPISTEMY_DB_SECRET_ARN", "")
    db_name: str = os.getenv("EPISTEMY_DB_NAME", "epistemy")
    db_user: str = os.getenv("EPISTEMY_DB_USER", "epistemy_admin")
    use_bedrock: bool = os.getenv("EPISTEMY_USE_BEDROCK", "0") == "1"
    ecr_repo: str = os.getenv("EPISTEMY_ECR_REPO", "epistemy-m3")

    def __post_init__(self) -> None:
        # S3 names are globally unique; encode account+region to avoid collisions
        if not self.bucket:
            name = (f"epistemy-materials-{self.env}-"
                    f"{_region_slug(self.region)}-{self.account_id}")
            # frozen=True requires object.__setattr__ to mutate during init
            object.__setattr__(self, "bucket", name)

    @property
    def cluster_name(self) -> str:
        return f"epistemy-{self.env}"

    @property
    def service_name(self) -> str:
        return f"epistemy-m3-{self.env}"

    @property
    def db_cluster_id(self) -> str:
        """Aurora cluster identifier; same value used to create and resolve it."""
        return "epistemy-process-db"

    @property
    def db_secret_name(self) -> str:
        """Secrets Manager name holding the DB credentials."""
        return f"epistemy/db-{self.env}"

    @property
    def log_group(self) -> str:
        return f"/epistemy/m3/{self.env}"

    @property
    def task_role(self) -> str:
        return f"epistemy-m3-task-{self.env}"

    @property
    def exec_role(self) -> str:
        return f"epistemy-m3-exec-{self.env}"

    @property
    def build_role(self) -> str:
        return f"epistemy-m3-build-{self.env}"

    @property
    def image_project(self) -> str:
        return f"epistemy-m3-image-{self.env}"

    @property
    def migrate_family(self) -> str:
        return f"epistemy-m3-migrate-{self.env}"

    @property
    def smoke_family(self) -> str:
        return f"epistemy-m3-smoke-{self.env}"

    @property
    def user_pool_name(self) -> str:
        return f"epistemy-{self.env}"

    @property
    def db_subnet_group(self) -> str:
        return f"epistemy-m3-{self.env}"

    @property
    def alb_name(self) -> str:
        return "epistemy-m3-int"

    @property
    def target_group_name(self) -> str:
        return "epistemy-m3-int-tg"

    def role_arn(self, name: str) -> str:
        """Build the IAM role ARN for a role this stack owns."""
        return f"arn:aws:iam::{self.account_id}:role/{name}"

    def ecr_image(self) -> str:
        """Latest ECR image URI for the service."""
        return (f"{self.account_id}.dkr.ecr.{self.region}"
                f".amazonaws.com/{self.ecr_repo}:latest")


def load_settings() -> Settings:
    """Build a Settings instance from the current environment.

    Fails fast if required configuration (account_id) is missing, rather than
    silently proceeding with an empty value that would cause cryptic failures
    downstream (e.g. malformed ARNs, S3 bucket names).
    """
    s = Settings()
    if not s.account_id:
        raise RuntimeError(
            "EPISTEMY_ACCOUNT environment variable is required but not set. "
            "Set it to the AWS account ID hosting this deployment."
        )
    return s