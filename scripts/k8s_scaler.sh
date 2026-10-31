#!/usr/bin/env bash
# Switch the worker Deployment between the three autoscalers compared in the report.
#   bash scripts/k8s_scaler.sh work    # our work-aware autoscaler (k8s/40-autoscaler.yaml)
#   bash scripts/k8s_scaler.sh queue   # same autoscaler, original queue-length rule
#   bash scripts/k8s_scaler.sh keda    # KEDA, Redis list length   (needs KEDA installed)
#   bash scripts/k8s_scaler.sh hpa     # Kubernetes HPA, 70 % CPU  (needs metrics-server)
# Only one scaler may own the Deployment's replica count at a time.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=imgframe
mode="${1:?usage: $0 work|queue|keda|hpa}"

kubectl -n "$NS" delete hpa worker-cpu --ignore-not-found >/dev/null
kubectl -n "$NS" delete scaledobject worker-queue --ignore-not-found >/dev/null 2>&1 || true
kubectl -n "$NS" delete hpa keda-hpa-worker-queue --ignore-not-found >/dev/null
kubectl -n "$NS" scale deploy/autoscaler --replicas=0 >/dev/null 2>&1 || true

case "$mode" in
    work|queue)
        kubectl apply -f k8s/40-autoscaler.yaml >/dev/null
        kubectl -n "$NS" set env deploy/autoscaler POLICY="$mode" >/dev/null
        kubectl -n "$NS" scale deploy/autoscaler --replicas=1 >/dev/null ;;
    keda) kubectl apply -f k8s/optional/keda-scaledobject.yaml >/dev/null ;;
    hpa)  kubectl apply -f k8s/optional/hpa-cpu.yaml >/dev/null ;;
    *) echo "unknown scaler $mode"; exit 2 ;;
esac
kubectl -n "$NS" scale deploy/worker --replicas=1 >/dev/null
kubectl -n "$NS" rollout status deploy/worker --timeout=120s >/dev/null
echo "worker Deployment now scaled by: $mode"
