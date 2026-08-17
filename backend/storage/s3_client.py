"""S3 wrapper. Presigned PUT bound to one key, plus existence checks/reads."""
from __future__ import annotations
from typing import Protocol


class S3Storage(Protocol):
    """Storage surface the API and worker depend on."""

    def presign_put(self, key: str, mime_type: str, max_bytes: int) -> str: ...
    def object_exists(self, key: str) -> bool: ...
    def get_bytes(self, key: str) -> bytes: ...


class BotoS3Storage:
    """Real S3-backed storage using a boto3 client."""

    def __init__(self, client, bucket: str, kms_key_id: str, ttl: int = 300):
        self.client = client
        self.bucket = bucket
        self.kms_key_id = kms_key_id
        self.ttl = ttl

    def presign_put(self, key: str, mime_type: str, max_bytes: int) -> str:
        """Presigned PUT locked to key and content-type; bucket default SSE-KMS."""
        return self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": mime_type},
            ExpiresIn=self.ttl)

    def object_exists(self, key: str) -> bool:
        """True when the object is present in the bucket."""
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def get_bytes(self, key: str) -> bytes:
        """Download an object body as bytes."""
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()
