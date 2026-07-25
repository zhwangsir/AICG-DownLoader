#!/bin/bash
# 构建 video-enhance 镜像
# 注意: 首次构建会 clone 3 个仓库 + 编译 mmcv-full, 耗时约 30-60 分钟
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="video-enhance:latest"

echo "[build.sh] 构建镜像 ${IMAGE_NAME} ..."
echo "[build.sh] Dockerfile 目录: ${SCRIPT_DIR}"

docker build -t "${IMAGE_NAME}" -f "${SCRIPT_DIR}/Dockerfile" "${SCRIPT_DIR}"

echo "[build.sh] 构建完成: ${IMAGE_NAME}"
docker images "${IMAGE_NAME}"
