#!/bin/bash
# 启动 xDiT HunyuanVideo-I2V 服务容器
# 部署目标: workstation 192.168.71.127 GPU3 (NVIDIA RTX PRO 6000 96GB)
# 端口: 8288, 单卡模式, 显存约 35-45GB (FP8)
set -e

IMAGE_NAME="xdit-hunyuanvideo:latest"
CONTAINER_NAME="xdit-hunyuanvideo"

# 清理旧容器
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

# 确保宿主机 IO 目录存在
mkdir -p /tmp/xdit-io/output /tmp/xdit-io/upload

exec docker run -d --name "${CONTAINER_NAME}" \
  --gpus '"device=3"' \
  -e NVIDIA_VISIBLE_DEVICES=3 \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -e SERVICE_HOST=192.168.71.127 \
  -e SERVICE_PORT=8288 \
  -e ENABLE_CPU_OFFLOAD=true \
  -v /home/merlin/.cache/huggingface:/root/.cache/huggingface \
  -v /tmp/xdit-io:/workspace/io \
  -p 8288:8288 \
  --shm-size=32gb \
  --restart unless-stopped \
  "${IMAGE_NAME}" \
  bash -c "python /workspace/serve_api.py --host 0.0.0.0 --port 8288"
