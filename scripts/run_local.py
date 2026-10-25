"""Run the whole framework locally without Docker: a Redis server (--redis-url,
else a local redis-server from .tools/bin, else an in-process fakeredis), the
FastAPI service and N worker processes.

    python scripts/run_local.py --workers 4                # serve until Ctrl+C
    python scripts/run_local.py --workers 4 --demo         # scripted end-to-end run
    python scripts/run_local.py --workers 4 --demo --kill-worker   # + fault tolerance
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


REDIS_BIN = ROOT / ".tools" / "bin" / "redis-server"
_redis_procs: list[subprocess.Popen] = []


def start_redis(port: int) -> str:
    """Start a throwaway Redis on `port`; returns its URL.

    Uses a real redis-server (.tools/bin, see scripts/build_redis.sh) when present,
    otherwise falls back to an in-process fakeredis server (fine for small demos).
    """
    import atexit

    if REDIS_BIN.exists():
        p = subprocess.Popen([str(REDIS_BIN), "--port", str(port), "--bind", "127.0.0.1",
                              "--save", "", "--appendonly", "no", "--loglevel", "warning"],
                             stdout=subprocess.DEVNULL)
        _redis_procs.append(p)
        atexit.register(lambda: p.poll() is None and p.terminate())
        import redis
        r = redis.Redis(port=port)
        for _ in range(50):
            try:
                r.ping()
                break
            except redis.ConnectionError:
                time.sleep(0.1)
    else:
        from fakeredis import TcpFakeServer

        server = TcpFakeServer(("127.0.0.1", port), server_type="redis")
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"redis://127.0.0.1:{port}/0"


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_redis() -> None:
    while _redis_procs:
        p = _redis_procs.pop()
        p.terminate()
        p.wait(5)


def wait_http(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


def http_json(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def multipart(fields: dict, file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----imgframe" + os.urandom(8).hex()
    out = []
    for k, v in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
               f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    out.append(content + f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def demo(api: str, workers: list[subprocess.Popen], args) -> int:
    import cv2
    import numpy as np

    from imgframe.pipeline import run_sequential
    from imgframe.sample import synthetic_image
    from imgframe.tiling import decode_image, encode_png

    if args.image:
        img = cv2.imread(args.image)
        name = Path(args.image).name
    else:
        img = synthetic_image(args.size, args.size)
        name = f"synthetic_{args.size}.png"
    print(f"submitting {name} ({img.shape[1]}x{img.shape[0]}), op={args.op}, tile={args.tile_size}")
    job = submit(api, encode_png(img), name, args.op, args.tile_size)
    print(f"job {job['id']}: {job['total']} tiles queued")

    killed = False
    while job["status"] not in ("done", "failed"):
        time.sleep(0.2)
        job = http_json(f"{api}/jobs/{job['id']}")
        if args.kill_worker and not killed and job["done"] > 0:
            victim = workers[0]
            victim.send_signal(signal.SIGKILL)   # hard crash: no graceful hand-back
            killed = True
            print(f"  !! killed worker pid {victim.pid} with SIGKILL at {job['done']}/{job['total']} tiles")
        print(f"  {job['status']:8s} {job['done']:4d}/{job['total']} tiles, retries={job['retries']}", end="\r")
    print()
    print(json.dumps(job, indent=2))
    if job["status"] != "done":
        return 1

    with urllib.request.urlopen(f"{api}/jobs/{job['id']}/result") as r:
        result = decode_image(r.read())
    out = ROOT / "output" / f"{job['id']}_{args.op}.png"
    out.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(out), result)
    t0 = time.perf_counter()
    expected = run_sequential(img, args.op)
    seq_s = time.perf_counter() - t0
    match = float((result == expected).mean())
    print(f"result saved to {out}")
    print(f"pixel agreement with sequential whole-image {args.op}: {match:.4%}")
    print(f"sequential in-memory compute: {seq_s:.3f}s; distributed end-to-end: {job['timings']['total_s']}s")
    print("metrics:", json.dumps(http_json(f"{api}/metrics"), indent=2))
    return 0 if match > 0.999 else 1


def launch(n_workers: int, port: int, redis_url: str, worker_procs: int = 1,
           extra_env: dict | None = None) -> tuple[subprocess.Popen, list[subprocess.Popen], str]:
    """Start the API and N worker processes; returns (api, workers, base_url)."""
    env = {**os.environ, "REDIS_URL": redis_url, "PYTHONPATH": str(ROOT),
           "WORKER_PROCS": str(worker_procs),
           "LEASE_SECONDS": os.environ.get("LEASE_SECONDS", "5"),
           "HEARTBEAT_SECONDS": os.environ.get("HEARTBEAT_SECONDS", "1"), **(extra_env or {})}
    py = sys.executable
    api = subprocess.Popen([py, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                            "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    workers = [subprocess.Popen([py, "-m", "worker.main"], cwd=ROOT,
                                env={**env, "HOSTNAME": f"worker-{i}", "LOG_LEVEL": "WARNING"})
               for i in range(n_workers)]
    url = f"http://127.0.0.1:{port}"
    wait_http(f"{url}/healthz")
    return api, workers, url


def stop(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def submit(api: str, png: bytes, name: str, op: str, tile_size: int, wait_s: float = 300) -> dict:
    """POST /jobs, retrying while the API answers 503 (back-pressure)."""
    import urllib.error

    body, ctype = multipart({"op": op, "tile_size": tile_size}, "file", name, png)
    deadline = time.time() + wait_s
    while True:
        try:
            return http_json(f"{api}/jobs", body, {"Content-Type": ctype})
        except urllib.error.HTTPError as exc:
            if exc.code != 503 or time.time() > deadline:
                raise
            print(f"  API busy (503): {json.loads(exc.read()).get('detail')}; retrying...")
            time.sleep(float(exc.headers.get("Retry-After", 5)))


def delete_job(api: str, job_id: str) -> None:
    urllib.request.urlopen(urllib.request.Request(f"{api}/jobs/{job_id}", method="DELETE"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker-procs", type=int, default=1, help="process-pool slots per worker")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--redis-url", help="use a real Redis instead of the built-in fake one")
    ap.add_argument("--redis-port", type=int, default=6390)
    ap.add_argument("--demo", action="store_true", help="submit one job, verify it, then exit")
    ap.add_argument("--kill-worker", action="store_true", help="SIGKILL a worker mid-job during --demo")
    ap.add_argument("--image", help="image for --demo (default: generated)")
    ap.add_argument("--size", type=int, default=4096, help="generated image side length")
    ap.add_argument("--op", default="canny")
    ap.add_argument("--tile-size", type=int, default=512)
    args = ap.parse_args()

    def _terminate(*_):  # `kill <pid>` shuts the whole stack down like Ctrl+C
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _terminate)

    busy = [p for p in [args.port] + ([] if args.redis_url else [args.redis_port]) if port_in_use(p)]
    if busy:
        print(f"Port {', '.join(map(str, busy))} already in use: another run_local.py (or docker compose) "
              f"is still running. Stop it with Ctrl+C in its terminal, or pass --port / --redis-port.")
        return 1

    redis_url = args.redis_url
    if not redis_url:
        redis_url = start_redis(args.redis_port)
    api, workers, url = launch(args.workers, args.port, redis_url, args.worker_procs)
    try:
        print(f"API ready at {url}  ({args.workers} workers, redis {redis_url})")
        if args.demo:
            return demo(url, workers, args)
        print("Open the URL in a browser. Ctrl+C to stop.")
        api.wait()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop(workers + [api])
        stop_redis()


if __name__ == "__main__":
    sys.exit(main())
