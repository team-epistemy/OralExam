"""Builds real AWS/DB-backed components from Settings (used by both processes)."""
from __future__ import annotations
import json

import boto3

from backend.config import Settings
from backend.db.postgres import PostgresRepository
from backend.storage.s3_client import BotoS3Storage
from backend.async_jobs.queue import SqsQueue
from backend.async_jobs.pipeline import IngestPipeline
from backend.async_jobs.worker import IngestWorker
from backend.embedding.embedder import BedrockEmbedder
from backend.embedding.fake import FakeEmbedder
from backend.api.service import MaterialsApi


def db_connection(settings: Settings):
    """Open a psycopg2 connection using credentials from Secrets Manager."""
    import psycopg2
    creds = _db_credentials(settings)
    return psycopg2.connect(
        host=creds["host"], port=creds.get("port", 5432),
        dbname=creds.get("dbname", settings.db_name), user=creds["username"],
        password=creds["password"], connect_timeout=10)


def build_pool(settings: Settings):
    """Create a ThreadedConnectionPool for the HTTP server.

    Uses the non-owner runtime role so RLS is enforced; app.org_id is reset on
    return to the pool so a tenant binding never leaks to the next request.
    """
    from psycopg2.pool import ThreadedConnectionPool
    creds = _db_credentials(settings, app_role=True)
    return ThreadedConnectionPool(
        minconn=2, maxconn=10,
        host=creds["host"], port=creds.get("port", 5432),
        dbname=creds.get("dbname", settings.db_name),
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
    )


def _db_credentials(settings: Settings, app_role: bool = False) -> dict:
    """Fetch DB creds: the runtime app role when app_role else the admin owner."""
    sid = (settings.db_app_secret_name if app_role
           else settings.db_secret_arn or settings.db_secret_name)
    sm = boto3.client("secretsmanager", region_name=settings.region)
    return json.loads(sm.get_secret_value(SecretId=sid)["SecretString"])


def build_repo(settings: Settings) -> PostgresRepository:
    """Build a repo with a dedicated connection (for workers/scripts, not HTTP)."""
    return PostgresRepository(db_connection(settings))


def get_connection_from_pool(pool) -> "psycopg2.extensions.connection":
    """Acquire a connection from the pool for a single request."""
    return pool.getconn()


def return_connection_to_pool(pool, conn) -> None:
    """Reset tenant state, then return the connection so it can't leak to the next request."""
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', '', false)")
    except Exception:
        pass
    pool.putconn(conn)


def build_storage(settings: Settings) -> BotoS3Storage:
    """S3 storage bound to the materials bucket and its KMS key alias."""
    from botocore.config import Config
    # SigV4 required for SSE-KMS presigned URLs (SigV2 silently fails)
    cfg = Config(signature_version="s3v4")
    # Explicit endpoint avoids path-style vs virtual-hosted ambiguity
    s3 = boto3.client("s3", region_name=settings.region,
                      endpoint_url=f"https://s3.{settings.region}.amazonaws.com",
                      config=cfg)
    return BotoS3Storage(s3, settings.bucket, settings.kms_alias,
                         ttl=settings.presign_ttl)


def build_queue(settings: Settings) -> SqsQueue:
    sqs = boto3.client("sqs", region_name=settings.region)
    return SqsQueue(sqs, settings.queue_url)


def build_embedder(settings: Settings):
    """Real Bedrock embedder when enabled, else the deterministic fake."""
    if not settings.use_bedrock:
        return FakeEmbedder(dims=settings.embed_dims)
    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)
    return BedrockEmbedder(client, settings.embed_model, dims=settings.embed_dims)


def build_cognito_config(settings: Settings) -> dict:
    """Load the Cognito pool/client/issuer config from Secrets Manager."""
    sm = boto3.client("secretsmanager", region_name=settings.region)
    return json.loads(
        sm.get_secret_value(SecretId=settings.cognito_secret_name)["SecretString"])


def build_identity_resolver(settings: Settings, pool):
    """Access-token validator + pooled resolver = the request-time chokepoint."""
    from backend.auth.token import validator_from_config
    from backend.auth.identity import IdentityResolver
    cfg = build_cognito_config(settings)
    return IdentityResolver(validator_from_config(cfg), pool)


def build_api(settings: Settings, repo, storage, queue) -> MaterialsApi:
    """Wire the presign/register API. Professors may only act on courses they own."""
    def _owns_course(caller, course_id) -> bool:
        c = repo.get_course(course_id)
        return c is not None and c.created_by == caller.user_id
    return MaterialsApi(repo, storage, queue, _owns_course)


def build_worker(settings: Settings) -> IngestWorker:
    """Assemble the SQS worker with a Postgres-backed pipeline."""
    repo = build_repo(settings)
    pipeline = IngestPipeline(repo, build_storage(settings), build_embedder(settings),
                              settings=settings)
    return IngestWorker(repo, build_queue(settings), pipeline)
