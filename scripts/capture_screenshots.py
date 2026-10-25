"""Capture the screenshots and figures used in the project report.

Starts a real local stack (redis-server + API + workers), drives the web UI
with headless Firefox (Selenium), runs the demos/tests/benchmarks and renders
their real terminal output as images.

    python scripts/capture_screenshots.py              # everything
    python scripts/capture_screenshots.py --skip-bench # reuse benchmark logs

Output: docs/screenshots/*.png, docs/logs/*.txt, samples/*.png
"""
import argparse
import html
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import run_local  # noqa: E402
from imgframe.ops import get_op  # noqa: E402
from imgframe.sample import synthetic_image  # noqa: E402
from imgframe.tiling import plan_tiles  # noqa: E402

SHOTS = ROOT / "docs" / "screenshots"
LOGS = ROOT / "docs" / "logs"
SAMPLES = ROOT / "samples"
PY = sys.executable


# --------------------------------------------------------------- browser
def browser(width=1400, height=1000):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service

    # Snap Firefox can only see its own dirs, so keep Selenium's profile there.
    snap_tmp = Path.home() / "snap" / "firefox" / "common" / "tmp"
    if snap_tmp.parent.exists():
        snap_tmp.mkdir(exist_ok=True)
        os.environ["TMPDIR"] = str(snap_tmp)
    opts = Options()
    opts.add_argument("-headless")
    snap_bin = Path("/snap/firefox/current/usr/lib/firefox/firefox")
    if snap_bin.exists():
        opts.binary_location = str(snap_bin)
    gecko = "/snap/bin/firefox.geckodriver" if Path("/snap/bin/firefox.geckodriver").exists() else None
    driver = webdriver.Firefox(options=opts, service=Service(gecko) if gecko else None)
    driver.set_window_size(width, height)
    return driver


def shot(driver, name: str, full_page: bool = True) -> None:
    path = SHOTS / f"{name}.png"
    if full_page:
        driver.get_full_page_screenshot_as_file(str(path))
    else:
        driver.save_screenshot(str(path))
    print("  saved", path.relative_to(ROOT))


def wait_for(fn, timeout=120, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError("condition not met")


# ------------------------------------------------------------- terminal
def clean(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return "\n".join(line.split("\r")[-1] for line in text.splitlines())


def run_cmd(cmd: str, log_name: str, timeout=1800, env=None) -> str:
    print(f"$ {cmd}")
    p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    out = clean(p.stdout + p.stderr)
    (LOGS / f"{log_name}.txt").write_text(f"$ {cmd}\n{out}")
    return out


def _trim(output: str, max_lines: int) -> list[str]:
    lines = output.rstrip().splitlines()
    if len(lines) > max_lines:
        lines = lines[: max_lines - 6] + ["  [...]"] + lines[-5:]
    return lines


def terminal_image(driver, name: str, cmd: str, output: str, max_lines=70, width=1100) -> None:
    terminal_multi(driver, name, [(cmd, output)], max_lines=max_lines, width=width)


def terminal_multi(driver, name: str, segments: list[tuple[str, str]], max_lines=70,
                   width=1100) -> None:
    """Render one or more `$ command` + output blocks as a terminal window image."""
    parts = []
    for cmd, output in segments:
        body = html.escape("\n".join(_trim(output, max_lines)))
        body = re.sub(r"(\d+ passed)", r'<span class="ok">\1</span>', body)
        body = re.sub(r"(PASSED|Running|scaling up.*|scaling down.*)", r'<span class="ok">\1</span>', body)
        body = re.sub(r"(!!.*|Terminating|ContainerCreating|Pending|Error|deleted|force deletion.*)",
                      r'<span class="warn">\1</span>', body)
        parts.append(f'<span class="cmd">$ {html.escape(cmd)}</span>\n{body}')
    page = f"""<html><head><meta charset="utf-8"><style>
      body {{ margin:0; background:#1e1e2e; }}
      .win {{ margin:0; font:13px/1.45 "DejaVu Sans Mono", "Ubuntu Mono", monospace; color:#cdd6f4; }}
      .bar {{ background:#313244; padding:8px 12px; color:#a6adc8; font:12px sans-serif; }}
      .bar i {{ display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:6px; }}
      pre {{ margin:0; padding:14px 16px; white-space:pre-wrap; word-break:break-all; font:inherit; }}
      .cmd {{ color:#a6e3a1; }} .ok {{ color:#a6e3a1; font-weight:bold; }} .warn {{ color:#f9e2af; font-weight:bold; }}
    </style></head><body><div class="win">
      <div class="bar"><i style="background:#f38ba8"></i><i style="background:#f9e2af"></i><i style="background:#a6e3a1"></i>
      &nbsp; ~/Downloads/cloud proj</div>
      <pre>{"\n\n".join(parts)}</pre></div></body></html>"""
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"term_{name}.html"
    tmp.write_text(page)
    driver.set_window_size(width, 600)
    driver.get(tmp.as_uri())
    shot(driver, name)


# -------------------------------------------------------------- figures
def make_samples() -> tuple[Path, Path]:
    SAMPLES.mkdir(exist_ok=True)
    photo = SAMPLES / "astronaut_2048.png"
    if not photo.exists():
        from skimage import data  # public-domain NASA photo bundled with scikit-image
        img = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(photo), cv2.resize(img, (2048, 2048), interpolation=cv2.INTER_CUBIC))
    big = SAMPLES / "synthetic_8192.png"
    if not big.exists():
        cv2.imwrite(str(big), synthetic_image(8192, 8192), [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return photo, big


def tiling_figure(photo: Path) -> None:
    """Visualise partitioning: tile cores (green) and one tile's halo (orange)."""
    img = cv2.resize(cv2.imread(str(photo)), (1024, 1024), interpolation=cv2.INTER_AREA)
    scale = 1024 / 2048
    specs = plan_tiles(2048, 2048, 256, get_op("canny").halo * 3)  # halo exaggerated to be visible
    out = img.copy()
    for s in specs:
        cv2.rectangle(out, (int(s.x0 * scale), int(s.y0 * scale)),
                      (int(s.x1 * scale) - 1, int(s.y1 * scale) - 1), (80, 220, 80), 2)
        cv2.putText(out, str(s.index), (int(s.x0 * scale) + 6, int(s.y0 * scale) + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 220, 80), 2)
    s = specs[27]
    cv2.rectangle(out, (int(s.hx0 * scale), int(s.hy0 * scale)),
                  (int(s.hx1 * scale), int(s.hy1 * scale)), (0, 140, 255), 3)
    cv2.imwrite(str(SHOTS / "fig_tiling.png"), out)
    print("  saved docs/screenshots/fig_tiling.png")


def result_figures(photo: Path) -> None:
    from imgframe.pipeline import run_tiled

    def label(img, text):
        img = img.copy()
        cv2.putText(img, text, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5)
        cv2.putText(img, text, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        return img

    img = cv2.resize(cv2.imread(str(photo)), (1024, 1024), interpolation=cv2.INTER_AREA)
    cells = [label(cv2.resize(img, (512, 512), interpolation=cv2.INTER_AREA), "original")]
    for op in ("grayscale", "canny", "sobel", "bilateral", "sharpen"):
        out = run_tiled(img, op, 256, workers=4)            # tiled + parallel, then stitched
        if op == "canny":
            out = cv2.dilate(out, np.ones((2, 2), np.uint8))  # thicken 1-px edges for print
        if out.ndim == 2:
            out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
        cells.append(label(cv2.resize(out, (512, 512), interpolation=cv2.INTER_AREA), op))
    grid = np.vstack([np.hstack(cells[:3]), np.hstack(cells[3:])])
    cv2.imwrite(str(SHOTS / "fig_operations.png"), grid)
    # Full-resolution Canny around tile boundaries (red lines): no seams at the joins.
    full = cv2.imread(str(photo))
    edges = run_tiled(full, "canny", 256, workers=4)
    crop = cv2.cvtColor(edges[512:1280, 640:1408], cv2.COLOR_GRAY2BGR)
    for x in (768 - 640, 1024 - 640, 1280 - 640):
        cv2.line(crop, (x, 0), (x, crop.shape[0]), (0, 0, 255), 1)
    for y in (768 - 512, 1024 - 512):
        cv2.line(crop, (0, y), (crop.shape[1], y), (0, 0, 255), 1)
    cv2.imwrite(str(SHOTS / "fig_seamless.png"), crop)
    print("  saved docs/screenshots/fig_operations.png, fig_seamless.png")


def architecture_figure(driver) -> None:
    svg = (ROOT / "docs" / "architecture.svg").read_text()
    page = f'<html><body style="margin:0;background:#fff">{svg}</body></html>'
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "arch.html"
    tmp.write_text(page)
    driver.set_window_size(1300, 700)
    driver.get(tmp.as_uri())
    shot(driver, "fig_architecture")


# ------------------------------------------------------------- web UI
def ui_shots(driver, photo: Path, big: Path) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    redis_url = run_local.start_redis(6500)
    api, workers, url = run_local.launch(4, 8200, redis_url,
                                         extra_env={"LEASE_SECONDS": "5", "HEARTBEAT_SECONDS": "1"})
    try:
        driver.set_window_size(1400, 1000)
        driver.get(url + "/")
        wait_for(lambda: "Active workers" in driver.page_source and
                 driver.find_element(By.ID, "workers").text.count("worker-") == 4)
        time.sleep(1)
        shot(driver, "ui_01_idle", full_page=False)

        def submit(path: Path, op: str):
            driver.find_element(By.ID, "file").send_keys(str(path))
            Select(driver.find_element(By.ID, "op")).select_by_value(op)
            driver.find_element(By.ID, "go").click()

        def status():
            return driver.find_element(By.ID, "status").text

        def tiles_done():
            m = re.search(r"(\d+) / (\d+) tiles", status())
            return (int(m.group(1)), int(m.group(2))) if m else (0, 1)

        # 1) Canny on a real photo (256 px tiles -> 64 tiles)
        tile_input = driver.find_element(By.NAME, "tile_size")
        tile_input.clear()
        tile_input.send_keys("256")
        submit(photo, "canny")
        wait_for(lambda: "done" in status() and driver.execute_script(
            "const i=document.getElementById('res'); return i.complete && i.naturalWidth > 0"))
        time.sleep(1.2)
        shot(driver, "ui_02_canny_done")

        # 2) Large CPU-heavy job: catch it mid-flight, then finished
        tile_input.clear()
        tile_input.send_keys("512")
        submit(big, "bilateral")
        wait_for(lambda: 0.25 < (lambda d, t: d / t)(*tiles_done()) < 0.8, interval=0.05)
        shot(driver, "ui_03_bilateral_running", full_page=False)
        wait_for(lambda: "done" in status(), timeout=300)
        time.sleep(1.5)
        shot(driver, "ui_04_bilateral_done")

        # 3) Fault tolerance: SIGKILL a worker while it holds a tile
        submit(big, "bilateral")
        wait_for(lambda: tiles_done()[0] >= 40, interval=0.05)
        workers[0].send_signal(signal.SIGKILL)
        print("  killed worker-0 (SIGKILL) mid-job")
        wait_for(lambda: "done" in status(), timeout=300)
        wait_for(lambda: "dead" in driver.find_element(By.ID, "workers").get_attribute("innerHTML"),
                 timeout=30)
        time.sleep(1.2)
        shot(driver, "ui_05_fault_recovered")

        # 4) API docs (Swagger UI) and raw JSON endpoints
        driver.get(url + "/docs")
        try:
            wait_for(lambda: "POST" in driver.page_source and "/jobs" in driver.page_source, timeout=20)
            time.sleep(1.5)
            shot(driver, "ui_06_api_docs", full_page=False)
        except TimeoutError:
            print("  (skipped API docs: Swagger UI assets need internet)")
        driver.get(url + "/metrics")
        time.sleep(1.5)
        shot(driver, "ui_07_metrics_json", full_page=False)
    finally:
        run_local.stop(workers + [api])
        run_local.stop_redis()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-bench", action="store_true", help="reuse docs/logs/bench_*.txt")
    ap.add_argument("--only", choices=["ui", "terminal", "figures"], help="capture one group only")
    args = ap.parse_args()
    for d in (SHOTS, LOGS):
        d.mkdir(parents=True, exist_ok=True)

    photo, big = make_samples()
    driver = browser()
    try:
        if args.only in (None, "figures"):
            print("figures")
            tiling_figure(photo)
            result_figures(photo)
            architecture_figure(driver)
        if args.only in (None, "ui"):
            print("web UI")
            ui_shots(driver, photo, big)
        if args.only in (None, "terminal"):
            print("terminal")
            cmds = [
                ("term_01_tests", ".venv/bin/python -m pytest -v -p no:warnings", 70),
                ("term_02_demo", ".venv/bin/python scripts/run_local.py --workers 4 --demo "
                                 "--op canny --image samples/astronaut_2048.png --tile-size 256 --port 8201 --redis-port 6501", 40),
                ("term_03_fault", ".venv/bin/python scripts/run_local.py --workers 4 --demo --kill-worker "
                                  "--op bilateral --size 4096 --port 8202 --redis-port 6502", 40),
            ]
            if not args.skip_bench:
                cmds += [
                    ("term_04_bench_local", ".venv/bin/python -m benchmark.bench_local --size 8192 "
                                            "--ops bilateral canny gaussian_blur --workers 1 2 4 8 16", 40),
                    ("term_05_bench_distributed", ".venv/bin/python -m benchmark.bench_distributed "
                                                  "--workers 1 2 4 8 --op bilateral --size 8192", 40),
                ]
            for name, cmd, lines in cmds:
                out = run_cmd(cmd, name)
                terminal_image(driver, name, cmd, out, max_lines=lines)
            if args.skip_bench:
                for name in ("term_04_bench_local", "term_05_bench_distributed"):
                    log = LOGS / f"{name}.txt"
                    if log.exists():
                        cmd, _, out = log.read_text().partition("\n")
                        terminal_image(driver, name, cmd.removeprefix("$ "), out, max_lines=40)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
