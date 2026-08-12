"""Builds real AWS/DB-backed components from Settings (used by both processes)."""
from __future__ import annotations
import json

import boto3

from epistemy_m3.config import Settings
from epistemy_m3.db.postgres import PostgresRepository
from epistemy_m3.storage.s3_client import BotoS3Storage
from epistemy_m3.async_jobs.queue import SqsQueue
from epistemy_m3.async_jobs.pipeline import IngestPipeline
from epistemy_m3.async_jobs.worker import IngestWorker
from epistemy_m3.embedding.embedder import BedrockEmbedder
from epistemy_m3.embedding.fake import FakeEmbedder
from epistemy_m3.api.service import MaterialsApi


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

    Each request acquires its own connection, ensuring tenant-isolation (RLS
    session vars) cannot leak between concurrent requests.
    """
    from psycopg2.pool import ThreadedConnectionPool
    creds = _db_credentials(settings)
    return ThreadedConnectionPool(
        minconn=2, maxconn=10,
        host=creds["host"], port=creds.get("port", 5432),
        dbname=creds.get("dbname", settings.db_name),
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
    )


def _db_credentials(settings: Settings) -> dict:
    """Fetch and parse the DB secret JSON."""
    sm = boto3.client("secretsmanager", region_name=settings.region)
    raw = sm.get_secret_value(SecretId=settings.db_secret_arn)["SecretString"]
    return json.loads(raw)


def build_repo(settings: Settings) -> PostgresRepository:
    """Build a repo with a dedicated connection (for workers/scripts, not HTTP)."""
    return PostgresRepository(db_connection(settings))


def get_connection_from_pool(pool) -> "psycopg2.extensions.connection":
    """Acquire a connection from the pool for a single request."""
    return pool.getconn()


def return_connection_to_pool(pool, conn) -> None:
    """Return a connection to the pool after a request completes."""
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


def build_api(settings: Settings, repo, storage, queue) -> MaterialsApi:
    """Wire the presign/register API."""
    # Authorize-all stub: real RBAC arrives with M1 auth module
    return MaterialsApi(repo, storage, queue, lambda caller, course_id: True)


def build_worker(settings: Settings) -> IngestWorker:
    """Assemble the SQS worker with a Postgres-backed pipeline."""
    repo = build_repo(settings)
    pipeline = IngestPipeline(repo, build_storage(settings), build_embedder(settings),
                              settings=settings)
    return IngestWorker(repo, build_queue(settings), pipeline)
