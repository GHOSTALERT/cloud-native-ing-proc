"""End-to-end benchmark of the full framework (API -> Redis queue -> workers ->
aggregation), compared with sequential whole-image processing.

Local sweep (spins up a fresh stack per worker count, no Docker needed):
    python -m benchmark.bench_distributed --workers 1 2 4 8 --op bilateral --size 4096

Against a running deployment (Docker Compose / Kubernetes), one row per call;
scale the workers yourself and pass the count as a label:
    kubectl -n imgframe scale deploy/worker --replicas 4
    python -m benchmark.bench_distributed --api http://$(minikube ip):30080 --label 4
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import psutil

from benchmark.monitor import ResourceMonitor
from imgframe.pipeline import run_sequential
from imgframe.sample import synthetic_image
from imgframe.tiling import encode_png

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_local  # noqa: E402


def run_job(api: str, png: bytes, op: str, tile_size: int) -> tuple[dict, float, dict]:
    with ResourceMonitor() as mon:
        t0 = time.perf_counter()
        job = run_local.submit(api, png, "bench.png", op, tile_size)
        while job["status"] not in ("done", "failed"):
            time.sleep(0.05)
            job = run_local.http_json(f"{api}/jobs/{job['id']}")
        wall = time.perf_counter() - t0
    run_local.delete_job(api, job["id"])  # free Redis memory between runs
    if job["status"] != "done":
        raise RuntimeError(f"job failed: {job.get('error')}")
    return job, wall, mon.summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4, 8])
    ap.add_argument("--op", default="bilateral")
    ap.add_argument("--size", type=int, default=4096)
    ap.add_argument("--tile-size", type=int, default=512)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--api", help="benchmark an already running deployment instead")
    ap.add_argument("--label", default="?", help="worker count to record with --api")
    ap.add_argument("--out", default="benchmark/results")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    img = synthetic_image(args.size, args.size)
    png = encode_png(img)
    t0 = time.perf_counter()
    run_sequential(img, args.op)
    seq_t = time.perf_counter() - t0
    print(f"{args.op} on {args.size}x{args.size}: sequential whole-image {seq_t:.3f}s\n")

    rows = [{"op": args.op, "mode": "sequential", "workers": 1, "time_s": round(seq_t, 4),
             "speedup": 1.0, "efficiency": 1.0, "partition_s": None, "processing_s": round(seq_t, 4),
             "processing_speedup": 1.0, "aggregate_s": None, "retries": 0,
             "cores_busy_avg": None, "rss_peak_mb": None}]

    def record(n, runs):
        best = min(runs, key=lambda r: r[1])  # best of N: least disturbed by other load
        job, wall, res = best
        speedup = seq_t / wall
        t = job["timings"]
        rows.append({"op": args.op, "mode": "distributed", "workers": n, "time_s": round(wall, 4),
                     "speedup": round(speedup, 2),
                     "efficiency": round(speedup / n, 2) if isinstance(n, int) else None,
                     "partition_s": t["partition_s"], "processing_s": t["processing_s"],
                     "processing_speedup": round(seq_t / t["processing_s"], 2),
                     "aggregate_s": t["aggregate_s"], "retries": job["retries"],
                     "cores_busy_avg": res["cores_busy_avg"], "rss_peak_mb": res["rss_peak_mb"]})
        print(f"{n!s:>3} workers: end-to-end {wall:6.3f}s ({speedup:4.2f}x)  |  parallel phase "
              f"{t['processing_s']:6.3f}s ({seq_t / t['processing_s']:4.2f}x)  |  partition "
              f"{t['partition_s']}s, aggregate {t['aggregate_s']}s, {res['cores_busy_avg']} cores busy")

    if args.api:
        record(args.label, [run_job(args.api, png, args.op, args.tile_size) for _ in range(args.repeats)])
    else:
        for i, n in enumerate(args.workers):
            redis_port, port = 6400 + i, 8100 + i
            redis_url = run_local.start_redis(redis_port)
            api, workers, url = run_local.launch(n, port, redis_url)
            try:
                time.sleep(1.0)  # let workers register their scripts before timing
                runs = [run_job(url, png, args.op, args.tile_size) for _ in range(args.repeats)]
            finally:
                run_local.stop(workers + [api])
                run_local.stop_redis()
            record(n, runs)

    name = f"k8s_{args.op}_w{args.label}" if args.api else f"distributed_{args.op}"
    with open(out / f"{name}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / f"{name}.json").write_text(json.dumps({"size": args.size, "tile_size": args.tile_size,
                                                  "cpus": psutil.cpu_count(), "rows": rows}, indent=2))
    if not args.api:
        from benchmark.plot import plot_distributed
        plot_distributed(rows, out / f"{name}.png",
                         f"Full framework: {args.op}, {args.size}x{args.size}, {args.tile_size}px tiles")
    print(f"\nwrote {out}/{name}.csv and .json")


if __name__ == "__main__":
    main()
