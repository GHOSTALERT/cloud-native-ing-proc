"""Replay one mixed job trace against a running deployment and record how the
worker autoscaler behaves (Upgrade 4: HPA vs KEDA vs work-aware).

The trace (fixed seed, so every scaler sees the identical workload) alternates
bursts of cheap jobs (grayscale, canny) with bursts of expensive ones
(bilateral, hed), with Poisson (exponential inter-arrival) timing. All jobs use
a fixed 512 px tile, so task counts are comparable and only the scaler differs.

Every second it samples the worker Deployment (spec and ready replicas via
kubectl) and the API's /metrics (queued work-seconds, pending tiles). Output:
per-job latency, p50/p95, % of jobs within the latency target, worker
pod-seconds (cost proxy), scaling events and time to first scale-up.

    bash scripts/k8s_scaler.sh work
    python scripts/trace_replay.py --api http://$(minikube ip):30080 --label work --run 1
"""
import argparse
import json
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from run_local import delete_job, http_json, submit  # noqa: E402

# (phase name, ops to draw from, number of jobs, mean inter-arrival seconds)
PHASES = [
    ("cheap burst 1", ["grayscale", "canny"], 10, 1.5),
    ("expensive burst 1", ["bilateral", "hed"], 6, 6.0),
    ("cheap burst 2", ["canny", "grayscale"], 10, 1.5),
    ("expensive burst 2", ["hed", "bilateral"], 6, 6.0),
]
IMAGE_SIDE = {"grayscale": 4096, "canny": 4096, "bilateral": 4096, "hed": 2048}


def make_trace(seed: int) -> list[dict]:
    rng = random.Random(seed)
    t, trace = 0.0, []
    for phase, ops, n, gap in PHASES:
        for _ in range(n):
            t += rng.expovariate(1.0 / gap)
            trace.append({"t": round(t, 2), "op": rng.choice(ops), "phase": phase})
        t += 15.0  # pause between phases
    return trace


def replicas(ns: str) -> tuple[int, int]:
    out = subprocess.run(["kubectl", "-n", ns, "get", "deploy", "worker", "-o",
                          "jsonpath={.spec.replicas} {.status.readyReplicas}"],
                         capture_output=True, text=True, timeout=10).stdout.split()
    spec = int(out[0]) if out else 0
    ready = int(out[1]) if len(out) > 1 and out[1] else 0
    return spec, ready


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True)
    ap.add_argument("--label", required=True, help="scaler name for the output file (hpa/keda/work)")
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--target", type=float, default=30.0, help="per-job latency target (s); = LATENCY_TARGET_S")
    ap.add_argument("--namespace", default="imgframe")
    ap.add_argument("--tail", type=float, default=45.0, help="keep sampling after the last job (s)")
    ap.add_argument("--out", default="benchmark/results/autoscale")
    args = ap.parse_args()

    from imgframe.sample import synthetic_image
    from imgframe.tiling import encode_png
    import cv2
    src = cv2.imread(str(ROOT / "samples" / "astronaut_2048.png"))
    images = {}
    for op, n in IMAGE_SIDE.items():
        img = cv2.resize(src, (n, n)) if op == "hed" else synthetic_image(n, n, seed=1)
        images[op] = encode_png(img)

    api = args.api.rstrip("/")
    trace = make_trace(args.seed)
    samples, jobs, stop = [], [], threading.Event()
    t0 = time.time()

    def sampler():
        while not stop.is_set():
            try:
                spec, ready = replicas(args.namespace)
                m = http_json(f"{api}/metrics")
                samples.append({"t": round(time.time() - t0, 2), "spec": spec, "ready": ready,
                                "work_s": m["work_seconds"], "pending": m["pending"],
                                "processing": m["processing"], "workers": m["active_workers"]})
            except Exception as exc:  # keep sampling through API hiccups
                print("sample failed:", exc, file=sys.stderr)
            stop.wait(1.0)

    def run_job(item):
        time.sleep(max(0.0, item["t"] - (time.time() - t0)))
        sub = time.time()
        j = submit(api, images[item["op"]], f"{item['op']}.png", item["op"], 512)
        while j["status"] not in ("done", "failed"):
            time.sleep(0.5)
            j = http_json(f"{api}/jobs/{j['id']}")
        rec = {**item, "submitted": round(sub - t0, 2), "latency_s": round(time.time() - sub, 2),
               "status": j["status"], "tiles": j["total"], "retries": j["retries"]}
        jobs.append(rec)
        print(f"{rec['submitted']:7.1f}s  {item['op']:10s} {j['status']:6s} {rec['latency_s']:6.1f}s  "
              f"({item['phase']})", flush=True)
        delete_job(api, j["id"])

    threading.Thread(target=sampler, daemon=True).start()
    threads = [threading.Thread(target=run_job, args=(it,)) for it in trace]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    time.sleep(args.tail)
    stop.set()

    lat = np.array([j["latency_s"] for j in jobs])
    ts = np.array([s["t"] for s in samples])
    ready = np.array([s["ready"] for s in samples])
    spec = [s["spec"] for s in samples]
    first_arrival = trace[0]["t"]
    up = [s["t"] for s in samples if s["t"] >= first_arrival and s["spec"] > spec[0]]
    summary = {
        "label": args.label, "run": args.run, "seed": args.seed, "target_s": args.target, "jobs": len(jobs),
        "failed": sum(j["status"] != "done" for j in jobs),
        "p50_s": round(float(np.percentile(lat, 50)), 2), "p95_s": round(float(np.percentile(lat, 95)), 2),
        "within_target_pct": round(100 * float(np.mean(lat <= args.target)), 1),
        "pod_seconds": round(float(np.trapezoid(ready, ts)), 1),
        "scaling_events": sum(a != b for a, b in zip(spec, spec[1:])),
        "first_scale_up_s": round(up[0] - first_arrival, 1) if up else None,
        "max_replicas": max(spec), "duration_s": round(float(ts[-1]), 1),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}_run{args.run}.json").write_text(json.dumps(
        {"summary": summary, "jobs": sorted(jobs, key=lambda j: j["t"]), "samples": samples, "trace": trace},
        indent=1))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
