"""Before/after study for Upgrade 1: pixels in Redis vs pixels in S3 (MinIO).

Both modes run the same stack (Redis capped at --redis-maxmemory, the API and
--workers workers); only where tile bytes live differs. Measured:

  largest image    biggest square image a single POST /jobs accepts before HTTP 503
  concurrency      how many 8192x8192 jobs can be queued at once before HTTP 503
  redis per job    MEMORY USAGE of one 8192x8192 job's Redis keys, and used_memory growth
  io per tile      tile fetch + result store time per tile (S3 GET/PUT vs Redis HGET/HSET)

    python -m benchmark.bench_storage
"""
import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

import redis

from imgframe.sample import synthetic_image
from imgframe.tiling import encode_png

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import run_local  # noqa: E402


def post(url: str, png: bytes, op: str, tile: int) -> dict | None:
    """Submit once; None on HTTP 503 (no retry: we want to see the refusal)."""
    body, ctype = run_local.multipart({"op": op, "tile_size": tile}, "file", "s.png", png)
    try:
        return run_local.http_json(f"{url}/jobs", body, {"Content-Type": ctype})
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            return None
        raise


def wait(url: str, job_id: str) -> dict:
    while True:
        j = run_local.http_json(f"{url}/jobs/{job_id}")
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.1)


def study(mode: str, args, pngs: dict[int, bytes], i: int) -> dict:
    redis_url = run_local.start_redis(6440 + i, args.redis_maxmemory)
    s3 = run_local.start_minio(9140 + i) if mode == "s3" else {}
    r = redis.Redis.from_url(redis_url)
    res = {"mode": mode, "redis_maxmemory": args.redis_maxmemory}

    # 1+2: admission tests with no workers, so nothing drains
    api, _, url = run_local.launch(0, 8150 + i, redis_url, 1, {**s3, "PLANNER": "0"})
    try:
        largest = None
        for n in sorted(pngs):
            j = post(url, pngs[n], "grayscale", 512)
            print(f"  [{mode}] single {n}x{n}: {'accepted' if j else 'HTTP 503'}", flush=True)
            if j is None:
                break
            largest = n
            run_local.delete_job(url, j["id"])
        res["largest_accepted"] = largest
        base = int(r.info("memory")["used_memory"])
        ids = []
        for k in range(args.max_concurrent):
            j = post(url, pngs[8192], "canny", 512)
            if j is None:
                break
            ids.append(j["id"])
            if k == 0:
                from imgframe.store import job_key
                res["redis_bytes_per_8192_job"] = sum(
                    int(r.memory_usage(key, samples=0) or 0) for key in r.scan_iter(job_key(j["id"]) + "*"))
                res["redis_used_growth_one_job"] = int(r.info("memory")["used_memory"]) - base
        res["concurrent_8192_jobs_accepted"] = len(ids)
        res["concurrent_limit_hit"] = len(ids) < args.max_concurrent
        print(f"  [{mode}] concurrent 8192x8192 jobs accepted: {len(ids)}"
              f"{' (then 503)' if res['concurrent_limit_hit'] else ''}; one job uses "
              f"{res['redis_bytes_per_8192_job'] / 2**20:.3f} MB of Redis", flush=True)
        for jid in ids:
            run_local.delete_job(url, jid)
    finally:
        run_local.stop([api])

    # 3: per-tile I/O with real workers
    api, workers, url = run_local.launch(args.workers, 8160 + i, redis_url, 1, {**s3, "PLANNER": "0"})
    try:
        time.sleep(1)
        runs = []
        for _ in range(3):
            j = wait(url, post(url, pngs[4096], "bilateral", 512)["id"])
            runs.append(j)
            run_local.delete_job(url, j["id"])
        best = min(runs, key=lambda j: j["timings"]["total_s"])
        t = best["timings"]
        res["io_ms_per_tile"] = round(1000 * min(j["timings"]["io_s"] for j in runs) / best["total"], 3)
        res["bilateral_4096"] = {k: t[k] for k in ("partition_s", "upload_s", "processing_s", "aggregate_s",
                                                    "total_s")}
        print(f"  [{mode}] io per tile {res['io_ms_per_tile']} ms; bilateral 4096 end-to-end {t['total_s']}s", flush=True)
    finally:
        run_local.stop(workers + [api])
        run_local.stop_redis()
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--redis-maxmemory", default="1gb")
    ap.add_argument("--sizes", nargs="+", type=int, default=[4096, 8192, 12288, 16384])
    ap.add_argument("--max-concurrent", type=int, default=12)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="benchmark/results")
    args = ap.parse_args()
    print("encoding test images...", flush=True)
    pngs = {n: encode_png(synthetic_image(n, n)) for n in sorted(set(args.sizes) | {4096, 8192})}
    results = [study(m, args, pngs, i) for i, m in enumerate(("redis", "s3"))]
    out = Path(args.out) / "storage_study.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
