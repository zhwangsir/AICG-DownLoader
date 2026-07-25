# xDiT + HunyuanVideo-I2V 视频生成服务

AI 短剧项目的图生视频服务，基于 xDiT 官方镜像 + diffusers，通过 FastAPI 包装 HunyuanVideo-I2V 的 Python API 对外提供 HTTP 接口。

- **部署目标**: workstation `192.168.71.127` GPU3 (NVIDIA RTX PRO 6000 96GB)
- **端口**: `8288`
- **模式**: 单卡（world_size=1，ulysses/pipefusion/cfg degree 均为 1）
- **显存**: 约 35-45GB (FP8)，开启 CPU offload 后更低
- **推理耗时**: 720p / 97 帧 / 30 步约 3-8 分钟

> 背景：xDiT 官方镜像 `thufeifeibear/xdit-dev` 的 HTTP `/generate` 端点仅支持 T2I，I2V 需自包装 FastAPI 调用 xDiT Python API。

## 文件说明

| 文件 | 说明 |
|---|---|
| `Dockerfile` | 基于 `thufeifeibear/xdit-dev:latest`，安装 FastAPI 服务层与 diffusers main |
| `serve_api.py` | FastAPI 包装层，暴露视频生成 / 任务查询 / 上传 / 健康检查接口 |
| `requirements-serve.txt` | 服务层附加依赖（diffusers 在 Dockerfile 中单独从 git main 安装） |
| `build.sh` | 构建镜像脚本 |
| `run.sh` | 启动容器脚本（GPU3、端口 8288、挂载 HF 缓存与 IO 目录） |

## 部署步骤

在 workstation（`192.168.71.127`）上执行：

```bash
cd platform/deploy/xdit-video

# 1. 构建镜像（首次较慢，需安装 diffusers main 分支）
bash build.sh

# 2. 准备宿主机目录
mkdir -p /tmp/xdit-io/output /tmp/xdit-io/upload

# 3. 启动容器
bash run.sh

# 4. 查看日志，确认服务就绪
docker logs -f xdit-hunyuanvideo
```

模型权重通过 HF 缓存挂载（`/home/merlin/.cache/huggingface`），首次请求时会自动从 `hf-mirror.com` 下载（已通过 `HF_ENDPOINT` 配置）。镜像内不打包权重。

## API 接口

### `GET /health`
健康检查。返回 `{"status": "ok", "model_loaded": false}`。

### `GET /v1/models`
返回模型列表：`{"data": [{"id": "hunyuanvideo-i2v", ...}]}`。

### `POST /v1/upload`
上传输入图片，返回 `image_url`。

```bash
curl -F "file=@input.jpg" http://192.168.71.127:8288/v1/upload
# {"image_url": "http://192.168.71.127:8288/files/upload/xxxx.jpg", ...}
```

### `POST /v1/videos/generations`
图生视频。请求体（JSON）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model` | string | `hunyuanvideo-i2v` | 模型名 |
| `prompt` | string | `""` | 文本提示词 |
| `image_url` | string | 必填 | I2V 输入图，支持 http(s) URL / base64 / 本服务上传返回的 URL |
| `num_frames` | int | `97` | 帧数，自动对齐 4k+1 |
| `num_inference_steps` | int | `30` | 推理步数 |
| `cfg` | float | `6.0` | guidance scale |
| `seed` | int | `0` | 随机种子，0 表示随机 |
| `size` | string | `720p` | 分辨率预设（`540p`/`720p`/`1080p`）或 `WxH` |

查询参数：
- `async_mode=true`：异步模式，立即返回 `task_id`（推荐）；默认同步模式阻塞等待。

**同步模式示例**：

```bash
curl -X POST http://192.168.71.127:8288/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hunyuanvideo-i2v",
    "prompt": "a girl walking in the park, cinematic",
    "image_url": "http://192.168.71.127:8288/files/upload/xxxx.jpg",
    "num_frames": 97,
    "num_inference_steps": 30,
    "cfg": 6.0,
    "size": "720p"
  }' --max-time 900
```

返回：
```json
{
  "task_id": "...",
  "url": "http://192.168.71.127:8288/files/output/abcd.mp4",
  "elapsed": 320.5,
  "num_frames": 97,
  "size": "1280x720",
  "seed": 123456
}
```

**异步模式示例**（推荐用于生产调用）：

```bash
# 提交任务
curl -X POST "http://192.168.71.127:8288/v1/videos/generations?async_mode=true" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "...", "image_url": "..."}'
# {"task_id": "abc123", "status": "pending"}

# 轮询任务状态
curl http://192.168.71.127:8288/v1/tasks/abc123
```

### `GET /v1/tasks/{task_id}`
查询异步任务状态。状态流转：`pending` → `running` → `succeeded` / `failed`。
`succeeded` 时返回体含 `result.url`；`failed` 时含 `error`。

## 注意事项

1. **单卡模式**：本服务仅用 GPU3，通过 `--gpus '"device=3"'` 与 `NVIDIA_VISIBLE_DEVICES=3` 隔离；xDiT runtime 以 `world_size=1` 初始化，无多卡通信开销。
2. **显存 FP8**：默认 bfloat16 + CPU offload；设置环境变量 `ENABLE_FP8=true` 启用 FP8 量化（需镜像内含 `optimum-quanto`），显存约 35-45GB。
3. **HTTP 超时**：视频生成 3-8 分钟，同步模式客户端超时需 **> 600s**（建议 900s）；推荐使用异步模式。
4. **diffusers main 分支**：HunyuanVideo-I2V pipeline 需 diffusers 最新代码，Dockerfile 中通过 `git+https://...@main` 安装，**不要**用 PyPI 稳定版。
5. **模型懒加载**：首次请求时加载 pipeline（耗时数分钟），后续请求复用；加载过程由 `threading.Lock` 保护。
6. **串行推理**：单线程池执行，避免并发请求导致显存溢出（OOM）。
7. **文件访问**：生成的视频通过 `/files/output/xxx.mp4` 静态访问；上传图片存于 `/files/upload/`。
8. **调用 ComfyUI-LB 纪律**：本服务独立于 ComfyUI，短剧主流程调用本服务时使用 `http://192.168.71.127:8288`。
