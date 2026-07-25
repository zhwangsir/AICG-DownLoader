#!/bin/bash
# 启动 LatentSync 1.6 唇形同步容器
# 部署: workstation GPU1 (NVIDIA RTX PRO 6000 96GB), 端口 8289, 显存 ~18GB
# 网络模式: host (避免 Docker NAT 与 IPv6 冲突)
# 注意: 不挂载 /workspace — serve_api.py 与 LatentSync 仓库均已在镜像内 (Dockerfile COPY + git clone),
#       挂载 /workspace 会覆盖镜像内的 LatentSync 目录导致 import 失败。
# 模型权重: 通过 -v 挂载宿主机预下载的 checkpoints 到 /workspace/LatentSync/checkpoints (只读),
#           避免 predict.py 走 Replicate weights.replicate.delivery 下载 (该 URL 已不返回 1.6 权重)。
set -e

docker rm -f latentsync 2>/dev/null || true

exec docker run -d --name latentsync --gpus '"device=1"' \
  -e NVIDIA_VISIBLE_DEVICES=1 \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -v /home/merlin/.cache/huggingface:/root/.cache/huggingface \
  -v /home/merlin/deploys/latentsync/checkpoints:/workspace/LatentSync/checkpoints:ro \
  -v /tmp/latentsync-io:/workspace/io \
  --network host --shm-size=8gb --restart unless-stopped \
  latentsync-1.6:latest
