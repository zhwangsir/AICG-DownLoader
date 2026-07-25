#!/bin/bash
# 启动 video-enhance 容器到 workstation GPU3
# 部署目标: 192.168.71.127 (workstation), GPU3 = NVIDIA RTX PRO 6000 96GB
# 端口: 8290, 显存峰值约 15GB (三模型串行)
set -e

IMAGE_NAME="video-enhance:latest"
CONTAINER_NAME="video-enhance"

echo "[run.sh] 移除已有容器 ${CONTAINER_NAME} ..."
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

echo "[run.sh] 启动容器 ${CONTAINER_NAME} (image=${IMAGE_NAME}, gpu=3, port=8290) ..."
exec docker run -d \
  --name "${CONTAINER_NAME}" \
  --gpus '"device=3"' \
  -e NVIDIA_VISIBLE_DEVICES=3 \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -v /home/merlin/video-models:/workspace/checkpoints:ro \
  -v /tmp/video-enhance-io:/workspace/io \
  -p 8290:8290 \
  --shm-size=8gb \
  --restart unless-stopped \
  "${IMAGE_NAME}" \
  bash -c "python /workspace/serve_api.py --host 0.0.0.0 --port 8290"
