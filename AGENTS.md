# AGENTS.md — AIGCPannel

> **最后更新**：2026-08-27（双远程已推 e3e30c0）
> **集群真相源**：`../ToIV/AGENTS.md`（禁止把设备清单/凭据复制进本文件）
> **文档五件套**：README.md / AGENTS.md / DEVELOPMENT.md / STATE.json / TEST_LOG.md

## 本项目

**AIGCPannel**（用户拼法 / 产品显示名）：ComfyUI 模型下载器 + 短剧工作台 + DashBox 引擎，一个项目、一个根。
路径：`ALLProject/AIGCPannel`（原 `AICG-DownLoader-main`）。不是三仓。LibTV 是已否掉的拆仓候选。

## 启动

- `./start-aigcpannel.sh`：工作台 backend `:8100` + frontend `:3501`
- `./start-engine.sh`：DashBox docker（默认 Web `:8080` / API `:8780`）。默认只打印命令，`--up` 才拉起。
- `GET /api/panel/status`：查 downloader `config` / `models.json` 是否可读，并返回 DashBox URL。

左侧导航已有「模型库」「引擎」。crate 名与 OS 配置目录仍是 `comfy-downloader`（保住已有 `models.json` 路径）。

## 许可

根 `NOTICE`：`platform/`、`src/` 等一等代码 MIT；`dashbox/` 是第三方（DramaClaw / DashBox），**ELv2，不是 MIT**。不要改 `dashbox/` LICENSE / NOTICE / 品牌文件，不要把 dashbox 当成本仓 MIT。

## 硬性规则

1. 状态、端口、GPU、挂载、模型占用必须 SSH 真机验证，以 ToIV/AGENTS.md + 真机为准。
2. 禁止跨项目改代码。ToIV 不动。
3. 远程：origin https://gitee.com/Winery_z/AIGCPannel 与 github https://github.com/zhwangsir/AIGCPannel 均已推 e3e30c0。双远程同步。ToIV 不动。
4. DashBox 上游 LICENSE / NOTICE / 品牌文件不要覆盖。
5. 旧文档已归档到 `ALLProject/.archive/docs-legacy-20260827/`。

## 2026-08-27 第一波融合（未 commit / push）

已删 `platform/deploy` 下 deepfilternet、hunyuanimage、latentsync、video-enhance、xdit-video（M23 已下线且无 Python import）。保留 `comfyui-lb`。
CORS 已去掉 `localhost:1420`。frontend 已无 `@tauri-apps`。`platform/backend/.venv` 已重建。引擎页可手动刷新探测 `:8080`/`:8780`。crate / 安装器路径仍旧。Gitee 与 GitHub 均已推 e3e30c0。

