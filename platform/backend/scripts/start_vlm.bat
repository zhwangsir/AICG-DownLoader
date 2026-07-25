@echo off
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HOME=F:\hf_cache"
set "HF_HUB_DISABLE_XET=1"
set "CUDA_VISIBLE_DEVICES=1"

echo [VLM] Starting VLM Server...
echo [VLM] Model: Qwen/Qwen3-VL-4B-Instruct
echo [VLM] GPU: 1
echo [VLM] Port: 8200

"F:\comfy\ComfyUI\ComfyUI\.venv\Scripts\python.exe" "C:\vlm\vlm_server.py" --model F:\vlm_model --port 8200 --gpu 0 >> C:\vlm\vlm2.log 2>&1
