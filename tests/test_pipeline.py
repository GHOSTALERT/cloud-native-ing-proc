"""End-to-end through the HTTP API with an in-process worker."""
import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from autoscaler.main import Autoscaler, desired_replicas
from imgframe.pipeline import run_sequential
from imgframe.tiling import decode_image, encode_png
from worker.main import Worker


@pytest.fixture
def client(store):
    with TestClient(create_app(store, run_reaper=False)) as c:
        yield c


def drain(store, n_workers=3):
    workers = [Worker(store, worker_id=f"w{i}", procs=1) for i in range(n_workers)]

    def loop(w):
        while w.step():
            pass

    threads = [threading.Thread(target=loop, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return workers


@pytest.mark.parametrize("op", ["canny", "gaussian_blur"])
def test_upload_process_download(client, store, image, op):
    r = client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                    data={"op": op, "tile_size": "200"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["total"] == 4 * 5
    assert client.get(f"/jobs/{job['id']}/result").status_code == 409

    drain(store)

    job = client.get(f"/jobs/{job['id']}").json()
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["timings"]["total_s"] is not None and job["timings"]["processing_s"] is not None
    jpg = client.get(f"/jobs/{job['id']}/result?format=jpg")
    assert jpg.status_code == 200 and jpg.headers["content-type"] == "image/jpeg"
    assert client.get(f"/jobs/{job['id']}/result?format=bmp").status_code == 400
    small = decode_image(client.get(f"/jobs/{job['id']}/result?max_side=300").content)
    assert max(small.shape[:2]) == 300
    assert client.get(f"/jobs/{job['id']}/preview").headers["content-type"] == "image/jpeg"
    result = decode_image(client.get(f"/jobs/{job['id']}/result").content)
    expected = run_sequential(image, op)
    assert result.shape == expected.shape
    assert (result == expected).mean() > 0.999

    m = client.get("/metrics").json()
    assert m["pending"] == 0 and m["tasks_done"] == 20 and m["active_workers"] == 3


def test_worker_crash_is_recovered(client, store, image):
    job = client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                      data={"op": "sobel", "tile_size": "300"}).json()
    lost = store.claim(now=1.0)        # a worker claims a tile then dies: its lease is long expired
    assert lost is not None
    drain(store)                       # surviving workers' reaper requeues the lost tile and finish the job
    job = client.get(f"/jobs/{job['id']}").json()
    assert job["status"] == "done" and job["retries"] == 1
    result = decode_image(client.get(f"/jobs/{job['id']}/result").content)
    assert np.array_equal(result, run_sequential(image, "sobel"))


def test_bad_requests(client):
    png = encode_png(np.zeros((10, 10, 3), np.uint8))
    assert client.post("/jobs", files={"file": ("x.png", png)}, data={"op": "nope"}).status_code == 400
    assert client.post("/jobs", files={"file": ("x.png", b"not an image")}).status_code == 400
    assert client.get("/jobs/missing").status_code == 404


def test_autoscaler_policy(store, image):
    assert desired_replicas(0, 1, 8, 16) == 1
    assert desired_replicas(33, 1, 8, 16) == 3
    assert desired_replicas(10_000, 1, 8, 16) == 8

    class FakeApps:
        replicas = 1
        def read_namespaced_deployment_scale(self, *_):
            return type("S", (), {"spec": type("Sp", (), {"replicas": self.replicas})})
        def patch_namespaced_deployment_scale(self, _n, _ns, body):
            self.replicas = body["spec"]["replicas"]

    apps = FakeApps()
    scaler = Autoscaler(store, apps, policy="queue")
    from imgframe.pipeline import partition
    big = cv2.resize(image, (2048, 2048))
    store.create_job(op="sobel", height=2048, width=2048, tile_size=128,
                     tiles=partition(big, "sobel", 128))           # 256 tasks
    assert scaler.tick(now=0) == 8 and apps.replicas == 8          # scale up immediately
    store.r.delete("imgframe:queue:pending")
    assert scaler.tick(now=1) == 8                                 # wait before scaling down
    assert scaler.tick(now=100) == 1 and apps.replicas == 1


def test_delete_job(client, store, image):
    job = client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")},
                      data={"op": "sobel", "tile_size": "300"}).json()
    drain(store)
    assert client.delete(f"/jobs/{job['id']}").status_code == 204
    assert client.get(f"/jobs/{job['id']}").status_code == 404
    assert not list(store.r.scan_iter(match=f"imgframe:job:{job['id']}*"))
    assert client.delete(f"/jobs/{job['id']}").status_code == 404


def test_backpressure_when_redis_is_full(client, store, image, monkeypatch):
    monkeypatch.setattr(store, "memory_ok", lambda extra: False)
    r = client.post("/jobs", files={"file": ("img.png", encode_png(image), "image/png")})
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert store.r.llen("imgframe:queue:pending") == 0
