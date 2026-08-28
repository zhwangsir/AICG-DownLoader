#![cfg_attr(all(windows, not(debug_assertions), not(test)), windows_subsystem = "windows")]
//! ComfyUI 模型下载器 — egui 原生跨平台版
//! 功能: Civitai 搜索 / 链接解析 / 队列下载(断点续传) / 模型库扫描 / 设置
use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use eframe::egui;

mod sys_info;

// ============ 品牌 / 署名 ============
// 作者 Winery（真名 WangZhenYu）。这些常量是全应用署名的唯一来源，
// 用于窗口标题、「关于」页与（经 Cargo/winresource）二进制元数据。
// 授权 MIT：可用可改，但必须保留版权与许可声明（详见仓库 LICENSE）。
const APP_NAME: &str = "AIGCPannel 模型库";
const APP_AUTHOR: &str = "Winery (WangZhenYu)";
const APP_COPYRIGHT: &str = "© 2026 WangZhenYu";
const APP_HOMEPAGE: &str = "https://github.com/zhwangsir/AIGCPannel";
const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
const WINDOW_TITLE: &str = "AIGCPannel 模型库 · by Winery";

static ACTIVE: AtomicUsize = AtomicUsize::new(0);
static NEXT_ID: AtomicU64 = AtomicU64::new(1);
// 全局暂停：置位后排队任务不取并发槽、下载中任务在下一个分块边界退出为「已暂停」
static PAUSED: AtomicBool = AtomicBool::new(false);
// 托盘「恢复」请求：托盘命令处理器是无 App 访问的自由函数，无法直接重新入队已暂停任务，
// 故只置此标志，由 App::update 每帧消费、调用 resume_all 完成恢复（与顶栏按钮同一逻辑）。
static RESUME_REQUESTED: AtomicBool = AtomicBool::new(false);

// 托盘窗口控制命令（跨平台）：托盘菜单点击后只置此标志，由 App::update 消费用 ViewportCommand 操作窗口。
// 0=None, 1=Show, 2=Hide, 3=Toggle, 4=Exit
static PENDING_TRAY_CMD: AtomicU8 = AtomicU8::new(0);
// 窗口当前可见性（每帧由 App::update 更新），供 Toggle 判断方向
static WINDOW_VISIBLE: AtomicBool = AtomicBool::new(true);

// ============ 主题 ============
const C_BG: egui::Color32 = egui::Color32::from_rgb(16, 16, 20); // 窗口底色
const C_PANEL: egui::Color32 = egui::Color32::from_rgb(23, 23, 29); // 顶/底栏
const C_CARD: egui::Color32 = egui::Color32::from_rgb(30, 30, 38); // 卡片
const C_ACCENT: egui::Color32 = egui::Color32::from_rgb(96, 145, 240); // 主题蓝
const C_GREEN: egui::Color32 = egui::Color32::from_rgb(95, 212, 155);
const C_RED: egui::Color32 = egui::Color32::from_rgb(240, 128, 128);
const C_YELLOW: egui::Color32 = egui::Color32::from_rgb(224, 176, 80);
const C_GRAY: egui::Color32 = egui::Color32::from_rgb(154, 154, 166);

fn setup_style(ctx: &egui::Context) {
    use egui::{FontFamily, FontId, TextStyle};
    let mut style = (*ctx.style()).clone();
    style.text_styles = [
        (TextStyle::Heading, FontId::new(20.0, FontFamily::Proportional)),
        (TextStyle::Body, FontId::new(14.5, FontFamily::Proportional)),
        (TextStyle::Button, FontId::new(14.5, FontFamily::Proportional)),
        (TextStyle::Small, FontId::new(12.0, FontFamily::Proportional)),
        (TextStyle::Monospace, FontId::new(13.0, FontFamily::Monospace)),
    ]
    .into();
    style.spacing.item_spacing = egui::vec2(8.0, 8.0);
    style.spacing.button_padding = egui::vec2(14.0, 6.0);
    style.spacing.interact_size.y = 30.0;
    let mut v = egui::Visuals::dark();
    v.window_fill = C_BG;
    v.panel_fill = C_BG;
    v.extreme_bg_color = egui::Color32::from_rgb(12, 12, 15); // 输入框/进度条底
    v.faint_bg_color = egui::Color32::from_rgb(34, 34, 42);
    v.selection.bg_fill = C_ACCENT; // 选中项与进度条填充
    v.selection.stroke = egui::Stroke::new(1.0, egui::Color32::WHITE);
    v.hyperlink_color = C_ACCENT;
    v.window_rounding = egui::Rounding::same(10.0);
    for w in [
        &mut v.widgets.noninteractive,
        &mut v.widgets.inactive,
        &mut v.widgets.hovered,
        &mut v.widgets.active,
        &mut v.widgets.open,
    ] {
        w.rounding = egui::Rounding::same(7.0);
    }
    v.widgets.noninteractive.fg_stroke.color = egui::Color32::from_rgb(216, 216, 226);
    v.widgets.noninteractive.bg_stroke.color = egui::Color32::from_rgb(42, 42, 52); // 分隔线
    v.widgets.inactive.weak_bg_fill = egui::Color32::from_rgb(38, 38, 50); // 按钮底
    v.widgets.inactive.fg_stroke.color = egui::Color32::from_rgb(214, 214, 224);
    v.widgets.hovered.weak_bg_fill = egui::Color32::from_rgb(50, 50, 66);
    v.widgets.hovered.bg_stroke = egui::Stroke::new(1.0, egui::Color32::from_rgb(72, 96, 150));
    v.widgets.active.weak_bg_fill = egui::Color32::from_rgb(58, 58, 80);
    // 弹窗/下拉柔和投影，增强层次
    let soft = egui::epaint::Shadow {
        offset: egui::vec2(0.0, 6.0),
        blur: 24.0,
        spread: 0.0,
        color: egui::Color32::from_black_alpha(120),
    };
    v.window_shadow = soft;
    v.popup_shadow = egui::epaint::Shadow {
        offset: egui::vec2(0.0, 4.0),
        blur: 16.0,
        spread: 0.0,
        color: egui::Color32::from_black_alpha(110),
    };
    style.visuals = v;
    ctx.set_style(style);
}

// 卡片描边（极淡的白，给纯色卡片一道边缘高光，与背景分离）
const C_CARD_STROKE: egui::Color32 = egui::Color32::from_rgb(48, 48, 60);

// 统一的内容卡片：柔和投影 + 1px 描边 + 圆角 + 内边距。主面板/结果卡用，建立层次。
fn card() -> egui::Frame {
    egui::Frame::none()
        .fill(C_CARD)
        .stroke(egui::Stroke::new(1.0, C_CARD_STROKE))
        .rounding(egui::Rounding::same(12.0))
        .inner_margin(egui::Margin::same(12.0))
        .shadow(egui::epaint::Shadow {
            offset: egui::vec2(0.0, 3.0),
            blur: 12.0,
            spread: 0.0,
            color: egui::Color32::from_black_alpha(70),
        })
}

// 轻量卡片：仅描边 + 圆角，无投影。用于重复的列表行/指标小卡，避免阴影堆叠显脏。
fn soft_card() -> egui::Frame {
    egui::Frame::none()
        .fill(C_CARD)
        .stroke(egui::Stroke::new(1.0, C_CARD_STROKE))
        .rounding(egui::Rounding::same(10.0))
        .inner_margin(egui::Margin::same(10.0))
}

// 胶囊形彩色标签（类型/状态徽章）
fn chip(ui: &mut egui::Ui, text: &str, bg: egui::Color32, fg: egui::Color32) {
    egui::Frame::none()
        .fill(bg)
        .rounding(egui::Rounding::same(999.0)) // 大圆角 → 渲染时按半高裁剪成胶囊
        .inner_margin(egui::Margin::symmetric(9.0, 3.0))
        .show(ui, |ui| {
            ui.label(egui::RichText::new(text).size(11.5).color(fg));
        });
}

// 可点击复制的胶囊（用于触发词）：外观同 chip，但整块可点击，返回 Response 交上层处理。
fn click_chip(ui: &mut egui::Ui, text: &str, bg: egui::Color32, fg: egui::Color32) -> egui::Response {
    let inner = egui::Frame::none()
        .fill(bg)
        .rounding(egui::Rounding::same(999.0))
        .inner_margin(egui::Margin::symmetric(9.0, 3.0))
        .show(ui, |ui| {
            ui.label(egui::RichText::new(text).size(11.5).color(fg));
        });
    let resp = inner.response.interact(egui::Sense::click());
    if resp.hovered() {
        ui.ctx().output_mut(|o| o.cursor_icon = egui::CursorIcon::PointingHand);
    }
    resp.on_hover_text("点击复制")
}

// 主操作按钮：强调色填充。每页一个，建立「蓝色 = 本页主操作」的视觉规则
fn accent_btn(text: &str) -> egui::Button<'static> {
    egui::Button::new(egui::RichText::new(text.to_owned()).color(egui::Color32::WHITE)).fill(C_ACCENT)
}

fn status_chip(ui: &mut egui::Ui, status: &str) {
    let (bg, fg) = if status.starts_with("重试等待") {
        (egui::Color32::from_rgb(66, 54, 26), egui::Color32::from_rgb(230, 190, 100))
    } else {
        match status {
            "下载中" => (egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248)),
            "完成" | "已存在" => (egui::Color32::from_rgb(26, 56, 40), C_GREEN),
            "失败" => (egui::Color32::from_rgb(66, 34, 38), C_RED),
            "已暂停" => (egui::Color32::from_rgb(66, 54, 26), egui::Color32::from_rgb(230, 190, 100)),
            _ => (egui::Color32::from_rgb(42, 42, 52), C_GRAY), // 排队中/已取消
        }
    };
    chip(ui, status, bg, fg);
}

// ============ 配置 ============
/// 统一的网络预览图渲染：在 w×h 固定框内等比居中显示图片。
/// - `show=false`：显示「预览已关闭」占位，不发起任何网络请求
/// - `uri` 为空：显示「无预览」占位
/// - 加载中：spinner 占位
/// - 加载失败：可点击的「点击重试」占位（点击 `forget_image` 触发重新加载），
///   而非 egui 默认那个红色 ⚠——在需要代理却连不上 image-b2.civitai.com 时尤其常见。
fn preview_img(ui: &mut egui::Ui, uri: &str, w: f32, h: f32, rounding: f32, show: bool) {
    let r = egui::Rounding::same(rounding);
    let bg = egui::Color32::from_rgb(38, 38, 50);
    let box_size = egui::vec2(w, h);
    let icon_size = (w.min(h) * 0.22).clamp(16.0, 36.0);
    // 居中图标+文字的占位框
    let placeholder = |ui: &mut egui::Ui, icon: &str, text: &str, icon_color: egui::Color32| -> egui::Response {
        let (rect, resp) = ui.allocate_exact_size(box_size, egui::Sense::click());
        ui.painter().rect_filled(rect, r, bg);
        let p = ui.painter();
        if !icon.is_empty() {
            p.text(rect.center() - egui::vec2(0.0, 11.0), egui::Align2::CENTER_CENTER, icon, egui::FontId::proportional(icon_size), icon_color);
        }
        p.text(rect.center() + egui::vec2(0.0, 14.0), egui::Align2::CENTER_CENTER, text, egui::FontId::proportional(11.0), C_GRAY);
        resp
    };
    if uri.is_empty() {
        placeholder(ui, "🖼", "无预览", C_GRAY);
        return;
    }
    if !show {
        placeholder(ui, "🚫", "预览已关闭", C_GRAY);
        return;
    }
    let probe = egui::Image::from_uri(uri.to_string());
    match probe.load_for_size(ui.ctx(), box_size) {
        Ok(egui::load::TexturePoll::Ready { texture }) => {
            let (rect, _) = ui.allocate_exact_size(box_size, egui::Sense::hover());
            let ts = texture.size;
            let draw = if ts.x > 0.0 && ts.y > 0.0 {
                let scale = (rect.width() / ts.x).min(rect.height() / ts.y);
                egui::Rect::from_center_size(rect.center(), egui::vec2(ts.x * scale, ts.y * scale))
            } else {
                rect
            };
            egui::Image::from_uri(uri.to_string()).rounding(r).paint_at(ui, draw);
        }
        Ok(egui::load::TexturePoll::Pending { .. }) => {
            let (rect, _) = ui.allocate_exact_size(box_size, egui::Sense::hover());
            ui.painter().rect_filled(rect, r, bg);
            egui::Spinner::new().paint_at(ui, rect);
        }
        Err(_) => {
            let resp = placeholder(ui, "⚠", "点击重试", C_YELLOW);
            if resp.clicked() {
                ui.ctx().forget_image(uri);
            }
            if resp.hovered() {
                ui.ctx().output_mut(|o| o.cursor_icon = egui::CursorIcon::PointingHand);
            }
        }
    }
}

/// Justified Rows 画廊分行算法（Flickr/Google 相册式）。
/// 输入各图宽高比 `aspects`（宽/高）、列宽 `avail_w`、间距 `gap`、目标行高 `target_h`。
/// 返回每行 `(起始下标, 张数, 行高)`：每个铺满的行(非末行)宽度精确等于 `avail_w`，
/// 末行未铺满则保持 `target_h` 不拉伸。纯函数，便于单测，与 GUI 无关。
fn justify_rows(aspects: &[f32], avail_w: f32, gap: f32, target_h: f32) -> Vec<(usize, usize, f32)> {
    let mut rows = Vec::new();
    let mut i = 0;
    while i < aspects.len() {
        // 贪心攒一行：累加宽高比，直到「按目标行高排布的总宽」≥ 列宽
        let mut end = i;
        let mut sum_aspect = 0.0;
        loop {
            sum_aspect += aspects[end].max(0.01);
            end += 1;
            let row_w = target_h * sum_aspect + gap * (end - i - 1) as f32;
            if end >= aspects.len() || row_w >= avail_w {
                break;
            }
        }
        let n = (end - i) as f32;
        // 反推行高让本行精确等于列宽；末行未铺满则保持目标高度
        let filled = target_h * sum_aspect + gap * (n - 1.0) >= avail_w;
        let row_h = if filled {
            ((avail_w - gap * (n - 1.0)) / sum_aspect).clamp(110.0, 300.0)
        } else {
            target_h
        };
        rows.push((i, end - i, row_h));
        i = end;
    }
    rows
}

fn default_comfy_url() -> String {
    "http://127.0.0.1:8188".into()
}
fn default_civitai_host() -> String {
    "civitai.red".into()
}
fn default_torch_index() -> String {
    "cu130".into()
}
fn default_true() -> bool {
    true
}

#[derive(Serialize, Deserialize, Clone)]
struct Config {
    comfy_root: String,
    civitai_token: String,
    hf_mirror: bool,
    max_concurrent: usize,
    // serde(default)：老 config.json 没有此字段也能正常反序列化，不会回退默认丢失 token
    #[serde(default = "default_comfy_url")]
    comfy_url: String,
    // 显式代理地址，优先于系统代理环境变量；例：http://127.0.0.1:7890
    #[serde(default)]
    proxy_url: Option<String>,
    // 关闭窗口时最小化到系统托盘（而非退出），后台继续下载
    #[serde(default = "default_true")]
    tray_minimize: bool,
    // 下载完成/失败时弹出系统通知
    #[serde(default = "default_true")]
    notify_on_complete: bool,
    // ComfyUI 启动附加参数，如 --lowvram --listen
    #[serde(default)]
    comfy_args: String,
    // Civitai API 域名（com / red / work），用于应对不同网络环境下域名被重置的情况
    #[serde(default = "default_civitai_host")]
    civitai_host: String,
    // 是否加载网络预览图（搜索卡片/详情页/解析弹窗/模型库缩略图）。
    // 部分网络环境下 image-b2.civitai.com 的 TLS 连不上，关掉可避免满屏加载失败。
    #[serde(default = "default_true")]
    show_previews: bool,
    // 安装 ComfyUI 时的 PyTorch 源后缀（cu130/cu128/cu124/cu121/cpu），随显卡 CUDA 选择
    #[serde(default = "default_torch_index")]
    torch_index: String,
    // 安装时 pip 走国内镜像（清华源）加速依赖下载
    #[serde(default)]
    pip_mirror: bool,
    // 下载模型的目标模型根目录（.../models）；空 = 自动（Desktop 用其主目录，否则 comfy_root\models）
    #[serde(default)]
    download_root: String,
    // 大文件多连接分块下载加速（仅对支持 Range 的非 Civitai 源）。出问题可关闭回落单连接。
    #[serde(default = "default_true")]
    multipart: bool,
}
impl Default for Config {
    fn default() -> Self {
        Config {
            comfy_root: if cfg!(windows) { "D:\\ComfyUI".into() } else { "~/ComfyUI".into() },
            civitai_token: String::new(),
            hf_mirror: true,
            max_concurrent: 2,
            comfy_url: default_comfy_url(),
            proxy_url: None,
            tray_minimize: true,
            notify_on_complete: true,
            comfy_args: String::new(),
            civitai_host: default_civitai_host(),
            show_previews: true,
            torch_index: default_torch_index(),
            pip_mirror: false,
            download_root: String::new(),
            multipart: true,
        }
    }
}
impl Config {
    /// 返回 Civitai API 根地址（含 https:// 和 /api，不含末尾斜杠）
    fn civitai_api_base(&self) -> String {
        let host = self.civitai_host.trim();
        let host = host.strip_prefix("https://").unwrap_or(host);
        let host = host.strip_suffix('/').unwrap_or(host);
        if host.is_empty() {
            return "https://civitai.red/api".into();
        }
        format!("https://{}/api", host)
    }
}
fn config_path() -> PathBuf {
    // 便携模式优先：exe 同目录已有 config.json 就继续读写它（绿色软件用法不变）
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join("config.json");
            if p.exists() {
                return p;
            }
        }
    }
    // 否则用系统配置目录，避免 cargo clean 删掉 token、安装到只读目录时无法保存
    let base = if cfg!(windows) {
        std::env::var_os("APPDATA").map(PathBuf::from)
    } else if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library/Application Support"))
    } else {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
    };
    match base {
        Some(b) => b.join("comfy-downloader").join("config.json"),
        None => PathBuf::from("config.json"),
    }
}
fn load_config() -> Config {
    if let Ok(s) = fs::read_to_string(config_path()) {
        // 容忍 UTF-8 BOM：用户用记事本等编辑器手改 config.json 时常见
        if let Ok(c) = serde_json::from_str::<Config>(s.trim_start_matches('\u{feff}')) {
            return c;
        }
    }
    Config::default()
}
fn save_config(c: &Config) -> Result<(), String> {
    let s = serde_json::to_string_pretty(c).map_err(|e| e.to_string())?;
    let p = config_path();
    if let Some(dir) = p.parent() {
        let _ = fs::create_dir_all(dir);
    }
    fs::write(&p, s).map_err(|e| e.to_string())
}

fn type_dir(t: &str) -> &'static str {
    match t {
        "Checkpoint" => "models/checkpoints",
        "LORA" | "LoCon" | "DoRA" => "models/loras",
        "TextualInversion" => "models/embeddings",
        "VAE" => "models/vae",
        "Controlnet" => "models/controlnet",
        "Upscaler" => "models/upscale_models",
        "Unet" => "models/unet",
        "TextEncoder" => "models/text_encoders",
        _ => "models/checkpoints",
    }
}
fn guess_type(name: &str) -> &'static str {
    let s = name.to_lowercase();
    if s.contains(".gguf") || s.contains("/unet") || s.contains("diffusion_model") {
        "Unet"
    } else if s.contains("vae") {
        "VAE"
    } else if s.contains("text_encoder") || s.contains("umt5") || s.contains("clip") || s.contains("t5xxl") {
        "TextEncoder"
    } else if s.contains("lora") {
        "LORA"
    } else if s.contains("controlnet") {
        "Controlnet"
    } else {
        "Checkpoint"
    }
}
fn hf_base(c: &Config) -> &'static str {
    if c.hf_mirror { "https://hf-mirror.com" } else { "https://huggingface.co" }
}

// 把 HF 下载链接按当前镜像开关切换 host：分析工作流时固化的 URL，下载时仍按最新设置走
fn apply_mirror(url: &str, c: &Config) -> String {
    if c.hf_mirror {
        url.replace("https://huggingface.co/", "https://hf-mirror.com/")
    } else {
        url.replace("https://hf-mirror.com/", "https://huggingface.co/")
    }
}

// 非 Windows 默认根目录是 "~/ComfyUI"，PathBuf 不展开 ~，必须手动替换为家目录
fn expand_root(root: &str) -> PathBuf {
    if let Some(rest) = root.strip_prefix("~/").or_else(|| root.strip_prefix("~\\")) {
        if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
            return PathBuf::from(home).join(rest);
        }
    }
    PathBuf::from(root)
}

// 文件名来自远端 API/URL/用户输入，去掉 Windows 非法字符与路径分隔符，防穿越与落盘失败
fn sanitize_filename(name: &str) -> String {
    let mut out = String::new();
    for c in name.chars() {
        match c {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => out.push('_'),
            c if (c as u32) < 0x20 => {}
            c => out.push(c),
        }
    }
    let out = out.trim().trim_matches('.').trim().to_string();
    if out.is_empty() { "unnamed".into() } else { out }
}

// Civitai 模型描述是 HTML，剥成纯文本并截断，用于下载前的简介展示
fn html_to_text(html: &str, max: usize) -> String {
    let mut out = String::new();
    let mut in_tag = false;
    for c in html.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(c),
            _ => {}
        }
    }
    let out = out
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'");
    let collapsed: String = out.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.chars().count() > max {
        let s: String = collapsed.chars().take(max).collect();
        format!("{}…", s)
    } else {
        collapsed
    }
}

// HF URL 末段可能带 %20 等百分号编码，按字节解码后再做 UTF-8 还原
fn percent_decode(s: &str) -> String {
    fn hex(c: u8) -> Option<u8> {
        match c {
            b'0'..=b'9' => Some(c - b'0'),
            b'a'..=b'f' => Some(c - b'a' + 10),
            b'A'..=b'F' => Some(c - b'A' + 10),
            _ => None,
        }
    }
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(h), Some(l)) = (hex(b[i + 1]), hex(b[i + 2])) {
                out.push(h * 16 + l);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

// 鉴权失败和哈希不匹配是系统性错误，自动重试只会浪费流量；网络类瞬时错误才值得重试
fn should_retry(e: &str) -> bool {
    !(e.contains("status code 401")
        || e.contains("status code 403")
        || e.contains("status code 404")
        || e.contains("SHA256 校验失败")
        || e.contains("鉴权失败"))
}

// 把 ureq 的英文报错翻译成用户可操作的提示
fn friendly_err(e: String) -> String {
    if e.contains("status code 401") || e.contains("status code 403") {
        format!("鉴权失败，请在「设置」中配置有效的 Civitai 密钥（{}）", e)
    } else if e.contains("status code 429") {
        format!("请求过于频繁被限流，请稍后再试（{}）", e)
    } else if e.contains("timed out") || e.contains("Dns") || e.contains("Connection") || e.contains("Network") || e.contains("io error") {
        format!("网络连接失败，请检查网络或代理（{}）", e)
    } else {
        e
    }
}

// ============ 数据结构 ============
#[derive(Clone)]
struct SearchItem {
    id: i64,
    name: String,
    kind: String,
    nsfw: bool,
    base: String,
    version_id: i64,
    image: String,
    downloads: i64,
}

// 搜索源：Civitai（图片卡片）/ HuggingFace（仓库→文件）
#[derive(PartialEq, Clone, Copy)]
enum SearchSource {
    Civitai,
    HuggingFace,
}

// HuggingFace 搜索结果里的一个模型仓库
#[derive(Clone)]
struct HfRepo {
    id: String,           // "org/name"
    downloads: i64,
    likes: i64,
    pipeline_tag: String, // 如 text-to-image / text-to-video，可能为空
}

// HF 仓库里的一个可下载模型文件
#[derive(Clone)]
struct HfFile {
    path: String, // 仓库内相对路径，可能含子目录，如 "split_files/vae/foo.safetensors"
    size: u64,    // 字节（LFS 大文件取 lfs.size）
}

// 当前打开的「HF 仓库文件」弹窗状态
#[derive(Clone)]
struct HfFilesState {
    repo: String,
    files: Vec<HfFile>,
    loading: bool,
    err: String,
}
#[derive(Clone)]
struct VerInfo {
    id: i64,
    name: String,
    base: String,
    filename: String,
    size_kb: f64,
    sha256: String,
    trained_words: Vec<String>, // LoRA/嵌入触发词（Civitai trainedWords，多为 LoRA 才有）
    published_at: String,       // 发布日期 YYYY-MM-DD（publishedAt 缺则退 createdAt）
}
// 画廊里的一张预览图：除 URL 外带上 Civitai 返回的原始宽高，
// 用于在图片像素下载完成前就按真实宽高比做自适应布局（Justified Rows）。
#[derive(Clone)]
struct GalleryImg {
    url: String,
    w: f32,
    h: f32,
}
impl GalleryImg {
    // 宽高比（宽/高）；缺尺寸或异常时回退 1:1，避免除零或布局塌陷。
    fn aspect(&self) -> f32 {
        if self.w > 0.0 && self.h > 0.0 {
            (self.w / self.h).clamp(0.2, 5.0)
        } else {
            1.0
        }
    }
}
#[derive(Clone)]
struct ModelDetail {
    id: i64,
    kind: String,
    base: String,
    downloads: i64,
    description: String,
    tags: Vec<String>,
    versions: Vec<VerInfo>,
    images: Vec<GalleryImg>,
}
#[derive(Clone)]
struct ModelDetailState {
    item: SearchItem,
    data: Option<ModelDetail>,
    loading: bool,
    err: String,
    sel_version: i64,
    // 画廊分页：当前展示的预览图数量，「加载更多」递增（避免一次性实例化几十张）
    gallery_shown: usize,
}
#[derive(Clone)]
struct Resolved {
    source: String,
    model_name: String,
    kind: String,
    base: String,
    filename: String,
    size_kb: f64,
    subdir: String,
    image: String,
    download_url: String,
    versions: Vec<VerInfo>,
    version_id: i64,
    model_id: i64,
    sha256: String,
    desc: String,
}

#[derive(Clone)]
#[allow(dead_code)]
struct Task {
    id: u64,
    filename: String,
    subdir: String,
    status: String,
    downloaded: u64,
    total: u64,
    speed: f64,
    error: String,
    cancel: Arc<AtomicBool>,
    download_url: String,
    source: String,
    expected_sha256: Option<String>,
    verified: bool,
    // 下载状态扩展字段
    started_at: Option<Instant>,
    completed_at: Option<Instant>,
    local_path: Option<PathBuf>,
    // 模型简介（Civitai 来源通常有，HF 无）
    desc: String,
    // 是否已发送过完成/失败通知（避免重复弹窗）
    notified: bool,
}
type TaskRef = Arc<Mutex<Task>>;

// 持久化的未完成任务（写 tasks.json，重开程序自动恢复并靠 .part 续传）
#[derive(Serialize, Deserialize, Clone)]
struct PersistTask {
    filename: String,
    subdir: String,
    download_url: String,
    source: String,
    size_kb: f64,
    sha256: Option<String>,
    // serde(default)：老 tasks.json 没有此字段也能正常反序列化
    #[serde(default)]
    desc: String,
}

fn tasks_path() -> PathBuf {
    config_path().with_file_name("tasks.json")
}

fn save_tasks_to(p: &Path, list: &[PersistTask]) {
    if let Ok(s) = serde_json::to_string_pretty(list) {
        if let Some(dir) = p.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let _ = fs::write(p, s);
    }
}

fn load_tasks_from(p: &Path) -> Vec<PersistTask> {
    fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str(s.trim_start_matches('\u{feff}')).ok())
        .unwrap_or_default()
}

// 本地模型索引：记录下载过的模型元数据，用于工作流缺失项直接补齐
#[derive(Serialize, Deserialize, Clone, Debug)]
struct ModelRecord {
    filename: String,
    subdir: String,
    source: String,
    download_url: String,
    sha256: Option<String>,
    desc: String,
    model_id: Option<String>,
    version_id: Option<String>,
    size_kb: f64,
    downloaded_at: Option<String>, // ISO 8601 简单字符串
    // LoRA 触发词（Civitai trainedWords），供工作台注册表融合；
    // serde(default)：无此字段的旧 models.json 反序列化为空 vec，不报错
    #[serde(default)]
    trigger_words: Vec<String>,
}

fn models_path() -> PathBuf {
    config_path().with_file_name("models.json")
}

fn load_models_index(p: &Path) -> Vec<ModelRecord> {
    fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str(s.trim_start_matches('\u{feff}')).ok())
        .unwrap_or_default()
}

fn save_models_index(p: &Path, list: &[ModelRecord]) {
    if let Ok(s) = serde_json::to_string_pretty(list) {
        if let Some(dir) = p.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let _ = fs::write(p, s);
    }
}

// 用 filename+subdir 作唯一键，已存在则更新，不存在则追加
fn upsert_model_record(list: &mut Vec<ModelRecord>, rec: ModelRecord) {
    let key = format!("{}|{}", rec.filename, rec.subdir);
    if let Some(pos) = list.iter().position(|r| format!("{}|{}", r.filename, r.subdir) == key) {
        list[pos] = rec;
    } else {
        list.push(rec);
    }
}

fn now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs().to_string())
        .unwrap_or_default()
}

// 从已完成的任务写入/更新 models.json
fn record_task_to_index(t: &Task) {
    if t.status != "完成" && t.status != "已存在" {
        return;
    }
    let mut list = load_models_index(&models_path());
    // Task 不携带触发词：保留链接解析阶段（record_resolved_to_index）已落盘的 trigger_words
    let words = existing_trigger_words(&list, &t.filename, &t.subdir);
    let rec = task_to_record(t, words);
    upsert_model_record(&mut list, rec);
    save_models_index(&models_path(), &list);
}

// Task → ModelRecord（纯函数，便于单测）
fn task_to_record(t: &Task, trigger_words: Vec<String>) -> ModelRecord {
    ModelRecord {
        filename: t.filename.clone(),
        subdir: t.subdir.clone(),
        source: t.source.clone(),
        download_url: t.download_url.clone(),
        sha256: t.expected_sha256.clone().filter(|s| !s.is_empty()),
        desc: t.desc.clone(),
        model_id: None,
        version_id: None,
        size_kb: (t.total as f64 / 1024.0).max(0.0),
        downloaded_at: Some(now_iso()),
        trigger_words,
    }
}

// 列表中同 filename+subdir 记录的触发词（upsert 覆盖时保留，空则丢词）
fn existing_trigger_words(list: &[ModelRecord], filename: &str, subdir: &str) -> Vec<String> {
    list.iter()
        .find(|r| r.filename == filename && r.subdir == subdir)
        .map(|r| r.trigger_words.clone())
        .unwrap_or_default()
}

// 从链接解析结果写入/更新 models.json
fn record_resolved_to_index(r: &Resolved) {
    let mut list = load_models_index(&models_path());
    let rec = resolved_to_record(r);
    upsert_model_record(&mut list, rec);
    save_models_index(&models_path(), &list);
}

// Resolved → ModelRecord（纯函数，便于单测）
fn resolved_to_record(r: &Resolved) -> ModelRecord {
    ModelRecord {
        filename: r.filename.clone(),
        subdir: r.subdir.clone(),
        source: r.source.clone(),
        download_url: r.download_url.clone(),
        sha256: Some(r.sha256.clone()).filter(|s| !s.is_empty()),
        desc: r.desc.clone(),
        model_id: Some(r.model_id.to_string()).filter(|_| r.model_id > 0),
        version_id: Some(r.version_id.to_string()).filter(|_| r.version_id > 0),
        size_kb: r.size_kb,
        downloaded_at: None,
        trigger_words: resolved_trigger_words(r),
    }
}

// 取 Resolved 当前选中版本的 trainedWords（无匹配版本则空，HF 来源 versions 为空亦空）
fn resolved_trigger_words(r: &Resolved) -> Vec<String> {
    r.versions
        .iter()
        .find(|v| v.id == r.version_id)
        .map(|v| v.trained_words.clone())
        .unwrap_or_default()
}

// ============ 网络 ============
fn agent(cfg: &Config) -> ureq::Agent {
    let mut b = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(20))
        // 读超时：连接僵死时让 read 出错返回，否则任务永久卡死且占住并发槽
        .timeout_read(Duration::from_secs(30))
        .user_agent("ComfyToolbox/1.0");
    // 配置里的显式代理优先（用户可在设置页填写），其次才读系统代理环境变量
    let mut proxy_set = false;
    if let Some(ref url) = cfg.proxy_url {
        let url = url.trim();
        if !url.is_empty() {
            if let Ok(p) = ureq::Proxy::new(url) {
                b = b.proxy(p);
                proxy_set = true;
            }
        }
    }
    if !proxy_set {
        // 尊重系统代理环境变量（ureq 默认不读）：Clash 仅系统代理模式、或 TUN 对 rustls
        // 指纹不友好的节点下，走本地代理端口仍可联网
        for key in ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"] {
            if let Ok(v) = std::env::var(key) {
                if !v.is_empty() {
                    if let Ok(p) = ureq::Proxy::new(&v) {
                        b = b.proxy(p);
                        break;
                    }
                }
            }
        }
    }
    b.build()
}

// Civitai 的 images 数组混有 type=video 的预览（视频模型常见），egui 无法解码视频，只取 type=image 的
fn first_image_url(images: &[Value]) -> Option<String> {
    images
        .iter()
        .filter(|im| im.get("type").and_then(|t| t.as_str()).is_none_or(|t| t == "image"))
        .find_map(|im| im.get("url").and_then(|u| u.as_str()).map(|s| s.to_string()))
}

// 取版本的 primary 文件，无 primary 则退回第一个
fn primary_file(ver: &Value) -> Option<Value> {
    let files = ver.get("files").and_then(|x| x.as_array())?;
    files
        .iter()
        .find(|f| f.get("primary").and_then(|x| x.as_bool()).unwrap_or(false))
        .or_else(|| files.first())
        .cloned()
}

// 从 Civitai modelVersions[] 的单个版本对象解析出 VerInfo（详情页与链接解析共用）。
fn parse_version(ver: &Value) -> VerInfo {
    let vf = primary_file(ver);
    VerInfo {
        id: ver.get("id").and_then(|x| x.as_i64()).unwrap_or(0),
        name: ver.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        base: ver.get("baseModel").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        filename: vf.as_ref().and_then(|f| f.get("name")).and_then(|x| x.as_str()).unwrap_or("").to_string(),
        size_kb: vf.as_ref().and_then(|f| f.get("sizeKB")).and_then(|x| x.as_f64()).unwrap_or(0.0),
        sha256: vf
            .as_ref()
            .and_then(|f| f.get("hashes"))
            .and_then(|h| h.get("SHA256"))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_lowercase(),
        trained_words: ver
            .get("trainedWords")
            .and_then(|x| x.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|w| w.as_str().map(|s| s.trim().to_string()))
                    .filter(|s| !s.is_empty())
                    .collect()
            })
            .unwrap_or_default(),
        published_at: ver
            .get("publishedAt")
            .and_then(|x| x.as_str())
            .or_else(|| ver.get("createdAt").and_then(|x| x.as_str()))
            .map(|s| s.chars().take(10).collect())
            .unwrap_or_default(),
    }
}

fn civitai_search(cfg: &Config, query: &str, types: &str, base: &str) -> Result<(Vec<SearchItem>, Option<String>), String> {
    let mut url = format!("{}/v1/models?limit=24&nsfw=true&sort=Most%20Downloaded", cfg.civitai_api_base());
    if !query.is_empty() {
        url.push_str(&format!("&query={}", urlencode(query)));
    }
    if !types.is_empty() {
        url.push_str(&format!("&types={}", types));
    }
    if !base.is_empty() {
        url.push_str(&format!("&baseModels={}", urlencode(base)));
    }
    civitai_fetch_page(cfg, &url)
}

// 拉取一页搜索结果；nextPage 是 Civitai 在 metadata 里给的完整下一页 URL（游标分页）
fn civitai_fetch_page(cfg: &Config, url: &str) -> Result<(Vec<SearchItem>, Option<String>), String> {
    let mut req = agent(cfg).get(url);
    if !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    let body = req.call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    if let Some(items) = v.get("items").and_then(|x| x.as_array()) {
        for it in items {
            let ver = it.get("modelVersions").and_then(|x| x.as_array()).and_then(|a| a.first());
            let image = ver
                .and_then(|v| v.get("images"))
                .and_then(|x| x.as_array())
                .and_then(|a| first_image_url(a))
                .unwrap_or_default();
            out.push(SearchItem {
                id: it.get("id").and_then(|x| x.as_i64()).unwrap_or(0),
                name: it.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                kind: it.get("type").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                nsfw: it.get("nsfw").and_then(|x| x.as_bool()).unwrap_or(false),
                base: ver.and_then(|v| v.get("baseModel")).and_then(|x| x.as_str()).unwrap_or("").to_string(),
                version_id: ver.and_then(|v| v.get("id")).and_then(|x| x.as_i64()).unwrap_or(0),
                image,
                downloads: it
                    .get("stats")
                    .and_then(|s| s.get("downloadCount"))
                    .and_then(|x| x.as_i64())
                    .unwrap_or(0),
            });
        }
    }
    let next = v
        .get("metadata")
        .and_then(|m| m.get("nextPage"))
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    Ok((out, next))
}

// ============ HuggingFace 搜索 ============

// 解析 HF /api/models 的返回（数组）。纯函数，便于单测。
fn parse_hf_search(v: &Value) -> Vec<HfRepo> {
    v.as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    let id = m
                        .get("id")
                        .or_else(|| m.get("modelId"))
                        .and_then(|x| x.as_str())?
                        .to_string();
                    if id.is_empty() {
                        return None;
                    }
                    Some(HfRepo {
                        id,
                        downloads: m.get("downloads").and_then(|x| x.as_i64()).unwrap_or(0),
                        likes: m.get("likes").and_then(|x| x.as_i64()).unwrap_or(0),
                        pipeline_tag: m.get("pipeline_tag").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

// 搜索 HF 模型仓库（公开搜索无需 token）。按下载量降序，取前 30。
fn hf_search(cfg: &Config, query: &str) -> Result<Vec<HfRepo>, String> {
    let url = format!(
        "{}/api/models?search={}&limit=30&sort=downloads&direction=-1",
        hf_base(cfg),
        urlencode(query)
    );
    let body = agent(cfg).get(&url).call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    Ok(parse_hf_search(&v))
}

// 模型权重文件扩展名（用于从仓库文件树里筛出可下载项）
const HF_MODEL_EXTS: [&str; 8] = [".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".sft"];

// 解析 HF tree API 返回，筛出模型权重文件并按大小降序。纯函数，便于单测。
fn parse_hf_files(v: &Value) -> Vec<HfFile> {
    let mut files: Vec<HfFile> = v
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter(|e| e.get("type").and_then(|x| x.as_str()) == Some("file"))
                .filter_map(|e| {
                    let path = e.get("path").and_then(|x| x.as_str())?.to_string();
                    let low = path.to_lowercase();
                    if !HF_MODEL_EXTS.iter().any(|x| low.ends_with(x)) {
                        return None;
                    }
                    // 大模型都是 LFS，真实大小在 lfs.size；非 LFS 小文件退回 size
                    let size = e
                        .get("lfs")
                        .and_then(|l| l.get("size"))
                        .and_then(|x| x.as_u64())
                        .or_else(|| e.get("size").and_then(|x| x.as_u64()))
                        .unwrap_or(0);
                    Some(HfFile { path, size })
                })
                .collect()
        })
        .unwrap_or_default();
    files.sort_by_key(|f| std::cmp::Reverse(f.size));
    files
}

// 列出 HF 仓库 main 分支下的模型文件（递归）。tree API 单页 1000 项，超大仓库可能截断。
fn hf_repo_files(cfg: &Config, repo: &str) -> Result<Vec<HfFile>, String> {
    let url = format!("{}/api/models/{}/tree/main?recursive=true", hf_base(cfg), repo);
    let body = agent(cfg).get(&url).call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let files = parse_hf_files(&v);
    if files.is_empty() {
        return Err("该仓库未找到可下载的模型文件（.safetensors/.gguf 等）".into());
    }
    Ok(files)
}

fn strip_html(html: &str) -> String {
    let re = regex::Regex::new(r"<[^>]+>").unwrap();
    let s = re.replace_all(html, " ");
    // 简单解码常见 HTML 实体
    let s = s.replace("&nbsp;", " ").replace("&quot;", "\"").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">");
    regex::Regex::new(r"\s+")
        .unwrap()
        .replace_all(&s, " ")
        .trim()
        .to_string()
}

// 获取模型详情页完整数据（描述、版本、图片）
fn civitai_model_detail(cfg: &Config, item: &SearchItem) -> Result<ModelDetail, String> {
    let api = format!("https://{}/api/v1/models/{}", cfg.civitai_host, item.id);
    let mut req = agent(cfg).get(&api);
    if !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    let body = req.call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let kind = v.get("type").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let downloads = v
        .get("stats")
        .and_then(|s| s.get("downloadCount"))
        .and_then(|x| x.as_i64())
        .unwrap_or(0);
    let description = strip_html(v.get("description").and_then(|x| x.as_str()).unwrap_or(""));
    let tags = v
        .get("tags")
        .and_then(|x| x.as_array())
        .map(|a| a.iter().filter_map(|t| t.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();
    let vers_arr = v.get("modelVersions").and_then(|x| x.as_array()).cloned().unwrap_or_default();
    let versions: Vec<VerInfo> = vers_arr.iter().map(parse_version).collect();
    // 图片：优先收集当前版本，再收集其它版本，去重
    let mut images = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut push_imgs = |ver: &Value| {
        if let Some(arr) = ver.get("images").and_then(|x| x.as_array()) {
            for im in arr {
                if let Some(url) = im.get("url").and_then(|x| x.as_str()) {
                    if seen.insert(url.to_string()) {
                        // 宽高 Civitai 大多返回；缺失则给 0，aspect() 自动回退 1:1。
                        let w = im.get("width").and_then(|x| x.as_f64()).unwrap_or(0.0) as f32;
                        let h = im.get("height").and_then(|x| x.as_f64()).unwrap_or(0.0) as f32;
                        images.push(GalleryImg { url: url.to_string(), w, h });
                    }
                }
            }
        }
    };
    if let Some(pos) = vers_arr.iter().position(|ver| {
        ver.get("id").and_then(|x| x.as_i64()) == Some(item.version_id)
    }) {
        push_imgs(&vers_arr[pos]);
    }
    for ver in &vers_arr {
        push_imgs(ver);
    }
    let base = versions
        .iter()
        .find(|v| v.id == item.version_id)
        .map(|v| v.base.clone())
        .unwrap_or_else(|| item.base.clone());
    Ok(ModelDetail {
        id: item.id,
        kind,
        base,
        downloads,
        description,
        tags,
        versions,
        images,
    })
}

fn resolve_url(cfg: &Config, url: &str) -> Result<Resolved, String> {
    let url = url.trim();
    // Civitai
    let re_model = regex::Regex::new(r"civitai\.(?:com|red|work)/models/(\d+)").unwrap();
    let re_ver = regex::Regex::new(r"modelVersionId=(\d+)").unwrap();
    let re_num = regex::Regex::new(r"^\d+$").unwrap();
    let model_id = re_model
        .captures(url)
        .map(|c| c[1].to_string())
        .or_else(|| if re_num.is_match(url) { Some(url.to_string()) } else { None });
    if let Some(mid) = model_id {
        let want_ver = re_ver.captures(url).map(|c| c[1].to_string());
        return resolve_civitai_model(cfg, &mid, want_ver);
    }
    resolve_url_rest(cfg, url)
}

// 经 /api/v1/models/{id} 解析 Civitai 模型为可下载项；want_ver 指定版本（作品页资源带版本号）
fn resolve_civitai_model(cfg: &Config, mid: &str, want_ver: Option<String>) -> Result<Resolved, String> {
    {
        let api = format!("https://{}/api/v1/models/{}", cfg.civitai_host, mid);
        let mut req = agent(cfg).get(&api);
        if !cfg.civitai_token.is_empty() {
            req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
        }
        let body = req.call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
        let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
        let kind = v.get("type").and_then(|x| x.as_str()).unwrap_or("Checkpoint").to_string();
        let model_name = v.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let vers_arr = v.get("modelVersions").and_then(|x| x.as_array()).cloned().unwrap_or_default();
        if vers_arr.is_empty() {
            return Err("未找到模型版本".into());
        }
        let chosen = match &want_ver {
            Some(wv) => vers_arr
                .iter()
                .find(|ver| ver.get("id").and_then(|x| x.as_i64()).map(|i| i.to_string()) == Some(wv.clone()))
                .unwrap_or(&vers_arr[0]),
            None => &vers_arr[0],
        };
        let file = primary_file(chosen).ok_or("该版本无文件")?;
        let version_id = chosen.get("id").and_then(|x| x.as_i64()).unwrap_or(0);
        let image = chosen
            .get("images")
            .and_then(|x| x.as_array())
            .and_then(|a| first_image_url(a))
            .unwrap_or_default();
        let versions = vers_arr.iter().map(parse_version).collect();
        Ok(Resolved {
            source: "civitai".into(),
            model_name,
            kind: kind.clone(),
            base: chosen.get("baseModel").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            filename: file.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            size_kb: file.get("sizeKB").and_then(|x| x.as_f64()).unwrap_or(0.0),
            subdir: type_dir(&kind).to_string(),
            image,
            download_url: format!("https://{}/api/download/models/{}", cfg.civitai_host, version_id),
            versions,
            version_id,
            model_id: v.get("id").and_then(|x| x.as_i64()).unwrap_or(0),
            sha256: file
                .get("hashes")
                .and_then(|h| h.get("SHA256"))
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_lowercase(),
            desc: html_to_text(v.get("description").and_then(|x| x.as_str()).unwrap_or(""), 240),
        })
    }
}

// resolve_url 的非 Civitai-模型页部分（HF 等）
fn resolve_url_rest(cfg: &Config, url: &str) -> Result<Resolved, String> {
    // HuggingFace
    let re_hf = regex::Regex::new(r"https?://(?:hf-mirror\.com|huggingface\.co)/(.+?)/(?:resolve|blob)/([^/]+)/(.+)").unwrap();
    if let Some(c) = re_hf.captures(url) {
        let repo = c[1].to_string();
        let branch = c[2].to_string();
        let path = c[3].to_string();
        let fname = percent_decode(path.rsplit('/').next().unwrap_or(&path));
        let kind = guess_type(&format!("{} {}", fname, path)).to_string();
        return Ok(Resolved {
            source: "hf".into(),
            model_name: repo.clone(),
            kind: kind.clone(),
            base: String::new(),
            filename: fname,
            size_kb: 0.0,
            subdir: type_dir(&kind).to_string(),
            image: String::new(),
            download_url: format!("{}/{}/resolve/{}/{}", hf_base(cfg), repo, branch, path),
            versions: Vec::new(),
            version_id: 0,
            model_id: 0,
            sha256: hf_sha256(cfg, &repo, &branch, &percent_decode(&path)).unwrap_or_default(),
            desc: String::new(),
        });
    }
    Err("无法识别的链接（支持 Civitai 模型页·作品页 / HuggingFace 文件页）".into())
}

// 资源引用：(可选 model_id, 可选 version_id)，至少一个为 Some
type MediaRef = (Option<String>, Option<String>);

// 从作品页 HTML 的 __NEXT_DATA__ 内嵌 JSON 提取资源引用。比抓渲染后的 <ul> 可靠得多：
// 图片/视频页（同走 /images/ 路由）有完整 resources[]（含 modelId+modelVersionId）；
// 帖子页只有裸 modelVersionIds[]/modelVersionIdsManual[]。两者都从结构化 JSON 拿，字段稳定。
fn extract_media_resources(html: &str) -> Vec<MediaRef> {
    let mut out: Vec<MediaRef> = Vec::new();
    if let Some(json) = next_data_json(html) {
        if let Ok(v) = serde_json::from_str::<Value>(json) {
            collect_media_refs(&v, &mut out);
        }
    }
    if !out.is_empty() {
        return out;
    }
    // 回退：抓渲染后的 "Resources used" <ul>（老页面或 JSON 结构变动时兜底）
    if let Some(start) = html.find("Resources used") {
        let tail = &html[start..];
        let end = tail.find("</ul>").map(|i| i + 5).unwrap_or_else(|| tail.len().min(20000));
        let section = &tail[..end];
        let re = regex::Regex::new(r#"href="/models/(\d+)[^"]*""#).unwrap();
        let re_ver = regex::Regex::new(r"modelVersionId=(\d+)").unwrap();
        for c in re.captures_iter(section) {
            let href = c.get(0).map(|m| m.as_str()).unwrap_or("");
            let mid = Some(c[1].to_string());
            let vid = re_ver.captures(href).map(|v| v[1].to_string());
            if !out.iter().any(|(m, v)| *m == mid && *v == vid) {
                out.push((mid, vid));
            }
        }
    }
    out
}

fn next_data_json(html: &str) -> Option<&str> {
    let start = html.find("__NEXT_DATA__")?;
    let gt = html[start..].find('>')? + start + 1;
    let end = html[gt..].find("</script>")? + gt;
    Some(&html[gt..end])
}

// 递归收集资源引用：含 modelVersionId+modelId 的对象（图片/视频 resources[]），
// 以及 modelVersionIds/modelVersionIdsManual 数组里的裸版本号（帖子页）
fn collect_media_refs(v: &Value, out: &mut Vec<MediaRef>) {
    let push = |out: &mut Vec<MediaRef>, m: Option<String>, vid: Option<String>| {
        if m.is_none() && vid.is_none() {
            return;
        }
        if !out.iter().any(|(em, ev)| *em == m && *ev == vid) {
            out.push((m, vid));
        }
    };
    match v {
        Value::Object(o) => {
            let vid = o.get("modelVersionId").and_then(|x| x.as_i64());
            let mid = o.get("modelId").and_then(|x| x.as_i64());
            if let Some(vid) = vid {
                push(out, mid.map(|x| x.to_string()), Some(vid.to_string()));
            }
            for key in ["modelVersionIds", "modelVersionIdsManual"] {
                if let Some(arr) = o.get(key).and_then(|x| x.as_array()) {
                    for x in arr {
                        if let Some(id) = x.as_i64() {
                            push(out, None, Some(id.to_string()));
                        }
                    }
                }
            }
            for x in o.values() {
                collect_media_refs(x, out);
            }
        }
        Value::Array(a) => {
            for x in a {
                collect_media_refs(x, out);
            }
        }
        _ => {}
    }
}

// 按版本号解析（帖子页只有裸 versionId）：/api/v1/model-versions/{id} → Resolved
fn resolve_civitai_version(cfg: &Config, vid: &str) -> Result<Resolved, String> {
    let api = format!("https://{}/api/v1/model-versions/{}", cfg.civitai_host, vid);
    let mut req = agent(cfg).get(&api);
    if !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    let body = req.call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let mid = v.get("modelId").and_then(|x| x.as_i64()).ok_or("版本无对应模型")?;
    resolve_civitai_model(cfg, &mid.to_string(), Some(vid.to_string()))
}

// 解析 Civitai 作品页（图片/视频/帖子）：抓页面 → 解析 __NEXT_DATA__ 资源 → 逐个经 API 解析。
// 每次粘贴只抓一页，等同浏览器访问一次，不做批量爬取
fn resolve_media_page(cfg: &Config, url: &str) -> Result<Vec<Resolved>, String> {
    let body = agent(cfg).get(url).call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let refs = extract_media_resources(&body);
    if refs.is_empty() {
        return Err("未在该作品页找到模型资源（作品可能隐藏了生成信息，或该页面需要登录查看）".into());
    }
    let mut out = Vec::new();
    let mut last_err = String::new();
    for (mid, vid) in refs {
        let r = match (&mid, &vid) {
            (Some(m), _) => resolve_civitai_model(cfg, m, vid.clone()),
            (None, Some(ver)) => resolve_civitai_version(cfg, ver),
            (None, None) => continue,
        };
        match r {
            Ok(r) => {
                if !out.iter().any(|e: &Resolved| e.download_url == r.download_url) {
                    out.push(r);
                }
            }
            Err(e) => last_err = e,
        }
    }
    if out.is_empty() {
        return Err(format!("作品资源解析全部失败：{}", last_err));
    }
    Ok(out)
}

fn urlencode(s: &str) -> String {
    let mut o = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => o.push(b as char),
            b' ' => o.push_str("%20"),
            _ => o.push_str(&format!("%{:02X}", b)),
        }
    }
    o
}

// ============ 下载 ============
#[derive(Clone)]
struct DlMeta {
    download_url: String,
    source: String,
    expected_sha256: Option<String>,
    desc: String,
}

fn hex_str(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

// 经 HF tree API（GET，镜像 308 重定向可被 ureq 跟随；POST 的 paths-info 不行）查文件的
// LFS sha256。非 LFS 小文件或目录超过一页（1000 项）找不到时返回 None，此时跳过校验。
fn hf_sha256(cfg: &Config, repo: &str, branch: &str, path: &str) -> Option<String> {
    let parent = path.rsplit_once('/').map(|(d, _)| d).unwrap_or("");
    let url = if parent.is_empty() {
        format!("{}/api/models/{}/tree/{}", hf_base(cfg), repo, branch)
    } else {
        format!("{}/api/models/{}/tree/{}/{}", hf_base(cfg), repo, branch, parent)
    };
    let body = agent(cfg).get(&url).call().ok()?.into_string().ok()?;
    let v: Value = serde_json::from_str(&body).ok()?;
    v.as_array()?
        .iter()
        .find(|e| e.get("path").and_then(|x| x.as_str()) == Some(path))?
        .get("lfs")?
        .get("oid")?
        .as_str()
        .map(|s| s.to_lowercase())
}

// 从 HF 下载 URL 反推 repo/branch/path 再查哈希（套餐与恢复任务只有 URL）
fn hf_sha256_from_url(cfg: &Config, url: &str) -> Option<String> {
    let re = regex::Regex::new(r"(?:hf-mirror\.com|huggingface\.co)/(.+?)/resolve/([^/]+)/(.+)$").ok()?;
    let c = re.captures(url)?;
    hf_sha256(cfg, &c[1], &c[2], &percent_decode(&c[3]))
}

fn start_task(cfg: Config, downloads: Arc<Mutex<Vec<TaskRef>>>, filename: String, subdir: String, meta: DlMeta, size_kb: f64) {
    let filename = sanitize_filename(&filename);
    {
        // 同名同目录任务进行中则不重复入队，否则两个线程并发写同一 .part 会损坏文件
        let dl = downloads.lock().unwrap();
        let dup = dl.iter().any(|t| {
            let t = t.lock().unwrap();
            t.filename == filename && t.subdir == subdir && (t.status == "排队中" || t.status == "下载中")
        });
        if dup {
            return;
        }
    }
    let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    let cancel = Arc::new(AtomicBool::new(false));
    // 磁盘空间兜底拦截。关键：必须扣除已有 .part 续传进度，只按「剩余待下载字节」判定，
    // 否则盘越满、续传进度越高反而越会把可续传任务误杀（破坏断点续传不变量）。
    // 正式文件已存在 → 剩余 0（download_file 会判「已存在」秒结束）；size_kb=0 → Unknown 放行。
    let space_err = {
        let dir = resolve_dest_dir(&cfg, &subdir);
        let dest = dir.join(&filename);
        let part = dest.with_extension(format!("{}.part", dest.extension().and_then(|s| s.to_str()).unwrap_or("")));
        let part_bytes = fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
        let remaining = remaining_to_download(dest.exists(), (size_kb * 1024.0) as u64, part_bytes);
        match disk_precheck(available_space_bytes(&dir), remaining) {
            DiskCheck::Insufficient { avail, need } => {
                Some(format!("磁盘空间不足：目标盘仅剩 {}，仍需 {}", fmt_size(avail), fmt_size(need)))
            }
            _ => None,
        }
    };
    let task = Arc::new(Mutex::new(Task {
        id,
        filename: filename.clone(),
        subdir: subdir.clone(),
        status: if space_err.is_some() { "失败".into() } else { "排队中".into() },
        downloaded: 0,
        total: (size_kb * 1024.0) as u64,
        speed: 0.0,
        error: space_err.clone().unwrap_or_default(),
        cancel: cancel.clone(),
        download_url: meta.download_url.clone(),
        source: meta.source.clone(),
        expected_sha256: meta.expected_sha256.clone().filter(|s| !s.is_empty()),
        verified: false,
        started_at: None,
        completed_at: None,
        local_path: None,
        desc: meta.desc.clone(),
        notified: false,
    }));
    downloads.lock().unwrap().push(task.clone());
    if space_err.is_some() {
        return; // 空间不足：已作为「失败」任务展示，不启动下载线程
    }
    std::thread::spawn(move || {
        loop {
            if cancel.load(Ordering::Relaxed) {
                task.lock().unwrap().status = "已取消".into();
                return;
            }
            // 全局暂停时排队任务不取槽，线程原地等待
            if !PAUSED.load(Ordering::Relaxed) {
                // compare_exchange 闭合 load 与 add 之间的竞态窗口，确保并发数不超配置
                let cur = ACTIVE.load(Ordering::Relaxed);
                if cur < cfg.max_concurrent.max(1)
                    && ACTIVE.compare_exchange(cur, cur + 1, Ordering::Relaxed, Ordering::Relaxed).is_ok()
                {
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        // 套餐/恢复任务只有 URL 没有哈希：HF 源在这里懒查 paths-info（查不到就不校验）
        let mut meta = meta;
        if meta.expected_sha256.as_deref().unwrap_or("").is_empty() && meta.source == "hf" {
            if let Some(h) = hf_sha256_from_url(&cfg, &meta.download_url) {
                task.lock().unwrap().expected_sha256 = Some(h.clone());
                meta.expected_sha256 = Some(h);
            }
        }
        // 失败自动重试：指数退避 2/4/8 秒；鉴权错误与哈希不匹配不重试（重试也不会好）
        let mut r = download_file(&cfg, &task, &filename, &subdir, &meta);
        let mut attempt = 0u32;
        while let Err(e) = &r {
            if attempt >= 3 || !should_retry(e) || cancel.load(Ordering::Relaxed) {
                break;
            }
            attempt += 1;
            {
                let mut t = task.lock().unwrap();
                t.status = format!("重试等待 ({}/3)", attempt);
                t.error = friendly_err(e.clone());
            }
            let wait_ticks = (2u64 << (attempt - 1)) * 10; // 2/4/8 秒，按 100ms 检查取消
            let mut ticked = 0;
            let mut cancelled = false;
            while ticked < wait_ticks {
                if cancel.load(Ordering::Relaxed) {
                    task.lock().unwrap().status = "已取消".into();
                    cancelled = true;
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
                ticked += 1;
            }
            if cancelled {
                break;
            }
            task.lock().unwrap().error.clear();
            r = download_file(&cfg, &task, &filename, &subdir, &meta);
        }
        ACTIVE.fetch_sub(1, Ordering::Relaxed);
        if let Err(e) = r {
            let mut t = task.lock().unwrap();
            if t.status != "已取消" {
                t.status = "失败".into();
                t.error = friendly_err(e);
            }
        }
    });
}

// ============ 磁盘空间预检 ============
// 下载大模型(Flux/Wan 动辄一二十G)前检查目标盘可用空间：确认窗里警告/阻止，
// 并在 start_task 入口兜底拦截（覆盖套餐/恢复/补齐等非交互入队点）。

const DISK_MARGIN_MIN_BYTES: u64 = 300 * 1024 * 1024; // 安全余量下限 300MB
const DISK_MARGIN_RATIO: f64 = 0.05; // 安全余量比例 5%

// 解析某 subdir 的真实落盘目录。与 download_file 共用同一条解析路径，
// 保证"预检的盘 == 实际下载的盘"，不会出现检查了 A 盘却下到 B 盘。
fn resolve_dest_dir(cfg: &Config, subdir: &str) -> PathBuf {
    match effective_download_models_root(cfg) {
        Some(models_root) => {
            let ty = subdir.strip_prefix("models/").unwrap_or(subdir);
            let sub: PathBuf = ty.split('/').collect();
            models_root.join(sub)
        }
        None => {
            let sub: PathBuf = subdir.split('/').collect();
            expand_root(&cfg.comfy_root).join(sub)
        }
    }
}

// 向上回溯到第一个已存在的祖先目录：目标类型子目录可能尚未创建，
// 直接对不存在路径查可用空间会失败。
fn nearest_existing_ancestor(path: &Path) -> PathBuf {
    let mut p = path;
    loop {
        if p.exists() {
            return p.to_path_buf();
        }
        match p.parent() {
            Some(parent) => p = parent,
            None => return path.to_path_buf(),
        }
    }
}

// 目标目录所在盘对当前用户可用的字节数。取不到（网络盘/不存在盘符/暂未实现的平台）返回 None。
#[cfg(windows)]
fn available_space_bytes(path: &Path) -> Option<u64> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetDiskFreeSpaceExW(
            lp_directory_name: *const u16,
            lp_free_bytes_available_to_caller: *mut u64,
            lp_total_number_of_bytes: *mut u64,
            lp_total_number_of_free_bytes: *mut u64,
        ) -> i32;
    }
    let dir = nearest_existing_ancestor(path);
    let wide: Vec<u16> = dir.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
    let mut free_to_caller: u64 = 0;
    // SAFETY: wide 是 NUL 结尾的合法 UTF-16 路径；后两个 out 指针传 null 表示不需要该值。
    let ok = unsafe {
        GetDiskFreeSpaceExW(wide.as_ptr(), &mut free_to_caller, std::ptr::null_mut(), std::ptr::null_mut())
    };
    (ok != 0).then_some(free_to_caller)
}

#[cfg(not(windows))]
fn available_space_bytes(path: &Path) -> Option<u64> {
    // macOS/Linux: 用 statvfs 取调用者可用空间（考虑配额），与 Windows 分支的 GetDiskFreeSpaceExW 对齐。
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    #[repr(C)]
    struct Statvfs {
        f_bsize: u64,
        f_frsize: u64,
        f_blocks: u64,
        f_bfree: u64,
        f_bavail: u64,
        f_files: u64,
        f_ffree: u64,
        f_favail: u64,
        f_fsid: u64,
        f_flag: u64,
        f_namemax: u64,
        __spare: [u64; 6],
    }
    extern "C" {
        fn statvfs(path: *const i8, buf: *mut Statvfs) -> i32;
    }

    let dir = nearest_existing_ancestor(path);
    let c_path = CString::new(dir.as_os_str().as_bytes()).ok()?;
    let mut stat = Statvfs {
        f_bsize: 0, f_frsize: 0, f_blocks: 0, f_bfree: 0, f_bavail: 0,
        f_files: 0, f_ffree: 0, f_favail: 0, f_fsid: 0, f_flag: 0, f_namemax: 0,
        __spare: [0; 6],
    };
    // SAFETY: c_path 是 NUL 结尾的合法路径字符串；stat 是栈上局部可变变量。
    let ok = unsafe { statvfs(c_path.as_ptr(), &mut stat) };
    if ok == 0 {
        // f_bavail = 非特权用户可用的块数；f_frsize = 文件系统块大小
        Some(stat.f_bavail.saturating_mul(stat.f_frsize))
    } else {
        None
    }
}

// 磁盘空间判定结果
#[derive(Debug, PartialEq, Clone, Copy)]
enum DiskCheck {
    Unknown,                                 // 取不到可用空间 → 不打扰、放行
    Ok,                                      // 余量充足
    Tight { avail: u64, need: u64 },         // 能放下但余量紧张 → 黄色警告、可继续
    Insufficient { avail: u64, need: u64 },  // 放不下 → 红色阻止
}

// 纯判定：avail=可用字节(None=未知)，file_bytes=待下载文件大小。
fn disk_precheck(avail: Option<u64>, file_bytes: u64) -> DiskCheck {
    let Some(avail) = avail else {
        return DiskCheck::Unknown;
    };
    if file_bytes == 0 {
        return DiskCheck::Ok; // 无大小信息（大量入队点 size_kb=0）→ 不打扰
    }
    if avail < file_bytes {
        return DiskCheck::Insufficient { avail, need: file_bytes };
    }
    let margin = ((file_bytes as f64 * DISK_MARGIN_RATIO) as u64).max(DISK_MARGIN_MIN_BYTES);
    let need = file_bytes.saturating_add(margin);
    if avail >= need {
        DiskCheck::Ok
    } else {
        DiskCheck::Tight { avail, need }
    }
}

// 实际还需下载的字节：正式文件已存在→0（秒判已存在）；否则整文件减去已有 .part 续传进度。
// 磁盘预检必须用这个剩余量，否则盘越满、续传越深越会把可续传任务误判为空间不足。
fn remaining_to_download(dest_exists: bool, full_bytes: u64, part_bytes: u64) -> u64 {
    if dest_exists {
        0
    } else {
        full_bytes.saturating_sub(part_bytes)
    }
}

impl DiskCheck {
    // (是否阻止下载, 提示文案)；Ok/Unknown 返回 None（不显示任何行）。
    fn warning(&self) -> Option<(bool, String)> {
        match *self {
            DiskCheck::Unknown | DiskCheck::Ok => None,
            DiskCheck::Tight { avail, need } => Some((
                false,
                format!("目标盘仅剩 {}，本次下载约需 {}（含安全余量），空间偏紧", fmt_size(avail), fmt_size(need)),
            )),
            DiskCheck::Insufficient { avail, need } => Some((
                true,
                format!("目标盘仅剩 {}，连文件本体 {} 都放不下，已阻止入队", fmt_size(avail), fmt_size(need)),
            )),
        }
    }
}

// ============ 多连接分块下载 ============
// 仅对「全新下载（无 .part）+ 非 Civitai（签名一次性 URL 不并发）+ 远端支持 Range + 文件够大」启用。
// 任何不满足都回落到单连接 download_file 主路径（一字不改，永远可用的安全网）。
// SHA256 不边下边算（并发分块无法按字节序喂哈希），改为全部下完后顺序读 .part 整体算一次。

const MULTIPART_MIN: u64 = 64 * 1024 * 1024; // 小于 64MiB 不值得多连接探测往返
const MULTIPART_CONNS: usize = 4;

#[derive(Debug, Clone, PartialEq)]
struct RangeInfo {
    total: u64,
    supports_range: bool,
}

// 解析 Range 探测响应（纯函数，便于单测）：206 + Content-Range: "bytes 0-0/<total>" → 支持且拿到总长
fn parse_range_probe(status: u16, content_range: Option<&str>) -> RangeInfo {
    if status == 206 {
        if let Some(total) = content_range.and_then(|cr| cr.rsplit('/').next()).and_then(|s| s.trim().parse::<u64>().ok()) {
            return RangeInfo { total, supports_range: total > 0 };
        }
    }
    RangeInfo { total: 0, supports_range: false }
}

fn should_use_multipart(source: &str, supports_range: bool, total: u64) -> bool {
    source != "civitai" && supports_range && total >= MULTIPART_MIN
}

// 把 0..total 切成 n 个闭区间 [start,end]，无缝无叠，余数归最后一块。纯函数，单测穷举。
fn plan_chunks(total: u64, n: usize) -> Vec<(u64, u64)> {
    if total == 0 || n == 0 {
        return Vec::new();
    }
    let n = (n as u64).min(total) as usize; // total<n 时收敛块数，保证每块 ≥1 字节
    let base = total / n as u64;
    let mut chunks = Vec::with_capacity(n);
    let mut start = 0u64;
    for i in 0..n {
        let end = if i == n - 1 { total - 1 } else { start + base - 1 };
        chunks.push((start, end));
        start = end + 1;
    }
    chunks
}

// 用 Range: bytes=0-0 探测：拿 206 + Content-Range 总长，几乎零流量。失败/不支持返回 None。
fn probe_range(cfg: &Config, meta: &DlMeta) -> Option<RangeInfo> {
    let resp = agent(cfg).get(&meta.download_url).set("Range", "bytes=0-0").call().ok()?;
    let ri = parse_range_probe(resp.status(), resp.header("Content-Range"));
    ri.supports_range.then_some(ri)
}

// 单个分块 worker：下 [start,end] 闭区间到 .part 的对应偏移。块内断流/瞬时错误有限重试。
// chunk = (start, end, idx)。返回 Ok(true)=本块完整下完；Ok(false)=被暂停/取消中断退出。
// 主线程据「是否所有块都 Ok(true)」决定收尾，不读全局 PAUSED（恢复会把它翻回 false 造成误判）。
fn download_chunk(cfg: &Config, url: &str, part: &Path, chunk: (u64, u64, usize), progress: &[AtomicU64], cancel: &AtomicBool) -> Result<bool, String> {
    let (start, end, idx) = chunk;
    let mut cur = start;
    let mut conn_attempts = 0u32; // 连接错误重试预算
    let mut stall_attempts = 0u32; // 断流重试预算（与连接错误分开计，避免互相挤占/退避爆炸）
    loop {
        if cur > end {
            return Ok(true); // 完成优先于中断判定：刚下完就被暂停不应被当作中断而丢弃
        }
        if cancel.load(Ordering::Relaxed) || PAUSED.load(Ordering::Relaxed) {
            return Ok(false);
        }
        let resp = match agent(cfg).get(url).set("Range", &format!("bytes={}-{}", cur, end)).call() {
            Ok(r) => r,
            Err(e) => {
                let es = e.to_string();
                conn_attempts += 1;
                if conn_attempts < 4 && should_retry(&es) {
                    std::thread::sleep(Duration::from_secs(1u64 << conn_attempts.min(3))); // 2/4/8s 封顶
                    continue;
                }
                return Err(friendly_err(es));
            }
        };
        if resp.status() != 206 {
            return Err("分块请求未返回 206（远端不支持 Range），已回落".into());
        }
        // 校验响应确实从 cur 开始：206 不蕴含 body 起始偏移，畸形/缓存代理可能从别处送字节，
        // 不验证会把内容写到错误偏移，产出错位的损坏文件（无 SHA 时静默通过）。
        let cr_start = resp
            .header("Content-Range")
            .and_then(|cr| cr.trim().strip_prefix("bytes "))
            .and_then(|s| s.split('-').next())
            .and_then(|s| s.trim().parse::<u64>().ok());
        if cr_start != Some(cur) {
            return Err(format!("分块 Content-Range 起始 {:?} 与请求偏移 {} 不符，已中止", cr_start, cur));
        }
        let mut f = fs::OpenOptions::new().write(true).open(part).map_err(|e| e.to_string())?;
        f.seek(SeekFrom::Start(cur)).map_err(|e| e.to_string())?;
        let mut reader = resp.into_reader();
        let mut buf = vec![0u8; 256 * 1024];
        loop {
            if cur > end {
                break; // 本块下满
            }
            if cancel.load(Ordering::Relaxed) || PAUSED.load(Ordering::Relaxed) {
                return Ok(false);
            }
            let want = ((end - cur + 1).min(buf.len() as u64)) as usize;
            let n = reader.read(&mut buf[..want]).map_err(|e| e.to_string())?;
            if n == 0 {
                break; // 提前断流
            }
            f.write_all(&buf[..n]).map_err(|e| e.to_string())?;
            cur += n as u64;
            progress[idx].store(cur - start, Ordering::Relaxed);
        }
        if cur > end {
            return Ok(true); // 完成（cur == end+1）
        }
        // 提前断流且未到块尾：重试剩余区间
        stall_attempts += 1;
        if stall_attempts < 6 {
            std::thread::sleep(Duration::from_secs(1));
            continue;
        }
        return Err("分块提前断流，重试耗尽".into());
    }
}

fn download_multipart(cfg: &Config, task: &TaskRef, dest: &Path, part: &Path, meta: &DlMeta, ri: &RangeInfo) -> Result<(), String> {
    let total = ri.total;
    let chunks = plan_chunks(total, MULTIPART_CONNS);
    // 预分配 .part：一次性占位到 total，各 worker seek 写各自区间，互不竞争 cursor
    {
        let f = fs::OpenOptions::new().create(true).write(true).truncate(true).open(part).map_err(|e| e.to_string())?;
        f.set_len(total).map_err(|e| e.to_string())?;
    }
    {
        let mut t = task.lock().unwrap();
        t.status = "下载中".into();
        t.total = total;
        t.downloaded = 0;
        if t.started_at.is_none() {
            t.started_at = Some(Instant::now());
        }
        t.local_path = Some(dest.to_path_buf());
    }
    let progress: Arc<Vec<AtomicU64>> = Arc::new((0..chunks.len()).map(|_| AtomicU64::new(0)).collect());
    let cancel = task.lock().unwrap().cancel.clone();
    let mut handles = Vec::new();
    for (i, &(start, end)) in chunks.iter().enumerate() {
        let cfg = cfg.clone();
        let url = meta.download_url.clone();
        let part = part.to_path_buf();
        let progress = progress.clone();
        let cancel = cancel.clone();
        handles.push(std::thread::spawn(move || download_chunk(&cfg, &url, &part, (start, end, i), &progress, &cancel)));
    }
    // 主线程轮询聚合进度（worker 自行响应 cancel/PAUSED）
    let mut t0 = Instant::now();
    let mut last = 0u64;
    while !handles.iter().all(|h| h.is_finished()) {
        let sum: u64 = progress.iter().map(|a| a.load(Ordering::Relaxed)).sum();
        let dt = t0.elapsed().as_secs_f64();
        if dt >= 0.5 {
            let mut t = task.lock().unwrap();
            t.downloaded = sum;
            t.speed = sum.saturating_sub(last) as f64 / dt;
            t0 = Instant::now();
            last = sum;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    let mut first_err = None;
    let mut all_completed = true;
    for h in handles {
        match h.join() {
            Ok(Ok(true)) => {}                      // 本块完成
            Ok(Ok(false)) => all_completed = false, // 被暂停/取消中断
            Ok(Err(e)) => {
                first_err.get_or_insert(e);
            }
            Err(_) => {
                first_err.get_or_insert_with(|| "分块线程异常退出".into());
            }
        }
    }
    if let Some(e) = first_err {
        let _ = fs::remove_file(part);
        return Err(e);
    }
    // 收尾判据是「所有块都自报完成」，不读全局 PAUSED——否则恢复把 PAUSED 翻回 false 后，
    // 主线程会把暂停时零填充的半成品 .part 误当完整文件 rename（无 SHA 时静默损坏入库）。
    if !all_completed {
        // 多块 .part 预分配等长、不精确续传：先删 .part 再置状态，确保恢复看到「已暂停」时
        // .part 已无，重下不会与本线程残留抢写同一文件。cancel 单调可安全读。
        let _ = fs::remove_file(part);
        let status = if cancel.load(Ordering::Relaxed) { "已取消" } else { "已暂停" };
        task.lock().unwrap().status = status.into();
        return Ok(());
    }
    // 纵深防御：所有块完成后，落盘大小必须 == total（抓二阶 bug 导致的长度异常；偏移错位由
    // 各 worker 的 Content-Range 校验拦截）。无 SHA 源也至少有这道兜底。
    let got_len = fs::metadata(part).map(|m| m.len()).unwrap_or(0);
    if got_len != total {
        let _ = fs::remove_file(part);
        return Err(format!("分块下载落盘 {} 字节 ≠ 预期 {}，已删除损坏文件，请重试", got_len, total));
    }
    // 全部成功：顺序读整个 .part 算一次 SHA256（按真实字节序，与分块完成顺序解耦）
    let expected = meta.expected_sha256.as_deref().filter(|s| !s.is_empty()).map(|s| s.to_lowercase());
    let mut verified = false;
    if let Some(exp) = &expected {
        let mut hasher = Sha256::new();
        let mut f = fs::File::open(part).map_err(|e| e.to_string())?;
        let mut hb = vec![0u8; 1024 * 1024];
        loop {
            let n = f.read(&mut hb).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            hasher.update(&hb[..n]);
        }
        let got = hex_str(&hasher.finalize());
        if &got != exp {
            let _ = fs::remove_file(part);
            return Err(format!(
                "SHA256 校验失败（期望 {}… 实得 {}…），已删除损坏文件，请重新下载",
                &exp[..12.min(exp.len())],
                &got[..12]
            ));
        }
        verified = true;
    }
    fs::rename(part, dest).map_err(|e| e.to_string())?;
    let mut t = task.lock().unwrap();
    t.status = "完成".into();
    t.speed = 0.0;
    t.verified = verified;
    t.downloaded = total;
    t.total = total;
    t.local_path = Some(dest.to_path_buf());
    t.completed_at = Some(Instant::now());
    let (source, url, desc, sha) = (t.source.clone(), t.download_url.clone(), t.desc.clone(), t.expected_sha256.clone());
    drop(t);
    write_info_sidecar(dest, &source, &url, &desc, sha.as_deref());
    record_task_to_index(&task.lock().unwrap());
    Ok(())
}

fn download_file(cfg: &Config, task: &TaskRef, filename: &str, subdir: &str, meta: &DlMeta) -> Result<(), String> {
    // 下载目标：用户选了下载目录(或 Desktop 主目录)时落到 <模型根>/<类型>（去掉 subdir 的 models/ 前缀），
    // 否则维持原行为 comfy_root/models/<类型>。解析逻辑抽到 resolve_dest_dir，与磁盘预检共用。
    let dest_dir = resolve_dest_dir(cfg, subdir);
    fs::create_dir_all(&dest_dir).map_err(|e| e.to_string())?;
    let dest = dest_dir.join(filename);
    let part = dest.with_extension(format!(
        "{}.part",
        dest.extension().and_then(|s| s.to_str()).unwrap_or("")
    ));
    if dest.exists() {
        // 正式文件已在则视为完成，并清掉陈旧 .part，避免续传后把完整文件覆盖掉
        let _ = fs::remove_file(&part);
        let sz = fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
        let mut t = task.lock().unwrap();
        t.status = "已存在".into();
        t.downloaded = sz;
        t.total = sz;
        t.local_path = Some(dest.clone());
        t.completed_at = Some(Instant::now());
        let (source, url, desc, sha) = (t.source.clone(), t.download_url.clone(), t.desc.clone(), t.expected_sha256.clone());
        drop(t);
        write_info_sidecar(&dest, &source, &url, &desc, sha.as_deref());
        record_task_to_index(&task.lock().unwrap());
        return Ok(());
    }
    let mut existing = fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
    // 多连接分块加速：仅对「全新下载(无 .part) + 用户未关 + 非 Civitai + 远端支持 Range + 够大」启用；
    // 任何不满足都落到下面的单连接路径（安全网，一字不改）。probe 失败也回落。
    if existing == 0 && cfg.multipart && meta.source != "civitai" {
        if let Some(ri) = probe_range(cfg, meta) {
            if should_use_multipart(&meta.source, ri.supports_range, ri.total) {
                return download_multipart(cfg, task, &dest, &part, meta, &ri);
            }
        }
    }
    // 边下边算 SHA256。预喂已有 .part 必须在发起 HTTP 请求【之前】完成：
    // 拿到响应后再读盘几十秒不消费 body，会触发服务端写停滞超时断连（大文件 HDD 续传必败）
    let expected = meta.expected_sha256.as_deref().filter(|s| !s.is_empty()).map(|s| s.to_lowercase());
    let mut hasher = expected.as_ref().map(|_| Sha256::new());
    if existing > 0 {
        if let Some(h) = hasher.as_mut() {
            {
        let mut t = task.lock().unwrap();
        t.status = "下载中".into();
        if t.started_at.is_none() {
            t.started_at = Some(Instant::now());
        }
        t.local_path = Some(dest.clone());
    }
            let mut pf = fs::File::open(&part).map_err(|e| e.to_string())?;
            let mut hb = vec![0u8; 1024 * 1024];
            loop {
                if task.lock().unwrap().cancel.load(Ordering::Relaxed) {
                    task.lock().unwrap().status = "已取消".into();
                    return Ok(());
                }
                let n = pf.read(&mut hb).map_err(|e| e.to_string())?;
                if n == 0 {
                    break;
                }
                h.update(&hb[..n]);
            }
        }
    }
    let mut req = agent(cfg).get(&meta.download_url);
    if meta.source == "civitai" && !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    if existing > 0 {
        req = req.set("Range", &format!("bytes={}-", existing));
    }
    task.lock().unwrap().status = "下载中".into();
    let resp = match req.call() {
        Ok(r) => r,
        Err(ureq::Error::Status(416, r)) if existing > 0 => {
            // Range 越界：.part 与远端总长一致说明上次只差 rename，直接收尾；否则删掉重下
            let total = r
                .header("Content-Range")
                .and_then(|v| v.rsplit('/').next())
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0);
            if total > 0 && existing == total {
                // .part 已是完整文件，收尾前用预喂好的哈希器校验（无需重读文件）
                if let (Some(h), Some(exp)) = (hasher.take(), expected.as_ref()) {
                    let got = hex_str(&h.finalize());
                    if &got != exp {
                        let _ = fs::remove_file(&part);
                        return Err("SHA256 校验失败，已删除损坏的临时文件，请重新下载".into());
                    }
                }
                fs::rename(&part, &dest).map_err(|e| e.to_string())?;
                let mut t = task.lock().unwrap();
                t.status = "完成".into();
                t.downloaded = existing;
                t.total = existing;
                t.verified = expected.is_some();
                t.local_path = Some(dest.clone());
                t.completed_at = Some(Instant::now());
                let (source, url, desc, sha) = (t.source.clone(), t.download_url.clone(), t.desc.clone(), t.expected_sha256.clone());
                drop(t);
                write_info_sidecar(&dest, &source, &url, &desc, sha.as_deref());
                record_task_to_index(&task.lock().unwrap());
                return Ok(());
            }
            let _ = fs::remove_file(&part);
            return Err("续传范围越界，已清除临时文件，请重新下载".into());
        }
        Err(e) => return Err(e.to_string()),
    };
    // 服务器忽略 Range 返回 200 时必须从头写：append 会把全量内容拼在残块后面造成静默损坏；
    // 预喂过的哈希器同样作废，重置后从零开始算
    if existing > 0 && resp.status() != 206 {
        existing = 0;
        hasher = expected.as_ref().map(|_| Sha256::new());
    }
    let clen = resp.header("Content-Length").and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
    if clen > 0 {
        task.lock().unwrap().total = existing + clen;
    }
    let mut reader = resp.into_reader();
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(existing > 0)
        .write(true)
        .truncate(existing == 0)
        .open(&part)
        .map_err(|e| e.to_string())?;
    let mut buf = vec![0u8; 256 * 1024];
    let mut downloaded = existing;
    let mut t0 = Instant::now();
    let mut last = existing;
    loop {
        if task.lock().unwrap().cancel.load(Ordering::Relaxed) {
            task.lock().unwrap().status = "已取消".into();
            return Ok(());
        }
        // 全局暂停：在分块边界干净退出，保留 .part，恢复时按 Range 续传
        if PAUSED.load(Ordering::Relaxed) {
            task.lock().unwrap().status = "已暂停".into();
            return Ok(());
        }
        let n = reader.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        file.write_all(&buf[..n]).map_err(|e| e.to_string())?;
        if let Some(h) = hasher.as_mut() {
            h.update(&buf[..n]);
        }
        downloaded += n as u64;
        let dt = t0.elapsed().as_secs_f64();
        if dt >= 1.0 {
            let mut t = task.lock().unwrap();
            t.downloaded = downloaded;
            t.speed = (downloaded - last) as f64 / dt;
            t0 = Instant::now();
            last = downloaded;
        } else {
            task.lock().unwrap().downloaded = downloaded;
        }
    }
    drop(file);
    let mut verified = false;
    if let (Some(h), Some(exp)) = (hasher, expected.as_ref()) {
        let got = hex_str(&h.finalize());
        if &got != exp {
            let _ = fs::remove_file(&part);
            return Err(format!(
                "SHA256 校验失败（期望 {}… 实得 {}…），已删除损坏文件，请重新下载",
                &exp[..12.min(exp.len())],
                &got[..12]
            ));
        }
        verified = true;
    }
    fs::rename(&part, &dest).map_err(|e| e.to_string())?;
    let mut t = task.lock().unwrap();
    t.status = "完成".into();
    t.speed = 0.0;
    t.verified = verified;
    if t.total == 0 {
        t.total = downloaded;
    }
    t.downloaded = downloaded;
    t.local_path = Some(dest.clone());
    t.completed_at = Some(Instant::now());
    let (source, url, desc, sha) = (t.source.clone(), t.download_url.clone(), t.desc.clone(), t.expected_sha256.clone());
    drop(t);
    write_info_sidecar(&dest, &source, &url, &desc, sha.as_deref());
    record_task_to_index(&task.lock().unwrap());
    Ok(())
}

// ============ 模型库扫描 ============
const MODEL_DIRS: [&str; 16] = [
    "models/checkpoints", "models/loras", "models/vae", "models/unet",
    "models/diffusion_models", "models/text_encoders", "models/clip",
    "models/clip_vision", "models/embeddings", "models/controlnet",
    "models/upscale_models", "models/style_models", "models/ultralytics",
    "models/sams", "models/ipadapter", "models/insightface",
];
const MODEL_EXTS: [&str; 7] = [".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft"];

// 模型识别状态（懒加载，按需对单个文件算哈希 + Civitai 反查）
#[derive(Clone, PartialEq)]
enum Ident {
    Unknown,
    Working,
    Found { model_name: String, version_name: String, version_id: i64, model_type: String, model_id: i64, base: String },
    NotFound, // 哈希算出但 Civitai 无记录（本地训练/HF 来源）
    Failed(String),
}

#[derive(Clone)]
struct LibFile {
    name: String,
    path: PathBuf,
    size: u64,
    preview: Option<String>, // file:// 预览图 URI
    ident: Ident,
}

#[derive(Clone)]
struct LibDir {
    key: String, // 逻辑目录名 models/loras
    files: Vec<LibFile>,
}

// 模型更新检测结果缓存
#[derive(Clone)]
struct UpdateInfo {
    latest_vid: i64,
    latest_name: String,
}

// 模型可能的伴随文件（预览图 + sidecar 元数据），删除模型时一并清理
fn sidecar_paths(model_path: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let (Some(stem), Some(dir)) = (model_path.file_stem(), model_path.parent()) {
        let stem = stem.to_string_lossy();
        for suffix in [
            ".png", ".preview.png", ".jpeg", ".jpg", ".webp", // 预览图
            ".civitai.info", ".json", ".cm-info.json",         // sidecar 元数据
        ] {
            let p = dir.join(format!("{}{}", stem, suffix));
            if p.exists() {
                out.push(p);
            }
        }
    }
    out
}

// 找模型文件的同名预览图（ComfyUI / civitai 下载器惯例：foo.safetensors → foo.png / foo.preview.png）
fn find_preview(model_path: &Path) -> Option<String> {
    let stem = model_path.file_stem()?.to_string_lossy().into_owned();
    let dir = model_path.parent()?;
    for cand in [
        format!("{}.png", stem),
        format!("{}.preview.png", stem),
        format!("{}.jpeg", stem),
        format!("{}.jpg", stem),
        format!("{}.webp", stem),
    ] {
        let p = dir.join(&cand);
        if p.exists() {
            // egui_extras 的 file loader 需要 file:// URI；统一用正斜杠
            return Some(format!("file://{}", p.to_string_lossy().replace('\\', "/")));
        }
    }
    None
}

// a111/comfyui 块里的类型名 → 我们的 models/* 逻辑名
fn extra_path_key(k: &str) -> Option<&'static str> {
    match k {
        "checkpoints" => Some("models/checkpoints"),
        "loras" => Some("models/loras"),
        "vae" => Some("models/vae"),
        "unet" | "diffusion_models" => Some("models/unet"),
        "text_encoders" | "clip" => Some("models/text_encoders"),
        "clip_vision" => Some("models/clip_vision"),
        "controlnet" => Some("models/controlnet"),
        "embeddings" => Some("models/embeddings"),
        "upscale_models" => Some("models/upscale_models"),
        "style_models" => Some("models/style_models"),
        _ => None,
    }
}

fn join_extra(out: &mut Vec<(String, PathBuf)>, logical: &str, base: &str, rel: &str) {
    let rel = rel.trim().trim_matches('"').trim_matches('\'');
    if rel.is_empty() {
        return;
    }
    let p = if Path::new(rel).is_absolute() || base.is_empty() {
        PathBuf::from(rel)
    } else {
        Path::new(base).join(rel)
    };
    out.push((logical.to_string(), p));
}

// 解析 ComfyUI 的 extra_model_paths.yaml，返回 [(逻辑目录名, 路径)]，让模型放在别的盘也能被管理。
// 手解析（避免引入 YAML 依赖），支持官方默认的块标量(|)多路径写法：
//   text_encoders: |
//       models/text_encoders/
//       models/clip/
fn parse_extra_paths(yaml: &str) -> Vec<(String, PathBuf)> {
    let lines: Vec<&str> = yaml.lines().collect();
    let indent_of = |s: &str| s.len() - s.trim_start().len();
    let mut out = Vec::new();
    let mut base = String::new();
    let mut i = 0;
    while i < lines.len() {
        let raw = lines[i];
        let t = raw.trim();
        if t.is_empty() || t.starts_with('#') {
            i += 1;
            continue;
        }
        let indent = indent_of(raw);
        if indent == 0 {
            base.clear(); // 新的来源块
            i += 1;
            continue;
        }
        // split_once 只在首个冒号切分，Windows 盘符冒号(D:/...)留在值里
        if let Some((k, v)) = t.split_once(':') {
            let k = k.trim();
            let v = v.split(" #").next().unwrap_or(v).trim(); // 去行内注释
            if k == "base_path" {
                base = v.trim_matches('"').trim_matches('\'').to_string();
                i += 1;
                continue;
            }
            let logical = extra_path_key(k);
            let is_block = v.is_empty() || v == "|" || v == "|-" || v == ">" || v == ">-";
            if !is_block {
                if let Some(lg) = logical {
                    join_extra(&mut out, lg, &base, v);
                }
                i += 1;
                continue;
            }
            // 块标量：后续比 key 更深缩进的行都是该类型的路径
            let mut j = i + 1;
            while j < lines.len() {
                let r = lines[j];
                let tt = r.trim();
                if tt.is_empty() {
                    j += 1;
                    continue;
                }
                if indent_of(r) <= indent {
                    break;
                }
                if !tt.starts_with('#') {
                    if let Some(lg) = logical {
                        join_extra(&mut out, lg, &base, tt);
                    }
                }
                j += 1;
            }
            i = j;
            continue;
        }
        i += 1;
    }
    out
}

// 收集所有要扫描的 (逻辑目录名, 绝对路径)：comfy_root 下的标准目录 + extra_model_paths.yaml 里的额外路径
// ============ ComfyUI Desktop（electron / standalone）适配 ============
// Desktop 版不是 git 源码版：源码、内置 python、custom_nodes、模型目录都在别处，
// 配置写在 %APPDATA%\Comfy Desktop\。这里读取这些配置定位真实路径，让管理/库功能对其生效。
#[derive(Clone, Default)]
struct DesktopInfo {
    install_path: PathBuf,      // installations.json 的 installPath
    source_dir: PathBuf,        // install_path\ComfyUI（main.py 所在）
    python_exe: PathBuf,        // install_path\standalone-env\python.exe（内置便携 python）
    custom_nodes_dir: PathBuf,  // source_dir\custom_nodes
    model_dirs: Vec<PathBuf>,   // settings.json 的 modelsDirs（首个为主目录）
    port: Option<u16>,
    version: String,
    launch_args: String,
    app_exe: Option<PathBuf>,   // electron 应用本体（best-effort）
}

// Windows: %APPDATA%\Comfy Desktop；macOS: ~/Library/Application Support/Comfy Desktop；Linux: ~/.config/Comfy Desktop
fn desktop_userdata() -> Option<PathBuf> {
    let base = if cfg!(windows) {
        std::env::var_os("APPDATA").map(PathBuf::from)
    } else if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library/Application Support"))
    } else {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
    };
    let p = base?.join("Comfy Desktop");
    if p.is_dir() { Some(p) } else { None }
}

fn read_json_file(p: &Path) -> Option<Value> {
    let body = fs::read_to_string(p).ok()?;
    serde_json::from_str(body.trim_start_matches('\u{feff}')).ok()
}

fn find_desktop_exe(cfg: &Config) -> Option<PathBuf> {
    let root = expand_root(&cfg.comfy_root);
    let mut cands: Vec<PathBuf> = Vec::new();
    if cfg!(windows) {
        cands.push(root.join("Comfy Desktop").join("Comfy Desktop.exe"));
        cands.push(root.join("Comfy Desktop.exe"));
        if let Some(la) = std::env::var_os("LOCALAPPDATA") {
            let la = PathBuf::from(la);
            cands.push(la.join("Programs").join("@comfyorgcomfyui-electron").join("ComfyUI.exe"));
            cands.push(la.join("Programs").join("Comfy Desktop").join("Comfy Desktop.exe"));
        }
    } else if cfg!(target_os = "macos") {
        // macOS: /Applications/ComfyUI.app 或 ~/Applications/ComfyUI.app
        cands.push(PathBuf::from("/Applications/ComfyUI.app"));
        if let Some(home) = std::env::var_os("HOME") {
            cands.push(PathBuf::from(home).join("Applications").join("ComfyUI.app"));
        }
    } else {
        // Linux: ~/.local/share/ComfyUI 或 /usr/lib/comfyui
        if let Some(home) = std::env::var_os("HOME") {
            cands.push(PathBuf::from(home).join(".local").join("share").join("ComfyUI"));
        }
        cands.push(PathBuf::from("/usr/lib/comfyui"));
    }
    cands.into_iter().find(|p| p.exists())
}

fn detect_desktop(cfg: &Config) -> Option<DesktopInfo> {
    let ud = desktop_userdata()?;
    let mut info = DesktopInfo::default();
    // settings.json → modelsDirs（真实模型目录，含可能不在 comfy_root 下的主目录）
    if let Some(v) = read_json_file(&ud.join("settings.json")) {
        if let Some(arr) = v.get("modelsDirs").and_then(|x| x.as_array()) {
            for d in arr {
                if let Some(s) = d.as_str() {
                    let p = PathBuf::from(s);
                    if p.is_dir() {
                        info.model_dirs.push(p);
                    }
                }
            }
        }
    }
    // installations.json → 第一个带 installPath 的本地安装（跳过 cloud）
    if let Some(v) = read_json_file(&ud.join("installations.json")) {
        if let Some(arr) = v.as_array() {
            for it in arr {
                if let Some(ip) = it.get("installPath").and_then(|x| x.as_str()) {
                    info.install_path = PathBuf::from(ip);
                    // 优先用 ComfyUI 应用版本(comfyVersion.baseTag)，否则退回 standalone 环境版本
                    info.version = it
                        .get("comfyVersion")
                        .and_then(|c| c.get("baseTag"))
                        .and_then(|x| x.as_str())
                        .or_else(|| it.get("version").and_then(|x| x.as_str()))
                        .unwrap_or("")
                        .to_string();
                    info.launch_args = it.get("launchArgs").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    break;
                }
            }
        }
    }
    if info.install_path.as_os_str().is_empty() {
        return None;
    }
    info.source_dir = info.install_path.join("ComfyUI");
    // 验证确实是 Desktop 源码布局，否则不当作 Desktop
    if !info.source_dir.join("main.py").is_file() {
        return None;
    }
    info.python_exe = if cfg!(windows) {
        info.install_path.join("standalone-env").join("python.exe")
    } else {
        info.install_path.join("standalone-env").join("bin").join("python3")
    };
    info.custom_nodes_dir = info.source_dir.join("custom_nodes");
    // port-locks\port-NNNN.json → 端口
    if let Ok(rd) = fs::read_dir(ud.join("port-locks")) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if let Some(n) = name.strip_prefix("port-").and_then(|s| s.strip_suffix(".json")) {
                if let Ok(p) = n.parse::<u16>() {
                    info.port = Some(p);
                    break;
                }
            }
        }
    }
    info.app_exe = find_desktop_exe(cfg);
    Some(info)
}

// custom_nodes 目录：Desktop 安装走其真实路径，否则 comfy_root\custom_nodes
fn custom_nodes_dir(cfg: &Config) -> PathBuf {
    if let Some(d) = detect_desktop(cfg) {
        return d.custom_nodes_dir;
    }
    expand_root(&cfg.comfy_root).join("custom_nodes")
}

// 本工具下载模型的目标模型根目录（已是 .../models）：用户显式选 > Desktop 主目录 > None(回退 comfy_root\models)
fn effective_download_models_root(cfg: &Config) -> Option<PathBuf> {
    let dr = cfg.download_root.trim();
    if !dr.is_empty() {
        return Some(expand_root(dr));
    }
    detect_desktop(cfg).and_then(|d| d.model_dirs.into_iter().next())
}

fn scan_targets(cfg: &Config) -> Vec<(String, PathBuf)> {
    let root = expand_root(&cfg.comfy_root);
    let mut targets: Vec<(String, PathBuf)> = MODEL_DIRS
        .iter()
        .map(|d| {
            let sub: PathBuf = d.split('/').collect();
            (d.to_string(), root.join(sub))
        })
        .collect();
    let yaml_path = root.join("extra_model_paths.yaml");
    if let Ok(text) = fs::read_to_string(&yaml_path) {
        for (key, path) in parse_extra_paths(&text) {
            targets.push((key, path));
        }
    }
    // Desktop 的真实模型目录（可能在别的盘/路径，comfy_root 扫不到）：按类型子目录展开，
    // key 与标准目录一致，scan_library 会按 key 合并、按规范化路径去重
    if let Some(d) = detect_desktop(cfg) {
        for md in &d.model_dirs {
            for entry in MODEL_DIRS.iter() {
                let ty = entry.strip_prefix("models/").unwrap_or(entry);
                targets.push((entry.to_string(), md.join(ty)));
            }
        }
    }
    targets
}

fn scan_library(cfg: &Config) -> Vec<LibDir> {
    let exts = MODEL_EXTS;
    // 同一逻辑目录可能对应多个物理路径（extra paths），合并到一个 LibDir 下。
    // 按规范化后的绝对路径全局去重：extra path 与标准目录指向同一物理文件时只算一次，
    // 否则磁盘统计翻倍、正常文件全被误标「疑似重复」
    let mut merged: Vec<LibDir> = Vec::new();
    let mut seen: std::collections::HashSet<PathBuf> = std::collections::HashSet::new();
    for (key, full) in scan_targets(cfg) {
        let mut files = Vec::new();
        if let Ok(rd) = fs::read_dir(&full) {
            let mut v: Vec<_> = rd.flatten().collect();
            v.sort_by_key(|e| e.file_name());
            for e in v {
                if e.file_type().map(|t| t.is_file()).unwrap_or(false) {
                    let name = e.file_name().to_string_lossy().to_string();
                    if exts.iter().any(|x| name.to_lowercase().ends_with(x)) {
                        let path = e.path();
                        let canon = fs::canonicalize(&path).unwrap_or_else(|_| path.clone());
                        if !seen.insert(canon) {
                            continue; // 已从别的逻辑目录/extra 路径收录过同一物理文件
                        }
                        let size = e.metadata().map(|m| m.len()).unwrap_or(0);
                        files.push(LibFile {
                            preview: find_preview(&path),
                            name,
                            path,
                            size,
                            ident: Ident::Unknown,
                        });
                    }
                }
            }
        }
        if files.is_empty() {
            continue;
        }
        if let Some(d) = merged.iter_mut().find(|d| d.key == key) {
            d.files.extend(files);
        } else {
            merged.push(LibDir { key, files });
        }
    }
    merged
}

// ============ 哈希缓存（避免重复对多 GB 文件算 SHA256）============
#[derive(Serialize, Deserialize, Default, Clone)]
struct HashCache {
    // key = 绝对路径；value = (size, mtime_secs, sha256)
    entries: std::collections::HashMap<String, (u64, u64, String)>,
}

fn hash_cache_path() -> PathBuf {
    config_path().with_file_name("hash-cache.json")
}

fn load_hash_cache() -> HashCache {
    fs::read_to_string(hash_cache_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_hash_cache(c: &HashCache) {
    if let Ok(s) = serde_json::to_string(c) {
        let _ = fs::write(hash_cache_path(), s);
    }
}

// 删除文件后清掉其哈希缓存条目，避免缓存无限增长 + 同路径新文件误命中旧哈希
fn forget_hash_cache(path: &Path) {
    let _g = HASH_CACHE_LOCK.lock().unwrap();
    let mut cache = load_hash_cache();
    if cache.entries.remove(&path.to_string_lossy().to_string()).is_some() {
        save_hash_cache(&cache);
    }
}

fn file_mtime_secs(p: &Path) -> u64 {
    fs::metadata(p)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// 缓存文件读改写的全局锁：多个识别线程并发时避免互相覆盖 hash-cache.json 丢更新/损坏
static HASH_CACHE_LOCK: Mutex<()> = Mutex::new(());

// 取文件 SHA256：命中缓存（路径+大小+mtime 都匹配）直接返回，否则算并写回缓存。
// 慢哈希在锁外进行，仅查询与合并写入持锁，保证并发安全又不串行化磁盘读取
fn cached_file_hash(p: &Path) -> Result<String, String> {
    let size = fs::metadata(p).map(|m| m.len()).map_err(|e| e.to_string())?;
    let mtime = file_mtime_secs(p);
    let key = p.to_string_lossy().to_string();
    {
        let _g = HASH_CACHE_LOCK.lock().unwrap();
        if let Some((s, m, h)) = load_hash_cache().entries.get(&key) {
            if *s == size && *m == mtime {
                return Ok(h.clone());
            }
        }
    }
    let mut f = fs::File::open(p).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    let hash = hex_str(&hasher.finalize());
    {
        // 持锁期间重新读取再插入，避免覆盖其他线程刚写入的条目
        let _g = HASH_CACHE_LOCK.lock().unwrap();
        let mut cache = load_hash_cache();
        cache.entries.insert(key, (size, mtime, hash.clone()));
        save_hash_cache(&cache);
    }
    Ok(hash)
}

// ============ 运行中的 ComfyUI 实例联动 ============
// ComfyUI 是本机服务，不应走系统代理（代理会把 127.0.0.1 也拦掉），用独立无代理 agent
fn local_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(3))
        .timeout_read(Duration::from_secs(8))
        .build()
}

// 规整服务地址：用户常只填 host:port，没有 scheme ureq 会报费解的 InvalidUrl，这里补 http://
fn comfy_base(cfg: &Config) -> String {
    let u = cfg.comfy_url.trim().trim_end_matches('/');
    if u.starts_with("http://") || u.starts_with("https://") {
        u.to_string()
    } else {
        format!("http://{}", u)
    }
}

// GET /system_stats → ComfyUI 版本（兼可作连接探测）
fn comfy_system_stats(cfg: &Config) -> Result<String, String> {
    let url = format!("{}/system_stats", comfy_base(cfg));
    let body = local_agent().get(&url).call().map_err(|e| friendly_err(e.to_string()))?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let ver = v.get("system").and_then(|s| s.get("comfyui_version")).and_then(|x| x.as_str()).unwrap_or("unknown");
    Ok(ver.to_string())
}

// /object_info 里加载器输入字段名 → 逻辑目录。带目录维度才能避免「不同类别同名文件」误判
fn object_info_field_dir(field: &str) -> Option<&'static str> {
    let f = field.to_lowercase();
    if f.contains("clip_vision") {
        Some("models/clip_vision")
    } else if f.contains("ckpt") {
        Some("models/checkpoints")
    } else if f.contains("lora") {
        Some("models/loras")
    } else if f.contains("vae") {
        Some("models/vae")
    } else if f.contains("control_net") || f.contains("controlnet") {
        Some("models/controlnet")
    } else if f.contains("unet") || f.contains("diffusion") {
        Some("models/unet")
    } else if f.contains("style_model") {
        Some("models/style_models")
    } else if f.contains("upscale") {
        Some("models/upscale_models")
    } else if f.contains("clip") || f.contains("t5") || f.contains("text_encoder") {
        Some("models/text_encoders")
    } else {
        None
    }
}

// GET /object_info → 收集运行中的 ComfyUI 真正"看得见"的模型，按 (逻辑目录, 小写basename) 记录。
// 结构化解析加载器节点的下拉枚举（input.required/optional 里 [[选项...],{}] 形式），带目录维度去重，
// 比扫目录更权威（覆盖 symlink / 额外路径），又不会因跨类别同名文件误判
fn comfy_known_models(cfg: &Config) -> Result<std::collections::HashSet<(String, String)>, String> {
    let url = format!("{}/object_info", comfy_base(cfg));
    let body = local_agent().get(&url).call().map_err(|e| friendly_err(e.to_string()))?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let mut set = std::collections::HashSet::new();
    if let Some(nodes) = v.as_object() {
        for node in nodes.values() {
            let Some(input) = node.get("input") else { continue };
            for sect in ["required", "optional"] {
                let Some(fields) = input.get(sect).and_then(|x| x.as_object()) else { continue };
                for (fname, fdef) in fields {
                    let Some(dir) = object_info_field_dir(fname) else { continue };
                    // fdef 形如 [ [选项字符串...], {meta} ]，选项在 [0]
                    if let Some(opts) = fdef.as_array().and_then(|a| a.first()).and_then(|x| x.as_array()) {
                        for o in opts {
                            if let Some(s) = o.as_str() {
                                if is_model_filename(s) {
                                    let base = s.replace('\\', "/").rsplit('/').next().unwrap_or(s).to_lowercase();
                                    set.insert((dir.to_string(), base));
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(set)
}

// ============ 自定义节点（custom_nodes）管理 ============
#[derive(Clone)]
struct NodeInfo {
    name: String,    // 目录名（去掉 .disabled 后缀）
    path: PathBuf,   // 实际目录路径
    is_git: bool,
    rev: String,     // "分支 @ 短sha"，非 git 仓库为空
    disabled: bool,  // 目录以 .disabled 结尾（Manager 的禁用约定）
}

// 跑 git 子命令并返回 (成功→stdout/否则stderr)；失败带上 stderr 便于排查
fn git_run(repo: &Path, args: &[&str]) -> Result<String, String> {
    let out = std::process::Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map_err(|e| format!("无法执行 git：{}", e))?;
    let so = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let se = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if out.status.success() {
        Ok(if so.is_empty() { se } else { so })
    } else {
        Err(if se.is_empty() { so } else { se })
    }
}

// ComfyUI-Manager 注册表里的一个节点条目
#[derive(Clone)]
struct RegistryNode {
    title: String,
    reference: String, // git 仓库 URL（克隆源）
    author: String,
    description: String,
    search_lc: String, // title+author+description 的小写拼接，供过滤免每帧重复分配
}

// 粗校验 git 仓库 URL：http(s):// 或 git@ 开头且有内容
fn is_git_url(url: &str) -> bool {
    let u = url.trim();
    (u.starts_with("http://") || u.starts_with("https://") || u.starts_with("git@")) && u.len() > 10
}

// 从 git URL 推导克隆后的目录名：取末段，去掉 .git 与末尾斜杠
fn repo_dir_name(url: &str) -> String {
    let s = url.trim().trim_end_matches('/');
    let last = s.rsplit(['/', ':']).next().unwrap_or(s);
    last.trim_end_matches(".git").to_string()
}

// 安全的克隆目录名：必须是 custom_nodes 的直接子目录。拒绝空 / . / .. / 含分隔符或冒号
// 的退化结果，防止 join 出的路径逃出 custom_nodes（reference 可能来自网络注册表）。
fn safe_node_name(url: &str) -> Option<String> {
    let name = repo_dir_name(url);
    if name.is_empty() || name == "." || name == ".." || name.contains(['/', '\\', ':']) {
        return None;
    }
    Some(name)
}

// 解析 ComfyUI-Manager 的 custom-node-list.json（纯函数，便于单测）。
// 只保留有可克隆 reference URL 的条目。
fn parse_node_registry(body: &str) -> Vec<RegistryNode> {
    let Ok(v) = serde_json::from_str::<Value>(body) else {
        return Vec::new();
    };
    let Some(arr) = v.get("custom_nodes").and_then(|x| x.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|n| {
            let reference = n.get("reference").and_then(|x| x.as_str()).unwrap_or("").trim().to_string();
            if !is_git_url(&reference) {
                return None;
            }
            let title = n.get("title").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let author = n.get("author").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let description = n.get("description").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let search_lc = format!("{} {} {}", title, author, description).to_lowercase();
            Some(RegistryNode { title, reference, author, description, search_lc })
        })
        .collect()
}

// 拉取 ComfyUI-Manager 官方节点注册表（公开 raw，走带代理的 agent）
fn fetch_node_registry(cfg: &Config) -> Result<Vec<RegistryNode>, String> {
    let url = "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/custom-node-list.json";
    let body = agent(cfg).get(url).call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let list = parse_node_registry(&body);
    if list.is_empty() {
        return Err("注册表为空或解析失败".into());
    }
    Ok(list)
}

// 扫描 custom_nodes 目录，列出每个节点及其 git 状态
fn scan_custom_nodes(cfg: &Config) -> Vec<NodeInfo> {
    let dir = custom_nodes_dir(cfg);
    let mut out = Vec::new();
    let Ok(rd) = fs::read_dir(&dir) else { return out; };
    for e in rd.flatten() {
        let p = e.path();
        if !p.is_dir() {
            continue;
        }
        let raw = e.file_name().to_string_lossy().to_string();
        if raw == "__pycache__" || raw == ".disabled" {
            continue;
        }
        let disabled = raw.ends_with(".disabled");
        let name = raw.trim_end_matches(".disabled").to_string();
        let is_git = p.join(".git").exists();
        let rev = if is_git {
            let branch = git_run(&p, &["rev-parse", "--abbrev-ref", "HEAD"]).unwrap_or_default();
            let sha = git_run(&p, &["rev-parse", "--short", "HEAD"]).unwrap_or_default();
            if branch.is_empty() {
                sha
            } else {
                format!("{} @ {}", branch, sha)
            }
        } else {
            String::new()
        };
        out.push(NodeInfo { name, path: p, is_git, rev, disabled });
    }
    out.sort_by_key(|n| n.name.to_lowercase());
    out
}

// 查询运行中 ComfyUI 的 ComfyUI-Manager 版本（只读探测，接口随版本变化，按 Value 宽松解析）
fn comfy_manager_version(cfg: &Config) -> Result<String, String> {
    let base = comfy_base(cfg);
    // 不同 Manager 版本路径不一，依次尝试
    for path in ["/api/manager/version", "/manager/version"] {
        let url = format!("{}{}", base, path);
        if let Ok(resp) = local_agent().get(&url).call() {
            if let Ok(body) = resp.into_string() {
                let body = body.trim();
                if body.is_empty() {
                    continue;
                }
                // 可能是纯文本版本号，也可能是 JSON
                if let Ok(v) = serde_json::from_str::<Value>(body) {
                    let ver = v
                        .get("version")
                        .and_then(|x| x.as_str())
                        .or_else(|| v.as_str())
                        .unwrap_or(body);
                    return Ok(ver.to_string());
                }
                return Ok(body.trim_matches('"').to_string());
            }
        }
    }
    Err("未获取到 Manager 版本（请确认 ComfyUI 正在运行且已装 ComfyUI-Manager）".into())
}

// 识别单个文件：算哈希（带缓存）→ Civitai by-hash 反查
fn identify_one(cfg: &Config, path: &Path) -> Ident {
    match cached_file_hash(path) {
        Ok(h) => civitai_by_hash(cfg, &h),
        Err(e) => Ident::Failed(e),
    }
}

// Civitai by-hash 反查：本地模型 → 是什么模型/哪个版本
fn civitai_by_hash(cfg: &Config, sha256: &str) -> Ident {
    let url = format!("https://{}/api/v1/model-versions/by-hash/{}", cfg.civitai_host, sha256);
    let mut req = agent(cfg).get(&url);
    if !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    match req.call() {
        Ok(resp) => {
            let body = match resp.into_string() {
                Ok(b) => b,
                Err(e) => return Ident::Failed(e.to_string()),
            };
            // 200 但 body 不是合法 JSON（代理插页/空响应等）是瞬时错误，判 Failed 可重试；
            // 只有明确的 404 才是「Civitai 确无此模型」
            let v: Value = match serde_json::from_str(&body) {
                Ok(v) => v,
                Err(_) => return Ident::Failed("响应异常（非 JSON），可重试".into()),
            };
            match v.get("modelId").and_then(|x| x.as_i64()) {
                Some(mid) => Ident::Found {
                    model_name: v.get("model").and_then(|m| m.get("name")).and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    version_name: v.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    version_id: v.get("id").and_then(|x| x.as_i64()).unwrap_or(0),
                    model_type: v.get("model").and_then(|m| m.get("type")).and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    model_id: mid,
                    base: v.get("baseModel").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                },
                None => Ident::NotFound,
            }
        }
        Err(ureq::Error::Status(404, _)) => Ident::NotFound,
        Err(e) => Ident::Failed(friendly_err(e.to_string())),
    }
}

// 将 ISO 8601/RFC 3339 时间字符串统一为可比较形式（补齐毫秒位）
fn normalize_date(s: &str) -> String {
    let Some(prefix) = s.strip_suffix('Z') else {
        return s.to_string();
    };
    if let Some((base, frac)) = prefix.split_once('.') {
        let mut frac = frac.to_string();
        while frac.len() < 3 {
            frac.push('0');
        }
        if frac.len() > 3 {
            frac.truncate(3);
        }
        format!("{}.{}{}", base, frac, 'Z')
    } else {
        format!("{}.000Z", prefix)
    }
}

fn ver_name(ver: &Value) -> &str {
    ver.get("name").and_then(|x| x.as_str()).unwrap_or("")
}
fn ver_base(ver: &Value) -> &str {
    ver.get("baseModel").and_then(|x| x.as_str()).unwrap_or("")
}

// 查询 Civitai 模型最新版本：在当前版本同基模/同变体的版本里，按 createdAt 找最新。
// 若当前版本已不在列表中，则按当前基模筛选后取最新。
fn civitai_model_latest_version(cfg: &Config, model_id: i64, current_vid: i64, current_base: &str) -> Result<(i64, String, String), String> {
    let url = format!("https://{}/api/v1/models/{}", cfg.civitai_host, model_id);
    let mut req = agent(cfg).get(&url);
    if !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    let body = req.call().map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let versions = v
        .get("modelVersions")
        .and_then(|x| x.as_array())
        .cloned()
        .ok_or("该模型无版本信息")?;
    if versions.is_empty() {
        return Err("该模型无版本信息".into());
    }

    let ver_id = |ver: &Value| ver.get("id").and_then(|x| x.as_i64()).unwrap_or(0);
    let ver_date = |ver: &Value| ver.get("createdAt").and_then(|x| x.as_str()).map(normalize_date);

    let current = versions.iter().find(|ver| ver_id(ver) == current_vid);

    if let Some(cur) = current {
        let cur_date = ver_date(cur);
        let cur_base = ver_base(cur);

        let mut best: Option<&Value> = None;
        let mut best_date: Option<String> = None;
        for ver in &versions {
            let vid = ver_id(ver);
            if vid == 0 || vid == current_vid {
                continue;
            }
            let Some(d) = ver_date(ver) else { continue };
            if let Some(ref cd) = cur_date {
                if d <= *cd {
                    continue;
                }
            }
            if !cur_base.is_empty() && ver_base(ver) != cur_base {
                continue;
            }
            if best_date.as_ref().map(|bd| d > *bd).unwrap_or(true) {
                best = Some(ver);
                best_date = Some(d);
            }
        }
        if let Some(ver) = best {
            let vid = ver_id(ver);
            return Ok((vid, ver_name(ver).to_string(), format!("https://{}/api/download/models/{}", cfg.civitai_host, vid)));
        }
        // 当前变体已是最新，把当前版本作为“最新”返回，让 UI 显示“已是最新”
        let vid = ver_id(cur);
        return Ok((vid, ver_name(cur).to_string(), format!("https://{}/api/download/models/{}", cfg.civitai_host, vid)));
    }

    // 当前版本已不在 Civitai 列表中：优先按当前基模筛选，再按发布时间取最新
    let mut best: Option<&Value> = None;
    let mut best_date: Option<String> = None;
    for ver in &versions {
        let vid = ver_id(ver);
        if vid == 0 {
            continue;
        }
        if !current_base.is_empty() && ver_base(ver) != current_base {
            continue;
        }
        let Some(d) = ver_date(ver) else { continue };
        if best_date.as_ref().map(|bd| d > *bd).unwrap_or(true) {
            best = Some(ver);
            best_date = Some(d);
        }
    }
    let ver = best.ok_or("该模型无有效版本")?;
    let vid = ver_id(ver);
    Ok((vid, ver_name(ver).to_string(), format!("https://{}/api/download/models/{}", cfg.civitai_host, vid)))
}

// ============ 工作流缺失模型分析 ============
#[derive(Clone)]
struct WfModel {
    name: String,
    dir: String,
    found_at: String,        // 本地路径，空 = 本地缺失
    in_comfy: bool,          // 运行中的 ComfyUI 实例能看到（即便本地扫描没扫到）
    dl: Option<DlMeta>,      // 本地缺失时，若知道精确下载源，可一键补齐
    wf_hash: Option<String>, // 工作流内嵌的 SHA256（若有），用于 Civitai by-hash 精确反查
}

// 缺失项「选择下载源」弹窗状态（多候选/低置信时让用户挑）
#[derive(Clone)]
struct ResolvePickState {
    name: String, // 落盘文件名（缺失项 basename）
    dir: String,  // 目标子目录
    candidates: Vec<ResolveCandidate>,
}

fn is_model_filename(s: &str) -> bool {
    // 排除 Note/说明节点里的 URL 和整段注释文本（它们也可能以模型扩展名结尾）
    if s.len() > 160 || s.contains("://") || s.contains('\n') || s.contains('\r') {
        return false;
    }
    let l = s.to_lowercase();
    MODEL_EXTS.iter().any(|e| l.ends_with(e))
}

// ComfyUI 加载器节点类型 → 模型目录（按子串匹配以兼容自定义节点变体，顺序敏感：CLIPVision 先于 CLIP）
fn node_type_to_dir(t: &str) -> Option<&'static str> {
    let s = t.to_lowercase();
    if s.contains("clipvision") || s.contains("clip_vision") {
        Some("models/clip_vision")
    } else if s.contains("lora") {
        Some("models/loras")
    } else if s.contains("checkpoint") {
        Some("models/checkpoints")
    } else if s.contains("vae") {
        Some("models/vae")
    } else if s.contains("controlnet") {
        Some("models/controlnet")
    } else if s.contains("upscale") {
        Some("models/upscale_models")
    } else if s.contains("unet") || s.contains("diffusion") {
        Some("models/unet")
    } else if s.contains("style") {
        Some("models/style_models")
    } else if s.contains("clip") || s.contains("t5") || s.contains("textencoder") || s.contains("text_encoder") {
        Some("models/text_encoders")
    } else {
        None
    }
}

fn walk_strings<'a>(v: &'a Value, f: &mut dyn FnMut(&'a str)) {
    match v {
        Value::String(s) => f(s),
        Value::Array(a) => {
            for x in a {
                walk_strings(x, f);
            }
        }
        Value::Object(o) => {
            for x in o.values() {
                walk_strings(x, f);
            }
        }
        _ => {}
    }
}

fn push_cand(cand: &mut Vec<(String, &'static str)>, s: &str, dir: Option<&'static str>) {
    if !is_model_filename(s) {
        return;
    }
    if let Some(existing) = cand.iter_mut().find(|(n, _)| n == s) {
        // 同名引用出现多次时，保留首个非空目录提示
        if existing.1.is_empty() {
            if let Some(d) = dir {
                existing.1 = d;
            }
        }
    } else {
        cand.push((s.to_string(), dir.unwrap_or("")));
    }
}

type LibIndex = std::collections::HashMap<String, String>;

fn index_dir(dir: &Path, rel: &str, depth: u32, map: &mut LibIndex) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let p = e.path();
        if p.is_dir() {
            if depth > 0 {
                index_dir(&p, &format!("{}/{}", rel, name), depth - 1, map);
            }
        } else {
            map.entry(name.to_lowercase()).or_insert_with(|| format!("{}/{}", rel, name));
        }
    }
}

// 一次遍历建全库索引（小写文件名 → 相对路径）。逐个缺失模型重复扫 16 个目录在
// 机械盘/NAS 上会卡数秒，索引化后查询 O(1)
fn build_library_index(root: &Path) -> LibIndex {
    let mut map = LibIndex::new();
    for d in MODEL_DIRS {
        let sub: PathBuf = d.split('/').collect();
        index_dir(&root.join(sub), d, 3, &mut map);
    }
    map
}

// 找模型：先按提示目录的精确相对路径（工作流名可带子目录；unet/diffusion_models、text_encoders/clip 互为别名），
// 未命中再查全库文件名索引（模型可能被放在任何目录）
fn find_model_file(root: &Path, index: &LibIndex, dir_hint: &str, name: &str) -> Option<String> {
    let norm = name.replace('\\', "/");
    let rel: PathBuf = norm.split('/').collect();
    let mut hints: Vec<&str> = Vec::new();
    if !dir_hint.is_empty() {
        hints.push(dir_hint);
        if dir_hint == "models/unet" {
            hints.push("models/diffusion_models");
        }
        if dir_hint == "models/text_encoders" {
            hints.push("models/clip");
        }
    }
    for h in &hints {
        let sub: PathBuf = h.split('/').collect();
        if root.join(&sub).join(&rel).exists() {
            return Some(format!("{}/{}", h, norm));
        }
    }
    let base = norm.rsplit('/').next().unwrap_or(&norm).to_lowercase();
    index.get(&base).cloned()
}

// 界面格式：递归找所有带 widgets_values 的节点对象（顶层 nodes 与子图 definitions.subgraphs 内的都覆盖）
fn collect_ui_nodes(v: &Value, cand: &mut Vec<(String, &'static str)>) {
    match v {
        Value::Object(o) => {
            if let Some(wv) = o.get("widgets_values") {
                let dir = node_type_to_dir(o.get("type").and_then(|x| x.as_str()).unwrap_or(""));
                walk_strings(wv, &mut |s| push_cand(cand, s, dir));
            }
            for x in o.values() {
                collect_ui_nodes(x, cand);
            }
        }
        Value::Array(a) => {
            for x in a {
                collect_ui_nodes(x, cand);
            }
        }
        _ => {}
    }
}

// 解析 ComfyUI 工作流 JSON（界面格式 nodes[].widgets_values / API 格式 class_type+inputs），
// 列出引用的模型文件及本地存在情况
// 是否 64 位十六进制（SHA256）
fn is_sha256_hex(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

// 防御式提取工作流内嵌的模型哈希：递归找「同一对象里既有模型文件名、又有 64-hex 串」的配对，
// basename(小写) → sha256(小写)。不写死 rgthree/ComfyUI 的具体字段路径（各版本不一），
// 凡是结构上把文件名和哈希放一起的都能抓到；抓不到也不影响（退化为按文件名搜索）。
fn collect_wf_hashes(v: &Value) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    fn walk(v: &Value, out: &mut std::collections::HashMap<String, String>) {
        match v {
            Value::Object(o) => {
                // 只在对象内「恰好一个模型文件名 + 恰好一个 64-hex」时配对，避免多模型对象里
                // 文件名与不属于它的哈希错配（rgthree 的 models[] 每项正是一名一哈希）。
                let mut fname: Option<&str> = None;
                let mut hash: Option<String> = None;
                let mut ambiguous = false;
                for val in o.values() {
                    if let Some(s) = val.as_str() {
                        if is_model_filename(s) {
                            if fname.is_some() {
                                ambiguous = true;
                            }
                            fname = Some(s);
                        }
                        if is_sha256_hex(s) {
                            if hash.is_some() {
                                ambiguous = true;
                            }
                            hash = Some(s.to_lowercase());
                        }
                    }
                }
                if !ambiguous {
                    if let (Some(f), Some(h)) = (fname, hash) {
                        let base = f.replace('\\', "/").rsplit('/').next().unwrap_or(f).to_lowercase();
                        out.entry(base).or_insert(h);
                    }
                }
                for val in o.values() {
                    walk(val, out);
                }
            }
            Value::Array(a) => {
                for val in a {
                    walk(val, out);
                }
            }
            _ => {}
        }
    }
    walk(v, &mut out);
    out
}

fn analyze_workflow(cfg: &Config, text: &str) -> Result<Vec<WfModel>, String> {
    let text = text.trim_start_matches('\u{feff}'); // ComfyUI 导出的 json 可能带 UTF-8 BOM
    let v: Value = serde_json::from_str(text).map_err(|e| format!("JSON 解析失败: {}", e))?;
    let mut cand: Vec<(String, &'static str)> = Vec::new();
    if v.get("nodes").is_some() {
        collect_ui_nodes(&v, &mut cand);
    } else if let Some(obj) = v.as_object() {
        for node in obj.values() {
            let dir = node_type_to_dir(node.get("class_type").and_then(|x| x.as_str()).unwrap_or(""));
            if let Some(inputs) = node.get("inputs") {
                walk_strings(inputs, &mut |s| push_cand(&mut cand, s, dir));
            }
        }
    }
    if cand.is_empty() {
        // 兜底：全树扫所有带模型扩展名的字符串
        walk_strings(&v, &mut |s| push_cand(&mut cand, s, None));
    }
    if cand.is_empty() {
        return Err("未在工作流中发现模型文件引用".into());
    }
    let root = expand_root(&cfg.comfy_root);
    let index = build_library_index(&root);
    let preset_idx = preset_file_index(cfg);
    let models_idx = models_index_lookup();
    let wf_hashes = collect_wf_hashes(&v);
    let mut models: Vec<WfModel> = cand
        .into_iter()
        .map(|(name, dir)| {
            let found_at = find_model_file(&root, &index, dir, &name).unwrap_or_default();
            let dir = if dir.is_empty() { type_dir(guess_type(&name)).to_string() } else { dir.to_string() };
            // 本地缺失时，先查本地 models.json 索引，再查预设已知文件表
            let base = name.replace('\\', "/").rsplit('/').next().unwrap_or(&name).to_lowercase();
            let dl = if found_at.is_empty() {
                models_idx
                    .get(&base)
                    .cloned()
                    .or_else(|| preset_idx.get(&base).cloned())
            } else {
                None
            };
            let wf_hash = if found_at.is_empty() { wf_hashes.get(&base).cloned() } else { None };
            WfModel { name, dir, found_at, in_comfy: false, dl, wf_hash }
        })
        .collect();
    // 本地缺失的项，再用运行中的 ComfyUI 实例核对（覆盖 symlink / 额外路径）；实例没开就跳过
    if models.iter().any(|m| m.found_at.is_empty()) {
        if let Ok(live) = comfy_known_models(cfg) {
            for m in &mut models {
                if m.found_at.is_empty() {
                    let base = m.name.replace('\\', "/").rsplit('/').next().unwrap_or(&m.name).to_lowercase();
                    // 带目录维度比对，避免不同类别同名文件误判「已加载」而漏补
                    if live.contains(&(m.dir.clone(), base)) {
                        m.in_comfy = true;
                    }
                }
            }
        }
    }
    Ok(models)
}

// 本地 models.json 索引：文件名(小写) → DlMeta，用于工作流缺失模型一键补齐
fn models_index_lookup() -> std::collections::HashMap<String, DlMeta> {
    let mut idx: std::collections::HashMap<String, DlMeta> = std::collections::HashMap::new();
    for r in load_models_index(&models_path()) {
        let base = r.filename.to_lowercase();
        let meta = DlMeta {
            download_url: r.download_url,
            source: r.source,
            expected_sha256: r.sha256,
            desc: r.desc,
        };
        // 同名文件优先保留有 SHA256 的记录；否则后覆盖先
        if let Some(existing) = idx.get(&base) {
            if existing.expected_sha256.is_some() {
                continue;
            }
        }
        idx.insert(base, meta);
    }
    idx
}

// 预设套餐里所有文件的 文件名(小写) → DlMeta 索引，用于工作流缺失模型一键补齐
fn preset_file_index(cfg: &Config) -> std::collections::HashMap<String, DlMeta> {
    let mut idx = std::collections::HashMap::new();
    for (_k, _title, files) in presets(cfg) {
        for (url, name, _sub) in files {
            idx.insert(
                name.to_lowercase(),
                DlMeta { download_url: url, source: "hf".into(), expected_sha256: None, desc: String::new() },
            );
        }
    }
    idx
}

// 缺失模型 → Civitai 搜索词：取文件名主干，下划线/连字符还原成空格
fn search_term(name: &str) -> String {
    let norm = name.replace('\\', "/");
    let base = norm.rsplit('/').next().unwrap_or(&norm);
    let stem = base.rsplit_once('.').map(|(s, _)| s).unwrap_or(base);
    stem.replace(['_', '-'], " ")
}

// ============ 工作流缺失模型：跨源自动解析下载源 ============

#[derive(Clone)]
struct ResolveCandidate {
    source: String,   // "civitai" / "hf"
    label: String,    // 展示用：模型名 / 仓库名
    filename: String, // 候选文件名
    dl: DlMeta,
    score: i32,       // 可靠度：哈希精确 120、文件名精确 100、去量化 80、子串 60、token 40
    size_kb: f64,
}

fn file_stem_lc(name: &str) -> String {
    let base = name.replace('\\', "/").rsplit('/').next().unwrap_or(name).to_string();
    base.rsplit_once('.').map(|(s, _)| s).unwrap_or(&base).to_lowercase()
}

// 去掉量化/精度后缀（fp8/fp16/bf16/q4_k_m/q8_0/scaled/e4m3fn 等）便于宽松同名匹配。
// 必须后缀锚定（strip_suffix）：无锚点 replace 会删词中间子串（model_q6_kungfu→modelungfu）。
fn quant_precision_strip(stem: &str) -> String {
    // 长标签在前，避免 q4 抢先匹配掉 q4_k_m
    let tags = [
        "q4_k_m", "q4_k_s", "q5_k_m", "q5_k_s", "q3_k_m", "q6_k", "q8_0", "q4_0", "q5_0", "e4m3fn",
        "scaled", "fp16", "bf16", "fp8", "q4", "q5", "q6", "q8",
    ];
    let mut s = stem.to_lowercase();
    'again: loop {
        for tag in tags {
            for sep in ['-', '_', '.'] {
                if let Some(stripped) = s.strip_suffix(&format!("{}{}", sep, tag)) {
                    s = stripped.to_string();
                    continue 'again; // 剥一层后重试，处理 -fp8-scaled 这类叠加
                }
            }
        }
        break;
    }
    s.trim_matches(['-', '_', ' ', '.']).to_string()
}

// 文件名匹配评分：精确 100、去量化后相等 80、互为子串 60、token Jaccard≥0.6 给 40，否则 0
fn score_filename_match(query: &str, cand: &str) -> i32 {
    let qs = file_stem_lc(query);
    let cs = file_stem_lc(cand);
    if qs.is_empty() || cs.is_empty() {
        return 0;
    }
    if qs == cs {
        return 100;
    }
    let (qstrip, cstrip) = (quant_precision_strip(&qs), quant_precision_strip(&cs));
    if !qstrip.is_empty() && qstrip == cstrip {
        return 80;
    }
    if cs.contains(&qs) || qs.contains(&cs) {
        return 60;
    }
    let split = |s: &str| -> std::collections::HashSet<String> {
        s.split(['_', '-', ' ', '.']).filter(|t| !t.is_empty()).map(|t| t.to_string()).collect()
    };
    let qt = split(&qs);
    let ct = split(&cs);
    if qt.is_empty() || ct.is_empty() {
        return 0;
    }
    let inter = qt.intersection(&ct).count();
    let union = qt.union(&ct).count();
    if union > 0 && inter as f32 / union as f32 >= 0.6 {
        40
    } else {
        0
    }
}

// Civitai by-hash 精确反查 → 下载元数据（复用 by-hash 端点，保留响应取 files/下载 URL）
fn civitai_by_hash_meta(cfg: &Config, sha256: &str) -> Option<(DlMeta, String, f64)> {
    let url = format!("https://{}/api/v1/model-versions/by-hash/{}", cfg.civitai_host, sha256);
    let mut req = agent(cfg).get(&url);
    if !cfg.civitai_token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", cfg.civitai_token));
    }
    let body = req.call().ok()?.into_string().ok()?;
    let v: Value = serde_json::from_str(&body).ok()?;
    let want = sha256.to_lowercase();
    // 精确匹配「被查哈希」对应的那个文件（版本常多文件：fp8/fp16 双发、+VAE 等），
    // 用它自己的 downloadUrl/name/sizeKB，保证下载内容与校验哈希是同一个文件，
    // 否则会下到 primary 却用别的文件哈希校验、必然失败。匹配不到则返回 None，退化为跨源搜索。
    let files = v.get("files").and_then(|x| x.as_array())?;
    let file = files.iter().find(|f| {
        f.get("hashes").and_then(|h| h.get("SHA256")).and_then(|x| x.as_str()).map(|s| s.to_lowercase()).as_deref()
            == Some(want.as_str())
    })?;
    let download_url = file.get("downloadUrl").and_then(|x| x.as_str())?.to_string();
    let filename = file.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let size_kb = file.get("sizeKB").and_then(|x| x.as_f64()).unwrap_or(0.0);
    let model_name = v.get("model").and_then(|m| m.get("name")).and_then(|x| x.as_str()).unwrap_or("").to_string();
    let dl = DlMeta { download_url, source: "civitai".into(), expected_sha256: Some(want), desc: model_name };
    Some((dl, filename, size_kb))
}

// HF 候选：按搜索词搜仓库（取前 3），列文件，对文件名打分，保留 ≥60 的
fn build_candidates_hf(cfg: &Config, name: &str) -> Vec<ResolveCandidate> {
    let mut out = Vec::new();
    let Ok(repos) = hf_search(cfg, &search_term(name)) else {
        return out;
    };
    for repo in repos.into_iter().take(3) {
        let Ok(files) = hf_repo_files(cfg, &repo.id) else {
            continue;
        };
        for f in files {
            let fname = f.path.rsplit('/').next().unwrap_or(&f.path).to_string();
            let score = score_filename_match(name, &fname);
            if score >= 60 {
                out.push(ResolveCandidate {
                    source: "hf".into(),
                    label: repo.id.clone(),
                    filename: fname,
                    dl: DlMeta {
                        download_url: format!("{}/{}/resolve/main/{}", hf_base(cfg), repo.id, f.path),
                        source: "hf".into(),
                        expected_sha256: None, // start_task 经 hf_sha256_from_url 懒校验
                        desc: repo.id.clone(),
                    },
                    score,
                    size_kb: f.size as f64 / 1024.0,
                });
            }
        }
    }
    out
}

// Civitai 候选：按搜索词搜模型（取前 6 命中），作模型级候选供人工确认
fn build_candidates_civitai(cfg: &Config, name: &str) -> Vec<ResolveCandidate> {
    let Ok((items, _)) = civitai_search(cfg, &search_term(name), "", "") else {
        return Vec::new();
    };
    let norm = name.replace('\\', "/");
    let fname = norm.rsplit('/').next().unwrap_or(&norm).to_string();
    items
        .into_iter()
        .take(6)
        .filter(|it| it.version_id > 0)
        .map(|it| {
            // 模型名 vs 缺失文件名的弱匹配；保底 30 让搜索命中仍进候选供人工确认
            let score = score_filename_match(name, &it.name).max(30);
            ResolveCandidate {
                source: "civitai".into(),
                label: it.name.clone(),
                filename: fname.clone(),
                dl: DlMeta {
                    download_url: format!("https://{}/api/download/models/{}", cfg.civitai_host, it.version_id),
                    source: "civitai".into(),
                    expected_sha256: None,
                    desc: it.name,
                },
                score,
                size_kb: 0.0,
            }
        })
        .collect()
}

// 解析一个缺失项的候选下载源：有内嵌哈希先走 Civitai by-hash 精确反查（score 120），
// 否则跨源（HF 文件名精确 + Civitai 搜索）合并、按分降序、按下载 URL 去重、截断 12。
fn resolve_missing_one(cfg: &Config, m: &WfModel) -> Vec<ResolveCandidate> {
    if let Some(hash) = &m.wf_hash {
        if let Some((dl, filename, size_kb)) = civitai_by_hash_meta(cfg, hash) {
            return vec![ResolveCandidate { source: "civitai".into(), label: dl.desc.clone(), filename, dl, score: 120, size_kb }];
        }
    }
    let mut out = build_candidates_hf(cfg, &m.name);
    out.extend(build_candidates_civitai(cfg, &m.name));
    out.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then(b.size_kb.partial_cmp(&a.size_kb).unwrap_or(std::cmp::Ordering::Equal))
    });
    let mut seen = std::collections::HashSet::new();
    out.retain(|c| seen.insert(c.dl.download_url.clone()));
    out.truncate(12);
    out
}

// ============ 预设套餐 ============
type PresetEntry = (&'static str, String, Vec<(String, String, String)>);

fn presets(cfg: &Config) -> Vec<PresetEntry> {
    // 返回: (key, 标题, [(下载url, 文件名, 子目录)])
    let hf = |repo: &str, path: &str| format!("{}/{}/resolve/main/{}", hf_base(cfg), repo, path);
    vec![
        (
            "wan22_video",
            "Wan 2.2 图生视频全套 (适配16G显存)".into(),
            vec![
                (hf("QuantStack/Wan2.2-I2V-A14B-GGUF", "HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"), "Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf".into(), "models/unet".into()),
                (hf("QuantStack/Wan2.2-I2V-A14B-GGUF", "LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf"), "Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf".into(), "models/unet".into()),
                (hf("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"), "umt5_xxl_fp8_e4m3fn_scaled.safetensors".into(), "models/text_encoders".into()),
                (hf("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan_2.1_vae.safetensors"), "wan_2.1_vae.safetensors".into(), "models/vae".into()),
                (hf("lightx2v/Wan2.2-Lightning", "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/high_noise_model.safetensors"), "Wan22_Lightning_I2V_4step_HIGH.safetensors".into(), "models/loras".into()),
                (hf("lightx2v/Wan2.2-Lightning", "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/low_noise_model.safetensors"), "Wan22_Lightning_I2V_4step_LOW.safetensors".into(), "models/loras".into()),
            ],
        ),
        (
            "flux_image",
            "Flux.1 文生图全套 (适配16G显存, fp8)".into(),
            vec![
                (hf("Comfy-Org/flux1-dev", "flux1-dev-fp8.safetensors"), "flux1-dev-fp8.safetensors".into(), "models/checkpoints".into()),
            ],
        ),
    ]
}

// ============ 后台消息 ============
enum Msg {
    // bool = append（加载更多时追加而非替换）
    Search(Result<(Vec<SearchItem>, Option<String>), String>, bool),
    // HuggingFace 仓库搜索结果 / 某仓库的文件列表
    HfSearch(Result<Vec<HfRepo>, String>),
    HfRepoFiles { repo: String, result: Result<Vec<HfFile>, String> },
    ModelDetailFetch { item: SearchItem, result: Result<ModelDetail, String> },
    Resolve(Box<Result<Resolved, String>>),
    ResolveSet(Result<Vec<Resolved>, String>),
    Library(Vec<LibDir>),
    Identify { path: PathBuf, ident: Ident },
    Workflow(Result<Vec<WfModel>, String>),
    // 缺失项自动解析出的候选下载源
    WfResolve { name: String, result: Result<Vec<ResolveCandidate>, String> },
    ComfyStatus(Result<String, String>),
    // model_id → 最新版本查询结果
    UpdateCheck { model_id: i64, result: Result<UpdateInfo, String> },
    // ComfyUI 子进程输出 / 退出 / 启动 PID
    ComfyOutput(String),
    ComfyExited(Option<i32>),
    ComfyStarted(u32),
    // ComfyUI 安装过程输出 / 完成
    ComfyInstallOutput(String),
    ComfyInstallDone(Result<(), String>),
    // 系统画像检测结果
    ComfyProfile(Result<sys_info::SystemProfile, String>),
    // 自定义节点扫描结果 / 节点更新完成 / Manager 版本探测
    CustomNodes(Vec<NodeInfo>),
    NodeUpdateDone { name: String, result: Result<String, String> },
    ManagerVersion(Result<String, String>),
    // 节点安装：流式日志 / 完成 / 注册表拉取结果
    NodeInstallOutput(String),
    NodeInstallDone { name: String, result: Result<(), String> },
    NodeRegistry(Result<Vec<RegistryNode>, String>),
}

// 运行外部命令并把 stdout/stderr 按行发送到消息队列（install=true 用 ComfyInstallOutput，否则 ComfyOutput）
// 子进程流式输出的去向：ComfyUI 安装日志 / 自定义节点安装日志
#[derive(Clone, Copy)]
enum CmdOut {
    Install,
    Node,
}

fn run_cmd_stream(cmd: &mut std::process::Command, tx: &std::sync::mpsc::Sender<Msg>, kind: CmdOut) -> Result<(), String> {
    use std::io::{BufRead, BufReader};
    use std::process::Stdio;
    // stdin 关掉：任何子进程都不应在 UI 后台等待交互输入（否则永久挂起、无从取消）
    let mut child = cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|e| e.to_string())?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    // 把一行输出按 kind 路由到对应的 Msg（kind 是 Copy，各线程各持一份）
    fn route(kind: CmdOut, tx: &std::sync::mpsc::Sender<Msg>, line: String) {
        let _ = match kind {
            CmdOut::Install => tx.send(Msg::ComfyInstallOutput(line)),
            CmdOut::Node => tx.send(Msg::NodeInstallOutput(line)),
        };
    }
    if let Some(stdout) = stdout {
        let tx = tx.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                route(kind, &tx, line);
            }
        });
    }
    if let Some(stderr) = stderr {
        let tx = tx.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                route(kind, &tx, format!("[stderr] {}", line));
            }
        });
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("退出码 {:?}", status.code()))
    }
}

// ============ egui 应用 ============
#[derive(PartialEq)]
enum Tab {
    Search,
    Link,
    Preset,
    Workflow,
    Library,
    Downloads,
    ComfyUI,
    Settings,
}

#[derive(PartialEq, Clone, Copy)]
enum DownloadsFilter {
    All,
    Active,      // 下载中 / 排队中 / 重试等待
    Failed,
    Completed,   // 完成 / 已存在
    Paused,
}

struct App {
    cfg: Config,
    tab: Tab,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
    busy: bool,
    // 搜索
    search_source: SearchSource,
    query: String,
    type_filter: String,
    base_filter: String,
    results: Vec<SearchItem>,
    next_page: Option<String>,
    detail: Option<ModelDetailState>,
    // HuggingFace 搜索
    hf_results: Vec<HfRepo>,
    hf_files: Option<HfFilesState>, // 打开的「仓库文件」弹窗，None = 不显示
    // 链接
    link: String,
    resolve_err: String,
    // 解析弹窗
    pending: Option<Resolved>,
    // 作品页批量解析结果（资源清单勾选弹窗），空 = 不显示
    pending_set: Vec<(Resolved, bool)>,
    edit_name: String,
    edit_subdir: String,
    sel_version: i64,
    // 确认窗磁盘预检缓存：(键=目标子目录|需求字节, 结果)；键不变就不重算，
    // 避免弹窗每帧穿透 resolve_dest_dir→detect_desktop 的磁盘 I/O。
    disk_check_cache: Option<(String, DiskCheck)>,
    // 模型库
    library: Vec<LibDir>,
    lib_scanned: bool,
    lib_filter: String,
    delete_confirm: Option<PathBuf>,
    lib_updates: std::collections::HashMap<i64, Result<UpdateInfo, String>>,
    lib_checking_updates: std::collections::HashSet<i64>,
    // 工作流分析
    wf_input: String,
    wf_models: Vec<WfModel>,
    wf_err: String,
    wf_note: String,
    // 缺失项自动解析下载源
    wf_resolving: std::collections::HashSet<String>,
    wf_resolve_notes: std::collections::HashMap<String, String>,
    wf_resolve_queue: Vec<ResolvePickState>, // 待用户选择的「下载源」弹窗队列（批量找源会有多个）
    // 设置
    token_input: String,
    saved_msg: String,
    comfy_status: String,
    cjk_font_ok: bool,
    // 下载
    downloads: Arc<Mutex<Vec<TaskRef>>>,
    last_tasks_fp: String,
    single_instance: bool,
    _instance_lock: Option<fs::File>,
    dl_filter: DownloadsFilter,
    dl_sort_newest_first: bool,
    dl_detail: Option<u64>, // 当前打开详情弹窗的任务 id
    // ComfyUI 管理
    profile: Option<sys_info::SystemProfile>,
    profile_err: String,
    // 检测到的 ComfyUI Desktop 安装（None = 非 Desktop / 未装），启动时探测一次
    desktop: Option<DesktopInfo>,
    comfy_log: Vec<String>,
    comfy_pid: Option<u32>,
    comfy_installing: bool,
    comfy_install_log: String,
    // 非正常退出标记（显示红色横幅 + 重启按钮），启动时清除
    comfy_crashed: bool,
    // 运行日志过滤
    comfy_log_filter: String,
    comfy_log_errors_only: bool,
    // 自定义节点管理
    custom_nodes: Vec<NodeInfo>,
    nodes_scanned: bool,
    nodes_busy: std::collections::HashSet<String>,
    node_results: std::collections::HashMap<String, Result<String, String>>,
    nodes_filter: String,
    // 节点安装（git clone）
    node_install_url: String,
    node_installing: bool,
    node_install_log: Vec<String>,
    // ComfyUI-Manager 注册表浏览
    registry: Vec<RegistryNode>,
    registry_loading: bool,
    registry_err: String,
    registry_filter: String,
    show_registry: bool,
    // ComfyUI-Manager 版本探测结果（API 联动）
    manager_status: String,
    // 托盘图标必须存活，否则会立即从任务栏消失
    #[allow(dead_code)]
    tray: Option<tray_icon::TrayIcon>,
    tray_initialized: bool,
    // 托盘「退出」时置位，让 close_requested 逻辑放行而非隐藏到托盘
    force_exit: bool,
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let cjk_font_ok = install_cjk_font(&cc.egui_ctx);
        setup_style(&cc.egui_ctx);
        let cfg = load_config();
        // 让默认/兜底的 HTTP 图片加载器（egui_extras 内部用 ehttp）也能走用户配置的代理，
        // 否则图片 URL 会直接连接，在需要代理的网络下全部失败。
        if let Some(ref proxy) = cfg.proxy_url {
            let proxy = proxy.trim();
            if !proxy.is_empty() {
                std::env::set_var("HTTP_PROXY", proxy);
                std::env::set_var("HTTPS_PROXY", proxy);
            }
        }
        egui_extras::install_image_loaders(&cc.egui_ctx);
        // 自定义预览图加载器：在默认加载器之后注册（egui 按后进先出顺序尝试），
        // 由它优先接管 http(s) 预览图——走我们带代理的 ureq agent + 本地磁盘缓存。
        cc.egui_ctx.add_bytes_loader(Arc::new(PreviewLoader::new(&cfg)));
        let (tx, rx) = std::sync::mpsc::channel();
        let downloads: Arc<Mutex<Vec<TaskRef>>> = Arc::new(Mutex::new(Vec::new()));
        // 单实例文件锁：第二个实例不恢复也不写 tasks.json，否则两进程会并发写同一 .part 损坏文件
        let lock_path = tasks_path().with_file_name("app.lock");
        if let Some(dir) = lock_path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let instance_lock = fs::OpenOptions::new().create(true).write(true).truncate(false).open(&lock_path).ok();
        let single_instance = match &instance_lock {
            Some(f) => f.try_lock().is_ok(),
            None => true, // 锁文件建不出来（只读目录等）时不阻止使用
        };
        if single_instance {
            // 恢复上次未完成/失败的下载任务（靠 .part 自动断点续传）
            for pt in load_tasks_from(&tasks_path()) {
                start_task(
                    cfg.clone(),
                    downloads.clone(),
                    pt.filename,
                    pt.subdir,
                    DlMeta { download_url: pt.download_url, source: pt.source, expected_sha256: pt.sha256, desc: pt.desc },
                    pt.size_kb,
                );
            }
        }
        // 启动即后台扫描模型库（仅读目录，很快），打开「模型库」标签页时已有内容
        {
            let cfg = cfg.clone();
            let tx = tx.clone();
            std::thread::spawn(move || {
                let _ = tx.send(Msg::Library(scan_library(&cfg)));
            });
        }
        // 启动时后台检测系统配置
        {
            let tx = tx.clone();
            std::thread::spawn(move || {
                let _ = tx.send(Msg::ComfyProfile(Ok(sys_info::detect())));
            });
        }
        // 调试/截图辅助：COMFY_START_TAB=link|preset|workflow|library|comfy|settings 指定启动页
        let tab = match std::env::var("COMFY_START_TAB").as_deref() {
            Ok("link") => Tab::Link,
            Ok("preset") => Tab::Preset,
            Ok("workflow") => Tab::Workflow,
            Ok("library") => Tab::Library,
            Ok("downloads") => Tab::Downloads,
            Ok("comfy") => Tab::ComfyUI,
            Ok("settings") => Tab::Settings,
            _ => Tab::Search,
        };
        App {
            tab,
            tx,
            rx,
            busy: false,
            search_source: SearchSource::Civitai,
            query: String::new(),
            type_filter: String::new(),
            base_filter: String::new(),
            results: Vec::new(),
            next_page: None,
            detail: None,
            hf_results: Vec::new(),
            hf_files: None,
            link: String::new(),
            resolve_err: String::new(),
            pending: None,
            pending_set: Vec::new(),
            edit_name: String::new(),
            edit_subdir: String::new(),
            sel_version: 0,
            disk_check_cache: None,
            library: Vec::new(),
            lib_scanned: false,
            lib_filter: String::new(),
            delete_confirm: None,
            lib_updates: std::collections::HashMap::new(),
            lib_checking_updates: std::collections::HashSet::new(),
            wf_input: String::new(),
            wf_models: Vec::new(),
            wf_err: String::new(),
            wf_note: String::new(),
            wf_resolving: std::collections::HashSet::new(),
            wf_resolve_notes: std::collections::HashMap::new(),
            wf_resolve_queue: Vec::new(),
            token_input: String::new(),
            saved_msg: String::new(),
            comfy_status: String::new(),
            cjk_font_ok,
            downloads,
            // 哨兵初值：保证首帧必写一次盘，否则"恢复的任务秒终结→空快照==空初值"会让过期 tasks.json 永不清空
            last_tasks_fp: "<init>".into(),
            single_instance,
            _instance_lock: instance_lock,
            dl_filter: DownloadsFilter::All,
            dl_sort_newest_first: true,
            dl_detail: None,
            profile: None,
            profile_err: String::new(),
            desktop: detect_desktop(&cfg),
            comfy_log: Vec::new(),
            comfy_pid: None,
            comfy_installing: false,
            comfy_install_log: String::new(),
            comfy_crashed: false,
            comfy_log_filter: String::new(),
            comfy_log_errors_only: false,
            custom_nodes: Vec::new(),
            nodes_scanned: false,
            nodes_busy: std::collections::HashSet::new(),
            node_results: std::collections::HashMap::new(),
            nodes_filter: String::new(),
            node_install_url: String::new(),
            node_installing: false,
            node_install_log: Vec::new(),
            registry: Vec::new(),
            registry_loading: false,
            registry_err: String::new(),
            registry_filter: String::new(),
            show_registry: false,
            manager_status: String::new(),
            tray: None,
            tray_initialized: false,
            force_exit: false,
            cfg,
        }
    }

    // 未完成任务集合变化时写盘（tasks.json 与 config.json 同目录）
    fn persist_if_changed(&mut self) {
        if !self.single_instance {
            return;
        }
        let snap: Vec<PersistTask> = {
            let dl = self.downloads.lock().unwrap();
            dl.iter()
                .filter_map(|t| {
                    let t = t.lock().unwrap();
                    // 失败任务也要保留元数据：断网等瞬时故障导致的失败，重启后应能自动重试，
                    // 否则持久化恰好在它最该起作用的场景下丢队列。不再想要的任务用「移除」按钮清掉。
                    let keep = t.status == "排队中"
                        || t.status == "下载中"
                        || t.status == "失败"
                        || t.status == "已暂停"
                        || t.status.starts_with("重试等待");
                    if keep {
                        Some(PersistTask {
                            filename: t.filename.clone(),
                            subdir: t.subdir.clone(),
                            download_url: t.download_url.clone(),
                            source: t.source.clone(),
                            size_kb: t.total as f64 / 1024.0,
                            sha256: t.expected_sha256.clone(),
                            desc: t.desc.clone(),
                        })
                    } else {
                        None
                    }
                })
                .collect()
        };
        let fp: String = snap.iter().map(|t| format!("{}|{};", t.filename, t.subdir)).collect();
        if fp != self.last_tasks_fp {
            save_tasks_to(&tasks_path(), &snap);
            self.last_tasks_fp = fp;
        }
    }

    fn run_wf_analyze(&mut self) {
        if self.busy {
            return; // 分析进行中，避免重复发起并发线程导致结果乱序覆盖
        }
        self.wf_err.clear();
        self.wf_models.clear();
        let input = self.wf_input.trim_start_matches('\u{feff}').trim().trim_matches('"').to_string();
        if input.is_empty() {
            self.wf_err = "请先输入工作流 .json 路径或 JSON 内容".into();
            return;
        }
        let text = if input.starts_with('{') {
            input
        } else {
            match fs::read_to_string(&input) {
                Ok(s) => s,
                Err(e) => {
                    self.wf_err = format!("读取文件失败: {}", e);
                    return;
                }
            }
        };
        // 分析含全库索引扫描，机械盘/NAS 上可能耗时数秒，放后台线程避免卡 UI
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        self.busy = true;
        std::thread::spawn(move || {
            let _ = tx.send(Msg::Workflow(analyze_workflow(&cfg, &text)));
        });
    }

    // 为单个缺失项跨源解析候选下载源（后台）
    fn resolve_wf_missing(&mut self, name: String) {
        if self.wf_resolving.contains(&name) {
            return;
        }
        let Some(m) = self.wf_models.iter().find(|m| m.name == name).cloned() else {
            return;
        };
        self.wf_resolving.insert(name.clone());
        self.wf_resolve_notes.remove(&name);
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(Msg::WfResolve { name, result: Ok(resolve_missing_one(&cfg, &m)) });
        });
    }

    // 为全部真缺失且未知源的项串行解析（单 worker，避免轰炸 API 触发 429）
    fn resolve_all_wf_missing(&mut self) {
        let targets: Vec<WfModel> = self
            .wf_models
            .iter()
            .filter(|m| m.found_at.is_empty() && !m.in_comfy && m.dl.is_none() && !self.wf_resolving.contains(&m.name))
            .cloned()
            .collect();
        if targets.is_empty() {
            return;
        }
        for m in &targets {
            self.wf_resolving.insert(m.name.clone());
            self.wf_resolve_notes.remove(&m.name);
        }
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            for m in targets {
                let name = m.name.clone();
                let _ = tx.send(Msg::WfResolve { name, result: Ok(resolve_missing_one(&cfg, &m)) });
            }
        });
    }

    fn do_search(&mut self) {
        let cfg = self.cfg.clone();
        let q = self.query.clone();
        let tx = self.tx.clone();
        self.busy = true;
        self.resolve_err.clear();
        self.next_page = None;
        match self.search_source {
            SearchSource::Civitai => {
                let tf = self.type_filter.clone();
                let bf = self.base_filter.clone();
                std::thread::spawn(move || {
                    let _ = tx.send(Msg::Search(civitai_search(&cfg, &q, &tf, &bf), false));
                });
            }
            SearchSource::HuggingFace => {
                self.hf_results.clear();
                std::thread::spawn(move || {
                    let _ = tx.send(Msg::HfSearch(hf_search(&cfg, &q)));
                });
            }
        }
    }

    // 打开某 HF 仓库的文件弹窗并后台拉取文件列表
    fn open_hf_files(&mut self, repo: String) {
        self.hf_files = Some(HfFilesState {
            repo: repo.clone(),
            files: Vec::new(),
            loading: true,
            err: String::new(),
        });
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = hf_repo_files(&cfg, &repo);
            let _ = tx.send(Msg::HfRepoFiles { repo, result });
        });
    }

    fn do_load_more(&mut self) {
        let Some(url) = self.next_page.take() else { return };
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        self.busy = true;
        std::thread::spawn(move || {
            let _ = tx.send(Msg::Search(civitai_fetch_page(&cfg, &url), true));
        });
    }

    fn do_resolve(&mut self, url: String) {
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        self.busy = true;
        self.resolve_err.clear();
        // 作品页（图片/视频/帖子）走爬取+批量解析，其余走单链接解析
        let re_media = regex::Regex::new(r"civitai\.(?:com|red|work)/(?:images|videos|posts)/\d+").unwrap();
        if re_media.is_match(&url) {
            std::thread::spawn(move || {
                let _ = tx.send(Msg::ResolveSet(resolve_media_page(&cfg, &url)));
            });
        } else {
            std::thread::spawn(move || {
                let _ = tx.send(Msg::Resolve(Box::new(resolve_url(&cfg, &url))));
            });
        }
    }

    fn do_scan(&mut self) {
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(Msg::Library(scan_library(&cfg)));
        });
    }

    // 后台识别单个模型：算哈希（带缓存）→ Civitai by-hash 反查
    fn do_identify(&mut self, path: PathBuf) {
        // 标记为识别中
        for d in &mut self.library {
            for f in &mut d.files {
                if f.path == path {
                    f.ident = Ident::Working;
                }
            }
        }
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let ident = identify_one(&cfg, &path);
            let _ = tx.send(Msg::Identify { path, ident });
        });
    }

    // 识别全部尚未识别的模型：单个 worker 顺序处理，避免并发轰炸 by-hash 触发 429
    fn do_identify_all(&mut self) {
        let pending: Vec<PathBuf> = self
            .library
            .iter()
            .flat_map(|d| d.files.iter())
            .filter(|f| f.ident == Ident::Unknown || matches!(f.ident, Ident::Failed(_)))
            .map(|f| f.path.clone())
            .collect();
        if pending.is_empty() {
            return;
        }
        for d in &mut self.library {
            for f in &mut d.files {
                if pending.contains(&f.path) {
                    f.ident = Ident::Working;
                }
            }
        }
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            for path in pending {
                let ident = identify_one(&cfg, &path);
                if tx.send(Msg::Identify { path, ident }).is_err() {
                    break; // UI 已关闭
                }
            }
        });
    }

    fn open_pending(&mut self, r: Resolved) {
        self.edit_name = r.filename.clone();
        self.edit_subdir = r.subdir.clone();
        self.sel_version = r.version_id;
        self.disk_check_cache = None; // 新弹窗：丢弃旧缓存，让磁盘预检重新算一次（避免显示过期空间）
        self.pending = Some(r);
    }

    // 确认窗磁盘预检（带缓存）：key=subdir|need_bytes 不变就复用上次结果，
    // 避免弹窗每帧穿透 resolve_dest_dir→detect_desktop 的磁盘 I/O。
    fn cached_disk_check(&mut self, subdir: &str, need_bytes: u64) -> DiskCheck {
        let key = format!("{subdir}|{need_bytes}");
        if self.disk_check_cache.as_ref().map(|(k, _)| k.as_str()) != Some(key.as_str()) {
            let check = disk_precheck(available_space_bytes(&resolve_dest_dir(&self.cfg, subdir)), need_bytes);
            self.disk_check_cache = Some((key, check));
        }
        self.disk_check_cache.as_ref().map(|(_, c)| *c).unwrap_or(DiskCheck::Unknown)
    }

    // 检查指定 model_id 在 Civitai 上的最新版本
    fn do_check_update(&mut self, model_id: i64, current_vid: i64, current_base: String) {
        if !self.lib_checking_updates.insert(model_id) {
            return; // 已在检查中
        }
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = match civitai_model_latest_version(&cfg, model_id, current_vid, &current_base) {
                Ok((latest_vid, latest_name, _)) => {
                    Ok(UpdateInfo { latest_vid, latest_name })
                }
                Err(e) => Err(e),
            };
            let _ = tx.send(Msg::UpdateCheck { model_id, result });
        });
    }

    // 清除全局暂停并把所有「已暂停」任务按元数据重新入队（线程已退出，靠 .part 续传）。
    // 顶栏恢复按钮与托盘「恢复」共用此逻辑，避免两处行为不一致。
    fn resume_all(&mut self) {
        PAUSED.store(false, Ordering::Relaxed);
        let paused_tasks: Vec<PersistTask> = {
            let mut dl = self.downloads.lock().unwrap();
            let snap = dl
                .iter()
                .filter(|t| t.lock().unwrap().status == "已暂停")
                .map(|t| {
                    let t = t.lock().unwrap();
                    PersistTask {
                        filename: t.filename.clone(),
                        subdir: t.subdir.clone(),
                        download_url: t.download_url.clone(),
                        source: t.source.clone(),
                        size_kb: t.total as f64 / 1024.0,
                        sha256: t.expected_sha256.clone(),
                        desc: t.desc.clone(),
                    }
                })
                .collect();
            dl.retain(|t| t.lock().unwrap().status != "已暂停");
            snap
        };
        for p in paused_tasks {
            start_task(
                self.cfg.clone(),
                self.downloads.clone(),
                p.filename,
                p.subdir,
                DlMeta { download_url: p.download_url, source: p.source, expected_sha256: p.sha256, desc: p.desc },
                p.size_kb,
            );
        }
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // 第一帧初始化托盘图标，确保与 winit 事件循环同线程且事件循环已在运行
        if !self.tray_initialized {
            self.tray = setup_tray();
            self.tray_initialized = true;
        }

        // 消费托盘「恢复」请求：托盘自由函数无 App 访问，只置标志，这里完成重新入队
        if RESUME_REQUESTED.swap(false, Ordering::Relaxed) {
            self.resume_all();
        }

        // 消费托盘窗口控制命令（跨平台统一走 ViewportCommand）
        match PENDING_TRAY_CMD.swap(0, Ordering::Relaxed) {
            1 => {
                WINDOW_VISIBLE.store(true, Ordering::Relaxed);
                ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
            }
            2 => {
                WINDOW_VISIBLE.store(false, Ordering::Relaxed);
                ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
            }
            3 => {
                let visible = WINDOW_VISIBLE.load(Ordering::Relaxed);
                let new_visible = !visible;
                WINDOW_VISIBLE.store(new_visible, Ordering::Relaxed);
                ctx.send_viewport_cmd(egui::ViewportCommand::Visible(new_visible));
            }
            4 => {
                self.force_exit = true;
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
            _ => {}
        }

        // 关闭窗口时若启用托盘最小化（且非托盘「退出」），则隐藏窗口而非退出
        if ctx.input(|i| i.viewport().close_requested()) && self.cfg.tray_minimize && !self.force_exit {
            WINDOW_VISIBLE.store(false, Ordering::Relaxed);
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }

        // 处理后台消息
        while let Ok(m) = self.rx.try_recv() {
            // busy 只代表搜索/解析/工作流分析这类带 spinner 的操作；Identify/ComfyStatus/Library
            // 各有自己的进度指示，不能让它们的回包误清 busy（否则并发时 spinner 提前消失）
            match m {
                Msg::Search(..) | Msg::HfSearch(_) | Msg::Resolve(_) | Msg::ResolveSet(_) | Msg::Workflow(_) => {
                    self.busy = false
                }
                _ => {}
            }
            match m {
                Msg::Search(Ok((items, next)), append) => {
                    if append {
                        self.results.extend(items);
                    } else {
                        self.results = items;
                    }
                    self.next_page = next;
                }
                Msg::Search(Err(e), _) => self.resolve_err = friendly_err(e),
                Msg::HfSearch(Ok(repos)) => self.hf_results = repos,
                Msg::HfSearch(Err(e)) => self.resolve_err = friendly_err(e),
                Msg::HfRepoFiles { repo, result } => {
                    // 仅当弹窗仍是同一仓库时才回填（用户可能已关闭或切换）
                    if let Some(state) = self.hf_files.as_mut() {
                        if state.repo == repo {
                            state.loading = false;
                            match result {
                                Ok(files) => state.files = files,
                                Err(e) => state.err = friendly_err(e),
                            }
                        }
                    }
                }
                Msg::ModelDetailFetch { item, result } => {
                    if let Some(state) = self.detail.as_mut() {
                        if state.item.id == item.id {
                            match result {
                                Ok(d) => {
                                    state.loading = false;
                                    state.sel_version = if d.versions.iter().any(|v| v.id == state.sel_version) {
                                        state.sel_version
                                    } else {
                                        d.versions.first().map(|v| v.id).unwrap_or(item.version_id)
                                    };
                                    state.data = Some(d);
                                }
                                Err(e) => {
                                    state.loading = false;
                                    state.err = friendly_err(e);
                                }
                            }
                        }
                    }
                }
                Msg::Resolve(r) => match *r {
                    Ok(r) => {
                        record_resolved_to_index(&r);
                        self.open_pending(r);
                    }
                    Err(e) => self.resolve_err = friendly_err(e),
                },
                Msg::ResolveSet(Ok(list)) => {
                    for r in &list {
                        record_resolved_to_index(r);
                    }
                    self.pending_set = list.into_iter().map(|r| (r, true)).collect();
                }
                Msg::ResolveSet(Err(e)) => self.resolve_err = friendly_err(e),
                Msg::Library(l) => {
                    self.library = l;
                    self.lib_scanned = true;
                }
                Msg::Identify { path, ident } => {
                    for d in &mut self.library {
                        for f in &mut d.files {
                            if f.path == path {
                                f.ident = ident.clone();
                            }
                        }
                    }
                }
                Msg::Workflow(Ok(l)) => {
                    self.wf_models = l;
                    self.wf_resolving.clear();
                    self.wf_resolve_notes.clear();
                    self.wf_resolve_queue.clear();
                }
                Msg::Workflow(Err(e)) => self.wf_err = e,
                Msg::WfResolve { name, result } => {
                    self.wf_resolving.remove(&name);
                    let dir = self.wf_models.iter().find(|m| m.name == name).map(|m| m.dir.clone());
                    let fname = name.replace('\\', "/").rsplit('/').next().unwrap_or(&name).to_string();
                    match result {
                        Err(e) => {
                            self.wf_resolve_notes.insert(name, friendly_err(e));
                        }
                        Ok(cands) if cands.is_empty() => {
                            self.wf_resolve_notes.insert(name, "未找到可靠下载源，可手动「去搜索」".into());
                        }
                        Ok(cands) => {
                            let dir = dir.unwrap_or_else(|| type_dir(guess_type(&fname)).to_string());
                            let top = &cands[0];
                            if top.score >= 120 {
                                // 哈希精确：SHA 反查保证内容正确，按工作流引用名落盘（否则名不符 ComfyUI 仍判缺失），
                                // 直接入队免确认。
                                let mut meta = top.dl.clone();
                                meta.download_url = apply_mirror(&meta.download_url, &self.cfg);
                                start_task(self.cfg.clone(), self.downloads.clone(), sanitize_filename(&fname), dir, meta, top.size_kb);
                                self.wf_resolve_notes.insert(name, "✓ 已按精确哈希自动入队下载".into());
                            } else {
                                // 多候选/低置信：排队等用户在弹窗里逐个挑（批量找源会产生多个，用队列避免互相覆盖）
                                self.wf_resolve_queue.push(ResolvePickState { name, dir, candidates: cands });
                            }
                        }
                    }
                }
                Msg::ComfyStatus(Ok(ver)) => self.comfy_status = format!("已连接 · ComfyUI {}", ver),
                Msg::ComfyStatus(Err(e)) => self.comfy_status = format!("未连接: {}", e),
                Msg::UpdateCheck { model_id, result } => {
                    self.lib_checking_updates.remove(&model_id);
                    self.lib_updates.insert(model_id, result);
                }
                Msg::ComfyProfile(Ok(p)) => self.profile = Some(p),
                Msg::ComfyProfile(Err(e)) => self.profile_err = e,
                Msg::ComfyOutput(line) => {
                    self.comfy_log.push(line);
                    if self.comfy_log.len() > 500 {
                        self.comfy_log.remove(0);
                    }
                }
                Msg::ComfyExited(code) => {
                    self.comfy_pid = None;
                    // 非零/未知退出码视为崩溃，给醒目提示；正常停止(0)不报警
                    self.comfy_crashed = !matches!(code, Some(0));
                    self.comfy_log.push(format!("[ComfyUI 已退出，退出码: {:?}]", code));
                }
                Msg::ComfyStarted(pid) => {
                    self.comfy_pid = Some(pid);
                    self.comfy_crashed = false;
                }
                Msg::ComfyInstallOutput(line) => {
                    self.comfy_install_log.push_str(&line);
                    self.comfy_install_log.push('\n');
                    if self.comfy_install_log.len() > 20000 {
                        self.comfy_install_log = self.comfy_install_log.split_off(self.comfy_install_log.len() - 15000);
                    }
                }
                Msg::ComfyInstallDone(result) => {
                    self.comfy_installing = false;
                    match result {
                        Ok(()) => self.comfy_install_log.push_str("\n[安装完成]"),
                        Err(e) => self.comfy_install_log.push_str(&format!("\n[安装失败: {}]", e)),
                    }
                }
                Msg::CustomNodes(nodes) => {
                    self.custom_nodes = nodes;
                    self.nodes_scanned = true;
                }
                Msg::NodeUpdateDone { name, result } => {
                    self.nodes_busy.remove(&name);
                    self.node_results.insert(name, result);
                    // 更新后 rev 可能变了，重新扫一次
                    self.do_scan_nodes();
                }
                Msg::ManagerVersion(r) => {
                    self.manager_status = match r {
                        Ok(v) => format!("ComfyUI-Manager {}", v),
                        Err(e) => e,
                    };
                }
                Msg::NodeInstallOutput(line) => {
                    self.node_install_log.push(line);
                    let n = self.node_install_log.len();
                    if n > 400 {
                        self.node_install_log.drain(0..n - 400); // 截末 400 行，防无限增长
                    }
                }
                Msg::NodeInstallDone { name, result } => {
                    self.node_installing = false;
                    match &result {
                        Ok(()) => self.node_install_log.push(format!("✓ {} 安装完成", name)),
                        Err(e) => self.node_install_log.push(format!("✗ {}", e)),
                    }
                    self.node_results.insert(name, result.map(|_| "刚安装".to_string()));
                    self.do_scan_nodes(); // 重扫让新节点出现在列表
                }
                Msg::NodeRegistry(r) => {
                    self.registry_loading = false;
                    match r {
                        Ok(list) => self.registry = list,
                        Err(e) => self.registry_err = friendly_err(e),
                    }
                }
            }
        }

        // 未完成任务集合变化时持久化
        self.persist_if_changed();

        // 系统通知：对刚进入终态且未通知的任务弹一次通知
        if self.cfg.notify_on_complete {
            let mut to_notify: Vec<(String, String)> = Vec::new();
            {
                let dl = self.downloads.lock().unwrap();
                for t in dl.iter() {
                    let mut t = t.lock().unwrap();
                    if !t.notified {
                        let (title, body) = match t.status.as_str() {
                            "完成" | "已存在" => ("下载完成".into(), format!("{} 已下载到 {}", t.filename, t.subdir)),
                            "失败" => ("下载失败".into(), format!("{}: {}", t.filename, t.error)),
                            "已取消" => ("下载已取消".into(), t.filename.clone()),
                            _ => continue,
                        };
                        t.notified = true;
                        to_notify.push((title, body));
                    }
                }
            }
            for (title, body) in to_notify {
                notify(&title, &body);
            }
        }

        // 拖拽 .json 工作流文件进窗口 → 直接分析；非 json 或多文件都给出明确提示
        let dropped: Vec<PathBuf> =
            ctx.input(|i| i.raw.dropped_files.iter().filter_map(|f| f.path.clone()).collect());
        if !dropped.is_empty() {
            self.tab = Tab::Workflow;
            let json = dropped
                .iter()
                .find(|p| p.extension().and_then(|e| e.to_str()).is_some_and(|e| e.eq_ignore_ascii_case("json")));
            match json {
                Some(p) => {
                    self.wf_input = p.to_string_lossy().into_owned();
                    self.wf_note = if dropped.len() > 1 {
                        format!("一次只分析一个文件，已忽略其余 {} 个", dropped.len() - 1)
                    } else {
                        String::new()
                    };
                    self.run_wf_analyze();
                }
                None => {
                    self.wf_models.clear();
                    self.wf_note.clear();
                    self.wf_err = "拖入的不是 .json 工作流文件".into();
                }
            }
        }

        let top_frame = egui::Frame::none()
            .fill(C_PANEL)
            .inner_margin(egui::Margin { left: 16.0, right: 16.0, top: 12.0, bottom: 10.0 });
        egui::TopBottomPanel::top("top").frame(top_frame).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading(egui::RichText::new(APP_NAME).strong());
                chip(ui, &self.cfg.comfy_root, egui::Color32::from_rgb(38, 38, 50), C_GRAY);
                if self.busy {
                    ui.spinner();
                }
            });
            ui.add_space(4.0);
            if !self.single_instance {
                ui.colored_label(
                    C_YELLOW,
                    "检测到另一个实例正在运行：本窗口不会恢复或记录下载队列，请勿在两个窗口下载同一文件。",
                );
            }
            if !self.cjk_font_ok {
                // 没装上 CJK 字体时中文全是方块，只能用英文提示
                ui.colored_label(
                    C_RED,
                    "CJK font not found - Chinese text will show as boxes. Please install Noto Sans CJK (e.g. apt install fonts-noto-cjk).",
                );
            }
            ui.horizontal(|ui| {
                // 标签做成胶囊：活动项 = 强调蓝 pill，非活动 = 纯文字（hover 有底色），不用 emoji 避免缺字形
                let pill = egui::Rounding::same(999.0);
                ui.visuals_mut().widgets.inactive.rounding = pill;
                ui.visuals_mut().widgets.hovered.rounding = pill;
                ui.visuals_mut().widgets.active.rounding = pill;
                ui.spacing_mut().item_spacing.x = 6.0;
                ui.spacing_mut().button_padding = egui::vec2(13.0, 6.0);
                ui.selectable_value(&mut self.tab, Tab::Search, "搜索");
                ui.selectable_value(&mut self.tab, Tab::Link, "链接");
                ui.selectable_value(&mut self.tab, Tab::Preset, "套餐");
                ui.selectable_value(&mut self.tab, Tab::Workflow, "工作流");
                ui.selectable_value(&mut self.tab, Tab::Library, "模型库");
                ui.selectable_value(&mut self.tab, Tab::Downloads, "下载");
                ui.selectable_value(&mut self.tab, Tab::ComfyUI, "ComfyUI");
                ui.selectable_value(&mut self.tab, Tab::Settings, "设置");
            });
        });

        // 下载队列摘要条（完整管理在「下载」标签页）
        let bottom_frame = egui::Frame::none()
            .fill(C_PANEL)
            .inner_margin(egui::Margin { left: 16.0, right: 16.0, top: 10.0, bottom: 10.0 });
        let mut toggle_pause = false;
        egui::TopBottomPanel::bottom("downloads").frame(bottom_frame).exact_height(46.0).show(ctx, |ui| {
            let (total, active, failed, done, total_speed, total_remaining) = {
                let dl = self.downloads.lock().unwrap();
                let mut active = 0usize;
                let mut failed = 0usize;
                let mut done = 0usize;
                let mut total_speed = 0.0f64;
                let mut total_remaining = 0u64;
                for t in dl.iter() {
                    let t = t.lock().unwrap();
                    match t.status.as_str() {
                        "下载中" | "排队中" => {
                            active += 1;
                            total_speed += t.speed;
                            if t.total > t.downloaded {
                                total_remaining += t.total - t.downloaded;
                            }
                        }
                        s if s.starts_with("重试等待") => active += 1,
                        "失败" | "已取消" => failed += 1,
                        "完成" | "已存在" => done += 1,
                        _ => {}
                    }
                }
                (dl.len(), active, failed, done, total_speed, total_remaining)
            };
            ui.horizontal(|ui| {
                ui.strong("下载队列");
                if total == 0 {
                    ui.weak("暂无任务");
                } else {
                    chip(ui, &format!("{} 进行", active), egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                    if done > 0 {
                        chip(ui, &format!("{} 完成", done), egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                    }
                    if failed > 0 {
                        chip(ui, &format!("{} 失败", failed), egui::Color32::from_rgb(66, 34, 38), C_RED);
                    }
                    ui.weak(format!("共 {}", total));
                    if total_speed > 0.0 {
                        ui.small(format!("· 总 {}/s", fmt_size(total_speed as u64)));
                        if let Some(eta) = eta_secs(0, total_remaining, total_speed) {
                            ui.small(format!("· 预计 {} 完成", fmt_duration(eta)));
                        }
                    }
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let paused = PAUSED.load(Ordering::Relaxed);
                    if total > 0 && ui.small_button(if paused { "全部恢复" } else { "全部暂停" }).clicked() {
                        toggle_pause = true;
                    }
                    if self.tab != Tab::Downloads && ui.small_button("打开下载页").clicked() {
                        self.tab = Tab::Downloads;
                    }
                    if paused {
                        chip(ui, "已全局暂停", egui::Color32::from_rgb(66, 54, 26), egui::Color32::from_rgb(230, 190, 100));
                    }
                });
            });
        });
        if toggle_pause {
            if PAUSED.load(Ordering::Relaxed) {
                self.resume_all();
            } else {
                PAUSED.store(true, Ordering::Relaxed);
            }
        }

        let central_frame = egui::Frame::none().fill(C_BG).inner_margin(egui::Margin::same(16.0));
        egui::CentralPanel::default().frame(central_frame).show(ctx, |ui| match self.tab {
            Tab::Search => self.ui_search(ui),
            Tab::Link => self.ui_link(ui),
            Tab::Preset => self.ui_preset(ui),
            Tab::Workflow => self.ui_workflow(ui),
            Tab::Library => self.ui_library(ui),
            Tab::Downloads => self.ui_downloads(ui),
            Tab::ComfyUI => self.ui_comfy(ui),
            Tab::Settings => self.ui_settings(ui),
        });

        // 解析弹窗
        self.ui_pending(ctx);
        self.ui_pending_set(ctx);
        // 模型详情弹窗
        self.ui_model_detail(ctx);
        // HuggingFace 仓库文件弹窗
        self.ui_hf_files(ctx);
        // 工作流缺失项「选择下载源」弹窗
        self.ui_wf_resolve_pick(ctx);

        ctx.request_repaint_after(Duration::from_millis(500));
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.persist_if_changed();
    }
}

impl App {
    fn ui_search(&mut self, ui: &mut egui::Ui) {
        // 搜索源切换：Civitai（图片卡片）/ HuggingFace（仓库→文件）
        ui.horizontal(|ui| {
            ui.selectable_value(&mut self.search_source, SearchSource::Civitai, "Civitai");
            ui.selectable_value(&mut self.search_source, SearchSource::HuggingFace, "HuggingFace");
        });
        ui.add_space(4.0);
        if self.search_source == SearchSource::HuggingFace {
            self.ui_search_hf(ui);
            return;
        }
        ui.weak("搜索 Civitai 模型，点卡片的下载会自动归类目录。");
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            let r = ui.add(egui::TextEdit::singleline(&mut self.query).desired_width(360.0).hint_text("关键词，如 wan2.2 i2v lora"));
            egui::ComboBox::from_id_salt("tf")
                .selected_text(if self.type_filter.is_empty() { "全部类型" } else { &self.type_filter })
                .show_ui(ui, |ui| {
                    ui.selectable_value(&mut self.type_filter, String::new(), "全部类型");
                    ui.selectable_value(&mut self.type_filter, "Checkpoint".into(), "Checkpoint");
                    ui.selectable_value(&mut self.type_filter, "LORA".into(), "LoRA");
                    ui.selectable_value(&mut self.type_filter, "VAE".into(), "VAE");
                    ui.selectable_value(&mut self.type_filter, "Controlnet".into(), "ControlNet");
                });
            egui::ComboBox::from_id_salt("bf")
                .selected_text(if self.base_filter.is_empty() { "全部底模" } else { &self.base_filter })
                .show_ui(ui, |ui| {
                    ui.selectable_value(&mut self.base_filter, String::new(), "全部底模");
                    for b in ["SD 1.5", "SDXL 1.0", "Pony", "Illustrious", "NoobAI", "Flux.1 D", "Wan Video", "Wan Video 2.2 I2V-A14B", "Wan Video 2.2 T2V-A14B", "Hunyuan Video", "LTXV"] {
                        ui.selectable_value(&mut self.base_filter, b.to_string(), b);
                    }
                });
            if ui.add(accent_btn("搜索")).clicked() || (r.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter))) {
                self.do_search();
            }
        });
        if !self.resolve_err.is_empty() {
            ui.colored_label(C_RED, &self.resolve_err);
        }
        ui.add_space(4.0);
        if self.results.is_empty() {
            // 空状态给可点击的引导：示例关键词一键搜索
            ui.add_space(56.0);
            ui.vertical_centered(|ui| {
                ui.label(egui::RichText::new("🔍").size(34.0));
                ui.add_space(4.0);
                ui.weak(if self.busy { "搜索中…" } else { "输入关键词搜索 Civitai 模型，或试试：" });
            });
            if !self.busy {
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    let demos = ["wan2.2 i2v lora", "flux lora", "illustrious checkpoint"];
                    ui.add_space(((ui.available_width() - 420.0) / 2.0).max(0.0));
                    for kw in demos {
                        if ui.button(kw).clicked() {
                            self.query = kw.into();
                            self.do_search();
                        }
                    }
                });
            }
            return;
        }
        let show_previews = self.cfg.show_previews;
        let results = self.results.clone();
        egui::ScrollArea::vertical().show(ui, |ui| {
            // 自适应网格：根据可用宽度计算列数，卡片宽度自动填充
            let gap = 12.0;
            let min_card_width = 170.0;
            let available = ui.available_width();
            let cols = ((available + gap) / (min_card_width + gap)).floor() as usize;
            let cols = cols.max(1).min(results.len().max(1));
            let card_width = (available - gap * (cols.saturating_sub(1)) as f32) / cols as f32;

            for chunk in results.chunks(cols) {
                ui.horizontal_top(|ui| {
                    ui.spacing_mut().item_spacing.x = 0.0;
                    for (idx, it) in chunk.iter().enumerate() {
                        if idx > 0 {
                            ui.add_space(gap);
                        }
                        ui.allocate_ui_with_layout(
                            egui::vec2(card_width, 0.0),
                            egui::Layout::top_down(egui::Align::Center),
                            |ui| {
                                let card_inner = card()
                                    .show(ui, |ui| {
                                        ui.vertical(|ui| {
                                            // 固定高度缩略图区域：有图时按原比例缩放居中，无图/失败/关闭时显示占位
                                            let thumb_height = 200.0;
                                            let thumb_width = ui.available_width();
                                            preview_img(ui, &it.image, thumb_width, thumb_height, 8.0, show_previews);
                                            ui.add(egui::Label::new(egui::RichText::new(&it.name).strong()).truncate());
                                            ui.horizontal(|ui| {
                                                chip(ui, &it.kind, egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                                                if !it.base.is_empty() {
                                                    chip(ui, &it.base, egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                                }
                                                if it.nsfw {
                                                    chip(ui, "NSFW", egui::Color32::from_rgb(66, 34, 38), C_RED);
                                                }
                                            });
                                            ui.small(format!("⬇ {}", it.downloads));
                                            let mut download_clicked = false;
                                            if ui.add_sized([ui.available_width(), 28.0], egui::Button::new("下载")).clicked() {
                                                download_clicked = true;
                                                self.do_resolve(format!("https://civitai.com/models/{}?modelVersionId={}", it.id, it.version_id));
                                            }
                                            download_clicked
                                        }).inner
                                    });
                                let card_response = card_inner.response.interact(egui::Sense::click());
                                let download_clicked = card_inner.inner;
                                if card_response.clicked() && !download_clicked {
                                    self.open_detail(it.clone());
                                }
                                if card_response.hovered() {
                                    ui.ctx().output_mut(|o| o.cursor_icon = egui::CursorIcon::PointingHand);
                                    let _ = card_response.on_hover_text("点击查看详情");
                                }
                            },
                        );
                    }
                });
                ui.add_space(gap);
            }
            if self.next_page.is_some() {
                ui.add_space(8.0);
                ui.vertical_centered(|ui| {
                    if self.busy {
                        ui.spinner();
                    } else if ui.add_sized([200.0, 30.0], egui::Button::new("加载更多")).clicked() {
                        self.do_load_more();
                    }
                });
                ui.add_space(8.0);
            }
        });
    }

    // HuggingFace 搜索视图：搜仓库 → 仓库卡片 → 「查看文件」开文件弹窗
    fn ui_search_hf(&mut self, ui: &mut egui::Ui) {
        ui.weak("搜索 HuggingFace 模型仓库，点开看文件列表，按需下载到对应目录。");
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            let r = ui.add(
                egui::TextEdit::singleline(&mut self.query)
                    .desired_width(420.0)
                    .hint_text("仓库关键词，如 flux gguf / wan2.2 / t5xxl"),
            );
            if ui.add(accent_btn("搜索")).clicked()
                || (r.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)))
            {
                self.do_search();
            }
        });
        if !self.resolve_err.is_empty() {
            ui.colored_label(C_RED, &self.resolve_err);
        }
        ui.add_space(4.0);
        if self.hf_results.is_empty() {
            ui.add_space(56.0);
            ui.vertical_centered(|ui| {
                ui.label(egui::RichText::new("🔍").size(34.0));
                ui.add_space(4.0);
                ui.weak(if self.busy {
                    "搜索中…"
                } else {
                    "输入关键词搜索 HuggingFace 模型仓库（如 flux、wan2.2、t5xxl gguf）"
                });
            });
            return;
        }
        let repos = self.hf_results.clone();
        let mut open_repo: Option<String> = None;
        egui::ScrollArea::vertical().show(ui, |ui| {
            for r in &repos {
                soft_card().inner_margin(egui::Margin::same(10.0)).show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.vertical(|ui| {
                            ui.set_width((ui.available_width() - 96.0).max(120.0));
                            ui.add(egui::Label::new(egui::RichText::new(&r.id).strong()).truncate());
                            ui.horizontal(|ui| {
                                if !r.pipeline_tag.is_empty() {
                                    chip(ui, &r.pipeline_tag, egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                                }
                                ui.small(format!("⬇ {}    ♥ {}", r.downloads, r.likes));
                            });
                        });
                        if ui.add_sized([84.0, 30.0], egui::Button::new("查看文件")).clicked() {
                            open_repo = Some(r.id.clone());
                        }
                    });
                });
                ui.add_space(8.0);
            }
        });
        if let Some(repo) = open_repo {
            self.open_hf_files(repo);
        }
    }

    // 「HF 仓库文件」弹窗：列出筛过的模型文件，逐个下载（复用 do_resolve → 确认 → 入队）
    fn ui_hf_files(&mut self, ctx: &egui::Context) {
        let Some(state) = self.hf_files.as_ref() else {
            return;
        };
        let repo = state.repo.clone();
        let loading = state.loading;
        let err = state.err.clone();
        let files = state.files.clone();
        let base = hf_base(&self.cfg).to_string();
        let mut open = true;
        let mut dl_url: Option<String> = None;
        let screen = ctx.screen_rect();
        egui::Window::new("HuggingFace 仓库文件")
            .open(&mut open)
            .resizable(true)
            .collapsible(false)
            .default_size([660.0, 540.0])
            .max_size([screen.width() - 40.0, screen.height() - 40.0])
            .show(ctx, |ui| {
                ui.add(egui::Label::new(egui::RichText::new(&repo).strong()).truncate());
                ui.weak("点「下载」按文件名/路径自动归类到 ComfyUI 对应目录。");
                ui.add_space(6.0);
                if loading {
                    ui.vertical_centered(|ui| {
                        ui.spinner();
                    });
                }
                if !err.is_empty() {
                    ui.colored_label(C_RED, &err);
                }
                egui::ScrollArea::vertical().show(ui, |ui| {
                    for f in &files {
                        soft_card().inner_margin(egui::Margin::same(8.0)).show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.vertical(|ui| {
                                    ui.set_width((ui.available_width() - 96.0).max(140.0));
                                    ui.add(egui::Label::new(egui::RichText::new(&f.path).strong()).truncate());
                                    let short_dir = type_dir(guess_type(&f.path)).trim_start_matches("models/");
                                    ui.small(format!("{} · → {}", fmt_size(f.size), short_dir));
                                });
                                if ui.add_sized([80.0, 30.0], egui::Button::new("下载")).clicked() {
                                    dl_url = Some(format!("{}/{}/resolve/main/{}", base, repo, f.path));
                                }
                            });
                        });
                        ui.add_space(6.0);
                    }
                });
            });
        if let Some(u) = dl_url {
            self.do_resolve(u);
        }
        if !open {
            self.hf_files = None;
        }
    }

    fn open_detail(&mut self, item: SearchItem) {
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        let state = ModelDetailState {
            item: item.clone(),
            data: None,
            loading: true,
            err: String::new(),
            sel_version: item.version_id,
            gallery_shown: 6,
        };
        self.detail = Some(state);
        std::thread::spawn(move || {
            let result = civitai_model_detail(&cfg, &item);
            let _ = tx.send(Msg::ModelDetailFetch { item, result });
        });
    }

    fn ui_model_detail(&mut self, ctx: &egui::Context) {
        let Some(state) = self.detail.as_ref() else { return; };
        let item = state.item.clone();
        let mut sel_version = state.sel_version;
        let mut gallery_shown = state.gallery_shown;
        let loading = state.loading;
        let err = state.err.clone();
        let data = state.data.clone();
        let show_previews = self.cfg.show_previews;
        let mut open = true;
        let screen = ctx.screen_rect();
        egui::Window::new("模型详情")
            .open(&mut open)
            .resizable(true)
            .default_size([800.0, 620.0])
            .min_size([620.0, 460.0])
            .max_size([screen.width() - 40.0, screen.height() - 40.0])
            .show(ctx, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.heading(&item.name);
                    if item.nsfw {
                        chip(ui, "NSFW", egui::Color32::from_rgb(66, 34, 38), C_RED);
                    }
                });
                ui.add_space(8.0);
                if loading {
                    ui.vertical_centered(|ui| {
                        ui.spinner();
                    });
                }
                if !err.is_empty() {
                    ui.colored_label(C_RED, &err);
                }
                let Some(d) = data.as_ref() else { return; };
                ui.horizontal_top(|ui| {
                    ui.spacing_mut().item_spacing.x = 0.0;
                    // 左栏固定 260px，避免 auto-size 把窗口撑到屏幕外
                    ui.allocate_ui_with_layout(
                        egui::vec2(260.0, 0.0),
                        egui::Layout::top_down(egui::Align::Center),
                        |ui| {
                            // 主图按自身宽高比显示：宽固定 260，高由真实比例推得，
                            // 钳制 [180,360] 防极端竖图把左栏撑过头。无图时退回方框占位。
                            if let Some(hero) = d.images.first() {
                                let hero_h = (260.0 / hero.aspect()).clamp(180.0, 360.0);
                                preview_img(ui, &hero.url, 260.0, hero_h, 10.0, show_previews);
                            } else {
                                preview_img(ui, "", 260.0, 260.0, 10.0, show_previews);
                            }
                            ui.add_space(10.0);
                            ui.horizontal_wrapped(|ui| {
                                chip(ui, &d.kind, egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                                if !d.base.is_empty() {
                                    chip(ui, &d.base, egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                }
                                chip(ui, &format!("⬇ {}", d.downloads), egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                            });
                            if !d.tags.is_empty() {
                                ui.add_space(6.0);
                                ui.label("标签：");
                                ui.horizontal_wrapped(|ui| {
                                    for t in &d.tags {
                                        chip(ui, t, egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                    }
                                });
                            }
                            ui.add_space(12.0);
                            ui.label("选择版本：");
                            egui::ComboBox::from_id_salt("detail_ver")
                                .selected_text(
                                    d.versions
                                        .iter()
                                        .find(|v| v.id == sel_version)
                                        .map(|v| v.name.as_str())
                                        .unwrap_or("—"),
                                )
                                .show_ui(ui, |ui| {
                                    for v in &d.versions {
                                        ui.selectable_value(&mut sel_version, v.id, &v.name);
                                    }
                                });
                            if let Some(v) = d.versions.iter().find(|v| v.id == sel_version) {
                                ui.small(format!("{} · {}", v.filename, fmt_size((v.size_kb * 1024.0) as u64)));
                                if !v.published_at.is_empty() {
                                    ui.small(format!("发布于 {}", v.published_at));
                                }
                                // 触发词（多为 LoRA 才有）：每个胶囊点击复制单个，「复制全部」逗号拼接。
                                if !v.trained_words.is_empty() {
                                    ui.add_space(8.0);
                                    ui.horizontal(|ui| {
                                        ui.label("触发词");
                                        if ui.small_button("复制全部").clicked() {
                                            ui.ctx().output_mut(|o| o.copied_text = v.trained_words.join(", "));
                                        }
                                    });
                                    ui.add_space(2.0);
                                    ui.horizontal_wrapped(|ui| {
                                        for w in &v.trained_words {
                                            if click_chip(
                                                ui,
                                                w,
                                                egui::Color32::from_rgb(40, 44, 60),
                                                egui::Color32::from_rgb(180, 200, 240),
                                            )
                                            .clicked()
                                            {
                                                ui.ctx().output_mut(|o| o.copied_text = w.clone());
                                            }
                                        }
                                    });
                                }
                            }
                            ui.add_space(8.0);
                            if ui.add_sized([ui.available_width(), 32.0], accent_btn("下载该版本")).clicked() {
                                let url = format!("https://civitai.com/models/{}?modelVersionId={}", d.id, sel_version);
                                self.do_resolve(url);
                            }
                        },
                    );
                    ui.add_space(16.0);
                    // 右栏：占满剩余宽度；描述固定高度，画廊占满剩余高度
                    let right_width = ui.available_width().max(280.0);
                    ui.allocate_ui_with_layout(
                        egui::vec2(right_width, 0.0),
                        egui::Layout::top_down(egui::Align::Min),
                        |ui| {
                            if !d.description.is_empty() {
                                soft_card()
                                    .inner_margin(egui::Margin::same(12.0))
                                    .show(ui, |ui| {
                                        egui::ScrollArea::vertical().max_height(160.0).show(ui, |ui| {
                                            ui.add(egui::Label::new(&d.description).wrap());
                                        });
                                    });
                                ui.add_space(10.0);
                            }
                            let total_imgs = d.images.len();
                            let shown = gallery_shown.min(total_imgs);
                            ui.strong(format!("预览图 ({}/{})", shown, total_imgs));
                            ui.add_space(6.0);
                            let gallery_height = (ui.available_height() - 20.0).max(200.0);
                            egui::ScrollArea::vertical().max_height(gallery_height).show(ui, |ui| {
                                // Justified Rows（Flickr/Google 相册式）：每行按各图真实宽高比
                                // 铺满列宽、零留白；矮宽图一行挤几张，高竖图一行就一张。
                                let avail_w = ui.available_width();
                                let gap = 8.0;
                                let imgs: Vec<&GalleryImg> = d.images.iter().take(shown).collect();
                                let aspects: Vec<f32> = imgs.iter().map(|g| g.aspect()).collect();
                                for (start, count, row_h) in justify_rows(&aspects, avail_w, gap, 190.0) {
                                    // 行级懒加载：整行不在视口内只占位，不实例化图片（避免大量网络加载）
                                    let row_top = ui.next_widget_position();
                                    let visible = ui.is_rect_visible(egui::Rect::from_min_size(
                                        row_top,
                                        egui::vec2(avail_w, row_h),
                                    ));
                                    if visible {
                                        ui.horizontal(|ui| {
                                            ui.spacing_mut().item_spacing.x = gap;
                                            for img in &imgs[start..start + count] {
                                                let w = (img.aspect() * row_h).min(avail_w);
                                                preview_img(ui, &img.url, w, row_h, 8.0, show_previews);
                                            }
                                        });
                                    } else {
                                        ui.allocate_exact_size(egui::vec2(avail_w, row_h), egui::Sense::hover());
                                    }
                                    ui.add_space(gap);
                                }
                                if shown < total_imgs {
                                    ui.vertical_centered(|ui| {
                                        if ui.add_sized([180.0, 30.0], egui::Button::new(format!("加载更多 (剩 {})", total_imgs - shown))).clicked() {
                                            gallery_shown = (shown + 6).min(total_imgs);
                                        }
                                    });
                                    ui.add_space(8.0);
                                }
                            });
                        },
                    );
                });
            });
        if open {
            if let Some(state) = self.detail.as_mut() {
                state.sel_version = sel_version;
                state.gallery_shown = gallery_shown;
            }
        } else {
            self.detail = None;
        }
    }

    fn ui_link(&mut self, ui: &mut egui::Ui) {
        ui.weak("支持：Civitai 模型页 (/models/...)、作品页 (/images|posts/... 自动解析 Resources used 整套下载)、HuggingFace 文件页 (.../resolve|blob/...)");
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            ui.add(egui::TextEdit::singleline(&mut self.link).desired_width(520.0).hint_text("粘贴 Civitai 模型页 / 作品(视频)页 或 HuggingFace 文件链接"));
            if ui.add(accent_btn("解析")).clicked() {
                let url = self.link.clone();
                self.do_resolve(url);
            }
        });
        if !self.resolve_err.is_empty() {
            ui.colored_label(C_RED, &self.resolve_err);
        }
    }

    fn ui_preset(&mut self, ui: &mut egui::Ui) {
        ui.weak("预设整套模型，一键全部加入下载队列，自动归类目录并校验 SHA256。");
        ui.add_space(4.0);

        // ===== 按显存推荐 =====
        let gpu = self.profile.as_ref().and_then(|p| p.gpu.clone());
        let mut go_search: Option<String> = None;
        card()
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.strong("🎯 按你的显卡推荐");
                    if let Some(g) = &gpu {
                        let gb = (g.vram_mb as f64 / 1024.0).round() as u64;
                        chip(ui, &format!("{} · {} GB", g.name, gb), egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                    }
                });
                match &gpu {
                    None if self.profile.is_none() => {
                        ui.horizontal(|ui| {
                            ui.spinner();
                            ui.weak("正在检测显卡…");
                        });
                    }
                    None => {
                        ui.weak("未检测到 NVIDIA 显卡，无法按显存推荐；可直接用下方预设或搜索。");
                    }
                    Some(g) => {
                        ui.weak(sys_info::vram_tier(g.vram_mb));
                        ui.add_space(4.0);
                        for (cat, model, query) in sys_info::vram_recommendations(g.vram_mb) {
                            ui.horizontal(|ui| {
                                chip(ui, cat, egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                ui.label(model);
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    if ui.small_button("🔍 搜索").clicked() {
                                        go_search = Some(query.to_string());
                                    }
                                });
                            });
                        }
                    }
                }
            });
        if let Some(q) = go_search {
            self.query = q;
            self.tab = Tab::Search;
            self.do_search();
        }
        ui.add_space(8.0);

        let ps = presets(&self.cfg);
        for (_k, title, files) in ps {
            card()
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.strong(&title);
                        chip(ui, &format!("{} 个文件", files.len()), egui::Color32::from_rgb(38, 38, 50), C_GRAY);
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.add(accent_btn("一键下载")).clicked() {
                                for (url, name, sub) in &files {
                                    start_task(
                                        self.cfg.clone(),
                                        self.downloads.clone(),
                                        name.clone(),
                                        sub.clone(),
                                        DlMeta { download_url: url.clone(), source: "hf".into(), expected_sha256: None, desc: String::new() },
                                        0.0,
                                    );
                                }
                            }
                        });
                    });
                    for (_url, name, sub) in &files {
                        ui.horizontal(|ui| {
                            ui.small(name);
                            ui.weak(egui::RichText::new(sub).size(11.5));
                        });
                    }
                });
            ui.add_space(8.0);
        }
    }

    fn ui_workflow(&mut self, ui: &mut egui::Ui) {
        ui.weak("分析 ComfyUI 工作流引用的模型并标出本地缺失项。三种输入：把 .json 文件拖进窗口 / 粘贴文件路径 / 粘贴 JSON 内容。");
        ui.add(
            egui::TextEdit::multiline(&mut self.wf_input)
                .desired_width(700.0)
                .desired_rows(3)
                .hint_text("工作流 .json 路径或 JSON 内容"),
        );
        ui.horizontal(|ui| {
            if ui.add(accent_btn("分析")).clicked() {
                self.wf_note.clear();
                self.run_wf_analyze();
            }
            if ui.button("选择文件…").clicked() {
                if let Some(p) = rfd::FileDialog::new()
                    .set_title("选择 ComfyUI 工作流 JSON")
                    .add_filter("工作流 JSON", &["json"])
                    .pick_file()
                {
                    self.wf_input = p.display().to_string();
                    self.wf_note.clear();
                    self.run_wf_analyze();
                }
            }
        });
        if !self.wf_err.is_empty() {
            ui.colored_label(C_RED, &self.wf_err);
        }
        if !self.wf_note.is_empty() {
            ui.weak(&self.wf_note);
        }
        ui.add_space(4.0);
        if self.wf_models.is_empty() {
            ui.centered_and_justified(|ui| {
                ui.weak(if self.busy { "分析中…" } else { "把 ComfyUI 工作流 .json 拖进窗口即可分析" });
            });
            return;
        }
        // 真缺失 = 本地没有 + ComfyUI 实例也没有；可一键补齐的单列出来
        let truly_missing = self.wf_models.iter().filter(|m| m.found_at.is_empty() && !m.in_comfy).count();
        // 工作流引用名可能带子目录前缀（xl/foo.safetensors），落盘用 basename，否则被 sanitize 把 / 替成 _
        let basename = |n: &str| n.replace('\\', "/").rsplit('/').next().unwrap_or(n).to_string();
        let fillable: Vec<(String, String, DlMeta)> = self
            .wf_models
            .iter()
            .filter(|m| m.found_at.is_empty() && !m.in_comfy)
            .filter_map(|m| m.dl.as_ref().map(|meta| (basename(&m.name), m.dir.clone(), meta.clone())))
            .collect();
        let mut act_resolve: Option<String> = None;
        let mut act_resolve_all = false;
        // 真缺失且暂不知源的项数（可自动找源）
        let auto_count = self
            .wf_models
            .iter()
            .filter(|m| m.found_at.is_empty() && !m.in_comfy && m.dl.is_none())
            .count();
        ui.horizontal(|ui| {
            ui.strong(format!("共引用 {} 个模型", self.wf_models.len()));
            if truly_missing > 0 {
                chip(ui, &format!("缺失 {} 个", truly_missing), egui::Color32::from_rgb(66, 34, 38), C_RED);
            } else {
                chip(ui, "全部齐备", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
            }
            if !fillable.is_empty() || auto_count > 0 {
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if !fillable.is_empty() && ui.add(accent_btn(&format!("一键补齐 {} 个", fillable.len()))).clicked() {
                        for (name, sub, meta) in &fillable {
                            let mut meta = meta.clone();
                            meta.download_url = apply_mirror(&meta.download_url, &self.cfg);
                            start_task(self.cfg.clone(), self.downloads.clone(), name.clone(), sub.clone(), meta, 0.0);
                        }
                    }
                    if auto_count > 0 && ui.button(format!("全部自动找源 {}", auto_count)).clicked() {
                        act_resolve_all = true;
                    }
                });
            }
        });
        let mut jump: Option<String> = None;
        let mut fill_one: Option<(String, String, DlMeta)> = None;
        egui::ScrollArea::vertical().show(ui, |ui| {
            for m in &self.wf_models {
                soft_card()
                    .inner_margin(egui::Margin::symmetric(10.0, 7.0))
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            if !m.found_at.is_empty() {
                                chip(ui, "已有", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                ui.label(&m.name);
                                ui.weak(&m.found_at);
                            } else if m.in_comfy {
                                chip(ui, "ComfyUI 已加载", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                ui.label(&m.name);
                                ui.weak("运行中的实例可见（在本工具未扫描的路径）");
                            } else {
                                chip(ui, "缺失", egui::Color32::from_rgb(66, 34, 38), C_RED);
                                ui.label(&m.name);
                                ui.weak(format!("应放入 {}", m.dir));
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    if let Some(meta) = &m.dl {
                                        if ui.add(accent_btn("下载")).clicked() {
                                            fill_one = Some((basename(&m.name), m.dir.clone(), meta.clone()));
                                        }
                                    } else if self.wf_resolving.contains(&m.name) {
                                        ui.add(egui::Spinner::new().size(14.0));
                                        ui.weak("查找下载源…");
                                    } else {
                                        if ui.small_button("去搜索").clicked() {
                                            jump = Some(search_term(&m.name));
                                        }
                                        if ui.button("自动找源").clicked() {
                                            act_resolve = Some(m.name.clone());
                                        }
                                    }
                                });
                            }
                        });
                        // 自动找源的结果提示（未找到 / 已按哈希入队）
                        if let Some(note) = self.wf_resolve_notes.get(&m.name) {
                            let c = if note.starts_with('✓') { C_GREEN } else { C_GRAY };
                            ui.colored_label(c, egui::RichText::new(note).size(11.0));
                        }
                    });
                ui.add_space(5.0);
            }
        });
        if let Some((name, sub, mut meta)) = fill_one {
            meta.download_url = apply_mirror(&meta.download_url, &self.cfg);
            start_task(
                self.cfg.clone(),
                self.downloads.clone(),
                name,
                sub,
                meta,
                0.0,
            );
        }
        if let Some(q) = jump {
            self.query = q;
            self.tab = Tab::Search;
            self.do_search();
        }
        if let Some(name) = act_resolve {
            self.resolve_wf_missing(name);
        }
        if act_resolve_all {
            self.resolve_all_wf_missing();
        }
    }

    // 「选择下载源」弹窗：多候选/低置信时让用户挑一个下载。队列逐个弹（批量找源会有多个）。
    fn ui_wf_resolve_pick(&mut self, ctx: &egui::Context) {
        let Some(state) = self.wf_resolve_queue.first().cloned() else {
            return;
        };
        let remaining = self.wf_resolve_queue.len();
        let mut open = true;
        let mut pick: Option<ResolveCandidate> = None;
        let screen = ctx.screen_rect();
        egui::Window::new("选择下载源")
            .open(&mut open)
            .resizable(true)
            .collapsible(false)
            .default_size([620.0, 460.0])
            .max_size([screen.width() - 40.0, screen.height() - 40.0])
            .show(ctx, |ui| {
                if remaining > 1 {
                    ui.weak(format!("还有 {} 项待选择", remaining - 1));
                }
                ui.label(format!("为缺失项「{}」找到 {} 个候选，点「下载」选一个：", state.name, state.candidates.len()));
                ui.weak("匹配度越高越可能正确；HF 多为文件名精确匹配，Civitai 多为模型级匹配需你判断。");
                ui.add_space(6.0);
                egui::ScrollArea::vertical().show(ui, |ui| {
                    for c in &state.candidates {
                        soft_card().inner_margin(egui::Margin::same(8.0)).show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.vertical(|ui| {
                                    ui.set_width((ui.available_width() - 92.0).max(180.0));
                                    ui.horizontal(|ui| {
                                        if c.source == "hf" {
                                            chip(ui, "HF", egui::Color32::from_rgb(40, 44, 60), egui::Color32::from_rgb(180, 200, 240));
                                        } else {
                                            chip(ui, "Civitai", egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                                        }
                                        let m = if c.score >= 120 { "哈希精确".to_string() } else { format!("匹配 {}", c.score) };
                                        chip(ui, &m, egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                        if c.size_kb > 0.0 {
                                            ui.weak(fmt_size((c.size_kb * 1024.0) as u64));
                                        }
                                    });
                                    ui.add(egui::Label::new(egui::RichText::new(&c.filename).strong()).truncate());
                                    ui.add(egui::Label::new(egui::RichText::new(&c.label).size(11.0).color(C_GRAY)).truncate());
                                });
                                if ui.add_sized([82.0, 30.0], egui::Button::new("下载")).clicked() {
                                    pick = Some(c.clone());
                                }
                            });
                        });
                        ui.add_space(5.0);
                    }
                });
            });
        if let Some(c) = pick {
            let mut meta = c.dl.clone();
            meta.download_url = apply_mirror(&meta.download_url, &self.cfg);
            // 按工作流引用名落盘（与 fillable 路径一致），否则名不符 ComfyUI 仍判缺失
            let base = state.name.replace('\\', "/").rsplit('/').next().unwrap_or(&state.name).to_string();
            start_task(self.cfg.clone(), self.downloads.clone(), sanitize_filename(&base), state.dir.clone(), meta, c.size_kb);
            self.wf_resolve_notes.insert(state.name.clone(), "✓ 已入队下载".into());
            if !self.wf_resolve_queue.is_empty() {
                self.wf_resolve_queue.remove(0); // 处理完弹出队首，下一个自动顶上
            }
        } else if !open && !self.wf_resolve_queue.is_empty() {
            self.wf_resolve_queue.remove(0); // 跳过这一项
        }
    }

    fn ui_library(&mut self, ui: &mut egui::Ui) {
        let total_size: u64 = self.library.iter().flat_map(|d| d.files.iter()).map(|f| f.size).sum();
        let total_count: usize = self.library.iter().map(|d| d.files.len()).sum();
        let unknown: usize = self
            .library
            .iter()
            .flat_map(|d| d.files.iter())
            .filter(|f| f.ident == Ident::Unknown)
            .count();
        let updatable: Vec<(i64, i64, String)> = self
            .library
            .iter()
            .flat_map(|d| d.files.iter())
            .filter_map(|f| match &f.ident {
                Ident::Found { model_id, version_id, base, .. } if *model_id > 0 && *version_id > 0 => Some((*model_id, *version_id, base.clone())),
                _ => None,
            })
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();
        ui.horizontal(|ui| {
            if ui.add(accent_btn("🔄 刷新")).clicked() {
                self.do_scan();
            }
            if total_count > 0 && ui.add_enabled(unknown > 0, egui::Button::new(format!("识别全部 ({})", unknown))).clicked() {
                self.do_identify_all();
            }
            if total_count > 0 && !updatable.is_empty() && ui.add_enabled(!updatable.iter().all(|(m, _, _)| self.lib_checking_updates.contains(m)), egui::Button::new(format!("检查全部更新 ({})", updatable.len()))).clicked() {
                for (mid, vid, base) in &updatable {
                    self.do_check_update(*mid, *vid, base.clone());
                }
            }
            if total_count > 0 {
                ui.add(egui::TextEdit::singleline(&mut self.lib_filter).desired_width(180.0).hint_text("按名称筛选"));
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if total_count > 0 {
                    chip(ui, &format!("{} 个 · {}", total_count, fmt_size(total_size)), egui::Color32::from_rgb(38, 38, 50), C_GRAY);
                }
            });
        });
        ui.weak("识别 = 算 SHA256 后到 Civitai 反查这是什么模型（结果会缓存）。支持 extra_model_paths.yaml 里的额外路径。");
        ui.add_space(4.0);
        if !self.lib_scanned {
            ui.centered_and_justified(|ui| {
                ui.weak("点「刷新」扫描 ComfyUI 模型目录");
            });
            return;
        }
        if total_count == 0 {
            ui.centered_and_justified(|ui| {
                ui.weak("未扫描到模型文件 — 检查「设置」里的 ComfyUI 根目录是否正确");
            });
            return;
        }
        // 大库一次性解码几百张原图会撑爆显存（egui_extras 不按显示尺寸降采样），超阈值就不显示缩略图
        // 缩略图：受全局「显示图片预览」开关控制；另外大库一次性解码几百张原图会撑爆显存
        // （egui_extras 不按显示尺寸降采样），超阈值也隐藏。
        let show_thumbs = self.cfg.show_previews && total_count <= 200;
        if self.cfg.show_previews && total_count > 200 {
            ui.weak("模型较多，已隐藏缩略图以节省内存");
        }
        let filter = self.lib_filter.to_lowercase();
        // 同名文件出现在多个位置 → 标记疑似重复（无需哈希的廉价提示）
        let mut name_count: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
        for d in &self.library {
            for f in &d.files {
                *name_count.entry(f.name.to_lowercase()).or_default() += 1;
            }
        }
        let mut identify: Option<PathBuf> = None;
        let mut open_dir: Option<PathBuf> = None;
        let mut del: Option<PathBuf> = None;
        let mut open_model: Option<i64> = None;
        let mut check_update: Option<(i64, i64, String)> = None; // (model_id, current_version_id, current_base)
        let mut download_update: Option<(String, String, DlMeta)> = None; // (filename, subdir, meta)
        let library = self.library.clone();
        egui::ScrollArea::vertical().show(ui, |ui| {
            for d in &library {
                let shown: Vec<&LibFile> = d.files.iter().filter(|f| filter.is_empty() || f.name.to_lowercase().contains(&filter)).collect();
                if shown.is_empty() {
                    continue;
                }
                let dir_size: u64 = shown.iter().map(|f| f.size).sum();
                ui.horizontal(|ui| {
                    ui.strong(&d.key);
                    chip(ui, &shown.len().to_string(), egui::Color32::from_rgb(38, 38, 50), C_GRAY);
                    ui.weak(fmt_size(dir_size));
                });
                for f in shown {
                    soft_card()
                        .inner_margin(egui::Margin::symmetric(10.0, 6.0))
                        .show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.horizontal(|ui| {
                                if show_thumbs {
                                    if let Some(uri) = &f.preview {
                                        ui.add(
                                            egui::Image::from_uri(uri.clone())
                                                .max_height(46.0)
                                                .max_width(46.0)
                                                .rounding(egui::Rounding::same(5.0)),
                                        );
                                    }
                                }
                                ui.vertical(|ui| {
                                    ui.horizontal(|ui| {
                                        ui.label(&f.name);
                                        if name_count.get(&f.name.to_lowercase()).copied().unwrap_or(0) > 1 {
                                            chip(ui, "疑似重复", egui::Color32::from_rgb(66, 54, 26), egui::Color32::from_rgb(230, 190, 100));
                                        }
                                    });
                                    match &f.ident {
                                        Ident::Working => {
                                            ui.horizontal(|ui| {
                                                ui.spinner();
                                                ui.weak("识别中…");
                                            });
                                        }
                                        Ident::Found { model_name, version_name, version_id, model_id, model_type, .. } => {
                                            ui.horizontal(|ui| {
                                                chip(ui, model_type, egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                                                ui.colored_label(C_GREEN, format!("{} · {}", model_name, version_name));
                                                if *model_id > 0 {
                                                    if self.lib_checking_updates.contains(model_id) {
                                                        ui.spinner();
                                                        ui.weak("检查中…");
                                                    } else if let Some(res) = self.lib_updates.get(model_id) {
                                                        match res {
                                                            Ok(info) if info.latest_vid != *version_id => {
                                                                chip(ui, &format!("有新版本: {}", info.latest_name), egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                                            }
                                                            Ok(_) => {
                                                                chip(ui, "已是最新", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                                            }
                                                            Err(e) => {
                                                                ui.colored_label(C_RED, format!("检查失败: {}", e));
                                                            }
                                                        }
                                                    }
                                                }
                                            });
                                        }
                                        Ident::NotFound => {
                                            ui.weak("Civitai 无记录（本地训练 / HF 来源）");
                                        }
                                        Ident::Failed(e) => {
                                            ui.colored_label(C_RED, format!("识别失败: {}", e));
                                        }
                                        Ident::Unknown => {}
                                    }
                                });
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    if ui.small_button("删除").clicked() {
                                        del = Some(f.path.clone());
                                    }
                                    if ui.small_button("目录").clicked() {
                                        open_dir = Some(f.path.clone());
                                    }
                                    if let Ident::Found { model_id, version_id, model_name, base, .. } = &f.ident {
                                        if ui.small_button("Civitai").clicked() {
                                            open_model = Some(*model_id);
                                        }
                                        if *model_id > 0 && *version_id > 0 && ui.small_button("检查更新").clicked() {
                                            check_update = Some((*model_id, *version_id, base.clone()));
                                        }
                                        if let Some(Ok(info)) = self.lib_updates.get(model_id) {
                                            if info.latest_vid != *version_id && ui.small_button("下载新版").clicked() {
                                                let url = format!("https://{}/api/download/models/{}", self.cfg.civitai_host, info.latest_vid);
                                                let meta = DlMeta {
                                                    download_url: url,
                                                    source: "civitai".into(),
                                                    expected_sha256: None,
                                                    desc: format!("{} - {}", model_name, info.latest_name),
                                                };
                                                download_update = Some((f.name.clone(), d.key.clone(), meta));
                                            }
                                        }
                                    } else if matches!(f.ident, Ident::Unknown | Ident::Failed(_)) && ui.small_button("识别").clicked() {
                                        identify = Some(f.path.clone());
                                    }
                                    ui.weak(fmt_size(f.size));
                                });
                            });
                        });
                }
                ui.add_space(8.0);
            }
        });
        if let Some(p) = identify {
            self.do_identify(p);
        }
        if let Some(p) = open_dir {
            open_in_file_manager(&p);
        }
        if let Some(mid) = open_model {
            ui.ctx().open_url(egui::OpenUrl::new_tab(format!("https://civitai.com/models/{}", mid)));
        }
        if let Some((mid, vid, base)) = check_update {
            self.do_check_update(mid, vid, base);
        }
        if let Some((name, sub, meta)) = download_update {
            start_task(
                self.cfg.clone(),
                self.downloads.clone(),
                name,
                sub,
                meta,
                0.0,
            );
        }
        if let Some(p) = del {
            self.delete_confirm = Some(p);
        }
        self.ui_delete_confirm(ui.ctx());
    }

    fn ui_delete_confirm(&mut self, ctx: &egui::Context) {
        let Some(path) = self.delete_confirm.clone() else { return };
        let name = path.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let mut close = false;
        egui::Window::new("确认删除")
            .collapsible(false)
            .resizable(false)
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .show(ctx, |ui| {
                ui.label(format!("永久删除模型文件？\n{}", name));
                ui.weak(path.to_string_lossy());
                let extras = sidecar_paths(&path).len();
                if extras > 0 {
                    ui.weak(format!("将一并删除 {} 个伴随文件（预览图 / 元数据）", extras));
                }
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    let danger = egui::Button::new(egui::RichText::new("删除").color(egui::Color32::WHITE)).fill(C_RED);
                    if ui.add(danger).clicked() {
                        if fs::remove_file(&path).is_ok() {
                            // 连带清理预览图/sidecar 与哈希缓存条目，避免孤儿文件与缓存膨胀
                            for sc in sidecar_paths(&path) {
                                let _ = fs::remove_file(&sc);
                            }
                            forget_hash_cache(&path);
                            // 从内存列表移除，避免重扫
                            for d in &mut self.library {
                                d.files.retain(|f| f.path != path);
                            }
                        }
                        close = true;
                    }
                    if ui.button("取消").clicked() {
                        close = true;
                    }
                });
            });
        if close {
            self.delete_confirm = None;
        }
    }

    fn ui_settings(&mut self, ui: &mut egui::Ui) {
        card()
            .inner_margin(egui::Margin::same(14.0))
            .show(ui, |ui| {
        egui::Grid::new("cfg").num_columns(2).spacing([12.0, 10.0]).show(ui, |ui| {
            ui.label("ComfyUI 根目录");
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut self.cfg.comfy_root).desired_width(360.0));
                if ui.button("浏览…").clicked() {
                    let mut dlg = rfd::FileDialog::new().set_title("选择 ComfyUI 根目录");
                    let cur = expand_root(&self.cfg.comfy_root);
                    if cur.is_dir() {
                        dlg = dlg.set_directory(&cur);
                    }
                    if let Some(p) = dlg.pick_folder() {
                        self.cfg.comfy_root = p.display().to_string();
                    }
                }
            });
            ui.end_row();
            ui.label("Civitai 密钥(留空不改)");
            ui.add(egui::TextEdit::singleline(&mut self.token_input).password(true).desired_width(420.0).hint_text(if self.cfg.civitai_token.is_empty() { "未设置" } else { "已保存 ****" }));
            ui.end_row();
            ui.label("Civitai 域名");
            egui::ComboBox::from_id_salt("civitai_host")
                .selected_text(&self.cfg.civitai_host)
                .show_ui(ui, |ui| {
                    for host in ["civitai.com", "civitai.red", "civitai.work"] {
                        ui.selectable_value(&mut self.cfg.civitai_host, host.to_string(), host);
                    }
                });
            ui.end_row();
            ui.label("HuggingFace 镜像");
            ui.checkbox(&mut self.cfg.hf_mirror, "使用 hf-mirror 国内镜像");
            ui.end_row();
            ui.label("图片预览");
            ui.checkbox(&mut self.cfg.show_previews, "加载网络预览图（图床连不上时关闭可避免满屏加载失败）");
            ui.end_row();
            ui.label("模型下载目录");
            ui.vertical(|ui| {
                let cur = if self.cfg.download_root.is_empty() {
                    "自动（Desktop 主目录 / 否则 comfy_root/models）".to_string()
                } else {
                    self.cfg.download_root.clone()
                };
                // 先把候选目录快照成 String，避免 combo 闭包里同时借 self.desktop 和 self.cfg
                let dl_dirs: Vec<String> = self
                    .desktop
                    .as_ref()
                    .map(|d| d.model_dirs.iter().map(|p| p.display().to_string()).collect())
                    .unwrap_or_default();
                egui::ComboBox::from_id_salt("dl_root").selected_text(cur).width(420.0).show_ui(ui, |ui| {
                    ui.selectable_value(&mut self.cfg.download_root, String::new(), "自动（推荐）");
                    for d in &dl_dirs {
                        ui.selectable_value(&mut self.cfg.download_root, d.clone(), d.as_str());
                    }
                });
                if dl_dirs.is_empty() {
                    ui.weak("未检测到 ComfyUI Desktop 模型目录；自动即 comfy_root/models");
                }
            });
            ui.end_row();
            ui.label("同时下载数");
            ui.add(egui::Slider::new(&mut self.cfg.max_concurrent, 1..=4));
            ui.end_row();
            ui.label("多连接加速");
            ui.checkbox(&mut self.cfg.multipart, "大文件分块并发下载（非 Civitai 源；出问题可关掉回落单连接）");
            ui.end_row();
            ui.label("网络代理");
            ui.horizontal(|ui| {
                let proxy = self.cfg.proxy_url.get_or_insert_with(String::new);
                ui.add(egui::TextEdit::singleline(proxy).desired_width(260.0).hint_text("http://127.0.0.1:7897"));
                if ui.button("填入 Clash 默认").clicked() {
                    *proxy = "http://127.0.0.1:7897".into();
                }
                if ui.button("清空").clicked() {
                    self.cfg.proxy_url = None;
                }
            });
            ui.end_row();
            ui.label("关闭时最小化到托盘");
            ui.checkbox(&mut self.cfg.tray_minimize, "关闭窗口后保留在系统托盘，后台继续下载");
            ui.end_row();
            ui.label("下载完成通知");
            ui.checkbox(&mut self.cfg.notify_on_complete, "任务完成或失败时弹出系统通知");
            ui.end_row();
            ui.label("ComfyUI 服务地址");
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut self.cfg.comfy_url).desired_width(300.0).hint_text("http://127.0.0.1:8188"));
                // 连接中禁用按钮，避免并发探测导致状态乱序覆盖
                if ui.add_enabled(self.comfy_status != "连接中…", egui::Button::new("测试连接")).clicked() {
                    self.comfy_status = "连接中…".into();
                    let cfg = self.cfg.clone();
                    let tx = self.tx.clone();
                    std::thread::spawn(move || {
                        let r = comfy_system_stats(&cfg);
                        let _ = tx.send(Msg::ComfyStatus(r));
                    });
                }
                if !self.comfy_status.is_empty() {
                    let c = if self.comfy_status.starts_with("已连接") { C_GREEN } else if self.comfy_status == "连接中…" { C_GRAY } else { C_RED };
                    ui.colored_label(c, &self.comfy_status);
                }
            });
            ui.end_row();
        });
        ui.weak("连接运行中的 ComfyUI 后，工作流分析会用它核对模型（覆盖本工具未扫描的额外路径）。");
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            if ui.add(accent_btn("保存设置")).clicked() {
                if !self.token_input.is_empty() {
                    self.cfg.civitai_token = self.token_input.clone();
                    self.token_input.clear();
                }
                self.saved_msg = match save_config(&self.cfg) {
                    Ok(()) => "已保存 ✓".into(),
                    Err(e) => format!("保存失败: {}", e),
                };
            }
            ui.weak(&self.saved_msg);
        });
        ui.weak(egui::RichText::new(format!("配置文件: {}", config_path().display())).size(11.5));
            });
        // ============ 关于 / 署名 ============
        ui.add_space(10.0);
        card()
            .inner_margin(egui::Margin::same(14.0))
            .show(ui, |ui| {
                ui.heading("关于");
                ui.add_space(4.0);
                egui::Grid::new("about").num_columns(2).spacing([12.0, 6.0]).show(ui, |ui| {
                    ui.label("应用");
                    ui.label(format!("{}  ·  v{}", APP_NAME, APP_VERSION));
                    ui.end_row();
                    ui.label("作者");
                    ui.label(egui::RichText::new(APP_AUTHOR).strong().color(C_ACCENT));
                    ui.end_row();
                    ui.label("项目主页");
                    ui.hyperlink(APP_HOMEPAGE);
                    ui.end_row();
                    ui.label("授权");
                    ui.label(format!("MIT License · {}", APP_COPYRIGHT));
                    ui.end_row();
                });
                ui.add_space(4.0);
                ui.weak("本工具由 Winery (WangZhenYu) 开发。MIT 授权：可自由使用与修改，但请保留本署名与版权声明。");
            });
    }

    // ============ ComfyUI 管理页 ============
    fn ui_comfy(&mut self, ui: &mut egui::Ui) {
        ui.heading("ComfyUI 管理");
        ui.add_space(8.0);
        // 内容较高（画像 + 安装/运行 + 日志 + 节点），整体包一层滚动，小窗口也能够到底部
        egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {

        // 系统画像
        card().show(ui, |ui| {
            ui.strong("系统配置");
            if let Some(ref p) = self.profile {
                ui.horizontal(|ui| {
                    ui.label(format!("系统: {} {}", p.os_name, p.os_arch));
                    ui.label(format!("CPU: {}", p.cpu));
                    ui.label(format!("内存: {} GB", p.ram_mb / 1024));
                });
                if let Some(ref gpu) = p.gpu {
                    ui.horizontal(|ui| {
                        ui.label(format!("GPU: {} · VRAM {} MB · 驱动 {}", gpu.name, gpu.vram_mb, gpu.driver_version));
                        if let Some(ref cuda) = p.cuda_version {
                            ui.label(format!("CUDA {}", cuda));
                        }
                    });
                    ui.label(format!("模型档位建议: {}", sys_info::vram_tier(gpu.vram_mb)));
                } else {
                    ui.colored_label(C_YELLOW, "未检测到 NVIDIA GPU，视频生成可能无法使用");
                }
                ui.horizontal(|ui| {
                    ui.label(format!("Python: {}", p.python_version.as_deref().unwrap_or("未安装")));
                    ui.label(format!("Git: {}", p.git_version.as_deref().unwrap_or("未安装")));
                });
                if !p.comfy_installs.is_empty() {
                    ui.horizontal_wrapped(|ui| {
                        ui.weak("已发现 ComfyUI:");
                        for path in &p.comfy_installs {
                            ui.weak(path.to_string_lossy().to_string());
                        }
                    });
                }
            } else if !self.profile_err.is_empty() {
                ui.colored_label(C_RED, &self.profile_err);
            } else {
                ui.horizontal(|ui| {
                    ui.spinner();
                    ui.weak("正在检测系统配置…");
                });
            }
            if ui.button("刷新检测").clicked() {
                self.desktop = detect_desktop(&self.cfg);
                let tx = self.tx.clone();
                std::thread::spawn(move || {
                    let _ = tx.send(Msg::ComfyProfile(Ok(sys_info::detect())));
                });
            }
        });

        ui.add_space(12.0);

        let desktop = self.desktop.clone();
        // 源码版才需要手填目录/参数；Desktop 版路径由其自身配置决定
        if desktop.is_none() {
            ui.horizontal(|ui| {
                ui.label("ComfyUI 目录:");
                ui.add(egui::TextEdit::singleline(&mut self.cfg.comfy_root).desired_width(360.0));
                if ui.button("浏览…").clicked() {
                    if let Some(p) = rfd::FileDialog::new().set_title("选择 ComfyUI 目录").pick_folder() {
                        self.cfg.comfy_root = p.display().to_string();
                    }
                }
            });
            ui.horizontal(|ui| {
                ui.label("启动参数:");
                ui.add(egui::TextEdit::singleline(&mut self.cfg.comfy_args).desired_width(400.0).hint_text("--lowvram --listen"));
                ui.weak("例: --lowvram --normalvram --listen --port 8188");
            });
        }

        let installed = desktop.is_some() || Path::new(&self.cfg.comfy_root).join("main.py").is_file();
        ui.add_space(12.0);

        if !installed {
            card().show(ui, |ui| {
                ui.strong("一键安装");
                ui.weak("将自动执行: git clone → 创建 venv → 安装 PyTorch → 安装依赖 → 安装 ComfyUI-Manager");
                ui.add_space(6.0);
                // 前置检查：缺 Python/Git 直接挡住，避免装到一半崩在费解的报错里
                let prof = self.profile.as_ref();
                let py = prof.and_then(|p| p.python_version.clone());
                let git = prof.and_then(|p| p.git_version.clone());
                let detected_cuda = prof.and_then(|p| p.cuda_version.clone());
                let mut missing: Vec<&str> = Vec::new();
                if prof.is_some() {
                    if py.is_none() { missing.push("Python"); }
                    if git.is_none() { missing.push("Git"); }
                }
                ui.horizontal(|ui| {
                    ui.label("PyTorch 源:");
                    egui::ComboBox::from_id_salt("torch_index")
                        .selected_text(&self.cfg.torch_index)
                        .show_ui(ui, |ui| {
                            for s in ["cu130", "cu128", "cu124", "cu121", "cpu"] {
                                ui.selectable_value(&mut self.cfg.torch_index, s.to_string(), s);
                            }
                        });
                    if let Some(c) = &detected_cuda {
                        ui.weak(format!("(检测到 CUDA {})", c));
                    }
                });
                ui.checkbox(&mut self.cfg.pip_mirror, "pip 走国内镜像（清华源）加速依赖下载");
                ui.add_space(6.0);
                if self.comfy_installing {
                    ui.horizontal(|ui| {
                        ui.spinner();
                        ui.weak("安装中…");
                    });
                    egui::ScrollArea::vertical().max_height(220.0).stick_to_bottom(true).show(ui, |ui| {
                        ui.monospace(&self.comfy_install_log);
                    });
                } else {
                    if prof.is_none() {
                        ui.weak("正在检测环境…");
                    } else if !missing.is_empty() {
                        ui.colored_label(C_RED, format!("缺少 {}，请先安装后再试（或刷新检测）", missing.join(" / ")));
                    }
                    let can_install = prof.is_some() && missing.is_empty();
                    let label = if self.comfy_install_log.is_empty() { "开始安装 ComfyUI" } else { "重试安装（跳过已完成步骤）" };
                    if ui.add_enabled(can_install, accent_btn(label)).clicked() {
                        self.do_comfy_install();
                    }
                    if !self.comfy_install_log.is_empty() {
                        egui::ScrollArea::vertical().max_height(180.0).show(ui, |ui| {
                            ui.monospace(&self.comfy_install_log);
                        });
                    }
                }
            });
        } else {
            // 已安装：Desktop 显示 Desktop 面板，源码版显示 python 运行控制 + 日志；两者都接 custom_nodes 管理
            if let Some(d) = &desktop {
            // ===== ComfyUI Desktop（electron）面板 =====
            let url = d.port.map(|p| format!("http://127.0.0.1:{}", p)).unwrap_or_else(|| self.cfg.comfy_url.clone());
            card().show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.strong("ComfyUI Desktop");
                    if !d.version.is_empty() {
                        chip(ui, &d.version, egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                    }
                    if let Some(p) = d.port {
                        chip(ui, &format!("端口 {}", p), egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                    }
                });
                ui.weak(format!("安装目录: {}", d.install_path.display()));
                if !d.launch_args.is_empty() {
                    ui.weak(format!("启动参数: {}", d.launch_args));
                }
                if !d.model_dirs.is_empty() {
                    ui.weak("模型目录:");
                    for md in &d.model_dirs {
                        ui.weak(format!("    • {}", md.display()));
                    }
                }
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    if let Some(exe) = d.app_exe.clone() {
                        if ui.add(accent_btn("启动 ComfyUI Desktop")).clicked() {
                            if let Err(e) = std::process::Command::new(&exe).spawn() {
                                self.comfy_log.push(format!("[启动 Desktop 失败: {}]", e));
                            }
                        }
                    }
                    if ui.button("在浏览器打开").clicked() {
                        ui.ctx().open_url(egui::OpenUrl::new_tab(&url));
                    }
                    if let Some(md) = d.model_dirs.first() {
                        if ui.button("📁 模型目录").clicked() {
                            open_in_file_manager(md);
                        }
                    }
                    if ui.button("📁 安装目录").clicked() {
                        open_in_file_manager(&d.install_path);
                    }
                    if ui.button("📁 custom_nodes").clicked() {
                        open_in_file_manager(&d.custom_nodes_dir);
                    }
                });
                ui.weak("Desktop 版由 electron 应用自行管理后端进程；本工具不直接启停其后端。");
            });
        } else {
            card().show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.strong("运行控制");
                    if let Some(pid) = self.comfy_pid {
                        chip(ui, &format!("运行中 · PID {}", pid), egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                    } else {
                        chip(ui, "已停止", egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                    }
                });
                if self.comfy_crashed {
                    ui.add_space(4.0);
                    ui.colored_label(C_RED, "⚠ ComfyUI 异常退出，请查看日志末尾的报错（端口占用 / 依赖缺失等）");
                }
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    if self.comfy_pid.is_some() {
                        if ui.add(egui::Button::new("停止 ComfyUI").fill(C_RED)).clicked() {
                            self.do_comfy_stop();
                        }
                    } else {
                        let start_label = if self.comfy_crashed { "重新启动" } else { "启动 ComfyUI" };
                        if ui.add(accent_btn(start_label)).clicked() {
                            self.do_comfy_start();
                        }
                    }
                    if ui.button("在浏览器打开").clicked() {
                        ui.ctx().open_url(egui::OpenUrl::new_tab(&self.cfg.comfy_url));
                    }
                    if ui.button("📁 models 目录").clicked() {
                        open_in_file_manager(&Path::new(&self.cfg.comfy_root).join("models"));
                    }
                    if ui.button("📁 ComfyUI 目录").clicked() {
                        open_in_file_manager(Path::new(&self.cfg.comfy_root));
                    }
                });
            });

            ui.add_space(8.0);
            ui.horizontal(|ui| {
                ui.strong("运行日志");
                ui.add(egui::TextEdit::singleline(&mut self.comfy_log_filter).desired_width(200.0).hint_text("过滤…"));
                ui.checkbox(&mut self.comfy_log_errors_only, "只看错误");
                if ui.small_button("清空").clicked() {
                    self.comfy_log.clear();
                }
            });
            let filter = self.comfy_log_filter.to_lowercase();
            let errors_only = self.comfy_log_errors_only;
            let is_err = |l: &str| {
                let low = l.to_lowercase();
                l.contains("[stderr]") || low.contains("error") || low.contains("traceback") || low.contains("exception") || low.contains("failed")
            };
            egui::Frame::none().fill(egui::Color32::from_rgb(20, 20, 26)).rounding(8.0).inner_margin(8.0).show(ui, |ui| {
                egui::ScrollArea::vertical().max_height(300.0).stick_to_bottom(true).show(ui, |ui| {
                    let shown: Vec<&String> = self
                        .comfy_log
                        .iter()
                        .filter(|l| (filter.is_empty() || l.to_lowercase().contains(&filter)) && (!errors_only || is_err(l)))
                        .collect();
                    if shown.is_empty() {
                        ui.weak(if self.comfy_log.is_empty() { "日志为空" } else { "无匹配日志" });
                    } else {
                        for line in shown {
                            if is_err(line) {
                                ui.monospace(egui::RichText::new(line).color(C_RED));
                            } else {
                                ui.monospace(line);
                            }
                        }
                    }
                });
            });
            }
            self.ui_custom_nodes(ui);
        }
        });
    }

    // ===== 自定义节点管理（custom_nodes：git 状态 / 更新 / 启停 / Manager 联动）=====
    fn ui_custom_nodes(&mut self, ui: &mut egui::Ui) {
        ui.add_space(12.0);
            if !self.nodes_scanned {
                self.nodes_scanned = true;
                self.do_scan_nodes();
            }
            let nodes = self.custom_nodes.clone();
            let busy = self.nodes_busy.clone();
            let results = self.node_results.clone();
            let nfilter = self.nodes_filter.to_lowercase();
            let mut act_update: Option<(String, PathBuf)> = None;
            let mut act_toggle: Option<(PathBuf, bool)> = None;
            let mut act_open: Option<PathBuf> = None;
            let mut act_install: Option<String> = None;
            let mut act_load_registry = false;
            card().show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.strong(format!("自定义节点 ({})", nodes.len()));
                    if ui.small_button("🔄 刷新").clicked() {
                        self.do_scan_nodes();
                    }
                    ui.add(egui::TextEdit::singleline(&mut self.nodes_filter).desired_width(160.0).hint_text("过滤节点…"));
                });
                // Manager API 联动（只读探测版本 + 安装/更新走网页，避免后台跑脚本改环境的风险）
                ui.horizontal(|ui| {
                    if ui.small_button("检查 Manager").clicked() {
                        self.do_check_manager();
                    }
                    if !self.manager_status.is_empty() {
                        ui.weak(&self.manager_status);
                    }
                    if ui.small_button("在 Manager 网页处理(装/更新)").clicked() {
                        ui.ctx().open_url(egui::OpenUrl::new_tab(&self.cfg.comfy_url));
                    }
                });
                ui.add_space(6.0);
                // 安装新节点：粘贴 git URL 直接 clone，或从 ComfyUI-Manager 注册表选
                ui.horizontal(|ui| {
                    ui.add(egui::TextEdit::singleline(&mut self.node_install_url).desired_width(300.0).hint_text("粘贴 git 仓库 URL 安装节点…"));
                    if self.node_installing {
                        ui.add(egui::Spinner::new().size(14.0));
                        ui.weak("安装中…");
                    } else {
                        if ui.button("安装").clicked() {
                            act_install = Some(self.node_install_url.trim().to_string());
                        }
                        if ui.button(if self.show_registry { "收起注册表" } else { "从注册表选…" }).clicked() {
                            self.show_registry = !self.show_registry;
                            if self.show_registry && self.registry.is_empty() {
                                act_load_registry = true;
                            }
                        }
                    }
                });
                if !self.node_install_log.is_empty() {
                    egui::ScrollArea::vertical().max_height(88.0).show(ui, |ui| {
                        for line in &self.node_install_log {
                            ui.label(egui::RichText::new(line).size(11.0).monospace());
                        }
                    });
                }
                ui.add_space(4.0);
                let shown: Vec<&NodeInfo> = nodes
                    .iter()
                    .filter(|n| nfilter.is_empty() || n.name.to_lowercase().contains(&nfilter))
                    .collect();
                if shown.is_empty() {
                    ui.weak(if nodes.is_empty() { "未发现自定义节点（custom_nodes 为空或未扫描）" } else { "无匹配节点" });
                }
                egui::ScrollArea::vertical().max_height(260.0).show(ui, |ui| {
                    for n in shown {
                        egui::Frame::none().fill(egui::Color32::from_rgb(24, 24, 32)).rounding(6.0).inner_margin(egui::Margin::symmetric(10.0, 6.0)).show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.horizontal(|ui| {
                                ui.vertical(|ui| {
                                    ui.horizontal(|ui| {
                                        ui.label(&n.name);
                                        if n.disabled {
                                            chip(ui, "已禁用", egui::Color32::from_rgb(66, 54, 26), egui::Color32::from_rgb(230, 190, 100));
                                        }
                                        if !n.is_git {
                                            chip(ui, "非 git", egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                        }
                                    });
                                    if !n.rev.is_empty() {
                                        ui.weak(egui::RichText::new(&n.rev).size(11.0));
                                    }
                                    match results.get(&n.name) {
                                        Some(Ok(msg)) => ui.colored_label(C_GREEN, egui::RichText::new(msg).size(11.0)),
                                        Some(Err(e)) => ui.colored_label(C_RED, egui::RichText::new(e).size(11.0)),
                                        None => ui.label(""),
                                    };
                                });
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    if ui.small_button("打开").clicked() {
                                        act_open = Some(n.path.clone());
                                    }
                                    let toggle_label = if n.disabled { "启用" } else { "禁用" };
                                    if ui.small_button(toggle_label).clicked() {
                                        act_toggle = Some((n.path.clone(), n.disabled));
                                    }
                                    if n.is_git && !n.disabled {
                                        if busy.contains(&n.name) {
                                            ui.add(egui::Spinner::new().size(14.0));
                                        } else if ui.small_button("更新").clicked() {
                                            act_update = Some((n.name.clone(), n.path.clone()));
                                        }
                                    }
                                });
                            });
                        });
                    }
                });
                // ComfyUI-Manager 注册表浏览（按需展开）
                if self.show_registry {
                    ui.separator();
                    ui.horizontal(|ui| {
                        ui.strong("ComfyUI-Manager 注册表");
                        if self.registry_loading {
                            ui.add(egui::Spinner::new().size(14.0));
                        } else if ui.small_button("刷新").clicked() {
                            act_load_registry = true;
                        }
                        ui.add(egui::TextEdit::singleline(&mut self.registry_filter).desired_width(160.0).hint_text("搜索节点…"));
                    });
                    if !self.registry_err.is_empty() {
                        ui.colored_label(C_RED, &self.registry_err);
                    }
                    let installed: std::collections::HashSet<String> = nodes.iter().map(|n| n.name.to_lowercase()).collect();
                    let rfilter = self.registry_filter.to_lowercase();
                    let matches: Vec<&RegistryNode> = self
                        .registry
                        .iter()
                        .filter(|r| rfilter.is_empty() || r.search_lc.contains(&rfilter))
                        .take(200)
                        .collect();
                    if !self.registry.is_empty() {
                        ui.weak(format!("共 {} 个，显示前 {}（可搜索过滤）", self.registry.len(), matches.len()));
                    }
                    let node_installing = self.node_installing;
                    egui::ScrollArea::vertical().max_height(240.0).show(ui, |ui| {
                        for r in matches {
                            egui::Frame::none().fill(egui::Color32::from_rgb(24, 24, 32)).rounding(6.0).inner_margin(egui::Margin::symmetric(10.0, 6.0)).show(ui, |ui| {
                                ui.set_width(ui.available_width());
                                ui.horizontal(|ui| {
                                    ui.vertical(|ui| {
                                        ui.horizontal(|ui| {
                                            ui.strong(&r.title);
                                            if !r.author.is_empty() {
                                                ui.weak(format!("by {}", r.author));
                                            }
                                        });
                                        if !r.description.is_empty() {
                                            ui.add(egui::Label::new(egui::RichText::new(&r.description).size(11.0).color(C_GRAY)).truncate())
                                                .on_hover_text(&r.description);
                                        }
                                    });
                                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                        let dir_name = repo_dir_name(&r.reference).to_lowercase();
                                        if installed.contains(&dir_name) {
                                            chip(ui, "已安装", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                        } else if node_installing {
                                            ui.add_enabled(false, egui::Button::new("安装"));
                                        } else if ui.small_button("安装").clicked() {
                                            act_install = Some(r.reference.clone());
                                        }
                                    });
                                });
                            });
                        }
                    });
                }
            });
            if let Some((name, path)) = act_update {
                self.do_update_node(name, path);
            }
            if let Some((path, disabled)) = act_toggle {
                self.do_toggle_node(path, disabled);
            }
            if let Some(path) = act_open {
                open_in_file_manager(&path);
            }
            if act_load_registry {
                self.do_load_registry();
            }
            if let Some(url) = act_install {
                self.do_install_node(url);
            }
    }

    fn do_comfy_install(&mut self) {
        if self.comfy_installing {
            return;
        }
        self.comfy_installing = true;
        self.comfy_install_log.clear();
        let dir = self.cfg.comfy_root.clone();
        let torch_index = self.cfg.torch_index.clone();
        let pip_mirror = self.cfg.pip_mirror;
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let send = |s: &str| {
                let _ = tx.send(Msg::ComfyInstallOutput(s.to_string()));
            };
            let done = |r: Result<(), String>| {
                let _ = tx.send(Msg::ComfyInstallDone(r));
            };
            // 清华源（仅用于普通 pip 包；torch 的 CUDA 轮子必须走 pytorch.org 专用 index）
            const TUNA: &str = "https://pypi.tuna.tsinghua.edu.cn/simple";

            if !Path::new(&dir).join("main.py").exists() {
                send("[1/5] 克隆 ComfyUI ...");
                let mut cmd = std::process::Command::new("git");
                cmd.args(["clone", "https://github.com/comfyanonymous/ComfyUI.git", &dir]);
                if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                    done(Err(format!("git clone 失败：{}（请确认已安装 Git 且网络可访问 github）", e)));
                    return;
                }
            } else {
                send("ComfyUI 目录已存在，跳过克隆");
            }

            // venv 已存在则跳过重建（失败重试时不浪费时间，也避免锁定报错）
            let venv_dir = format!("{}/venv", dir);
            let python = format!("{}\\venv\\Scripts\\python.exe", dir);
            if Path::new(&python).exists() {
                send("[2/5] 虚拟环境已存在，跳过创建");
            } else {
                send("[2/5] 创建虚拟环境 ...");
                let mut cmd = std::process::Command::new("python");
                cmd.args(["-m", "venv", &venv_dir]);
                if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                    done(Err(format!("创建 venv 失败：{}（请确认 python 在 PATH 且为 3.x）", e)));
                    return;
                }
            }

            send(&format!("[3/5] 升级 pip 并安装 PyTorch ({}) ...", torch_index));
            let mut cmd = std::process::Command::new(&python);
            cmd.args(["-m", "pip", "install", "--upgrade", "pip"]);
            if pip_mirror {
                cmd.args(["-i", TUNA]);
            }
            if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                done(Err(e));
                return;
            }
            let torch_url = format!("https://download.pytorch.org/whl/{}", torch_index);
            let mut cmd = std::process::Command::new(&python);
            cmd.args([
                "-m", "pip", "install", "torch", "torchvision", "torchaudio",
                "--index-url", &torch_url,
            ]);
            if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                done(Err(format!("PyTorch 安装失败：{}（可在设置换 PyTorch 源后重试安装）", e)));
                return;
            }

            send("[4/5] 安装 ComfyUI 依赖 ...");
            let mut cmd = std::process::Command::new(&python);
            cmd.args(["-m", "pip", "install", "-r", &format!("{}/requirements.txt", dir)]);
            if pip_mirror {
                cmd.args(["-i", TUNA]);
            }
            cmd.current_dir(&dir);
            if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                done(Err(e));
                return;
            }

            send("[5/5] 安装 ComfyUI-Manager ...");
            let manager_dir = format!("{}/custom_nodes/ComfyUI-Manager", dir);
            if !Path::new(&manager_dir).join("__init__.py").exists() {
                let mut cmd = std::process::Command::new("git");
                cmd.args(["clone", "https://github.com/ltdrdata/ComfyUI-Manager.git", &manager_dir]);
                if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                    done(Err(e));
                    return;
                }
            } else {
                send("ComfyUI-Manager 已存在，跳过");
            }

            send("验证 PyTorch ...");
            let mut cmd = std::process::Command::new(&python);
            cmd.args([
                "-c",
                "import torch; print(torch.__version__); print('CUDA可用:', torch.cuda.is_available())",
            ]);
            if let Err(e) = run_cmd_stream(&mut cmd, &tx, CmdOut::Install) {
                done(Err(e));
                return;
            }
            done(Ok(()));
        });
    }

    fn do_comfy_start(&mut self) {
        if self.comfy_pid.is_some() {
            return;
        }
        // 快速探测：地址已响应说明 ComfyUI（可能外部启动的）已在运行，不重复启动以免端口冲突。
        // 超时设短，避免明显卡顿。
        let probe = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_millis(800))
            .timeout_read(Duration::from_secs(2))
            .build();
        if probe.get(&format!("{}/system_stats", comfy_base(&self.cfg))).call().is_ok() {
            self.comfy_log.push("[检测到 ComfyUI 似乎已在运行，未重复启动；如需访问请点「在浏览器打开」]".into());
            return;
        }
        self.comfy_crashed = false;
        let dir = self.cfg.comfy_root.clone();
        let args = self.cfg.comfy_args.clone();
        let tx = self.tx.clone();
        self.comfy_log.clear();
        std::thread::spawn(move || {
            let python = format!("{}\\venv\\Scripts\\python.exe", dir);
            let mut cmd = std::process::Command::new(&python);
            cmd.arg(format!("{}\\main.py", dir)).current_dir(&dir);
            for a in args.split_whitespace() {
                cmd.arg(a);
            }
            cmd.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped());
            match cmd.spawn() {
                Ok(mut child) => {
                    let pid = child.id();
                    let _ = tx.send(Msg::ComfyStarted(pid));
                    let _ = tx.send(Msg::ComfyOutput(format!("[启动 PID: {}]", pid)));
                    if let Some(stdout) = child.stdout.take() {
                        let tx = tx.clone();
                        std::thread::spawn(move || {
                            use std::io::{BufRead, BufReader};
                            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                                let _ = tx.send(Msg::ComfyOutput(line));
                            }
                        });
                    }
                    if let Some(stderr) = child.stderr.take() {
                        let tx = tx.clone();
                        std::thread::spawn(move || {
                            use std::io::{BufRead, BufReader};
                            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                                let _ = tx.send(Msg::ComfyOutput(format!("[stderr] {}", line)));
                            }
                        });
                    }
                    let code = child.wait().ok().and_then(|s| s.code());
                    let _ = tx.send(Msg::ComfyExited(code));
                }
                Err(e) => {
                    let _ = tx.send(Msg::ComfyOutput(format!("[启动失败: {}]", e)));
                    let _ = tx.send(Msg::ComfyExited(None));
                }
            }
        });
    }

    fn do_comfy_stop(&mut self) {
        if let Some(pid) = self.comfy_pid {
            self.comfy_log.push(format!("[正在停止 PID {}]", pid));
            let _ = std::process::Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .output();
            self.comfy_pid = None;
            self.comfy_crashed = false;
        }
    }

    fn do_scan_nodes(&mut self) {
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(Msg::CustomNodes(scan_custom_nodes(&cfg)));
        });
    }

    // git pull 更新单个节点（--ff-only 避免本地有改动时产生合并冲突）
    fn do_update_node(&mut self, name: String, path: PathBuf) {
        if self.nodes_busy.contains(&name) {
            return;
        }
        self.nodes_busy.insert(name.clone());
        self.node_results.remove(&name);
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = git_run(&path, &["pull", "--ff-only"]);
            let _ = tx.send(Msg::NodeUpdateDone { name, result });
        });
    }

    // 启用/禁用节点：按 Manager 约定加/去 .disabled 后缀（本地、可逆）
    fn do_toggle_node(&mut self, path: PathBuf, disabled: bool) {
        let Some(fname) = path.file_name().map(|s| s.to_string_lossy().to_string()) else { return; };
        let new = if disabled {
            path.with_file_name(fname.trim_end_matches(".disabled"))
        } else {
            path.with_file_name(format!("{}.disabled", fname))
        };
        if let Err(e) = fs::rename(&path, &new) {
            self.manager_status = format!("切换失败：{}", e);
        }
        self.do_scan_nodes();
    }

    fn do_check_manager(&mut self) {
        self.manager_status = "查询中…".into();
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(Msg::ManagerVersion(comfy_manager_version(&cfg)));
        });
    }

    // 装新节点：git clone --depth 1 到 custom_nodes/<repo 名>，流式回传日志
    fn do_install_node(&mut self, url: String) {
        if self.node_installing {
            return;
        }
        let url = url.trim().to_string();
        if !is_git_url(&url) {
            self.node_install_log = vec!["URL 无效：需以 http(s):// 或 git@ 开头的 git 仓库地址".into()];
            return;
        }
        let Some(name) = safe_node_name(&url) else {
            self.node_install_log = vec!["无法从该 URL 推断合法的节点目录名（URL 应形如 .../owner/repo）".into()];
            return;
        };
        let dir = custom_nodes_dir(&self.cfg).join(&name);
        if dir.exists() {
            self.node_install_log = vec![format!("已存在同名节点目录「{}」，请先删除或禁用后再装", name)];
            return;
        }
        self.node_installing = true;
        self.node_install_log = vec![format!("git clone {} → custom_nodes/{}", url, name)];
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let mut cmd = std::process::Command::new("git");
            cmd.args(["clone", "--depth", "1", &url])
                .arg(&dir)
                // 缺凭据/未知主机指纹时让 git 立即失败而非阻塞等输入（否则 node_installing 卡死）
                .env("GIT_TERMINAL_PROMPT", "0")
                .env("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new");
            let result = run_cmd_stream(&mut cmd, &tx, CmdOut::Node)
                .map_err(|e| format!("克隆失败：{}（确认已装 Git 且网络可达 GitHub）", e));
            let _ = tx.send(Msg::NodeInstallDone { name, result });
        });
    }

    fn do_load_registry(&mut self) {
        if self.registry_loading {
            return;
        }
        self.registry_loading = true;
        self.registry_err.clear();
        let cfg = self.cfg.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(Msg::NodeRegistry(fetch_node_registry(&cfg)));
        });
    }

    // ============ 下载队列完整管理页 ============
    fn ui_downloads(&mut self, ui: &mut egui::Ui) {
        // 先把任务数据快照出来，避免渲染过程中频繁加锁
        let mut tasks: Vec<Task> = {
            self.downloads.lock().unwrap().iter().map(|t| t.lock().unwrap().clone()).collect()
        };
        // 排序
        if self.dl_sort_newest_first {
            tasks.reverse();
        }
        // 筛选
        let filtered: Vec<&Task> = tasks
            .iter()
            .filter(|t| match self.dl_filter {
                DownloadsFilter::All => true,
                DownloadsFilter::Active => t.status == "下载中" || t.status == "排队中" || t.status.starts_with("重试等待"),
                DownloadsFilter::Failed => t.status == "失败" || t.status == "已取消",
                DownloadsFilter::Completed => t.status == "完成" || t.status == "已存在",
                DownloadsFilter::Paused => t.status == "已暂停",
            })
            .collect();

        // 统计
        let total = tasks.len();
        let active = tasks.iter().filter(|t| t.status == "下载中" || t.status == "排队中" || t.status.starts_with("重试等待")).count();
        let failed = tasks.iter().filter(|t| t.status == "失败" || t.status == "已取消").count();
        let done = tasks.iter().filter(|t| t.status == "完成" || t.status == "已存在").count();
        let paused = tasks.iter().filter(|t| t.status == "已暂停").count();
        let total_downloaded: u64 = tasks.iter().map(|t| t.downloaded.min(t.total)).sum();
        let total_size: u64 = tasks.iter().map(|t| t.total).sum();
        let total_speed: f64 = tasks.iter().filter(|t| t.status == "下载中").map(|t| t.speed).sum();
        let total_remaining: u64 = tasks
            .iter()
            .filter(|t| t.status == "下载中" || t.status == "排队中")
            .map(|t| t.total.saturating_sub(t.downloaded))
            .sum();
        let global_frac = if total_size > 0 { (total_downloaded as f32 / total_size as f32).min(1.0) } else { 0.0 };

        ui.heading("下载队列");
        ui.add_space(8.0);

        // 统计卡片行
        ui.horizontal(|ui| {
            soft_card().show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("总任务");
                    ui.strong(total.to_string());
                });
            });
            soft_card().show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("进行中");
                    ui.strong(active.to_string());
                });
            });
            soft_card().show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("已完成");
                    ui.strong(done.to_string());
                });
            });
            soft_card().show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("失败");
                    ui.strong(failed.to_string());
                });
            });
            if paused > 0 {
                soft_card().show(ui, |ui| {
                    ui.vertical(|ui| {
                        ui.weak("已暂停");
                        ui.strong(paused.to_string());
                    });
                });
            }
            soft_card().show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("总速度");
                    ui.strong(format!("{}/s", fmt_size(total_speed as u64)));
                });
            });
            if total_speed > 0.0 {
                if let Some(eta) = eta_secs(0, total_remaining, total_speed) {
                    soft_card().show(ui, |ui| {
                        ui.vertical(|ui| {
                            ui.weak("预计完成");
                            ui.strong(fmt_duration(eta));
                        });
                    });
                }
            }
        });
        ui.add_space(8.0);

        // 全局进度条 + 批量操作
        ui.horizontal(|ui| {
            ui.add(egui::ProgressBar::new(global_frac).desired_height(10.0).rounding(egui::Rounding::same(5.0)).text(format!("{:.0}% · {} / {}", global_frac * 100.0, fmt_size(total_downloaded), fmt_size(total_size))));
        });
        ui.add_space(8.0);

        let mut batch_remove_done = false;
        let mut batch_retry_failed = false;
        let mut batch_cancel_queued = false;
        ui.horizontal(|ui| {
            ui.label("筛选:");
            ui.selectable_value(&mut self.dl_filter, DownloadsFilter::All, "全部");
            ui.selectable_value(&mut self.dl_filter, DownloadsFilter::Active, "进行中");
            ui.selectable_value(&mut self.dl_filter, DownloadsFilter::Paused, "已暂停");
            ui.selectable_value(&mut self.dl_filter, DownloadsFilter::Failed, "失败");
            ui.selectable_value(&mut self.dl_filter, DownloadsFilter::Completed, "已完成");
            ui.separator();
            if ui.button(if self.dl_sort_newest_first { "最新在前" } else { "最早在前" }).clicked() {
                self.dl_sort_newest_first = !self.dl_sort_newest_first;
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("清空已完成").clicked() {
                    batch_remove_done = true;
                }
                if ui.button("全部重试失败").clicked() {
                    batch_retry_failed = true;
                }
                if ui.button("取消全部排队").clicked() {
                    batch_cancel_queued = true;
                }
            });
        });
        ui.add_space(10.0);

        // 任务列表
        let mut action_remove: Option<u64> = None;
        let mut action_retry: Vec<PersistTask> = Vec::new();
        let mut action_detail: Option<u64> = None;
        let mut action_detail_close = false;

        egui::ScrollArea::vertical().show(ui, |ui| {
            if filtered.is_empty() {
                ui.vertical_centered(|ui| {
                    ui.add_space(40.0);
                    ui.weak("没有符合当前筛选条件的任务");
                });
            } else {
                for t in filtered {
                    let frac = if t.total > 0 { (t.downloaded as f32 / t.total as f32).min(1.0) } else { 0.0 };
                    soft_card()
                        .inner_margin(egui::Margin::symmetric(12.0, 10.0))
                        .show(ui, |ui| {
                            ui.horizontal(|ui| {
                                status_chip(ui, &t.status);
                                ui.strong(&t.filename);
                                ui.weak(&t.subdir);
                                if t.verified {
                                    chip(ui, "SHA256 ✓", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
                                }
                                if !t.desc.is_empty() {
                                    ui.add(egui::Label::new(egui::RichText::new(&t.desc).small().color(C_GRAY)).truncate())
                                        .on_hover_text(&t.desc);
                                }
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    if ui.small_button("详情").clicked() {
                                        action_detail = Some(t.id);
                                    }
                                    if (t.status == "完成" || t.status == "已存在") && ui.small_button("打开文件夹").clicked() {
                                        if let Some(ref p) = t.local_path {
                                            open_in_file_manager(p);
                                        }
                                    }
                                    if (t.status == "失败" || t.status == "已取消" || t.status == "完成" || t.status == "已存在" || t.status == "已暂停")
                                        && ui.small_button("移除").clicked()
                                    {
                                        action_remove = Some(t.id);
                                    }
                                    if t.status == "失败" && ui.small_button("重试").clicked() {
                                        action_retry.push(PersistTask {
                                            filename: t.filename.clone(),
                                            subdir: t.subdir.clone(),
                                            download_url: t.download_url.clone(),
                                            source: t.source.clone(),
                                            size_kb: t.total as f64 / 1024.0,
                                            sha256: t.expected_sha256.clone(),
                                            desc: t.desc.clone(),
                                        });
                                        action_remove = Some(t.id);
                                    }
                                    if (t.status == "下载中" || t.status == "排队中" || t.status.starts_with("重试等待"))
                                        && ui.small_button("取消").clicked()
                                    {
                                        if let Some(task_ref) = self.downloads.lock().unwrap().iter().find(|x| x.lock().unwrap().id == t.id) {
                                            task_ref.lock().unwrap().cancel.store(true, Ordering::Relaxed);
                                        }
                                    }
                                });
                            });
                            ui.add_space(4.0);
                            let bar = egui::ProgressBar::new(frac).desired_height(8.0).rounding(egui::Rounding::same(4.0));
                            let bar = match t.status.as_str() {
                                "失败" | "已取消" => bar.fill(egui::Color32::from_rgb(118, 58, 64)),
                                "完成" | "已存在" => bar.fill(egui::Color32::from_rgb(46, 118, 82)),
                                _ => bar,
                            };
                            ui.add(bar);
                            ui.horizontal(|ui| {
                                ui.small(format!("{} / {}", fmt_size(t.downloaded), if t.total > 0 { fmt_size(t.total) } else { "?".into() }));
                                if t.speed > 0.0 {
                                    ui.small(format!("· {}/s", fmt_size(t.speed as u64)));
                                }
                                if frac > 0.0 {
                                    ui.small(format!("· {:.0}%", frac * 100.0));
                                }
                                if t.status == "下载中" {
                                    if let Some(eta) = eta_secs(t.downloaded, t.total, t.speed) {
                                        ui.small(format!("· 剩余 {}", fmt_duration(eta)));
                                    }
                                }
                                if let Some(started) = t.started_at {
                                    let elapsed = started.elapsed().as_secs();
                                    ui.small(format!("· 已用 {}", fmt_duration(elapsed)));
                                }
                                if !t.error.is_empty() {
                                    ui.add(egui::Label::new(egui::RichText::new(&t.error).small().color(C_RED)).truncate())
                                        .on_hover_text(&t.error);
                                }
                            });
                            ui.horizontal(|ui| {
                                let src_label = if t.source == "civitai" { "Civitai" } else { "HuggingFace" };
                                chip(ui, src_label, egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                if let Some(ref p) = t.local_path {
                                    ui.weak(format!("→ {}", p.display()));
                                }
                            });
                        });
                    ui.add_space(8.0);
                }
            }
        });

        // 详情弹窗
        if let Some(detail_id) = self.dl_detail {
            let detail_task = tasks.iter().find(|t| t.id == detail_id).cloned();
            if let Some(t) = detail_task {
                let mut open = true;
                egui::Window::new("任务详情").collapsible(false).resizable(false).open(&mut open).show(ui.ctx(), |ui| {
                    ui.horizontal(|ui| {
                        ui.strong(&t.filename);
                        status_chip(ui, &t.status);
                    });
                    ui.add_space(6.0);
                    egui::Grid::new("dl_detail").num_columns(2).spacing([12.0, 6.0]).show(ui, |ui| {
                        ui.label("目录:");
                        ui.label(&t.subdir);
                        ui.label("来源:");
                        ui.label(&t.source);
                        ui.label("链接:");
                        ui.add(egui::Label::new(egui::RichText::new(&t.download_url).small().color(C_ACCENT)).truncate());
                        ui.label("本地路径:");
                        if let Some(ref p) = t.local_path {
                            ui.label(p.display().to_string());
                        } else {
                            ui.weak("未确定");
                        }
                        ui.label("简介:");
                        if !t.desc.is_empty() {
                            ui.colored_label(C_GRAY, &t.desc);
                        } else {
                            ui.weak("无");
                        }
                        ui.label("SHA256:");
                        if let Some(ref h) = t.expected_sha256 {
                            ui.label(format!("{}... {}", &h[..16.min(h.len())], if t.verified { "✓ 已校验" } else { "待校验/跳过" }));
                        } else {
                            ui.weak("无");
                        }
                        ui.label("大小:");
                        ui.label(format!("{} / {}", fmt_size(t.downloaded), if t.total > 0 { fmt_size(t.total) } else { "?".into() }));
                        if let Some(started) = t.started_at {
                            ui.label("已用时间:");
                            ui.label(fmt_duration(started.elapsed().as_secs()));
                        }
                        if let Some(completed) = t.completed_at {
                            if let Some(started) = t.started_at {
                                let dur = completed.checked_duration_since(started).unwrap_or(Duration::ZERO).as_secs();
                                ui.label("下载耗时:");
                                ui.label(fmt_duration(dur));
                            }
                        }
                        if !t.error.is_empty() {
                            ui.label("错误:");
                            ui.colored_label(C_RED, &t.error);
                        }
                    });
                    ui.add_space(8.0);
                    ui.horizontal(|ui| {
                        if let Some(ref p) = t.local_path {
                            if ui.button("打开所在文件夹").clicked() {
                                open_in_file_manager(p);
                            }
                        }
                        if ui.button("复制下载链接").clicked() {
                            ui.ctx().output_mut(|o| o.copied_text = t.download_url.clone());
                        }
                        if t.status == "失败" && ui.button("重试").clicked() {
                            action_retry.push(PersistTask {
                                filename: t.filename.clone(),
                                subdir: t.subdir.clone(),
                                download_url: t.download_url.clone(),
                                source: t.source.clone(),
                                size_kb: t.total as f64 / 1024.0,
                                sha256: t.expected_sha256.clone(),
                                desc: t.desc.clone(),
                            });
                            action_remove = Some(t.id);
                            action_detail_close = true;
                        }
                    });
                });
                if !open || action_detail_close {
                    self.dl_detail = None;
                }
            } else {
                self.dl_detail = None;
            }
        }

        // 执行批量/单条操作
        if batch_remove_done {
            let ids: Vec<u64> = tasks.iter().filter(|t| t.status == "完成" || t.status == "已存在").map(|t| t.id).collect();
            self.downloads.lock().unwrap().retain(|t| !ids.contains(&t.lock().unwrap().id));
        }
        if batch_retry_failed {
            for t in tasks.iter().filter(|t| t.status == "失败") {
                action_retry.push(PersistTask {
                    filename: t.filename.clone(),
                    subdir: t.subdir.clone(),
                    download_url: t.download_url.clone(),
                    source: t.source.clone(),
                    size_kb: t.total as f64 / 1024.0,
                    sha256: t.expected_sha256.clone(),
                    desc: t.desc.clone(),
                });
            }
            let ids: Vec<u64> = tasks.iter().filter(|t| t.status == "失败").map(|t| t.id).collect();
            self.downloads.lock().unwrap().retain(|t| !ids.contains(&t.lock().unwrap().id));
        }
        if batch_cancel_queued {
            for t in tasks.iter().filter(|t| t.status == "排队中") {
                if let Some(task_ref) = self.downloads.lock().unwrap().iter().find(|x| x.lock().unwrap().id == t.id) {
                    task_ref.lock().unwrap().cancel.store(true, Ordering::Relaxed);
                }
            }
        }
        if let Some(rid) = action_remove {
            self.downloads.lock().unwrap().retain(|t| t.lock().unwrap().id != rid);
        }
        for p in action_retry {
            start_task(
                self.cfg.clone(),
                self.downloads.clone(),
                p.filename,
                p.subdir,
                DlMeta { download_url: p.download_url, source: p.source, expected_sha256: p.sha256, desc: p.desc },
                p.size_kb,
            );
        }
        if let Some(id) = action_detail {
            self.dl_detail = Some(id);
        }
    }

    // 作品页资源清单：勾选后批量入队
    fn ui_pending_set(&mut self, ctx: &egui::Context) {
        if self.pending_set.is_empty() {
            return;
        }
        let mut open = true;
        let mut act: Option<bool> = None; // Some(true)=下载选中, Some(false)=取消
        let n_sel = self.pending_set.iter().filter(|(_, on)| *on).count();
        egui::Window::new(format!("作品引用的资源 ({})", self.pending_set.len()))
            .collapsible(false)
            .resizable(false)
            .open(&mut open)
            .show(ctx, |ui| {
                ui.weak("解析自作品页的 Resources used，勾选要下载的项（自动归类目录并校验 SHA256）：");
                ui.add_space(4.0);
                egui::ScrollArea::vertical().max_height(380.0).show(ui, |ui| {
                    for (r, on) in self.pending_set.iter_mut() {
                        ui.horizontal(|ui| {
                            ui.checkbox(on, "");
                            ui.vertical(|ui| {
                                ui.strong(&r.model_name);
                                ui.horizontal(|ui| {
                                    chip(ui, &r.kind, egui::Color32::from_rgb(30, 48, 82), egui::Color32::from_rgb(140, 180, 248));
                                    if !r.base.is_empty() {
                                        chip(ui, &r.base, egui::Color32::from_rgb(42, 42, 52), C_GRAY);
                                    }
                                    if r.size_kb > 0.0 {
                                        ui.small(fmt_size((r.size_kb * 1024.0) as u64));
                                    }
                                });
                                ui.weak(format!("{} → {}", r.filename, r.subdir));
                                if !r.desc.is_empty() {
                                    ui.add(egui::Label::new(egui::RichText::new(&r.desc).small().color(C_GRAY)).truncate())
                                        .on_hover_text(&r.desc);
                                }
                            });
                        });
                        ui.separator();
                    }
                });
                // 磁盘空间预检：勾选项按大小累加，对目标盘聚合判断（多数落同一盘，取首个选中项的盘为代表）
                let sel_total: u64 = self
                    .pending_set
                    .iter()
                    .filter(|(_, on)| *on)
                    .map(|(r, _)| (r.size_kb * 1024.0) as u64)
                    .sum();
                let first_subdir = self.pending_set.iter().find(|(_, on)| *on).map(|(r, _)| r.subdir.clone());
                let set_check = match first_subdir {
                    Some(sub) => self.cached_disk_check(&sub, sel_total),
                    None => DiskCheck::Unknown,
                };
                let set_block = matches!(set_check, DiskCheck::Insufficient { .. });
                if let Some((block, msg)) = set_check.warning() {
                    ui.colored_label(if block { C_RED } else { C_YELLOW }, msg);
                }
                ui.add_space(4.0);
                ui.horizontal(|ui| {
                    if ui.add_enabled(n_sel > 0 && !set_block, accent_btn(&format!("下载选中 ({})", n_sel))).clicked() {
                        act = Some(true);
                    }
                    if ui.button("取消").clicked() {
                        act = Some(false);
                    }
                });
            });
        match act {
            Some(true) => {
                let items: Vec<Resolved> = std::mem::take(&mut self.pending_set)
                    .into_iter()
                    .filter(|(_, on)| *on)
                    .map(|(r, _)| r)
                    .collect();
                for r in items {
                    let sha = Some(r.sha256.clone()).filter(|s| !s.is_empty());
                    start_task(
                        self.cfg.clone(),
                        self.downloads.clone(),
                        r.filename.clone(),
                        r.subdir.clone(),
                        DlMeta { download_url: r.download_url.clone(), source: "civitai".into(), expected_sha256: sha, desc: r.desc.clone() },
                        r.size_kb,
                    );
                }
            }
            Some(false) => self.pending_set.clear(),
            None => {
                if !open {
                    self.pending_set.clear();
                }
            }
        }
    }

    fn ui_pending(&mut self, ctx: &egui::Context) {
        let mut open = self.pending.is_some();
        if !open {
            return;
        }
        let mut start = false;
        let mut cancel = false;
        let show_previews = self.cfg.show_previews;
        if let Some(r) = self.pending.clone() {
            egui::Window::new(if r.model_name.is_empty() { r.filename.clone() } else { r.model_name.clone() })
                .collapsible(false)
                .resizable(false)
                .open(&mut open)
                .show(ctx, |ui| {
                    if !r.image.is_empty() {
                        preview_img(ui, &r.image, 260.0, 220.0, 8.0, show_previews);
                    }
                    let sel_ver = r.versions.iter().find(|v| v.id == self.sel_version);
                    ui.horizontal(|ui| {
                        ui.small(&r.kind);
                        if !r.base.is_empty() { ui.small(&r.base); }
                        let size_kb = sel_ver.map(|v| v.size_kb).filter(|s| *s > 0.0).unwrap_or(r.size_kb);
                        if size_kb > 0.0 { ui.small(fmt_size((size_kb * 1024.0) as u64)); }
                        let has_sha = sel_ver.map(|v| !v.sha256.is_empty()).unwrap_or(!r.sha256.is_empty());
                        if has_sha { ui.small("· 将校验 SHA256"); }
                    });
                    if !r.desc.is_empty() {
                        ui.add_space(2.0);
                        ui.add(egui::Label::new(egui::RichText::new(&r.desc).small().color(C_GRAY)));
                    }
                    if r.versions.len() > 1 {
                        let prev_ver = self.sel_version;
                        egui::ComboBox::from_label("版本")
                            .selected_text(
                                r.versions.iter().find(|v| v.id == self.sel_version).map(|v| v.name.clone()).unwrap_or_default(),
                            )
                            .show_ui(ui, |ui| {
                                for v in &r.versions {
                                    ui.selectable_value(&mut self.sel_version, v.id, format!("{} · {}", v.name, v.base));
                                }
                            });
                        if self.sel_version != prev_ver {
                            // 切换版本后同步文件名，否则会下到新版本字节却存成旧版本文件名
                            if let Some(v) = r.versions.iter().find(|v| v.id == self.sel_version) {
                                if !v.filename.is_empty() {
                                    self.edit_name = v.filename.clone();
                                }
                            }
                        }
                    }
                    ui.horizontal(|ui| {
                        ui.label("保存为");
                        ui.add(egui::TextEdit::singleline(&mut self.edit_name).desired_width(360.0));
                    });
                    ui.horizontal(|ui| {
                        ui.label("放入目录");
                        egui::ComboBox::from_id_salt("subdir")
                            .selected_text(&self.edit_subdir)
                            .show_ui(ui, |ui| {
                                for d in ["models/checkpoints", "models/loras", "models/unet", "models/vae", "models/text_encoders", "models/controlnet", "models/embeddings", "models/upscale_models"] {
                                    ui.selectable_value(&mut self.edit_subdir, d.to_string(), d);
                                }
                            });
                    });
                    // 磁盘空间预检：紧张黄字警告(可继续)，不足红字并禁用「开始下载」
                    let need_bytes = {
                        let size_kb = sel_ver.map(|v| v.size_kb).filter(|s| *s > 0.0).unwrap_or(r.size_kb);
                        (size_kb * 1024.0) as u64
                    };
                    let subdir = self.edit_subdir.clone();
                    let check = self.cached_disk_check(&subdir, need_bytes);
                    let space_block = matches!(check, DiskCheck::Insufficient { .. });
                    if let Some((block, msg)) = check.warning() {
                        ui.colored_label(if block { C_RED } else { C_YELLOW }, msg);
                    }
                    ui.add_space(4.0);
                    ui.horizontal(|ui| {
                        if ui.add_enabled(!space_block, accent_btn("开始下载")).clicked() {
                            start = true;
                        }
                        if ui.button("取消").clicked() {
                            cancel = true;
                        }
                    });
                });
        }
        if start {
            if let Some(r) = self.pending.take() {
                let url = if r.source == "civitai" {
                    format!("https://{}/api/download/models/{}", self.cfg.civitai_host, self.sel_version)
                } else {
                    r.download_url.clone()
                };
                let sel = r.versions.iter().find(|v| v.id == self.sel_version);
                let size_kb = sel.map(|v| v.size_kb).filter(|s| *s > 0.0).unwrap_or(r.size_kb);
                // 哈希必须严格对应所选版本：选中的版本无哈希就不校验，绝不回退到其他版本的哈希
                let sha = match sel {
                    Some(v) => Some(v.sha256.clone()).filter(|s| !s.is_empty()),
                    None => Some(r.sha256.clone()).filter(|s| !s.is_empty()),
                };
                start_task(
                    self.cfg.clone(),
                    self.downloads.clone(),
                    self.edit_name.clone(),
                    self.edit_subdir.clone(),
                    DlMeta { download_url: url, source: r.source.clone(), expected_sha256: sha, desc: r.desc.clone() },
                    size_kb,
                );
            }
        } else if cancel || !open {
            self.pending = None;
        }
    }
}

// 在系统文件管理器里定位文件（Windows 资源管理器选中 / macOS Finder / Linux 打开目录）
fn open_in_file_manager(path: &Path) {
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("explorer").arg("/select,").arg(path).spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg("-R").arg(path).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Some(dir) = path.parent() {
            let _ = std::process::Command::new("xdg-open").arg(dir).spawn();
        }
    }
}

// 下载完成后写一个侧写文件，保存简介、来源、SHA256 等元数据，便于日后查找
fn write_info_sidecar(dest: &Path, source: &str, url: &str, desc: &str, sha256: Option<&str>) {
    let info = dest.with_extension(format!(
        "{}.info.txt",
        dest.extension().and_then(|s| s.to_str()).unwrap_or("")
    ));
    if info.exists() {
        return; // 不覆盖已有信息文件
    }
    let mut s = String::new();
    s.push_str(&format!("source: {}\n", source));
    s.push_str(&format!("url: {}\n", url));
    if let Some(h) = sha256 {
        s.push_str(&format!("sha256: {}\n", h));
    }
    if !desc.is_empty() {
        s.push_str("\n--- description ---\n");
        s.push_str(desc);
        s.push('\n');
    }
    let _ = fs::write(&info, s);
}

fn fmt_size(b: u64) -> String {
    let units = ["B", "KB", "MB", "GB"];
    let mut x = b as f64;
    let mut i = 0;
    while x >= 1024.0 && i < 3 {
        x /= 1024.0;
        i += 1;
    }
    format!("{:.1}{}", x, units[i])
}

fn fmt_duration(secs: u64) -> String {
    if secs < 60 {
        format!("{}s", secs)
    } else if secs < 3600 {
        format!("{}m {}s", secs / 60, secs % 60)
    } else {
        format!("{}h {}m", secs / 3600, (secs % 3600) / 60)
    }
}

// 基于当前速度和剩余大小估算剩余秒数；速度为 0 或任务未开始返回 None
fn eta_secs(downloaded: u64, total: u64, speed: f64) -> Option<u64> {
    if speed <= 0.0 || total <= downloaded {
        return None;
    }
    let remaining = (total - downloaded) as f64;
    Some((remaining / speed) as u64)
}

// ============ 预览图加载器（自定义 BytesLoader：显式代理 + 本地磁盘缓存） ============
// egui_extras 内置的 HTTP 图片加载器走 ehttp，不一定吃我们在设置里配的代理；这里改用项目
// 自己的 ureq agent（带显式代理 + JA3 绕过的本地端口隧道），把成功下载的预览图按 URL 哈希
// 缓存到磁盘，跨会话复用、避免重复下载，失败由 preview_img 的占位/重试兜底。
// 仅接管 http(s):// URI，其余（file:// 本地缩略图、bytes://）返回 NotSupported 交还默认加载器。
enum ImgState {
    Loading,
    Loaded(Arc<[u8]>, Option<String>),
    Failed,
}

struct PreviewLoader {
    agent: ureq::Agent,
    dir: PathBuf,
    cache: Arc<Mutex<std::collections::HashMap<String, ImgState>>>,
}

const PREVIEW_LOADER_ID: &str = "comfy_preview_loader";
// 磁盘缓存容量上限，超出时启动清理最旧的文件
const PREVIEW_CACHE_MAX_BYTES: u64 = 300 * 1024 * 1024;

impl PreviewLoader {
    fn new(cfg: &Config) -> Self {
        let dir = config_path().with_file_name("image-cache");
        let _ = fs::create_dir_all(&dir);
        prune_preview_cache(&dir, PREVIEW_CACHE_MAX_BYTES);
        PreviewLoader {
            agent: agent(cfg),
            dir,
            cache: Arc::new(Mutex::new(std::collections::HashMap::new())),
        }
    }
    fn cache_file(&self, uri: &str) -> PathBuf {
        let mut h = Sha256::new();
        h.update(uri.as_bytes());
        let digest = h.finalize();
        self.dir.join(format!("{}.img", hex_str(&digest)))
    }
}

// 启动时按 mtime 删除最旧的缓存文件，把磁盘占用压到上限内（避免长期使用后无限膨胀）
fn prune_preview_cache(dir: &Path, max_bytes: u64) {
    let Ok(rd) = fs::read_dir(dir) else { return; };
    let mut files: Vec<(PathBuf, u64, std::time::SystemTime)> = Vec::new();
    let mut total: u64 = 0;
    for e in rd.flatten() {
        if let Ok(meta) = e.metadata() {
            if meta.is_file() {
                total += meta.len();
                let mtime = meta.modified().unwrap_or(std::time::SystemTime::UNIX_EPOCH);
                files.push((e.path(), meta.len(), mtime));
            }
        }
    }
    if total <= max_bytes {
        return;
    }
    files.sort_by_key(|(_, _, t)| *t); // 最旧的在前
    for (p, sz, _) in files {
        if total <= max_bytes {
            break;
        }
        if fs::remove_file(&p).is_ok() {
            total = total.saturating_sub(sz);
        }
    }
}

impl egui::load::BytesLoader for PreviewLoader {
    fn id(&self) -> &str {
        PREVIEW_LOADER_ID
    }

    fn load(&self, ctx: &egui::Context, uri: &str) -> egui::load::BytesLoadResult {
        if !(uri.starts_with("http://") || uri.starts_with("https://")) {
            return Err(egui::load::LoadError::NotSupported);
        }
        // 内存缓存
        {
            let map = self.cache.lock().unwrap();
            match map.get(uri) {
                Some(ImgState::Loading) => return Ok(egui::load::BytesPoll::Pending { size: None }),
                Some(ImgState::Loaded(bytes, mime)) => {
                    return Ok(egui::load::BytesPoll::Ready {
                        size: None,
                        bytes: egui::load::Bytes::Shared(bytes.clone()),
                        mime: mime.clone(),
                    });
                }
                Some(ImgState::Failed) => {
                    return Err(egui::load::LoadError::Loading("预览图加载失败".into()));
                }
                None => {}
            }
        }
        // 磁盘缓存（预览图很小，同步读入即可）
        let file = self.cache_file(uri);
        if let Ok(data) = fs::read(&file) {
            if !data.is_empty() {
                let bytes: Arc<[u8]> = Arc::from(data.into_boxed_slice());
                self.cache.lock().unwrap().insert(uri.to_string(), ImgState::Loaded(bytes.clone(), None));
                return Ok(egui::load::BytesPoll::Ready { size: None, bytes: egui::load::Bytes::Shared(bytes), mime: None });
            }
        }
        // 起后台线程下载（UI 线程不阻塞），完成后写缓存并 request_repaint 唤醒
        self.cache.lock().unwrap().insert(uri.to_string(), ImgState::Loading);
        let net = self.agent.clone();
        let cache = self.cache.clone();
        let ctx = ctx.clone();
        let uri_owned = uri.to_string();
        std::thread::spawn(move || {
            let fetched: Option<(Arc<[u8]>, Option<String>)> = (|| {
                let resp = net.get(&uri_owned).call().ok()?;
                let mime = resp.header("Content-Type").map(|s| s.to_string());
                // 上限 64MB，防异常大响应吃内存
                let mut reader = resp.into_reader().take(64 * 1024 * 1024);
                let mut buf = Vec::new();
                reader.read_to_end(&mut buf).ok()?;
                if buf.is_empty() {
                    return None;
                }
                Some((Arc::from(buf.into_boxed_slice()), mime))
            })();
            match fetched {
                Some((bytes, mime)) => {
                    let _ = fs::write(&file, &bytes[..]);
                    cache.lock().unwrap().insert(uri_owned, ImgState::Loaded(bytes, mime));
                }
                None => {
                    cache.lock().unwrap().insert(uri_owned, ImgState::Failed);
                }
            }
            ctx.request_repaint();
        });
        Ok(egui::load::BytesPoll::Pending { size: None })
    }

    fn forget(&self, uri: &str) {
        self.cache.lock().unwrap().remove(uri);
        // 删磁盘缓存，确保「点击重试」真的重新下载而非读回旧的失败/损坏内容
        let _ = fs::remove_file(self.cache_file(uri));
    }

    fn forget_all(&self) {
        self.cache.lock().unwrap().clear();
        if let Ok(rd) = fs::read_dir(&self.dir) {
            for e in rd.flatten() {
                let _ = fs::remove_file(e.path());
            }
        }
    }

    fn byte_size(&self) -> usize {
        self.cache
            .lock()
            .unwrap()
            .values()
            .map(|s| match s {
                ImgState::Loaded(b, _) => b.len(),
                _ => 0,
            })
            .sum()
    }
}

// 字节头校验：损坏/零字节的字体喂给 egui 会让 epaint 在首帧 panic（启动必闪退）
fn valid_font(b: &[u8]) -> bool {
    b.len() >= 4
        && (b[..4] == [0x00, 0x01, 0x00, 0x00] || &b[..4] == b"OTTO" || &b[..4] == b"ttcf" || &b[..4] == b"true")
}

// 在字体目录里浅层递归找一个 CJK 字体（Arch/Fedora 等发行版路径不固定，按文件名特征匹配）。
// 命中后读取并校验，无效（损坏文件/断裂符号链接）则继续找下一个，不中断遍历。
fn find_cjk_font_in(dir: &Path, depth: u32) -> Option<Vec<u8>> {
    let rd = fs::read_dir(dir).ok()?;
    let mut subdirs = Vec::new();
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() {
            subdirs.push(p);
            continue;
        }
        let name = e.file_name().to_string_lossy().to_lowercase();
        let is_font = name.ends_with(".ttc") || name.ends_with(".ttf") || name.ends_with(".otf");
        if is_font && (name.contains("cjk") || name.contains("wqy") || name.contains("sourcehan")) {
            if let Ok(b) = fs::read(&p) {
                if valid_font(&b) {
                    return Some(b);
                }
            }
        }
    }
    if depth > 0 {
        for d in subdirs {
            if let Some(b) = find_cjk_font_in(&d, depth - 1) {
                return Some(b);
            }
        }
    }
    None
}

// 返回是否成功装上 CJK 字体；失败时 UI 顶部会用英文提示（中文此时渲染不出来）
fn install_cjk_font(ctx: &egui::Context) -> bool {
    let candidates = [
        "C:\\Windows\\Fonts\\msyh.ttc",
        "C:\\Windows\\Fonts\\simhei.ttf",
        "C:\\Windows\\Fonts\\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ];
    let mut bytes: Option<Vec<u8>> = None;
    for path in candidates {
        if let Ok(b) = fs::read(path) {
            if valid_font(&b) {
                bytes = Some(b);
                break;
            }
        }
    }
    if bytes.is_none() {
        let mut scan_dirs = vec![
            PathBuf::from("/usr/share/fonts"),
            PathBuf::from("/usr/local/share/fonts"),
            PathBuf::from("/System/Library/Fonts"),
        ];
        if let Some(home) = std::env::var_os("HOME") {
            let h = PathBuf::from(home);
            scan_dirs.push(h.join(".fonts"));
            scan_dirs.push(h.join(".local/share/fonts"));
        }
        for dir in scan_dirs {
            if let Some(b) = find_cjk_font_in(&dir, 3) {
                bytes = Some(b);
                break;
            }
        }
    }
    let Some(bytes) = bytes else { return false };
    let mut fonts = egui::FontDefinitions::default();
    fonts.font_data.insert("cjk".to_owned(), egui::FontData::from_owned(bytes));
    fonts.families.entry(egui::FontFamily::Proportional).or_default().insert(0, "cjk".to_owned());
    fonts.families.entry(egui::FontFamily::Monospace).or_default().push("cjk".to_owned());
    ctx.set_fonts(fonts);
    true
}

// 发送系统通知；失败时静默忽略，不干扰下载流程
fn notify(title: &str, body: &str) {
    let _ = notify_rust::Notification::new()
        .summary(title)
        .body(body)
        .timeout(notify_rust::Timeout::Milliseconds(6000))
        .show();
}

// 创建 32x32 的简单托盘图标（蓝底白 C），失败返回 None
fn tray_icon_rgba() -> Option<(Vec<u8>, u32, u32)> {
    const W: u32 = 32;
    const H: u32 = 32;
    let mut rgba = vec![0u8; (W * H * 4) as usize];
    for y in 0..H {
        for x in 0..W {
            let idx = ((y * W + x) * 4) as usize;
            // 圆角蓝底
            let cx = x as f32 - W as f32 / 2.0 + 0.5;
            let cy = y as f32 - H as f32 / 2.0 + 0.5;
            let r = (cx * cx + cy * cy).sqrt();
            if r < 14.0 {
                rgba[idx] = 96;
                rgba[idx + 1] = 145;
                rgba[idx + 2] = 240;
                rgba[idx + 3] = 255;
                // 简单的 "C" 字形（白色）
                let angle = cy.atan2(cx);
                if r > 6.0 && r < 11.0 && angle.abs() > 0.7 {
                    rgba[idx] = 255;
                    rgba[idx + 1] = 255;
                    rgba[idx + 2] = 255;
                }
            }
        }
    }
    Some((rgba, W, H))
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum TrayCmd {
    Show,
    Hide,
    Toggle,
    PauseResume,
    Exit,
}

fn handle_tray_cmd_now(cmd: TrayCmd) {
    // 跨平台：所有窗口操作通过原子标志传递，由 App::update 消费用 ViewportCommand 执行。
    // 不再直接调 Win32 ShowWindow，macOS/Linux 也能正常工作。
    match cmd {
        TrayCmd::Show => {
            PENDING_TRAY_CMD.store(1, Ordering::Relaxed);
        }
        TrayCmd::Hide => {
            PENDING_TRAY_CMD.store(2, Ordering::Relaxed);
        }
        TrayCmd::Toggle => {
            PENDING_TRAY_CMD.store(3, Ordering::Relaxed);
        }
        TrayCmd::PauseResume => {
            if PAUSED.load(Ordering::Relaxed) {
                // 恢复要重新入队「已暂停」任务（需 App 状态），托盘无 App 访问：
                // 只置请求标志，由 App::update 消费并调 resume_all（与顶栏按钮同一逻辑）。
                RESUME_REQUESTED.store(true, Ordering::Relaxed);
            } else {
                PAUSED.store(true, Ordering::Relaxed);
            }
        }
        TrayCmd::Exit => {
            // 通过原子标志让 App::update 执行 Close，会触发 on_exit 持久化任务后再退出
            PENDING_TRAY_CMD.store(4, Ordering::Relaxed);
        }
    }
}

// 在主线程创建托盘图标与菜单；托盘事件直接操作主窗口。
// 必须与 winit/eframe 事件循环同线程，否则 Windows 消息无法分派，点击无响应。
fn setup_tray() -> Option<tray_icon::TrayIcon> {
    let (rgba, w, h) = tray_icon_rgba()?;
    let icon = tray_icon::Icon::from_rgba(rgba, w, h).ok()?;
    let menu = tray_icon::menu::Menu::new();
    let show_i = tray_icon::menu::MenuItem::new("显示主窗口", true, None);
    let hide_i = tray_icon::menu::MenuItem::new("隐藏窗口", true, None);
    let pause_i = tray_icon::menu::MenuItem::new("暂停 / 恢复", true, None);
    let exit_i = tray_icon::menu::MenuItem::new("退出", true, None);
    let _ = menu.append(&show_i);
    let _ = menu.append(&hide_i);
    let _ = menu.append(&pause_i);
    let _ = menu.append(&exit_i);

    let tray = tray_icon::TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip(APP_NAME)
        .with_icon(icon)
        .build()
        .ok()?;

    let cmd_map: std::collections::HashMap<tray_icon::menu::MenuId, TrayCmd> = [
        (show_i.id().clone(), TrayCmd::Show),
        (hide_i.id().clone(), TrayCmd::Hide),
        (pause_i.id().clone(), TrayCmd::PauseResume),
        (exit_i.id().clone(), TrayCmd::Exit),
    ]
    .into_iter()
    .collect();

    // 菜单事件直接操作主窗口
    tray_icon::menu::MenuEvent::set_event_handler(Some(move |event: tray_icon::menu::MenuEvent| {
        if let Some(&cmd) = cmd_map.get(&event.id) {
            handle_tray_cmd_now(cmd);
        }
    }));

    // 左键单击/双击托盘图标：切换显示/隐藏
    tray_icon::TrayIconEvent::set_event_handler(Some(move |event: tray_icon::TrayIconEvent| {
        let is_left = matches!(
            event,
            tray_icon::TrayIconEvent::Click {
                button: tray_icon::MouseButton::Left,
                ..
            } | tray_icon::TrayIconEvent::DoubleClick {
                button: tray_icon::MouseButton::Left,
                ..
            }
        );
        if is_left {
            handle_tray_cmd_now(TrayCmd::Toggle);
        }
    }));

    Some(tray)
}

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1120.0, 800.0])
            .with_title(WINDOW_TITLE),
        ..Default::default()
    };
    eframe::run_native(
        WINDOW_TITLE, // 窗口标题带署名 "— by Winery"
        options,
        Box::new(|cc| Ok(Box::new(App::new(cc)))),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn justify_rows_fills_width_and_covers_all() {
        let aspects = vec![1.5, 0.7, 1.0, 1.8, 0.6, 1.2];
        let avail_w = 460.0;
        let gap = 8.0;
        let target_h = 190.0;
        let rows = justify_rows(&aspects, avail_w, gap, target_h);

        // 覆盖所有图、不重不漏，下标连续
        let total: usize = rows.iter().map(|(_, n, _)| *n).sum();
        assert_eq!(total, aspects.len());
        let mut cursor = 0;
        for &(start, n, _) in &rows {
            assert_eq!(start, cursor);
            cursor += n;
        }

        // 每个非末行精确铺满列宽（误差 < 1px）
        for (idx, &(start, n, h)) in rows.iter().enumerate() {
            let row_w: f32 =
                aspects[start..start + n].iter().map(|a| a * h).sum::<f32>() + gap * (n as f32 - 1.0);
            if idx != rows.len() - 1 {
                assert!((row_w - avail_w).abs() < 1.0, "行{idx} 宽 {row_w} ≠ {avail_w}");
            }
            assert!(h > 0.0);
        }

        // 边界：空输入不 panic；超宽单图自成一行
        assert!(justify_rows(&[], avail_w, gap, target_h).is_empty());
        assert_eq!(justify_rows(&[5.0], avail_w, gap, target_h).len(), 1);
        // 零/异常宽高比不 panic（aspect() 已兜底，这里再确认纯函数自身健壮）
        assert_eq!(justify_rows(&[0.0, 0.0], avail_w, gap, target_h).iter().map(|(_, n, _)| n).sum::<usize>(), 2);
    }

    #[test]
    fn parse_version_extracts_trigger_words_and_date() {
        let ver: serde_json::Value = serde_json::from_str(
            r#"{
                "id": 123, "name": "v1.1 - IL", "baseModel": "Illustrious",
                "publishedAt": "2024-09-08T12:34:56.000Z",
                "createdAt": "2024-09-01T00:00:00.000Z",
                "trainedWords": ["  mugi  ", "hitohira", "", "  "],
                "files": [{"primary": true, "name": "foo.safetensors", "sizeKB": 332800.0,
                           "hashes": {"SHA256": "ABCDEF"}}]
            }"#,
        )
        .unwrap();
        let v = parse_version(&ver);
        assert_eq!(v.id, 123);
        assert_eq!(v.base, "Illustrious");
        assert_eq!(v.filename, "foo.safetensors");
        assert_eq!(v.sha256, "abcdef"); // 统一小写
        assert_eq!(v.trained_words, vec!["mugi", "hitohira"]); // 去首尾空白、丢空串
        assert_eq!(v.published_at, "2024-09-08"); // 取日期前 10 位

        // publishedAt 缺失 → 退回 createdAt；无 trainedWords → 空
        let ver2: serde_json::Value =
            serde_json::from_str(r#"{"id":1,"createdAt":"2023-01-02T00:00:00Z","files":[]}"#).unwrap();
        let v2 = parse_version(&ver2);
        assert_eq!(v2.published_at, "2023-01-02");
        assert!(v2.trained_words.is_empty());
    }

    #[test]
    fn parse_hf_search_extracts_repos() {
        let v: serde_json::Value = serde_json::from_str(
            r#"[
                {"id":"black-forest-labs/FLUX.1-dev","downloads":123456,"likes":789,"pipeline_tag":"text-to-image"},
                {"modelId":"city96/FLUX.1-dev-gguf","downloads":50000,"likes":120},
                {"downloads":1,"likes":1}
            ]"#,
        )
        .unwrap();
        let repos = parse_hf_search(&v);
        assert_eq!(repos.len(), 2); // 第三个无 id/modelId，丢弃
        assert_eq!(repos[0].id, "black-forest-labs/FLUX.1-dev");
        assert_eq!(repos[0].pipeline_tag, "text-to-image");
        assert_eq!(repos[1].id, "city96/FLUX.1-dev-gguf"); // modelId 兜底
        assert_eq!(repos[1].pipeline_tag, ""); // 缺失给空
        assert!(parse_hf_search(&serde_json::json!({"error":"x"})).is_empty()); // 非数组不 panic
    }

    #[test]
    fn parse_hf_files_filters_and_sorts() {
        let v: serde_json::Value = serde_json::from_str(
            r#"[
                {"type":"file","path":"README.md","size":1024},
                {"type":"file","path":"flux1-dev.safetensors","lfs":{"size":23800000000}},
                {"type":"directory","path":"vae"},
                {"type":"file","path":"vae/diffusion_pytorch_model.safetensors","lfs":{"size":335000000}},
                {"type":"file","path":"model.gguf","size":11900000000}
            ]"#,
        )
        .unwrap();
        let files = parse_hf_files(&v);
        assert_eq!(files.len(), 3); // README(非模型扩展名) 与 目录 被滤掉
        // 按大小降序：safetensors 23.8G > gguf 11.9G > vae 335M
        assert_eq!(files[0].path, "flux1-dev.safetensors");
        assert_eq!(files[0].size, 23_800_000_000);
        assert_eq!(files[1].path, "model.gguf"); // 用 size（非 LFS）
        assert_eq!(files[2].path, "vae/diffusion_pytorch_model.safetensors");
        assert!(parse_hf_files(&serde_json::json!({})).is_empty());
    }

    #[test]
    fn disk_precheck_rules() {
        let gb = 1024u64 * 1024 * 1024;
        assert_eq!(disk_precheck(None, 5 * gb), DiskCheck::Unknown); // 未知 → 放行
        assert_eq!(disk_precheck(Some(gb), 0), DiskCheck::Ok); // 无大小 → 不打扰
        assert_eq!(disk_precheck(Some(100 * gb), 10 * gb), DiskCheck::Ok); // 充足
        assert!(matches!(disk_precheck(Some(gb), 5 * gb), DiskCheck::Insufficient { .. })); // 放不下
        // 能放下文件但余量不足 → Tight（10G 文件，余量 5%=0.5G，need=10.5G，avail=10.2G）
        assert!(matches!(disk_precheck(Some(10 * gb + 200 * 1024 * 1024), 10 * gb), DiskCheck::Tight { .. }));
        // 边界：avail==file_bytes → 余量=0 < margin → Tight
        assert!(matches!(disk_precheck(Some(5 * gb), 5 * gb), DiskCheck::Tight { .. }));
        // 边界：avail 比文件少 1 字节 → Insufficient
        assert!(matches!(disk_precheck(Some(5 * gb - 1), 5 * gb), DiskCheck::Insufficient { .. }));
    }

    #[test]
    fn disk_margin_floor_and_ratio() {
        let mb = 1024u64 * 1024;
        let gb = 1024 * mb;
        // 小文件(50MB)：余量取 300MB 下限 → need≈350MB
        assert!(matches!(disk_precheck(Some(320 * mb), 50 * mb), DiskCheck::Tight { .. })); // 320<350
        assert_eq!(disk_precheck(Some(400 * mb), 50 * mb), DiskCheck::Ok); // 400>350
        // 大文件(20GB)：余量 5%=1GB → need=21GB
        assert_eq!(disk_precheck(Some(21 * gb + 600 * mb), 20 * gb), DiskCheck::Ok); // 21.6G>21G
        assert!(matches!(disk_precheck(Some(20 * gb + 512 * mb), 20 * gb), DiskCheck::Tight { .. })); // 20.5G<21G 但>20G
    }

    #[test]
    fn remaining_subtracts_part_progress() {
        let gb = 1024u64 * 1024 * 1024;
        // 续传核心：19G 的 .part / 20G 整文件 → 只还需 1G（修复前会用整 20G 误杀续传）
        assert_eq!(remaining_to_download(false, 20 * gb, 19 * gb), gb);
        // 全新下载（无 .part）→ 整文件
        assert_eq!(remaining_to_download(false, 20 * gb, 0), 20 * gb);
        // 正式文件已存在 → 0（download_file 会判已存在，不占新空间）
        assert_eq!(remaining_to_download(true, 20 * gb, 0), 0);
        // .part 比记录的整文件还大（异常）→ 饱和到 0，不下溢
        assert_eq!(remaining_to_download(false, 20 * gb, 21 * gb), 0);
    }

    #[test]
    fn nearest_existing_ancestor_walks_up() {
        let tmp = std::env::temp_dir();
        assert!(tmp.exists());
        assert_eq!(nearest_existing_ancestor(&tmp), tmp); // 已存在 → 原样
        let deep = tmp.join("comfy_dl__nope__zzz/a/b/c"); // 不存在 → 回溯到 tmp
        assert_eq!(nearest_existing_ancestor(&deep), tmp);
    }

    #[test]
    fn resolve_dest_dir_uses_download_root() {
        // 设了 download_root → <root>/<去 models/ 前缀的类型>（短路 detect_desktop，确定性）
        let mut cfg = test_cfg(Path::new("Z:/__fake_comfy__"));
        cfg.download_root = "E:/aimodels".into();
        assert_eq!(resolve_dest_dir(&cfg, "models/loras"), expand_root("E:/aimodels").join("loras"));
        assert_eq!(resolve_dest_dir(&cfg, "models/text_encoders"), expand_root("E:/aimodels").join("text_encoders"));
    }

    /// 真实平台 API：临时目录所在盘可用空间。手动运行: cargo test -- --ignored
    #[test]
    #[ignore]
    fn available_space_e2e() {
        let tmp = std::env::temp_dir();
        let avail = available_space_bytes(&tmp);
        assert!(avail.is_some(), "应能取到可用空间");
        assert!(avail.unwrap() > 0);
        println!("temp 可用 {}", fmt_size(avail.unwrap()));
        // 不存在的路径不 panic（nearest_existing_ancestor 回退到根目录）
        let _ = available_space_bytes(Path::new("/__no_such_dir__/x"));
    }

    #[test]
    fn plan_chunks_covers_exactly() {
        for &total in &[0u64, 1, 3, 4, 5, 100, 1_000_003] {
            for &n in &[1usize, 2, 4] {
                let chunks = plan_chunks(total, n);
                if total == 0 {
                    assert!(chunks.is_empty());
                    continue;
                }
                assert_eq!(chunks.first().unwrap().0, 0, "首块从 0 始");
                assert_eq!(chunks.last().unwrap().1, total - 1, "末块到 total-1");
                for w in chunks.windows(2) {
                    assert_eq!(w[1].0, w[0].1 + 1, "块间首尾相接无空洞无重叠");
                }
                for &(s, e) in &chunks {
                    assert!(s <= e, "每块非空");
                }
                let sum: u64 = chunks.iter().map(|(s, e)| e - s + 1).sum();
                assert_eq!(sum, total, "总字节 = total");
                assert!(chunks.len() <= (n as u64).min(total) as usize, "块数收敛");
            }
        }
    }

    #[test]
    fn multipart_gating() {
        let big = 100 * 1024 * 1024;
        assert!(should_use_multipart("hf", true, big));
        assert!(!should_use_multipart("civitai", true, big)); // civitai 永远不并发
        assert!(!should_use_multipart("hf", false, big)); // 不支持 Range
        assert!(!should_use_multipart("hf", true, 10 * 1024 * 1024)); // 太小
    }

    #[test]
    fn parse_range_probe_reads_total() {
        let ri = parse_range_probe(206, Some("bytes 0-0/123456789"));
        assert_eq!(ri.total, 123456789);
        assert!(ri.supports_range);
        assert!(!parse_range_probe(200, None).supports_range); // 200 忽略 Range
        assert!(!parse_range_probe(206, None).supports_range); // 无 Content-Range
        assert!(!parse_range_probe(206, Some("bytes 0-0/0")).supports_range); // total 0
    }

    /// 真实网络：多连接分块下载并与单连接逐字节比对（验证分块重组正确）。cargo test -- --ignored
    #[test]
    #[ignore]
    fn download_multipart_e2e() {
        let tmp = std::env::temp_dir().join("comfy_dl_mp_e2e");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let cfg = test_cfg(&tmp);
        let url = "https://hf-mirror.com/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/README.md";
        let meta = DlMeta { download_url: url.into(), source: "hf".into(), expected_sha256: None, desc: String::new() };
        let ri = probe_range(&cfg, &meta).expect("应支持 Range");
        assert!(ri.total > 0);
        let t = new_task();
        let dest = tmp.join("mp.bin");
        let part = tmp.join("mp.bin.part");
        download_multipart(&cfg, &t, &dest, &part, &meta, &ri).unwrap();
        let mp_bytes = fs::read(&dest).unwrap();
        assert_eq!(mp_bytes.len() as u64, ri.total, "多块下载大小应等于总长");
        let plain = agent(&cfg).get(url).call().unwrap().into_string().unwrap();
        assert_eq!(mp_bytes, plain.as_bytes(), "多块重组内容应与单连接逐字节一致");
        println!("多块 {} 字节（{} 块），与单连接逐字节一致", mp_bytes.len(), plan_chunks(ri.total, MULTIPART_CONNS).len());
    }

    #[test]
    fn sha256_hex_and_wf_hashes() {
        assert!(is_sha256_hex(&"a".repeat(64)));
        assert!(is_sha256_hex("ABCDEF0123456789abcdef0123456789ABCDEF0123456789abcdef0123456789"));
        assert!(!is_sha256_hex(&"a".repeat(63)));
        assert!(!is_sha256_hex(&"g".repeat(64))); // 非 hex
        let h64 = "ab".repeat(32);
        let v: serde_json::Value = serde_json::from_str(&format!(
            r#"{{"nodes":[{{"properties":{{"models":[{{"name":"foo.safetensors","hash":"{h64}"}}]}}}}]}}"#
        ))
        .unwrap();
        let h = collect_wf_hashes(&v);
        assert_eq!(h.get("foo.safetensors"), Some(&h64)); // 同对象内文件名↔哈希配对
    }

    #[test]
    fn score_filename_match_levels() {
        assert_eq!(score_filename_match("Foo-Bar_v1.safetensors", "foo-bar_v1.ckpt"), 100); // 同名忽略扩展/大小写
        assert_eq!(score_filename_match("flux1-dev-fp8.safetensors", "flux1-dev.safetensors"), 80); // 去精度后缀相等
        assert_eq!(score_filename_match("wan22_i2v.safetensors", "my_wan22_i2v_hi.safetensors"), 60); // 子串
        assert_eq!(score_filename_match("totally_unrelated.safetensors", "something_else_x.gguf"), 0);
    }

    #[test]
    fn quant_strip_removes_suffixes() {
        assert_eq!(quant_precision_strip("flux1-dev-fp8"), "flux1-dev");
        assert_eq!(quant_precision_strip("model-q4_k_m"), "model");
        assert_eq!(quant_precision_strip("wan_bf16"), "wan");
        assert_eq!(quant_precision_strip("flux1-dev-fp8-scaled"), "flux1-dev"); // 叠加后缀都剥
        // 后缀锚定：不删词中间（修复前 _q6_k 会命中 kungfu 中间 → modelungfu）
        assert_eq!(quant_precision_strip("model_q6_kungfu"), "model_q6_kungfu");
    }

    #[test]
    fn collect_wf_hashes_skips_ambiguous() {
        let h = "cd".repeat(32);
        // 同一对象里两个模型文件名 → 歧义，跳过，不把哈希错配给某个
        let v: serde_json::Value = serde_json::from_str(&format!(
            r#"{{"x":{{"a":"foo.safetensors","b":"bar.safetensors","hash":"{h}"}}}}"#
        ))
        .unwrap();
        assert!(collect_wf_hashes(&v).is_empty());
    }

    /// 真实网络：缺失模型跨源解析。手动运行: cargo test -- --ignored
    #[test]
    #[ignore]
    fn resolve_missing_e2e() {
        let tmp = std::env::temp_dir().join("comfy_dl_resolve_e2e");
        let cfg = test_cfg(&tmp);
        let m = WfModel {
            name: "flux1-dev.safetensors".into(),
            dir: "models/unet".into(),
            found_at: String::new(),
            in_comfy: false,
            dl: None,
            wf_hash: None,
        };
        let cands = resolve_missing_one(&cfg, &m);
        assert!(!cands.is_empty(), "应找到候选");
        println!("flux1-dev.safetensors 候选 {} 个：", cands.len());
        for c in cands.iter().take(5) {
            println!("  [{}|{}] {} ({})", c.source, c.score, c.filename, c.label);
        }
    }

    #[test]
    fn repo_dir_name_extracts() {
        assert_eq!(repo_dir_name("https://github.com/x/ComfyUI-Foo.git"), "ComfyUI-Foo");
        assert_eq!(repo_dir_name("https://github.com/x/ComfyUI-Foo"), "ComfyUI-Foo");
        assert_eq!(repo_dir_name("https://github.com/x/Bar/"), "Bar");
        assert_eq!(repo_dir_name("git@github.com:user/Baz.git"), "Baz");
    }

    #[test]
    fn is_git_url_validates() {
        assert!(is_git_url("https://github.com/a/b"));
        assert!(is_git_url("http://example.com/a/b.git"));
        assert!(is_git_url("git@github.com:a/b.git"));
        assert!(!is_git_url(""));
        assert!(!is_git_url("ComfyUI-Foo")); // 纯名字
        assert!(!is_git_url("ftp://x/y")); // 非 http/git
        assert!(!is_git_url("https://")); // 太短
    }

    #[test]
    fn safe_node_name_blocks_traversal() {
        assert_eq!(safe_node_name("https://github.com/a/Foo.git"), Some("Foo".into()));
        assert_eq!(safe_node_name("https://github.com/a/b"), Some("b".into()));
        assert_eq!(safe_node_name("https://host/x/.."), None); // 上跳到父目录
        assert_eq!(safe_node_name("https://host/a/.git"), None); // 空名
        assert_eq!(safe_node_name("https://host/x/..git"), None); // 退化成 "."
    }

    #[test]
    fn parse_node_registry_extracts() {
        let body = r#"{"custom_nodes":[
            {"title":"Foo","reference":"https://github.com/a/Foo","author":"alice","description":"do foo"},
            {"title":"NoUrl","reference":"","author":"bob"},
            {"title":"Bar","reference":"https://github.com/c/Bar.git"}
        ]}"#;
        let list = parse_node_registry(body);
        assert_eq!(list.len(), 2); // 无可克隆 reference 的被跳过
        assert_eq!(list[0].title, "Foo");
        assert_eq!(list[0].author, "alice");
        assert_eq!(list[1].title, "Bar");
        assert_eq!(list[1].author, ""); // 缺 author 给空
        assert!(parse_node_registry("not json").is_empty()); // 非法 JSON 不 panic
        assert!(parse_node_registry(r#"{"other":1}"#).is_empty()); // 无 custom_nodes
    }

    /// 真实网络：拉 ComfyUI-Manager 节点注册表。手动运行: cargo test -- --ignored
    #[test]
    #[ignore]
    fn fetch_node_registry_e2e() {
        let tmp = std::env::temp_dir().join("comfy_dl_reg_e2e");
        let cfg = test_cfg(&tmp);
        let list = fetch_node_registry(&cfg).expect("拉取注册表应成功");
        assert!(list.len() > 100, "注册表应有大量条目，实得 {}", list.len());
        assert!(list.iter().all(|r| is_git_url(&r.reference)));
        println!("注册表 {} 个节点，首个: {} ({})", list.len(), list[0].title, list[0].reference);
    }

    #[test]
    fn sanitize_strips_illegal_chars() {
        assert_eq!(
            sanitize_filename("a/b\\c:d*e?f\"g<h>i|j.safetensors"),
            "a_b_c_d_e_f_g_h_i_j.safetensors"
        );
        assert_eq!(sanitize_filename("  .hidden.  "), "hidden");
        assert_eq!(sanitize_filename("..\\..\\evil.bin"), "_.._evil.bin");
        assert_eq!(sanitize_filename(""), "unnamed");
        assert_eq!(sanitize_filename("正常文件名.gguf"), "正常文件名.gguf");
    }

    #[test]
    fn percent_decode_basic() {
        assert_eq!(percent_decode("my%20file.safetensors"), "my file.safetensors");
        assert_eq!(percent_decode("%E6%A8%A1%E5%9E%8B.bin"), "模型.bin");
        assert_eq!(percent_decode("nothing"), "nothing");
        assert_eq!(percent_decode("bad%zz"), "bad%zz");
        assert_eq!(percent_decode("end%2"), "end%2");
    }

    #[test]
    fn expand_root_tilde() {
        let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")).unwrap();
        assert_eq!(expand_root("~/ComfyUI"), PathBuf::from(&home).join("ComfyUI"));
        assert_eq!(expand_root("D:\\ComfyUI"), PathBuf::from("D:\\ComfyUI"));
    }

    #[test]
    fn font_magic_validation() {
        assert!(valid_font(&[0x00, 0x01, 0x00, 0x00, 0xAA]));
        assert!(valid_font(b"OTTOxxxx"));
        assert!(valid_font(b"ttcfxxxx"));
        assert!(!valid_font(b""));
        assert!(!valid_font(b"abc"));
        assert!(!valid_font(b"<html>not a font</html>"));
    }

    #[test]
    fn type_mapping() {
        assert_eq!(type_dir("LORA"), "models/loras");
        assert_eq!(guess_type("wan2.2_vae.safetensors"), "VAE");
        assert_eq!(guess_type("model-Q5_K_M.gguf"), "Unet");
        assert_eq!(guess_type("umt5_xxl_fp8.safetensors"), "TextEncoder");
    }

    fn test_cfg(root: &Path) -> Config {
        // 用 Default 兜底，仅覆盖测试关心的字段；新增配置字段时不必再改这里
        Config {
            comfy_root: root.to_string_lossy().into_owned(),
            max_concurrent: 1,
            ..Default::default()
        }
    }

    fn new_task() -> TaskRef {
        Arc::new(Mutex::new(Task {
            id: 0,
            filename: String::new(),
            subdir: String::new(),
            status: String::new(),
            downloaded: 0,
            total: 0,
            speed: 0.0,
            error: String::new(),
            cancel: Arc::new(AtomicBool::new(false)),
            download_url: String::new(),
            source: String::new(),
            expected_sha256: None,
            verified: false,
            started_at: None,
            completed_at: None,
            local_path: None,
            desc: String::new(),
            notified: false,
        }))
    }

    #[test]
    fn persist_roundtrip() {
        let tmp = std::env::temp_dir().join("comfy_dl_tasks_test.json");
        let list = vec![PersistTask {
            filename: "a.safetensors".into(),
            subdir: "models/loras".into(),
            download_url: "https://example.com/x".into(),
            source: "hf".into(),
            size_kb: 12.5,
            sha256: Some("abcd".into()),
            desc: "test desc".into(),
        }];
        save_tasks_to(&tmp, &list);
        let back = load_tasks_from(&tmp);
        assert_eq!(back.len(), 1);
        assert_eq!(back[0].filename, "a.safetensors");
        assert_eq!(back[0].sha256.as_deref(), Some("abcd"));
        // 不存在/损坏的文件回退为空列表
        assert!(load_tasks_from(Path::new("Z:/__no_such__/tasks.json")).is_empty());
        let _ = fs::remove_file(&tmp);
    }

    // ---- M24.3：ModelRecord.trigger_words 字段与落盘 ----

    fn sample_resolved() -> Resolved {
        Resolved {
            source: "civitai".into(),
            model_name: "Mugi".into(),
            kind: "LORA".into(),
            base: "Illustrious".into(),
            filename: "mugi.safetensors".into(),
            size_kb: 1024.0,
            subdir: "loras".into(),
            image: String::new(),
            download_url: "https://example.com/mugi".into(),
            versions: vec![
                VerInfo {
                    id: 1,
                    name: "v1".into(),
                    base: "Illustrious".into(),
                    filename: "mugi.safetensors".into(),
                    size_kb: 1024.0,
                    sha256: "aa".into(),
                    trained_words: vec!["mugi".into(), "hitohira".into()],
                    published_at: String::new(),
                },
                VerInfo {
                    id: 2,
                    name: "v2".into(),
                    base: "Illustrious".into(),
                    filename: "mugi_v2.safetensors".into(),
                    size_kb: 2048.0,
                    sha256: "bb".into(),
                    trained_words: vec!["v2word".into()],
                    published_at: String::new(),
                },
            ],
            version_id: 1,
            model_id: 99,
            sha256: "aa".into(),
            desc: "d".into(),
        }
    }

    #[test]
    fn model_record_trigger_words_serde_compat() {
        // 旧格式 models.json（无 trigger_words 字段）→ 反序列化为空 vec，不报错
        let old = r#"[{"filename":"a.safetensors","subdir":"loras","source":"civitai","download_url":"u","sha256":null,"desc":"","model_id":null,"version_id":null,"size_kb":1.0,"downloaded_at":null}]"#;
        let list: Vec<ModelRecord> = serde_json::from_str(old).unwrap();
        assert_eq!(list.len(), 1);
        assert!(list[0].trigger_words.is_empty());

        // 新格式带 trigger_words → 序列化/反序列化往返一致
        let rec = resolved_to_record(&sample_resolved());
        let s = serde_json::to_string(&vec![rec]).unwrap();
        assert!(s.contains("trigger_words"));
        let back: Vec<ModelRecord> = serde_json::from_str(&s).unwrap();
        assert_eq!(back[0].trigger_words, vec!["mugi", "hitohira"]);
    }

    #[test]
    fn resolved_to_record_picks_selected_version_words() {
        // 选中 version_id=1 → 取 v1 的触发词
        let rec = resolved_to_record(&sample_resolved());
        assert_eq!(rec.trigger_words, vec!["mugi", "hitohira"]);
        assert_eq!(rec.model_id.as_deref(), Some("99"));
        assert_eq!(rec.version_id.as_deref(), Some("1"));

        // version_id 无匹配 → 空，不 panic
        let mut r2 = sample_resolved();
        r2.version_id = 999;
        assert!(resolved_to_record(&r2).trigger_words.is_empty());
        // versions 为空（HF 来源）→ 空
        let mut r3 = sample_resolved();
        r3.versions.clear();
        assert!(resolved_trigger_words(&r3).is_empty());
    }

    #[test]
    fn task_to_record_preserves_existing_trigger_words() {
        // 解析阶段已带词的记录
        let list = vec![resolved_to_record(&sample_resolved())];
        let mut t = new_task().lock().unwrap().clone();
        t.filename = "mugi.safetensors".into();
        t.subdir = "loras".into();
        t.status = "完成".into();
        t.total = 1024 * 1024;
        // 下载完成回写时不丢解析阶段落盘的触发词
        let words = existing_trigger_words(&list, &t.filename, &t.subdir);
        let rec = task_to_record(&t, words);
        assert_eq!(rec.trigger_words, vec!["mugi", "hitohira"]);
        assert_eq!(rec.size_kb, 1024.0);
        assert!(rec.downloaded_at.is_some());
        // 无既有记录 → 空
        assert!(existing_trigger_words(&list, "other.safetensors", "loras").is_empty());
        assert!(existing_trigger_words(&list, "mugi.safetensors", "vae").is_empty()); // subdir 不匹配
    }

    #[test]
    fn models_index_trigger_words_roundtrip() {
        // 落盘 → 读取，trigger_words 完整保留
        let tmp = std::env::temp_dir().join("comfy_models_trigger_words_test.json");
        let rec = resolved_to_record(&sample_resolved());
        save_models_index(&tmp, &[rec]);
        let back = load_models_index(&tmp);
        assert_eq!(back.len(), 1);
        assert_eq!(back[0].filename, "mugi.safetensors");
        assert_eq!(back[0].trigger_words, vec!["mugi", "hitohira"]);
        let _ = fs::remove_file(&tmp);
    }

    #[test]
    fn workflow_analysis_both_formats() {
        let tmp = std::env::temp_dir().join("comfy_wf_test_root");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(tmp.join("models/loras/sub")).unwrap();
        fs::write(tmp.join("models/loras/sub/have.safetensors"), b"x").unwrap();
        let cfg = test_cfg(&tmp);
        // API 格式：class_type + inputs；提示词等非模型字符串必须被过滤
        let api = r#"{
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "missing_ckpt.safetensors"}},
            "2": {"class_type": "LoraLoader", "inputs": {"lora_name": "have.safetensors", "text": "a photo of a cat"}},
            "3": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "wan.gguf"}}
        }"#;
        let r = analyze_workflow(&cfg, api).unwrap();
        assert_eq!(r.len(), 3);
        let ckpt = r.iter().find(|m| m.name == "missing_ckpt.safetensors").unwrap();
        assert!(ckpt.found_at.is_empty());
        assert_eq!(ckpt.dir, "models/checkpoints");
        let lora = r.iter().find(|m| m.name == "have.safetensors").unwrap();
        assert_eq!(lora.found_at, "models/loras/sub/have.safetensors"); // 子目录里也能按文件名找到
        let unet = r.iter().find(|m| m.name == "wan.gguf").unwrap();
        assert_eq!(unet.dir, "models/unet");
        // 界面格式：nodes[].type + widgets_values
        let uiw = r#"{"nodes": [
            {"type": "VAELoader", "widgets_values": ["wan_vae.safetensors"]},
            {"type": "KSampler", "widgets_values": [20, "euler", 7.5]}
        ]}"#;
        let r = analyze_workflow(&cfg, uiw).unwrap();
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].dir, "models/vae");
        assert!(r[0].found_at.is_empty());
        // 非法 JSON 报错
        assert!(analyze_workflow(&cfg, "not json").is_err());
        // 带 BOM 也能解析；Note 节点里的 URL 被过滤；子图（definitions.subgraphs）内的节点也被发现
        let subgraph = "\u{feff}{\"nodes\":[{\"type\":\"Note\",\"widgets_values\":[\"下载: https://hf-mirror.com/x/resolve/main/note_url.safetensors\"]}],\"definitions\":{\"subgraphs\":[{\"nodes\":[{\"type\":\"LoraLoader\",\"widgets_values\":[\"sub_lora.safetensors\",0.8]}]}]}}";
        let r = analyze_workflow(&cfg, subgraph).unwrap();
        assert_eq!(r.len(), 1, "Note URL 应被过滤、子图 lora 应被发现，实际: {:?}", r.iter().map(|m| &m.name).collect::<Vec<_>>());
        assert_eq!(r[0].name, "sub_lora.safetensors");
        assert_eq!(r[0].dir, "models/loras");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn model_filename_filter() {
        assert!(is_model_filename("wan/v1.safetensors"));
        assert!(is_model_filename("model with space.gguf"));
        assert!(!is_model_filename("https://x.com/y/z.safetensors"), "URL 必须被排除");
        assert!(!is_model_filename("第一行\n第二行 z.safetensors"), "多行文本必须被排除");
        assert!(!is_model_filename(&format!("{}.safetensors", "x".repeat(200))), "超长字符串必须被排除");
        assert!(!is_model_filename("readme.txt"));
    }

    #[test]
    fn instance_lock_excludes_second_handle() {
        let p = std::env::temp_dir().join("comfy_dl_lock_test.lock");
        let f1 = fs::OpenOptions::new().create(true).write(true).truncate(false).open(&p).unwrap();
        assert!(f1.try_lock().is_ok());
        let f2 = fs::OpenOptions::new().create(true).write(true).truncate(false).open(&p).unwrap();
        assert!(f2.try_lock().is_err(), "第二个句柄应拿不到锁");
        drop(f2);
        drop(f1);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn search_term_from_filename() {
        assert_eq!(search_term("wan/Wan2.2_I2V-lora_v1.safetensors"), "Wan2.2 I2V lora v1");
        assert_eq!(search_term("flux1-dev-fp8.safetensors"), "flux1 dev fp8");
    }

    /// 真实网络：HF 仓库搜索 + 文件列表（验证线上 JSON 形状与 parse_* 一致）。
    /// 手动运行: cargo test -- --ignored
    #[test]
    #[ignore]
    fn hf_search_and_files_e2e() {
        let tmp = std::env::temp_dir().join("comfy_dl_hf_e2e");
        let mut cfg = test_cfg(&tmp);
        cfg.hf_mirror = false; // 用官方 API（agent() 走系统代理）

        let repos = hf_search(&cfg, "flux1-dev gguf").expect("HF 搜索应成功");
        assert!(!repos.is_empty(), "搜索应有结果");
        println!("HF 搜到 {} 个仓库，首个: {}", repos.len(), repos[0].id);

        // 一个长期稳定、含 gguf 的仓库
        let files = hf_repo_files(&cfg, "city96/FLUX.1-dev-gguf").expect("列文件应成功");
        assert!(!files.is_empty(), "应筛出模型文件");
        assert!(files.iter().any(|f| f.path.to_lowercase().ends_with(".gguf")), "应含 .gguf 文件");
        assert!(files[0].size > 0, "LFS 大小应解析出来");
        println!("city96/FLUX.1-dev-gguf 命中 {} 个模型文件，最大: {} ({} 字节)", files.len(), files[0].path, files[0].size);
    }

    /// 真实网络的端到端测试：全量下载 → 已存在跳过 → 半截 .part 续传 → .part 已完整(416 自愈)。
    /// 手动运行: cargo test -- --ignored
    #[test]
    #[ignore]
    fn download_resume_e2e() {
        let url = "https://hf-mirror.com/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/README.md";
        let tmp = std::env::temp_dir().join("comfy_dl_e2e_test");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        // 显式 download_root 指向临时目录，短路 detect_desktop —— 否则在装了 ComfyUI Desktop
        // 的机器上 resolve_dest_dir 会把文件落到真实安装目录，测试既污染真实库又因残留误判已存在
        let mut cfg = test_cfg(&tmp);
        cfg.download_root = tmp.join("models").display().to_string();
        let meta = DlMeta { download_url: url.into(), source: "hf".into(), expected_sha256: None, desc: String::new() };
        let dest = tmp.join("models/checkpoints/e2e_test.bin");
        let part = tmp.join("models/checkpoints/e2e_test.bin.part");

        // 1) 全量下载
        let t = new_task();
        download_file(&cfg, &t, "e2e_test.bin", "models/checkpoints", &meta).unwrap();
        assert_eq!(t.lock().unwrap().status, "完成");
        let full = fs::read(&dest).unwrap();
        assert!(full.len() > 100, "下载内容过短: {} 字节", full.len());

        // 2) 已存在 → 跳过
        let t = new_task();
        download_file(&cfg, &t, "e2e_test.bin", "models/checkpoints", &meta).unwrap();
        assert_eq!(t.lock().unwrap().status, "已存在");

        // 3) 残留半截 .part → Range 续传，最终内容必须与全量一致
        //    （服务器若忽略 Range 返回 200，新逻辑应从头重写，结果同样一致）
        fs::remove_file(&dest).unwrap();
        fs::write(&part, &full[..full.len() / 2]).unwrap();
        let t = new_task();
        download_file(&cfg, &t, "e2e_test.bin", "models/checkpoints", &meta).unwrap();
        assert_eq!(t.lock().unwrap().status, "完成");
        assert_eq!(fs::read(&dest).unwrap(), full, "断点续传拼接结果与全量下载不一致");

        // 4) .part 已是完整文件 → 越界 Range：带 Content-Range 的 416 应直接收尾改名，
        //    不带则清理报错；返回 200 全量则从头重写。三种合法行为都收敛到内容正确。
        fs::remove_file(&dest).unwrap();
        fs::write(&part, &full).unwrap();
        let t = new_task();
        match download_file(&cfg, &t, "e2e_test.bin", "models/checkpoints", &meta) {
            Ok(()) => assert_eq!(fs::read(&dest).unwrap(), full),
            Err(e) => {
                assert!(e.contains("续传范围越界"), "意外错误: {}", e);
                assert!(!part.exists(), "失败路径应清理 .part");
            }
        }
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn media_resources_from_next_data() {
        // 图片/视频页：__NEXT_DATA__ 里的 resources[]（含 modelId+modelVersionId），应去重
        let html = r#"<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"trpcState":{"json":{"queries":[{"state":{"data":{"resources":[{"imageId":1,"modelVersionId":290640,"modelId":257749,"modelName":"Pony Diffusion V6 XL","modelType":"Checkpoint"},{"imageId":1,"modelVersionId":330475,"modelId":264290,"modelName":"Some LoRA","modelType":"LORA"},{"imageId":1,"modelVersionId":290640,"modelId":257749,"modelName":"Pony Diffusion V6 XL"}]}}}]}}}}}</script>"#;
        let r = extract_media_resources(html);
        assert_eq!(r.len(), 2, "应去重: {:?}", r);
        assert!(r.contains(&(Some("257749".to_string()), Some("290640".to_string()))));
        assert!(r.contains(&(Some("264290".to_string()), Some("330475".to_string()))));
    }

    #[test]
    fn media_resources_post_bare_versions() {
        // 帖子页：只有裸 modelVersionIds[]，提取为 (None, Some(vid))
        let html = r#"<script id="__NEXT_DATA__" type="application/json">{"x":{"items":[{"modelVersionIds":[111,222],"modelVersionIdsManual":[333]}]}}</script>"#;
        let r = extract_media_resources(html);
        assert!(r.contains(&(None, Some("111".to_string()))));
        assert!(r.contains(&(None, Some("222".to_string()))));
        assert!(r.contains(&(None, Some("333".to_string()))));
    }

    #[test]
    fn media_resources_ul_fallback() {
        // 无 __NEXT_DATA__ 时回退抓 Resources used <ul>
        let html = r#"<p>Resources used</p><ul><li><a href="/models/2563220/x?modelVersionId=2880272">A</a></li></ul>"#;
        let r = extract_media_resources(html);
        assert_eq!(r, vec![(Some("2563220".to_string()), Some("2880272".to_string()))]);
        assert!(extract_media_resources("<html>没有资源</html>").is_empty());
    }

    #[test]
    fn extra_paths_parse() {
        let yaml = "comfyui:\n    base_path: D:/Other/ComfyUI/\n    checkpoints: models/checkpoints\n    loras: models/loras\n    # comment\n\na111:\n    base_path: E:/sd-webui\n    checkpoints: models/Stable-diffusion\n    vae: models/VAE\n";
        let r = parse_extra_paths(yaml);
        assert!(r.iter().any(|(k, p)| k == "models/checkpoints" && p == &PathBuf::from("D:/Other/ComfyUI/").join("models/checkpoints")));
        assert!(r.iter().any(|(k, p)| k == "models/loras" && p == &PathBuf::from("D:/Other/ComfyUI/").join("models/loras")));
        assert!(r.iter().any(|(k, p)| k == "models/checkpoints" && p == &PathBuf::from("E:/sd-webui").join("models/Stable-diffusion")));
        assert!(r.iter().any(|(k, p)| k == "models/vae" && p == &PathBuf::from("E:/sd-webui").join("models/VAE")));
    }

    // 用本地 mock HTTP 服务模拟 ComfyUI 的 /system_stats 与 /object_info，验证客户端解析（无需真跑 ComfyUI）
    #[test]
    fn comfy_client_against_mock() {
        use std::io::{Read as _, Write as _};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let stats = r#"{"system":{"comfyui_version":"0.3.40","os":"nt","python_version":"3.12"}}"#;
        // object_info：CheckpointLoaderSimple 的 ckpt_name 下拉含两个模型；混入非模型字符串应被忽略
        let object_info = r#"{"CheckpointLoaderSimple":{"input":{"required":{"ckpt_name":[["flux1-dev-fp8.safetensors","sdxl_base.safetensors"],{}]}}},"KSampler":{"input":{"required":{"sampler_name":[["euler","dpmpp_2m"],{}]}}}}"#;
        let handle = std::thread::spawn(move || {
            // 接受两个请求：/system_stats 与 /object_info
            for _ in 0..2 {
                let (mut sock, _) = listener.accept().unwrap();
                let mut buf = [0u8; 2048];
                let n = sock.read(&mut buf).unwrap();
                let req = String::from_utf8_lossy(&buf[..n]);
                let body = if req.contains("/object_info") { object_info } else { stats };
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                sock.write_all(resp.as_bytes()).unwrap();
            }
        });
        // 不带 scheme 也应能用（comfy_base 补 http://）
        let cfg = Config { comfy_url: format!("127.0.0.1:{}", port), ..Config::default() };
        assert_eq!(comfy_system_stats(&cfg).unwrap(), "0.3.40");
        let models = comfy_known_models(&cfg).unwrap();
        // 带目录维度：(逻辑目录, 小写basename)
        assert!(models.contains(&("models/checkpoints".into(), "flux1-dev-fp8.safetensors".into())), "应提取出 checkpoint");
        assert!(models.contains(&("models/checkpoints".into(), "sdxl_base.safetensors".into())));
        assert!(!models.iter().any(|(_, b)| b == "euler"), "采样器名不应被收入");
        handle.join().unwrap();
    }

    #[test]
    fn comfy_base_normalizes_scheme() {
        let mk = |u: &str| comfy_base(&Config { comfy_url: u.into(), ..Config::default() });
        assert_eq!(mk("127.0.0.1:8188"), "http://127.0.0.1:8188");
        assert_eq!(mk("http://127.0.0.1:8188/"), "http://127.0.0.1:8188");
        assert_eq!(mk("https://my.comfy.host"), "https://my.comfy.host");
    }

    #[test]
    fn mirror_switch() {
        let hf = Config { hf_mirror: true, ..Config::default() };
        let off = Config { hf_mirror: false, ..Config::default() };
        let u = "https://huggingface.co/repo/resolve/main/x.safetensors";
        assert!(apply_mirror(u, &hf).starts_with("https://hf-mirror.com/"));
        let m = "https://hf-mirror.com/repo/resolve/main/x.safetensors";
        assert!(apply_mirror(m, &off).starts_with("https://huggingface.co/"));
    }

    #[test]
    fn config_back_compat_without_comfy_url() {
        // 老 config.json 没有 comfy_url 字段也应能反序列化（serde default），不丢 token
        let old = r#"{"comfy_root":"D:\\ComfyUI","civitai_token":"tok123","hf_mirror":true,"max_concurrent":2}"#;
        let c: Config = serde_json::from_str(old).expect("旧配置应可解析");
        assert_eq!(c.civitai_token, "tok123");
        assert_eq!(c.comfy_url, "http://127.0.0.1:8188");
        assert_eq!(c.proxy_url, None);
    }

    #[test]
    fn extra_paths_block_scalar() {
        // ComfyUI 官方默认写法：块标量 | 多路径 + 行内注释 + 绝对路径
        let yaml = "comfyui:\n    base_path: /data/comfy\n    text_encoders: |\n        models/text_encoders/\n        models/clip/\n    checkpoints: models/checkpoints  # 主目录\n    loras: /abs/loras\n";
        let r = parse_extra_paths(yaml);
        // 块标量两行都被收录
        assert!(r.iter().any(|(k, p)| k == "models/text_encoders" && p == &PathBuf::from("/data/comfy").join("models/text_encoders/")));
        assert!(r.iter().any(|(k, p)| k == "models/text_encoders" && p == &PathBuf::from("/data/comfy").join("models/clip/")));
        // 行内注释被剥掉
        assert!(r.iter().any(|(k, p)| k == "models/checkpoints" && p == &PathBuf::from("/data/comfy").join("models/checkpoints")));
        // 绝对路径不再拼 base
        assert!(r.iter().any(|(k, p)| k == "models/loras" && p == &PathBuf::from("/abs/loras")));
        // 不应产生含字面竖线的伪路径
        assert!(!r.iter().any(|(_, p)| p.to_string_lossy().contains('|')));
    }

    #[test]
    fn html_strip() {
        assert_eq!(html_to_text("<p>Hello <b>world</b></p>", 100), "Hello world");
        assert_eq!(html_to_text("a&amp;b&nbsp;c", 100), "a&b c");
        assert_eq!(html_to_text("<p>0123456789</p>", 5), "01234…");
    }

    /// 作品页爬取+解析的真实网络测试。
    /// 手动运行: cargo test --release -- --ignored
    #[test]
    #[ignore]
    fn media_page_resolve_e2e() {
        let cfg = Config::default(); // 公开作品无需 token
        let list = resolve_media_page(&cfg, "https://civitai.com/images/132805523").unwrap();
        assert!(!list.is_empty(), "应至少解析出一个资源");
        assert_eq!(list[0].source, "civitai");
        assert!(!list[0].filename.is_empty(), "资源应有文件名");
        assert!(list[0].download_url.contains("/api/download/models/"), "下载链接应是 API 直链");
    }

    /// 本地模型 by-hash 反查的真实网络测试。
    /// 手动运行: cargo test --release -- --ignored
    #[test]
    #[ignore]
    fn library_identify_e2e() {
        let cfg = Config::default();
        // 已知存在的 SHA256（Pony LoRA "Not Artists Styles"）→ 应识别为 Found
        let h = "DF7C757437EF3696E76EE5CC18C063681478639132547B6292B0B4D773814BA5";
        match civitai_by_hash(&cfg, h) {
            Ident::Found { model_name, model_type, model_id, .. } => {
                assert!(!model_name.is_empty(), "应有模型名");
                assert_eq!(model_type, "LORA");
                assert!(model_id > 0);
            }
            other => panic!("期望 Found，实得 {:?}", match other {
                Ident::NotFound => "NotFound",
                Ident::Failed(_) => "Failed",
                _ => "其他",
            }),
        }
        // 伪造哈希 → NotFound
        let bogus = "0".repeat(64);
        assert!(matches!(civitai_by_hash(&cfg, &bogus), Ident::NotFound), "伪哈希应 NotFound");
    }

    /// Civitai 搜索分页 + 底模过滤的真实网络测试（无 token 也可搜索）。
    /// 手动运行: cargo test --release -- --ignored
    #[test]
    #[ignore]
    fn civitai_search_pagination_e2e() {
        let cfg = Config::default();
        let (items, next) = civitai_search(&cfg, "flux", "LORA", "Flux.1 D").unwrap();
        assert!(!items.is_empty(), "首页应有结果");
        assert!(items.iter().all(|i| i.kind == "LORA"), "类型过滤应生效");
        let next = next.expect("应返回下一页 URL");
        let (more, _) = civitai_fetch_page(&cfg, &next).unwrap();
        assert!(!more.is_empty(), "下一页应有结果");
        // 加载更多不应与首页重复
        let first_ids: Vec<i64> = items.iter().map(|i| i.id).collect();
        assert!(more.iter().any(|m| !first_ids.contains(&m.id)), "下一页应有新条目");
    }

    /// SHA256 校验的真实网络测试：正确哈希通过并标记 verified、错误哈希删文件报错、续传路径哈希正确。
    /// 手动运行: cargo test --release -- --ignored
    #[test]
    #[ignore]
    fn download_sha256_e2e() {
        let url = "https://hf-mirror.com/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/README.md";
        let tmp = std::env::temp_dir().join("comfy_dl_sha_test");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let cfg = test_cfg(&tmp);
        let dest = tmp.join("models/checkpoints/sha_test.bin");
        let part = tmp.join("models/checkpoints/sha_test.bin.part");

        // 先无校验下载，本地算出正确哈希
        let plain = DlMeta { download_url: url.into(), source: "hf".into(), expected_sha256: None, desc: String::new() };
        let t = new_task();
        download_file(&cfg, &t, "sha_test.bin", "models/checkpoints", &plain).unwrap();
        assert!(!t.lock().unwrap().verified, "无期望哈希不应标记已校验");
        let full = fs::read(&dest).unwrap();
        let good = {
            let mut h = Sha256::new();
            h.update(&full);
            hex_str(&h.finalize())
        };

        // 错误哈希：必须失败且删除文件
        fs::remove_file(&dest).unwrap();
        let bad = DlMeta { download_url: url.into(), source: "hf".into(), expected_sha256: Some("0".repeat(64)), desc: String::new() };
        let t = new_task();
        let e = download_file(&cfg, &t, "sha_test.bin", "models/checkpoints", &bad).unwrap_err();
        assert!(e.contains("SHA256"), "意外错误: {}", e);
        assert!(!part.exists() && !dest.exists(), "校验失败必须删除文件");

        // 正确哈希：全量下载通过并标记 verified
        let goodm = DlMeta { download_url: url.into(), source: "hf".into(), expected_sha256: Some(good.clone()), desc: String::new() };
        let t = new_task();
        download_file(&cfg, &t, "sha_test.bin", "models/checkpoints", &goodm).unwrap();
        assert!(t.lock().unwrap().verified);

        // 续传 + 校验：半截 .part 续传后哈希仍须正确（验证已有字节先喂哈希器的路径）
        fs::remove_file(&dest).unwrap();
        fs::write(&part, &full[..full.len() / 2]).unwrap();
        let t = new_task();
        download_file(&cfg, &t, "sha_test.bin", "models/checkpoints", &goodm).unwrap();
        assert!(t.lock().unwrap().verified, "续传路径也应完成校验");
        assert_eq!(fs::read(&dest).unwrap(), full);

        // paths-info API：对已知 LFS 文件应返回 64 位十六进制哈希
        let s = hf_sha256(&cfg, "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "main", "split_files/vae/wan_2.1_vae.safetensors");
        assert!(s.as_deref().map(|x| x.len() == 64).unwrap_or(false), "paths-info 未返回哈希: {:?}", s);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn normalize_date_orders_iso_strings() {
        assert_eq!(normalize_date("2025-06-01T12:00:00Z"), "2025-06-01T12:00:00.000Z");
        assert_eq!(normalize_date("2025-06-01T12:00:00.5Z"), "2025-06-01T12:00:00.500Z");
        assert_eq!(normalize_date("2025-06-01T12:00:00.12345Z"), "2025-06-01T12:00:00.123Z");
        assert!(normalize_date("2025-06-02T00:00:00.000Z") > normalize_date("2025-06-01T23:59:59.999Z"));
    }
}
