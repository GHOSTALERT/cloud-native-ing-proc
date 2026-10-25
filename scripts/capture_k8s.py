"""Run the Kubernetes / Docker Compose demonstrations and capture them for the report.

Needs a running Minikube cluster with the app deployed (see the guide), plus
Firefox for the browser screenshots.

    python scripts/capture_k8s.py                 # everything
    python scripts/capture_k8s.py --only scaling  # one step: deploy|ui|scaling|fault|bench|compose

Output: docs/screenshots/k8s_*.png, docs/logs/k8s_*.txt, benchmark/results/k8s_*.json/.png
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from capture_screenshots import (LOGS, SHOTS, browser, clean, make_samples, shot,  # noqa: E402
                                 terminal_multi, wait_for)

NS = "imgframe"
PY = str(ROOT / ".venv" / "bin" / "python")
RESULTS = ROOT / "benchmark" / "results"
# Until the user logs out and back in, the docker group is only active via `sg`.
DOCKER_OK = os.access("/var/run/docker.sock", os.W_OK)


def sh(cmd: str, timeout: int = 900, check: bool = False) -> str:
    if not DOCKER_OK and cmd.split()[0] in ("minikube", "docker"):
        cmd = f"sg docker -c {json.dumps(cmd)}"
    p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"{cmd} failed:\n{p.stdout}\n{p.stderr}")
    return clean(p.stdout + p.stderr)


def k(args: str, **kw) -> str:
    return sh(f"kubectl -n {NS} {args}", **kw)


def log(name: str, segments: list[tuple[str, str]]) -> None:
    (LOGS / f"{name}.txt").write_text("\n\n".join(f"$ {c}\n{o}" for c, o in segments))


def api_url() -> str:
    return f"http://{sh('minikube ip').strip()}:30080"


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def replicas() -> tuple[int, int]:
    out = k("get deploy worker -o jsonpath={.spec.replicas},{.status.readyReplicas}").strip()
    spec, _, ready = out.partition(",")
    return int(spec or 0), int(ready or 0)


def wait_workers(api: str, n: int, timeout: int = 240) -> None:
    wait_for(lambda: replicas() == (n, n) and get_json(f"{api}/metrics")["active_workers"] == n,
             timeout=timeout, interval=1)


def load_test(api: str, jobs: int, image: Path, op: str = "bilateral") -> subprocess.Popen:
    return subprocess.Popen([PY, "scripts/load_test.py", "--api", api, "--jobs", str(jobs),
                             "--op", op, "--image", str(image)],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


# ------------------------------------------------------------------ steps
def step_deploy(driver, api):
    segs = [(c, k(c.removeprefix(f"kubectl -n {NS} ")) if c.startswith("kubectl -n") else sh(c))
            for c in ("kubectl get nodes",
                      f"kubectl -n {NS} get deploy,svc",
                      f"kubectl -n {NS} get pods -o wide")]
    log("k8s_01_deployment", segs)
    terminal_multi(driver, "k8s_01_deployment", segs, width=1350)


def step_ui(driver, api, photo):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    driver.set_window_size(1400, 1000)
    driver.get(api + "/")
    wait_for(lambda: "worker-" in driver.find_element(By.ID, "workers").text, timeout=30)
    tile = driver.find_element(By.NAME, "tile_size")
    tile.clear()
    tile.send_keys("256")
    driver.find_element(By.ID, "file").send_keys(str(photo))
    Select(driver.find_element(By.ID, "op")).select_by_value("canny")
    driver.find_element(By.ID, "go").click()
    wait_for(lambda: "done" in driver.find_element(By.ID, "status").text and driver.execute_script(
        "const i=document.getElementById('res'); return i.complete && i.naturalWidth > 0"), timeout=120)
    time.sleep(1.5)
    shot(driver, "k8s_02_ui")


def step_scaling(driver, api, big):
    print("  waiting for the autoscaler to settle at 1 worker...")
    wait_workers(api, 1, timeout=300)
    samples, stop = [], threading.Event()
    peak_pods = {"text": "", "n": 0}
    t0 = time.time()

    def sampler():
        while not stop.is_set():
            try:
                m = get_json(f"{api}/metrics")
                spec, ready = replicas()
                samples.append({"t": round(time.time() - t0, 1), "pending": m["pending"],
                                "processing": m["processing"], "spec": spec, "ready": ready,
                                "active": m["active_workers"]})
                if ready > peak_pods["n"]:
                    peak_pods.update(n=ready, text=k("get pods -l app=worker"))
            except Exception as exc:  # keep sampling through transient errors
                print("  sampler:", exc)
            stop.wait(1.0)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    time.sleep(3)
    cmd = f"python scripts/load_test.py --api {api} --jobs 4 --op bilateral --image samples/synthetic_8192.png"
    p = load_test(api, 4, big)
    lt_out = clean(p.communicate(timeout=900)[0])
    print("  load test finished; waiting for scale-down...")
    try:
        wait_for(lambda: replicas()[0] == 1, timeout=120, interval=2)
        time.sleep(8)
    except TimeoutError:
        print("  (no scale-down within 120 s)")
    stop.set()
    th.join()
    scaler = k("logs deploy/autoscaler --since=15m")
    scaler = "\n".join(line for line in scaler.splitlines() if "scaling" in line or "autoscaling" in line)

    segs = [(cmd, lt_out), (f"kubectl -n {NS} get pods -l app=worker   # at peak", peak_pods["text"])]
    log("k8s_03_scaling", segs)
    terminal_multi(driver, "k8s_03_scaling", segs, width=1350)
    segs = [(f"kubectl -n {NS} logs deploy/autoscaler | grep scaling", scaler)]
    log("k8s_04_autoscaler", segs)
    terminal_multi(driver, "k8s_04_autoscaler", segs, width=1350)
    (RESULTS / "k8s_scaling_timeline.json").write_text(json.dumps(samples, indent=1))
    plot_timeline(samples)


def plot_timeline(samples):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = [s["t"] for s in samples]
    fig, ax1 = plt.subplots(figsize=(11, 4.2))
    ax1.fill_between(t, [s["pending"] + s["processing"] for s in samples], color="#93c5fd", alpha=0.6,
                     label="queue depth (pending + in progress)")
    ax1.set_xlabel("time (s)")
    ax1.set_ylabel("tiles in queue", color="#1d4ed8")
    ax2 = ax1.twinx()
    ax2.step(t, [s["spec"] for s in samples], where="post", color="#ea580c", lw=2,
             label="desired worker replicas")
    ax2.step(t, [s["ready"] for s in samples], where="post", color="#16a34a", lw=2, ls="--",
             label="ready worker pods")
    ax2.set_ylabel("worker pods")
    ax2.set_ylim(0, max(s["spec"] for s in samples) + 1)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=9)
    ax1.grid(alpha=0.3)
    ax1.set_title("Autoscaling on Kubernetes: 4 concurrent jobs (1024 tiles), bilateral, 8192² each")
    fig.tight_layout()
    fig.savefig(SHOTS / "k8s_scaling_timeline.png", dpi=120)
    plt.close(fig)
    print("  saved docs/screenshots/k8s_scaling_timeline.png")


def _running_worker(after_done: int, api: str) -> str:
    start = get_json(f"{api}/metrics")["tasks_done"]
    wait_for(lambda: get_json(f"{api}/metrics")["tasks_done"] - start >= after_done, timeout=300,
             interval=0.3)
    pods = k("get pods -l app=worker --field-selector=status.phase=Running "
             "-o jsonpath={.items[*].metadata.name}").split()
    return pods[0]


def step_fault(driver, api, big):
    wait_workers(api, 1, timeout=300)
    # A) hard crash: SIGKILL the worker container on the node
    cmd_lt = f"python scripts/load_test.py --api {api} --jobs 2 --op bilateral --image samples/synthetic_8192.png"
    p = load_test(api, 2, big)
    pod = _running_worker(25, api)
    cid = sh(f"minikube ssh -- docker ps -q --filter label=io.kubernetes.pod.name={pod} "
             f"--filter label=io.kubernetes.container.name=worker").strip()
    kill_cmd = f"minikube ssh -- docker kill --signal KILL {cid}"
    kill_out = sh(kill_cmd)
    print(f"  killed container {cid} of {pod}")
    lt_out = clean(p.communicate(timeout=900)[0])
    pods_after = k("get pods -l app=worker")
    segs = [(f"# crash a worker mid-job: SIGKILL its container ({pod})", ""),
            (kill_cmd, kill_out), (cmd_lt, lt_out),
            (f"kubectl -n {NS} get pods -l app=worker   # RESTARTS = 1: Kubernetes restarted it", pods_after)]
    log("k8s_05_crash", segs)
    terminal_multi(driver, "k8s_05_crash", segs, width=1350)

    # B) graceful: delete a pod (scale-down / eviction / rolling update path)
    wait_workers(api, 1, timeout=300)
    p = load_test(api, 2, big)
    wait_for(lambda: replicas()[1] >= 4, timeout=120, interval=0.5)   # autoscaler has added pods
    pod = _running_worker(10, api)
    del_cmd = f"kubectl -n {NS} delete pod {pod}"
    del_out = k(f"delete pod {pod} --wait=false")
    pods_mid = k("get pods -l app=worker")
    lt_out = clean(p.communicate(timeout=900)[0])
    segs = [(del_cmd, del_out),
            (f"kubectl -n {NS} get pods -l app=worker   # old pod terminating, replacement created",
             pods_mid),
            (cmd_lt, lt_out)]
    log("k8s_06_delete_pod", segs)
    terminal_multi(driver, "k8s_06_delete_pod", segs, width=1350)


def step_bench(driver, api):
    k("scale deploy autoscaler --replicas=0", check=True)
    rows = []
    try:
        for n in (1, 2, 4, 8):
            k(f"scale deploy worker --replicas={n}", check=True)
            wait_workers(api, n, timeout=300)
            time.sleep(3)
            out = sh(f"{shlex.quote(PY)} -m benchmark.bench_distributed --api {api} --label {n} --repeats 2 "
                     f"--op bilateral --size 8192", timeout=1800)
            result = RESULTS / f"k8s_bilateral_w{n}.json"
            if not result.exists():
                raise RuntimeError(f"benchmark with {n} pods failed:\n{out}")
            print(f"    {n} pods:", next((l for l in out.splitlines() if "end-to-end" in l), "").strip())
            data = json.loads(result.read_text())
            if not rows:
                rows.append(data["rows"][0])                            # sequential baseline
            rows.append(data["rows"][1])
    finally:
        k("scale deploy autoscaler --replicas=1")
        k("scale deploy worker --replicas=2")
    for r in rows:
        if r["mode"] != "sequential":
            r["workers"] = int(r["workers"])
            r["efficiency"] = round(r["speedup"] / r["workers"], 2)
    (RESULTS / "k8s_bilateral.json").write_text(json.dumps({"size": 8192, "tile_size": 512,
                                                            "rows": rows}, indent=2))
    from benchmark.plot import plot_distributed
    plot_distributed(rows, RESULTS / "k8s_bilateral.png",
                     "Kubernetes (Minikube): bilateral, 8192x8192, 512px tiles, 1 CPU per worker pod")
    for f in RESULTS.glob("k8s_bilateral_w*.*"):
        f.unlink()
    table = "\n".join(f"{r['mode']:12s} workers={r['workers']!s:>2}  end-to-end {r['time_s']:6.2f}s  "
                      f"speedup {r['speedup']:4.2f}x  parallel phase {r['processing_s']:6.2f}s "
                      f"({r['processing_speedup']:4.2f}x)" for r in rows)
    segs = [("python -m benchmark.bench_distributed --api $API --label N   # N = 1, 2, 4, 8 worker pods", table)]
    log("k8s_07_bench", segs)
    terminal_multi(driver, "k8s_07_bench", segs, width=1350)


def step_compose(driver, big):
    segs = []
    sh("docker compose down", timeout=120)
    segs.append(("docker compose up -d --scale worker=4", sh("docker compose up -d --scale worker=4", timeout=600)))
    wait_for(lambda: _ok("http://127.0.0.1:8000/healthz"), timeout=120, interval=1)
    time.sleep(3)
    segs.append(("docker compose ps", sh("docker compose ps --format 'table {{.Name}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}'")))
    p = load_test("http://127.0.0.1:8000", 2, big)
    start = get_json("http://127.0.0.1:8000/metrics")["tasks_done"]
    wait_for(lambda: get_json("http://127.0.0.1:8000/metrics")["tasks_done"] - start >= 25, timeout=300,
             interval=0.3)
    segs.append(("docker kill imgframe-worker-1      # crash a container mid-job", sh("docker kill imgframe-worker-1")))
    lt = clean(p.communicate(timeout=900)[0])
    segs.append(("python scripts/load_test.py --api http://127.0.0.1:8000 --jobs 2 --op bilateral "
                 "--image samples/synthetic_8192.png", lt))
    log("compose_01", segs)
    terminal_multi(driver, "compose_01", segs, width=1350)
    sh("docker compose down", timeout=120)


def _ok(url):
    try:
        get_json(url)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["deploy", "ui", "scaling", "fault", "bench", "compose"])
    args = ap.parse_args()
    photo, big = make_samples()
    driver = browser()
    try:
        api = None if args.only == "compose" else api_url()
        steps = [args.only] if args.only else ["deploy", "ui", "scaling", "fault", "bench", "compose"]
        for s in steps:
            print(s)
            if s == "deploy":
                step_deploy(driver, api)
            elif s == "ui":
                step_ui(driver, api, photo)
            elif s == "scaling":
                step_scaling(driver, api, big)
            elif s == "fault":
                step_fault(driver, api, big)
            elif s == "bench":
                step_bench(driver, api)
            elif s == "compose":
                step_compose(driver, big)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
