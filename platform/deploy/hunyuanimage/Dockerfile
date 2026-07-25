# HunyuanImage 2.1 FP8 图像生成服务
# 基础镜像: PyTorch 2.5.1 + CUDA 12.4 + cuDNN 9 (与 LatentSync/xDiT 共用, 已有本地缓存)
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel

# 基础系统工具 (git/ffmpeg/libgl1)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PyPI 清华镜像源
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /workspace

# 复制预克隆的 HunyuanImage 仓库（避免容器内 git clone 失败）
COPY HunyuanImage /workspace/HunyuanImage

# 安装仓库自带依赖 (强制只用清华源, --retries 应对网络抖动)
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /workspace/HunyuanImage/requirements.txt

# 安装 flash-attn 2.7.3 (--no-build-isolation, 复用基础镜像 torch 2.5.1)
# 预装 ninja/packaging/wheel 以支持源码编译
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        ninja packaging wheel && \
    pip install --no-cache-dir --no-build-isolation --timeout 600 --retries 5 \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        flash-attn==2.7.3

# 拷贝服务封装层与依赖清单
COPY requirements-serve.txt /workspace/requirements-serve.txt
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /workspace/requirements-serve.txt

COPY serve_api.py /workspace/serve_api.py

# 环境变量: 显存可扩展段 + PYTHONPATH 让 hyimage 可被导入 + HF 镜像加速
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    PYTHONPATH=/workspace/HunyuanImage \
    HF_ENDPOINT=https://hf-mirror.com

# 输出目录 (生成图片落地处, 运行时挂载到宿主机 /tmp/hunyuanimage-io)
RUN mkdir -p /workspace/io/output

EXPOSE 8600

WORKDIR /workspace/HunyuanImage

# 默认启动命令 (run.sh 会覆盖为显式 bash -c 形式)
CMD ["python", "/workspace/serve_api.py", "--host", "0.0.0.0", "--port", "8600"]
