# HunyuanImage 2.1 FP8 图像生成服务

AI 短剧项目的图像生成服务,基于腾讯 HunyuanImage 2.1 (17B FP8),部署到 workstation GPU3。

- **部署位置**: workstation `192.168.71.127` GPU3 (NVIDIA RTX PRO 6000 96GB)
- **端口**: 8600
- **显存占用**: ~24GB (FP8 量化)
- **基础镜像**: `pytorch/pytorch:2.4.1-cuda12.4-cudnn9-devel`

## 部署步骤

```bash
cd platform/deploy/hunyuanimage

# 1. 构建镜像 (首次会克隆官方仓库 + 源码编译 flash-attn, 耗时较长)
bash build.sh

# 2. 启动容器
bash run.sh

# 3. 查看日志 / 健康检查
docker logs -f hunyuanimage
curl http://192.168.71.127:8600/health
```

宿主机需提前创建挂载目录:

```bash
mkdir -p /tmp/hunyuanimage-io/output
mkdir -p /home/merlin/.cache/huggingface   # 模型缓存, 多容器共享
```

## API 接口

### 1. 健康检查

```
GET /health
```

返回 `{"status":"ok","model":"hunyuanimage-v2.1","loaded":true|false}`。

### 2. 模型列表

```
GET /v1/models
```

返回 OpenAI 兼容的模型列表,id 为 `hunyuanimage-v2.1`。

### 3. 图像生成 (异步,默认)

```
POST /v1/images/generations
Content-Type: application/json

{
  "prompt": "一个穿着汉服的女孩站在竹林中, cinematic lighting",
  "model": "hunyuanimage-v2.1",
  "size": "2048x2048",
  "n": 1,
  "response_format": "b64_json",
  "seed": 42
}
```

立即返回:

```json
{"task_id": "abc123...", "status": "pending", "created_at": "..."}
```

### 4. 图像生成 (同步,`?sync=true`)

```
POST /v1/images/generations?sync=true
```

阻塞等待完成,直接返回 OpenAI 兼容格式:

```json
{
  "created": 1780000000,
  "data": [{"b64_json": "<base64 PNG>"}]
}
```

`response_format` 为 `url` 时,`data` 中返回相对路径 `/v1/files/<name>.png`,通过 `GET /v1/files/<name>` 下载。

### 5. 查询任务

```
GET /v1/tasks/{task_id}
```

返回任务状态 (`pending` / `processing` / `succeeded` / `failed`),成功时附带 `data` 字段。
若 size 被自动调整,响应中包含 `warning` 字段说明实际使用的分辨率。

## 请求参数

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `prompt` | string | 必填 | 正面提示词,原生支持中文 |
| `model` | string | `hunyuanimage-v2.1` | 模型 ID |
| `size` | string | `2048x2048` | 期望尺寸,非 2K 组合时自动选最接近组合 |
| `n` | int | 1 | 生成数量 (1-4) |
| `response_format` | string | `b64_json` | `b64_json` 或 `url` |
| `seed` | int | 随机 | 随机种子 |

## 注意事项

1. **仅支持 2K 分辨率**:HunyuanImage 2.1 原生训练分辨率为 2K,支持 `2048x2048`、`2560x1536`、`2304x1728`、`2688x1536`、`2240x2240` 及其对应横竖构图的固定组合。请求其他尺寸时会自动匹配最接近组合并在响应中给出 `warning`。

2. **显存约 24GB**:FP8 量化下单卡 24GB 显存可跑,容器分配 GPU3。已设置 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` 缓解碎片化。

3. **首次下载约 17GB 权重**:首次请求时会从 HuggingFace 下载 `hunyuanimage-v2.1` 权重(约 17GB),走 `hf-mirror.com` 镜像加速。模型缓存挂载到宿主机 `/home/merlin/.cache/huggingface`,后续启动无需重复下载。模型懒加载,启动瞬间即可响应健康检查,首次推理会因加载模型较慢。

4. **flash-attn 在 Blackwell 架构上可能需源码编译**:RTX PRO 6000 (Blackwell) 上 `flash-attn==2.7.3` 预编译 wheel 可能不兼容,若构建失败需在 Dockerfile 中改为从源码编译(去掉 wheel 安装,保留 `--no-build-isolation`),编译耗时约 20-40 分钟。

5. **异步任务管理**:任务状态存储在进程内存 dict 中,容器重启后任务历史丢失。生成图片落盘到 `/workspace/io/output/`(宿主机 `/tmp/hunyuanimage-io/output/`),重启不丢失。

6. **并发加载保护**:模型懒加载用 `threading.Lock` 保护,避免首个并发请求重复加载;推理在后台 daemon 线程执行,建议串行提交以避免显存竞争。
