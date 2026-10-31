"""Automatic halo calibration for neural operations (Upgrade 2).

A CNN output pixel depends on its whole receptive field, so the exact halo
(HED: ~98 px, Real-ESRGAN general: 34 px) nearly doubles the work per tile.
Most of the influence comes from a much smaller effective receptive field
(Luo et al., 2016). This script measures, for each model, how the tiled output
deviates from whole-image inference as the halo grows, and picks the smallest
halo whose error is under the threshold:

    seam PSNR >= --psnr dB   (pixels within 4 px of a tile border)
    and 99.9th-percentile |difference| <= --p999 grey levels

It writes imgframe/calibration.json (read by imgframe/ops.py) and
benchmark/results/halo_calibration.{json,png}.

    python scripts/calibrate_halo.py                 # both models
    python scripts/calibrate_halo.py --ops hed --size 1024
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imgframe.ops import get_op  # noqa: E402
from imgframe.pipeline import run_sequential, run_tiled  # noqa: E402
from imgframe.tiling import plan_tiles  # noqa: E402

SWEEPS = {
    "hed": [0, 4, 8, 16, 24, 32, 48, 64, 98],
    "superres": [0, 2, 4, 6, 8, 10, 12, 16, 24, 34],
}
SIZES = {"hed": 1024, "superres": 512}       # input side; superres output is 4x larger


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def seam_mask(h: int, w: int, tile: int, scale: int, band: int = 4) -> np.ndarray:
    m = np.zeros((h * scale, w * scale), bool)
    for s in plan_tiles(h, w, tile, 0):
        for y in (s.y0, s.y1):
            if 0 < y < h:
                m[max(0, (y - band) * scale) : (y + band) * scale, :] = True
        for x in (s.x0, s.x1):
            if 0 < x < w:
                m[:, max(0, (x - band) * scale) : (x + band) * scale] = True
    return m


def calibrate(op: str, img: np.ndarray, tile: int, psnr_min: float, p999_max: float) -> dict:
    o = get_op(op)
    h, w = img.shape[:2]
    t0 = time.perf_counter()
    full = run_sequential(img, op)
    t_full = time.perf_counter() - t0
    mask = seam_mask(h, w, tile, o.scale)
    rows = []
    for halo in SWEEPS[op]:
        t0 = time.perf_counter()
        tiled = run_tiled(img, op, tile, workers=1, halo=halo)
        t = time.perf_counter() - t0
        diff = np.abs(full.astype(np.int16) - tiled)
        row = {"halo": halo, "psnr_db": round(psnr(full, tiled), 2),
               "seam_psnr_db": round(psnr(full[mask], tiled[mask]), 2),
               "max_abs": int(diff.max()), "p999_abs": float(np.percentile(diff, 99.9)),
               "mean_abs": round(float(diff.mean()), 4),
               "overhead_theory": round(((tile + 2 * halo) ** 2) / tile ** 2 - 1, 3),
               "time_s": round(t, 3)}
        rows.append(row)
        print(f"[{op}] halo {halo:3d}: seam PSNR {row['seam_psnr_db']:6.2f} dB, PSNR {row['psnr_db']:6.2f} dB, "
              f"p99.9 |d| {row['p999_abs']:5.1f}, max |d| {row['max_abs']:3d}, "
              f"theory +{row['overhead_theory']:.0%}, time {t:.2f}s")
    t0_ = rows[0]["time_s"]
    for r in rows:
        r["overhead_measured"] = round(r["time_s"] / t0_ - 1, 3)
    ok = [r for r in rows if r["seam_psnr_db"] >= psnr_min and r["p999_abs"] <= p999_max]
    chosen = ok[0] if ok else rows[-1]
    print(f"[{op}] chosen halo {chosen['halo']} px (theoretical {o.theoretical_halo} px): "
          f"overhead {chosen['overhead_theory']:.0%} vs {((tile + 2 * o.theoretical_halo) ** 2) / tile ** 2 - 1:.0%}\n")
    return {"halo": chosen["halo"], "theoretical_halo": o.theoretical_halo, "tile_size": tile,
            "image": f"{w}x{h}", "whole_image_s": round(t_full, 3),
            "criterion": {"seam_psnr_min_db": psnr_min, "p999_abs_max": p999_max}, "sweep": rows}


def plot(results: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(results), figsize=(6.2 * len(results), 4.2), squeeze=False)
    for ax, (op, res) in zip(axes[0], results.items()):
        halos = [r["halo"] for r in res["sweep"]]
        ax.plot(halos, [r["seam_psnr_db"] for r in res["sweep"]], "o-", color="#2563eb",
                label="seam PSNR (dB)")
        ax.axhline(res["criterion"]["seam_psnr_min_db"], color="#2563eb", ls=":", lw=1)
        ax.set_xlabel("halo (px)")
        ax.set_ylabel("seam PSNR vs whole image (dB)", color="#2563eb")
        ax2 = ax.twinx()
        ax2.plot(halos, [100 * r["overhead_measured"] for r in res["sweep"]], "s--", color="#d97706",
                 label="compute overhead (measured)")
        ax2.plot(halos, [100 * r["overhead_theory"] for r in res["sweep"]], ":", color="#d97706", lw=1,
                 label="(s+2h)²/s² − 1")
        ax2.set_ylabel("extra compute (%)", color="#d97706")
        ax.axvline(res["halo"], color="#16a34a", lw=1.5)
        ax.set_title(f"{op}: chosen halo {res['halo']} px (theoretical {res['theoretical_halo']} px), "
                     f"{res['tile_size']} px tiles", fontsize=10)
        lines = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
        labels = ax.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
        ax.legend(lines, labels, loc="center right", fontsize=8)
        ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ops", nargs="+", default=list(SWEEPS))
    ap.add_argument("--image", default=str(ROOT / "samples" / "astronaut_2048.png"))
    ap.add_argument("--size", type=int, help="input side (default: 1024 for hed, 512 for superres)")
    ap.add_argument("--tile-size", type=int, default=256, help="smallest planner tile = most seams")
    ap.add_argument("--psnr", type=float, default=40.0)
    ap.add_argument("--p999", type=float, default=8.0)
    args = ap.parse_args()

    src = cv2.imread(args.image)
    calib_path = ROOT / "imgframe" / "calibration.json"
    try:
        calib = json.loads(calib_path.read_text())
    except OSError:
        calib = {}
    results = {}
    for op in args.ops:
        n = args.size or SIZES[op]
        img = cv2.resize(src, (n, n), interpolation=cv2.INTER_AREA)
        results[op] = calibrate(op, img, args.tile_size, args.psnr, args.p999)
        calib[op] = {"halo": results[op]["halo"], "theoretical_halo": results[op]["theoretical_halo"],
                     "criterion": results[op]["criterion"]}
    calib_path.write_text(json.dumps(calib, indent=2) + "\n")
    out = ROOT / "benchmark" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "halo_calibration.json").write_text(json.dumps(results, indent=2))
    plot(results, out / "halo_calibration.png")
    print(f"wrote {calib_path.relative_to(ROOT)}, {out.relative_to(ROOT)}/halo_calibration.json and .png")


if __name__ == "__main__":
    main()
