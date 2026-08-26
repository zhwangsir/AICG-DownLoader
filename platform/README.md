# AI 短剧一条龙工作台

> AIGCPannel 短剧内容生产平台（2026-08 当前架构，v0.4.0）

## 能力概览

从「一句话创意」到「可播放短剧成片」的全流程管线，由 9 个 Agent 协作完成：

| Agent | 输入 | 输出 | 依赖 |
|-------|------|------|------|
| 剧本 Agent | 一句话创意 | JSON 剧本 | spark02 LLM（qwen3.6-uncensored） |
| 角色 Agent | 角色描述 | 三视图定妆照 | SDXL（ComfyUI-LB，style_anchor 选 checkpoint） |
| 分镜 Agent | 场景描述 | 9:16 关键帧 | SDXL（ComfyUI-LB，IPAdapter 定妆照锚定） |
| 视频 Agent | 关键帧 + 提示词 | MP4 视频片段 | 双引擎：MiniMax H3 / LTX-2.5（回退 Wan2.2） |
| 配音 Agent | 台词 | 多角色 WAV | IndexTTS-2 |
| 字幕 Agent | 音频 | SRT 字幕 | whisper.cpp（主）/ AI-Omni faster-whisper（回退） |
| 剪辑 Agent | 视频+音频+字幕 | 完整成片 | FFmpeg |
| 文本质检 Agent | 剧本+字幕 | 结构化质检报告 | spark02 LLM |
| 视觉质检 Agent | 视频 | 抽帧视觉质检报告 | spark02 VLM（与 LLM 同入口） |

另有 `ai_optimizer`（AI 优化 Agent）与内置 RAG 提示词优化（`rag_service`）辅助各环节。

## 后端基础设施（当前真实拓扑）

| 能力 | 服务 | 地址 |
|------|------|------|
| LLM / 视觉质检 | spark02（OpenAI 兼容，qwen3.6-uncensored） | http://192.168.71.84:8000/v1 |
| 图像生成 | SDXL 经 ComfyUI-LB（3 后端：gpu0 / pc01 / pc02） | http://192.168.71.127:8188 |
| 视频引擎 A | MiniMax H3（2K/15s/原生立体声，对白与角色一致性镜头） | http://192.168.71.127:8195 |
| 视频引擎 B | LTX-2.5（音画同出，空镜/动作/长场景/分镜预览） | http://192.168.71.127:8198 |
| TTS | IndexTTS-2（POST /tts multipart → WAV） | http://192.168.71.127:9200 |
| ASR | AI-Omni faster-whisper large-v3（回退） | http://192.168.71.127:9210 |
| ASR（主用） | studio02 whisper.cpp | http://192.168.71.111:9212 |

> 已下线并删除：Nemotron/EXO（LLM）、HunyuanImage、flux_pulid、xdit、LatentSync/RealBasicVSR/RIFE/DeepFilterNet 等唇形同步与后处理服务。超分由 ToIV M6 fleet（:8261-8263）承担。

## 关键子系统

- **pipeline 编排**：`app/services/pipeline_orchestrator.py` 串起剧本→角色→分镜→视频→配音→字幕→剪辑→质检全链路。
- **双引擎视频路由**：`video_agent.route_video_engine`——有台词/参考资产 → H3（FL2VA / Ref2VA / 多镜 / Turbo LoRA）；长镜/纯运动 → LTX-2.5（distilled 两阶段）；失败回退链 `ltx → h3 → comfyui(Wan2.2)`。
- **提示词扩写**：`app/services/prompt_expander.py`——ShotSpec IR + H3ContextIRCompiler（三字段 / `cuts to` / `<d>`台词 / `(Sx)`说话人）+ LTXProseCompiler（散文六要素）+ `recommended_quality_params`。
- **模型注册表**：`app/services/model_registry_service.py` + `GET /api/drama/models/registry`，融合 lora_manifest 与下载器 models.json，打通下载器 ↔ 工作台。
- **长视频**：`long_video_planner`（M21.3 拆块）+ `long_video_service`（M20 H3 帧链）+ M22 LongCat 路线 B（A/B 对比报告见 `platform/reports/`）。
- **图像风格锚定**：`style_anchor` 按写实性选择 checkpoint——写实 majicMIX / 动漫 animagineXL40。

## 目录结构

```
platform/
├── backend/          # Python FastAPI 后端（uv 管理，Python 3.11+）
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置（.env，见 .env.example）
│   │   ├── core/                # 进度跟踪（SSE）+ 指数退避重试
│   │   ├── agents/              # 9 个 Agent + 基类
│   │   ├── services/            # pipeline 编排 / 提示词扩写 / 模型注册表 / 长视频 / TTS / ASR / RAG 等
│   │   ├── knowledge_base/      # RAG 知识库（题材/镜头/风格/反例）
│   │   ├── models/schemas.py    # Pydantic 数据模型
│   │   └── routers/             # drama API + SSE 进度
│   ├── tests/                   # pytest（676 passed / 覆盖率 83.52%）
│   └── pyproject.toml           # v0.4.0
├── frontend/         # React 18 + TypeScript + Vite + Zustand + React Flow（pnpm）
│   ├── src/
│   │   ├── store/useDramaStore.ts # 全局状态管理
│   │   ├── components/            # 节点画布（dagre 布局）/ 操作弹窗 / 进度条
│   │   ├── hooks/useProgress.ts   # SSE 进度监听
│   │   └── api/client.ts          # API 客户端
│   └── vitest.config.ts           # 前端测试（vitest 42 passed / tsc 0 error）
└── reports/          # 里程碑实验报告（如 M22.2 LongCat A/B 对比）
```

## 快速启动

仓库根目录一键拉起后端 + 前端（默认 :8100 / :3501）：

```bash
./start-aigcpannel.sh
```

引擎（捆绑 DashBox）另开：`./start-engine.sh`（`--up` 直接 compose）。Windows 可用 `start-aigcpannel.bat`。

### 后端

```bash
cd platform/backend
uv sync --extra dev        # 安装依赖
cp .env.example .env       # 按实际环境修改
uv run pytest              # 运行测试
uv run uvicorn app.main:app --host 0.0.0.0 --port 8100
```

### 前端

```bash
cd platform/frontend
pnpm install
pnpm run test              # 运行 vitest 测试
pnpm run dev               # Web 开发模式（默认 5173；一键脚本用 :3501）
```

## AIGCPannel 融合面板

1. **启动**：根目录 `./start-aigcpannel.sh` 拉起本工作台；`./start-engine.sh` 打印/启动捆绑引擎。
2. **模型库**：左侧「模型库」走 `GET /api/models/*` 与 `GET /api/drama/models/registry`（融合 `models.json` + `lora_manifest`）。
3. **引擎**：左侧「引擎」只做启动说明 / 状态 / 链接。捆绑的 DramaClaw/DashBox 为第三方 Elastic License 2.0（`:8080` Web / `:8780` API），不重品牌、不抓取其页面。
4. **DOWNLOADER_***：`DOWNLOADER_CONFIG_PATH`（默认仓库根 `config.json`）、`DOWNLOADER_MODELS_JSON`（可选覆盖已下载清单）。crate/OS 配置目录仍为 `comfy-downloader`。
5. **静态资源**：后端挂载 `/static/audio`、`/static/subtitle`、`/static/video`。
