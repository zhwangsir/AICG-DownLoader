# AI 短剧工作台 — 技术说明

## 1. 架构总览

```
┌─────────────────┐      HTTP/WebSocket      ┌──────────────────────┐
│   React 前端    │  ◄────────────────────►  │   FastAPI 后端       │
│  Tauri / Web    │                         │  Python + LangGraph  │
└─────────────────┘                         └──────────┬───────────┘
                                                       │
              ┌────────────────────────────────────────┼────────────┐
              │                                        │            │
              ▼                                        ▼            ▼
       EXO LLM 集群                              ComfyUI Worker 池   FFmpeg
  GLM-5.2 / Kimi-K2.7                        SDXL / Wan 2.2 I2V    视频合成
```

## 2. 后端架构

### 2.1 Agent 基类

`app/agents/base.py` 提供统一能力：
- `call_llm`: 调用 EXO OpenAI 兼容接口，支持 streaming 和 JSON 强制输出
- `call_comfyui` / `get_comfyui_result`: 提交和轮询 ComfyUI 工作流
- `upload_image_to_comfyui`: 上传图片到 ComfyUI input 目录
- 自动应用 `with_retry` 重试装饰器

### 2.2 进度反馈

`app/core/progress.py` 实现内存任务跟踪器：
- 每个任务有唯一 `task_id`
- 支持订阅/通知模式
- `app/routers/progress.py` 提供 SSE 流接口

### 2.3 错误恢复

`app/core/retry.py` 提供指数退避重试装饰器：
- 默认可重试：TimeoutError、ConnectionError、httpx 超时、HTTP 5xx
- 支持自定义重试条件和回调

### 2.4 数据模型

`app/models/schemas.py` 定义所有 Pydantic 模型：
- `Script` / `Scene` / `Character`: 剧本结构
- `StoryboardResult` / `VideoResult` / `VoiceResult` / `SubtitleResult`: 各 Agent 输出
- `EditRequest` / `EditResult`: 剪辑
- `QualityCheckRequest` / `QualityCheckResult`: 文本质检
- `QualityVisualRequest` / `QualityVisualResult`: 视觉质检

### 2.5 路由

`app/routers/drama.py` 提供同步和异步 Agent 端点：
- `POST /api/drama/{agent}/generate`: 同步执行
- `POST /api/drama/{agent}/generate_async`: 异步执行，返回 task_id
- `GET /api/progress/{task_id}/stream`: SSE 进度流

## 3. 前端架构

### 3.1 状态管理

`src/store/useDramaStore.ts` 使用 Zustand 管理全局状态：
- 各 Agent 生成结果
- 弹窗开关
- 状态栏信息

### 3.2 画布

`src/components/Canvas.tsx` 使用 React Flow 渲染节点图：
- 自定义 `DramaNode` 组件支持图片/视频/音频/字幕预览
- 使用 dagre 自动布局节点
- 根据数据变化自动重建节点和边

### 3.3 进度监听

`src/hooks/useProgress.ts` 通过 `EventSource` 订阅 SSE：
- 实时更新进度百分比
- 完成后调用 `onSuccess` 回调

## 4. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| LLM 集群 | EXO 分布式部署 GLM-5.2 / Kimi-K2.7 | Mac Studio 集群本地推理，零 API 费用 |
| 图像/视频 | ComfyUI Worker 池 | 4×RTX PRO 6000 工作站并行生成 |
| TTS/ASR | edge-tts + faster-whisper | 免费、本地 CPU 可跑 |
| 进度推送 | SSE | 单向推送，FastAPI 原生支持 |
| 状态管理 | Zustand | 轻量、无样板代码 |
| 视频合成 | FFmpeg subprocess | 成熟稳定，无需额外依赖 |
| 视觉质检 | Qwen3-VL（可选降级） | SOTA 视频理解能力，未部署时提示用户 |

## 5. 测试策略

- **后端**：pytest + pytest-asyncio，Mock 所有外部调用（LLM/ComfyUI/TTS/ASR），覆盖率 > 90%
- **前端**：vitest + @testing-library/react，Mock ResizeObserver，覆盖 Store 和 App 渲染
