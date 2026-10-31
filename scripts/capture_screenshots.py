"""Capture the screenshots used in the run guide (docs/screenshots/*_v2*.png).

    python scripts/capture_screenshots.py            # tests, UI (HED + super-res), API calls
    python scripts/capture_screenshots.py --k8s      # also `kubectl get` of the Minikube deployment

Starts its own Redis (port 6510), MinIO (9010), API (8300) and 4 workers, so it does
not disturb a running deployment. Drives the web UI with headless Firefox (Selenium)
and renders real terminal output as images. Output: docs/screenshots/, docs/logs/.
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2  # noqa: E402

import run_local  # noqa: E402

SHOTS = ROOT / "docs" / "screenshots"
LOGS = ROOT / "docs" / "logs"
SAMPLES = ROOT / "samples"

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



def ui(driver, tmp: Path) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    photo = SAMPLES / "astronaut_2048.png"
    small = SAMPLES / "astronaut_512.png"   # snap Firefox cannot read /tmp
    cv2.imwrite(str(small), cv2.resize(cv2.imread(str(photo)), (512, 512), interpolation=cv2.INTER_AREA))

    s3 = run_local.start_minio(9010)
    redis_url = run_local.start_redis(6510)
    api, workers, url = run_local.launch(4, 8300, redis_url, extra_env={**s3, "S3_PUBLIC_ENDPOINT": s3["S3_ENDPOINT"]})
    try:
        driver.set_window_size(1400, 1000)
        driver.get(url + "/")
        wait_for(lambda: driver.find_element(By.ID, "workers").text.count("worker-") == 4)

        def run(path: Path, op: str, name: str):
            driver.find_element(By.ID, "file").send_keys(str(path))
            Select(driver.find_element(By.ID, "op")).select_by_value(op)
            t = driver.find_element(By.NAME, "tile_size")
            t.clear()
            t.send_keys("auto")
            driver.find_element(By.ID, "go").click()
            time.sleep(1)
            wait_for(lambda: "done" in driver.find_element(By.ID, "status").text and driver.execute_script(
                "const i=document.getElementById('res'); return i.complete && i.naturalWidth > 0"), timeout=300)
            time.sleep(1.5)
            shot(driver, name)

        run(photo, "hed", "ui_v2_hed_done")
        run(small, "superres", "ui_v2_superres_done")

        # API walk-through with curl
        segs = []
        for cmd in (f"curl -s '{url}/plan?op=hed&width=4096&height=4096' | python3 -m json.tool",
                    f"curl -s -F file=@samples/astronaut_2048.png -F op=canny -F tile_size=auto {url}/jobs"
                    " | python3 -m json.tool"):
            out = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True).stdout
            segs.append((cmd.replace(url, "http://127.0.0.1:8000"), out))
        job = json.loads(segs[-1][1])
        time.sleep(3)
        cmd = f"curl -s -o /dev/null -w '%{{http_code}} -> %{{redirect_url}}\\n' {url}/jobs/{job['id']}/result"
        out = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True).stdout
        segs.append((cmd.replace(url, "http://127.0.0.1:8000"), out))
        cmd = f"curl -s {url}/metrics | python3 -m json.tool"
        out = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True).stdout
        segs.append((cmd.replace(url, "http://127.0.0.1:8000"), out))
        terminal_multi(driver, "term_api_v2", segs, max_lines=45, width=1150)
    finally:
        run_local.stop([api, *workers])
        run_local.stop_redis()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k8s", action="store_true")
    ap.add_argument("--skip-ui", action="store_true")
    args = ap.parse_args()
    tmp = Path(__import__("tempfile").mkdtemp())
    driver = browser()
    try:
        out = run_cmd(".venv/bin/python -m pytest -q", "term_tests_v2")
        terminal_multi(driver, "term_tests_v2", [(".venv/bin/python -m pytest -q", out)], max_lines=40)
        if not args.skip_ui:
            ui(driver, tmp)
        if args.k8s:
            out = run_cmd("kubectl -n imgframe get deploy,pods,svc -o wide | cut -c1-150", "k8s_v2_pods")
            terminal_multi(driver, "k8s_v2_pods", [("kubectl -n imgframe get deploy,pods,svc", out)],
                           max_lines=40, width=1250)
    finally:
        driver.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
