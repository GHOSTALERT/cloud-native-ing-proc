"""REST API: image upload + partitioning + job tracking.

POST /jobs              upload an image -> plan -> split into tiles -> enqueue tasks
GET  /jobs              recent jobs
GET  /jobs/{id}         status, progress, plan and per-phase timings
GET  /jobs/{id}/result  final image (S3 mode: 307 redirect to a short-lived presigned URL)
GET  /jobs/{id}/url     presigned URL as JSON
GET  /plan              what the cost-aware planner would do for an image size
GET  /metrics           queue depth, queued work-seconds, workers, retries
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
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from imgframe import planner
from imgframe.config import settings
from imgframe.objstore import job_prefix
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
        if store.obj is not None:
            await run_in_threadpool(store.obj.ensure_bucket)
        task = asyncio.create_task(reaper()) if run_reaper else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Cloud-Native Parallel Image Processing", lifespan=lifespan)
    # Partitioning holds several copies of an image in memory; bound how many run at once.
    uploads = asyncio.Semaphore(settings.max_concurrent_uploads)

    def workers_available() -> int:
        if settings.planner_workers > 0:
            return settings.planner_workers
        try:
            return max(1, store.metrics()["active_workers"])
        except Exception:
            return 1

    def make_plan(op: str, h: int, w: int, tile_size: int | None) -> planner.Plan:
        """Planner decision, or the user's fixed tile size (naive tiling) as an override."""
        c = store.profile_c_op(op)  # online estimate, refined by the workers
        n = workers_available()
        if tile_size is None and settings.planner:
            return planner.plan(op, h, w, n, c=c, margin=settings.planner_margin)
        ts = tile_size or settings.tile_size
        ov, c = planner.overheads(), c or planner.c_op(op)
        t, k = planner.predict(op, h, w, ts, n, c, ov)
        return planner.Plan(op, "fixed", ts, n, k, t, planner.predict_local(op, h, w, c, ov, True), t,
                            "tile size given by the request")

    def _submit(data: bytes, op: str, tile_size: int | None, filename: str) -> dict:
        t0 = time.perf_counter()
        img = decode_image(data)
        h, w = img.shape[:2]
        o = get_op(op)
        if store.obj is None:
            del data
            # Input tiles + result tiles (+ halos) + final image stay in Redis until the job ends.
            if not store.memory_ok(int(img.nbytes * (1.2 + 2 * o.scale ** 2))):
                raise Busy("Redis memory budget exhausted; retry when running jobs finish")
        p = make_plan(op, h, w, tile_size)
        tiles = partition(img, op, p.tile_size)
        if store.obj is not None and not store.queue_ok(len(tiles)):
            raise Busy("task queue is full; retry when running jobs finish")
        partition_s = time.perf_counter() - t0
        c, ov = store.profile_c_op(op) or planner.c_op(op), planner.overheads()
        costs = {s.index: planner.tile_cost(op, s.halo_pixels, c, ov) for s, _ in tiles}
        job_id = store.new_job_id()
        if store.obj is not None:
            store.obj.put(job_prefix(job_id) + "input" + (Path(filename).suffix.lower() or ".png"), data)
        store.cache_encoded_result(job_id, "preview", _encode(img, "jpg", PREVIEW_SIDE))
        store.create_job(op=op, height=h, width=w, tile_size=p.tile_size, tiles=tiles,
                         filename=filename, partition_s=partition_s, job_id=job_id,
                         scale=o.scale, costs=costs, plan=p.to_dict())
        log.info("job %s: %s %dx%d -> %s (%s)", job_id, op, w, h, p.mode, p.reason)
        return store.get_job(job_id)

    @app.post("/jobs", status_code=201)
    async def submit(file: UploadFile = File(...), op: str = Form("canny"),
                     tile_size: str = Form("auto")) -> dict:
        """tile_size: "auto" lets the cost-aware planner decide (local vs distributed and
        the tile size); a number forces naive tiling with that size."""
        try:
            get_op(op)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        ts: int | None = None
        if tile_size not in ("", "auto"):
            try:
                ts = int(tile_size)
            except ValueError:
                raise HTTPException(400, "tile_size must be 'auto' or an integer")
            if not 32 <= ts <= 65536:
                raise HTTPException(400, "tile_size must be between 32 and 65536")
        async with uploads:
            data = await file.read()
            try:
                job = await run_in_threadpool(_submit, data, op, ts, file.filename or "")
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

    def _result_variant(job_id: str, format: str, max_side: int) -> tuple[str, bytes | None]:
        """Make sure the requested encoding exists (encode on first request, then cache).
        Returns (object key or cache name, bytes if they were just produced)."""
        if format == "tif":
            job = store.get_job(job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if not job.get("output_key", "").endswith(".tif"):
                raise HTTPException(409, "this job has no tiled TIFF output; use png/jpg/webp")
            return job["output_key"], None
        if format not in FORMATS:
            raise HTTPException(400, f"format must be one of {sorted(FORMATS) + ['tif']}")
        variant = f"{format}.{max_side}" if max_side > 0 else format
        data = store.get_encoded_result(job_id, variant)
        if data is None:
            raw = store.get_result(job_id)
            if raw is None:
                job = store.get_job(job_id)
                if job is None:
                    raise HTTPException(404, "job not found")
                if job["status"] == "done":
                    raise HTTPException(409, "result is a streamed tiled TIFF (too large to encode "
                                             "whole); use format=tif or max_side<=1200")
                raise HTTPException(409, f"job is {job['status']}")
            data = _encode(decode_tile(raw), format, max_side)
            store.cache_encoded_result(job_id, variant, data)
        return job_prefix(job_id) + f"result.{variant}", data

    @app.get("/jobs/{job_id}/result")
    def job_result(job_id: str, format: str = "png", max_side: int = 0) -> Response:
        """Final image, optionally downscaled (max_side px) for display. Encoded on first
        request and cached, so the job itself finishes as soon as the tiles are stitched.
        In S3 mode the bytes do not pass through the API: the reply is a redirect to a
        presigned URL that expires after PRESIGN_SECONDS."""
        key, data = _result_variant(job_id, format, max_side)
        media = {"jpg": "image/jpeg", "tif": "image/tiff"}.get(format, f"image/{format}")
        if store.obj is not None:
            if settings.result_delivery == "redirect":
                return RedirectResponse(store.obj.presign(key), status_code=307)
            data = data if data is not None else store.obj.get(key)
        return Response(data, media_type=media)

    @app.get("/jobs/{job_id}/url")
    def job_result_url(job_id: str, format: str = "png", max_side: int = 0) -> dict:
        """Presigned download URL (S3 mode only)."""
        if store.obj is None:
            raise HTTPException(400, "presigned URLs need object storage (set S3_BUCKET)")
        key, _ = _result_variant(job_id, format, max_side)
        return {"url": store.obj.presign(key, filename=f"{job_id}.{format}"),
                "expires_in": settings.presign_seconds}

    @app.get("/jobs/{job_id}/preview")
    def job_preview(job_id: str) -> Response:
        """Downscaled copy of the uploaded image."""
        data = store.get_encoded_result(job_id, "preview")
        if data is None:
            raise HTTPException(404, "preview not found")
        return Response(data, media_type="image/jpeg")

    @app.get("/plan")
    def show_plan(op: str, width: int, height: int, workers: int = 0) -> dict:
        """Dry run of the cost-aware planner (nothing is queued)."""
        try:
            get_op(op)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        c = store.profile_c_op(op)
        p = planner.plan(op, height, width, workers or workers_available(), c=c,
                         margin=settings.planner_margin)
        return {**p.to_dict(), "c_op": c or planner.c_op(op), "c_op_source": "online" if c else "profile"}

    @app.get("/metrics")
    def metrics() -> dict:
        return store.metrics()

    @app.get("/ops")
    def ops() -> dict:
        from imgframe.models import MODELS, available
        return {name: op.description for name, op in OPS.items()
                if name not in MODELS or available(name)}

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
                                   "tile_size", "scale", "total", "done", "retries", "error",
                                   "storage", "plan_mode", "planned_workers", "work_s")}
    out["predicted_s"] = _r(job.get("predicted_s"))
    out["output"] = "tiled-tiff" if str(job.get("output_key", "")).endswith(".tif") else "image"
    out["progress"] = round(job["done"] / job["total"], 4) if job.get("total") else 0.0
    created, started, finished = job.get("created"), job.get("started"), job.get("finished")
    out["timings"] = {
        "partition_s": _r(job.get("partition_s")),
        "upload_s": _r(job.get("upload_s")),
        "queue_wait_s": _r(started - created) if started else None,
        # first tile claimed -> last tile done: the phase the workers parallelise
        "processing_s": _r(job["processed"] - started) if started and job.get("processed") else None,
        "compute_s": _r(job.get("compute_s")),
        # tile fetch + result store, summed over tiles (S3 or Redis round trips)
        "io_s": _r(job.get("io_s")),
        "aggregate_s": _r(job.get("aggregate_s")),
        # upload received -> final image ready (partitioning happens before `created`)
        "total_s": _r(finished - created + job.get("partition_s", 0.0)) if finished else None,
    }
    return out


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = create_app()  # Redis connects lazily, so importing this module needs no server
