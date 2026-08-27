# AGENTS.md — DashBox

> **最后更新**：2026-08-27（身份：DashBox 为壳；短剧流水线/下载器是模块）
> **集群真相源**：`../ToIV/AGENTS.md`（禁止把设备清单/凭据复制进本文件）
> **文档五件套**：README.md / AGENTS.md / DEVELOPMENT.md / STATE.json / TEST_LOG.md

## 本项目

**DashBox** 是产品壳。ToIV 才是聚合平台；本仓不要做成第二个 ToIV。短剧流水线（`platform/`）与下载器（`src/`）是模块。路径 `ALLProject/DashBox`。远程 origin Gitee `Winery_z/DashBox` 与 github `zhwangsir/DashBox`，尖端 `19a3141`。不是三仓。LibTV / comfy-downloader / AIGCPannel 旧 slug 是本仓 rename 跳转，禁止当独立仓删。本地只有 `ALLProject/DashBox`。
代码与测试归 AICG 开发；五件套归项目管家。禁止改 ToIV 业务代码。

## 启动

- `./start-dashbox.sh`：短剧后端 `:8100` + DashBox `:8080`/`:8780`。主界面 `:8080`。
- `./start-aigcpannel.sh`：薄封装，转调 `start-dashbox.sh`。
- `GET /api/panel/status`：product=DashBox；查 downloader `config` / `models.json` 是否可读。
- DashBox `:8780` 反代 `/api/drama/*` → `host.docker.internal:8100`。`:8080`/`:8780`/`:8100` `/api/drama/health` 均 200。
- Studio `NSFWDramaStudioNode` 默认 `pipelineEngine=drama`：剧本/首帧 `/api/drama/script|storyboard/generate_async`；配音/出片/合成 `/api/drama/{voice|video|edit}/generate_async`；失败回退 R18；可切换。edit 可省略 `subtitle_url`（不下载空 SRT），空字幕不再回退 R18。`19a3141` 已双推。web 仍 docker cp（镜像在烤）。CSP `img-src` 含 `http://192.168.71.127:8188`。

左侧导航已有「模型库」「引擎」。crate 名与 OS 配置目录仍是 `comfy-downloader`（保住已有 `models.json` 路径）。

## 2026-08-27 model library / gateway (code uncommitted)

- registry: error when NAS unreadable, no empty list. Scan root includes `/Users/wangzhenyu/NAS/Windows/ComfyUI/ComfyUIModel/models`. MateBook `~/NAS` is SMB-mounted (not on boot). registry: loras 101, checkpoints 24. model root readable.
- `gateway/health`: no studio04/01/02. Required: llm spark02, vlm spark01, LB :8188, H3 :8195, TTS :9200, ASR :9210. LTX required=false. Cluster SoT still `../ToIV/AGENTS.md`.
- DashBox: local colima; `:8080`/`:8780` listening; panel web/api_listening true; Colima disk 20G tight. LICENSE/NOTICE/brand untouched.
- drama smoke 2026-08-27 (uncommitted): script-ef4765a34f37 / project bed1ceac-10cb-46a6-9cea-93669d264432 杯底的血; storyboard-1c3cb3b243de scene1 PNG ~630KB :8188; spark02 live; H3 not submitted; script generate_async ~20min; character preview LLM 45s timeout -> template.

## 许可

根 `NOTICE`：`platform/`、`src/` 等一等代码 MIT；`dashbox/` 是第三方（DramaClaw / DashBox），**ELv2，不是 MIT**。不要改 `dashbox/` LICENSE / NOTICE / 品牌文件，不要把 dashbox 当成本仓 MIT。

## 硬性规则

1. 状态、端口、GPU、挂载、模型占用必须 SSH 真机验证，以 ToIV/AGENTS.md + 真机为准。
2. 禁止跨项目改代码。ToIV 不动。
3. 远程：origin https://gitee.com/Winery_z/DashBox 与 github https://github.com/zhwangsir/DashBox 均已推 `543264e`。双远程同步。ToIV 不动。
4. DashBox 是产品壳（主界面 `:8080`），开发和测试归 AICG 开发。只禁止覆盖上游 LICENSE / NOTICE / DramaClaw 品牌文件，不要改成 MIT。
5. 旧文档已归档到 `ALLProject/.archive/docs-legacy-20260827/`。

## 2026-08-27 第一波融合（未 commit / push）

已删 `platform/deploy` 下 deepfilternet、hunyuanimage、latentsync、video-enhance、xdit-video（M23 已下线且无 Python import）。保留 `comfyui-lb`。
CORS 已去掉 `localhost:1420`。frontend 已无 `@tauri-apps`。`platform/backend/.venv` 已重建。引擎页可手动刷新探测 `:8080`/`:8780`。crate / 安装器路径仍旧。Gitee 与 GitHub 均已推 e3e30c0。

