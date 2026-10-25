#!/usr/bin/env bash
# Build a local redis-server into .tools/bin (no sudo needed) for running the
# framework and benchmarks without Docker. Needs gcc + make.
set -euo pipefail
VERSION=7.2.5
SHA256=5981179706f8391f03be91d951acafaeda91af7fac56beffb2701963103e423d
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.tools/src" "$ROOT/.tools/bin"
cd "$ROOT/.tools/src"
curl -fsSL -o redis.tar.gz "https://download.redis.io/releases/redis-$VERSION.tar.gz"
echo "$SHA256  redis.tar.gz" | sha256sum -c -
tar xzf redis.tar.gz
make -C "redis-$VERSION" -j"$(nproc)" BUILD_TLS=no MALLOC=libc >/dev/null
cp "redis-$VERSION/src/redis-server" "redis-$VERSION/src/redis-cli" "$ROOT/.tools/bin/"
"$ROOT/.tools/bin/redis-server" --version
