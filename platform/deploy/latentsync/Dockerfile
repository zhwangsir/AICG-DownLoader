# LatentSync 1.6 唇形同步服务镜像
# 部署: workstation GPU1 (NVIDIA RTX PRO 6000 96GB), 端口 8289, 显存 ~18GB
# 基础镜像升级: 2.5.1 + CUDA 12.4 + cuDNN 9 (自带 torch 2.5.1, 免下载 ~2GB wheel)
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120 \
    HF_HOME=/root/.cache/huggingface \
    # flash-attn 编译资源限制，避免 OOM
    MAX_JOBS=4 \
    NVCC_THREADS=2

# 系统依赖: git(克隆仓库) / ffmpeg(音视频预处理) / wget(下载 wheel) / OpenCV 运行时库 / 构建工具(flash-attn)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        wget \
        libgl1 \
        libglib2.0-0 \
        build-essential \
        ninja-build \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 复制预克隆的 LatentSync 仓库（避免容器内 git clone GitHub GnuTLS 兼容性问题）
COPY LatentSync /workspace/LatentSync

# 安装 LatentSync 自身依赖
# 关键优化: 用 sed 从 requirements.txt 中移除 torch / torchvision 行,
#           避免覆盖基础镜像自带的 torch 2.5.1+cu124 (省 ~2GB 下载, 解决 SHA256 校验失败问题)
# 强制只用清华源 (--no-index 移除 PyPI 默认源, 避免走 files.pythonhosted.org 超时)
# 增加 --retries 5 应对偶发网络抖动
RUN cd /workspace/LatentSync \
    && sed -i '/^torch[=<>!]/d; /^torchvision[=<>!]/d' requirements.txt \
    && pip install --no-cache-dir --timeout 600 --retries 5 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        -r requirements.txt

# 安装 flash-attn 2.7.3
# 策略: 直接 COPY 宿主机预下载的 wheel 进镜像 (避免容器内下载 GitHub Release 不稳定)
# wheel 来源: https://gh-proxy.com/https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.3/flash_attn-2.7.3+cu12torch2.5cxx11abiFALSE-cp311-cp311-linux_x86_64.whl
# 对应: torch 2.5 + cu12 + cp311 + cxx11abiFALSE (匹配基础镜像 torch 2.5.1+cu124)
# 注: 必须保留原始 wheel 文件名, pip 通过文件名识别 wheel 元数据
COPY flash_attn-2.7.3+cu12torch2.5cxx11abiFALSE-cp311-cp311-linux_x86_64.whl /tmp/
RUN pip install --no-cache-dir /tmp/flash_attn-2.7.3+cu12torch2.5cxx11abiFALSE-cp311-cp311-linux_x86_64.whl \
    && rm -f /tmp/flash_attn-2.7.3+cu12torch2.5cxx11abiFALSE-cp311-cp311-linux_x86_64.whl

# 安装 FastAPI 服务层依赖
COPY requirements-serve.txt /workspace/requirements-serve.txt
RUN pip install --no-cache-dir --timeout 600 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /workspace/requirements-serve.txt

# 拷贝服务入口 (模型权重通过 HF/卷挂载, 不在镜像内下载)
COPY serve_api.py /workspace/serve_api.py

WORKDIR /workspace/LatentSync

EXPOSE 8289

CMD ["python", "/workspace/serve_api.py", "--host", "0.0.0.0", "--port", "8289"]
