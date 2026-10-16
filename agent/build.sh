#!/usr/bin/env bash
set -Eeuo pipefail
AGENT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：bash agent/build.sh [输出目录]；使用 Docker manylinux2014 构建本机架构的 Linux 独立包，无需在节点安装 Python/pip。'
  echo '--help 显示帮助；AGENT_BUILD_IMAGE 可指定已审核的镜像 digest。'
  exit 0
fi
[[ "$(uname -s)" == Linux ]] || { echo '请在 Linux 构建；目标节点不需要 Docker。' >&2; exit 1; }
ARCH=$(uname -m)
[[ "$ARCH" == x86_64 || "$ARCH" == aarch64 ]] || { echo "暂不支持架构：$ARCH" >&2; exit 1; }
command -v docker >/dev/null || { echo '构建机需要 Docker。' >&2; exit 1; }
OUTPUT="${1:-$AGENT_DIR/dist}"
mkdir -p -- "$OUTPUT"
OUTPUT="$(cd -- "$OUTPUT" && pwd -P)"
mkdir -p -- "$OUTPUT/.build-cache"
IMAGE="${AGENT_BUILD_IMAGE:-quay.io/pypa/manylinux2014_$ARCH}"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
DIGEST=$(docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE")
docker run --rm --cpus=2 --memory=2g --cap-drop=ALL --security-opt=no-new-privileges \
  -e AGENT_BUILD_IMAGE="$DIGEST" -e PYTHONDONTWRITEBYTECODE=1 -e XDG_CACHE_HOME=/tmp/cache \
  -v "$AGENT_DIR:/source:ro" -v "$OUTPUT:/output" -v "$OUTPUT/.build-cache:/cache" "$IMAGE" \
  /opt/python/cp311-cp311/bin/python /source/build_bundle.py
