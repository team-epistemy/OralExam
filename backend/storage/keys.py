"""S3 key construction. The backend owns every component of the key."""
from __future__ import annotations


def build_s3_key(org_id: str, course_id: str, material_id: str,
                 version_no: int, file_name: str) -> str:
    """Build the tenant-scoped object key the presigned URL is bound to."""
    safe = file_name.replace("/", "_").strip()
    return (f"{org_id}/{course_id}/materials/{material_id}"
            f"/v{version_no}/{safe}")
