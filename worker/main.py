"""Worker: claims tile tasks from Redis, fetches the tile (from S3 in object
storage mode), processes it with OpenCV or ONNX Runtime and stores the result.
The worker that completes a job's last tile reconstructs the final image
(aggregation); outputs too large to hold in memory are streamed to a tiled
TIFF in object storage instead.

Inside one worker, WORKER_PROCS slots run in parallel, each backed by a
process from a ProcessPoolExecutor. Across workers, Kubernetes scales pods.
"""
import logging
import multiprocessing
import os
import random
import signal
import socket
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor

import cv2

from imgframe import planner
from imgframe.config import settings
from imgframe.objstore import job_prefix
from imgframe.pipeline import assemble, process_tile
from imgframe.store import PROCESSING, Store
from imgframe.tiling import decode_tile, encode_tile, stream_tiled_tiff

log = logging.getLogger("worker")
# The worker is multi-threaded, so pool processes must not be fork()ed from it.
_MP = multiprocessing.get_context("forkserver")

# Chaos testing: probability that the worker process dies mid-task (simulates a pod crash).
CRASH_RATE = float(os.environ.get("CHAOS_CRASH_RATE", "0"))


class Worker:
    def __init__(self, store: Store, worker_id: str | None = None, procs: int | None = None,
                 use_pool: bool = True):
        self.store = store
        self.id = worker_id or os.environ.get("HOSTNAME") or f"{socket.gethostname()}-{os.getpid()}"
        self.procs = max(1, procs or settings.worker_procs)
        self.pool = ProcessPoolExecutor(self.procs, mp_context=_MP) if use_pool and self.procs > 1 else None
        self.stop = threading.Event()
        self.in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._last_reap = 0.0

    # ----------------------------------------------------------- main loop
    def run(self) -> None:
        log.info("worker %s started with %d slot(s)", self.id, self.procs)
        threads = [threading.Thread(target=self._slot, daemon=True) for _ in range(self.procs)]
        threads.append(threading.Thread(target=self._heartbeat, daemon=True))
        for t in threads:
            t.start()
        while not self.stop.is_set():
            try:
                self.store.record_worker(self.id)
            except Exception as exc:  # e.g. Redis restarting or full: keep the worker alive
                log.warning("could not record worker status: %s", exc)
            self.stop.wait(2.0)
        self._drain(threads[:-1])

    def _drain(self, slots: list[threading.Thread], grace: float = 20.0) -> None:
        deadline = time.time() + grace
        for t in slots:
            t.join(max(0.0, deadline - time.time()))
        with self._lock:
            leftover = list(self.in_flight)
        for task in leftover:  # hand unfinished tiles straight back to the queue
            self.store.release(task)
            log.info("released %s on shutdown", task)
        if self.pool:
            self.pool.shutdown(cancel_futures=True)
        log.info("worker %s stopped", self.id)

    def _slot(self) -> None:
        while not self.stop.is_set():
            try:
                if not self.step():
                    time.sleep(0.1)
            except Exception:  # keep the slot alive across Redis hiccups
                log.exception("slot error")
                time.sleep(1.0)

    def step(self) -> bool:
        """Claim and process one task. Returns False if the queue was empty."""
        self._maybe_reap()
        task = self.store.claim()
        if task is None:
            return False
        with self._lock:
            self.in_flight.add(task)
        try:
            self._process(task)
        finally:
            with self._lock:
                self.in_flight.discard(task)
        return True

    # ------------------------------------------------------------ stages
    def _process(self, task: str) -> None:
        payload = self.store.load_task(task)
        if payload is None:
            self.store.discard(task)
            return
        t0 = time.perf_counter()
        try:
            if CRASH_RATE and random.random() < CRASH_RATE:
                log.warning("chaos: crashing while holding %s", task)
                os._exit(137)
            if self.pool:
                result, compute_s = self.pool.submit(process_tile, payload.op,
                                                     payload.tile).result()
            else:
                result, compute_s = process_tile(payload.op, payload.tile)
        except Exception:
            log.exception("tile %s failed; will be retried", task)
            self.store.expire_lease(task)
            return
        t1 = time.perf_counter()
        done, total = self.store.complete(task, result, compute_s)
        store_s = time.perf_counter() - t1
        self.store.r.hincrbyfloat(f"imgframe:job:{payload.job_id}", "io_s", payload.fetch_s + store_s)
        try:
            # The tile ran next to `busy` others; remove that contention (the planner applies
            # it again through S_op(n)) so c_op stays a single-worker cost.
            busy = max(1, self.store.r.llen(PROCESSING) + 1)
            cap = planner.overheads("cluster", op=payload.op)
            self.store.update_profile(payload.op, compute_s * cap.speedup(busy) / busy,
                                      payload.spec.halo_pixels)
        except Exception:
            log.debug("profile update failed", exc_info=True)
        self.store.record_worker(self.id, tiles=1, busy_s=time.perf_counter() - t0)
        if done == total:
            self._aggregate(payload.job_id)

    def _stream(self, job: dict) -> str:
        """Stitch straight into a tiled BigTIFF in object storage (bounded memory)."""
        job_id = job["id"]
        key = job_prefix(job_id) + "output.tif"
        parts = ((spec, decode_tile(data)) for spec, data in self.store.iter_results(job_id))
        with tempfile.NamedTemporaryFile(suffix=".tif") as f:
            preview = stream_tiled_tiff(f.name, job["height"], job["width"], job["tile_size"],
                                        job["scale"], parts)
            self.store.obj.upload_file(key, f.name, "image/tiff")
        ok, buf = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 90])
        self.store.cache_encoded_result(job_id, "jpg.1200", buf.tobytes())
        return key

    def _aggregate(self, job_id: str, attempts: int = 3) -> None:
        processed_at = time.time()  # last tile finished: the parallel phase is over
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                job = self.store.get_job(job_id)
                out_mp = job["height"] * job["width"] * job["scale"] ** 2 / 1e6
                if self.store.obj is not None and out_mp > settings.stream_output_mp:
                    key = self._stream(job)
                    self.store.finish_job(job_id, None, time.perf_counter() - t0, processed_at,
                                          output_key=key)
                else:
                    img = assemble(job["height"], job["width"], self.store.load_results(job_id),
                                   job["scale"])
                    self.store.finish_job(job_id, encode_tile(img), time.perf_counter() - t0,
                                          processed_at)
                log.info("job %s reconstructed (%dx%d x%d) in %.3fs", job_id, job["width"],
                         job["height"], job["scale"], time.perf_counter() - t0)
                return
            except Exception as exc:
                log.exception("aggregation attempt %d failed for %s", attempt, job_id)
                if attempt == attempts:
                    self.store.fail_job(job_id, f"aggregation failed: {exc}")
                time.sleep(0.5 * attempt)

    # -------------------------------------------------------- background
    def _heartbeat(self) -> None:
        while not self.stop.wait(settings.heartbeat_seconds):
            with self._lock:
                tasks = list(self.in_flight)
            for task in tasks:
                try:
                    self.store.heartbeat(task)
                except Exception:
                    log.exception("heartbeat failed")

    def _maybe_reap(self) -> None:
        now = time.time()
        if now - self._last_reap >= settings.reaper_interval:
            self._last_reap = now
            requeued, failed = self.store.reap(now)
            if requeued or failed:
                log.warning("reaper: requeued %d, gave up on %d", requeued, failed)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = Worker(Store())
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
