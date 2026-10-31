"""Figures for the upgraded report and slides, built from the measured results.

    python docs/make_figures.py          # writes docs/figures/*.png

Inputs: benchmark/results/{results.csv, planner_engine.json, planner_cluster.json,
storage_study.json, halo_calibration.json, autoscale/*.json}, imgframe/profiles.json,
samples/astronaut_2048.png. Every number drawn comes from those files.
"""
import csv
import json
import sys
from pathlib import Path

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RES = ROOT / "benchmark" / "results"
OUT = ROOT / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

INK, MUTED, GRID = "#1f2937", "#6b7280", "#e5e7eb"
BLUE, ORANGE, GREEN, RED, PURPLE, GREY = "#2563eb", "#d97706", "#16a34a", "#dc2626", "#7c3aed", "#94a3b8"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": INK, "ytick.color": INK, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.dpi": 100})


def load(name):
    p = RES / name
    return json.loads(p.read_text()) if p.exists() else None


def save(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  wrote docs/figures/" + name)


# --------------------------------------------------------------- architecture
def architecture():
    fig, ax = plt.subplots(figsize=(11, 5.4))
    ax.set_xlim(-2, 107)
    ax.set_ylim(-7, 57)
    ax.axis("off")

    def box(x, y, w, h, title, sub, color):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.5",
                                    lw=1.7, ec=color, fc="white"))
        ax.text(x + w / 2, y + h - 2.2, title, ha="center", va="top", fontsize=10.5, weight="bold", color=color)
        ax.text(x + w / 2, y + h - 6.2, sub, ha="center", va="top", fontsize=8.2, color=INK, linespacing=1.4)

    def arrow(a, b, color=INK, ls="-"):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=13, lw=1.4, color=color, linestyle=ls))

    def label(x, y, text, color, ha="center"):
        ax.text(x, y, text, fontsize=7.8, color=color, ha=ha, va="center",
                bbox=dict(fc="white", ec="none", pad=0.8))

    # top row: client, API, workers; bottom row: autoscaler, Redis, object storage
    box(0, 33, 20, 18, "Client", "web UI, curl,\nload generator", GREY)
    box(36, 31, 26, 22, "API (FastAPI)", "decode + partition\ncost-aware planner:\nlocal vs distributed,\ntile size, workers", BLUE)
    box(76, 31, 26, 22, "Worker pods ×N", "claim (lease) → GET tile\n→ OpenCV / ONNX op\n→ PUT result → complete;\nlast tile stitches", GREEN)
    box(0, 0, 20, 20, "Autoscaler", "work-aware:\nn = max(⌈W/(D·u)⌉, ⌈λ/u⌉)\npatches the worker\nDeployment", PURPLE)
    box(36, 0, 26, 20, "Redis (control only)", "task ids, tile specs, leases,\nheartbeats, work_seconds,\nonline c_op profiles", RED)
    box(76, 0, 26, 20, "Object storage", "S3 (AWS) / MinIO: input,\ntiles, results, output;\nSSE-KMS, private, lifecycle", ORANGE)

    arrow((20.5, 45), (35.5, 45)); label(28, 47.3, "POST /jobs", INK)
    arrow((35.5, 38), (20.5, 38), MUTED); label(28, 35.6, "307 → presigned\nS3 URL", MUTED)
    arrow((44, 30.5), (44, 20.5), RED); label(44, 25.5, "task ids + costs", RED)
    arrow((62.5, 34), (77, 20.5), ORANGE); label(66.5, 25.2, "PUT input\n+ tiles", ORANGE, "left")
    arrow((76.5, 35), (56, 20.5), RED); label(52.5, 23.2, "claim / complete", RED)
    arrow((89, 30.5), (89, 20.5), ORANGE); label(89, 25.5, "GET tile / PUT result", ORANGE)
    arrow((20.5, 10), (35.5, 10), PURPLE); label(28, 12.3, "read W, λ", PURPLE)
    # scale arrow routed under the bottom row and up the right edge
    ax.plot([10, 10, 105, 105], [-0.5, -5, -5, 42], color=PURPLE, lw=1.4)
    arrow((105, 42), (102.4, 42), PURPLE)
    label(56, -5, "scale replicas (patch Deployment)", PURPLE)
    save(fig, "fig_architecture_v2.png")


# ------------------------------------------------------------ motivation
def motivation():
    rows = list(csv.DictReader(open(RES / "results.csv")))
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    for op, color in (("bilateral", BLUE), ("canny", RED), ("gaussian_blur", ORANGE)):
        pts = [(int(r["workers"]), float(r["speedup"])) for r in rows if r["op"] == op and r["mode"] == "parallel"]
        ax.plot(*zip(*pts), "o-", color=color, label=op.replace("_", " "))
    ax.axhline(1, color=INK, lw=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8, 16], ["1", "2", "4", "8", "16"])
    ax.set_xlabel("worker processes")
    ax.set_ylabel("speedup vs sequential (×)")
    ax.set_title("Original framework, naive 512 px tiling (8192², results.csv)", fontsize=9.5)
    ax.legend(frameon=False)
    ax.grid(alpha=.3)
    save(fig, "fig_motivation.png")


# ------------------------------------------------------------ capacity
def capacity():
    prof = json.loads((ROOT / "imgframe" / "profiles.json").read_text())
    cap = prof["capacity"]
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ns = [1, 16]
    ax.plot(ns, ns, ":", color=MUTED, label="ideal (linear)")
    for op, color in (("bilateral", BLUE), ("hed", GREEN), ("superres", PURPLE), ("canny", RED)):
        if op in cap:
            pts = sorted((int(k), v) for k, v in cap[op].items())
            ax.plot(*zip(*pts), "o-", color=color, label=op)
    ax.set_xlabel("concurrently busy workers n")
    ax.set_ylabel("throughput S(n) relative to one")
    ax.set_title(f"Measured capacity curves ({prof['meta']['cpus']}-thread host)", fontsize=9.5)
    ax.legend(frameon=False, fontsize=8.5)
    ax.grid(alpha=.3)
    save(fig, "fig_capacity.png")


# ------------------------------------------------------------ scaling
def scaling():
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.plot([1, 16], [1, 16], ":", color=MUTED, label="linear")
    series = [("bilateral", RES / "results.csv", BLUE), ("canny", RES / "results.csv", RED),
              ("hed", RES / "neural_hed" / "results.csv", GREEN),
              ("superres", RES / "neural_superres" / "results.csv", PURPLE)]
    for op, path, color in series:
        if path.exists():
            rows = [r for r in csv.DictReader(open(path)) if r["op"] == op and r["mode"] == "parallel"]
            ax.plot([int(r["workers"]) for r in rows], [float(r["speedup"]) for r in rows], "o-", color=color,
                    label=op)
    ax.axhline(1, color=INK, lw=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8, 16], ["1", "2", "4", "8", "16"])
    ax.set_ylim(0, 8)
    ax.set_xlabel("worker processes (512 px tiles)")
    ax.set_ylabel("speedup vs sequential (×)")
    ax.set_title("Measured speedup: neural ops saturate early", fontsize=9.5)
    ax.legend(frameon=False, fontsize=8.5)
    ax.grid(alpha=.3)
    save(fig, "fig_scaling.png")


# ------------------------------------------------------------ neural ops
def neural_samples():
    from imgframe.pipeline import run_sequential, run_tiled

    src = cv2.imread(str(ROOT / "samples" / "astronaut_2048.png"))
    img = cv2.resize(src, (768, 768), interpolation=cv2.INTER_AREA)
    canny = run_sequential(img, "canny")
    whole = run_sequential(img, "hed")
    hed = run_tiled(img, "hed", 256, 1)
    naive = run_tiled(img, "hed", 256, 1, halo=0)
    err_naive = np.clip(np.abs(naive.astype(int) - whole) * 4, 0, 255)
    err_cal = np.clip(np.abs(hed.astype(int) - whole) * 4, 0, 255)
    fig, axes = plt.subplots(1, 5, figsize=(15, 3.4))
    for a, im, t in zip(axes, [img[..., ::-1], canny, hed, err_naive, err_cal],
                        ["input", "Canny (classical)", "HED, tiled (calibrated halo)",
                         "|error| ×4, no halo", "|error| ×4, calibrated halo"]):
        a.imshow(im, cmap=None if im.ndim == 3 else ("magma" if "error" in t else "gray"), vmin=0, vmax=255)
        a.set_title(t, fontsize=10)
        a.axis("off")
    save(fig, "fig_canny_vs_hed.png")

    crop = cv2.resize(src, (512, 512), interpolation=cv2.INTER_AREA)[150:278, 170:298]
    sr = run_tiled(crop, "superres", 64, 1)
    bic = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.6))
    for a, im, t in zip(axes, [crop, bic, sr], ["input 128×128", "bicubic ×4", "Real-ESRGAN ×4, tiled"]):
        a.imshow(im[..., ::-1], interpolation="nearest")
        a.set_title(t, fontsize=10)
        a.axis("off")
    save(fig, "fig_superres.png")


# ------------------------------------------------------------ halo
def halo():
    res = load("halo_calibration.json")
    if not res:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    for ax, (op, r) in zip(axes, res.items()):
        hs = [x["halo"] for x in r["sweep"]]
        ps = [min(x["seam_psnr_db"], 70) for x in r["sweep"]]
        ax.plot(hs, ps, "o-", color=BLUE, label="seam PSNR vs whole image")
        ax.axhline(r["criterion"]["seam_psnr_min_db"], color=BLUE, ls=":", lw=1)
        ax.set_ylabel("seam PSNR (dB, capped at 70)", color=BLUE)
        ax.set_xlabel("halo (px)")
        ax2 = ax.twinx()
        ax2.plot(hs, [100 * x["overhead_measured"] for x in r["sweep"]], "s--", color=ORANGE,
                 label="extra compute (measured)")
        ax2.set_ylabel("extra compute vs halo 0 (%)", color=ORANGE)
        ax2.spines["right"].set_visible(True)
        ax.axvline(r["halo"], color=GREEN, lw=1.6)
        ax.set_title(f"{op}: calibrated halo {r['halo']} px (receptive field {r['theoretical_halo']} px)",
                     fontsize=9.5)
        ax.grid(alpha=.3)
    save(fig, "fig_halo.png")


# ------------------------------------------------------------ planner
def planner_fig():
    res = load("planner_engine.json")
    if not res:
        return
    rows = res["rows"]
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    x = np.arange(len(rows))
    w = 0.27
    for off, key, label, color in ((-w, "naive_speedup", "naive: 512 px, all workers", GREY),
                                   (0, "fixed512_speedup", "planner, tile fixed at 512", "#93c5fd"),
                                   (w, "plan_speedup", "planner", BLUE)):
        bars = ax.bar(x + off, [r[key] for r in rows], w, label=label, color=color)
        ax.bar_label(bars, fmt="%.2f", fontsize=6.5, padding=1)
    ax.axhline(1, color=INK, lw=0.8)
    ax.set_xticks(x, [r["op"].replace("_", "\n") for r in rows], fontsize=8.5)
    ax.set_ylabel("speedup over sequential (×)")
    ax.set_title(f"In-memory engine, {res['workers']} workers available", fontsize=9.5)
    ax.legend(frameon=False, fontsize=8)
    save(fig, "fig_planner.png")

    sc = res["scatter"]
    fig, ax = plt.subplots(figsize=(4.6, 4.0))
    ops = sorted({s["op"] for s in sc})
    cmap = plt.get_cmap("tab10")
    for i, op in enumerate(ops):
        pts = [s for s in sc if s["op"] == op]
        ax.scatter([s["predicted_s"] for s in pts], [s["actual_s"] for s in pts], s=18, color=cmap(i), label=op)
    lo = min(min(s["predicted_s"], s["actual_s"]) for s in sc) * 0.7
    hi = max(max(s["predicted_s"], s["actual_s"]) for s in sc) * 1.4
    ax.plot([lo, hi], [lo, hi], "--", color=MUTED, lw=1)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("predicted (s)")
    ax.set_ylabel("measured (s)")
    ax.set_title(f"Cost model: MAPE {res['mape_pct']:.0f}% ({len(sc)} runs)", fontsize=9.5)
    ax.legend(frameon=False, fontsize=7)
    ax.grid(alpha=.3, which="both")
    save(fig, "fig_model_accuracy.png")


# ------------------------------------------------------------ storage
def storage():
    res = load("storage_study.json")
    if not res:
        return
    r, s = res
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.2))
    labels = ["Redis mode", "S3 mode"]
    vals = [r["redis_bytes_per_8192_job"] / 2**20, s["redis_bytes_per_8192_job"] / 2**20]
    b = axes[0].bar(labels, vals, color=[RED, ORANGE])
    axes[0].bar_label(b, labels=[f"{v:.1f} MB" if v >= 1 else f"{v * 1024:.0f} KB" for v in vals], fontsize=8)
    axes[0].set_yscale("log")
    axes[0].set_title("Redis memory per 8192² job", fontsize=9.5)
    vals = [r["concurrent_8192_jobs_accepted"], s["concurrent_8192_jobs_accepted"]]
    b = axes[1].bar(labels, vals, color=[RED, ORANGE])
    axes[1].bar_label(b, labels=[f"{v}" + ("" if not x["concurrent_limit_hit"] else " (then 503)")
                                 for v, x in zip(vals, res)], fontsize=8)
    axes[1].set_title(f"8192² jobs queued before HTTP 503\n(Redis maxmemory {r['redis_maxmemory']})", fontsize=9.5)
    vals = [r["io_ms_per_tile"], s["io_ms_per_tile"]]
    b = axes[2].bar(labels, vals, color=[RED, ORANGE])
    axes[2].bar_label(b, fmt="%.1f ms", fontsize=8)
    axes[2].set_title("Tile I/O per 512 px tile\n(fetch + store, per worker)", fontsize=9.5)
    save(fig, "fig_storage.png")


# ------------------------------------------------------------ autoscaling
def autoscale():
    d = RES / "autoscale"
    if not d.exists():
        return
    runs = {}
    for f in sorted(d.glob("*_run*.json")):
        j = json.loads(f.read_text())
        runs.setdefault(j["summary"]["label"], []).append(j)
    order = [k for k in ("hpa", "keda", "work") if k in runs]
    if not order:
        return
    names = {"hpa": "HPA (CPU 70%)", "keda": "KEDA (queue length)", "work": "Work-aware (ours)"}
    colors = {"hpa": GREY, "keda": ORANGE, "work": BLUE}
    fig, axes = plt.subplots(len(order), 1, figsize=(10, 2.3 * len(order)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, k in zip(axes, order):
        j = runs[k][0]
        t = [s["t"] for s in j["samples"]]
        ax.step(t, [s["spec"] for s in j["samples"]], where="post", color=colors[k], lw=1.8, label="replicas")
        ax.set_ylabel("replicas")
        ax.set_ylim(0, 9)
        ax2 = ax.twinx()
        ax2.fill_between(t, [s["work_s"] for s in j["samples"]], color=GREEN, alpha=0.18, step="post",
                         label="queued work (s)")
        ax2.set_ylabel("queued work (s)", color=GREEN)
        ax2.spines["right"].set_visible(True)
        for job in j["trace"]:
            ax.axvline(job["t"], color=RED if job["op"] in ("bilateral", "hed") else MUTED, lw=0.5, alpha=0.5,
                       ymax=0.06)
        sm = j["summary"]
        ax.set_title(f"{names[k]}: p95 {sm['p95_s']} s, {sm['within_target_pct']}% within target, "
                     f"{sm['pod_seconds']:.0f} pod-s", fontsize=9.5, loc="left")
    axes[-1].set_xlabel("time (s); ticks at the bottom = job arrivals (red = expensive)")
    save(fig, "fig_autoscale.png")


if __name__ == "__main__":
    which = sys.argv[1:] or ["architecture", "motivation", "capacity", "scaling", "neural_samples", "halo",
                             "planner_fig", "storage", "autoscale"]
    for name in which:
        globals()[name]()
