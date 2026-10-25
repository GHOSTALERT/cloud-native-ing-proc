import numpy as np

from imgframe.config import settings
from imgframe.pipeline import partition
from imgframe.store import LEASES, PENDING, PROCESSING


def make_job(store, image, tile=256):
    tiles = partition(image, "sobel", tile)
    h, w = image.shape[:2]
    return store.create_job(op="sobel", height=h, width=w, tile_size=tile, tiles=tiles), len(tiles)


def test_claim_complete_counts_progress(store, image):
    job_id, total = make_job(store, image)
    assert store.r.llen(PENDING) == total
    task = store.claim()
    assert task.startswith(job_id)
    assert store.get_job(job_id)["status"] == "running"
    assert store.r.llen(PROCESSING) == 1 and store.r.zcard(LEASES) == 1
    done, tot = store.complete(task, b"png")
    assert (done, tot) == (1, total)
    assert store.r.llen(PROCESSING) == 0 and store.r.zcard(LEASES) == 0


def test_duplicate_completion_is_ignored(store, image):
    job_id, _ = make_job(store, image)
    task = store.claim()
    assert store.complete(task, b"a")[0] == 1
    assert store.complete(task, b"b")[0] == -1
    assert store.get_job(job_id)["done"] == 1


def test_expired_lease_is_requeued(store, image):
    job_id, total = make_job(store, image)
    t0 = 1_000_000.0
    task = store.claim(now=t0)
    assert store.reap(now=t0 + 1) == (0, 0)                       # lease still valid
    assert store.reap(now=t0 + settings.lease_seconds + 1) == (1, 0)
    assert store.r.lindex(PENDING, 0).decode() == task           # retried first
    assert store.r.llen(PROCESSING) == 0
    assert store.get_job(job_id)["retries"] == 1


def test_heartbeat_extends_lease(store, image):
    make_job(store, image)
    t0 = 1_000_000.0
    task = store.claim(now=t0)
    assert store.heartbeat(task, now=t0 + 10)
    assert store.reap(now=t0 + settings.lease_seconds + 1) == (0, 0)


def test_job_fails_after_max_retries(store, image):
    job_id, _ = make_job(store, image)
    now = 1_000_000.0
    for attempt in range(settings.max_retries):
        task = store.claim(now=now)
        now += settings.lease_seconds + 1
        requeued, failed = store.reap(now=now)
    assert failed == 1
    job = store.get_job(job_id)
    assert job["status"] == "failed" and "attempts" in job["error"]
    assert store.load_task(store.claim()) is None    # remaining tiles of a failed job are dropped


def test_release_does_not_count_as_attempt(store, image):
    make_job(store, image)
    task = store.claim()
    assert store.release(task)
    assert store.r.lindex(PENDING, 0).decode() == task
    assert int(store.r.hget("imgframe:queue:attempts", task)) == 0
