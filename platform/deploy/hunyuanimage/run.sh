#!/bin/bash
# 启动 HunyuanImage 2.1 FP8 容器 (workstation GPU3, 端口 8600)
set -e

docker rm -f hunyuanimage 2>/dev/null || true

exec docker run -d --name hunyuanimage --gpus '"device=3"' -e NVIDIA_VISIBLE_DEVICES=3 \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -v /home/merlin/.cache/huggingface:/root/.cache/huggingface \
  -v /tmp/hunyuanimage-io:/workspace/io \
  -p 8600:8600 --shm-size=16gb --restart unless-stopped \
  hunyuanimage:2.1-fp8 \
  bash -c "cd /workspace/HunyuanImage && python /workspace/serve_api.py --host 0.0.0.0 --port 8600"
