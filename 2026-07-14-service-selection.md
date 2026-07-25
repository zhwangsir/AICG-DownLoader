# ToIV 服务选型与部署拓扑

> 生成日期:2026-07-14
> 用途:与外部项目对齐技术选型、运行位置与接入点
> 来源:同步自 `deploy/docker-compose.yml`、`deploy/openresty-toiv.conf`、`deploy/toiv_model_paths.yaml`、`apps/api/app/config.py`、`apps/api/.env`、`apps/web/.env.local`、`apps/api/requirements.txt`、`apps/web/package.json`

---

## 一、应用层(ToIV 自研,跑 Docker)

| 服务 | 技术栈 | 容器端口 | 部署位置 |
|---|---|---|---|
| API | FastAPI 0.137 + SQLModel 0.0.38 + Pydantic 2.13 + httpx + sse-starlette + sentry-sdk + prometheus-fastapi-instrumentator | 8090:8080 | 100.86.42.89(Tailscale) |
| Web | Next.js 15.1 + React 19 + TypeScript 5.7 + lucide-react + framer-motion 12 + @xyflow/react + @sentry/nextjs 9 | 3100:3100 | 100.86.42.89 |
| 反代 | OpenResty/Nginx | 80/443 | 43.119.32.180(公网入口)+ toiv.dgmt.top |
| 数据库 | SQLite(生产可切 PostgreSQL,`TOIV_DATABASE_URL`) | — | 容器卷 `/data/toiv.db` |

### 应用层依赖清单

**后端 API**(关键依赖):
- fastapi 0.137.2 + starlette 1.3.1 + uvicorn 0.49.0
- sqlmodel 0.0.38 + sqlalchemy 2.0.51
- pydantic 2.13.4 + pydantic-settings 2.14.2
- httpx 0.28.1 + websockets 16.0
- sse-starlette 3.4.4
- paramiko 5.0.0(NAS SFTP)
- sentry-sdk 2.65.0
- prometheus-fastapi-instrumentator 8.0.2
- pyjwt 2.13.0
- python-multipart 0.0.32

**前端 Web**(关键依赖):
- next 15.1 + react 19 + react-dom 19
- lucide-react 1.24(统一图标库)
- framer-motion 12.40
- @xyflow/react 12.11
- @sentry/nextjs 9
- typescript 5.7
- @playwright/test 1.61(E2E)
- @axe-core/playwright 4.12(无障碍)

---

## 二、AI 模型服务(外部 GPU 机,走 Tailscale/LAN)

| 服务 | 地址 | 模型 / 引擎 | 用途 |
|---|---|---|---|
| ComfyUI Worker | 192.168.71.100:8000/8002/8003/8004(Tailscale 100.99.181.103) | PRO6000 ×4 卡 | 出图/视频(仅 8003 配 LTX 模型) |
| ComfyUI Worker | 100.82.102.43:8188 | PC01 5090 | 备用出图 |
| Forge (reForge SD WebUI) | 192.168.71.100:7860 | sdapi 同步出图 | 第二出图引擎 |
| LLM 大脑(docker-compose 默认) | 100.81.235.124:30000/v1 | qwen3.6-35b-a3b(AWQ,vLLM,reasoning-parser qwen3 + tool-call-parser qwen3_xml) | AI 智能体 |
| LLM 大脑(.env 实际) | 100.64.201.37:52415/v1 | GLM-5.2-fp8(EXO) + 备用 Kimi-K2.7-Code-4bit | AI 智能体 |
| Embedding (RAG) | 192.168.71.100:1234/v1 | text-embedding-nomic-embed-text-v1.5(LM Studio) | 向量嵌入 |
| TTS (IndexTTS2) | 192.168.71.100:9000 | 中文/英文配音 | 配音合成 |
| 多语言 TTS | 未部署(`tts_multilingual_url=""`) | — | 日/韩/粤(预留) |
| Whisper ASR | 默认容器内 faster-whisper CPU(base/int8) | 可配外部 GPU `whisper_url` | 译制听写 |
| LoRA Trainer | 192.168.71.100:9100 | 独立 HTTP 服务 | 模型训练 |
| VLM Server(视频评估) | 100.99.181.103:8200 | Qwen3-VL(GPU 0,OpenAI 兼容 API) | 视频质量诊断 |

### ComfyUI Worker 启用情况

- **当前启用**:`http://100.99.181.103:8003`(唯一配 LTX 模型 + NAS 路径)
- **已禁用**:8002(Z: NAS 路径缺 LTX 模型)
- **其他**:8000/8004 在线但无 LTX 模型,仅 SFW 出图
- **备用**:100.82.102.43:8188(PC01 5090)

### LLM 大脑双配置说明

docker-compose.yml 默认指向 `100.81.235.124:30000`(vLLM 部署的 qwen3.6-35b-a3b AWQ);
本机 `apps/api/.env` 实际覆盖为 `100.64.201.37:52415`(EXO 部署的 GLM-5.2-fp8),
并配 `TOIV_LLM_FALLBACK_MODEL=mlx-community/Kimi-K2.7-Code-4bit` 作主掉线兜底。

历史教训:GLM-5.2 作为 Worker 推理曾 901s 超时卡死,切到 Kimi-K2.7-Code 完成任务。
GLM-5.2 适合作为大脑(LLM 调用),不适合作为 Worker(长视频推理)。

---

## 三、存储与模型

| 服务 | 地址 | 用途 |
|---|---|---|
| NAS(绿联 DXP8800 Pro) | SFTP `100.80.237.96:22` + cifs `//NAS/Windows/ComfyUI/ComfyUIModel/models` | 模型 + 生成内容集中存储 |
| ComfyUI 模型路径 | `toiv_model_paths.yaml`:`toiv_nas`(默认)+ `toiv_local`(F: 盘兜底) | ComfyUI `--extra-model-paths-config` 读取 |
| 生成内容 | 默认 `/data`(容器本地卷);可设 `TOIV_CONTENT_DIR=/data/nas/toiv` 落 NAS | 译制视频/配音/forge/cad |
| 模型市场 | Civitai 走 civitai.red 镜像 | 模型下载 |

### NAS 模型路径配置

`toiv_nas`(默认,`is_default: true`):
- base: `\\100.80.237.96\NAS\Windows\ComfyUI\ComfyUIModel\models`
- 子目录:checkpoints / classifiers / clip / clip_vision / configs / controlnet / diffusers / diffusion_models / embeddings / gligen / hypernetworks / latent_upscale_models / loras / model_patches / audio_encoders / photomaker / style_models / text_encoders / upscale_models / vae / vae_approx / mmaudio / sams / LLM

`toiv_local`(兜底):
- base: `F:\ComfyUIModel\models`
- 子目录:checkpoints / clip / clip_vision / controlnet / diffusion_models / embeddings / latent_upscale_models / loras / style_models / text_encoders / upscale_models / vae / vae_approx / mmaudio / sams

---

## 四、关键模型选型

| 用途 | 模型 | 文件 |
|---|---|---|
| SFW 出图底模 | FLUX.2 dev(fp8mixed,33GB) | `flux2_dev_fp8mixed.safetensors` |
| FLUX.2 编码器 | mistral_3_small_flux2_fp8 | model_profiles 配置 |
| NSFW 视频底模 | 10Eros v1.2 | `10eros_v12.safetensors` |
| NSFW 文本编码器 | Gemma 3 12B(非 T5) | `gemma_3_12B_it_fp8_scaled.safetensors` |
| NSFW VAE | LTX VAE | `ltx_vae.safetensors` |
| SFW 视频底模 | LTX-2.3 distilled | `diffusion_models/ltx-2.3/` |
| 测试默认底模 | RealVisXL_V5.0 | `RealVisXL_V5.0_fp16.safetensors` |
| 视频评估 VLM | Qwen3-VL | `F:\vlm_model`(GPU 0,8200 端口) |

### NSFW 视频生成参数约束

- 默认底模:`10eros_v12.safetensors`
- 默认文本编码器:`gemma_3_12B_it_fp8_scaled.safetensors`(非 T5)
- 默认 VAE:`ltx_vae.safetensors`
- 3 预设场景:文生视频 / 图生视频 / 口型同步
- 分辨率预设:480p / 720p / 1080p
- 时长选项:6s / 10s / 15s
- 高级开关:2 阶段采样 / Detailer / RIFE 插帧 / 种子锁定
- 已知问题:`rife49.pth` 缺失(仅 rife47.pth),需 `use_rife=false` 或改 ltx_video.py

---

## 五、网络拓扑

```
                          公网入口
                  43.119.32.180(toiv.dgmt.top)
                            |
                       OpenResty 80/443
                            |
            +---------------+---------------+
            |                               |
        /api/ → 100.86.42.89:8090       / → 100.86.42.89:3100
        (FastAPI 容器)                  (Next.js 容器)
            |
            +-- Docker 内部卷 toiv-data (/data/toiv.db)
            +-- Docker 内部卷 toiv-nas (cifs → //NAS/...)
            |
            +-- HTTP 调用外部 GPU 服务(全部 Tailscale/LAN):
                |
                +-- ComfyUI Workers(100.99.181.103:8000/8002/8003/8004)
                +-- ComfyUI Worker 备用(100.82.102.43:8188)
                +-- Forge(192.168.71.100:7860)
                +-- LLM 大脑(100.64.201.37:52415 / 100.81.235.124:30000)
                +-- Embedding(192.168.71.100:1234)
                +-- TTS(192.168.71.100:9000)
                +-- Trainer(192.168.71.100:9100)
                +-- VLM Server(100.99.181.103:8200)
                +-- NAS SFTP(100.80.237.96:22)
```

---

## 六、核心接入点(给对齐用)

| 项 | 值 |
|---|---|
| ToIV API 端口(开发) | `http://127.0.0.1:8200`(.env 覆盖了 docker-compose 的 8090) |
| ToIV API 端口(容器) | `http://100.86.42.89:8090` |
| ToIV Web 端口 | `http://localhost:3100` |
| 生产入口 | `https://toiv.dgmt.top` |
| ComfyUI Worker Pool | `TOIV_COMFY_WORKERS=http://100.99.181.103:8003` |
| LLM 大脑 | `TOIV_LLM_BASE_URL=http://100.64.201.37:52415/v1` + `TOIV_LLM_MODEL=mlx-community/GLM-5.2-fp8` |
| LLM 备用模型 | `mlx-community/Kimi-K2.7-Code-4bit` |
| Embedding | `TOIV_EMBED_BASE_URL=http://192.168.71.100:1234/v1` + `text-embedding-nomic-embed-text-v1.5` |
| TTS | `TOIV_TTS_URL=http://192.168.71.100:9000` |
| Trainer | `TOIV_TRAINER_URL=http://100.99.181.103:9100` |
| VLM Server | `vlm_server_url=http://100.99.181.103:8200` + `vlm_model_id=qwen3-vl` |
| Forge | `forge_url=http://192.168.71.100:7860` |
| CORS 允许源 | `https://toiv.dgmt.top;http://localhost:3100` |
| 测试账号 | admin / admin123 |
| 测试通道 | `TOIV_TEST_KEY=dev-test-key-only`(生产留空关闭) |

---

## 七、可观测性

| 项 | 实现 |
|---|---|
| Docker healthcheck | api:python urllib 探 `/api/health`(30s/5s/3 次,start_period 20s);web:wget --spider 探根路径 |
| 日志轮转 | local driver + max-size 10m + max-file 3 |
| Prometheus metrics | `/metrics` endpoint(prometheus-fastapi-instrumentator) |
| Sentry 后端 | sentry-sdk[fastapi],`TOIV_SENTRY_DSN` 空=不启用,启用后 10% 性能采样 |
| Sentry 前端 | @sentry/nextjs 9,`NEXT_PUBLIC_SENTRY_DSN` 空=不启用,try/catch 加载 next.config.mjs |

---

## 八、安全约束(硬性)

1. **CORS**:禁止 `allow_origins=["*"]` + `allow_credentials=True`(浏览器安全违规)
2. **后端 auth**:cookie 优先 + Bearer token 兜底
3. **Cookie Secure**:开发环境(localhost/127.0.0.1)自动关闭,生产(.dgmt.top)自动开启
4. **NSFW 模块**:`/nsfw` 路径独立访问,其他视图锁定平台默认模型
5. **未成年防护**:User.birthdate 字段 + `is_underage()` 硬阻断 R18 入口
6. **NSFW 请求头**:`X-NSFW: 1` 才能访问 R18 模型
7. **测试通道**:`/api/auth/test-login` 空时返 404(隐藏端点存在性)
8. **/system/gpu**:需 `Depends(get_current_admin)` 鉴权
9. **图标库统一**:全局 lucide-react,禁 emoji / 其他图标库 / 自定义 SVG
10. **项目隔离**:ToIV 与 AIHub / ShuGou HUB / Drt-Erp 等项目严格独立

---

## 九、待启动项(未完成)

- 视频评估端到端实测(`video_scorer_enabled` 默认关,需 `TOIV_VIDEO_SCORER_ENABLED=true` 启用)
- CreateView 改造消费 ModelPicker / OptimizeButton
- 各视图移动端适配(断点令牌已建)
- NSFW 对齐优化(考虑 Huihui abliterated 权重)
- 模型升级路线:FLUX.2 / Qwen-Image / Z-Image(第 4 阶段)
- 音频升级 + 微调 + 数据飞轮(第 6 阶段)

---

## 十、配置文件位置索引

| 文件 | 路径 |
|---|---|
| Docker 编排 | `deploy/docker-compose.yml` |
| 反代配置 | `deploy/openresty-toiv.conf` |
| ComfyUI 模型路径 | `deploy/toiv_model_paths.yaml` |
| 后端配置类 | `apps/api/app/config.py` |
| 后端 .env | `apps/api/.env`(实际值) + `apps/api/.env.example`(模板) |
| 前端 .env | `apps/web/.env.local` + `apps/web/.env.local.example` |
| 后端依赖 | `apps/api/requirements.txt` + `apps/api/pyproject.toml` |
| 前端依赖 | `apps/web/package.json` |
| Worker Pool 实现 | `apps/api/app/pool.py` |
| ComfyUI 客户端 | `apps/api/app/comfy/` |
| Forge 客户端 | `apps/api/app/forge/` |
| NAS 客户端 | `apps/api/app/nas.py` |
| LLM 客户端 | `apps/api/app/llm.py` |
| 视频评估器 | `apps/api/app/scoring.py`(VideoScorer 类,L205+) |
| SSE 事件路由 | `apps/api/app/routes/jobs.py` |
| 前端 SSE 监听 | `apps/web/lib/trackJob.ts` |
| 前端生成 hook | `apps/web/lib/useGeneration.ts` |
