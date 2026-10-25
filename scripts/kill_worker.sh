#!/usr/bin/env bash
# Fault-tolerance demo: wait until workers are busy with tiles, then break one worker.
#   bash scripts/kill_worker.sh local        # SIGKILL a run_local.py worker process
#   bash scripts/kill_worker.sh compose      # docker kill a Compose worker container
#   bash scripts/kill_worker.sh k8s          # SIGKILL a worker pod's container (Kubernetes restarts it)
#   bash scripts/kill_worker.sh k8s-delete   # delete a worker pod (Deployment replaces it)
# Start it before or right after scripts/load_test.py; it waits for the work to begin.
set -euo pipefail

mode="${1:-local}"
case "$mode" in
    local|compose) api="${API:-http://127.0.0.1:8000}" ;;
    k8s|k8s-delete) api="${API:-http://$(minikube ip):30080}" ;;
    *) echo "usage: $0 local|compose|k8s|k8s-delete"; exit 2 ;;
esac

echo "waiting for workers to be busy at $api ..."
# A large backlog (200+ pending tiles, ~2 s of work) plus tiles in progress means every
# worker is holding a tile right now, so kill straight away.
busy='"pending":([2-9][0-9]{2}|[1-9][0-9]{3,}),"processing":[1-9]'
until curl -fs "$api/metrics" | grep -Eq "$busy"; do sleep 0.2; done
sleep 0.2

case "$mode" in
    local)
        pid="$(pgrep -o -u "$USER" -f 'python -m worker[.]main')"
        echo "killing worker process $pid with SIGKILL"
        kill -9 "$pid" ;;
    compose)
        echo "killing container imgframe-worker-1"
        docker kill imgframe-worker-1 >/dev/null ;;
    k8s|k8s-delete)
        # The oldest worker pod is the one already busy (newly scaled pods may not have claimed a tile).
        pod="$(kubectl -n imgframe get pods -l app=worker --field-selector=status.phase=Running \
                --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[0].metadata.name}')"
        if [ "$mode" = k8s-delete ]; then
            echo "deleting pod $pod (SIGTERM: in-flight tiles go straight back to the queue)"
            kubectl -n imgframe delete pod "$pod" --wait=false
        else
            cid="$(minikube ssh -- docker ps -q \
                    --filter "label=io.kubernetes.pod.name=$pod" \
                    --filter "label=io.kubernetes.container.name=worker" | tr -d '\r')"
            echo "crashing pod $pod (SIGKILL container $cid: its tiles are retried after the lease expires)"
            minikube ssh -- docker kill --signal KILL "$cid" >/dev/null
        fi
        sleep 2
        kubectl -n imgframe get pods -l app=worker ;;
esac
