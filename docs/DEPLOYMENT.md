# AI 短剧工作台 — 部署文档

## 1. 环境要求

### 1.1 开发环境

- macOS / Linux / Windows
- Python 3.11+
- Node.js 20+
- FFmpeg + ffprobe
- uv（推荐）或 pip

### 1.2 生产环境

- **LLM 集群**：4×Mac Studio M3 Ultra 512GB，运行 EXO
  - GLM-5.2-fp8（~753GB）
  - Kimi-K2.7-Code-4bit（~611GB）
- **GPU 工作站**：Windows 11 + 4×RTX PRO 6000 Blackwell Max-Q
  - ComfyUI 0.25.1 多实例：cuda0=8000, cuda1=8002, cuda2=8003, cuda3=8004
- **NAS**：UGREEN DXP8800 Pro 44TB（可选，用于素材/模型存储）

## 2. 后端部署

### 2.1 安装依赖

```bash
cd platform/backend
uv sync --extra dev
```

### 2.2 配置环境变量

创建 `.env` 文件：

```env
EXO_BASE_URL=http://100.64.201.37:52415/v1
EXO_MODEL_GLM52=mlx-community/GLM-5.2-fp8
EXO_MODEL_KIMI=mlx-community/Kimi-K2.7-Code-4bit

COMFYUI_IMAGE_HQ=http://192.168.71.100:8000
COMFYUI_IMAGE_FAST=http://192.168.71.100:8002
COMFYUI_VIDEO_A=http://192.168.71.100:8003
COMFYUI_VIDEO_B=http://192.168.71.100:8004

BACKEND_HOST=0.0.0.0
BACKEND_PORT=8100
CORS_ORIGINS=http://localhost:5173,http://localhost:1420

# 可选：视觉质检模型
VISUAL_MODEL_URL=
VISUAL_MODEL_NAME=mlx-community/Qwen3-VL-235B-A22B-Thinking-fp8
```

### 2.3 运行测试

```bash
uv run pytest
```

### 2.4 启动服务

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8100
```

## 3. 前端部署

### 3.1 安装依赖

```bash
cd platform/frontend
npm install
```

### 3.2 配置代理

`vite.config.ts` 默认代理 `/api` 到 `http://localhost:8100`，如需修改请编辑：

```ts
server: {
  proxy: {
    "/api": "http://localhost:8100",
  },
}
```

### 3.3 运行测试

```bash
npm run test
```

### 3.4 Web 模式启动

```bash
npm run dev
```

### 3.5 Tauri 桌面应用

```bash
npm run tauri dev     # 开发模式
npm run tauri build   # 打包
```

## 4. 可选：部署 Qwen3-VL 视觉质检

1. 在 Mac 集群上部署 Qwen3-VL-235B-A22B-Thinking FP8（~235GB）
2. 暴露 OpenAI 兼容 API
3. 在 `.env` 中设置 `VISUAL_MODEL_URL=http://your-vlm-server/v1`
4. 重启后端

## 5. 健康检查

- 后端健康：`curl http://localhost:8100/api/drama/health`
- API 文档：`http://localhost:8100/docs`
- 前端：`http://localhost:5173`
