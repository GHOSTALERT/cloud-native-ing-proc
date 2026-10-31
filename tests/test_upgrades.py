"""Tests for the four upgrades: object storage, scale/align-aware tiling and neural
ops, the cost-aware planner, and work-aware autoscaling."""
import threading

import cv2
import fakeredis
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from autoscaler.main import Autoscaler, work_replicas
from imgframe import ops as ops_mod
from imgframe import planner
from imgframe.config import settings
from imgframe.models import available
from imgframe.objstore import MemoryObjStore
from imgframe.pipeline import partition, run_sequential, run_tiled
from imgframe.store import WORK, Store
from imgframe.tiling import decode_image, encode_png, plan_tiles, reconstruct, split_image, stream_tiled_tiff
from worker.main import Worker


@pytest.fixture
def s3store():
    return Store(client=fakeredis.FakeRedis(), obj=MemoryObjStore())


@pytest.fixture
def s3client(s3store):
    with TestClient(create_app(s3store, run_reaper=False)) as c:
        yield c


def drain(store, n=3):
    workers = [Worker(store, worker_id=f"w{i}", procs=1) for i in range(n)]
    threads = [threading.Thread(target=lambda w=w: [None for _ in iter(w.step, False)]) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


@pytest.fixture
def setting(monkeypatch):
    old = {}

    def set_(**kw):
        for k, v in kw.items():
            old.setdefault(k, getattr(settings, k))
            object.__setattr__(settings, k, v)
    yield set_
    for k, v in old.items():
        object.__setattr__(settings, k, v)


# --------------------------------------------------------------- Upgrade 1
def test_s3_mode_keeps_pixels_out_of_redis(s3client, s3store, image):
    r = s3client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                      data={"op": "gaussian_blur", "tile_size": "200"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["storage"] == "s3"
    objs = s3store.obj.objects
    assert sum(k.startswith(f"jobs/{job['id']}/tiles/") for k in objs) == job["total"] == 20
    assert f"jobs/{job['id']}/input.png" in objs
    # Redis holds only control data: no value anywhere near tile size.
    biggest = max(len(v) for k in s3store.r.scan_iter(f"imgframe:job:{job['id']}*")
                  if s3store.r.type(k) == b"hash" for v in s3store.r.hvals(k))
    assert biggest < 2000

    drain(s3store)
    job = s3client.get(f"/jobs/{job['id']}").json()
    assert job["status"] == "done"
    assert not any(k.startswith(f"jobs/{job['id']}/tiles/") for k in objs)    # cleaned up
    r = s3client.get(f"/jobs/{job['id']}/result", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("memory://")
    url = s3client.get(f"/jobs/{job['id']}/url").json()
    assert url["expires_in"] == settings.presign_seconds
    key = r.headers["location"].split("memory://")[1].split("?")[0]
    assert np.array_equal(decode_image(objs[key]), run_sequential(image, "gaussian_blur"))
    assert s3client.delete(f"/jobs/{job['id']}").status_code == 204
    assert not any(k.startswith(f"jobs/{job['id']}/") for k in objs)


def test_s3_proxy_delivery(s3client, s3store, image, setting):
    setting(result_delivery="proxy")
    job = s3client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                        data={"op": "sobel", "tile_size": "300"}).json()
    drain(s3store)
    r = s3client.get(f"/jobs/{job['id']}/result")
    assert r.status_code == 200
    assert np.array_equal(decode_image(r.content), run_sequential(image, "sobel"))


def test_s3_backpressure_is_queue_depth(s3client, image, setting):
    setting(max_queued_tiles=5)
    r = s3client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                      data={"op": "sobel", "tile_size": "200"})
    assert r.status_code == 503


def test_worker_crash_recovered_in_s3_mode(s3client, s3store, image):
    job = s3client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                        data={"op": "sobel", "tile_size": "300"}).json()
    assert s3store.claim(now=1.0)         # claimed by a worker that then dies
    drain(s3store)
    job = s3client.get(f"/jobs/{job['id']}").json()
    assert job["status"] == "done" and job["retries"] == 1


# --------------------------------------------------------------- Upgrade 2
def _nearest2x(img):
    return cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)


def test_scale_aware_stitching(image, monkeypatch):
    monkeypatch.setitem(ops_mod.OPS, "up2", ops_mod.Op("up2", _nearest2x, 0, "test", scale=2))
    out = run_tiled(image, "up2", 128, workers=1)
    assert out.shape[:2] == (image.shape[0] * 2, image.shape[1] * 2)
    assert np.array_equal(out, _nearest2x(image))


def test_halo_alignment():
    for s in plan_tiles(1000, 1300, 256, halo=20, align=16):
        assert s.hy0 % 16 == 0 and s.hx0 % 16 == 0
        assert s.hy0 <= max(0, s.y0 - 20) and s.hx0 <= max(0, s.x0 - 20)


def test_streamed_tiff_equals_in_memory(image, tmp_path):
    import tifffile

    parts = [(s, _nearest2x(t)) for s, t in split_image(image, 160, 0)]
    preview = stream_tiled_tiff(str(tmp_path / "o.tif"), *image.shape[:2], 160, 2, iter(parts),
                                preview_side=300)
    expected = reconstruct(*image.shape[:2], parts, scale=2)
    assert np.array_equal(tifffile.imread(tmp_path / "o.tif")[..., ::-1], expected)
    assert max(preview.shape[:2]) == 300


@pytest.mark.skipif(not available("hed"), reason="HED model not downloaded")
def test_hed_tiled_with_calibrated_halo(image):
    img = cv2.resize(image, (384, 384))
    full = run_sequential(img, "hed")
    tiled = run_tiled(img, "hed", 128, workers=1)
    assert full.shape == img.shape[:2] and full.dtype == np.uint8
    assert np.abs(full.astype(int) - tiled).max() <= 16      # calibrated: small seam error
    naive = run_tiled(img, "hed", 128, workers=1, halo=0)
    assert np.abs(full.astype(int) - naive).mean() > np.abs(full.astype(int) - tiled).mean()


@pytest.mark.skipif(not available("superres"), reason="super-resolution model not downloaded")
def test_superres_end_to_end(client, store, image):
    small = cv2.resize(image, (160, 120))
    job = client.post("/jobs", files={"file": ("s.png", encode_png(small), "image/png")},
                      data={"op": "superres", "tile_size": "64"}).json()
    assert job["scale"] == 4
    from tests.test_pipeline import drain as drain_redis
    drain_redis(store)
    out = decode_image(client.get(f"/jobs/{job['id']}/result").content)
    assert out.shape == (480, 640, 3)


@pytest.fixture
def client(store):
    with TestClient(create_app(store, run_reaper=False)) as c:
        yield c


# --------------------------------------------------------------- Upgrade 3
def test_planner_keeps_cheap_ops_local_and_distributes_heavy_ones():
    canny = planner.plan("canny", 8192, 8192, 16)
    assert canny.mode == "local"
    bil = planner.plan("bilateral", 8192, 8192, 16)
    assert bil.mode == "distributed" and bil.predicted_s < bil.t_local_s
    hed = planner.plan("hed", 2048, 2048, 8)
    assert hed.mode == "distributed" and hed.tile_size >= 512   # big halo -> avoid small tiles


def test_planner_cost_mode_uses_fewer_workers():
    fast = planner.plan("bilateral", 8192, 8192, 16)
    cheap = planner.plan("bilateral", 8192, 8192, 16, deadline_s=fast.predicted_s * 2)
    assert cheap.workers < fast.workers and cheap.predicted_s <= fast.predicted_s * 2


def test_api_auto_plan_runs_cheap_op_as_single_tile(client, store, image):
    job = client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                      data={"op": "grayscale"}).json()
    assert job["plan_mode"] == "local" and job["total"] == 1
    drain(store, 1)
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "done"
    assert client.get("/plan", params={"op": "bilateral", "width": 8192, "height": 8192,
                                       "workers": 8}).json()["mode"] == "distributed"


def test_online_profile_ema(store):
    store.update_profile("canny", 1.0, 1_000_000)
    assert store.profile_c_op("canny") == pytest.approx(1e-6)
    store.update_profile("canny", 2.0, 1_000_000)
    assert store.profile_c_op("canny") == pytest.approx(1e-6 * (1 - settings.profile_ema)
                                                        + 2e-6 * settings.profile_ema, rel=1e-4)


# --------------------------------------------------------------- Upgrade 4
def test_work_counter_is_exact_with_retries_and_duplicates(store, image):
    tiles = partition(image, "sobel", 256)
    costs = {s.index: 0.5 for s, _ in tiles}
    h, w = image.shape[:2]
    job = store.create_job(op="sobel", height=h, width=w, tile_size=256, tiles=tiles, costs=costs)
    assert store.work_seconds() == pytest.approx(0.5 * len(tiles))
    t = store.claim()
    store.complete(t, b"x")
    store.complete(t, b"x")                              # duplicate: subtracted once
    assert store.work_seconds() == pytest.approx(0.5 * (len(tiles) - 1))
    lost = store.claim(now=1.0)
    store.reap(now=1.0 + settings.lease_seconds + 1)     # retried: cost stays queued
    assert store.work_seconds() == pytest.approx(0.5 * (len(tiles) - 1))
    store.discard(lost)
    assert store.work_seconds() == pytest.approx(0.5 * (len(tiles) - 2))
    store.delete_job(job)
    assert store.work_seconds() == pytest.approx(0.0, abs=1e-9)
    assert store.work_added() == pytest.approx(0.5 * len(tiles))


def test_work_replicas_rule():
    assert work_replicas(0, 0, 30, 0.8, 1, 8) == 1
    assert work_replicas(48, 0, 30, 0.8, 1, 8) == 2           # 48 / (30 * 0.8) = 2
    assert work_replicas(49, 0, 30, 0.8, 1, 8) == 3
    assert work_replicas(0, 3.3, 30, 0.8, 1, 8) == 5          # keep up with arrivals: 3.3 / 0.8
    assert work_replicas(10_000, 0, 30, 0.8, 1, 8) == 8
    assert work_replicas(48, 0, 30, 0.8, 1, 8, startup_s=10) == 3   # less time to help


def test_work_aware_scaler_ignores_task_count(store, image):
    class FakeApps:
        replicas = 1
        def read_namespaced_deployment_scale(self, *_):
            return type("S", (), {"spec": type("Sp", (), {"replicas": self.replicas})})
        def patch_namespaced_deployment_scale(self, _n, _ns, body):
            self.replicas = body["spec"]["replicas"]

    big = cv2.resize(image, (2048, 2048))
    tiles = partition(big, "sobel", 128)                             # 256 cheap tasks
    store.create_job(op="sobel", height=2048, width=2048, tile_size=128, tiles=tiles,
                     costs={s.index: 0.001 for s, _ in tiles})
    apps = FakeApps()
    queue, work = Autoscaler(store, apps, "queue"), Autoscaler(store, FakeApps(), "work")
    assert queue.tick(now=0) == 8           # 256 tasks -> max replicas
    assert work.tick(now=0) == 1            # 0.26 s of work -> one worker is plenty
    store.r.set(WORK, 200.0)                # 200 s of expensive work queued
    assert work.tick(now=3) == 8
