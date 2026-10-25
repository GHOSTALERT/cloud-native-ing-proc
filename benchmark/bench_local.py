"""Compute-level benchmark: sequential vs tiled parallel execution.

Sequential = the OpenCV op on the whole image in one process.
Parallel   = image split into tiles, processed by a pool of N processes,
             stitched back (the same partition/reconstruct code the cluster uses).

Reports processing time, speedup, parallel efficiency and resource utilization
(CPU and memory) and writes results.csv / results.json / charts to --out.

    python -m benchmark.bench_local --size 8192 --ops bilateral canny --workers 1 2 4 8 16
"""
import argparse
import csv
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import psutil

from benchmark.monitor import ResourceMonitor
from imgframe.pipeline import MP_CONTEXT, run_sequential, run_tiled
from imgframe.sample import synthetic_image


def timed(fn, repeats: int) -> tuple[float, dict, object]:
    times, out = [], None
    with ResourceMonitor() as mon:
        for _ in range(repeats):
            t0 = time.perf_counter()
            out = fn()
            times.append(time.perf_counter() - t0)
    return statistics.median(times), mon.summary, out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=8192, help="image side length (px)")
    ap.add_argument("--ops", nargs="+", default=["bilateral", "canny"])
    ap.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4, 8, 16])
    ap.add_argument("--tile-size", type=int, default=512)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="benchmark/results")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"machine: {psutil.cpu_count(logical=False)} physical / {psutil.cpu_count()} logical cores, "
          f"{psutil.virtual_memory().total / 2**30:.0f} GB RAM")
    img = synthetic_image(args.size, args.size)
    print(f"image: {args.size}x{args.size} ({img.nbytes / 2**20:.0f} MB), tile {args.tile_size}px\n")

    rows = []
    for op in args.ops:
        seq_t, seq_res, expected = timed(lambda: run_sequential(img, op), args.repeats)
        rows.append({"op": op, "mode": "sequential", "workers": 1, "time_s": round(seq_t, 4),
                     "speedup": 1.0, "efficiency": 1.0, "match_pct": 100.0, **seq_res})
        print(f"[{op}] sequential: {seq_t:.3f}s  cpu {seq_res['cores_busy_avg']} cores")
        for n in args.workers:
            with ProcessPoolExecutor(n, mp_context=MP_CONTEXT) as pool:
                list(pool.map(abs, range(n * 2)))                      # warm up the pool
                par_t, res, result = timed(lambda: run_tiled(img, op, args.tile_size, n, pool),
                                           args.repeats)
            match = float(np.mean(result == expected)) * 100
            speedup = seq_t / par_t
            rows.append({"op": op, "mode": "parallel", "workers": n, "time_s": round(par_t, 4),
                         "speedup": round(speedup, 2), "efficiency": round(speedup / n, 2),
                         "match_pct": round(match, 3), **res})
            print(f"[{op}] {n:2d} workers: {par_t:.3f}s  speedup {speedup:5.2f}x  "
                  f"efficiency {speedup / n:4.0%}  cpu {res['cores_busy_avg']} cores  "
                  f"mem {res['rss_peak_mb']} MB  match {match:.3f}%")
        print()

    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "results.json").write_text(json.dumps({"size": args.size, "tile_size": args.tile_size,
                                                  "cpus": psutil.cpu_count(), "rows": rows}, indent=2))
    from benchmark.plot import plot
    plot(rows, out, f"{args.size}x{args.size} image, {args.tile_size}px tiles")
    print(f"wrote {out}/results.csv, results.json, charts.png")


if __name__ == "__main__":
    main()
