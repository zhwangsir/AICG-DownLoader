#![cfg_attr(all(windows, not(debug_assertions), not(test)), windows_subsystem = "windows")]
//! ComfyUI 模型下载器 — egui 原生跨平台版
//! 功能: Civitai 搜索 / 链接解析 / 队列下载(断点续传) / 模型库扫描 / 设置
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use eframe::egui;

static ACTIVE: AtomicUsize = AtomicUsize::new(0);
static NEXT_ID: AtomicU64 = AtomicU64::new(1);
// 全局暂停：置位后排队任务不取并发槽、下载中任务在下一个分块边界退出为「已暂停」
static PAUSED: AtomicBool = AtomicBool::new(false);

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
    v.widgets.hovered.weak_bg_fill = egui::Color32::from_rgb(48, 48, 64);
    v.widgets.active.weak_bg_fill = egui::Color32::from_rgb(56, 56, 76);
    style.visuals = v;
    ctx.set_style(style);
}

// 小圆角彩色标签（类型/状态徽章）
fn chip(ui: &mut egui::Ui, text: &str, bg: egui::Color32, fg: egui::Color32) {
    egui::Frame::none()
        .fill(bg)
        .rounding(egui::Rounding::same(6.0))
        .inner_margin(egui::Margin::symmetric(7.0, 2.0))
        .show(ui, |ui| {
            ui.label(egui::RichText::new(text).size(11.5).color(fg));
        });
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
fn default_comfy_url() -> String {
    "http://127.0.0.1:8188".into()
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
        }
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
#[derive(Clone)]
struct VerInfo {
    id: i64,
    name: String,
    base: String,
    filename: String,
    size_kb: f64,
    sha256: String,
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
    let rec = ModelRecord {
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
    };
    upsert_model_record(&mut list, rec);
    save_models_index(&models_path(), &list);
}

// 从链接解析结果写入/更新 models.json
fn record_resolved_to_index(r: &Resolved) {
    let mut list = load_models_index(&models_path());
    let rec = ModelRecord {
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
    };
    upsert_model_record(&mut list, rec);
    save_models_index(&models_path(), &list);
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

fn civitai_search(cfg: &Config, query: &str, types: &str, base: &str) -> Result<(Vec<SearchItem>, Option<String>), String> {
    let mut url = "https://civitai.com/api/v1/models?limit=24&nsfw=true&sort=Most%20Downloaded".to_string();
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
        let api = format!("https://civitai.com/api/v1/models/{}", mid);
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
        let versions = vers_arr
            .iter()
            .map(|ver| {
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
                }
            })
            .collect();
        Ok(Resolved {
            source: "civitai".into(),
            model_name,
            kind: kind.clone(),
            base: chosen.get("baseModel").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            filename: file.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            size_kb: file.get("sizeKB").and_then(|x| x.as_f64()).unwrap_or(0.0),
            subdir: type_dir(&kind).to_string(),
            image,
            download_url: format!("https://civitai.com/api/download/models/{}", version_id),
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
    let api = format!("https://civitai.com/api/v1/model-versions/{}", vid);
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
    let task = Arc::new(Mutex::new(Task {
        id,
        filename: filename.clone(),
        subdir: subdir.clone(),
        status: "排队中".into(),
        downloaded: 0,
        total: (size_kb * 1024.0) as u64,
        speed: 0.0,
        error: String::new(),
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

fn download_file(cfg: &Config, task: &TaskRef, filename: &str, subdir: &str, meta: &DlMeta) -> Result<(), String> {
    let root = expand_root(&cfg.comfy_root);
    let sub: PathBuf = subdir.split('/').collect();
    let dest_dir = root.join(sub);
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

// 识别单个文件：算哈希（带缓存）→ Civitai by-hash 反查
fn identify_one(cfg: &Config, path: &Path) -> Ident {
    match cached_file_hash(path) {
        Ok(h) => civitai_by_hash(cfg, &h),
        Err(e) => Ident::Failed(e),
    }
}

// Civitai by-hash 反查：本地模型 → 是什么模型/哪个版本
fn civitai_by_hash(cfg: &Config, sha256: &str) -> Ident {
    let url = format!("https://civitai.com/api/v1/model-versions/by-hash/{}", sha256);
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
    let url = format!("https://civitai.com/api/v1/models/{}", model_id);
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
            return Ok((vid, ver_name(ver).to_string(), format!("https://civitai.com/api/download/models/{}", vid)));
        }
        // 当前变体已是最新，把当前版本作为“最新”返回，让 UI 显示“已是最新”
        let vid = ver_id(cur);
        return Ok((vid, ver_name(cur).to_string(), format!("https://civitai.com/api/download/models/{}", vid)));
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
    Ok((vid, ver_name(ver).to_string(), format!("https://civitai.com/api/download/models/{}", vid)))
}

// ============ 工作流缺失模型分析 ============
struct WfModel {
    name: String,
    dir: String,
    found_at: String,        // 本地路径，空 = 本地缺失
    in_comfy: bool,          // 运行中的 ComfyUI 实例能看到（即便本地扫描没扫到）
    dl: Option<DlMeta>,      // 本地缺失时，若知道精确下载源，可一键补齐
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
            WfModel { name, dir, found_at, in_comfy: false, dl }
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
    Resolve(Box<Result<Resolved, String>>),
    ResolveSet(Result<Vec<Resolved>, String>),
    Library(Vec<LibDir>),
    Identify { path: PathBuf, ident: Ident },
    Workflow(Result<Vec<WfModel>, String>),
    ComfyStatus(Result<String, String>),
    // model_id → 最新版本查询结果
    UpdateCheck { model_id: i64, result: Result<UpdateInfo, String> },
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
    query: String,
    type_filter: String,
    base_filter: String,
    results: Vec<SearchItem>,
    next_page: Option<String>,
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
    // 托盘图标必须存活，否则会立即从任务栏消失
    #[allow(dead_code)]
    tray: Option<tray_icon::TrayIcon>,
    tray_initialized: bool,
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        store_main_hwnd(cc);
        let cjk_font_ok = install_cjk_font(&cc.egui_ctx);
        setup_style(&cc.egui_ctx);
        egui_extras::install_image_loaders(&cc.egui_ctx);
        let (tx, rx) = std::sync::mpsc::channel();
        let cfg = load_config();
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
        // 调试/截图辅助：COMFY_START_TAB=link|preset|workflow|library|settings 指定启动页
        let tab = match std::env::var("COMFY_START_TAB").as_deref() {
            Ok("link") => Tab::Link,
            Ok("preset") => Tab::Preset,
            Ok("workflow") => Tab::Workflow,
            Ok("library") => Tab::Library,
            Ok("settings") => Tab::Settings,
            _ => Tab::Search,
        };
        App {
            tab,
            tx,
            rx,
            busy: false,
            query: String::new(),
            type_filter: String::new(),
            base_filter: String::new(),
            results: Vec::new(),
            next_page: None,
            link: String::new(),
            resolve_err: String::new(),
            pending: None,
            pending_set: Vec::new(),
            edit_name: String::new(),
            edit_subdir: String::new(),
            sel_version: 0,
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
            tray: None,
            tray_initialized: false,
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

    fn do_search(&mut self) {
        let cfg = self.cfg.clone();
        let q = self.query.clone();
        let tf = self.type_filter.clone();
        let bf = self.base_filter.clone();
        let tx = self.tx.clone();
        self.busy = true;
        self.resolve_err.clear();
        self.next_page = None;
        std::thread::spawn(move || {
            let _ = tx.send(Msg::Search(civitai_search(&cfg, &q, &tf, &bf), false));
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
        self.pending = Some(r);
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
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // 第一帧初始化托盘图标，确保与 winit 事件循环同线程且事件循环已在运行
        if !self.tray_initialized {
            self.tray = setup_tray();
            self.tray_initialized = true;
        }

        // 关闭窗口时若启用托盘最小化，则隐藏窗口而非退出
        // （托盘菜单的“退出”直接给主线程发 WM_QUIT，不经过这里的 close_requested）
        if ctx.input(|i| i.viewport().close_requested()) && self.cfg.tray_minimize {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }

        // 处理后台消息
        while let Ok(m) = self.rx.try_recv() {
            // busy 只代表搜索/解析/工作流分析这类带 spinner 的操作；Identify/ComfyStatus/Library
            // 各有自己的进度指示，不能让它们的回包误清 busy（否则并发时 spinner 提前消失）
            match m {
                Msg::Search(..) | Msg::Resolve(_) | Msg::ResolveSet(_) | Msg::Workflow(_) => self.busy = false,
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
                Msg::Workflow(Ok(l)) => self.wf_models = l,
                Msg::Workflow(Err(e)) => self.wf_err = e,
                Msg::ComfyStatus(Ok(ver)) => self.comfy_status = format!("已连接 · ComfyUI {}", ver),
                Msg::ComfyStatus(Err(e)) => self.comfy_status = format!("未连接: {}", e),
                Msg::UpdateCheck { model_id, result } => {
                    self.lib_checking_updates.remove(&model_id);
                    self.lib_updates.insert(model_id, result);
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
                ui.heading(egui::RichText::new("ComfyUI 模型下载器").strong());
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
                ui.selectable_value(&mut self.tab, Tab::Search, "🔍 搜索");
                ui.selectable_value(&mut self.tab, Tab::Link, "🔗 链接");
                ui.selectable_value(&mut self.tab, Tab::Preset, "📦 套餐");
                ui.selectable_value(&mut self.tab, Tab::Workflow, "📋 工作流");
                ui.selectable_value(&mut self.tab, Tab::Library, "📁 模型库");
                ui.selectable_value(&mut self.tab, Tab::Downloads, "⬇ 下载");
                ui.selectable_value(&mut self.tab, Tab::Settings, "⚙ 设置");
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
            let was_paused = PAUSED.load(Ordering::Relaxed);
            PAUSED.store(!was_paused, Ordering::Relaxed);
            if was_paused {
                // 恢复：暂停中任务的线程已退出，按元数据重新入队（靠 .part 续传）
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

        let central_frame = egui::Frame::none().fill(C_BG).inner_margin(egui::Margin::same(16.0));
        egui::CentralPanel::default().frame(central_frame).show(ctx, |ui| match self.tab {
            Tab::Search => self.ui_search(ui),
            Tab::Link => self.ui_link(ui),
            Tab::Preset => self.ui_preset(ui),
            Tab::Workflow => self.ui_workflow(ui),
            Tab::Library => self.ui_library(ui),
            Tab::Downloads => self.ui_downloads(ui),
            Tab::Settings => self.ui_settings(ui),
        });

        // 解析弹窗
        self.ui_pending(ctx);
        self.ui_pending_set(ctx);

        ctx.request_repaint_after(Duration::from_millis(500));
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.persist_if_changed();
    }
}

impl App {
    fn ui_search(&mut self, ui: &mut egui::Ui) {
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
                                egui::Frame::none()
                                    .fill(C_CARD)
                                    .rounding(egui::Rounding::same(10.0))
                                    .inner_margin(egui::Margin::same(10.0))
                                    .show(ui, |ui| {
                                        ui.vertical(|ui| {
                                            if !it.image.is_empty() {
                                                ui.add(
                                                    egui::Image::from_uri(it.image.clone())
                                                        .max_height(200.0)
                                                        .max_width(ui.available_width())
                                                        .rounding(egui::Rounding::same(8.0)),
                                                );
                                            }
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
                                            if ui.add_sized([ui.available_width(), 28.0], egui::Button::new("下载")).clicked() {
                                                self.do_resolve(format!("https://civitai.com/models/{}?modelVersionId={}", it.id, it.version_id));
                                            }
                                        });
                                    });
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
        let ps = presets(&self.cfg);
        for (_k, title, files) in ps {
            egui::Frame::none()
                .fill(C_CARD)
                .rounding(egui::Rounding::same(10.0))
                .inner_margin(egui::Margin::same(12.0))
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
        ui.horizontal(|ui| {
            ui.strong(format!("共引用 {} 个模型", self.wf_models.len()));
            if truly_missing > 0 {
                chip(ui, &format!("缺失 {} 个", truly_missing), egui::Color32::from_rgb(66, 34, 38), C_RED);
            } else {
                chip(ui, "全部齐备", egui::Color32::from_rgb(26, 56, 40), C_GREEN);
            }
            if !fillable.is_empty() {
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.add(accent_btn(&format!("一键补齐 {} 个", fillable.len()))).clicked() {
                        for (name, sub, meta) in &fillable {
                            let mut meta = meta.clone();
                            meta.download_url = apply_mirror(&meta.download_url, &self.cfg);
                            start_task(
                                self.cfg.clone(),
                                self.downloads.clone(),
                                name.clone(),
                                sub.clone(),
                                meta,
                                0.0,
                            );
                        }
                    }
                });
            }
        });
        let mut jump: Option<String> = None;
        let mut fill_one: Option<(String, String, DlMeta)> = None;
        egui::ScrollArea::vertical().show(ui, |ui| {
            for m in &self.wf_models {
                egui::Frame::none()
                    .fill(C_CARD)
                    .rounding(egui::Rounding::same(8.0))
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
                                    } else if ui.small_button("去搜索").clicked() {
                                        jump = Some(search_term(&m.name));
                                    }
                                });
                            }
                        });
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
        let show_thumbs = total_count <= 200;
        if !show_thumbs {
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
                    egui::Frame::none()
                        .fill(C_CARD)
                        .rounding(egui::Rounding::same(8.0))
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
                                                let url = format!("https://civitai.com/api/download/models/{}", info.latest_vid);
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
        egui::Frame::none()
            .fill(C_CARD)
            .rounding(egui::Rounding::same(10.0))
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
            ui.label("HuggingFace 镜像");
            ui.checkbox(&mut self.cfg.hf_mirror, "使用 hf-mirror 国内镜像");
            ui.end_row();
            ui.label("同时下载数");
            ui.add(egui::Slider::new(&mut self.cfg.max_concurrent, 1..=4));
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
            egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("总任务");
                    ui.strong(total.to_string());
                });
            });
            egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("进行中");
                    ui.strong(active.to_string());
                });
            });
            egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("已完成");
                    ui.strong(done.to_string());
                });
            });
            egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("失败");
                    ui.strong(failed.to_string());
                });
            });
            if paused > 0 {
                egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
                    ui.vertical(|ui| {
                        ui.weak("已暂停");
                        ui.strong(paused.to_string());
                    });
                });
            }
            egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
                ui.vertical(|ui| {
                    ui.weak("总速度");
                    ui.strong(format!("{}/s", fmt_size(total_speed as u64)));
                });
            });
            if total_speed > 0.0 {
                if let Some(eta) = eta_secs(0, total_remaining, total_speed) {
                    egui::Frame::none().fill(C_CARD).rounding(egui::Rounding::same(8.0)).inner_margin(10.0).show(ui, |ui| {
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
                    egui::Frame::none()
                        .fill(C_CARD)
                        .rounding(egui::Rounding::same(10.0))
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
                ui.add_space(4.0);
                ui.horizontal(|ui| {
                    if ui.add_enabled(n_sel > 0, accent_btn(&format!("下载选中 ({})", n_sel))).clicked() {
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
        if let Some(r) = self.pending.clone() {
            egui::Window::new(if r.model_name.is_empty() { r.filename.clone() } else { r.model_name.clone() })
                .collapsible(false)
                .resizable(false)
                .open(&mut open)
                .show(ctx, |ui| {
                    if !r.image.is_empty() {
                        ui.add(egui::Image::from_uri(r.image.clone()).max_height(220.0));
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
                    ui.add_space(4.0);
                    ui.horizontal(|ui| {
                        if ui.add(accent_btn("开始下载")).clicked() {
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
                    format!("https://civitai.com/api/download/models/{}", self.sel_version)
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

#[cfg(windows)]
mod tray_win {
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::Mutex;
    pub type Hwnd = isize;
    pub const SW_HIDE: i32 = 0;
    pub const SW_SHOW: i32 = 5;
    pub const WM_QUIT: u32 = 0x0012;
    extern "system" {
        pub fn ShowWindow(hWnd: Hwnd, nCmdShow: i32) -> i32;
        pub fn IsWindowVisible(hWnd: Hwnd) -> i32;
        pub fn SetForegroundWindow(hWnd: Hwnd) -> i32;
        pub fn GetCurrentThreadId() -> u32;
        pub fn PostThreadMessageW(idThread: u32, Msg: u32, wParam: usize, lParam: isize) -> i32;
    }
    pub static TRAY_HWND: Mutex<Option<Hwnd>> = Mutex::new(None);
    pub static MAIN_THREAD_ID: AtomicU32 = AtomicU32::new(0);

    pub fn is_visible(hwnd: Hwnd) -> bool {
        unsafe { IsWindowVisible(hwnd) != 0 }
    }

    pub fn store_thread_id() {
        MAIN_THREAD_ID.store(unsafe { GetCurrentThreadId() }, Ordering::SeqCst);
    }

    pub fn request_exit() {
        let tid = MAIN_THREAD_ID.load(Ordering::SeqCst);
        if tid != 0 {
            unsafe { PostThreadMessageW(tid, WM_QUIT, 0, 0) };
        }
    }
}

#[cfg(not(windows))]
mod tray_win {
    use std::sync::Mutex;
    pub type Hwnd = isize;
    pub const SW_HIDE: i32 = 0;
    pub const SW_SHOW: i32 = 5;
    pub static TRAY_HWND: Mutex<Option<Hwnd>> = Mutex::new(None);
    pub fn is_visible(_hwnd: Hwnd) -> bool { true }
    pub unsafe fn ShowWindow(_hwnd: Hwnd, _n: i32) -> i32 { 0 }
    pub unsafe fn SetForegroundWindow(_hwnd: Hwnd) -> i32 { 0 }
    pub fn store_thread_id() {}
    pub fn request_exit() {}
}

fn store_main_hwnd(cc: &eframe::CreationContext<'_>) {
    #[cfg(windows)]
    {
        use raw_window_handle::{HasWindowHandle, RawWindowHandle};
        if let Ok(handle) = cc.window_handle() {
            if let RawWindowHandle::Win32(h) = handle.as_raw() {
                if let Ok(mut g) = tray_win::TRAY_HWND.lock() {
                    *g = Some(h.hwnd.get());
                }
            }
        }
    }
    tray_win::store_thread_id();
}

fn handle_tray_cmd_now(cmd: TrayCmd) {
    let hwnd = match tray_win::TRAY_HWND.lock() {
        Ok(g) => *g,
        Err(_) => return,
    };
    let Some(hwnd) = hwnd else { return };
    match cmd {
        TrayCmd::Show => unsafe {
            tray_win::ShowWindow(hwnd, tray_win::SW_SHOW);
            tray_win::SetForegroundWindow(hwnd);
        },
        TrayCmd::Hide => unsafe {
            tray_win::ShowWindow(hwnd, tray_win::SW_HIDE);
        },
        TrayCmd::Toggle => unsafe {
            if tray_win::is_visible(hwnd) {
                tray_win::ShowWindow(hwnd, tray_win::SW_HIDE);
            } else {
                tray_win::ShowWindow(hwnd, tray_win::SW_SHOW);
                tray_win::SetForegroundWindow(hwnd);
            }
        },
        TrayCmd::PauseResume => {
            let was = PAUSED.load(Ordering::Relaxed);
            PAUSED.store(!was, Ordering::Relaxed);
        }
        TrayCmd::Exit => {
            tray_win::request_exit();
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
        .with_tooltip("ComfyUI 模型下载器")
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
        viewport: egui::ViewportBuilder::default().with_inner_size([1120.0, 800.0]),
        ..Default::default()
    };
    eframe::run_native(
        "ComfyUI 模型下载器",
        options,
        Box::new(|cc| Ok(Box::new(App::new(cc)))),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

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
        Config {
            comfy_root: root.to_string_lossy().into_owned(),
            civitai_token: String::new(),
            hf_mirror: true,
            max_concurrent: 1,
            comfy_url: default_comfy_url(),
            proxy_url: None,
            tray_minimize: true,
            notify_on_complete: true,
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

    /// 真实网络的端到端测试：全量下载 → 已存在跳过 → 半截 .part 续传 → .part 已完整(416 自愈)。
    /// 手动运行: cargo test -- --ignored
    #[test]
    #[ignore]
    fn download_resume_e2e() {
        let url = "https://hf-mirror.com/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/README.md";
        let tmp = std::env::temp_dir().join("comfy_dl_e2e_test");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let cfg = test_cfg(&tmp);
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
