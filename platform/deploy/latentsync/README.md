# LatentSync 1.6 唇形同步服务

AI 短剧项目 P4.4 唇形同步服务,基于字节开源 [LatentSync 1.6](https://github.com/bytedance/LatentSync) 的 Docker 化部署,将视频人物口型与配音音频对齐。

- **部署位置**: workstation `192.168.71.127` GPU3 (NVIDIA RTX PRO 6000 96GB)
- **端口**: `8289`
- **显存占用**: ~18GB
- **分辨率**: 512 (LatentSync 1.6 原生)

## 目录文件

| 文件 | 说明 |
|---|---|
| `Dockerfile` | 基于 `pytorch/pytorch:2.3.1-cuda12.1-cudnn8-devel`,clone 官方仓库,装依赖 + flash-attn 2.7.3 + ffmpeg |
| `serve_api.py` | FastAPI 包装层,OpenAI 风格接口 + 兼容接口 |
| `requirements-serve.txt` | FastAPI 服务层依赖 |
| `build.sh` | 构建镜像 `latentsync:1.6` |
| `run.sh` | 启动容器 (GPU3 / 端口 8289 / 卷挂载) |

## 部署步骤

```bash
cd platform/deploy/latentsync

# 1. 构建镜像 (首次较慢, flash-attn 编译约 10-20 分钟)
./build.sh

# 2. 在 workstation 上准备模型权重目录
#    LatentSync 的 unet 权重放到 /home/merlin/latentsync-checkpoints
#    (HF 缓存目录 /home/merlin/.cache/huggingface 已存在)

# 3. 启动容器
./run.sh

# 4. 等待模型加载 (~1-2 分钟), 轮询健康检查
curl http://192.168.71.127:8289/health
# 期望: {"status":"ok","model_ready":true,...}

# 5. 验证模型列表
curl http://192.168.71.127:8289/v1/models
```

## API 接口

### 主接口 (OpenAI 风格,异步任务)

**提交任务**

```http
POST /lip_sync            # 或 /v1/lip_sync (兼容路径)
Content-Type: multipart/form-data
```

表单字段:

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `video` | file | 与 `video_url` 二选一 | - | 输入视频 (mp4) |
| `audio` | file | 与 `audio_url` 二选一 | - | 配音音频 (wav/mp3) |
| `reference_image` | file | 否 | - | 角色参考图 (png, 版本支持时透传) |
| `video_url` | string | 否 | - | 视频 URL (与 `video` 二选一) |
| `audio_url` | string | 否 | - | 音频 URL (与 `audio` 二选一) |
| `reference_image_url` | string | 否 | - | 参考图 URL |
| `inference_steps` | int | 否 | 40 | 扩散推理步数 |
| `guidance_scale` | float | 否 | 1.5 | 引导强度 |

返回:

```json
{"task_id": "abc123...", "status": "pending", "message": "任务已排队", "poll_url": "http://192.168.71.127:8289/tasks/abc123..."}
```

**查询任务**

```http
GET /tasks/{task_id}
```

```json
{
  "task_id": "abc123...",
  "status": "succeeded",          // pending / running / succeeded / failed
  "progress": 100,
  "message": "唇形同步完成",
  "degraded": false,              // true 表示推理失败已降级返回原视频
  "video_url": "http://192.168.71.127:8289/files/output/abc123....mp4",
  "duration_seconds": 5.20
}
```

### 健康检查 / 模型列表

```http
GET /health      # {"status":"ok","model_ready":true,"model_error":null,"tasks_total":0}
GET /v1/models   # OpenAI 风格模型列表
```

### 兼容接口 (供既有 `LatentSyncService` 客户端零改动使用)

为避免破坏现有短剧管线,本服务额外提供与 `platform/backend/app/services/latentsync_service.py` 客户端约定的接口,复用同一任务存储:

- `POST /v1/video/upload` — 上传媒体,返回 `{"filename"}`
- `POST /v1/lipsync/submit` — 提交任务 (JSON: video/audio/reference_image/scene_id...),返回 `{"task_id"}`
- `GET  /v1/lipsync/status/{task_id}` — 返回 `{"status","progress","message","elapsed_seconds"}`
- `GET  /v1/lipsync/result/{task_id}` — 返回 `{"video_url","duration_seconds"}`

## 注意事项

1. **显存**: 单任务 ~18GB,GPU 串行执行 (`ThreadPoolExecutor(max_workers=1)`),并发任务自动排队,避免 OOM。
2. **帧率/采样率**: LatentSync 要求视频 25fps、音频 16000Hz。`serve_api.py` 内部用 ffmpeg 强制转码 (`-r 25` / `-ar 16000 -ac 1`),调用方无需预处理。
3. **首次启动**: 首次推理时 InsightFace 会下载 `buffalo_sc` 人脸检测器到 `~/.cache/huggingface`。`run.sh` 已设置 `HF_ENDPOINT=https://hf-mirror.com` 并挂载 HF 缓存目录加速。建议提前在宿主机执行一次推理预热,或手动放置 `buffalo_sc` 到缓存目录。
4. **模型权重**: 不打进镜像,通过卷挂载:
   - HF 缓存: `/home/merlin/.cache/huggingface` → `/root/.cache/huggingface`
   - LatentSync checkpoints: `/home/merlin/latentsync-checkpoints` → `/workspace/checkpoints`
5. **失败降级**: 推理失败时服务把原视频 (25fps 预处理产物) 复制为结果返回,`/tasks/{id}` 的 `degraded=true`、`message` 含错误原因,调用方可据此决定是否重试。
6. **文件清理**: 上传/下载/预处理产生的临时文件在任务完成后自动删除;结果视频保留在 `/workspace/io/output/` (宿主机 `/tmp/latentsync-io/output/`) 供下载,需定期清理。
7. **CUDA / 架构兼容性**: 本镜像基于 CUDA 12.1 + flash-attn 2.7.3。若 GPU3 实测为 Blackwell 架构 (sm_120),需 CUDA 12.8+ 工具链与支持 sm_120 的 flash-attn 版本;如 flash-attn 加载失败,可尝试改用 `pytorch/pytorch:2.4.0-cuda12.4.1-cudnn9-devel` 基础镜像或回退 `xformers` 注意力实现。部署后请用 `nvidia-smi` + 一次实际推理验证。
