# AIGCPannel

融合项目：ComfyUI 模型下载器 + AI 短剧工作台（`platform/`）+ DashBox 引擎（`dashbox/`）。
产品显示名 **AIGCPannel**。远程仓仍是 `AICG-DownLoader`（Gitee/GitHub 先别改）。

- 工作台：`./start-aigcpannel.sh`（backend `:8100` + frontend `:3501`）
- 引擎：`./start-engine.sh`（DashBox docker，默认 Web `:8080` / API `:8780`；加 `--up` 才拉起）
- 状态：`GET /api/panel/status`（`config` / `models.json` 是否可读）

根 [`NOTICE`](NOTICE)：`dashbox/` 为 Elastic License 2.0，不要改品牌文件。crate / 配置目录名仍是 `comfy-downloader`。
集群真相：[`../ToIV/AGENTS.md`](../ToIV/AGENTS.md)。

---

# AICG-DownLoader · ComfyUI 模型下载器

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Releases](https://img.shields.io/badge/Download-Releases-brightgreen.svg)](https://github.com/zhwangsir/AICG-DownLoader/releases)

纯 Rust 原生 GUI（egui），单文件可执行程序，支持 Windows / macOS / Linux。
为本地 ComfyUI 用户设计：不碰命令行，把 Civitai / HuggingFace 上的模型下载到 ComfyUI 正确的子目录里。

> 下载安装包请到 [GitHub Releases](https://github.com/zhwangsir/AICG-DownLoader/releases) 页。

## 姊妹项目：AI 短剧工作台（platform/）

本仓库除下载器外，还包含 `platform/` 目录下的 **AI 短剧一条龙工作台**——一个从「一句话创意」到「可播放短剧成片」的内容生产平台（Python FastAPI 后端 + React/TS 前端，LLM/图像/视频/TTS/ASR 全链路 Agent 管线）。

两者关系：下载器负责把 Civitai / HuggingFace 模型可靠地下载到 ComfyUI 模型目录；工作台负责用这些模型生产内容。两侧通过 **模型注册表**（工作台 `GET /api/drama/models/registry`，融合下载器 `models.json` 与工作台 `lora_manifest`）打通——下载器下载的模型可直接被工作台发现与引用，工作台后端也会共享下载器的 `config.json` 配置。

工作台的架构、启动方式见 [platform/README.md](platform/README.md)，部署见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 功能

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

## 安装

到 [GitHub Releases](https://github.com/zhwangsir/AICG-DownLoader/releases) 下载对应平台的安装包：

- **Windows**：下载 `comfy-downloader-<版本>-windows-setup.exe`，双击运行向导即可（默认安装到当前用户目录，无需管理员权限），自动建开始菜单/桌面快捷方式。也可下载 `…-windows-x86_64.zip` 解压后直接运行 `comfy-downloader.exe`（便携模式）。
- **Linux**：下载 `…-linux-amd64.deb` 后执行 `sudo dpkg -i comfy-downloader-*.deb`（缺依赖时再 `sudo apt-get -f install`），随后从应用菜单或终端 `comfy-downloader` 启动。或下载 `…-linux-x86_64.AppImage`，`chmod +x *.AppImage` 后直接运行。
- **macOS**：下载 `…-macos-arm64.dmg`（或 `.zip`），把 `ComfyUI Downloader.app` 拖入「应用程序」。首次打开若被 Gatekeeper 拦（未签名），右键点 App 选「打开」确认一次即可；仍被拦时可在终端执行 `xattr -dr com.apple.quarantine "/Applications/ComfyUI Downloader.app"` 解除隔离属性。

每个安装包旁都附带同名 `.sha256` 校验文件，可下载后核对完整性。

## 编译

需要 Rust 工具链（https://rustup.rs ，Windows 另需 MSVC C++ Build Tools）：

```bash
cargo build --release
```

产物：`target/release/comfy-downloader(.exe)`，双击运行。

## 配置

程序启动时按以下顺序找 `config.json`：可执行文件同目录（便携模式）→ 系统配置目录（Windows `%APPDATA%\comfy-downloader\`，Linux `~/.config/comfy-downloader/`，macOS `~/Library/Application Support/comfy-downloader/`）。也可直接在「⚙ 设置」页修改（含原生目录选择器）：

```json
{
  "comfy_root": "D:\\ComfyUI",
  "civitai_token": "你的 Civitai API 密钥（civitai.com/user/account 生成）",
  "hf_mirror": true,
  "max_concurrent": 2
}
```

> 注意：`civitai_token` 以明文存储在 config.json，请勿把该文件分享给他人。

## 测试

```bash
cargo test --release                # 单元测试
cargo test --release -- --ignored   # 真实网络 e2e（下载/断点续传/SHA256/搜索分页）
```

## 技术栈

`eframe`/`egui` 0.29（GUI）· `ureq` 2（rustls，禁用 gzip 保证续传正确）· `sha2`（校验）· `rfd`（原生文件对话框）· `serde_json`（宽松解析，抗 API schema 变动）· `regex`

更多开发上下文（架构、已踩的坑、路线图）见 [开发提示词.md](开发提示词.md)。

## 已知限制

- Linux/macOS 的实机验证仍在完善中；打包（.deb / AppImage / .app / dmg）已由发布工作流自动产出，理论上 `cargo build --release` 即可用
- HF tree API 单页 1000 项，超大目录的文件可能查不到哈希（此时跳过校验，不影响下载）

## 作者 / License

- 作者：**Winery (WangZhenYu)**
- 项目主页：<https://github.com/zhwangsir/AICG-DownLoader>
- 许可证：**MIT License**（详见 [LICENSE](LICENSE)），Copyright © 2026 WangZhenYu (Winery)

本项目以 MIT 协议开源，可自由使用、修改与再分发。请在二次分发时保留应用内「关于」页的署名与本仓库的版权声明（MIT 要求保留版权与许可声明）。
