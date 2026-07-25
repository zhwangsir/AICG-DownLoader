# Video Enhance 服务 (RealBasicVSR + RIFE + ProPainter)

AI 短剧后处理三合一服务: 视频超分 + 插帧 + 修复, 部署到 workstation GPU3。

- **部署位置**: `192.168.71.127` (workstation), GPU3 = NVIDIA RTX PRO 6000 96GB
- **端口**: `8290`
- **显存峰值**: 约 15GB (三模型串行执行, 单卡 96GB 富余)
- **基础镜像**: `pytorch/pytorch:2.1.0-cuda11.8-cudnn8-devel`

---

## 部署步骤

```bash
# 1. 在 workstation 上下载模型权重 (约 1.5GB, 首次较慢)
MODEL_DIR=/home/merlin/video-models bash download_models.sh

# 2. 构建镜像 (首次约 30-60 分钟, mmcv-full 编译耗时)
bash build.sh

# 3. 启动容器 (占用 GPU3, 端口 8290)
bash run.sh

# 4. 健康检查
curl http://192.168.71.127:8290/health
```

---

## API 接口

### `POST /super_resolution` — RealBasicVSR 超分 (x4)

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `video` | file | 必填 | 输入 mp4 |
| `scale` | int | 4 | 放大倍数 (RealBasicVSR 仅支持 x4, 其他值忽略) |
| `async_mode` | bool | false | true 时立即返回 task_id |

### `POST /interpolate` — RIFE 插帧

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `video` | file | 必填 | 输入 mp4 |
| `exp` | int | 1 | 1=2x(50fps), 2=4x(100fps) |
| `async_mode` | bool | false | |

### `POST /inpaint` — ProPainter 视频修复

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `video` | file | 必填 | 输入 mp4 |
| `mask` | file | 必填 | 蒙版 png (需修复区域为白色) |
| `task` | str | `object_removal` | `object_removal` / `video_completion` |
| `async_mode` | bool | false | |

### `POST /enhance_pipeline` — 串联三步

按顺序执行 **超分 → 插帧 → 修复**, 单步失败不阻断 (best-effort), 返回部分结果 + `warnings`。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `video` | file | 必填 | |
| `mask` | file | 无 | 提供 mask 才执行修复步骤, 否则跳过 |
| `scale` | int | 4 | |
| `exp` | int | 1 | |
| `task` | str | `object_removal` | |
| `async_mode` | bool | **true** | pipeline 耗时长, 默认异步 |

### `GET /tasks/{task_id}`

查询异步任务状态, 返回 `status` (`pending`/`running`/`success`/`failed`)、`result`、`warnings`、`log` (最后 50 行)。

### `GET /health`

健康检查, 报告各模型权重是否就位。

### 调用示例

```bash
# 同步超分
curl -X POST http://192.168.71.127:8290/super_resolution \
  -F "video=@input.mp4" -F "scale=4"

# 异步 pipeline (默认)
curl -X POST http://192.168.71.127:8290/enhance_pipeline \
  -F "video=@input.mp4" -F "mask=@mask.png" -F "exp=1"
# → {"task_id":"abc123...","status":"pending"}

# 查询任务
curl http://192.168.71.127:8290/tasks/abc123...

# 下载结果 (返回的 url 是相对路径, 拼上 host)
curl -O http://192.168.71.127:8290/files/output/sr_xxx.mp4
```

---

## 目录映射

| 容器内路径 | 宿主机路径 | 说明 |
|---|---|---|
| `/workspace/checkpoints` | `/home/merlin/video-models` (只读) | 模型权重 |
| `/workspace/io` | `/tmp/video-enhance-io` | 上传/输出 (读写) |

---

## 注意事项 (踩坑记录)

1. **mmcv 版本地狱**: RealBasicVSR (BasicSR 1.x) 依赖 `mmcv-full==1.5.3`, ProPainter 依赖 `mmcv>=2.0` (新版 mmcv, 与 mmcv-full 互斥)。本服务用 **subprocess 分别调用三个工具的 CLI**, 进程隔离, Dockerfile 统一装 `mmcv-full==1.5.3`。ProPainter 部分依赖 mmcv>=2.0 的工具函数会缺失, 但 `inference_propainter.py` 主流程可跑。若需 ProPainter 完整功能, 建议拆成两个容器。

2. **RealBasicVSR 长视频 OOM**: RealBasicVSR 对长视频 (几百帧以上) 会因显存累积 OOM。短剧场景需把视频**分段**处理 (每段 ≤ 100 帧), 再拼接。当前 `serve_api.py` 未做自动分段, 长视频需调用方自行切分或后续扩展。

3. **ProPainter 首次下载 1.5GB 权重**: `ProPainter.pth` + `recurrent_flow_completion.pth` + `raft-things.pth` 合计约 1.5GB, 首次 `download_models.sh` 耗时较长, 走 mihomo 代理 (`:7890`)。

4. **ffmpeg 必装**: 帧率协调、视频拼装依赖 ffmpeg。RealBasicVSR 输出 25fps, RIFE 插帧后 50fps, pipeline 中间用 ffmpeg 重采样回 25fps 便于下游处理和播放。

5. **帧率协调**: pipeline 流程 `超分(25fps) → ffmpeg归一化 → RIFE(50fps) → ffmpeg重采样回25fps → ProPainter`。每步间用 ffmpeg 保证帧率稳定。

6. **GPU 隔离**: 容器通过 `--gpus '"device=3"'` 绑定 GPU3, 不影响 ComfyUI-LB (`:8188`) 使用的其他 GPU。

7. **串行执行**: 后台线程池 `max_workers=2`, 但单卡显存有限, 实际建议并发任务 ≤ 1 (多任务排队, 避免显存叠加)。
