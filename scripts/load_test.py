"""Submit several jobs at once and watch the queue drain: used to demonstrate
autoscaling (Kubernetes) and to load-test any running deployment.

    python scripts/load_test.py --api http://$(minikube ip):30080 --jobs 4 --op bilateral --size 8192
    python scripts/load_test.py --api http://127.0.0.1:8000 --image samples/astronaut_2048.png --op canny
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from run_local import delete_job, http_json, submit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--op", default="bilateral")
    ap.add_argument("--image", help="image file (default: generated synthetic image)")
    ap.add_argument("--size", type=int, default=8192, help="side of the generated image")
    ap.add_argument("--tile-size", type=int, default=512)
    ap.add_argument("--keep", action="store_true", help="keep the jobs (default: delete when done)")
    args = ap.parse_args()

    if args.image:
        data, name = Path(args.image).read_bytes(), Path(args.image).name
    else:
        from imgframe.sample import synthetic_image
        from imgframe.tiling import encode_png
        print(f"generating {args.size}x{args.size} test image...")
        data, name = encode_png(synthetic_image(args.size, args.size)), f"synthetic_{args.size}.png"

    api = args.api.rstrip("/")
    retries0 = http_json(f"{api}/metrics")["retries"]  # count only this run's retries
    t0 = time.time()
    print(f"submitting {args.jobs} x {args.op} jobs to {api} ...")
    with ThreadPoolExecutor(args.jobs) as pool:
        jobs = list(pool.map(lambda _: submit(api, data, name, args.op, args.tile_size), range(args.jobs)))
    ids = [j["id"] for j in jobs]
    print(f"queued {sum(j['total'] for j in jobs)} tiles in {time.time() - t0:.1f}s: {', '.join(ids)}\n")
    print(f"{'time':>6}  {'pending':>7}  {'in-progress':>11}  {'workers':>7}  {'retries':>7}  jobs done")
    while True:
        m = http_json(f"{api}/metrics")
        states = [http_json(f"{api}/jobs/{i}") for i in ids]
        done = sum(s["status"] == "done" for s in states)
        failed = sum(s["status"] == "failed" for s in states)
        print(f"{time.time() - t0:5.1f}s  {m['pending']:7d}  {m['processing']:11d}  "
              f"{m['active_workers']:7d}  {m['retries'] - retries0:7d}  {done}/{len(ids)}"
              + (f"  ({failed} failed)" if failed else ""))
        if done + failed == len(ids):
            break
        time.sleep(1)
    print(f"\nall jobs finished in {time.time() - t0:.1f}s")
    for s in states:
        print(f"  {s['id']}: {s['status']}, {s['total']} tiles, {s['retries']} retries, "
              f"end-to-end {s['timings']['total_s']}s")
    if not args.keep:
        for i in ids:
            delete_job(api, i)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
