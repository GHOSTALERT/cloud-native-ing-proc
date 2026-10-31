"""Build the detailed how-to-run guidebook (docs/How_to_Run_Guide.docx + .pdf).

    python docs/build_guide.py
"""
import sys
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docxkit import Doc, to_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
FIG = DOCS / "figures"
SHOTS = DOCS / "screenshots"
NAVY = RGBColor(0x1E, 0x3A, 0x8A)
L = WD_ALIGN_PARAGRAPH.LEFT
MISSING = []

TEAM = [("Prapti Kurkuri", "24BVD0185"), ("Vaanya Garg", "24BVD0139"),
        ("Aditya Manoj", "24BVD0070"), ("Arrnav Trisal", "24BVD0064")]


class Guide(Doc):
    def __init__(self):
        super().__init__(font="Calibri", size=10.5, margins_cm=(2.0, 2.2, 2.0, 2.2), code_font="Consolas",
                         accent=NAVY)
        self.n1 = self.n2 = 0

    def sec(self, text):
        self.n1 += 1
        self.n2 = 0
        return self.h(f"{self.n1}. {text}", 1, size=15, before=14, after=5)

    def sub(self, text):
        self.n2 += 1
        return self.h(f"{self.n1}.{self.n2} {text}", 2, size=12, before=9, after=3)

    def t(self, text, **kw):
        kw.setdefault("indent", False)
        kw.setdefault("align", L)
        kw.setdefault("after", 5)
        return self.p(text, **kw)

    def tbl(self, header, rows, caption, widths):
        return self.table(header, rows, caption, widths_cm=widths, size=9, ieee=False, header_fill="1E3A8A",
                          zebra="F1F5F9", align_cols=[L] * len(header))

    def figx(self, path, caption, max_h_in=4.6, width_in=None):
        if not path.exists():
            MISSING.append(str(path.relative_to(ROOT)))
            self.box(f"missing image {path.name}", fill="FEF3C7")
            return
        self.figure(path, caption, ieee=False, max_h_in=max_h_in, size=9, width_in=width_in)

    def tip(self, text):
        self.box(text, fill="ECFDF5", label="Tip:", size=9.5)

    def warn(self, text):
        self.box(text, fill="FEF3C7", label="Watch out:", size=9.5)


def build() -> Path:
    g = Guide()
    d = g.d

    # ---------------------------------------------------------------- cover
    for _ in range(5):
        g.p("", indent=False)
    g.p("BECE355L — Advanced Cloud Computing", size=13, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False,
        color=RGBColor(0x47, 0x55, 0x69))
    g.p("Cloud-Native Parallel Image Processing", bold=True, size=24, align=WD_ALIGN_PARAGRAPH.CENTER,
        indent=False, before=10, color=NAVY)
    g.p("How to Run — Detailed Guidebook", size=16, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, after=24)
    g.p("Setup from a fresh clone, the three ways to run the framework (local, Docker Compose, Kubernetes), "
        "the web UI and REST API, the cost-aware planner, the work-aware autoscaler and its baselines, and "
        "how to reproduce every result in the report.", size=11, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False,
        after=30)
    g.tbl(["Name", "Register No."], TEAM, None, [6.0, 4.0])
    g.p("Repository: github.com/GHOSTALERT/cloud-native-ing-proc", size=10, align=WD_ALIGN_PARAGRAPH.CENTER,
        indent=False, before=20)
    g.page_break()

    # ---------------------------------------------------------------- contents
    g.h("Contents", 1, size=15, before=0)
    for line in ["1. What you are running", "2. Prerequisites", "3. One-time setup", "4. Mode A: local run (no Docker)",
                 "5. Using the web UI", "6. Using the REST API", "7. Mode B: Docker Compose",
                 "8. Mode C: Kubernetes (Minikube)", "9. Autoscalers: work-aware, KEDA and HPA",
                 "10. Reproducing every result in the report", "11. Deploying on AWS (S3, IAM, KMS, EKS)",
                 "12. Configuration reference", "13. Troubleshooting"]:
        g.t(line, after=2)
    g.page_break()

    # ---------------------------------------------------------------- 1
    g.sec("What you are running")
    g.t("A large image is uploaded to a REST API. A **cost-aware planner** predicts how long the job will take "
        "locally and distributed, and picks the faster plan, the tile size and the worker count. The image is cut "
        "into tiles with overlapping halos; the tiles are stored in **S3-compatible object storage** (MinIO "
        "locally, Amazon S3 on AWS) and only their ids go into a **Redis** task queue. **Worker** processes or pods "
        "claim tiles with a lease, run an OpenCV filter or a neural network (HED edges, Real-ESRGAN 4× super-"
        "resolution), store the result in object storage, and the worker that finishes the last tile stitches "
        "the final image. A **work-aware autoscaler** sizes the worker Deployment from the predicted compute-"
        "seconds waiting in the queue.")
    g.figx(FIG / "fig_architecture_v2.png", "System architecture after the four upgrades.")
    g.tbl(["Folder", "What is inside"], [
        ["`imgframe/`", "core library: config, ops (OpenCV + ONNX), tiling, Redis store + queue, object store, planner, models"],
        ["`api/`", "FastAPI service and the web UI (`index.html`)"],
        ["`worker/`", "worker process (claim → fetch → process → store → aggregate)"],
        ["`autoscaler/`", "work-aware / queue-length autoscaler for Kubernetes"],
        ["`benchmark/`", "benchmarks; results in `benchmark/results/`"],
        ["`scripts/`", "run_local, model and MinIO fetchers, calibration, profiling, load tests, K8s helpers"],
        ["`k8s/`", "Kubernetes manifests; `optional/` holds the HPA and KEDA baselines"],
        ["`deploy/aws/`", "S3 bucket policy, IAM policies, lifecycle rule, KMS setup script, IRSA service accounts"],
        ["`docs/`", "report, slides, this guide, figures and the scripts that build them"],
        ["`tests/`", "pytest suite (43 tests)"],
    ], "Repository layout", [3.2, 13.3])

    # ---------------------------------------------------------------- 2
    g.sec("Prerequisites")
    g.t("Everything was developed and tested on Ubuntu (x86-64, 16 hardware threads, 22 GB RAM). Any recent "
        "Linux works; macOS works for modes A and B.")
    g.tbl(["Software", "Needed for", "How to get it"], [
        ["Python 3.12 or 3.13 + venv", "everything", "`sudo apt install python3 python3-venv`"],
        ["git, curl, gcc, make", "cloning, downloads, building Redis", "`sudo apt install git curl build-essential`"],
        ["Docker", "modes B and C, and fetching the MinIO binary", "`sudo bash scripts/install_k8s_tools.sh`"],
        ["Minikube + kubectl", "mode C", "same script (installs Docker, Minikube v1.37, kubectl v1.34)"],
        ["LibreOffice", "only to rebuild the PDF documents", "`sudo apt install libreoffice`"],
        ["Firefox", "only to recapture UI screenshots", "preinstalled on Ubuntu"],
    ], "Software prerequisites", [4.0, 5.0, 7.5])
    g.tip("Nothing in modes A and B needs root. Redis and MinIO run from `.tools/bin` inside the project.")

    # ---------------------------------------------------------------- 3
    g.sec("One-time setup")
    g.sub("Clone and create the Python environment")
    g.code("git clone https://github.com/GHOSTALERT/cloud-native-ing-proc.git\n"
           "cd cloud-native-ing-proc\n"
           "python3 -m venv .venv\n"
           ".venv/bin/pip install -r requirements-dev.txt")
    g.t("If `python3 -m venv` fails because `ensurepip` is missing (Debian/Ubuntu without python3-venv), create the "
        "venv without pip and let the system pip install into it:")
    g.code("python3 -m venv --without-pip .venv\n"
           "pip3 --python .venv/bin/python install -r requirements-dev.txt")
    g.sub("Redis and MinIO binaries (for mode A)")
    g.code("bash scripts/build_redis.sh      # compiles redis-server 7.2.5 into .tools/bin (about 1 minute)\n"
           "bash scripts/fetch_minio.sh      # copies the MinIO server binary into .tools/bin (needs Docker)")
    g.t("MinIO no longer publishes binaries or Docker Hub images, so `fetch_minio.sh` copies the binary out of "
        "Chainguard's open-source build (`cgr.dev/chainguard/minio`). Without `.tools/bin/minio`, `run_local.py` "
        "falls back to the original all-in-Redis storage mode, so the framework still runs.")
    g.sub("Neural network weights")
    g.code("bash scripts/fetch_models.sh     # ~125 MB download, checksums verified")
    g.t("This downloads the official HED Caffe model (Xie & Tu) and the Real-ESRGAN general-x4v3 ONNX export, and "
        "converts HED to `models/hed.onnx` with `scripts/convert_hed.py` (OpenCV 5 dropped its Caffe importer, so "
        "the converter decodes the caffemodel itself and rebuilds the network for ONNX Runtime). Without the "
        "weights the `hed` and `superres` operations are simply hidden from the UI and `/ops`.")
    g.sub("Sample images and the test suite")
    g.code(".venv/bin/python scripts/make_samples.py     # samples/astronaut_2048.png, synthetic_8192.png\n"
           ".venv/bin/python -m pytest -q                # 43 passed")
    g.figx(SHOTS / "term_tests_v2.png", "The test suite: 43 tests, including S3 mode, neural tiling, planner and "
          "work accounting.", max_h_in=3.6)

    # ---------------------------------------------------------------- 4
    g.sec("Mode A: local run (no Docker)")
    g.t("`scripts/run_local.py` starts Redis, MinIO, the API and N worker processes on this machine:")
    g.code(".venv/bin/python scripts/run_local.py --workers 4\n"
           "# API ready at http://127.0.0.1:8000  (4 workers, redis ..., storage MinIO http://127.0.0.1:9000)")
    g.t("Open **http://127.0.0.1:8000** in a browser. Stop everything with Ctrl+C.")
    g.sub("Scripted demos")
    g.code("# one job, checked pixel-by-pixel against whole-image processing\n"
           ".venv/bin/python scripts/run_local.py --workers 4 --demo --op bilateral --size 4096\n\n"
           "# neural edge detection on the sample photo, planner decides the tiling\n"
           ".venv/bin/python scripts/run_local.py --workers 8 --demo --op hed --image samples/astronaut_2048.png\n\n"
           "# 4x super-resolution of a 512 px image (output 2048 px)\n"
           ".venv/bin/python scripts/run_local.py --workers 4 --demo --op superres --size 512\n\n"
           "# fault tolerance: SIGKILL a worker mid-job; its tiles are retried, the result is still exact\n"
           ".venv/bin/python scripts/run_local.py --workers 4 --demo --kill-worker --op bilateral --tile-size 256")
    g.tbl(["Option", "Meaning"], [
        ["`--workers N`", "number of worker processes (default 4)"],
        ["`--storage s3|redis`", "`s3` (default when MinIO is present): pixels in MinIO; `redis`: original mode"],
        ["`--tile-size auto|N`", "`auto` lets the planner decide; a number forces naive tiling with that size"],
        ["`--op NAME`", "canny, sobel, gaussian_blur, sharpen, grayscale, bilateral, hed, superres"],
        ["`--image PATH` / `--size N`", "input image for `--demo`, else a generated N×N image"],
        ["`--port`, `--redis-port`, `--minio-port`", "change the ports (8000, 6390, 9000) if they are in use"],
    ], "run_local.py options", [5.2, 11.3])
    g.tip("The demo's pixel-agreement line is the correctness check: 100 % for local filters, ≥ 99.9 % for Canny, and "
          "≥ 40 dB seam PSNR for the neural ops at their calibrated halos.")

    # ---------------------------------------------------------------- 5
    g.sec("Using the web UI")
    g.numbered([
        "Choose an image (PNG/JPEG/TIFF) and an operation.",
        "Leave **Tile size** on `auto` to let the planner choose local vs distributed and the tile size, or type "
        "a number (256, 512, …) to force naive tiling.",
        "Press **Process**. The progress bar counts finished tiles; the right-hand panel shows the plan, the storage "
        "mode, per-phase timings, and the planner's predicted time next to the measured one.",
        "When the job is done, the result appears next to the original. Full-resolution links are short-lived "
        "presigned S3 URLs (5 minutes by default); huge outputs are offered as a tiled TIFF.",
        "The **Cluster** card shows queued work in compute-seconds (the autoscaler's input), pending and in-progress "
        "tiles, retries, live workers and Redis memory.",
    ])
    g.figx(SHOTS / "ui_v2_hed_done.png", "Web UI after an HED job planned automatically.", max_h_in=5.0)
    g.figx(SHOTS / "ui_v2_superres_done.png", "4× super-resolution job: the plan, scale factor and presigned download.",
          max_h_in=5.0)

    # ---------------------------------------------------------------- 6
    g.sec("Using the REST API")
    g.tbl(["Method + path", "Purpose"], [
        ["`POST /jobs` (form: `file`, `op`, `tile_size`=auto|N)", "submit an image; returns the job with its plan"],
        ["`GET /jobs`, `GET /jobs/{id}`", "list jobs / status, progress, plan, timings"],
        ["`GET /jobs/{id}/result?format=png|jpg|webp|tif&max_side=N`", "final image (S3 mode: 307 redirect to a presigned URL)"],
        ["`GET /jobs/{id}/url?format=…`", "the presigned URL as JSON, with its expiry"],
        ["`GET /jobs/{id}/preview`", "downscaled copy of the upload"],
        ["`DELETE /jobs/{id}`", "delete the job and its objects"],
        ["`GET /plan?op=&width=&height=&workers=`", "dry run of the planner (nothing is queued)"],
        ["`GET /metrics`", "queue depth, queued work-seconds, workers, retries, Redis memory"],
        ["`GET /ops`, `GET /healthz`, `GET /docs`", "operations, health check, Swagger UI"],
    ], "REST endpoints", [8.2, 8.3])
    g.code("API=http://127.0.0.1:8000\n"
           "curl -s -F file=@samples/astronaut_2048.png -F op=hed $API/jobs | python3 -m json.tool\n"
           "curl -s $API/jobs/<id> | python3 -m json.tool          # status, plan_mode, predicted_s, timings\n"
           "curl -sL -o edges.png $API/jobs/<id>/result            # -L follows the presigned redirect\n"
           "curl -s \"$API/plan?op=canny&width=8192&height=8192\"   # why is canny run locally?\n"
           "curl -s $API/metrics | python3 -m json.tool")
    g.figx(SHOTS / "term_api_v2.png", "Submitting a job and asking the planner with curl.", max_h_in=4.4)

    # ---------------------------------------------------------------- 7
    g.sec("Mode B: Docker Compose")
    g.t("Compose runs Redis (capped at 256 MB: it only holds control data now), MinIO, the API and four workers "
        "from one image. Fetch the models first; they are baked into the image.")
    g.code("bash scripts/fetch_models.sh\n"
           "docker compose up --build --scale worker=4\n"
           "# UI http://localhost:8000      MinIO (presigned downloads) http://localhost:9000\n"
           "bash scripts/kill_worker.sh compose      # optional: crash a busy worker during a job\n"
           "docker compose down")

    # ---------------------------------------------------------------- 8
    g.sec("Mode C: Kubernetes (Minikube)")
    g.sub("Start the cluster and build the image inside it")
    g.code("minikube start --driver=docker --cpus=8 --memory=12g\n"
           "eval $(minikube docker-env)\n"
           "docker build -t imgframe:latest .")
    g.sub("Deploy")
    g.code("kubectl apply -f k8s/\n"
           "kubectl -n imgframe set env deploy/api S3_PUBLIC_ENDPOINT=http://$(minikube ip):30900\n"
           "kubectl -n imgframe get pods -w          # redis, minio, api, autoscaler, worker(s) Running\n"
           "echo http://$(minikube ip):30080          # the web UI")
    g.t("The second command matters: presigned URLs are signed for a host name, and the browser reaches MinIO "
        "through the NodePort 30900 on the Minikube IP.")
    g.figx(SHOTS / "k8s_v2_pods.png", "All components running on Minikube.", max_h_in=3.5)
    g.tbl(["Manifest", "Creates"], [
        ["`00-namespace.yaml`", "namespace, ConfigMap (Redis/S3 settings, PLANNER_WORKERS=8), demo S3 credentials"],
        ["`10-redis.yaml`", "Redis 7.2, 256 MB, control data only"],
        ["`15-minio.yaml`", "MinIO object store, NodePort 30900"],
        ["`20-api.yaml`", "API Deployment + NodePort 30080"],
        ["`30-worker.yaml`", "worker Deployment (1 CPU, 2 GiB limit each)"],
        ["`40-autoscaler.yaml`", "work-aware autoscaler + RBAC to scale the worker Deployment"],
        ["`optional/hpa-cpu.yaml`", "baseline: CPU-based HorizontalPodAutoscaler (70 %)"],
        ["`optional/keda-scaledobject.yaml`", "baseline: KEDA scaling on Redis list length"],
    ], "Kubernetes manifests", [5.3, 11.2])
    g.sub("Demos on the cluster")
    g.code("API=http://$(minikube ip):30080\n"
           ".venv/bin/python scripts/load_test.py --api $API --jobs 6 --op bilateral --size 4096\n"
           "kubectl -n imgframe logs -f deploy/autoscaler           # scaling decisions with W and λ\n"
           "bash scripts/kill_worker.sh k8s                          # self-healing: crash a busy worker pod")

    # ---------------------------------------------------------------- 9
    g.sec("Autoscalers: work-aware, KEDA and HPA")
    g.t("Exactly one scaler may own the worker Deployment. Install the two baselines once, then switch with the "
        "helper script:")
    g.code("bash scripts/install_k8s_addons.sh     # metrics-server addon + KEDA 2.21 (one time)\n"
           "bash scripts/k8s_scaler.sh work        # ours: replicas from queued compute-seconds\n"
           "bash scripts/k8s_scaler.sh keda        # KEDA: 16 pending tiles per worker\n"
           "bash scripts/k8s_scaler.sh hpa         # HPA: 70 % CPU\n"
           "bash scripts/k8s_scaler.sh queue       # our autoscaler with the original queue-length rule")
    g.t("The work-aware rule is `replicas = clamp(max(⌈W/(D·u)⌉, ⌈λ/u⌉), MIN, MAX)` with W the predicted seconds of "
        "queued work, λ its arrival rate, D the latency target (`LATENCY_TARGET_S` minus `POD_STARTUP_S`) and u the "
        "target utilization. Tune it with `kubectl -n imgframe set env deploy/autoscaler LATENCY_TARGET_S=20`.")
    g.sub("Replaying the comparison trace")
    g.code("bash scripts/run_autoscale_comparison.sh 3     # ~45 min: 3 runs x (hpa, keda, work), interleaved\n"
           "\n"
           "# or one run by hand:\n"
           "bash scripts/k8s_scaler.sh keda\n"
           ".venv/bin/python scripts/trace_replay.py --api http://$(minikube ip):30080 --label keda --run 1\n"
           ".venv/bin/python docs/make_figures.py autoscale")
    g.t("The driver switches the scaler, waits until the queue is empty and the Deployment is back at one ready "
        "worker, waits 20 s more so HPA and KEDA see an idle sample, and then replays the trace. Runs are "
        "interleaved (hpa, keda, work, hpa, ...) so slow drift on the host affects all three alike.")
    g.t("Each run replays the same seeded Poisson trace (bursts of cheap grayscale/Canny jobs, then bursts of "
        "bilateral/HED jobs) and writes `benchmark/results/autoscale/<scaler>_run<N>.json` with per-job latency, "
        "p50/p95, jobs within the target, worker pod-seconds, scaling events and time to first scale-up.")

    # ---------------------------------------------------------------- 10
    g.sec("Reproducing every result in the report")
    g.tbl(["Result", "Command", "Output in benchmark/results/"], [
        ["Halo calibration (HED, Real-ESRGAN)", "`python scripts/calibrate_halo.py`", "halo_calibration.json/.png; imgframe/calibration.json"],
        ["Cost-model profiles", "`python scripts/profile_ops.py`", "imgframe/profiles.json"],
        ["Original naive-tiling benchmark", "`python -m benchmark.bench_local --size 8192`", "results.csv, charts.png"],
        ["Planner vs naive, model accuracy", "`python -m benchmark.bench_planner`", "planner_engine.json/.png"],
        ["Planner on the full framework", "`python -m benchmark.bench_planner --cluster --workers 8`", "planner_cluster.json"],
        ["Redis vs S3 storage study", "`python -m benchmark.bench_storage`", "storage_study.json"],
        ["Autoscaler comparison (Minikube)", "`bash scripts/run_autoscale_comparison.sh 3`", "autoscale/<scaler>_run<N>.json"],
        ["Screenshots", "`python scripts/capture_screenshots.py --k8s`", "docs/screenshots/*_v2*.png"],
        ["All report figures", "`python docs/make_figures.py`", "docs/figures/*.png"],
        ["Report, slides, this guide", "`python docs/build_report.py; python docs/build_slides.py; python docs/build_guide.py`", "docs/*.docx, .pdf, .pptx"],
    ], "Commands that regenerate each result (run from the repository root with .venv/bin/python)", [4.3, 6.6, 5.6])
    g.warn("Benchmarks measure wall-clock time: close other heavy programs and do not run two benchmarks at once. "
           "Numbers will differ on other hardware; the planner adapts because it reads `profiles.json`, so re-run "
           "`profile_ops.py` on a new machine first.")

    # ---------------------------------------------------------------- 11
    g.sec("Deploying on AWS (S3, IAM, KMS, EKS)")
    g.t("The `deploy/aws/` templates implement the security controls from Module 4. They were written for this "
        "project but **not executed** (no AWS account was used); review them before running.")
    g.numbered([
        "Create the bucket, a customer-managed KMS key, default SSE-KMS encryption, Block Public Access, a bucket "
        "policy that denies unencrypted or non-TLS requests, the lifecycle rule and two least-privilege IAM "
        "policies: `BUCKET=my-imgframe REGION=ap-south-1 bash deploy/aws/setup_s3.sh`.",
        "Create IAM roles `imgframe-api` and `imgframe-worker` with those policies and trust for EKS IRSA; apply "
        "`deploy/aws/irsa-serviceaccounts.yaml` and add `serviceAccountName` to the api and worker Deployments.",
        "In the ConfigMap: remove `S3_ENDPOINT`, set `S3_BUCKET`, `S3_SSE=aws:kms`, `S3_KMS_KEY_ID` and "
        "`S3_MANAGE_BUCKET=0`; delete the `s3-credentials` Secret and the MinIO manifest.",
        "Push the image to ECR, change `imagePullPolicy` to `IfNotPresent` and apply the remaining manifests.",
    ])
    g.tbl(["Role", "May do", "Cannot do"], [
        ["API", "PUT `jobs/*/input.*`, `tiles/*`, `result.*`; GET outputs; delete jobs", "read tiles or results; touch other prefixes"],
        ["Worker", "GET tiles/results; PUT results and outputs; delete tiles/results after stitching",
         "write inputs; delete outputs; list the bucket outside `jobs/`"],
    ], "Least-privilege split between the two service roles", [2.3, 8.0, 6.2])

    # ---------------------------------------------------------------- 12
    g.sec("Configuration reference")
    g.tbl(["Variable", "Default", "Meaning"], [
        ["`REDIS_URL`", "redis://localhost:6379/0", "Redis connection"],
        ["`S3_BUCKET`", "(empty)", "set → object-storage mode; empty → all-in-Redis mode"],
        ["`S3_ENDPOINT` / `S3_PUBLIC_ENDPOINT`", "(AWS) / = endpoint", "S3 API for the services / host used in presigned URLs"],
        ["`S3_SSE`, `S3_KMS_KEY_ID`", "(none)", "server-side encryption (`aws:kms` + key ARN)"],
        ["`S3_MANAGE_BUCKET`", "1", "create bucket + lifecycle at start (MinIO); 0 on AWS"],
        ["`PRESIGN_SECONDS`", "300", "lifetime of download URLs"],
        ["`RESULT_DELIVERY`", "redirect", "`redirect` (307 to S3) or `proxy` (stream through the API)"],
        ["`MAX_QUEUED_TILES`", "20000", "back-pressure in S3 mode (HTTP 503 beyond it)"],
        ["`STREAM_OUTPUT_MP`", "150", "outputs above this many megapixels are streamed to a tiled TIFF"],
        ["`MODEL_DIR`", "./models", "ONNX weights"],
        ["`PLANNER`, `PLANNER_MARGIN`", "1, 0.2", "planner on/off; distribute only if ≥ 20 % faster"],
        ["`PLANNER_WORKERS`", "0 (= live workers)", "workers the planner may assume (8 on Kubernetes)"],
        ["`PROFILE_EMA`", "0.05", "weight of each new tile in the online c_op estimate"],
        ["`TILE_SIZE`", "512", "tile size when the planner is off"],
        ["`LEASE_SECONDS`, `HEARTBEAT_SECONDS`, `MAX_RETRIES`", "15, 3, 3", "fault-tolerance timing"],
        ["`WORKER_PROCS`, `OPENCV_THREADS`", "1, 1", "process slots per worker; threads per op (also ONNX Runtime)"],
        ["`CHAOS_CRASH_RATE`", "0", "probability a worker crashes mid-tile (fault injection)"],
        ["`POLICY`", "work", "autoscaler rule: `work` or `queue`"],
        ["`LATENCY_TARGET_S`, `TARGET_UTILIZATION`, `POD_STARTUP_S`", "30, 0.8, 0", "work-aware rule parameters"],
        ["`MIN_WORKERS`, `MAX_WORKERS`, `SCALE_DOWN_DELAY`", "1, 8, 30", "autoscaler bounds and scale-down delay"],
        ["`IDLE_SCALE_DOWN_S`", "10", "work mode: once nothing is queued or running for this long, drop to MIN_WORKERS"],
    ], "Environment variables", [6.0, 3.4, 7.1])

    # ---------------------------------------------------------------- 13
    g.sec("Troubleshooting")
    g.tbl(["Symptom", "Cause and fix"], [
        ["`Port 8000 already in use`", "another run_local.py or Compose stack is running: stop it, or pass `--port`, `--redis-port`, `--minio-port`"],
        ["`.tools/bin/minio missing`", "run `bash scripts/fetch_minio.sh`, or use `--storage redis`"],
        ["`hed`/`superres` missing from the list", "weights not downloaded: `bash scripts/fetch_models.sh`"],
        ["HTTP 503 on upload", "back-pressure: the queue (S3 mode) or Redis memory (Redis mode) is full; the client retries after `Retry-After`"],
        ["Result link fails in the browser (Kubernetes)", "presigned host is wrong: set `S3_PUBLIC_ENDPOINT=http://$(minikube ip):30900` on the api"],
        ["Pods stuck in `ErrImageNeverPull`", "image built outside Minikube: run `eval $(minikube docker-env)` and rebuild"],
        ["HPA shows `<unknown>` CPU", "metrics-server not ready: `minikube addons enable metrics-server`, wait a minute"],
        ["`no matches for kind ScaledObject`", "KEDA not installed: `bash scripts/install_k8s_addons.sh`"],
        ["Worker pod `OOMKilled` on HED", "raise the worker memory limit in `k8s/30-worker.yaml` or use 512 px tiles"],
        ["Benchmarks much slower than the report", "other load on the machine, or different hardware: re-run `scripts/profile_ops.py`"],
    ], "Common problems", [5.4, 11.1])

    g.page_numbers(9)
    out = g.save(DOCS / "How_to_Run_Guide.docx")
    print("wrote", out.relative_to(ROOT))
    return out


if __name__ == "__main__":
    out = build()
    to_pdf([out], DOCS)
    print("wrote", out.with_suffix(".pdf").relative_to(ROOT))
    if MISSING:
        print("missing images:", *MISSING, sep="\n  ")
