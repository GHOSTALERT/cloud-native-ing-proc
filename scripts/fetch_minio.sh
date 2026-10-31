#!/usr/bin/env bash
# Put a MinIO server binary in .tools/bin for no-Docker local runs (scripts/run_local.py).
# MinIO no longer publishes binaries or Docker Hub images; Chainguard builds it from the
# open-source code (AGPLv3), so the binary is copied out of that image. Needs Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${MINIO_IMAGE:-cgr.dev/chainguard/minio:latest}"
mkdir -p "$ROOT/.tools/bin"
docker pull -q "$IMAGE"
cid="$(docker create "$IMAGE")"
trap 'docker rm "$cid" >/dev/null' EXIT
docker cp "$cid:/usr/bin/minio" "$ROOT/.tools/bin/minio"
"$ROOT/.tools/bin/minio" --version | head -1
