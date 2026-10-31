"""Offline profiling for the cost-aware planner (Upgrade 3).

Measures every parameter of the cost model and writes imgframe/profiles.json:

  ops.<op>          c_op, compute seconds per pixel: each op is timed on tiles of
                    several sizes and c_op fitted by least squares on pixel count
  capacity.<op>     host capacity curve S_op(n) for n concurrently busy workers
  engine            in-memory process pool: per-tile overhead o0 + o_px * pixels
                    (pickling to and from the pool), split and stitch per pixel
  cluster           full framework, S3 mode (Redis + MinIO + 1 worker)
  cluster_redis     full framework, original all-in-Redis mode

The cluster overheads come from near-empty jobs (grayscale, ~1 ns/px) at tile
sizes 256..2048: (processing time - compute) / tiles, regressed on tile pixels.
It also records raw round-trip latencies (Redis PING, S3 PUT+GET of a 512 px
tile vs Redis SET+GET of the same bytes) for the S3 latency study.

    python scripts/profile_ops.py                    # everything (~4 min)
    python scripts/profile_ops.py --skip-cluster     # ops + capacity + engine only
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from imgframe.models import MODELS, available  # noqa: E402
from imgframe.ops import OPS, get_op  # noqa: E402
from imgframe.pipeline import MP_CONTEXT, run_tiled  # noqa: E402
from imgframe.sample import synthetic_image  # noqa: E402
from imgframe.tiling import encode_png, reconstruct, split_image  # noqa: E402


def med(fn, n=3) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def fit(xs, ys) -> tuple[float, float]:
    """Least squares y = a + b x; returns (a, b) with a clamped at >= 0."""
    b, a = np.polyfit(np.asarray(xs, float), np.asarray(ys, float), 1)
    return max(0.0, float(a)), max(0.0, float(b))


def profile_ops(src: np.ndarray, ops: list[str]) -> dict:
    out = {}
    for op in ops:
        sizes = (128, 256, 384) if get_op(op).kind == "neural" else (256, 512, 1024, 2048)
        fn = get_op(op).fn
        fn(src[:64, :64])  # warm up (model load, OpenCV init)
        px, ts = [], []
        for s in sizes:
            tile = np.ascontiguousarray(src[:s, :s])
            px.append(s * s)
            ts.append(med(lambda: fn(tile)))
        a, c = fit(px, ts)
        out[op] = {"c_op": c, "intercept_s": a, "samples": [{"px": p, "s": round(t, 6)} for p, t in zip(px, ts)]}
        print(f"  {op:14s} c_op = {c * 1e9:9.2f} ns/px   (fixed {a * 1e3:.2f} ms)")
    return out


def _burn(op: str, tile: np.ndarray, reps: int) -> float:
    fn = get_op(op).fn
    fn(tile[:32, :32])  # model load is not part of the measurement
    t0 = time.perf_counter()
    for _ in range(reps):
        fn(tile)
    return time.perf_counter() - t0


def profile_capacity(src: np.ndarray, ops: list[str], c_ops: dict) -> dict:
    """Per-op host capacity curve S_op(n): n processes run the op at once on equal work;
    S(n) = n * t(1) / t(n). Captures shared cores, SMT, turbo and memory bandwidth,
    which hit memory-bound CNN inference much harder than compute-bound filters."""
    out = {}
    counts = sorted({1, 2, 4, 6, 8, 12, psutil.cpu_count()})
    for op in ops:
        side = 384 if get_op(op).kind == "neural" else 768
        tile = np.ascontiguousarray(src[:side, :side])
        reps = max(1, min(40, round(0.6 / max(1e-9, c_ops[op] * side * side))))
        curve, t1 = {}, None
        for n in counts:
            with ProcessPoolExecutor(n, mp_context=MP_CONTEXT) as pool:
                list(pool.map(abs, range(n * 2)))
                t = statistics.mean(pool.map(_burn, [op] * n, [tile] * n, [reps] * n))
            t1 = t1 or t
            curve[str(n)] = round(n * t1 / t, 3)
        out[op] = curve
        print(f"  capacity {op:14s} " + "  ".join(f"S({k})={v:.2f}" for k, v in curve.items()), flush=True)
    out["_default"] = out.get("bilateral", {"1": 1.0})
    return out


def profile_engine(img: np.ndarray, c_gray: float) -> dict:
    h, w = img.shape[:2]
    rows = []
    with ProcessPoolExecutor(1, mp_context=MP_CONTEXT) as pool:
        list(pool.map(abs, range(2)))
        for s in (256, 512, 1024, 2048):
            t = med(lambda: run_tiled(img, "grayscale", s, 1, pool))
            k = len(split_image(img, s, 0))
            rows.append((s * s, (t - c_gray * h * w) / k))
    o0, o_px = fit(*zip(*rows))
    split = med(lambda: [np.ascontiguousarray(t) for _, t in split_image(img, 512, 4)])
    parts = split_image(img, 512, 4)
    stitch = med(lambda: reconstruct(h, w, parts))
    res = {"o0": o0, "o_px": o_px / 2, "split_px": split / (h * w), "stitch_px": stitch / (h * w),
           "per_tile": [{"px": p, "overhead_s": round(o, 6)} for p, o in rows]}
    print(f"  engine: o0 {o0 * 1e3:.3f} ms + {o_px * 1e9:.2f} ns/px; split {res['split_px'] * 1e9:.2f} ns/px, "
          f"stitch {res['stitch_px'] * 1e9:.2f} ns/px")
    return res


def profile_cluster(img: np.ndarray, storage: str, c_gray: float, port: int) -> dict:
    import run_local

    h, w = img.shape[:2]
    png = encode_png(img)
    redis_url = run_local.start_redis(6420 + port % 10)
    s3 = run_local.start_minio(9100 + port % 10) if storage == "s3" else {}
    api, workers, url = run_local.launch(1, port, redis_url, 1, {**s3, "PLANNER": "0"})
    rows, splits, stitches = [], [], []
    try:
        time.sleep(1.0)
        for s in (256, 512, 1024, 2048):
            for _ in range(2):
                job = run_local.submit(url, png, "p.png", "grayscale", s)
                while job["status"] not in ("done", "failed"):
                    time.sleep(0.05)
                    job = run_local.http_json(f"{url}/jobs/{job['id']}")
                t = job["timings"]
                k = job["total"]
                rows.append((s * s, (t["processing_s"] - t["compute_s"]) / k))
                splits.append(t["partition_s"])
                stitches.append(t["aggregate_s"])
                run_local.delete_job(url, job["id"])
        rtt = round_trips(redis_url, s3)
    finally:
        run_local.stop(workers + [api])
        run_local.stop_redis()
    o0, o_px = fit(*zip(*rows))
    res = {"o0": o0, "o_px": o_px / 2, "split_px": statistics.median(splits) / (h * w),
           "stitch_px": statistics.median(stitches) / (h * w),
           "per_tile": [{"px": p, "overhead_s": round(o, 6)} for p, o in rows], "round_trips": rtt}
    print(f"  cluster ({storage}): o0 {o0 * 1e3:.3f} ms + {o_px * 1e9:.2f} ns/px; "
          f"split {res['split_px'] * 1e9:.2f} ns/px, stitch {res['stitch_px'] * 1e9:.2f} ns/px; {rtt}")
    return res


def round_trips(redis_url: str, s3: dict) -> dict:
    import os

    import redis

    r = redis.Redis.from_url(redis_url)
    tile = np.random.default_rng(0).integers(0, 255, (512 + 8, 512 + 8, 3), np.uint8).tobytes()
    out = {"redis_ping_ms": med(r.ping, 50) * 1e3,
           "redis_set_get_512_ms": med(lambda: (r.set("rt", tile), r.get("rt")), 20) * 1e3}
    if s3:
        os.environ.update(s3)
        from imgframe.objstore import ObjStore

        o = ObjStore(bucket=s3["S3_BUCKET"], endpoint=s3["S3_ENDPOINT"])
        o.ensure_bucket()
        o.put("rt/empty", b"")
        out["s3_put_get_empty_ms"] = med(lambda: (o.put("rt/empty", b""), o.get("rt/empty")), 20) * 1e3
        out["s3_put_get_512_ms"] = med(lambda: (o.put("rt/t", tile), o.get("rt/t")), 20) * 1e3
    return {k: round(v, 3) for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-cluster", action="store_true")
    ap.add_argument("--size", type=int, default=4096, help="image side for the overhead runs")
    args = ap.parse_args()

    import cv2
    src = cv2.resize(cv2.imread(str(ROOT / "samples" / "astronaut_2048.png")), (2048, 2048))
    ops = [o for o in OPS if o not in MODELS or available(o)]
    path = ROOT / "imgframe" / "profiles.json"
    try:
        prof = json.loads(path.read_text())
    except OSError:
        prof = {}
    print(f"machine: {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads\nops:")
    detail = profile_ops(src, ops)
    prof["ops"] = {op: d["c_op"] for op, d in detail.items()}
    prof["capacity"] = profile_capacity(src, ops, prof["ops"])
    img = synthetic_image(args.size, args.size)
    c_gray = prof["ops"]["grayscale"]
    print("overheads:")
    prof["engine"] = profile_engine(img, c_gray)
    if not args.skip_cluster:
        prof["cluster"] = profile_cluster(img, "s3", c_gray, 8130)
        prof["cluster_redis"] = profile_cluster(img, "redis", c_gray, 8131)
    prof["ops_detail"] = detail
    prof["meta"] = {"date": time.strftime("%Y-%m-%d"), "cpus": psutil.cpu_count(),
                    "image": f"{args.size}x{args.size}"}
    path.write_text(json.dumps(prof, indent=2) + "\n")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
