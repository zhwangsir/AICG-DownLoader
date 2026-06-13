use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Clone, Debug, Default)]
pub struct GpuInfo {
    pub name: String,
    pub vram_mb: u64,
    pub driver_version: String,
}

#[derive(Clone, Debug, Default)]
pub struct SystemProfile {
    pub os_name: String,
    pub os_arch: String,
    pub cpu: String,
    pub ram_mb: u64,
    pub gpu: Option<GpuInfo>,
    pub cuda_version: Option<String>,
    pub python_version: Option<String>,
    pub git_version: Option<String>,
    pub comfy_installs: Vec<PathBuf>,
}

fn run(cmd: &str, args: &[&str]) -> Option<String> {
    Command::new(cmd)
        .args(args)
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout).ok().map(|s| s.trim().to_string())
            } else {
                None
            }
        })
}

#[cfg(windows)]
fn run_pwsh(script: &str) -> Option<String> {
    Command::new("powershell")
        .args(["-ExecutionPolicy", "Bypass", "-Command", script])
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout).ok().map(|s| s.trim().to_string())
            } else {
                None
            }
        })
}

#[cfg(not(windows))]
fn run_pwsh(_script: &str) -> Option<String> {
    None
}

fn parse_wmic_kv(s: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for line in s.lines() {
        if let Some((k, v)) = line.split_once('=') {
            map.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    map
}

fn detect_gpu() -> Option<GpuInfo> {
    let out = run(
        "nvidia-smi",
        &["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
    )?;
    let parts: Vec<&str> = out.split(',').map(|s| s.trim()).collect();
    if parts.len() >= 3 {
        let vram_mb = parts[1]
            .split_whitespace()
            .next()
            .and_then(|n| n.parse().ok())
            .unwrap_or(0);
        Some(GpuInfo {
            name: parts[0].to_string(),
            vram_mb,
            driver_version: parts[2].to_string(),
        })
    } else {
        None
    }
}

fn detect_cuda() -> Option<String> {
    let out = run("nvidia-smi", &[])?;
    for line in out.lines() {
        if let Some(idx) = line.find("CUDA Version") {
            let rest = &line[idx..];
            if let Some((_, ver)) = rest.split_once(':') {
                return Some(ver.trim().to_string());
            }
        }
    }
    None
}

fn detect_python() -> Option<String> {
    run("python", &["--version"]).or_else(|| run("python3", &["--version"]))
}

fn detect_git() -> Option<String> {
    run("git", &["--version"])
}

fn is_comfyui_dir(p: &Path) -> bool {
    p.join("main.py").is_file() || p.join("comfy").is_dir()
}

fn detect_comfy_installs() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    let mut push_env = |var: &str| {
        if let Ok(v) = std::env::var(var) {
            candidates.push(PathBuf::from(v));
        }
    };
    push_env("USERPROFILE");
    push_env("LOCALAPPDATA");
    push_env("HOME");
    candidates.push(PathBuf::from("C:\\ComfyUI"));
    candidates.push(PathBuf::from("D:\\ComfyUI"));
    candidates.push(PathBuf::from("E:\\ComfyUI"));

    let mut installs = Vec::new();
    for base in candidates {
        let p = base.join("ComfyUI");
        if is_comfyui_dir(&p) {
            installs.push(p);
        } else if is_comfyui_dir(&base) {
            installs.push(base.clone());
        }
        // ComfyUI Desktop electron path
        let desktop = base.join("Programs").join("@comfyorgcomfyui-electron");
        if is_comfyui_dir(&desktop) {
            installs.push(desktop);
        }
    }
    installs.sort();
    installs.dedup();
    installs
}

pub fn detect() -> SystemProfile {
    let mut profile = SystemProfile::default();

    // OS / CPU / RAM via wmic (Windows) or uname (others)
    #[cfg(windows)]
    {
        if let Some(out) = run("wmic", &["os", "get", "Caption,OSArchitecture,TotalVisibleMemorySize", "/value"]) {
            let kv = parse_wmic_kv(&out);
            profile.os_name = kv.get("Caption").cloned().unwrap_or_default();
            profile.os_arch = kv.get("OSArchitecture").cloned().unwrap_or_default();
            if let Some(ram_kb) = kv.get("TotalVisibleMemorySize").and_then(|s| s.parse::<u64>().ok()) {
                profile.ram_mb = ram_kb / 1024;
            }
        }
        if let Some(out) = run("wmic", &["cpu", "get", "Name", "/value"]) {
            let kv = parse_wmic_kv(&out);
            profile.cpu = kv.get("Name").cloned().unwrap_or_default();
        }
        if profile.os_name.is_empty() {
            if let Some(out) = run_pwsh("(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + (Get-CimInstance Win32_OperatingSystem).OSArchitecture") {
                profile.os_name = out;
            }
        }
    }
    #[cfg(not(windows))]
    {
        profile.os_name = run("uname", &["-s"]).unwrap_or_default();
        profile.os_arch = run("uname", &["-m"]).unwrap_or_default();
        profile.cpu = run("uname", &["-p"]).unwrap_or_default();
        if let Some(out) = run("sysctl", &["-n", "hw.memsize"]) {
            if let Some(bytes) = out.parse::<u64>().ok() {
                profile.ram_mb = bytes / 1024 / 1024;
            }
        }
    }

    profile.gpu = detect_gpu();
    profile.cuda_version = detect_cuda();
    profile.python_version = detect_python();
    profile.git_version = detect_git();
    profile.comfy_installs = detect_comfy_installs();

    profile
}

/// 根据显存给出粗略的模型推荐档位
pub fn vram_tier(vram_mb: u64) -> &'static str {
    match vram_mb {
        0 => "未知",
        1..=4096 => "低显存（建议 GGUF Q4 / LTX 视频 / SD1.5）",
        4097..=8192 => "8GB 档（建议 SDXL / Flux GGUF / Wan 5B GGUF）",
        8193..=12288 => "12GB 档（建议 Flux fp8 / Wan 5B fp8）",
        12289..=16384 => "16GB 档（建议 Wan 14B fp8 / Flux fp8）",
        16385..=24576 => "24GB 档（可跑 Wan 14B fp16 / Flux dev）",
        _ => "高端（可跑原生 fp16 / 大分辨率）",
    }
}

/// 按显存档位给出具体模型推荐：返回 (类别, 推荐模型, 搜索关键词)。
/// 搜索关键词用于在工具内一键跳转 Civitai 搜索。
pub fn vram_recommendations(vram_mb: u64) -> Vec<(&'static str, &'static str, &'static str)> {
    match vram_mb {
        0 => Vec::new(),
        1..=4096 => vec![
            ("文生图 写实", "SD 1.5 / SDXL Turbo", "sdxl turbo"),
            ("文生图 省显存", "GGUF Q4 量化模型", "gguf q4"),
            ("视频", "LTX-Video（轻量）", "ltx video"),
        ],
        4097..=8192 => vec![
            ("文生图 写实", "Flux.1 GGUF Q4/Q5", "flux gguf"),
            ("文生图 动漫", "Illustrious / Pony (SDXL)", "illustrious"),
            ("视频", "Wan 2.2 5B GGUF", "wan 2.2 5b gguf"),
        ],
        8193..=12288 => vec![
            ("文生图 写实", "Flux.1 dev fp8", "flux dev fp8"),
            ("文生图 动漫", "Illustrious / NoobAI (SDXL)", "illustrious"),
            ("视频", "Wan 2.2 5B fp8", "wan 2.2 5b"),
        ],
        12289..=16384 => vec![
            ("视频 图生视频", "Wan 2.2 I2V 14B（fp8 / GGUF Q5）", "wan 2.2 i2v"),
            ("文生图 写实", "Flux.1 dev fp8", "flux dev"),
            ("文生图 动漫", "Illustrious / NoobAI (SDXL)", "illustrious"),
            ("加速", "Lightning / LCM 4-step LoRA", "lightning lora"),
        ],
        16385..=24576 => vec![
            ("视频 图生视频", "Wan 2.2 I2V 14B fp16", "wan 2.2 i2v"),
            ("文生图 写实", "Flux.1 dev（fp16）", "flux dev"),
            ("文生图 动漫", "Illustrious / Pony XL", "illustrious"),
        ],
        _ => vec![
            ("视频", "Wan 2.2 14B fp16（高分辨率）", "wan 2.2"),
            ("文生图", "Flux.1 dev fp16 / 原生大图", "flux dev"),
        ],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_runs_without_panic() {
        let _p = detect();
        // 只要没 panic 就算通过；CI/沙箱环境可能拿不到 wmic/os 名
    }
}
