#!/usr/bin/env bash
# One-time install of Docker, Minikube and kubectl on Ubuntu (run as root):
#   sudo bash scripts/install_k8s_tools.sh      (or: pkexec bash scripts/install_k8s_tools.sh)
set -euo pipefail

MINIKUBE_VERSION=v1.37.0
KUBECTL_VERSION=v1.34.1
TARGET_USER="${SUDO_USER:-$(id -nu "${PKEXEC_UID:-1000}")}"

echo "==> Installing Docker (apt)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 docker-buildx curl conntrack
systemctl enable --now docker

echo "==> Adding $TARGET_USER to the docker group"
usermod -aG docker "$TARGET_USER"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "==> Installing minikube $MINIKUBE_VERSION"
base="https://github.com/kubernetes/minikube/releases/download/$MINIKUBE_VERSION"
curl -fsSLO "$base/minikube-linux-amd64"
curl -fsSLO "$base/minikube-linux-amd64.sha256"
echo "$(cat minikube-linux-amd64.sha256)  minikube-linux-amd64" | sha256sum -c -
install -m 755 minikube-linux-amd64 /usr/local/bin/minikube

echo "==> Installing kubectl $KUBECTL_VERSION"
base="https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64"
curl -fsSLO "$base/kubectl"
curl -fsSLO "$base/kubectl.sha256"
echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c -
install -m 755 kubectl /usr/local/bin/kubectl

echo
docker --version
docker compose version
minikube version | head -1
kubectl version --client | head -1
echo
echo "Done. Log out and back in (or run: newgrp docker) so '$TARGET_USER' can use docker without sudo."
