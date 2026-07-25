# xDiT + HunyuanVideo-I2V 视频生成服务镜像
# 基础镜像: PyTorch 2.5.1 + CUDA 12.4 (与 LatentSync 共用, 已有本地缓存)
# xDiT 通过 pip install xdiffusers 安装, 无需官方 dev 镜像
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120 \
    HF_HOME=/root/.cache/huggingface

# 系统依赖: git (diffusers main 分支安装) + ffmpeg (视频读写) + libgl1 (OpenCV 运行时)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 安装 FastAPI 服务层依赖
COPY requirements-serve.txt /tmp/requirements-serve.txt
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /tmp/requirements-serve.txt

# 安装 diffusers main 分支（HunyuanVideo-I2V pipeline 需要最新代码）
# 三级 GitHub 镜像兜底, 全部失败则尝试 PyPI 稳定版
RUN pip install --no-cache-dir --timeout 600 --retries 3 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "git+https://ghproxy.com/https://github.com/huggingface/diffusers.git@main" \
    || pip install --no-cache-dir --timeout 600 --retries 3 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "git+https://gitclone.com/github.com/huggingface/diffusers.git@main" \
    || pip install --no-cache-dir --timeout 600 --retries 3 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "git+https://mirror.ghproxy.com/https://github.com/huggingface/diffusers.git@main" \
    || pip install --no-cache-dir --timeout 600 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        diffusers

# 安装 xDiT (xdiffusers) — 多卡并行推理引擎
# 从 GitHub 安装 (PyPI 上的 xdiffusers 包名不稳定, 直接用官方仓库)
RUN pip install --no-cache-dir --timeout 600 --retries 3 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "git+https://ghproxy.com/https://github.com/xdit-project/xDiT.git@main" \
    || pip install --no-cache-dir --timeout 600 --retries 3 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "git+https://gitclone.com/github.com/xdit-project/xDiT.git@main" \
    || pip install --no-cache-dir --timeout 600 --retries 3 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "git+https://mirror.ghproxy.com/https://github.com/xdit-project/xDiT.git@main" \
    || echo "[WARN] xDiT 安装失败, 将使用 diffusers 原生 pipeline (无多卡并行)"

# 复制服务代码
COPY serve_api.py /workspace/serve_api.py

EXPOSE 8288

# 默认启动命令（run.sh 会覆盖为显式参数）
CMD ["python", "/workspace/serve_api.py", "--host", "0.0.0.0", "--port", "8288"]
