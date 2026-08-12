"""Quick offline smoke test: drive a Markdown upload end-to-end with fakes."""
from epistemy_m3.models import Caller, Role, PresignRequest
from epistemy_m3.api.service import MaterialsApi
from epistemy_m3.async_jobs.pipeline import IngestPipeline
from epistemy_m3.async_jobs.worker import IngestWorker
from epistemy_m3.async_jobs.queue import InMemoryQueue
from epistemy_m3.db.memory import InMemoryRepository
from epistemy_m3.embedding.fake import FakeEmbedder
from epistemy_m3.testing.fakes import FakeS3

MD = b"""# Machine Learning

Intro paragraph about ML.

## Gradient Descent

Gradient descent follows the negative gradient to minimize loss.

## Backpropagation

Backprop computes gradients via the chain rule.
"""


def main():
    repo, storage, queue = InMemoryRepository(), FakeS3(), InMemoryQueue()
    api = MaterialsApi(repo, storage, queue, lambda c, cid: True)
    pipeline = IngestPipeline(repo, storage, FakeEmbedder(1024))
    worker = IngestWorker(repo, queue, pipeline)
    prof = Caller(user_id="prof_1", org_id="org_acme", role=Role.PROFESSOR)

    print("1. presign…")
    req = PresignRequest(file_name="lecture-1.md", mime_type="text/markdown",
                         bytes=len(MD))
    resp = api.presign(prof, "course_cs101", req)
    print(f"   material={resp.material_id[:8]} version_no={resp.version_no}")
    print(f"   s3_key={resp.s3_key}")

    print("2. browser uploads bytes to S3 (faked)…")
    storage.put(resp.s3_key, MD)

    print("3. register → enqueue ingest job…")
    job = api.register(prof, resp.material_version_id)
    print(f"   job={job.job_id[:8]} status={job.status.value}")

    print("4. worker runs the pipeline…")
    worker.handle(queue.receive())

    version = repo.get_version(resp.material_version_id)
    material = repo.get_material(resp.material_id)
    print(f"\nRESULT: version status = {version.status.value}")
    print(f"        current_version flipped = "
          f"{material.current_version_id == version.material_version_id}")
    print(f"        chunks persisted = "
          f"{repo.count_chunks(version.material_version_id)}")
    _show_chunks(repo)


def _show_chunks(repo):
    print("\nchunks:")
    for ch in sorted(repo._chunks.values(), key=lambda c: c.chunk_index):
        head = ch.position.heading_path
        print(f"  [{ch.chunk_index}] tokens={ch.token_count} "
              f"heading={head} dims={len(ch.embedding or [])}")


if __name__ == "__main__":
    main()
