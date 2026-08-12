"""Self-contained local demo: serves the UI and runs the pipeline with fakes.

  ../.venv/bin/python local_demo_server.py   then open http://localhost:8080
No AWS needed — uses in-memory repo, fake S3, fake embedder. Same pipeline code.
"""
from __future__ import annotations
import pathlib

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from epistemy_m3.models import Caller, Role, PresignRequest
from epistemy_m3.api.service import MaterialsApi, AuthorizationError
from epistemy_m3.async_jobs.pipeline import IngestPipeline
from epistemy_m3.async_jobs.worker import IngestWorker
from epistemy_m3.async_jobs.queue import InMemoryQueue
from epistemy_m3.db.memory import InMemoryRepository
from epistemy_m3.embedding.fake import FakeEmbedder
from epistemy_m3.testing.fakes import FakeS3
from epistemy_m3.tools.materials_tools import MaterialsTools

REPO = InMemoryRepository()
STORAGE = FakeS3()
QUEUE = InMemoryQueue()
API = MaterialsApi(REPO, STORAGE, QUEUE, lambda c, cid: c.role == Role.PROFESSOR)
WORKER = IngestWorker(REPO, QUEUE, IngestPipeline(REPO, STORAGE, FakeEmbedder(1024)))
TOOLS = MaterialsTools(REPO, API, lambda c, cid: True)

app = FastAPI(title="Epistemy M3 — Local Demo")
_STATIC = pathlib.Path(__file__).resolve().parent / "epistemy_m3" / "app" / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


def _caller(org, user, role) -> Caller:
    return Caller(user_id=user, org_id=org, role=Role(role))


def _guard(fn):
    try:
        return fn()
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.get("/")
def root():
    return RedirectResponse(url="/static/demo.html")


@app.get("/health")
def health():
    return {"status": "ok", "mode": "local-fakes"}


@app.post("/courses/{course_id}/materials:presign")
def presign(course_id: str, req: PresignRequest, x_org_id: str = Header(...),
            x_user_id: str = Header(...), x_role: str = Header("professor")):
    caller = _caller(x_org_id, x_user_id, x_role)
    resp = _guard(lambda: API.presign(caller, course_id, req))
    resp.upload_url = f"/local-upload?key={resp.s3_key}"
    return resp


@app.put("/local-upload")
async def local_upload(key: str, request: Request):
    """Stand in for the S3 presigned PUT: store bytes in the fake store."""
    STORAGE.put(key, await request.body())
    return PlainTextResponse("ok")


@app.post("/versions/{version_id}:register")
def register(version_id: str, x_org_id: str = Header(...),
             x_user_id: str = Header(...), x_role: str = Header("professor")):
    caller = _caller(x_org_id, x_user_id, x_role)
    job = _guard(lambda: API.register(caller, version_id))
    WORKER.handle(QUEUE.receive())
    return job


@app.get("/materials/{material_id}/versions")
def list_versions(material_id: str, x_org_id: str = Header(...),
                  x_user_id: str = Header(...), x_role: str = Header("professor")):
    caller = _caller(x_org_id, x_user_id, x_role)
    return _guard(lambda: TOOLS.list_material_versions(caller, material_id))


@app.get("/courses/{course_id}/materials")
def list_materials(course_id: str, x_org_id: str = Header(...),
                   x_user_id: str = Header(...), x_role: str = Header("professor")):
    caller = _caller(x_org_id, x_user_id, x_role)
    return _guard(lambda: TOOLS.list_materials(caller, course_id))


if __name__ == "__main__":
    import uvicorn
    print("Open http://localhost:8080 in your browser")
    uvicorn.run(app, host="127.0.0.1", port=8080)
