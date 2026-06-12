# AICG-DownLoader · ComfyUI 模型下载器

纯 Rust 原生 GUI（egui），单文件可执行程序，支持 Windows / macOS / Linux。
为本地 ComfyUI 用户设计：不碰命令行，把 Civitai / HuggingFace 上的模型下载到 ComfyUI 正确的子目录里。

## 功能

- **🔍 Civitai 搜索**：关键词 + 类型 + 底模（SDXL/Flux/Wan…）过滤，预览图卡片，游标分页「加载更多」
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

- Linux/macOS 的实机验证与打包（AppImage / .app）待完成；理论上 `cargo build --release` 即可用
- HF tree API 单页 1000 项，超大目录的文件可能查不到哈希（此时跳过校验，不影响下载）
