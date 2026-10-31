# Cloud-Native Parallel Computing Framework for Scalable Image Processing

BECE355L Advanced Cloud Computing project (Fall 2026–27, faculty: Aparna Mohanty).
Team: Prapti Kurkuri (24BVD0185), Vaanya Garg (24BVD0139), Aditya Manoj (24BVD0070), Arrnav Trisal (24BVD0064).

The framework splits a large image into tiles. Each tile carries a halo (overlap), so the stitched output matches whole-image processing. Tiles are scheduled through a reliable Redis queue to stateless worker pods, which process them with OpenCV or ONNX Runtime; the results are then stitched back together. Version 2 adds four upgrades:

| # | Upgrade | What changed | Headline result |
|---|---|---|---|
| 1 | **Object storage** | Inputs, tiles and results live in S3 / MinIO. Redis holds only task IDs, leases, costs and job state. Results are delivered as presigned URLs. Huge outputs stream to a tiled BigTIFF | Redis per 8192² job: 226 MB → 0.05 MB; concurrent 8192² jobs: 2 → 12+ |
| 2 | **Deep-learning ops** | HED edge detection and Real-ESRGAN ×4 super-resolution (ONNX). Halos are calibrated from the effective receptive field; halos are stride-aligned and stitching is scale-aware | Halos 98 → 80 px (HED) and 34 → 4 px (ESRGAN), seam PSNR ≥ 40 dB |
| 3 | **Cost-aware planner** | A calibrated time model (compute, per-tile overhead, measured capacity curve) chooses local vs distributed, the tile size and the worker count. It updates its costs online | 25 % MAPE vs 40 % for a linear model; naive grayscale 0.06× → 1.11× |
| 4 | **Work-aware autoscaling** | `replicas = max(⌈W/(D·u)⌉, ⌈λ/u⌉)` from queued compute-seconds W, compared with HPA and KEDA on a replayed trace | Every job within the 30 s target with 9 % fewer pod-seconds than KEDA; HPA met it for only 54 % |

Deliverables are in `docs/`:

- `Project_Report_IEEE.pdf`: the IEEE-format report
- `Project_Presentation.pptx`: 15 slides
- `How_to_Run_Guide.pdf`: the detailed guidebook

![Architecture](docs/figures/fig_architecture_v2.png)

## Quick start

```bash
cd ~/Downloads/"cloud proj"

# First time after cloning
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
bash scripts/build_redis.sh          # .tools/bin/redis-server
bash scripts/fetch_minio.sh          # .tools/bin/minio (needs Docker once)
bash scripts/fetch_models.sh         # models/hed.onnx (via scripts/convert_hed.py) + Real-ESRGAN, SHA-256 checked
.venv/bin/python scripts/make_samples.py

# A. Local, no Docker: Redis + MinIO + API + N workers
.venv/bin/python scripts/run_local.py --workers 4                   # http://127.0.0.1:8000
.venv/bin/python scripts/run_local.py --workers 4 --demo --op hed   # scripted end-to-end run
.venv/bin/python scripts/run_local.py --workers 4 --storage redis   # original all-in-Redis mode

# B. Docker Compose (Redis + MinIO + API + workers)
docker compose up --build --scale worker=4                          # http://localhost:8000

# C. Kubernetes (Minikube)
minikube start --driver=docker --cpus=8 --memory=12g
eval $(minikube docker-env) && docker build -t imgframe:latest .
kubectl apply -f k8s/
kubectl -n imgframe set env deploy/api S3_PUBLIC_ENDPOINT=http://$(minikube ip):30900
# UI: http://$(minikube ip):30080

# Autoscaler baselines + comparison
bash scripts/install_k8s_addons.sh                 # metrics-server + KEDA 2.21
bash scripts/k8s_scaler.sh work|hpa|keda           # exactly one scaler controls the workers
bash scripts/run_autoscale_comparison.sh 3         # 3 runs each, then plots
```

The full step-by-step guide, including troubleshooting, is in **docs/How_to_Run_Guide.pdf**.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs` (multipart: `file`, `op`, `tile_size` = `auto` or px) | Submit an image; `auto` lets the planner decide |
| GET | `/jobs`, `/jobs/{id}` | Status, progress, plan, predicted vs measured time, timings |
| GET | `/jobs/{id}/result?format=png\|jpg\|webp\|tif&max_side=N` | Result (307 to a presigned URL in S3 mode) |
| GET | `/jobs/{id}/url` | Presigned URL as JSON |
| GET | `/jobs/{id}/preview` | Downscaled upload |
| GET | `/plan?op=hed&width=4096&height=4096` | Dry-run the planner |
| DELETE | `/jobs/{id}` | Delete a job, its objects and its queued work |
| GET | `/metrics` | Queue, workers, work-seconds, storage mode, Redis memory |
| GET | `/ops` | Available operations (neural ones only if their model file exists) |
| GET | `/docs` | Swagger UI |

Operations: `grayscale`, `canny`, `sobel`, `gaussian_blur`, `sharpen`, `bilateral`, `hed`, `superres` (×4).

## Tests, calibration and benchmarks

```bash
.venv/bin/python -m pytest -q                                   # 42 tests
.venv/bin/python scripts/calibrate_halo.py                      # halo sweep -> imgframe/calibration.json
.venv/bin/python scripts/profile_ops.py                         # costs + capacity curves -> imgframe/profiles.json
.venv/bin/python -m benchmark.bench_planner                     # engine: naive vs planner, model accuracy
.venv/bin/python -m benchmark.bench_planner --cluster           # full framework with S3
.venv/bin/python -m benchmark.bench_storage                     # Redis mode vs S3 mode
.venv/bin/python -m benchmark.bench_local --ops hed --size 2048 # neural scaling
.venv/bin/python docs/make_figures.py                           # figures -> docs/figures/
.venv/bin/python docs/build_report.py && .venv/bin/python docs/build_slides.py && .venv/bin/python docs/build_guide.py
```

Results go to `benchmark/results/` (JSON, CSV, logs).

## Layout

```
imgframe/     config, ops (OpenCV + ONNX), models, tiling, store (Redis + Lua), objstore (S3), planner,
              profiles.json (calibrated costs), calibration.json (calibrated halos)
api/          FastAPI service + web UI
worker/       worker process (online cost updates, streamed TIFF output)
autoscaler/   work-aware / queue-length autoscaler
benchmark/    bench_local, bench_distributed, bench_planner, bench_storage, results/
k8s/          manifests (MinIO, Redis, API, workers, autoscaler); optional/ HPA and KEDA
deploy/aws/   S3 bucket policy, IAM policies, lifecycle, IRSA service accounts, setup_s3.sh (templates)
scripts/      run_local, convert_hed, calibrate_halo, profile_ops, trace_replay, k8s_scaler, ...
tests/        pytest suite
docs/         report, slides, guide, figures, screenshots
```

## Configuration (new in v2)

| Variable | Default | Meaning |
|---|---|---|
| `S3_BUCKET` | unset | Set to enable object-storage mode |
| `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT` | — | In-cluster endpoint; endpoint used in presigned URLs |
| `S3_SSE`, `S3_KMS_KEY_ID` | — | Server-side encryption (`aws:kms` on AWS) |
| `PRESIGN_SECONDS` | 300 | Validity of result URLs |
| `RESULT_DELIVERY` | redirect | `redirect` (307) or `proxy` |
| `MAX_QUEUED_TILES` | 20000 | Back-pressure in S3 mode |
| `STREAM_OUTPUT_MP` | 150 | Outputs above this many megapixels are streamed to tiled BigTIFF |
| `MODEL_DIR` | `models/` | ONNX model files |
| `PLANNER`, `PLANNER_MARGIN`, `PLANNER_WORKERS` | 1, 0.2, 0 | Planner on/off, safety margin, worker count override |
| `PROFILE_EMA` | 0.05 | Weight of online cost updates |
| `POLICY` | work | Autoscaler: `work` or `queue` |
| `LATENCY_TARGET_S`, `TARGET_UTILIZATION`, `POD_STARTUP_S` | 30, 0.8, 5 | Work-aware autoscaler |

The v1 settings (`REDIS_URL`, `TILE_SIZE`, `LEASE_SECONDS`, `HEARTBEAT_SECONDS`, `MAX_RETRIES`, `WORKER_PROCS`, `OPENCV_THREADS`, `CHAOS_CRASH_RATE`, `RESULT_TTL_SECONDS`, `REDIS_MEMORY_BUDGET`, `MAX_CONCURRENT_UPLOADS`) are unchanged.

## Notes

- The AWS templates in `deploy/aws/` are complete but were not deployed. MinIO stood in for S3.
- All measurements come from one 8-core / 16-thread laptop. Neural operations are memory-bandwidth bound there, so they peak at about 2.5×.
