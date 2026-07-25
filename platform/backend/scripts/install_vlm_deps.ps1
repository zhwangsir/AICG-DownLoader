$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:ALL_PROXY = $null

$python = "F:\comfy\ComfyUI\ComfyUI\.venv\Scripts\python.exe"

Write-Host "[1/2] 安装 fastapi + uvicorn..."
& $python -m pip install fastapi uvicorn -i https://mirrors.tuna.tsinghua.edu.cn/pypi/simple/ --trusted-host mirrors.tuna.tsinghua.edu.cn

Write-Host "[2/2] 验证安装..."
& $python -c "import fastapi; print('fastapi', fastapi.__version__); import uvicorn; print('uvicorn', uvicorn.__version__)"

Write-Host "完成"
