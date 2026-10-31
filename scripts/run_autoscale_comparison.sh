#!/usr/bin/env bash
# Three-way autoscaler comparison on Minikube: HPA (CPU) vs KEDA (queue length) vs
# work-aware, the same seeded trace replayed RUNS times each, scalers interleaved so slow
# drift on the host affects all three alike. Before every run it waits until the queue is
# empty and the worker Deployment is back at one replica.
#   bash scripts/run_autoscale_comparison.sh [RUNS]
set -euo pipefail
cd "$(dirname "$0")/.."
RUNS="${1:-3}"
API="http://$(minikube ip):30080"
PY="${PYTHON:-.venv/bin/python}"

settle() {
    for _ in $(seq 1 120); do
        reps=$(kubectl -n imgframe get deploy worker -o jsonpath='{.status.readyReplicas}')
        q=$(curl -s "$API/metrics" | "$PY" -c "import json,sys; m=json.load(sys.stdin); print(m['pending'] + m['processing'])")
        [ "${reps:-0}" = "1" ] && [ "$q" = "0" ] && return 0
        sleep 5
    done
    echo "did not settle" >&2
}

for run in $(seq 1 "$RUNS"); do
    for scaler in hpa keda work; do
        echo "=== $scaler run $run ==="
        bash scripts/k8s_scaler.sh "$scaler"
        settle
        sleep 20   # HPA/KEDA need a few metric samples at the idle state
        "$PY" scripts/trace_replay.py --api "$API" --label "$scaler" --run "$run" | tail -14
    done
done
bash scripts/k8s_scaler.sh work
"$PY" docs/make_figures.py autoscale
