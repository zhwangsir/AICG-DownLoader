# AIGCPannel

**AIGCPannel** 是短剧产品。`dashbox/` 是收尾引擎（`:8080`/`:8780`），不再当独立产品。ToIV 是聚合平台；本仓不是第二个 ToIV。短剧流水线（`platform/`）和 ComfyUI 模型下载器（`src/`）是模块。仓名/目录 `ALLProject/AIGCPannel`。旧 slug 只跳转不删。

目录 `ALLProject/AIGCPannel`。远程 origin [gitee.com/Winery_z/AIGCPannel](https://gitee.com/Winery_z/AIGCPannel) 与 github [github.com/zhwangsir/AIGCPannel](https://github.com/zhwangsir/AIGCPannel)，尖端 `bc85d48`（已双推，未强推）。只这一根融合仓。旧 slug `AICG-DownLoader` / `DashBox` / `LibTV` / `comfy-downloader` 是本仓 rename 跳转，**不要删**。

后续开发与测试归 AICG 开发；五件套归项目管家。ToIV 业务代码不在本仓改。

> 文档基准日：2026-08-27。集群设备 / GPU / 挂载 / 凭据只看 [`../ToIV/AGENTS.md`](../ToIV/AGENTS.md)，本文件不复制。

## 远程（已改名 AIGCPannel 并双推）

旧 slug `AICG-DownLoader` / `DashBox` / `LibTV` / `comfy-downloader` 是本仓 rename 跳转，**不要当独立仓删**。旧 README / `STATE.json` / `DEVELOPMENT.md` 里「远程仍为 AICG-DownLoader」的句子已过时，以 git 与 [`../项目登记册.md`](../项目登记册.md) 为准。

| 远程 | URL | 说明 |
|------|-----|------|
| `origin` | https://gitee.com/Winery_z/AIGCPannel.git | Gitee，主远程 |
| `github` | https://github.com/zhwangsir/AIGCPannel.git | GitHub 备份 |

- 当前 `main` 尖端：`bc85d48`（`fix: 模型下载根使用第一个可读 NAS 路径`）。Gitee/GitHub 已双推，未强推；含 `0511598`，叠在 docs `85e0787` 上
- 融合提交：`e3e30c0`（`feat: 产品更名为 AIGCPannel，融合下载器、短剧平台与 dashbox`）
- 其后文档提交即 `c0b73d0`

胶水层产品名 **AIGCPannel**。`dashbox/` 是收尾引擎，不再当独立产品。Crate 名与 OS 配置目录仍是 **`comfy-downloader`**（保住已有 `models.json` 路径）。`Cargo.toml` `homepage`/`repository` 已指向 AIGCPannel。安装 `DefaultDirName`/`AppId` 为升级兼容未改。NOTICE 仍历史写 AIGCPannel。上游 LICENSE/NOTICE 保留 ELv2，不要改成 MIT。





## 引擎对照（2026-08-28 晚，AICG 调研 HF+Civitai）

用户已定口径：AIGCPannel **SFW** 对白/锁人=MiniMax **H3**（海螺 3.0）；空镜/预览=**LTX-2.5**（`:8198` 起来再开，代码保留）。**Wan2.2** 与 **LTX-2.3+10Eros** 留 ToIV **R18**，价值主要在 NSFW，不是短剧 SFW 空镜/无声 fallback。ToIV 不换主路；AIGCPannel 不改 ToIV。Round 1 已落：剧本加速、NAS 可读下载根、Colima prune、H3 一镜冒烟。 LICENSE/NOTICE/ToIV 未动。

ToIV 对照细项（ToIV 开发读 `.env.example` / `engine_registry.py`，没改代码、不推）：视频主路 MiniMax **H3** `:8195`（海螺开源权重）。R18 故意留 **LTX-2.3+10Eros v14**，不跟 LTX-2.5。ToIV 侧 SFW LTX-2.5 已于 2026-08-23 退役；本地未推 Phase 4 有 `ltx25-multishot`，不是默认。无声/动作/R18 I2V 走 **Wan2.2**；编辑/转场/关键帧链仍是 **Wan2.1-VACE-14B**（产品代际，不是主路写错成 2.1）。长视频 LongCat `:8197`。图像默认 `flux2_dev_fp8mixed`，文生图可选 `qwen_image` / `z_image`；`qwen-image-edit` 在；R18 图 URPM。3D=Hunyuan3D，没挂混元视频 1.0。和 AIGCPannel 的差：ToIV 图像已是 FLUX.2/Qwen/Z-Image，AIGCPannel 仍 SDXL+IPAdapter（用户点名才追）。H3 主路两边对齐。

## spark01 LLM/VLM 代码落地（2026-09-02，`f5a4037`）

`f5a4037` 已双推（叠 `16fb242`）：AIGCPannel 剧本/角色/分镜/Context-IR 聊天 LLM 与 VLM 默认都是 `http://192.168.71.82:8000/v1`、模型 `qwen3.8-flash-next`。`/gateway/health` 必选 llm+vlm = spark01 `.82`，报告里不再出现 `.84`，`required_down=[]`。`start-aigcpannel.py` / local_gateway `LOCAL_LLM_BASE_URL` 与 `LOCAL_CHAT_MODEL` 同样默认。平台 `.env.example` 已改；本机 gitignored `.env` 已改未提交。未 SSH spark02，`.84` 服务未关。本机 `:8100`/`:8790` 健康；`:8080` 未动。缺口：若干注释/RFC/drift 脚本仍写 spark02 `.84`；dashbox docker `settings.db` 未重钉（代码 `CLUSTER_LLM` 默认已是 spark01）。LICENSE/NOTICE/ToIV 未动。

## 剧本/改写/视觉改走 spark01（2026-09-02）

2026-09-02 用户定口径（设备管家回写）：AIGCPannel 剧本/改写/视觉都用 spark01 `.82:8000` `qwen3.8-flash-next`。不拉 spark02 `.84:8000`。网关 `gateway/health` 必选不再含 spark02 LLM（llm+vlm 都 spark01）。`.84` 上的服务没动；spark02 不等于整集群退役（ToIV 可能仍用）。LICENSE/NOTICE/ToIV 未动。

## 短剧 compose 默认 24fps（2026-09-02，`505d039`）

`505d039` 已双推（叠 `ccfe7a6`）：短剧 compose/export 默认 `output_fps=24`。Canvas compose 固定 24；EditModal 默认 `FPS_OPTIONS[0]=24`；`FPS_OPTIONS=[24,30,60]`（30/60 仍可选）。schemas Edit/Pipeline `output_fps` 描述钉 24，值本来就是 24。未改 DashBox episode compose 1080×1920，未把 1.5× 标成 2K。未 SSH spark02。仍空：voice 3–8s；`happyhorse-1.0` 仍作 H3 别名；DashBox episode compose 仍 1080×1920。LICENSE/NOTICE/ToIV 未动。

## P6 compose 768P / 关 auto-LTX（2026-09-02，`1d5c2be`）

`1d5c2be` 已双推（P6，叠 `18f06d8`）：短剧 compose/export 默认 `768x1344`（横屏 `1344x768`）。`route_video_engine` 与网关 `_select_video_backend` 不再 auto-LTX；`/v1/models` 仅 `LOCAL_LTX_ENABLED=true` 才列出 LTX-2.5。LTX 代码仍在 `ltx_enabled` 后。H3 成片 768P 未改。DashBox 1.5× 未标成 2K。未 SSH spark02。缺口：voice 3–8s 仍未做；Canvas/EditModal compose fps 仍 30（schema 默认 24，AICG 接着改）；`happyhorse-1.0` 仍作 H3 别名列出；DashBox episode compose 仍 1080×1920（引擎导出，不是短剧 generate）。LICENSE/NOTICE/ToIV 未动。

## P5 短剧只留 H3 / VLM flash-next（2026-09-02，`7623d05`）

`7623d05` 已双推（P5，叠 `240d34d`）：短剧 generate 列表/UI 只留 H3（`h3-aio`/`h3-clean`），POST 不再接受 `wan22-*`；非法 action preset 映射到 `h3-aio`。Wan JSON 仍在盘上。MiniMax-H3-local 模板只 768P；UI `768×1344`/`1344×768`。一键成片 orchestrator 钉 `engine=h3, preview=false, quality=final`，`quality=final` 压过全局 `h3_turbo_enabled`。VLM 默认 `visual_model_name` / `LOCAL_VLM_MODEL` 改为 `qwen3.8-flash-next`（用户指定：更强、100万上下文、带视觉），env 仍可覆盖。未 SSH spark02。缺口：LTX auto 路由代码还在但一键成片钉 H3；compose 导出标签仍 1080x1920；voice 3–8s 未做。LICENSE/NOTICE/ToIV 未动。

## P4 AddGuide 修复 / 漫剧 pack（2026-09-02，`16dbbd5`）

`16dbbd5` 已双推（P4，叠 `a77032a`）：repair/inpaint 插 `MiniMaxH3AddGuide` + `LoadImageMask`/`SetLatentNoiseMask`，并降低 BasicScheduler denoise；mask 走 `SetLatentNoiseMask`（`SamplerCustomAdvanced` 无 `denoise_mask`）。`:8195` 现无 AddGuide 则 fail-closed `502`/`H3RepairUnavailable`，不回退 Wan/LTX。漫剧 pack 偏 `animagineXL40` 关键帧 + IPAdapter 0.85，视频引擎仍 H3 FL2VA/Ref2VA。NSFW PIN 默认仍 10Eros；`nsfw_variant=dasiwa` 仅 opt-in A/B（UNETLoader 只有 `minimax_h3_*` 和 10Eros，无 DaSiWa 权重，选了会预检失败）。Remix（civitai 2879272）未接线。P2 尾帧串镜仍默认开；P3 Turbo+内容 LoRA 仍拒绝。`:8195` 快探约 1s、855 节点：有 `MiniMaxH3ImageToVideo`/`ReferenceToVideo`/`TurboLoRA`/`SetLatentNoiseMask`/`LoadImageMask`；无 `MiniMaxH3AddGuide`（需升 ComfyUI，Comfy-Org #15439）。缺口：repair 会 502 直到 H3 那台 ComfyUI 升级；DaSiWa 只是 hook；没有独立 repair UI（只有 `VideoGenerateParams` 可选字段）。P0–P4 代码刀完。LICENSE/NOTICE/ToIV 未动。

## P3 Turbo 预览 / 成片 20 步（2026-09-02，`c27f6db`）

`c27f6db` 已双推（P3，叠 `291d994`）：`preview=true` / `quality=preview` 开 Turbo（`MiniMaxH3TurboLoRA`+`MiniMaxH3TurboSampler`；FL2VA 8 步、Ref2VA 4 步）。成片默认 / `preview=false` / `quality=final` 关 Turbo、原生 20 步；`h3_turbo_enabled` 配置默认仍 False。SFW turbo LoRA 是 `minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`，不是 10Eros。NSFW 预览可用 `10Eros_Max_h3_TURBO_ref2va.safetensors`（未在 NAS 上实锤文件名）。Turbo+内容 LoRA 直接拒绝（已知 shape 错）。工作台 VideoModal「Turbo 预览」vs「生成视频」；画布一键成片传 `preview:false, quality:final`。缺口：没用官方 `minimax_h3_fl2v`/`lightx2v` 名（`:8195` 产品默认已是 v4 pruned）；一键 pipeline 不传 preview；无 `:8195` 真机 Turbo 冒烟。LICENSE/NOTICE/ToIV 未动。

## P2 尾帧串镜 / FL2VA（2026-09-02，`a284c52`）

`a284c52` 已双推（P2，叠 `176ab03`）：尾帧串镜默认开，失败重试一次再降级只首帧；角色三视图+正脸+可选声纹进 Ref2VA；空镜 SFW/NSFW 都走 H3 **FL2VA**，不走 Wan；LTX-2.5 仅 `ltx_enabled` 且 `:8198` 活着。LICENSE/NOTICE/ToIV 未动。

## P1 H3 Context-IR（2026-09-02，`412f0ba`）

`412f0ba` 已双推（P1）：H3 出片前 spark 本地 **Context-IR** 改写，失败回退原文。LICENSE/NOTICE/ToIV 未动。

## P0 H3 Ref2VA（2026-09-02，`bf9fe4c`）

`bf9fe4c` 已双推（P0）：工作台/网关有参考走 `MiniMaxH3ReferenceToVideo`；PIN 开用 **10Eros_Max** H3 UNet，关用官方 INT8；对白只留 H3 原生音，不叠 IndexTTS；目录分辨率只留 **768P**，假 2K 去掉。NSFW 也是 H3，不走 Wan/LTX。LICENSE/NOTICE/ToIV 未动。

## 引擎 Settings 钉集群（2026-08-29，`7c75196`）

`7c75196` 已双推：引擎 Settings 钉到集群。custom 网关 `configured=true`，base `http://host.docker.internal:8790/v1`；LLM spark02 `.84:8000`；VLM spark01 `.82:8000`；图 ComfyUI `.127:8188` SDXL；视频 H3 `.127:8195`；TTS IndexTTS `.127:9200`；媒体 `relay=local_http`。LTX-2.5 仍配置但 `:8198` DOWN。官方 relayclaw 频道留着但 custom 模式不用。LICENSE/NOTICE/ToIV 未动。

## H3 出片冒烟（2026-08-28，无新代码）

H3 `generate_async` 已跑通（无新代码）。task `video-a54cf30392c7`，约 1.5min，mp4 768x1344 3s。HEAD 仍 `71d616f`。`:8080` 未反代 `/static/video`（410），本机静态在 `:8100`。 LICENSE/NOTICE/ToIV 未动。

## 升级第一轮（2026-08-28，`bc85d48`）

`0511598`：剧本默认关闭 thinking，`web_search` 改为请求/环境开关且默认关（避免 spark/qwen 思考链把一句话出剧本拖到十几分钟）。`bc85d48`：模型下载根改用第一个存在且可读/可写的 NAS 路径（Mac 上 `nas_model_roots` 第一项常是不可读的 `/mnt/toiv-nas`）。 LICENSE/NOTICE/ToIV 未动。H3 出片冒烟已跑通。

## config 默认（2026-08-28，`5a19c8d`）

无 `.env` 时默认 LTX off（`ltx_enabled=false`）、TTS=`indextts`、LLM=spark02 `qwen3.6-uncensored`、VLM=spark01 `qwen3-vl-32b`，与 `ToIV/.env.example` 一致。 集群设备/IP 只看 ToIV/AGENTS.md。LICENSE/NOTICE/品牌未动。

## 启动

产品启动 `./start-aigcpannel.sh`：短剧后端 `:8100` + 引擎 `:8080`/`:8780`。主界面 `:8080`。`start-dashbox.sh` 转调同一入口。活着的 `:8080` 镜像标题现为「AIGCPannel — 通用 AIGC 视频引擎」；HTML 里不再当产品名写 DashBox/虾导（web 已 rebuild，无新代码 commit）。

脚本实际调用同目录 python 启动器。

左侧导航已有「模型库」「引擎」。引擎页只做启动说明、状态与链接，可手动刷新探测本机 8080/8780。

工作台 panel 状态：HTTP GET `/api/panel/status`（不拉起 Rust 桌面端）。返回 product=AIGCPannel、下载器 config/models.json 是否可读、DashBox URL。

## 短剧 API 反代

DashBox `:8780` 反代 `/api/drama/*` 到 `host.docker.internal:8100`（短剧后端）。`:8080`/`:8780`/`:8100` 的 `/api/drama/health` 均 200。

## 画布 Studio（2026-08-27，`19a3141`）

`NSFWDramaStudioNode` 默认 `pipelineEngine=drama`：剧本/首帧 `/api/drama/script|storyboard/generate_async`；配音/出片/合成 `/api/drama/{voice|video|edit}/generate_async`；失败回退 R18；可切换。edit 可省略 `subtitle_url`（不下载空 SRT），空字幕不再回退 R18。活 web 镜像 `dashbox-web:latest` `11444d78e507`（标题「AIGCPannel — 通用 AIGC 视频引擎」）。旧 ID `e09bb3b548e8` 已过时。nginx CSP `img-src` 含 `http://192.168.71.127:8188`。LICENSE/NOTICE/品牌未动。

## 模型库 / 网关（2026-08-27，代码未 commit）

registry 在 NAS 不可读时明确报错，不再空列表。扫描根含 `/Users/wangzhenyu/NAS/Windows/ComfyUI/ComfyUIModel/models`。本机 MateBook `~/NAS` 已挂 NAS（非开机自动挂载），模型根可读。registry：loras 101、checkpoints 24。

`gateway/health` 不再探测 studio04/01/02。2026-08-27 当时必选含 llm spark02；**2026-09-02 覆盖**：必选健康为 llm spark01、vlm spark01（均为 `qwen3.8-flash-next` `.82:8000`）、LB :8188、H3 :8195、TTS :9200、ASR :9210。不再硬依赖 spark02 LLM。LTX required=false。集群设备仍只看 ToIV/AGENTS.md。

DashBox 正在本机 web :8080 and api :8780 listening; panel web/api_listening true; Colima disk 20G tight. LICENSE/NOTICE/品牌未改。ToIV 未动。

## 许可（根 NOTICE）

见 [NOTICE](NOTICE)。

一等代码（platform、src、packaging 以及 dashbox 以外文件）为 MIT，见 [LICENSE](LICENSE)，Copyright 2026 WangZhenYu (Winery)。

dashbox 目录是产品内的引擎树（DramaClaw / DashBox / SuperTale CE），协议为 Elastic License 2.0（ELv2），不是 MIT。不要改 dashbox 的 LICENSE、NOTICE 或品牌文件，不要把 dashbox 当成本仓 MIT。

## 仓库结构

- src/：Rust 桌面下载器（crate 名 comfy-downloader；main.rs 与 sys_info.rs）
- platform/：AI 短剧工作台。backend 为 FastAPI（drama-platform-backend 0.4.0，Python 3.11+，uv）；frontend 为 React + TypeScript + Vite + Zustand（dev 端口 3501）
- platform/deploy/ 只保留 comfyui-lb
- dashbox/：收尾引擎树（ELv2 第三方树；LICENSE/NOTICE/品牌不要覆盖，不要当独立产品）
- packaging/：下载器 Windows / macOS / Linux 安装器元数据
- 根目录启动脚本：`start-aigcpannel.sh` 是 canonical；`start-dashbox.sh` 转调同一入口；start-engine 仍可单独拉引擎
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

