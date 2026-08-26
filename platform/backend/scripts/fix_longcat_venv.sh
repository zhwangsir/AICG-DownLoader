#!/bin/bash
# M22.1 LongCat venv 确定性修复：清理 cu13 污染 → torch 2.8.0+cu128 → 依赖 → FA2
set -e
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export UV_HTTP_TIMEOUT=600
VENV=/home/merlin/longcat-video/.venv
UV="/home/merlin/.local/bin/uv pip install --python $VENV/bin/python"
PIP="$VENV/bin/pip"

echo "=== [1/5] 卸载可能受污染的 CUDA 栈 ==="
$VENV/bin/python -m pip uninstall -y torch torchvision triton flash-attn 2>/dev/null || true

echo "=== [2/5] 安装 torch 2.8.0+cu128（PyPI 2.8.0 默认即 cu128 构建）==="
$UV torch==2.8.0 torchvision==0.23.0
$VENV/bin/python -c 'import torch; v=torch.__version__; c=torch.version.cuda; print("torch", v, "cuda", c); assert v=="2.8.0+cu128" and c=="12.8", f"WRONG TORCH {v} cu{c}"'

echo "=== [3/5] 安装项目依赖（剔除 torch/flash-attn 行）==="
grep -v -E "^torch==|^flash-attn" /home/merlin/longcat-video/requirements.txt > /tmp/longcat_req.txt
$UV -r /tmp/longcat_req.txt
$UV ninja psutil packaging

echo "=== [4/5] 再次校验 torch 未被依赖解析替换 ==="
$VENV/bin/python -c 'import torch; v=torch.__version__; c=torch.version.cuda; print("after reqs:", v, "cuda", c); assert v=="2.8.0+cu128" and c=="12.8", f"TORCH REPLACED {v} cu{c}"'

echo "=== [5/5] 安装 flash_attn 2.8.3 (cu12torch2.8 abiTRUE) ==="
$UV /home/merlin/flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp310-cp310-linux_x86_64.whl
$VENV/bin/python -c '
import flash_attn
from flash_attn import flash_attn_func
print("flash_attn", flash_attn.__version__, "kernel import ok")
'
echo "ALL_DEPS_OK"
