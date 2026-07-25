# AGENTS.md — AICG-DownLoader-main

> 本文件为 AI 协作规范,所有 Agent (含 Claude/Codex/Cursor/Trae) 在本仓库工作时必须遵守。

---

## 一、项目概述

- **定位**: ComfyUI 模型下载器 + AI 短剧生成平台
- **版本**: main 分支,持续迭代
- **技术栈**:
  - 桌面端: Rust + egui (src-tauri 风格的本地 GUI)
  - 后端: Python 3.11+ / FastAPI / Uvicorn
  - 前端: TypeScript / React 18 / Vite
- **核心能力**:
  - ComfyUI 模型管理 (下载/校验/分类)
  - AI 短剧生成全流程 (脚本 → 分镜 → 角色 → 配音 → 字幕 → 视频)
- **部署位置**: 本地开发,后端 `platform/backend`,前端 `platform/frontend`

---

## 二、项目结构

```
AICG-DownLoader-main/
├── platform/
│   ├── backend/              # Python FastAPI 后端
│   │   ├── main.py           # 应用入口 (uvicorn main:app)
│   │   ├── routers/          # API 路由
│   │   ├── services/         # 业务逻辑 (短剧生成/模型下载)
│   │   ├── models/           # 数据模型
│   │   └── requirements.txt
│   └── frontend/             # React + TypeScript 前端
│       ├── src/
│       │   ├── components/   # 组件 (统一使用 lucide-react 图标)
│       │   ├── pages/        # 页面
│       │   ├── hooks/
│       │   └── api/          # API 调用封装
│       ├── package.json      # pnpm 管理
│       └── vite.config.ts
├── src-tauri/                # Rust 桌面端 (egui/tauri)
│   ├── src/
│   │   └── main.rs
│   └── Cargo.toml
└── AGENTS.md
```

---

## 三、开发命令

### 后端 (Python FastAPI)

```bash
cd platform/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8010
```

### 前端 (React + Vite)

```bash
cd platform/frontend
pnpm install
pnpm dev
```

### 桌面端 (Rust)

```bash
cd src-tauri
cargo build --release
```

### 构建

```bash
# 前端生产构建
cd platform/frontend && pnpm build
# Rust 桌面应用打包
cd src-tauri && cargo build --release
```

---

## 四、代码规范

### Python (后端)
- 类型注解必填,使用 `pydantic v2` 定义请求/响应模型
- 路由函数使用 `async def`,IO 密集场景使用 `asyncio`/`httpx`
- 不得在路由层写业务逻辑,统一放入 `services/`
- 日志使用 `logging`,禁止裸 `print`

### TypeScript (前端)
- 严格模式 `strict: true`,禁止 `any`(必要时用 `unknown` 收窄)
- 函数组件 + Hooks,优先函数式而非 class
- API 调用必须经 `src/api/` 封装,组件内不得直接 `fetch`
- 状态管理优先 `zustand` 或 React Context,避免过度引入 Redux

### Rust (桌面端)
- `cargo fmt` + `cargo clippy -- -D warnings`
- 错误处理使用 `thiserror` + `anyhow`,禁止 `unwrap()`/`expect()` 进入生产代码

---

## 五、测试策略

| 层 | 工具 | 命令 | 覆盖目标 |
|---|---|---|---|
| 后端单元/接口 | pytest | `cd platform/backend && pytest -v` | ≥ 70% |
| 前端组件 | vitest + @testing-library/react | `pnpm test` | 关键组件覆盖 |
| Rust 单元 | cargo test | `cd src-tauri && cargo test` | 核心逻辑 |

- 短剧生成流程必须有端到端冒烟测试
- 模型下载器必须测试断点续传与校验逻辑

---

## 六、集群依赖

> 完整集群拓扑详见 `/Users/wangzhenyu/Desktop/ALLProject/.设备说明.md`

- **ComfyUI-LB**: Workstation `192.168.71.127:8188`,短剧生成调用 ComfyUI 工作流
- **NAS SMB 模型存储**: `192.168.71.7:445`,模型下载目标路径,挂载点 `~/NAS` (Mac) 或 `/home/merlin/nas_mount` (Workstation)
- **mihomo 代理**: `:7890`,模型源 (HuggingFace/Civitai) 走代理
- **不依赖**: EXO 集群、OpenClaw、spark vLLM

调用 ComfyUI 时使用 LB 入口 `http://192.168.71.127:8188`,禁止直连单卡端口 8189-8192。

---

## 七、提交规范

- **不主动提交**: 用户未明确要求时,严禁执行 `git commit`/`git push`
- **Conventional Commits**:
  - `feat(short-drama): add scene splitter`
  - `fix(downloader): handle resume on network reset`
  - `docs: update AGENTS.md`
  - `refactor(frontend): extract video preview hook`
- 范围 (scope) 优先使用: `short-drama` / `downloader` / `frontend` / `backend` / `rust` / `docs`

---

## 八、项目隔离纪律

- **禁止跨项目修改**: 本项目代码不得修改 `AIHub/`、`ToIV/`、`DRT管理中心/` 等其他项目
- **共享基础设施不耦合**: 可调用集群服务 (ComfyUI-LB、NAS),但不得引用其他项目源码
- **依赖管理**: 后端依赖在 `platform/backend/requirements.txt`,前端在 `platform/frontend/package.json`,Rust 在 `src-tauri/Cargo.toml`,三套独立
- **配置隔离**: 环境变量通过 `.env` 注入,不得硬编码其他项目路径

---

## 九、图标规范

- **统一使用 Lucide React** (`lucide-react`),禁止 emoji、禁止其他图标库 (Heroicons/FontAwesome/Material Icons 等)
- 图标按需引入: `import { Download, Film, Mic } from 'lucide-react'`
- Rust 桌面端不涉及 Web 图标,遵循 egui 原生图标约定
- 已存在的 emoji 必须在下次重构时替换为 Lucide 组件

---

## 十、Agent 行为底线

1. 改动前先读相关文件,理解上下文
2. 不创建未要求的文件,不写未要求的文档
3. 测试失败时不重复同一修复路径,报告阻塞点
4. 完成任务后给出简明报告,包含改动文件路径与关键决策

---

## 端口配置

> 参考: /Users/wangzhenyu/Desktop/ALLProject/项目端口规划指南.md

| 服务 | 端口 | 说明 |
|------|------|------|
| 前端 dev (platform/frontend) | 3501 | Vite，原 8085 改此 |
| 后端 (FastAPI) | 8100 | 已固定，不变 |

端口段 35XX 专属 AICG-DownLoader。
