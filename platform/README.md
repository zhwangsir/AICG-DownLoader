# AI 短剧一条龙工作台 — M4 原型

> 与 AICG-DownLoader 相辅相成的短剧内容生产平台

## 能力概览

已实现 9 个 Agent 的全流程管线：

| Agent | 输入 | 输出 | 依赖 |
|-------|------|------|------|
| 剧本 Agent | 一句话创意 | JSON 剧本 | EXO GLM-5.2 |
| 角色 Agent | 角色描述 | 三视图定妆照 | ComfyUI SDXL |
| 分镜 Agent | 场景描述 | 9:16 关键帧 | ComfyUI SDXL |
| 视频 Agent | 关键帧 + 提示词 | MP4 视频片段 | ComfyUI Wan 2.2 I2V |
| 配音 Agent | 台词 | 多角色 MP3 | edge-tts |
| 字幕 Agent | 音频 | SRT 字幕 | faster-whisper |
| 剪辑 Agent | 视频+音频+字幕 | 完整成片 | FFmpeg |
| 文本质检 Agent | 剧本+字幕 | 结构化质检报告 | GLM-5.2 |
| 视觉质检 Agent | 视频 | 抽帧视觉质检报告 | Qwen3-VL（可选，未部署时降级提示） |

## 目录结构

```
platform/
├── backend/          # Python FastAPI + LangGraph 后端
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置（复用 AICG-DownLoader config.json）
│   │   ├── core/
│   │   │   ├── progress.py      # 内存任务进度 + SSE 推送
│   │   │   └── retry.py         # 指数退避重试装饰器
│   │   ├── agents/
│   │   │   ├── base.py          # Agent 基类（LLM/ComfyUI/重试）
│   │   │   ├── script_agent.py  # 剧本 Agent
│   │   │   ├── character_agent.py # 角色 Agent
│   │   │   ├── storyboard_agent.py # 分镜 Agent
│   │   │   ├── video_agent.py   # 视频 Agent
│   │   │   ├── voice_agent.py   # 配音 Agent
│   │   │   ├── subtitle_agent.py # 字幕 Agent
│   │   │   ├── edit_agent.py    # 剪辑 Agent
│   │   │   └── quality_agent.py # 质检 Agent（文本质检+视觉质检）
│   │   ├── models/schemas.py    # Pydantic 数据模型
│   │   └── routers/
│   │       ├── drama.py         # Agent API 路由
│   │       └── progress.py      # SSE 进度路由
│   ├── tests/                   # pytest 测试（覆盖率 > 90%）
│   └── pyproject.toml
├── frontend/         # Tauri 2.0 + React 18 + React Flow + Zustand
│   ├── src/
│   │   ├── App.tsx
│   │   ├── store/useDramaStore.ts # 全局状态管理
│   │   ├── components/Canvas.tsx  # 节点图画布（dagre 自动布局）
│   │   ├── components/Modals.tsx  # 各 Agent 操作弹窗
│   │   ├── components/ProgressBar.tsx
│   │   ├── hooks/useProgress.ts   # SSE 进度监听
│   │   └── api/client.ts          # API 客户端
│   ├── src-tauri/
│   └── vitest.config.ts           # 前端测试配置
└── README.md
```

## 快速启动

### 后端

```bash
cd platform/backend
uv sync --extra dev        # 安装依赖
uv run pytest              # 运行测试（覆盖率 > 90%）
uv run uvicorn app.main:app --reload --port 8100
```

### 前端

```bash
cd platform/frontend
npm install
npm run test               # 运行 vitest 测试
npm run dev                # Web 开发模式
# 或
npm run tauri dev          # Tauri 桌面应用
```

## 与 AICG-DownLoader 的集成

1. **配置共享**：后端 `config.py` 读取 AICG-DownLoader 的 `config.json`，复用 `comfy_root`、`comfy_url` 等配置
2. **静态资源服务**：后端挂载 `/static/audio`、`/static/subtitle`、`/static/video`，供前端播放/下载
3. **模型按需下载**：后续版本通过 AICG-DownLoader API 触发缺失模型下载

## M4 验收标准

- [x] 剧本 Agent：一句话创意 → JSON 剧本（GLM-5.2，含 json_repair 容错）
- [x] 角色 Agent：剧本人物 → 角色定妆照（SDXL majicMIX realistic）
- [x] 分镜 Agent：场景 → 9:16 关键帧（SDXL）
- [x] 视频 Agent：关键帧 → 视频片段（Wan 2.2 I2V）
- [x] 配音 Agent：台词 → 多角色语音（edge-tts）
- [x] 字幕 Agent：音频 → SRT 字幕（faster-whisper）
- [x] 剪辑 Agent：多场景素材 → 完整成片（FFmpeg）
- [x] 质检 Agent：剧本/字幕 → 文本质检报告；视频 → 视觉质检报告（Qwen3-VL 未部署时降级）
- [x] 进度反馈：SSE 实时推送 + `useProgress` Hook
- [x] 错误恢复：LLM/ComfyUI 调用指数退避重试
- [x] 前端状态管理：Zustand 替代 useState
- [x] 画布自动布局：dagre
- [x] 后端测试：pytest 122 个通过，覆盖率 92.92%
- [x] 前端测试：vitest 13 个通过
