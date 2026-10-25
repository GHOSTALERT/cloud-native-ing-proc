"""REST API: image upload + partitioning + job tracking.

POST /jobs              upload an image -> split into tiles -> enqueue tasks
GET  /jobs              recent jobs
GET  /jobs/{id}         status, progress and per-phase timings
GET  /jobs/{id}/result  reconstructed PNG
GET  /metrics           queue depth, workers, retries
GET  /                  minimal web UI
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from imgframe.config import settings
from imgframe.ops import OPS, get_op
from imgframe.pipeline import partition
from imgframe.store import Store
from imgframe.tiling import decode_image, decode_tile

log = logging.getLogger("api")

FORMATS = {
    "png": [cv2.IMWRITE_PNG_COMPRESSION, 1],
    "jpg": [cv2.IMWRITE_JPEG_QUALITY, 92],
    "webp": [cv2.IMWRITE_WEBP_QUALITY, 90],
}
PREVIEW_SIDE = 1200


def _encode(img, fmt: str, max_side: int = 0) -> bytes:
    h, w = img.shape[:2]
    if 0 < max_side < max(h, w):
        scale = max_side / max(h, w)
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(f".{fmt}", img, FORMATS[fmt])
    if not ok:
        raise ValueError(f"could not encode {fmt}")
    return buf.tobytes()


def create_app(store: Store | None = None, run_reaper: bool = True) -> FastAPI:
    store = store or Store()

    async def reaper() -> None:
        while True:
            try:
                requeued, failed = await run_in_threadpool(store.reap)
                if requeued or failed:
                    log.warning("reaper: requeued %d, gave up on %d", requeued, failed)
            except Exception:
                log.exception("reaper error")
            await asyncio.sleep(settings.reaper_interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(reaper()) if run_reaper else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Cloud-Native Parallel Image Processing", lifespan=lifespan)
    # Partitioning holds several copies of an image in memory; bound how many run at once.
    uploads = asyncio.Semaphore(settings.max_concurrent_uploads)

    def _submit(data: bytes, op: str, tile_size: int, filename: str) -> dict:
        t0 = time.perf_counter()
        img = decode_image(data)
        del data
        # Input tiles + result tiles (+ halos) + final image stay in Redis until the job ends.
        if not store.memory_ok(int(img.nbytes * 3.2)):
            raise Busy("Redis memory budget exhausted; retry when running jobs finish")
        tiles = partition(img, op, tile_size)
        partition_s = time.perf_counter() - t0
        h, w = img.shape[:2]
        job_id = store.create_job(op=op, height=h, width=w, tile_size=tile_size, tiles=tiles,
                                  filename=filename, partition_s=partition_s)
        store.cache_encoded_result(job_id, "preview", _encode(img, "jpg", PREVIEW_SIDE))
        return store.get_job(job_id)

    @app.post("/jobs", status_code=201)
    async def submit(file: UploadFile = File(...), op: str = Form("canny"),
                     tile_size: int = Form(settings.tile_size)) -> dict:
        try:
            get_op(op)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not 32 <= tile_size <= 8192:
            raise HTTPException(400, "tile_size must be between 32 and 8192")
        async with uploads:
            data = await file.read()
            try:
                job = await run_in_threadpool(_submit, data, op, tile_size, file.filename or "")
            except Busy as exc:
                raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        return _view(job)

    @app.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str) -> Response:
        if not store.delete_job(job_id):
            raise HTTPException(404, "job not found")
        return Response(status_code=204)

    @app.get("/jobs")
    def list_jobs(limit: int = 20) -> list[dict]:
        return [_view(j) for j in store.list_jobs(limit)]

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return _view(job)

    @app.get("/jobs/{job_id}/result")
    def job_result(job_id: str, format: str = "png", max_side: int = 0) -> Response:
        """Final image, optionally downscaled (max_side px) for display. Encoded on first
        request and cached, so the job itself finishes as soon as the tiles are stitched."""
        if format not in FORMATS:
            raise HTTPException(400, f"format must be one of {sorted(FORMATS)}")
        variant = f"{format}.{max_side}" if max_side > 0 else format
        data = store.get_encoded_result(job_id, variant)
        if data is None:
            raw = store.get_result(job_id)
            if raw is None:
                job = store.get_job(job_id)
                if job is None:
                    raise HTTPException(404, "job not found")
                raise HTTPException(409, f"job is {job['status']}")
            data = _encode(decode_tile(raw), format, max_side)
            store.cache_encoded_result(job_id, variant, data)
        return Response(data, media_type=f"image/{'jpeg' if format == 'jpg' else format}")

    @app.get("/jobs/{job_id}/preview")
    def job_preview(job_id: str) -> Response:
        """Downscaled copy of the uploaded image."""
        data = store.get_encoded_result(job_id, "preview")
        if data is None:
            raise HTTPException(404, "preview not found")
        return Response(data, media_type="image/jpeg")

    @app.get("/metrics")
    def metrics() -> dict:
        return store.metrics()

    @app.get("/ops")
    def ops() -> dict:
        return {name: op.description for name, op in OPS.items()}

    @app.get("/healthz")
    def healthz() -> dict:
        store.r.ping()
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (Path(__file__).parent / "index.html").read_text()

    return app


class Busy(Exception):
    """The cluster cannot accept another job right now (back-pressure)."""


def _view(job: dict) -> dict:
    """Public job representation with derived timings."""
    out = {k: job.get(k) for k in ("id", "status", "op", "filename", "width", "height",
                                   "tile_size", "total", "done", "retries", "error")}
    out["progress"] = round(job["done"] / job["total"], 4) if job.get("total") else 0.0
    created, started, finished = job.get("created"), job.get("started"), job.get("finished")
    out["timings"] = {
        "partition_s": _r(job.get("partition_s")),
        "queue_wait_s": _r(started - created) if started else None,
        # first tile claimed -> last tile done: the phase the workers parallelise
        "processing_s": _r(job["processed"] - started) if started and job.get("processed") else None,
        "compute_s": _r(job.get("compute_s")),
        "aggregate_s": _r(job.get("aggregate_s")),
        # upload received -> final image ready (partitioning happens before `created`)
        "total_s": _r(finished - created + job.get("partition_s", 0.0)) if finished else None,
    }
    return out


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = create_app()  # Redis connects lazily, so importing this module needs no server
