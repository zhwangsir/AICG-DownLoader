#!/bin/bash
# 下载三个模型的权重到 /home/merlin/video-models/
# - RealBasicVSR_x4.pth        (~60MB)
# - RIFE_v4.6.pkl              (~50MB)
# - ProPainter.pth             (~140MB)
# - recurrent_flow_completion.pth (~140MB)
# - raft-things.pth            (~140MB)
# 总计约 1.5GB, 首次下载较慢, 建议在 workstation 上执行
set -e

# 模型目标目录, 由调用方通过环境变量覆盖, 默认 /home/merlin/video-models
MODEL_DIR="${MODEL_DIR:-/home/merlin/video-models}"
mkdir -p "${MODEL_DIR}"
cd "${MODEL_DIR}"

echo "[download_models.sh] 目标目录: ${MODEL_DIR}"

# 走 mihomo 代理 (HuggingFace/GoogleDrive 在国内不稳定)
# 如已直连可注释掉下面两行
export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7890}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7890}"

# 1. RealBasicVSR_x4.pth (HuggingFace mirror)
if [ ! -f "RealBasicVSR_x4.pth" ]; then
  echo "[download_models.sh] 下载 RealBasicVSR_x4.pth ..."
  wget -c -O RealBasicVSR_x4.pth \
    "https://huggingface.co/ckkelvinchan/RealBasicVSR/resolve/main/RealBasicVSR_x4.pth" \
    || wget -c -O RealBasicVSR_x4.pth \
    "https://huggingface.co/spaces/ckkelvinchan/RealBasicVSR/resolve/main/RealBasicVSR_x4.pth"
else
  echo "[download_models.sh] RealBasicVSR_x4.pth 已存在, 跳过"
fi

# 2. RIFE_v4.6.pkl (GitHub Release)
if [ ! -f "RIFE_v4.6.pkl" ]; then
  echo "[download_models.sh] 下载 RIFE_v4.6.pkl ..."
  wget -c -O RIFE_v4.6.pkl \
    "https://github.com/hzwer/ECCV2022-RIFE/releases/download/v1.0/RIFE_v4.6.pkl"
else
  echo "[download_models.sh] RIFE_v4.6.pkl 已存在, 跳过"
fi

# 3. ProPainter 三件套 (GoogleDrive 镜像 + HuggingFace 备用)
if [ ! -f "ProPainter.pth" ]; then
  echo "[download_models.sh] 下载 ProPainter.pth ..."
  wget -c -O ProPainter.pth \
    "https://huggingface.co/camenduru/ProPainter/resolve/main/ProPainter.pth"
else
  echo "[download_models.sh] ProPainter.pth 已存在, 跳过"
fi

if [ ! -f "recurrent_flow_completion.pth" ]; then
  echo "[download_models.sh] 下载 recurrent_flow_completion.pth ..."
  wget -c -O recurrent_flow_completion.pth \
    "https://huggingface.co/camenduru/ProPainter/resolve/main/recurrent_flow_completion.pth"
else
  echo "[download_models.sh] recurrent_flow_completion.pth 已存在, 跳过"
fi

if [ ! -f "raft-things.pth" ]; then
  echo "[download_models.sh] 下载 raft-things.pth ..."
  wget -c -O raft-things.pth \
    "https://huggingface.co/camenduru/ProPainter/resolve/main/raft-things.pth"
else
  echo "[download_models.sh] raft-things.pth 已存在, 跳过"
fi

echo "[download_models.sh] 下载完成, 目录内容:"
ls -lh "${MODEL_DIR}"
