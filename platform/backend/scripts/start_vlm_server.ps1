$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:ALL_PROXY = $null
$env:NO_PROXY = "*"
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:HF_HOME = "F:\hf_cache"
$env:HF_HUB_DISABLE_XET = "1"
$env:CUDA_VISIBLE_DEVICES = "1"

$python = "F:\comfy\ComfyUI\ComfyUI\.venv\Scripts\python.exe"
$script = "C:\vlm\vlm_server.py"

Write-Host "[VLM] 启动 VLM 服务..."
Write-Host "[VLM] 模型: Qwen/Qwen3-VL-8B-Instruct"
Write-Host "[VLM] GPU: 1 (CUDA_VISIBLE_DEVICES=1)"
Write-Host "[VLM] 端口: 8200"
Write-Host "[VLM] HF_ENDPOINT: https://hf-mirror.com"
Write-Host "[VLM] HF_HOME: F:\hf_cache"
Write-Host ""

& $python $script --model Qwen/Qwen3-VL-8B-Instruct --port 8200 --gpu 0
