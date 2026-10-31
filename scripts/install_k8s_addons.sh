#!/usr/bin/env bash
# Baselines for the autoscaler comparison, installed into the running Minikube cluster:
#   metrics-server (for the CPU-based HPA) and KEDA (Redis list-length scaler).
set -euo pipefail
KEDA_VERSION="${KEDA_VERSION:-2.21.0}"
minikube addons enable metrics-server
kubectl apply --server-side -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml"
kubectl -n keda rollout status deploy/keda-operator --timeout=300s
kubectl -n kube-system rollout status deploy/metrics-server --timeout=300s
echo "metrics-server and KEDA $KEDA_VERSION ready"
