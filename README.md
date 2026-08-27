# DashBox

**DashBox** 是产品壳。ToIV 是聚合平台；本仓不是第二个 ToIV。短剧流水线（`platform/`）和 ComfyUI 模型下载器（`src/`）是模块，不再三套并列。

目录 `ALLProject/DashBox`。远程 origin [gitee.com/Winery_z/DashBox](https://gitee.com/Winery_z/DashBox) 与 github [github.com/zhwangsir/DashBox](https://github.com/zhwangsir/DashBox)，尖端 `4185c30`（已双推，未强推）。只这一根融合仓。LibTV / comfy-downloader / AIGCPannel / AICG-DownLoader 不是独立仓；GitHub/Gitee 旧 slug 是本仓 rename 跳转，删除会毁掉融合仓。

后续开发与测试归 AICG 开发；五件套归项目管家。ToIV 业务代码不在本仓改。

> 文档基准日：2026-08-27。集群设备 / GPU / 挂载 / 凭据只看 [`../ToIV/AGENTS.md`](../ToIV/AGENTS.md)，本文件不复制。

## 远程（已改名 DashBox 并双推）

旧 slug `AICG-DownLoader` / `AIGCPannel` / `LibTV` / `comfy-downloader` 是本仓 rename 跳转，**不要当独立仓删**。旧 README / `STATE.json` / `DEVELOPMENT.md` 里「远程仍为 AICG-DownLoader」的句子已过时，以 git 与 [`../项目登记册.md`](../项目登记册.md) 为准。

| 远程 | URL | 说明 |
|------|-----|------|
| `origin` | https://gitee.com/Winery_z/DashBox.git | Gitee，主远程 |
| `github` | https://github.com/zhwangsir/DashBox.git | GitHub 备份 |

- 当前 `main` 尖端：`4185c30`（`feat: GUI/打包显示名改为 DashBox 模型库`）。Gitee/GitHub 已双推，未强推；`2117d92`/`2829ff8` docs 已一并上去
- 融合提交：`e3e30c0`（`feat: 产品更名为 AIGCPannel，融合下载器、短剧平台与 dashbox`）
- 其后文档提交即 `c0b73d0`

胶水层 GUI/打包显示名是 **DashBox 模型库**。Crate 名与 OS 配置目录仍是 **`comfy-downloader`**（保住已有 `models.json` 路径）。安装 `DefaultDirName`/`AppId` 为升级兼容未改。NOTICE 仍历史写 AIGCPannel。`Cargo.toml` 的 `package.name` 仍为 `comfy-downloader` 0.1.0；其 `homepage` / `repository` 字段仍写旧仓名，**以本表 git remote 为准**。

## 启动

产品启动 `./start-dashbox.sh`：短剧后端 `:8100` + DashBox `:8080`/`:8780`。主界面 `:8080`。`start-aigcpannel.sh` 是薄封装转调。

脚本实际调用同目录 python 启动器。

左侧导航已有「模型库」「引擎」。引擎页只做启动说明、状态与链接，可手动刷新探测本机 8080/8780。

工作台 panel 状态：HTTP GET `/api/panel/status`（不拉起 Rust 桌面端）。返回 product=DashBox、下载器 config/models.json 是否可读、DashBox URL。

## 短剧 API 反代

DashBox `:8780` 反代 `/api/drama/*` 到 `host.docker.internal:8100`（短剧后端）。`:8080`/`:8780`/`:8100` 的 `/api/drama/health` 均 200。

## 画布 Studio（2026-08-27，`19a3141`）

`NSFWDramaStudioNode` 默认 `pipelineEngine=drama`：剧本/首帧 `/api/drama/script|storyboard/generate_async`；配音/出片/合成 `/api/drama/{voice|video|edit}/generate_async`；失败回退 R18；可切换。edit 可省略 `subtitle_url`（不下载空 SRT），空字幕不再回退 R18。web 已烤进镜像 dashbox-web:latest e09bb3b548e8（容器与镜像 SPA md5 一致，不再 docker-cp overlay）。Dockerfile 未改故未 commit。nginx CSP `img-src` 含 `http://192.168.71.127:8188`。LICENSE/NOTICE/品牌未动。

## 模型库 / 网关（2026-08-27，代码未 commit）

registry 在 NAS 不可读时明确报错，不再空列表。扫描根含 `/Users/wangzhenyu/NAS/Windows/ComfyUI/ComfyUIModel/models`。本机 MateBook `~/NAS` 已挂 NAS（非开机自动挂载），模型根可读。registry：loras 101、checkpoints 24。

`gateway/health` 不再探测 studio04/01/02。必选健康：llm spark02、vlm spark01、LB :8188、H3 :8195、TTS :9200、ASR :9210。LTX required=false。集群设备仍只看 ToIV/AGENTS.md。

DashBox 正在本机 web :8080 and api :8780 listening; panel web/api_listening true; Colima disk 20G tight. LICENSE/NOTICE/品牌未改。ToIV 未动。

## 许可（根 NOTICE）

见 [NOTICE](NOTICE)。

一等代码（platform、src、packaging 以及 dashbox 以外文件）为 MIT，见 [LICENSE](LICENSE)，Copyright 2026 WangZhenYu (Winery)。

dashbox 目录是产品内的引擎树（DramaClaw / DashBox / SuperTale CE），协议为 Elastic License 2.0（ELv2），不是 MIT。不要改 dashbox 的 LICENSE、NOTICE 或品牌文件，不要把 dashbox 当成本仓 MIT。

## 仓库结构

- src/：Rust 桌面下载器（crate 名 comfy-downloader；main.rs 与 sys_info.rs）
- platform/：AI 短剧工作台。backend 为 FastAPI（drama-platform-backend 0.4.0，Python 3.11+，uv）；frontend 为 React + TypeScript + Vite + Zustand（dev 端口 3501）
- platform/deploy/ 只保留 comfyui-lb
- dashbox/：产品壳引擎树（ELv2 第三方树；LICENSE/NOTICE/品牌不要覆盖）
- packaging/：下载器 Windows / macOS / Linux 安装器元数据
- 根目录启动脚本：start-dashbox.sh 为主；start-aigcpannel.sh 薄封装；start-engine 仍可单独拉引擎
- NOTICE、LICENSE、Cargo.toml，以及文末五件套

2026-08-27 已从 platform/deploy 删除 deepfilternet、hunyuanimage、latentsync、video-enhance、xdit-video（M23 已下线且无 Python import），只保留 comfyui-lb。

CORS 默认源已去掉 localhost:1420（旧 Tauri 桌面端口）。frontend 的 package.json 没有 @tauri-apps。默认 CORS 含 localhost:3501 等本地前端源（见 platform/backend/app/config.py 的 cors_origins）。

## 短剧工作台（platform/）

从「一句话创意」到「可播放短剧成片」。细节以 [platform/README.md](platform/README.md) 为准；引擎地址与 GPU 占用以 [../ToIV/AGENTS.md](../ToIV/AGENTS.md) 为准，勿把集群清单抄进本文件。

Agent 管线（platform README）：剧本、角色、分镜、视频、配音、字幕、剪辑、文本质检、视觉质检；另有 ai_optimizer 与内置 RAG 提示词优化。

视频双引擎路由：有台词或参考资产走 MiniMax H3；长镜或纯运动走 LTX-2.5；失败回退链为 ltx 到 h3 再到 comfyui Wan2.2。

模型注册表融合工作台 lora_manifest 与下载器 models.json（drama router 的 models/registry）。左侧模型库走 models 路由与上述 registry。

下载器配置环境变量：DOWNLOADER_CONFIG_PATH（默认仓库根 config.json）、DOWNLOADER_MODELS_JSON（可选覆盖清单）。后端挂载 static 下的 audio、subtitle、video。

STATE.json 项目版本字段为 0.34.0（里程碑记到 M27）；后端 pyproject.toml 为 0.4.0。

手工启动：在 platform/backend 执行 uv sync --extra dev，再用 uv run 启动 FastAPI，host 0.0.0.0、port 8100。frontend 的 vite 配置已固定 3501，代理 api 与 static 到 8100。

## 模型下载器（Rust / egui）

桌面端仍是纯 Rust 原生 GUI（eframe / egui 0.29），单文件可执行，Windows / macOS / Linux。给本地 ComfyUI 用户：把 Civitai / HuggingFace 模型下到正确子目录。产物名 target/release/comfy-downloader。融合后工作台左侧也可浏览与下载；桌面 crate 继续维护。

## 模型下载器功能（现有 DEVELOPMENT / 源码仍支持）

- **🔍 模型搜索（Civitai + HuggingFace）**：
  - Civitai：关键词 + 类型 + 底模（SDXL/Flux/Wan…）过滤，预览图卡片，游标分页「加载更多」；点卡片进详情页看版本/触发词（LoRA，可一键复制）/发布日期/画廊
  - HuggingFace：按仓库关键词搜索 → 列出仓库内的模型权重文件（按大小排序，标注目标目录）→ 逐个下载，自动归类 + SHA256 校验
- **🔗 链接解析**：粘贴 Civitai 模型页或 HuggingFace 文件页链接，自动识别类型/文件名/目标目录，多版本可选
- **🎬 作品页一键成套**：粘贴别人发布的图片/视频/帖子链接（`civitai.com/images|posts/...`），自动解析该作品用到的全部模型（含模型简介），勾选后整套下载（精确到作品使用的版本，自动带 SHA256）。注：创作者隐藏生成信息的作品无法解析
- **📦 预设套餐**：内置「Wan 2.2 图生视频全套（16G 显存适配）」「Flux.1 fp8 文生图」，一键整套入队
- **📋 工作流分析**：把 ComfyUI 工作流 .json 拖进窗口，列出引用的全部模型并标出本地缺失项（支持界面/API 双格式与子图），缺失项一键跳转搜索
- **📁 模型库管理**：卡片式浏览各 `models/*` 子目录的模型，带预览缩略图（同名 .png）、大小、磁盘占用统计；**一键识别**（算 SHA256 → Civitai 反查"这是什么模型/哪个版本"，结果缓存）；按名筛选、疑似重复提示、在资源管理器定位、删除（带确认）。支持 `extra_model_paths.yaml` 里的额外路径（模型放在别的盘也能管理）
- **🔌 ComfyUI 实例联动**：在设置里填服务地址（默认 127.0.0.1:8188）测试连接；工作流分析会用运行中的 ComfyUI 核对模型（覆盖本工具未扫描的额外路径/软链），区分"本地已有 / ComfyUI 已加载 / 真缺失"，真缺失若是内置套餐里的组件可一键补齐
- **下载队列**：
  - 断点续传（`.part` + HTTP Range，校验 206 防损坏）
  - **SHA256 自动校验**（Civitai/HF 哈希，边下边算，失败删文件，通过显示绿色徽标）
  - 失败自动重试（指数退避 2/4/8s）+ 手动重试按钮
  - 全局暂停 / 恢复；任务持久化（关闭程序后重开自动恢复续传）
  - 同名任务去重、单实例保护（防止两个窗口写坏同一文件）
- **国内网络友好**：hf-mirror 镜像开关；自动读取系统代理环境变量（`HTTPS_PROXY`/`HTTP_PROXY`，适配 Clash 等）
- **中文界面**：启动时自动加载系统 CJK 字体（Windows/macOS/Linux 主流路径 + 递归扫描兜底）

### 编译与测试

需要 Rust 工具链（Windows 另需 MSVC C++ Build Tools）：

    cargo build --release
    cargo test --release
    cargo test --release -- --ignored

配置文件查找顺序：可执行文件同目录（便携）到系统配置目录（Windows 为 APPDATA 下 comfy-downloader，Linux 为 ~/.config/comfy-downloader/，macOS 为 ~/Library/Application Support/comfy-downloader/）。字段示例见 [config.json.example](config.json.example)。civitai_token 明文存在 config 里，不要分享该文件。

ureq 禁用 gzip，以保证 Content-Length 与断点续传正确。HF tree API 单页 1000 项，超大目录可能查不到哈希（跳过校验，不影响下载）。

## 测试基线（STATE.json test_summary，条目日期 2026-08-15）

| 端 | 记录 |
|----|------|
| 后端 | 797 passed；覆盖率记 87.07%（unit + 部分 integration，门槛 80%） |
| 前端 | vitest 110；tsc 0；build 成功 |
| Rust | 46 passed / 0 failed（含 M24.3 trigger_words 4 例） |
| 实机 | M24.4 API 47 项全过（STATE 记载） |

2026-08-27 融合收尾在 TEST_LOG.md 记了 CORS、去掉 Tauri、重建 .venv、引擎页探测；未在当日重跑上述全量数字。M25 在 STATE 中仍为 planning。

## 同组与边界

- 同项目组还有 aigc-auth（鉴权，非 git，cloud 8001），见 [../aigc-auth/README.md](../aigc-auth/README.md)
- 禁止改 ToIV；集群真相只在 ToIV/AGENTS.md
- 不要改 dashbox 许可与品牌文件
- 旧散文档已归档到 ALLProject/.archive/docs-legacy-20260827/

## 文档五件套

- [README.md](README.md) — 本文件
- [AGENTS.md](AGENTS.md) — 本项目规则（集群见 ToIV/AGENTS.md）
- [DEVELOPMENT.md](DEVELOPMENT.md) — 开发/部署长文（远程改名段落可能滞后，以本文「远程」节为准）
- [STATE.json](STATE.json) — 状态快照（顶部 description 可能滞后）
- [TEST_LOG.md](TEST_LOG.md) — 测试与核验日志

