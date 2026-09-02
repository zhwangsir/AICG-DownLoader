# DEVELOPMENT.md — AIGCPannel

> 合并自旧 PROJECT_INIT / docs / 根目录散文档。原文在 `ALLProject/.archive/docs-legacy-20260827/`。
> 最后更新：2026-08-28
> 身份：AIGCPannel 是产品；`dashbox/` 是收尾引擎。目录 `ALLProject/AIGCPannel`，远程 Gitee/GitHub 均名 AIGCPannel，尖端 `bc85d48`。上游 LICENSE/NOTICE 保留 ELv2，不要改成 MIT。NOTICE 仍历史写 AIGCPannel。
>
> **2026-08-27 融合第一刀**：`./start-dashbox.sh`（短剧 :8100 + DashBox :8080/:8780，主界面 :8080）；`start-aigcpannel.sh` 薄封装转调。根 NOTICE 声明 dashbox/ 为 ELv2。crate 仍 `comfy-downloader`。已删 platform/deploy 下 deepfilternet / hunyuanimage / latentsync / video-enhance / xdit-video，保留 comfyui-lb。左侧导航新增模型库、引擎。`GET /api/panel/status`。未 commit/push。
> **2026-08-27 model library/gateway (uncommitted):** registry errors if NAS unreadable; health required spark02/spark01/LB:8188/H3:8195/TTS:9200/ASR:9210; no studio04/01/02; LTX required=false. DashBox web :8080 and api :8780 listening; Colima disk 20G tight. NAS mounted at ~/NAS (not on boot); loras 101 checkpoints 24.


## 启动（第一波融合）

| 脚本 | 作用 | 端口 |
|------|------|------|
| `./start-aigcpannel.sh` | canonical：短剧后端 + DashBox 引擎 | `:8100` + `:8080`/`:8780`；主界面 `:8080` |
| `./start-dashbox.sh` | 转调 start-aigcpannel | 同上 |
| `./start-engine.sh` | 仅引擎（默认打印，`--up` 拉起） | Web `:8080`，API `:8780` |

`GET /api/panel/status` 返回 product=`AIGCPannel`、downloader config/models.json 可读性、DashBox URL。不拉起 Rust 桌面端。

**2026-08-27 画布接线**：DashBox `:8780` 反代 `/api/drama/*` 到 `host.docker.internal:8100`。`:8080`/`:8780`/`:8100` `/api/drama/health` 均 200。

**2026-08-27 Studio `19a3141`**：`NSFWDramaStudioNode` 默认 `pipelineEngine=drama`；剧本/首帧 `/api/drama/script|storyboard/generate_async`；配音/出片/合成 `/api/drama/{voice|video|edit}/generate_async`；失败回退 R18；可切换。edit 可省略 `subtitle_url`（不下载空 SRT），空字幕不再回退 R18。活 web 镜像 `dashbox-web:latest` `11444d78e507`。旧 ID `e09bb3b548e8` 已过时。nginx CSP `img-src` 含 `http://192.168.71.127:8188`。


**2026-08-28 `4185c30`**：GUI/打包显示名改为「DashBox 模型库」（窗口、托盘、build.rs ProductName、安装器显示名）。crate/OS 配置目录仍 `comfy-downloader`；安装 DefaultDirName/AppId 为升级兼容未改。NOTICE 仍历史写 AIGCPannel。旧 slug AICG-DownLoader / AIGCPannel 是 redirect，不要删。健康：`:8080`、`:8780/api/drama/health`、`:8100/api/drama/health` 均为 200。Gitee/GitHub main 均 `4185c30`（未强推）；本地 docs `2117d92`/`2829ff8` 已一并上去。LICENSE/品牌/ToIV 未动。


**2026-08-28 `46b1994`**：产品身份收成 AIGCPannel，DashBox 作引擎模块（`:8080`/`:8780`）。canonical start `./start-aigcpannel.sh`；`start-dashbox.sh` 转调同一入口。crate/OS 配置仍 `comfy-downloader`。仓名/目录/旧 slug 仍 DashBox，不要改、不要删。健康：`:8080`、`:8780/api/drama/health`、`:8100/api/drama/health` 均为 200；panel product=`AIGCPannel`。Gitee/GitHub main 均 `46b1994`（未强推，含 docs `923b940`）。LICENSE/品牌/ToIV 未动。


**2026-08-28 `5a19c8d`**：无 `.env` 时默认 LTX off（`ltx_enabled=false`）、TTS=`indextts`、LLM=spark02 `qwen3.6-uncensored`、VLM=spark01 `qwen3-vl-32b`，与 `ToIV/.env.example` 一致。 叠在 docs `7aa28cc` 上。Gitee/GitHub 已双推，未强推。LICENSE/品牌/ToIV 未动。


**2026-08-28 `378f5c7`**：引擎壳与仓名收成 AIGCPannel。目录 `ALLProject/AIGCPannel`；origin `gitee.com/Winery_z/AIGCPannel`，github `github.com/zhwangsir/AIGCPannel`。旧 slug AICG-DownLoader / DashBox / LibTV / comfy-downloader 均为跳转，不要删。`dashbox/` 不再当独立产品。crate 仍 `comfy-downloader`。LICENSE/NOTICE 未改。活着的 `:8080` 镜像标题现为「AIGCPannel — 通用 AIGC 视频引擎」；HTML 里不再当产品名写 DashBox/虾导（web 已 rebuild，无新代码 commit）。Gitee/GitHub 已双推，未强推。ToIV 未动。


**2026-08-28 `bc85d48`（含 `0511598`）**：`0511598`：剧本默认关闭 thinking，`web_search` 改为请求/环境开关且默认关（避免 spark/qwen 思考链把一句话出剧本拖到十几分钟）。`bc85d48`：模型下载根改用第一个存在且可读/可写的 NAS 路径（Mac 上 `nas_model_roots` 第一项常是不可读的 `/mnt/toiv-nas`）。 叠在 docs `85e0787` 上。Gitee/GitHub 已双推，未强推。LICENSE/NOTICE/ToIV 未动。H3 出片冒烟已跑通。


**2026-08-28 H3 出片冒烟（无新代码）**：H3 `generate_async` 已跑通（无新代码）。task `video-a54cf30392c7`，约 1.5min，mp4 768x1344 3s。HEAD 仍 `71d616f`。`:8080` 未反代 `/static/video`（410），本机静态在 `:8100`。 LICENSE/NOTICE/ToIV 未动。


**2026-09-02 `1d5c2be`（已双推）**：`1d5c2be` 已双推（P6，叠 `18f06d8`）：短剧 compose/export 默认 `768x1344`（横屏 `1344x768`）。`route_video_engine` 与网关 `_select_video_backend` 不再 auto-LTX；`/v1/models` 仅 `LOCAL_LTX_ENABLED=true` 才列出 LTX-2.5。LTX 代码仍在 `ltx_enabled` 后。H3 成片 768P 未改。DashBox 1.5× 未标成 2K。未 SSH spark02。缺口：voice 3–8s 仍未做；Canvas/EditModal compose fps 仍 30（schema 默认 24，AICG 接着改）；`happyhorse-1.0` 仍作 H3 别名列出；DashBox episode compose 仍 1080×1920（引擎导出，不是短剧 generate）。LICENSE/NOTICE/ToIV 未动。

**2026-09-02 `7623d05`（已双推）**：`7623d05` 已双推（P5，叠 `240d34d`）：短剧 generate 列表/UI 只留 H3（`h3-aio`/`h3-clean`），POST 不再接受 `wan22-*`；非法 action preset 映射到 `h3-aio`。Wan JSON 仍在盘上。MiniMax-H3-local 模板只 768P；UI `768×1344`/`1344×768`。一键成片 orchestrator 钉 `engine=h3, preview=false, quality=final`，`quality=final` 压过全局 `h3_turbo_enabled`。VLM 默认 `visual_model_name` / `LOCAL_VLM_MODEL` 改为 `qwen3.8-flash-next`（用户指定：更强、100万上下文、带视觉），env 仍可覆盖。未 SSH spark02。缺口：LTX auto 路由代码还在但一键成片钉 H3；compose 导出标签仍 1080x1920；voice 3–8s 未做。LICENSE/NOTICE/ToIV 未动。

**2026-09-02 `16dbbd5`（已双推）**：`16dbbd5` 已双推（P4，叠 `a77032a`）：repair/inpaint 插 `MiniMaxH3AddGuide` + `LoadImageMask`/`SetLatentNoiseMask`，并降低 BasicScheduler denoise；mask 走 `SetLatentNoiseMask`（`SamplerCustomAdvanced` 无 `denoise_mask`）。`:8195` 现无 AddGuide 则 fail-closed `502`/`H3RepairUnavailable`，不回退 Wan/LTX。漫剧 pack 偏 `animagineXL40` 关键帧 + IPAdapter 0.85，视频引擎仍 H3 FL2VA/Ref2VA。NSFW PIN 默认仍 10Eros；`nsfw_variant=dasiwa` 仅 opt-in A/B（UNETLoader 只有 `minimax_h3_*` 和 10Eros，无 DaSiWa 权重，选了会预检失败）。Remix（civitai 2879272）未接线。P2 尾帧串镜仍默认开；P3 Turbo+内容 LoRA 仍拒绝。`:8195` 快探约 1s、855 节点：有 `MiniMaxH3ImageToVideo`/`ReferenceToVideo`/`TurboLoRA`/`SetLatentNoiseMask`/`LoadImageMask`；无 `MiniMaxH3AddGuide`（需升 ComfyUI，Comfy-Org #15439）。缺口：repair 会 502 直到 H3 那台 ComfyUI 升级；DaSiWa 只是 hook；没有独立 repair UI（只有 `VideoGenerateParams` 可选字段）。P0–P4 代码刀完。LICENSE/NOTICE/ToIV 未动。

**2026-09-02 `c27f6db`（已双推）**：`c27f6db` 已双推（P3，叠 `291d994`）：`preview=true` / `quality=preview` 开 Turbo（`MiniMaxH3TurboLoRA`+`MiniMaxH3TurboSampler`；FL2VA 8 步、Ref2VA 4 步）。成片默认 / `preview=false` / `quality=final` 关 Turbo、原生 20 步；`h3_turbo_enabled` 配置默认仍 False。SFW turbo LoRA 是 `minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`，不是 10Eros。NSFW 预览可用 `10Eros_Max_h3_TURBO_ref2va.safetensors`（未在 NAS 上实锤文件名）。Turbo+内容 LoRA 直接拒绝（已知 shape 错）。工作台 VideoModal「Turbo 预览」vs「生成视频」；画布一键成片传 `preview:false, quality:final`。缺口：没用官方 `minimax_h3_fl2v`/`lightx2v` 名（`:8195` 产品默认已是 v4 pruned）；一键 pipeline 不传 preview；无 `:8195` 真机 Turbo 冒烟。LICENSE/NOTICE/ToIV 未动。

**2026-09-02 `a284c52`（已双推）**：`a284c52` 已双推（P2，叠 `176ab03`）：尾帧串镜默认开，失败重试一次再降级只首帧；角色三视图+正脸+可选声纹进 Ref2VA；空镜 SFW/NSFW 都走 H3 **FL2VA**，不走 Wan；LTX-2.5 仅 `ltx_enabled` 且 `:8198` 活着。LICENSE/NOTICE/ToIV 未动。

**2026-09-02 `412f0ba`（已双推）**：`412f0ba` 已双推（P1）：H3 出片前 spark 本地 **Context-IR** 改写，失败回退原文。LICENSE/NOTICE/ToIV 未动。

**2026-09-02 `bf9fe4c`（已双推）**：`bf9fe4c` 已双推（P0）：工作台/网关有参考走 `MiniMaxH3ReferenceToVideo`；PIN 开用 **10Eros_Max** H3 UNet，关用官方 INT8；对白只留 H3 原生音，不叠 IndexTTS；目录分辨率只留 **768P**，假 2K 去掉。NSFW 也是 H3，不走 Wan/LTX。LICENSE/NOTICE/ToIV 未动。

**2026-08-29 `7c75196`**：`7c75196` 已双推：引擎 Settings 钉到集群。custom 网关 `configured=true`，base `http://host.docker.internal:8790/v1`；LLM spark02 `.84:8000`；VLM spark01 `.82:8000`；图 ComfyUI `.127:8188` SDXL；视频 H3 `.127:8195`；TTS IndexTTS `.127:9200`；媒体 `relay=local_http`。LTX-2.5 仍配置但 `:8198` DOWN。官方 relayclaw 频道留着但 custom 模式不用。LICENSE/NOTICE/ToIV 未动。

**2026-08-28 晚 引擎对照（AICG 调研，无新代码）**：用户已定口径：AIGCPannel **SFW** 对白/锁人=MiniMax **H3**（海螺 3.0）；空镜/预览=**LTX-2.5**（`:8198` 起来再开，代码保留）。**Wan2.2** 与 **LTX-2.3+10Eros** 留 ToIV **R18**，价值主要在 NSFW，不是短剧 SFW 空镜/无声 fallback。ToIV 不换主路；AIGCPannel 不改 ToIV。Round 1 已落：剧本加速、NAS 可读下载根、Colima prune、H3 一镜冒烟。 LICENSE/NOTICE/ToIV 未动。

**2026-08-28 晚 ToIV 对照细项（无新代码、不推 ToIV）**：ToIV 对照细项（ToIV 开发读 `.env.example` / `engine_registry.py`，没改代码、不推）：视频主路 MiniMax **H3** `:8195`（海螺开源权重）。R18 故意留 **LTX-2.3+10Eros v14**，不跟 LTX-2.5。ToIV 侧 SFW LTX-2.5 已于 2026-08-23 退役；本地未推 Phase 4 有 `ltx25-multishot`，不是默认。无声/动作/R18 I2V 走 **Wan2.2**；编辑/转场/关键帧链仍是 **Wan2.1-VACE-14B**（产品代际，不是主路写错成 2.1）。长视频 LongCat `:8197`。图像默认 `flux2_dev_fp8mixed`，文生图可选 `qwen_image` / `z_image`；`qwen-image-edit` 在；R18 图 URPM。3D=Hunyuan3D，没挂混元视频 1.0。和 AIGCPannel 的差：ToIV 图像已是 FLUX.2/Qwen/Z-Image，AIGCPannel 仍 SDXL+IPAdapter（用户点名才追）。H3 主路两边对齐。

`platform/deploy/` 现仅保留 `comfyui-lb`。

# 原 PROJECT_INIT（2026-07-12 归档；不要当现行路径抄）

> 当前：产品/仓名 **AIGCPannel**，路径 `/Users/wangzhenyu/Desktop/ALLProject/AIGCPannel`，origin https://gitee.com/Winery_z/AIGCPannel.git ，github https://github.com/zhwangsir/AIGCPannel 。canonical `./start-aigcpannel.sh`。`dashbox/` 是收尾引擎。以下为历史快照。

# AICG-DownLoader · 项目初始化文档（历史标题）

> 由项目管理中枢自动生成 | 更新日期: 2026-07-12 | 负责人: zhwangsir

## 一、项目基本信息

| 字段 | 值 |
|------|----|
| 项目名称 | AIGCPannel（dashbox/ 是收尾引擎，不再当独立产品） |
| 当前版本 | 0.1.0 |
| 创建日期 | 2026 年 |
| 负责人 | zhwangsir（Winery / WangZhenYu） |
| 项目路径 | /Users/wangzhenyu/Desktop/ALLProject/AIGCPannel |
| 远程仓库 | https://gitee.com/Winery_z/AIGCPannel （origin） / https://github.com/zhwangsir/AIGCPannel |
| 仓库可见性 | 公开；一等代码 MIT，dashbox/ 为 ELv2（见根 NOTICE） |
| 线上地址 | https://github.com/zhwangsir/AIGCPannel |

## 二、项目概述与核心功能

### 2.1 项目定位
纯 Rust 原生 GUI（egui）的跨平台桌面端应用，单文件可执行程序，支持 Windows / macOS / Linux。为本地 ComfyUI 用户设计：不碰命令行，把 Civitai / HuggingFace 上的模型下载到 ComfyUI 正确的子目录里。是与 ToIV 网页端工作台对应的桌面端形态。

### 2.2 核心功能列表
- **模型搜索**：Civitai（关键词 + 类型 + 底模 SDXL/Flux/Wan 过滤，预览图卡片，游标分页）+ HuggingFace（仓库关键词 → 权重文件列表，按大小排序标注目标目录）
- **链接解析**：粘贴 Civitai 模型页或 HuggingFace 文件页链接，自动识别类型/文件名/目标目录，多版本可选
- **作品页一键成套**：粘贴 Civitai 图片/视频/帖子链接，自动解析该作品用到的全部模型（抓 `__NEXT_DATA__` JSON），勾选后整套下载（精确版本 + SHA256）
- **预设套餐**：内置「Wan 2.2 图生视频全套（16G 显存适配）」「Flux.1 fp8 文生图」一键入队
- **工作流分析**：拖入 ComfyUI .json，列出引用的全部模型并标出本地缺失项（界面/API 双格式 + 子图），缺失项一键跳转搜索
- **模型库管理**：卡片式浏览 `models/*` 子目录，带预览缩略图、大小、磁盘占用；by-hash 识别（SHA256 → Civitai 反查）；支持 `extra_model_paths.yaml`
- **ComfyUI 实例联动**：填服务地址（默认 127.0.0.1:8188）测试连接；工作流分析用运行中的 ComfyUI 核对模型（区分本地已有/ComfyUI 已加载/真缺失）
- **下载队列**：断点续传（`.part` + HTTP Range，校验 206）、SHA256 自动校验（边下边算，失败删文件）、失败自动重试（指数退避 2/4/8s）、全局暂停/恢复、任务持久化、同名去重、单实例保护
- **系统信息检测**：GPU/VRAM/驱动/CPU/RAM/CUDA/Python/Git 版本/ComfyUI 安装位置（sys_info.rs，跨平台 `#[cfg]` 分离）
- **磁盘空间检查**：下载前校验磁盘可用空间
- **国内网络友好**：hf-mirror 镜像开关；自动读取系统代理（`HTTPS_PROXY`/`HTTP_PROXY`，适配 Clash）
- **中文界面 + 系统托盘**：启动自动加载系统 CJK 字体；托盘最小化 / 通知 / 单实例

### 2.3 目标用户
本地 ComfyUI 用户（开发者主力机 Windows 11 + RTX 5080 16GB，ComfyUI 装在 `D:\ComfyUI`）；不想用命令行下载模型的内容创作者。

## 三、技术架构

### 3.1 技术栈
- **语言**：Rust（edition 2021，stable ≥ 1.96）
- **GUI**：`eframe` + `egui` 0.29（原生即时模式 GUI，单文件可执行）+ `egui_extras` 0.29（http/file/image）
- **图片**：`image` 0.25（jpeg/png/webp）
- **网络**：`ureq` 2（`default-features=false, features=["tls","proxy-from-env"]`，rustls，**禁用 gzip** 保证断点续传 Content-Length 正确）
- **序列化**：`serde` + `serde_json`（Civitai/HF 响应用 `serde_json::Value` 宽松解析，抗 schema 变动）
- **正则**：`regex` 1（解析链接）
- **哈希**：`sha2` 0.10（SHA256 校验，SHA-NI 硬件加速）
- **文件对话框**：`rfd` 0.15
- **通知 / 托盘**：`notify-rust` 4、`tray-icon` 0.19
- **Windows 资源**：`winresource` 0.1（build.rs 把署名/版权写进 exe 版本资源）

### 3.2 架构说明
单文件源码（src/main.rs，约 3430 行，含测试模块），按区块组织：
- **配置区**：`Config`（serde）、`load_config`/`save_config`、`type_dir()`（模型类型→子目录）、`guess_type()`、`hf_base()`（镜像切换）
- **数据结构**：`SearchItem` / `VerInfo` / `Resolved`（含多版本）/ `Task`（`TaskRef = Arc<Mutex<Task>>`）
- **网络函数**：独立函数（`agent()`/`civitai_search()`/`resolve_url()`），供后台线程调用
- **下载引擎**：全局 `static ACTIVE: AtomicUsize` 并发闸门 + `static NEXT_ID`；`start_task()` 入队起线程；`download_file()` 流式下载 + Range 续传 + 分块进度
- **GUI**：`App` 持有 UI 状态 + `Sender/Receiver<Msg>`（mpsc）+ `downloads: Arc<Mutex<Vec<TaskRef>>>`；`App::update()` 每帧处理后台结果 → 顶栏标签 → 底部队列 → 中央按 `Tab` 分发

**并发模型**：UI 线程永不阻塞网络；搜索/解析/扫描各起一次性线程，结果经 mpsc 回传；下载各起常驻线程，靠 `ACTIVE` 原子量限并发，进度写 `Arc<Mutex<Task>>`，UI 每帧读锁渲染。

### 3.3 核心依赖
- eframe 0.29、egui_extras 0.29、image 0.25
- ureq 2（rustls，禁 gzip）、serde 1、serde_json 1、regex 1
- sha2 0.10、rfd 0.15、notify-rust 4、tray-icon 0.19
- winresource 0.1（仅 Windows build-dependencies）

## 四、目录结构

```
AICG-DownLoader-main/
├── src/
│   ├── main.rs            全部源码（约 3430 行，单文件，含测试模块）
│   └── sys_info.rs        系统信息检测（GPU/VRAM/CPU/RAM/CUDA/Python/ComfyUI 安装位置）
├── build.rs               编译期把署名/版权写进 Windows exe 版本资源
├── Cargo.toml             依赖清单（comfy-downloader 0.1.0，MIT）
├── Cargo.lock
├── config.json.example    配置示例（15 个字段）
├── packaging/
│   ├── windows/installer.iss          Inno Setup 安装器
│   ├── macos/Info.plist               macOS 应用元数据
│   └── linux/
│       ├── debian/control             deb 包元数据
│       ├── build-deb.sh               deb 打包脚本
│       └── comfy-downloader.desktop   桌面入口
├── .github/workflows/
│   ├── ci.yml             CI（三平台编译 + 测试）
│   └── release.yml        打 tag v* 触发三平台编译打包 + GitHub Release
├── dist/                  发布产物 + 研究文档（COMFYUI_GUIDE / LOCAL_MODELS_RESEARCH 等）
├── 开发提示词.md           项目上下文（粘贴给 AI 助手即可继续开发）
├── 开发热重载.bat          Windows 热重载脚本
├── 编译并运行.bat          Windows 一键编译启动
├── README.md / LICENSE
```

### 关键文件功能说明

| 路径 | 功能 |
|------|------|
| src/main.rs | 全部源码：配置/数据结构/网络/下载引擎/模型库/预设/主题/GUI/字体/入口 |
| src/sys_info.rs | 跨平台系统信息检测（`#[cfg(target_os)]` 分离），GPU/VRAM/ComfyUI 安装位置 |
| build.rs | Windows exe 版本资源注入（署名/版权，非 Windows 为空操作） |
| Cargo.toml | 依赖清单 + 包元数据（release profile: opt-level=2, strip=true） |
| config.json.example | 运行配置示例（15 字段） |
| packaging/windows/installer.iss | Inno Setup Windows 安装向导 |
| packaging/linux/build-deb.sh | Linux deb 打包脚本 |
| .github/workflows/release.yml | 打 tag 自动三平台构建 + 发布 Release |
| 开发提示词.md | 完整项目上下文（架构/已踩坑/路线图），粘贴给 AI 助手 |

## 五、环境搭建

### 5.1 前置环境要求
- Rust 工具链（stable ≥ 1.96，https://rustup.rs）
- Windows 另需 MSVC C++ Build Tools
- Linux 需系统 GUI 依赖（libgtk-3-dev 等，CI 已配置）
- 可选：ComfyUI 安装（用于路径检测 / 实例联动）、Civitai API 密钥（成人模型/高限额）

### 5.2 依赖安装步骤
```bash
# Rust 工具链安装后，依赖在首次 build 时自动拉取
cargo build --release
```
产物：`target/release/comfy-downloader(.exe)`

### 5.3 环境变量配置
本应用为桌面 GUI，配置走 `config.json`（非 .env）。程序启动按以下顺序查找：可执行文件同目录（便携）→ 系统配置目录（Windows `%APPDATA%\comfy-downloader\`，Linux `~/.config/comfy-downloader/`，macOS `~/Library/Application Support/comfy-downloader/`）。也可在「⚙ 设置」页修改。配置字段（config.json.example）：
- comfy_root
- civitai_token
- hf_mirror
- max_concurrent
- comfy_url
- proxy_url
- tray_minimize
- notify_on_complete
- comfy_args
- civitai_host
- show_previews
- torch_index
- pip_mirror
- download_root

> 注意：`civitai_token` 以明文存储在 config.json，请勿分享该文件。环境变量方面仅读取系统代理 `HTTPS_PROXY` / `HTTP_PROXY`（适配 Clash）。

## 六、启动与运行

### 6.1 开发模式启动
```bash
cargo build --release        # 编译
cargo run --release          # 编译并运行
```
Windows 一键：运行 `编译并运行.bat`。

### 6.2 生产构建
```bash
cargo build --release
```
产物 `target/release/comfy-downloader(.exe)`，双击运行。release profile：opt-level=2，strip=true。

### 6.3 部署方式
**发版**：打 tag（`v` 开头，如 `v0.1.0`）触发 `.github/workflows/release.yml`，三平台（ubuntu/windows/macos）各自编译、测试、打包出可分发产物（zip/tar.gz/dmg/AppImage）+ SHA256SUMS，汇总发布到 GitHub Release。
- **Windows**：`…-windows-setup.exe`（Inno Setup 向导）或 `…-windows-x86_64.zip`（便携）
- **Linux**：`…-linux-amd64.deb`（`sudo dpkg -i`）或 `…-linux-x86_64.AppImage`
- **macOS**：`…-macos-arm64.dmg` / `.zip`（拖入应用程序；首次 Gatekeeper 拦截右键「打开」，或 `xattr -dr com.apple.quarantine` 解除隔离）
- 每个安装包旁附 `.sha256` 校验文件

## 七、主要接口说明
本应用为桌面 GUI，无自建 HTTP 服务接口。主要与外部 API 交互：
- **Civitai 搜索**：`GET https://civitai.com/api/v1/models?query=&types=&limit=24&nsfw=true`（带 `Authorization: Bearer <token>`，可走 civitai.red 镜像）
- **Civitai 下载**：`GET https://civitai.com/api/download/models/{versionId}`（带 token，文件名从 `Content-Disposition` 兜底）
- **Civitai 作品页资源解析**：抓作品页 HTML 内 `__NEXT_DATA__` JSON（服务端渲染，无 Cloudflare 拦截），提取 `resources[]` 的 modelId/modelVersionId/modelName/modelType/baseModel
- **HuggingFace**：`{base}/{repo}/resolve/{branch}/{path}`，`base` 在 `huggingface.co` 与 `hf-mirror.com`（国内）间切换；tree API 查 `lfs.oid` 获取 SHA256
- **ComfyUI 实例联动**：`GET {comfy_url}/system_stats`（连接测试/版本）、`GET {comfy_url}/object_info`（收集模型扩展名作实例可见集）

## 八、已知问题与注意事项
- **跨平台约束**：必须用 `#[cfg(target_os)]` 分离平台特定代码；MPS 用 default，CUDA 用 fp8_e4m3fn
- Linux/macOS 实机验证仍在完善中；打包已由发布工作流自动产出
- HF tree API 单页 1000 项，超大目录文件可能查不到哈希（此时跳过校验，不影响下载）
- ureq 禁用 gzip：保证 Content-Length 与断点续传正确（关键约束，勿开启 gzip）
- `civitai_token` 明文存储在 config.json，勿分享该文件
- 创作者隐藏生成信息的 Civitai 作品无法解析模型
- 预设套餐模型：flux1-dev-fp8 / wan2.2_i2v_14B / wan_2.1_vae / ae / umt5_xxl / t5xxl / clip_l

## 九、与其他项目的关系
- **与 ToIV**：ToIV 是网页端工作台（图像/视频/语音/3D 生成）；AICG-DownLoader 是桌面端模型下载器，为本地 ComfyUI 用户下载模型到正确目录。两者共享 ComfyUI 生态：ToIV 网页侧做生成与模型管理，AICG 桌面侧做模型获取与库管理。
- **与 BIM**：BIM 的渲染底模（RealVisXL / Union ControlNet）可通过 AICG-DownLoader 下载到 ComfyUI 目录。
- **与 flipped**：flipped 是 AI 自动化开发工厂，可作为 AICG-DownLoader 这类 Rust 项目的自主开发工具链（给方向 → 自行拆解写码跑测修错循环）。


## 已归档文档索引

- `2026-07-14-service-selection.md` — ToIV 服务选型与部署拓扑
- `Gitee上传方法.md` — Gitee 上传方法（全项目统一）
- `PROJECT_INIT.md` — AICG-DownLoader · 项目初始化文档
- `开发提示词.md` — ComfyUI 模型下载器 — 开发提示词 / 项目上下文
- `设备说明.md` — 集群设备说明（单一真相源）

## 原 docs/ 目录

## 已归档文档索引

- `USER_GUIDE.md` — AI 短剧工作台 — 用户手册
- `USER_CLICK_TEST_REPORT.md` — 用户点击测试报告 — AI 短剧生成平台
- `LIBLIB_BENCHMARK_PROPOSAL.md` — Liblib（liblib.tv / LibTV）对标优化方案 —— AI 短剧一条龙工作台
- `LIBLIB_TASK_CARDS.md` — Liblib 对标前三高价值功能 —— 开发任务卡片（M24 候选）
- `DRAMACLAW_RESEARCH.md` — DramaClaw 深度调研报告
- `MCP_COMPATIBILITY_TROUBLESHOOTING.md` — MCP / GLM-5.2 兼容性排查清单
- `DEPLOYMENT.md` — AI 短剧工作台 — 部署文档
- `LIBTV_DEEP_RESEARCH.md` — LibTV 系统性深度调研报告
- `LIBTV_OPTIMIZATION_PLAN.md` — LibTV 对标优化方案与进度跟踪（M25）
- `TEST_REPORT_M24.md` — AICG-DownLoader M24 全面系统测试报告
- `M25_9_SKETCH_FIRST_EVALUATION.md` — M25.9 线稿先行两段式分镜 — DramaClaw 整合评估报告
- `TECHNICAL_DESIGN.md` — AI 短剧工作台 — 技术说明
