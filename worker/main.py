"""Worker: claims tile tasks from Redis, processes them with OpenCV and stores
the results. The worker that completes a job's last tile reconstructs the
final image (aggregation).

Inside one worker, WORKER_PROCS slots run in parallel, each backed by a
process from a ProcessPoolExecutor. Across workers, Kubernetes scales pods.
"""
import logging
import multiprocessing
import os
import random
import signal
import socket
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from imgframe.config import settings
from imgframe.pipeline import assemble, process_tile
from imgframe.store import Store
from imgframe.tiling import encode_tile

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
        done, total = self.store.complete(task, result, compute_s)
        self.store.record_worker(self.id, tiles=1, busy_s=time.perf_counter() - t0)
        if done == total:
            self._aggregate(payload.job_id)

    def _aggregate(self, job_id: str, attempts: int = 3) -> None:
        processed_at = time.time()  # last tile finished: the parallel phase is over
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                job = self.store.get_job(job_id)
                img = assemble(job["height"], job["width"], self.store.load_results(job_id))
                self.store.finish_job(job_id, encode_tile(img), time.perf_counter() - t0,
                                      processed_at)
                log.info("job %s reconstructed (%dx%d) in %.3fs", job_id, job["width"],
                         job["height"], time.perf_counter() - t0)
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
