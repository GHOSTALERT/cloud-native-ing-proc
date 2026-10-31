"""Capture the v2 screenshots used by the guide (docs/screenshots/*_v2*.png).

    python scripts/capture_v2.py            # tests, UI (HED + super-res), API calls
    python scripts/capture_v2.py --k8s      # also `kubectl get` of the Minikube deployment

Starts its own Redis (port 6510), MinIO (9010), API (8300) and 4 workers, so it does
not disturb a running deployment.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2  # noqa: E402

import run_local  # noqa: E402
from capture_screenshots import browser, run_cmd, shot, terminal_multi, wait_for  # noqa: E402

SAMPLES = ROOT / "samples"


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
