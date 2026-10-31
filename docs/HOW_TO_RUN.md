# How to run

This page is the short version. The full guidebook, with screenshots, explanations and troubleshooting, is [How_to_Run_Guide.pdf](How_to_Run_Guide.pdf).

```bash
git clone https://github.com/GHOSTALERT/cloud-native-ing-proc.git
cd cloud-native-ing-proc
```

## Requirements

- Linux with Python 3.11+ (tested on 3.13)
- Docker, for the MinIO binary, Docker Compose and Minikube
- For Kubernetes: Minikube and kubectl (`sudo bash scripts/install_k8s_tools.sh`)

## One-time setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
bash scripts/build_redis.sh          # .tools/bin/redis-server
bash scripts/fetch_minio.sh          # .tools/bin/minio (needs Docker once)
bash scripts/fetch_models.sh         # models/hed.onnx (via scripts/convert_hed.py) + Real-ESRGAN, SHA-256 checked
.venv/bin/python scripts/make_samples.py
```

## A. Local, no Docker

Redis + MinIO + API + N worker processes on one machine:

```bash
.venv/bin/python scripts/run_local.py --workers 4                   # http://127.0.0.1:8000
.venv/bin/python scripts/run_local.py --workers 4 --demo --op hed   # scripted end-to-end run
.venv/bin/python scripts/run_local.py --workers 4 --storage redis   # original all-in-Redis mode
.venv/bin/python scripts/run_local.py --workers 4 --demo --kill-worker   # fault tolerance demo
```

## B. Docker Compose

```bash
docker compose up --build --scale worker=4                          # http://localhost:8000
```

## C. Kubernetes (Minikube)

```bash
minikube start --driver=docker --cpus=8 --memory=12g
eval $(minikube docker-env) && docker build -t imgframe:latest .
kubectl apply -f k8s/
kubectl -n imgframe set env deploy/api S3_PUBLIC_ENDPOINT=http://$(minikube ip):30900
# UI: http://$(minikube ip):30080
```

### Autoscalers: work-aware, HPA, KEDA

```bash
bash scripts/install_k8s_addons.sh                 # metrics-server + KEDA 2.21 (once)
bash scripts/k8s_scaler.sh work                    # or hpa / keda: exactly one scaler controls the workers
bash scripts/run_autoscale_comparison.sh 3         # ~45 min: 3 interleaved runs each, then the plot
```

## Tests, calibration and benchmarks

```bash
.venv/bin/python -m pytest -q                                   # 43 tests
.venv/bin/python scripts/calibrate_halo.py                      # halo sweep -> imgframe/calibration.json
.venv/bin/python scripts/profile_ops.py                         # costs + capacity curves -> imgframe/profiles.json
.venv/bin/python -m benchmark.bench_planner                     # engine: naive vs planner, model accuracy
.venv/bin/python -m benchmark.bench_planner --cluster           # full framework with S3
.venv/bin/python -m benchmark.bench_storage                     # Redis mode vs S3 mode
.venv/bin/python -m benchmark.bench_local --ops hed --size 2048 # neural scaling
```

Results go to `benchmark/results/` (JSON, CSV, logs).

## Rebuild figures and documents

```bash
.venv/bin/python docs/make_figures.py                           # docs/figures/
.venv/bin/python docs/build_report.py                           # IEEE report (.docx + .pdf, needs LibreOffice for PDF)
.venv/bin/python docs/build_slides.py                           # 15-slide deck
.venv/bin/python docs/build_guide.py                            # the PDF guidebook
.venv/bin/python scripts/capture_screenshots.py --k8s           # UI and terminal screenshots
```
