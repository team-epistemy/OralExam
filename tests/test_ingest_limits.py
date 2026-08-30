"""P0 (P-S-1.3 / P-S-1.4): ingestion fails loudly with a stated reason instead
of silently partial-ingesting (image-only PDFs) or hanging (oversized files)."""
import io

import pytest

from backend.models import Caller, Role, PresignRequest, VersionStatus
from backend.api.service import MaterialsApi
from backend.async_jobs.pipeline import IngestPipeline
from backend.async_jobs.worker import IngestWorker
from backend.extract.pdf import PdfExtractor
from backend.embedding.fake import FakeEmbedder
from backend.db.memory import InMemoryRepository
from backend.async_jobs.queue import InMemoryQueue
from backend.testing.fakes import FakeS3
from backend.constants import MAX_UPLOAD_BYTES


def _wire():
    repo, storage, queue = InMemoryRepository(), FakeS3(), InMemoryQueue()
    api = MaterialsApi(repo, storage, queue, lambda c, cid: True)
    pipeline = IngestPipeline(repo, storage, FakeEmbedder(dims=1024))
    worker = IngestWorker(repo, queue, pipeline)
    return repo, storage, queue, api, worker


def _prof():
    return Caller(user_id="p1", org_id="org_a", role=Role.PROFESSOR)


def test_image_only_pdf_fails_with_named_reason(monkeypatch):
    """No extractable text -> FAILED naming the file, never a 'ready' empty doc."""
    repo, storage, queue, api, worker = _wire()
    req = PresignRequest(file_name="scan.pdf", mime_type="application/pdf", bytes=10)
    resp = api.presign(_prof(), "course_cs101", req)
    storage.put(resp.s3_key, b"%PDF-image-only")
    api.register(_prof(), resp.material_version_id)

    # Simulate a scanned/image-only PDF: the extractor yields no text units.
    class _EmptyExtractor:
        def extract(self, data):
            return []
    monkeypatch.setattr("backend.async_jobs.pipeline.get_extractor",
                        lambda source_type: _EmptyExtractor())

    worker.handle(queue.receive())
    version = repo.get_version(resp.material_version_id)
    material = repo.get_material(resp.material_id)
    assert version.status == VersionStatus.FAILED
    assert material.current_version_id is None            # nothing silently ingested
    assert "scan.pdf" in (version.error or {}).get("message", "")


def test_pdf_over_page_limit_raises_stated_limit(monkeypatch):
    """A PDF above the page cap fails with a message stating the limit."""
    PdfWriter = pytest.importorskip("pypdf").PdfWriter  # runtime dep; skip if absent
    monkeypatch.setattr("backend.extract.pdf.MAX_PDF_PAGES", 2)
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(ValueError) as ei:
        PdfExtractor().extract(buf.getvalue())
    assert "3 pages" in str(ei.value) and "2-page limit" in str(ei.value)


def test_presign_rejects_oversized_file():
    """Files over the byte cap are rejected up front with a stated limit."""
    repo, storage, queue, api, worker = _wire()
    req = PresignRequest(file_name="huge.pdf", mime_type="application/pdf",
                         bytes=MAX_UPLOAD_BYTES + 1)
    with pytest.raises(ValueError) as ei:
        api.presign(_prof(), "course_cs101", req)
    assert "huge.pdf" in str(ei.value) and "limit" in str(ei.value)
