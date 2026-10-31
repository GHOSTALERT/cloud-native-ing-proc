"""Naive tiling vs the cost-aware planner (Upgrade 3).

Engine level (in-memory process pool, the same setting as bench_local / results.csv):
  sequential   whole image in one process
  naive        512 px tiles on every available worker (the original behaviour)
  planner      planner.plan(): local, or its chosen tile size and worker count
  fixed-512    ablation: planner may only pick local vs distributed and n, tile fixed at 512
Plus a sweep of (tile size, workers) configurations: predicted vs measured time,
for the model-accuracy scatter and its mean absolute percentage error (MAPE).

Cluster level (--cluster): the full framework in S3 mode (Redis + MinIO +
N workers), naive (tile_size=512) vs planner (tile_size=auto), end to end.

    python -m benchmark.bench_planner                 # engine level
    python -m benchmark.bench_planner --cluster --workers 8
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import psutil

from imgframe import planner
from imgframe.models import MODELS, available
from imgframe.ops import get_op
from imgframe.pipeline import MP_CONTEXT, run_sequential, run_tiled
from imgframe.sample import synthetic_image

ROOT = Path(__file__).resolve().parent.parent
SIZES = {"hed": 2048, "superres": 1024}          # neural ops: ~20 s sequential each
DEFAULT_OPS = ["grayscale", "canny", "gaussian_blur", "sobel", "bilateral", "hed", "superres"]


def _warm(op: str) -> None:
    get_op(op).fn(np.zeros((32, 32, 3), np.uint8))   # load the model once per process


def test_image(op: str, size: int) -> np.ndarray:
    n = SIZES.get(op, size)
    if get_op(op).kind == "neural":   # natural image: the networks were trained on photos
        src = cv2.imread(str(ROOT / "samples" / "astronaut_2048.png"))
        return cv2.resize(src, (n, n), interpolation=cv2.INTER_AREA)
    return synthetic_image(n, n)


class Pools:
    """One warmed process pool per worker count, reused across runs."""

    def __init__(self, op: str):
        self.op, self.pools = op, {}

    def get(self, n: int) -> ProcessPoolExecutor:
        if n not in self.pools:
            self.pools[n] = ProcessPoolExecutor(n, mp_context=MP_CONTEXT, initializer=_warm,
                                                initargs=(self.op,))
            list(self.pools[n].map(abs, range(n * 4)))
        return self.pools[n]

    def close(self):
        for p in self.pools.values():
            p.shutdown()


def timed(fn, repeats: int) -> float:
    ts = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def run_plan(img, op, p: planner.Plan, pools: Pools, repeats: int) -> float:
    if p.mode == "local":
        return timed(lambda: run_sequential(img, op), repeats)
    return timed(lambda: run_tiled(img, op, p.tile_size, p.workers, pools.get(p.workers)), repeats)


def engine(args) -> dict:
    profiles = planner.load_profiles()
    rows, scatter = [], []
    for op in args.ops:
        img = test_image(op, args.size)
        h, w = img.shape[:2]
        neural = get_op(op).kind == "neural"
        reps = 1 if neural else args.repeats
        _warm(op)
        pools = Pools(op)
        try:
            seq = timed(lambda: run_sequential(img, op), reps)
            naive_t = timed(lambda: run_tiled(img, op, 512, args.workers, pools.get(args.workers)), reps)
            p = planner.plan(op, h, w, args.workers, backend="engine", profiles=profiles)
            plan_t = run_plan(img, op, p, pools, reps)
            f = planner.plan(op, h, w, args.workers, backend="engine", profiles=profiles, tile_sizes=(512,))
            fixed_t = plan_t if (f.mode, f.tile_size, f.workers) == (p.mode, p.tile_size, p.workers) \
                else run_plan(img, op, f, pools, reps)
            row = {"op": op, "size": f"{w}x{h}", "sequential_s": round(seq, 4),
                   "naive_s": round(naive_t, 4), "naive_speedup": round(seq / naive_t, 2),
                   "plan_mode": p.mode, "plan_tile": p.tile_size, "plan_workers": p.workers,
                   "plan_predicted_s": round(p.predicted_s, 4), "plan_s": round(plan_t, 4),
                   "plan_speedup": round(seq / plan_t, 2),
                   "fixed512_mode": f.mode, "fixed512_workers": f.workers, "fixed512_s": round(fixed_t, 4),
                   "fixed512_speedup": round(seq / fixed_t, 2)}
            rows.append(row)
            print(f"[{op}] {w}x{h}: seq {seq:.3f}s | naive {naive_t:.3f}s ({seq / naive_t:.2f}x) | "
                  f"planner {p.mode} t={p.tile_size} n={p.workers}: {plan_t:.3f}s ({seq / plan_t:.2f}x, "
                  f"pred {p.predicted_s:.3f}s) | fixed-512 {f.mode} n={f.workers}: {fixed_t:.3f}s", flush=True)

            # model accuracy: predicted vs measured over a sweep of configurations, for the
            # refined model and for the plain formula of the upgrade plan ("simple")
            c, ov = planner.c_op(op, profiles), planner.overheads("engine", profiles, op)

            def point(config, actual, s=None, n=None):
                if s is None:
                    pred = simple = p.t_local_s
                else:
                    pred = planner.predict(op, h, w, s, n, c, ov)[0]
                    simple = planner.predict(op, h, w, s, n, c, ov, model="simple")[0]
                scatter.append({"op": op, "config": config, "predicted_s": pred,
                                "predicted_simple_s": simple, "actual_s": actual})

            point("local", seq)
            point(f"t512/n{args.workers}", naive_t, 512, args.workers)
            sweep = [(256, 4), (1024, 8), (512, 2)] if neural else \
                [(256, 4), (256, 16), (512, 2), (512, 8), (1024, 4), (1024, 16), (2048, 4)]
            for s, n in sweep:
                point(f"t{s}/n{n}", timed(lambda: run_tiled(img, op, s, n, pools.get(n)), reps), s, n)
        finally:
            pools.close()
    return {"rows": rows, "scatter": scatter}


def cluster(args) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_local
    from imgframe.tiling import encode_png

    redis_url = run_local.start_redis(6430)
    s3 = run_local.start_minio(9130)
    api, workers, url = run_local.launch(args.workers, 8140, redis_url, 1,
                                         {**s3, "PLANNER_WORKERS": str(args.workers)})
    rows, scatter = [], []

    def job(png, op, ts):
        j = run_local.submit(url, png, f"{op}.png", op, ts)
        while j["status"] not in ("done", "failed"):
            time.sleep(0.05)
            j = run_local.http_json(f"{url}/jobs/{j['id']}")
        run_local.delete_job(url, j["id"])
        return j

    try:
        time.sleep(1.5)
        for op in args.ops:
            img = test_image(op, args.cluster_size)
            png = encode_png(img)
            reps = 1 if get_op(op).kind == "neural" else args.repeats
            res = {}
            for label, ts in (("naive", 512), ("planner", "auto")):
                runs = [job(png, op, ts) for _ in range(reps)]
                best = min(runs, key=lambda j: j["timings"]["total_s"])
                res[label] = best
                for j in runs:
                    scatter.append({"op": op, "config": label, "predicted_s": j["predicted_s"],
                                    "actual_s": j["timings"]["total_s"]})
            t0 = time.perf_counter()
            run_sequential(img, op)
            seq = time.perf_counter() - t0
            n, p = res["naive"], res["planner"]
            row = {"op": op, "size": f"{img.shape[1]}x{img.shape[0]}", "sequential_s": round(seq, 4),
                   "naive_s": n["timings"]["total_s"], "naive_tiles": n["total"],
                   "planner_s": p["timings"]["total_s"], "planner_mode": p["plan_mode"],
                   "planner_tile": p["tile_size"], "planner_tiles": p["total"],
                   "planner_workers": p["planned_workers"], "planner_predicted_s": p["predicted_s"],
                   "gain": round(n["timings"]["total_s"] / p["timings"]["total_s"], 2)}
            rows.append(row)
            print(f"[cluster {op}] naive {row['naive_s']}s ({row['naive_tiles']} tiles) | planner "
                  f"{row['planner_mode']} t={row['planner_tile']}: {row['planner_s']}s "
                  f"(pred {row['planner_predicted_s']}s) | sequential in-memory {seq:.3f}s", flush=True)
    finally:
        run_local.stop(workers + [api])
        run_local.stop_redis()
    return {"rows": rows, "scatter": scatter, "workers": args.workers}


def mape(scatter, key: str = "predicted_s") -> float:
    return 100 * statistics.mean(abs(s[key] - s["actual_s"]) / s["actual_s"] for s in scatter)


def plot(res: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows, sc = res["rows"], res["scatter"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.6))
    x = np.arange(len(rows))
    wd = 0.27
    a.bar(x - wd, [r["naive_speedup"] for r in rows], wd, label="naive (512 px, all workers)", color="#94a3b8")
    a.bar(x, [r["fixed512_speedup"] for r in rows], wd, label="planner, tile fixed at 512", color="#93c5fd")
    a.bar(x + wd, [r["plan_speedup"] for r in rows], wd, label="planner", color="#2563eb")
    a.axhline(1, color="#111827", lw=1)
    a.set_xticks(x, [f"{r['op']}\n{r['size']}" for r in rows], fontsize=8)
    a.set_ylabel("speedup over sequential (×)")
    a.set_title("Naive tiling vs cost-aware planner (in-memory engine)")
    a.legend(fontsize=8)
    a.grid(axis="y", alpha=.3)
    ops = sorted({s["op"] for s in sc})
    cmap = plt.get_cmap("tab10")
    for i, op in enumerate(ops):
        pts = [s for s in sc if s["op"] == op]
        b.scatter([s["predicted_s"] for s in pts], [s["actual_s"] for s in pts], s=22, color=cmap(i), label=op)
    lo = min(min(s["predicted_s"], s["actual_s"]) for s in sc) * 0.8
    hi = max(max(s["predicted_s"], s["actual_s"]) for s in sc) * 1.2
    b.plot([lo, hi], [lo, hi], "k--", lw=1)
    b.set_xscale("log"); b.set_yscale("log")
    b.set_xlabel("predicted time (s)"); b.set_ylabel("measured time (s)")
    b.set_title(f"Cost model accuracy: MAPE {mape(sc):.1f}% over {len(sc)} runs")
    b.legend(fontsize=8)
    b.grid(alpha=.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ops", nargs="+", default=[o for o in DEFAULT_OPS if o not in MODELS or available(o)])
    ap.add_argument("--size", type=int, default=8192, help="side of the classical-op image")
    ap.add_argument("--cluster-size", type=int, default=4096)
    ap.add_argument("--workers", type=int, default=psutil.cpu_count())
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--cluster", action="store_true", help="full framework instead of the engine")
    ap.add_argument("--out", default="benchmark/results")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.cluster:
        res = cluster(args)
        res["mape_pct"] = round(mape(res["scatter"]), 2)
        (out / "planner_cluster.json").write_text(json.dumps(res, indent=2))
        print(f"\nprediction MAPE {res['mape_pct']}%; wrote {out}/planner_cluster.json")
    else:
        res = engine(args)
        res["mape_pct"] = round(mape(res["scatter"]), 2)
        res["mape_simple_pct"] = round(mape(res["scatter"], "predicted_simple_s"), 2)
        by_op = {}
        for pt in res["scatter"]:
            by_op.setdefault(pt["op"], []).append(pt)
        res["mape_by_op"] = {op: {"refined": round(mape(v), 1), "simple": round(mape(v, "predicted_simple_s"), 1)}
                             for op, v in by_op.items()}
        res["workers"] = args.workers
        (out / "planner_engine.json").write_text(json.dumps(res, indent=2))
        plot(res, out / "planner_engine.png")
        print(f"\nprediction MAPE {res['mape_pct']}% (plain formula: {res['mape_simple_pct']}%); "
              f"wrote {out}/planner_engine.json and .png")


if __name__ == "__main__":
    main()
