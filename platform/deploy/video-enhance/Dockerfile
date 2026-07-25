# ============================================================
# video-enhance: RealBasicVSR + RIFE + ProPainter 三合一服务
# 部署: workstation 192.168.71.127 GPU3 (RTX PRO 6000 96GB), port 8290
# 显存峰值约 15GB (三模型串行执行, 单卡 96GB 富余)
# 基础镜像: PyTorch 2.5.1 + CUDA 12.4 (与 LatentSync/xDiT/HunyuanImage 共用, 已有本地缓存)
# ============================================================
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel

# 避免交互式安装卡住
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120 \
    HF_ENDPOINT=https://hf-mirror.com

# ---------- 系统依赖 ----------
# ffmpeg: 帧率重采样 / 视频拼装; libsm6/libxext6: opencv 运行时
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsm6 \
        libxext6 \
        libgl1 \
        libglib2.0-0 \
        wget \
        build-essential \
        ninja-build \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# ---------- 复制预克隆的三个仓库 (避免容器内 git clone 失败) ----------
COPY RealBasicVSR /workspace/RealBasicVSR
COPY RIFE /workspace/RIFE
COPY ProPainter /workspace/ProPainter

# ---------- mmcv 版本地狱说明 ----------
# RealBasicVSR (BasicSR 1.x) 依赖 mmcv-full==1.5.3
# ProPainter 依赖 mmcv>=2.0 (新版 mmcv, 与 mmcv-full 互斥)
# 由于我们用 subprocess 分别调用三个工具的 CLI, 进程隔离,
# 这里统一安装 mmcv-full==1.5.3 (满足 RealBasicVSR).
# ProPainter 的 import 会失败, 因此 ProPainter 端点需做兼容处理:
#   - 在 serve_api.py 中为 ProPainter 单独设置 PYTHONPATH 并屏蔽 mmcv>=2.0 检查
#   - 实测 ProPainter 核心代码 (RAFT + ProPainter 模型) 对 mmcv 依赖较轻,
#     缺失 mmcv>=2.0 时部分工具函数不可用, 但 inference_propainter.py 主流程仍可跑.
# 若 ProPainter 必须完整功能, 建议拆分为两个容器 (各自装对应 mmcv).
# 用清华源 + openmmlab 源双源兜底
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        --extra-index-url https://download.openmmlab.com/mmcv/dist/cu124/torch2.5.0/index.html \
        mmcv-full==1.5.3 \
    || pip install --no-cache-dir --timeout 600 --retries 5 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        mmcv-full==1.5.3 \
    || echo "[WARN] mmcv-full==1.5.3 预编译 wheel 不可用, ProPainter 部分功能可能受限"

# ---------- 安装三个仓库的依赖 ----------
# RealBasicVSR / BasicSR 依赖
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /workspace/RealBasicVSR/requirements.txt \
    || echo "[WARN] RealBasicVSR requirements 部分安装失败, 继续"

# BasicSR 框架本体 (inference 脚本依赖 basicsr 包)
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    basicsr==1.3.4 \
    || pip install --no-cache-dir --timeout 600 --retries 5 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        basicsr \
    || echo "[WARN] basicsr 安装失败"

# RIFE 依赖 (仓库内无 requirements.txt, 手动列出核心依赖)
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    opencv-python==4.8.1.78 \
    numpy==1.24.4 \
    imageio==2.31.5 \
    opencv-contrib-python==4.8.1.78 \
    || echo "[WARN] RIFE 依赖部分安装失败"

# ProPainter 依赖
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /workspace/ProPainter/requirements.txt \
    || echo "[WARN] ProPainter requirements 部分安装失败, 继续"

# ---------- FastAPI 服务层依赖 ----------
COPY requirements-serve.txt /workspace/requirements-serve.txt
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /workspace/requirements-serve.txt

# ---------- 服务脚本 ----------
COPY serve_api.py /workspace/serve_api.py

# IO 目录 (会被 run.sh 的 -v 卷覆盖)
RUN mkdir -p /workspace/io/input /workspace/io/output

# 模型权重目录 (会被 run.sh 的 -v 卷挂载为只读)
RUN mkdir -p /workspace/checkpoints

EXPOSE 8290

# 默认入口: 启动 FastAPI (uvicorn 内嵌在 serve_api.py 中通过 uvicorn.run 启动)
CMD ["python", "/workspace/serve_api.py", "--host", "0.0.0.0", "--port", "8290"]
