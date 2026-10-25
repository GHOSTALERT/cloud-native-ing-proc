"""Charts for benchmark results: time, speedup vs ideal, CPU utilization."""
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLORS = ["#2563eb", "#ea580c", "#16a34a", "#9333ea"]


def plot(rows: list[dict], out: Path, subtitle: str, name: str = "charts.png") -> None:
    ops = list(dict.fromkeys(r["op"] for r in rows))
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))
    ax_t, ax_s, ax_c = axes
    max_w = 1
    for i, op in enumerate(ops):
        par = [r for r in rows if r["op"] == op and r["mode"] == "parallel"]
        seq = next((r for r in rows if r["op"] == op and r["mode"] == "sequential"), None)
        ws = [r["workers"] for r in par]
        max_w = max(max_w, *ws)
        c = COLORS[i % len(COLORS)]
        ax_t.plot(ws, [r["time_s"] for r in par], "o-", color=c, label=f"{op} parallel")
        if seq:
            ax_t.axhline(seq["time_s"], color=c, ls="--", lw=1, label=f"{op} sequential")
        ax_s.plot(ws, [r["speedup"] for r in par], "o-", color=c, label=op)
        ax_c.plot(ws, [r["cores_busy_avg"] for r in par], "o-", color=c, label=op)
    ideal = [w for w in (1, 2, 4, 8, 16, 32, 64) if w <= max_w]
    ax_s.plot(ideal, ideal, ":", color="gray", label="ideal (linear)")
    ax_c.plot(ideal, ideal, ":", color="gray", label="all workers busy")
    for ax, title, ylabel in ((ax_t, "Processing time", "seconds (median)"),
                              (ax_s, "Speedup vs sequential", "speedup (×)"),
                              (ax_c, "CPU utilization", "cores busy (avg)")):
        ax.set_title(title)
        ax.set_xlabel("parallel workers")
        ax.set_ylabel(ylabel)
        ax.set_xscale("log", base=2)
        ax.set_xticks(ideal, [str(w) for w in ideal])
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    ax_t.set_yscale("log")
    ax_t.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%g"))
    ax_t.yaxis.set_minor_formatter(matplotlib.ticker.FormatStrFormatter("%g"))
    fig.suptitle(subtitle)
    fig.tight_layout()
    fig.savefig(Path(out) / name, dpi=120)
    plt.close(fig)


def plot_distributed(rows: list[dict], path: Path, title: str) -> None:
    """Phase breakdown (stacked) + end-to-end vs parallel-phase speedup."""
    seq = next(r for r in rows if r["mode"] == "sequential")
    dist = [r for r in rows if r["mode"] == "distributed"]
    labels = ["seq"] + [str(r["workers"]) for r in dist]
    fig, (ax_b, ax_s) = plt.subplots(1, 2, figsize=(13, 4.6))
    part = [0] + [r["partition_s"] for r in dist]
    proc = [seq["time_s"]] + [r["processing_s"] for r in dist]
    agg = [0] + [r["aggregate_s"] for r in dist]
    other = [0] + [max(0.0, r["time_s"] - r["partition_s"] - r["processing_s"] - r["aggregate_s"])
                   for r in dist]
    bottom = [0.0] * len(labels)
    for vals, lab, c in ((part, "upload decode + partition (serial)", "#94a3b8"),
                         (proc, "parallel processing", COLORS[0]),
                         (agg, "aggregation (serial)", COLORS[1]),
                         (other, "HTTP / polling overhead", "#e2e8f0")):
        ax_b.bar(labels, vals, bottom=bottom, label=lab, color=c)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax_b.set_title("Where the time goes")
    ax_b.set_xlabel("workers")
    ax_b.set_ylabel("seconds")
    ax_b.legend(fontsize=8)
    ws = [r["workers"] for r in dist]
    ax_s.plot(ws, [r["processing_speedup"] for r in dist], "o-", color=COLORS[0], label="parallel phase")
    ax_s.plot(ws, [r["speedup"] for r in dist], "o-", color=COLORS[1], label="end-to-end")
    ax_s.plot(ws, ws, ":", color="gray", label="ideal (linear)")
    ax_s.axhline(1, color="gray", lw=0.8)
    ax_s.set_xscale("log", base=2)
    ax_s.set_xticks(ws, [str(w) for w in ws])
    ax_s.set_title("Speedup vs sequential")
    ax_s.set_xlabel("workers")
    ax_s.set_ylabel("speedup (×)")
    ax_s.grid(alpha=0.3)
    ax_s.legend(fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
