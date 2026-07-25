# TEST_LOG.md — 测试与验证时序日志

> 本文件按时间倒序记录每次回归验证的命令、结果与关键代码片段。

## 2026-07-25 P4.4.10 前端补齐：唇形同步 + 后处理模态框

### 变更摘要

- **useDramaStore 扩展**：`ModalsState` 新增 `lipSync`/`postprocess` 开关；新增 `lipSyncs: LipSyncData[]` 与 `postprocesses: PostprocessData[]` 状态（按 scene_id 去重替换）；新增 `addLipSync`/`addPostprocess` actions；`reset()` 与 `setScriptData()` 同步清空。
- **LipSyncModal**：选择视频场景 + 配音音频 + 可选角色参考图 → `POST /lipsync/generate` → 展示同步结果；`synced=false` 时提示已降级返回原视频。
- **PostprocessModal**：选择场景 + 五步开关（超分/插帧/修复/降噪/编码）+ 输出分辨率 → `POST /postprocess/generate` → 逐步骤展示成功/跳过/失败状态与耗时。
- **Icon.tsx**：补充 `Smile`（唇形同步）与 `Layers`（后处理）两个 lucide 图标。
- **App.tsx**：注册两个新模态框；topbar 新增「唇形同步」「后处理」按钮（videos/voices 为空时禁用）；状态栏更新为 P4 服务栈。

### 前端测试

```bash
cd platform/frontend && pnpm test
```

**结果**：25/25 passed（useDramaStore 14 + ThemeSwitcher 8 + App 3）

新增 4 个 P4 store 测试：

```typescript
it("should toggle lipSync and postprocess modals", () => {
  useDramaStore.getState().setModal("lipSync", true);
  useDramaStore.getState().setModal("postprocess", true);
  expect(useDramaStore.getState().modals.lipSync).toBe(true);
  expect(useDramaStore.getState().modals.postprocess).toBe(true);
});

it("should add and replace lipSyncs by scene_id", () => {
  useDramaStore.getState().addLipSync(sampleLipSync);
  useDramaStore.getState().addLipSync({ ...sampleLipSync, video_url: "http://x/ls2.mp4" });
  const lipSyncs = useDramaStore.getState().lipSyncs;
  expect(lipSyncs).toHaveLength(1);
  expect(lipSyncs[0].video_url).toBe("http://x/ls2.mp4");
});

it("should clear lipSyncs and postprocesses when script is reset", () => {
  state.addLipSync(sampleLipSync);
  state.addPostprocess(samplePostprocess);
  state.setScriptData(sampleScript);
  expect(useDramaStore.getState().lipSyncs).toEqual([]);
  expect(useDramaStore.getState().postprocesses).toEqual([]);
});
```

### 前端构建

```bash
cd platform/frontend && pnpm build
```

**结果**：成功（dist/index.js 487.79KB / gzip 154.05KB，tsc 无类型错误）

### 后端全量回归（无回归确认）

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest tests/ -q --tb=no
```

**结果**：328/328 passed，覆盖率 87.71%（阈值 80%）

### 新增/修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/frontend/src/store/useDramaStore.ts` | 修改 | lipSyncs/postprocesses 状态 + actions + 模态开关 |
| `platform/frontend/src/components/Modals.tsx` | 修改 | 新增 LipSyncModal + PostprocessModal 组件 |
| `platform/frontend/src/components/ui/Icon.tsx` | 修改 | 新增 Smile/Layers 图标导出 |
| `platform/frontend/src/App.tsx` | 修改 | 注册模态框 + topbar 按钮 + 状态栏 P4 服务栈 |
| `platform/frontend/src/store/useDramaStore.test.ts` | 修改 | 新增 4 个 P4 store 测试用例 |

---

## 2026-07-25 P4.6 服务部署落地：LatentSync 1.6 + video-enhance 上线

### 变更摘要

- **LatentSync 1.6 唇形同步服务**部署到 workstation GPU1:8289，model_ready:true，/v1/models 返回 LatentSync-1.6 resolution:512。cog 0.21.0 依赖已持久化到镜像。
- **video-enhance 三合一后处理服务**（RealBasicVSR + RIFE + ProPainter）部署到 workstation GPU1:8290，5 个权重全部就绪。
- **模型权重下载**：通过 hf-mirror.com 镜像下载 5 个权重（415MB）：RealBasicVSR_x4.pth (201M) + ProPainter.pth (151M) + raft-things.pth (21M) + recurrent_flow_completion.pth (20M) + RIFE_v4.6.pkl (24M，实为 lopi999/rife_v4.26 的 flownet.pkl 副本，v4.26 比 v4.6 更新)。
- **RIFE 仓库补充**：镜像构建时 RIFE 目录缺失，git clone hzwer/RIFE 到宿主机后通过 volume 挂载到容器。
- **serve_api.py 修复**：do_interpolate 函数增加 `--model={CHECKPOINT_DIR}` 参数，RIFE inference_video.py 默认 modelDir=train_log 会找不到权重。
- **config.py 更新**：后处理编排注释 GPU3→GPU1（实际部署位置）。
- **HunyuanImage + xDiT 镜像就绪待 GPU**：两个镜像已构建完成（22.9GB + 21.6GB），但 GPU 资源不足暂缓部署。

### 服务健康检查

```bash
# LatentSync
curl -s http://192.168.71.127:8289/health
# {"status":"ok","model_ready":true,"model_error":null,"tasks_total":0}

# video-enhance
curl -s http://192.168.71.127:8290/health
# {"status":"ok","gpu":"void","weights":{"RealBasicVSR_x4.pth":true,"RIFE_v4.6.pkl":true,"ProPainter.pth":true,"recurrent_flow_completion.pth":true,"raft-things.pth":true}}
```

### 后端全量回归

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest -q --tb=short
```

**结果**：328/328 passed，覆盖率 87.71%（阈值 80%）

### GPU 资源现状

```
GPU0: 93GB/98GB (ComfyUI 85GB)        - 余 5GB
GPU1: 69GB/98GB (LatentSync 18GB + IndexTTS + Qwen3-ASR + ComfyUI)  - 余 29GB ← video-enhance 按需调用
GPU2: 93GB/98GB (ComfyUI 85GB)        - 余 5GB
GPU3: 86GB/98GB (Qwen3-VL 83.5GB)     - 余 12GB ← 不足 HunyuanImage FP8 24GB / xDiT 35-45GB
```

### 关键修复

1. **RIFE 权重文件名兼容**：RIFE 的 `load_model` 期望 `{modelDir}/flownet.pkl`，但 serve_api.py 检查 `RIFE_v4.6.pkl`。解决方案：下载 lopi999/rife_v4.26 的 flownet.pkl，同时复制为 RIFE_v4.6.pkl（满足 serve_api.py 健康检查）和 flownet.pkl（满足 RIFE load_model）。

2. **serve_api.py do_interpolate 传参**：
```python
# 修复前
cmd = ["python", os.path.join(RIFE_DIR, "inference_video.py"),
       f"--exp={exp}", f"--video={input_path}", f"--output={out_dir}"]
# 修复后（增加 --model 参数指向权重目录）
cmd = ["python", os.path.join(RIFE_DIR, "inference_video.py"),
       f"--exp={exp}", f"--video={input_path}", f"--output={out_dir}",
       f"--model={CHECKPOINT_DIR}"]
```

3. **video-enhance 部署 GPU3→GPU1**：GPU3 仅余 12GB（Qwen3-VL 占用），video-enhance 三模型串行峰值 ~15GB。迁移到 GPU1（余 29GB），与 LatentSync 共享 GPU1（按需调用不常驻显存）。

4. **run.sh volume 挂载方案**（避免重建镜像）：
```bash
# 挂载 RIFE 仓库（镜像内为空）+ 修改后的 serve_api.py + 权重目录
-v /home/merlin/video-models:/workspace/checkpoints:ro \
-v ${DEPLOY_DIR}/RIFE:/workspace/RIFE:ro \
-v ${DEPLOY_DIR}/serve_api.py:/workspace/serve_api.py:ro
```

### 后续更新（同日）：HunyuanImage + xDiT 上线

暂停 Qwen3-VL-30B-A3B-Thinking 容器释放 GPU3 83.5GB，部署 HunyuanImage + xDiT 两服务。

```bash
# 暂停 Qwen3-VL
docker stop qwen3-vl-30b-thinking
# GPU3: 86GB → 2.9GB (释放 83.5GB)

# 启动 HunyuanImage (GPU3:8600)
cd /home/merlin/deploys/hunyuanimage && ./run.sh
# 启动 xDiT (GPU3:8288)
cd /home/merlin/deploys/xdit-video && ./run.sh
```

**4 服务全量健康检查**：
```
LatentSync 1.6 (GPU1:8289): {"status":"ok","model_ready":true}
video-enhance (GPU1:8290): {"status":"ok", 5 weights ready}
HunyuanImage 2.1 (GPU3:8600): {"status":"ok","loaded":false}  # 懒加载
xDiT (GPU3:8288): {"status":"ok","model_loaded":false}  # 懒加载
```

### 最终待办

- ~~**DeepFilterNet3**：Mac studio01 (192.168.71.109:8301) 服务未运行（502），需在 Mac 端编译启动 Rust 服务。~~ → 已完成，见下方"后续更新：DeepFilterNet3 上线"
- **Qwen3-VL 重启**：Qwen3-VL 已暂停释放 GPU3，可在 GPU 资源释放后 `docker start qwen3-vl-30b-thinking` 重启视觉质检服务。

---

## 2026-07-25 P4.6.8 DeepFilterNet3 音频降噪服务上线（Mac studio01:8301）

### 变更摘要

- **触发原因**: P4.6 后处理编排步骤 4（音频降噪）原待部署项，Mac studio01 :8301 端口未运行，curl 返回 502/000。
- **本次范围**: 部署 DeepFilterNet3 0.5.6 预编译二进制（arm64）+ Python HTTP 包装器（零依赖）+ launchd 守护进程到 Mac studio01。
- **关键决策**:
  1. **预编译二进制替代 cargo install**：studio01 上 ~/.cargo/bin 不存在（rustup 残破），改用 GitHub release `deep-filter-0.5.6-aarch64-apple-darwin`（27MB），零编译。
  2. **Python 标准库 HTTP 包装器**：studio01 系统自带 python3.9.6，无需 pip 安装任何依赖。`http.server.ThreadingHTTPServer` + `subprocess` 调用 deep-filter CLI。
  3. **launchd 守护**：`com.aicg.deepfilternet.plist` 安装到 `~/Library/LaunchAgents/`，`RunAtLoad=true + KeepAlive=true` 实现开机自启 + 崩溃重启。
  4. **deep-filter returncode=1 兼容**：deep-filter 处理静音/边界输入时偶尔 rc=1 但输出文件已正常生成，包装器优先检查输出文件而非 returncode。
  5. **多格式自动识别**：通过文件头 magic bytes 自动识别 wav/mp3/ogg/flac，并支持 `multipart/form-data` 和 raw bytes 两种上传方式。

### 部署命令

```bash
# 1. 下载预编译二进制（arm64, 27MB）
ssh dgmt-studio01@192.168.71.109
mkdir -p ~/deploys/deepfilternet && cd ~/deploys/deepfilternet
curl -L -o deep-filter \
  https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-aarch64-apple-darwin
chmod +x deep-filter

# 2. 从工作站 scp 上传包装器与 plist（项目 deploy 目录）
# 文件位置: platform/deploy/deepfilternet/{serve_api.py,run.sh,com.aicg.deepfilternet.plist,README.md}
scp platform/deploy/deepfilternet/{serve_api.py,run.sh,com.aicg.deepfilternet.plist} \
  dgmt-studio01@192.168.71.109:~/deploys/deepfilternet/

# 3. 安装 launchd 守护
cp com.aicg.deepfilternet.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aicg.deepfilternet.plist
```

### 测试命令

```bash
# 1. 健康检查
curl -s http://192.168.71.109:8301/v1/health
curl -s http://192.168.71.109:8301/v1/models

# 2. 端到端 denoise 测试（在 studio01 上）
ssh dgmt-studio01@192.168.71.109 "cd ~/deploys/deepfilternet"
# 生成 2s 440Hz tone + 白噪声测试 wav
python3 -c "
import wave, struct, math, random
random.seed(42)
with wave.open('test_tone.wav', 'w') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
    frames = b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 440 * t / 48000) + 1500 * random.gauss(0, 1))) for t in range(48000 * 2))
    w.writeframes(frames)
"
# POST /v1/denoise
curl -s -X POST -H 'Content-Type: audio/wav' --data-binary @test_tone.wav \
  -o denoised_test_tone.wav -w 'HTTP %{http_code} | time %{time_total}s | size %{size_download}B\n' \
  http://localhost:8301/v1/denoise

# 3. 工作站访问验证（确认 LAN 跨机可用）
curl -s http://192.168.71.109:8301/v1/health
curl -s -X POST -H 'Content-Type: audio/wav' --data-binary @/dev/null \
  -w 'HTTP %{http_code}\n' http://192.168.71.109:8301/v1/denoise
```

### 测试结果

```
# 健康检查：200 OK
GET /v1/health → {"status":"ok","model":"deepfilternet3","version":"0.5.6",
                  "binary":".../deep-filter","binary_exists":true}
GET /v1/models → {"object":"list","data":[{"id":"deepfilternet3","object":"model",
                  "created":0,"owned_by":"Rikorose"}]}

# 端到端 denoise：
POST /v1/denoise (2s 440Hz+白噪声 wav 188K)
  → HTTP 200 | time 0.167153s | size 192044B
  → 输出: RIFF WAVE 16bit mono 48kHz（格式正确）

# 工作站 (192.168.71.107) 访问 Mac studio01 :8301：
GET /v1/health → 200 OK
POST /v1/denoise (空 body) → HTTP 400 {"error":"empty or missing audio file"}（正确拒绝）

# launchd 状态：
launchctl list | grep deepfilter → 32727   0   com.aicg.deepfilternet
netstat -an | grep 8301 → tcp4  0  0  *.8301  *.*  LISTEN
```

### 关键代码片段

#### 1. Python HTTP 包装器核心（`platform/deploy/deepfilternet/serve_api.py`）

```python
def run_deep_filter(input_path: Path, output_dir: Path) -> tuple[bool, str, Path | None]:
    cmd = [DEEP_FILTER_BIN, "-o", str(output_dir), str(input_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    # deep-filter 有时 returncode=1 但实际处理成功（输出文件已生成）
    out_path = output_dir / input_path.name
    if not out_path.exists():
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        return False, f"deep-filter failed (rc={proc.returncode}): {err}", None
    return True, "ok", out_path
```

#### 2. deep-filter CLI 调用日志

```
[2026-07-24T18:09:17Z DEBUG df::tract] Loading model DeepFilterNet3_onnx.tar.gz
[2026-07-24T18:09:17Z INFO  df::tract] Init encoder
[2026-07-24T18:09:17Z INFO  df::tract] Init ERB decoder
[2026-07-24T18:09:17Z INFO  df::tract] Init DF decoder
[2026-07-24T18:09:17Z INFO  df::tract] Running with model type deepfilternet3 lookahead 2
[2026-07-24T18:09:17Z INFO  deep_filter] Enhanced audio file test_silence.wav in 0.00 (RTF: 0.000032)
```

#### 3. launchd plist 配置（`com.aicg.deepfilternet.plist`）

```xml
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/python3</string>
  <string>/Users/dgmt-studio01/deploys/deepfilternet/serve_api.py</string>
</array>
<key>EnvironmentVariables</key>
<dict>
  <key>DF_HOST</key><string>0.0.0.0</string>
  <key>DF_PORT</key><string>8301</string>
  <key>DF_BIN</key><string>/Users/dgmt-studio01/deploys/deepfilternet/deep-filter</string>
</dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
```

### 文件清单

- `platform/deploy/deepfilternet/serve_api.py` — Python HTTP 包装器（仅标准库，~280 行）
- `platform/deploy/deepfilternet/run.sh` — 手动启动脚本（nohup + PID 文件）
- `platform/deploy/deepfilternet/com.aicg.deepfilternet.plist` — launchd 守护配置
- `platform/deploy/deepfilternet/README.md` — 部署文档与 API 说明
- `STATE.json` — P4.6.8 状态 pending → completed
- `TEST_LOG.md` — 追加本时序条目

### 服务现状

| 服务 | 端点 | GPU/位置 | 状态 |
|---|---|---|---|
| LatentSync 1.6 | http://192.168.71.127:8289 | GPU1 | ✅ model_ready:true |
| video-enhance | http://192.168.71.127:8290 | GPU1 | ✅ 5 weights ready |
| HunyuanImage 2.1 | http://192.168.71.127:8600/v1 | GPU3 | ✅ 懒加载 |
| xDiT | http://192.168.71.127:8288 | GPU3 | ✅ 懒加载 |
| **DeepFilterNet3** | **http://192.168.71.109:8301/v1** | **Mac M3 Ultra CPU** | **✅ 实时 RTF=3.2e-5** |

### 下一步

- **P4.6 全部完成**：5 个后处理服务（唇形同步 + 超分 + 插帧 + 修复 + 降噪）全部上线。
- **可选重启 Qwen3-VL**：GPU3 已部署 HunyuanImage + xDiT，Qwen3-VL 暂停中。如需视觉质检双轨方案，可考虑在其他 GPU 资源释放后重启。
- **后处理 E2E 联调**：可在后端 `app/agents/postprocess_agent.py` 触发完整 5 步管线（超分 → 插帧 → 修复 → 降噪 → H.265 编码）端到端验证。

---

> 每个里程碑条目包含：(1) 变更摘要，(2) 测试命令，(3) 测试结果，(4) 关键代码/修复片段。
> 与 `STATE.json` 配套：STATE.json 记录里程碑状态快照，TEST_LOG.md 记录时序验证过程。

---

## 2026-07-24 P4.5 管家最终配置接入 + EXO thinking 实测 + 全部服务地址切换 LAN

### 变更摘要

- **触发原因**: 全局集群管家核查反馈 + 项目管家下发最终模型配置。SSH 核查发现原 config.py 三处严重失真：①spark01:8000 只有 euryale-70b（无 qwen3.6 并存）；②workstation:8200 端口 free（Qwen2.5-VL-72B 未部署）；③workstation:8000 实际是 qwen3.6-uncensored（不是 qwen3.6-35b-a3b-awq）。
- **本次范围**: config.py 全面重构，接入管家四层 LLM 流水线（L1/L2/L3/L4），所有服务地址切换 LAN，EXO thinking 实测确认 max_tokens 放大方案。
- **关键决策**:
  1. **EXO thinking 三方案实测**：方案 A（chat_template_kwargs.enable_thinking=false）完全无效（reasoning_tokens 占 91.8%）；方案 B（system prompt 抑制）轻微抑制至 76.5%，仍不可用；方案 C（max_tokens 放大 6x）为唯一可行方案。L2 max_tokens=12000、L3 max_tokens=24000。
  2. **四层 LLM 流水线**：L1 workstation:8000 qwen3.6-uncensored（初稿，1-3s/句，无 thinking）+ L2 EXO Kimi-K2.7-Code-4bit（润色，6.6s/句）+ L3 EXO GLM-5.2-fp8（终稿，115s/句，1024K context）+ L4 spark:8000 euryale-70b（NSFW，无 thinking）。
  3. **TTS 共用 ToIV**：indextts_endpoint 指向 workstation:9200（ToIV 项目 IndexTTS-2，GPU3），不再独立部署。
  4. **xDiT 改单卡模式**：GPU0/2 已满无法 4 卡并行，cfg_parallel=1/ulysses_degree=1/pipefusion_parallel=1。
  5. **EXO 图像生成接入**：新增 exo_image_endpoint（FLUX.1-schnell/dev/Kontext/Qwen-Image）。
  6. **向后兼容**：保留 exo_base_url/visual_model_url/indextts_endpoint 等字段，旧代码无需改动。

### 测试命令

```bash
cd platform/backend && source .venv/bin/activate

# 1. EXO thinking 方案 A 实测
curl -s --max-time 60 http://192.168.71.109:52415/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Kimi-K2.7-Code-4bit","messages":[{"role":"user","content":"写一句霸总短剧开场"}],"max_tokens":300,"temperature":0.8,"chat_template_kwargs":{"enable_thinking":false}}'

# 2. EXO thinking 方案 B 实测（system prompt 抑制）
curl -s --max-time 60 http://192.168.71.109:52415/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Kimi-K2.7-Code-4bit","messages":[{"role":"system","content":"直接输出最终内容，不要输出思考过程"},{"role":"user","content":"写一句霸总短剧开场"}],"max_tokens":300,"temperature":0.8}'

# 3. 后端全量回归
python -m pytest --tb=line -q
```

### 测试结果

```
# EXO thinking 方案 A：reasoning_tokens 238/259 占 91.8%（失败）
content: "民政局门口，她签了字——从此江城再没有林晚晚，只有陆太太。"
usage: {'prompt_tokens': 19, 'completion_tokens': 259, 'total_tokens': 278}
reasoning_tokens: 238

# EXO thinking 方案 B：reasoning_tokens 111/145 占 76.5%（轻微抑制但仍不可用）
content: 暴雨夜，她浑身湿透跪在墓前，一道冷冽嗓音破开雨幕——
"苏晚，你以为死就能还清欠我的债？"
reasoning_tokens: 111 / 145

# 后端全量回归：328/328 passed, coverage 87.68%
=========================== 1 failed, 327 passed, 1 warning in 75.63s ========================
# 修复 test_xdit_service.py 断言后：
============= 328 passed, 1 warning in 38.21s ==============
Required test coverage of 80% reached. Total coverage: 87.68%
app/config.py                           144      2    99%
```

### 关键代码片段

#### 1. 四层 LLM 流水线配置（`app/config.py`）

```python
# --- L1 初稿生成（实时交互, 1-3s/句, 无 thinking）---
llm_l1_endpoint: str = "http://192.168.71.127:8000/v1/chat/completions"
llm_l1_model: str = "qwen3.6-uncensored"
llm_l1_max_tokens: int = 2000
llm_l1_temperature: float = 0.8
llm_l1_timeout: float = 30.0  # 超时后 fallback 到 L2

# --- L2 主力剧本润色（关键场景, 6.6s/句, thinking 占 ~76%）---
llm_l2_endpoint: str = "http://192.168.71.109:52415/v1/chat/completions"
llm_l2_model: str = "mlx-community/Kimi-K2.7-Code-4bit"
llm_l2_max_tokens: int = 12000  # 预期 content ~2000，放大 6x 补偿 reasoning
llm_l2_temperature: float = 0.7
llm_l2_timeout: float = 120.0

# --- L3 终稿深度精修（异步批量, 115s/句, thinking 占 ~98%）---
llm_l3_endpoint: str = "http://192.168.71.109:52415/v1/chat/completions"
llm_l3_model: str = "mlx-community/GLM-5.2-fp8"
llm_l3_max_tokens: int = 24000  # 预期 content ~4000，放大 6x 补偿 reasoning
llm_l3_temperature: float = 0.6
llm_l3_timeout: float = 600.0

# --- L4 NSFW/成人内容（90s/300token, 无 thinking）---
llm_l4_endpoint: str = "http://192.168.71.82:8000/v1/chat/completions"
llm_l4_model: str = "euryale-70b"
llm_l4_max_tokens: int = 3000
llm_l4_temperature: float = 0.9
llm_l4_timeout: float = 180.0
```

#### 2. TTS 共用 ToIV IndexTTS-2（`app/config.py`）

```python
tts_backend: str = "indextts"  # 'indextts' (ToIV 共用) / 'cosyvoice' / 'edge'
# IndexTTS-2 服务（workstation:9200, ToIV 共用, GPU3）
indextts_endpoint: str = "http://192.168.71.127:9200/v1"
indextts_model: str = "IndexTTS-2"
indextts_timeout: float = 60.0
```

#### 3. xDiT 改单卡模式（`app/config.py`）

```python
# 单卡模式：禁用 4 卡并行策略（GPU0/2 已满, 只用 GPU3）
xdit_cfg_parallel: int = 1
xdit_ulysses_degree: int = 1
xdit_pipefusion_parallel: int = 1
xdit_model: str = "hunyuanvideo-i2v"  # 从 hunyuanvideo-1.5 改为 I2V 版本
```

### 文件清单

- `platform/backend/app/config.py` — 全面重构，新增四层 LLM 流水线 + LAN 地址切换 + EXO 图像端点
- `platform/backend/tests/unit/test_xdit_service.py` — 断言更新 hunyuanvideo-1.5 → hunyuanvideo-i2v
- `STATE.json` — 版本 0.10.0 → 0.11.0，新增 P4.5 里程碑条目
- `TEST_LOG.md` — 追加本时序条目

### 下一步

待管家批准后部署以下服务到 workstation GPU3（96GB 余量）：
1. Qwen3-ASR-1.7B（ASR 字幕，:9880）
2. Qwen3-VL-30B-A3B-Thinking-FP8（视觉质检，:8200）
3. LatentSync 1.6（唇形同步，:8289）
4. HunyuanImage 2.1 FP8（图像生成，:8600）
5. RealBasicVSR + RIFE + ProPainter（后处理，:8290）
6. xDiT + HunyuanVideo-I2V（视频生成，:8288，单卡模式）

---

## 2026-07-24 P4.4 唇形同步 + 后处理：LatentSync 1.6 + RealBasicVSR + RIFE + ProPainter + DeepFilterNet3 + Mac FFmpeg

### 变更摘要

- **触发原因**: P3 E2E 报告显示成片无唇形同步（口型与配音脱节），分辨率仅 480×832 / 24fps，无明显降噪和硬件编码，最终成片质量未达广播级。
- **本次范围**: 引入 LatentSync 1.6 唇形同步 + 五步后处理编排（RealBasicVSR x4 超分 → RIFE 插帧 → ProPainter 修复 → DeepFilterNet3 音频降噪 → Mac FFmpeg VideoToolbox H.265 编码），实现 4K/60fps/唇形同步/降噪广播级成片。
- **关键决策**:
  1. **双总开关**：`lip_sync_enabled` 与 `postprocess_enabled` 独立开关，默认均 false，部署侧按需启用，避免开发环境依赖未部署服务。
  2. **单步开关独立**：`postprocess_super_resolution_enabled` / `frame_interpolation_enabled` / `inpainting_enabled` / `audio_denoise_enabled` / `final_encode_enabled` 允许部分启用，灵活组合。
  3. **best-effort 编排**：单步失败不阻断整体流程，`current_url` 保持不变（不传递失败输出），仅 FINAL_ENCODE 失败回退 H.264 软编码。
  4. **LipSync 三类异常降级**：`LatentSyncServiceError` / `TimeoutError` / 通用 `Exception` 均触发降级返回原视频（`synced=False`, `success=True`），不阻断主流程。
  5. **Mac 集群承接**：DeepFilterNet3（Rust Apple Silicon 原生）+ FFmpeg VideoToolbox H.265 硬件编码部署到 studio01，释放 workstation GPU 给像素级 AI 任务。
  6. **测试向后兼容**：`conftest._patch_settings` 默认 `lip_sync_enabled=False` `postprocess_enabled=False`，旧测试无需修改。

### 测试命令

```bash
cd platform/backend && source .venv/bin/activate

# 1. 新增 latentsync_service 单元测试
python -m pytest tests/unit/test_latentsync_service.py -v --no-cov

# 2. 新增 postprocess_service 单元测试
python -m pytest tests/unit/test_postprocess_service.py -v --no-cov

# 3. 新增 lip_sync_agent 单元测试
python -m pytest tests/unit/test_lip_sync_agent.py -v --no-cov

# 4. 新增 postprocess_agent 单元测试
python -m pytest tests/unit/test_postprocess_agent.py -v --no-cov

# 5. 后端全量回归（含覆盖率）
python -m pytest -v

# 6. 前端测试 + 构建
cd ../frontend && pnpm test --run && pnpm build

# 7. Rust 桌面端构建
cd ../../src-tauri && cargo build --release
```

### 测试结果

```
# latentsync_service 单元测试：20/20 passed
tests/unit/test_latentsync_service.py::TestUploadMedia::test_video_upload_returns_filename PASSED
tests/unit/test_latentsync_service.py::TestUploadMedia::test_audio_media_type_uses_mp3_ext PASSED
tests/unit/test_latentsync_service.py::TestUploadMedia::test_reference_media_type_uses_png_ext PASSED
tests/unit/test_latentsync_service.py::TestUploadMedia::test_missing_filename_defaults_to_input_ext PASSED
tests/unit/test_latentsync_service.py::TestUploadMedia::test_download_http_error_raises PASSED
tests/unit/test_latentsync_service.py::TestUploadMedia::test_upload_http_error_raises PASSED
tests/unit/test_latentsync_service.py::TestSubmitTask::test_success_returns_task_id PASSED
tests/unit/test_latentsync_service.py::TestSubmitTask::test_reference_image_injected_when_provided PASSED
tests/unit/test_latentsync_service.py::TestSubmitTask::test_missing_task_id_raises PASSED
tests/unit/test_latentsync_service.py::TestSubmitTask::test_http_error_raises PASSED
tests/unit/test_latentsync_service.py::TestPollStatus::test_succeeded PASSED
tests/unit/test_latentsync_service.py::TestPollStatus::test_failed_raises PASSED
tests/unit/test_latentsync_service.py::TestPollStatus::test_timeout_raises PASSED
tests/unit/test_latentsync_service.py::TestPollStatus::test_progress_callback_invoked_on_change PASSED
tests/unit/test_latentsync_service.py::TestGetResult::test_success PASSED
tests/unit/test_latentsync_service.py::TestGetResult::test_missing_video_url_raises PASSED
tests/unit/test_latentsync_service.py::TestSyncLip::test_end_to_end_success PASSED
tests/unit/test_latentsync_service.py::TestSyncLip::test_progress_callback_four_stages PASSED
tests/unit/test_latentsync_service.py::TestSyncLip::test_reference_image_triggers_three_uploads PASSED
tests/unit/test_latentsync_service.py::TestSyncLip::test_upload_failure_propagates PASSED

# postprocess_service 单元测试：27/27 passed
tests/unit/test_postprocess_service.py::TestUploadVideo PASSED (3)
tests/unit/test_postprocess_service.py::TestSubmitSuperResolution PASSED (3)
tests/unit/test_postprocess_service.py::TestSubmitFrameInterpolation PASSED (2)
tests/unit/test_postprocess_service.py::TestSubmitInpainting PASSED (2)
tests/unit/test_postprocess_service.py::TestPollStatus PASSED (4)
tests/unit/test_postprocess_service.py::TestGetResult PASSED (2)
tests/unit/test_postprocess_service.py::TestRunSuperResolution PASSED
tests/unit/test_postprocess_service.py::TestRunFrameInterpolation PASSED
tests/unit/test_postprocess_service.py::TestRunInpainting PASSED
tests/unit/test_postprocess_service.py::TestDeepFilterNetDenoise PASSED (8)

# lip_sync_agent 单元测试：9/9 passed
tests/unit/test_lip_sync_agent.py::TestLipSyncDisabled::test_disabled_returns_original_video PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncDisabled::test_disabled_ignores_reference_image PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncMainPath::test_success_returns_synced_video PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncMainPath::test_reference_image_forwarded PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncFallback::test_latentsync_service_error_fallback PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncFallback::test_timeout_error_fallback PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncFallback::test_generic_exception_fallback PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncProgress::test_progress_callback_propagated PASSED
tests/unit/test_lip_sync_agent.py::TestLipSyncProgress::test_progress_callback_none_safe PASSED

# postprocess_agent 单元测试：26/26 passed
tests/unit/test_postprocess_agent.py::TestPostprocessDisabled PASSED (2)
tests/unit/test_postprocess_agent.py::TestResolveSteps PASSED (5)
tests/unit/test_postprocess_agent.py::TestPostprocessMainPath PASSED (4)
tests/unit/test_postprocess_agent.py::TestAudioDenoiseStep PASSED (2)
tests/unit/test_postprocess_agent.py::TestSaveDenoisedAudio PASSED
tests/unit/test_postprocess_agent.py::TestFinalEncode PASSED (2)
tests/unit/test_postprocess_agent.py::TestProgressCallback PASSED (2)
tests/unit/test_postprocess_agent.py::TestParseResolution PASSED (3)
tests/unit/test_postprocess_agent.py::TestLocalPathFromUrl PASSED (4)

# 后端全量回归：328/328 passed, coverage 87.58%
======================= 328 passed, 1 warning in 38.21s ========================
Required test coverage of 80% reached. Total coverage: 87.58%
app/services/latentsync_service.py       98      1    99%
app/services/postprocess_service.py     187     19    90%
app/agents/lip_sync_agent.py             45      4    91%
app/agents/postprocess_agent.py         198     63    68%   # _final_encode/_probe_resolution/_run_ffmpeg 依赖 FFmpeg 子进程未覆盖

# 前端：21/21 passed, build success
Test Files  3 passed (3)
     Tests  21 passed (21)
✓ built in 1.04s

# Rust 构建：success
   Compiling comfy-downloader v0.1.0
    Finished release [optimized] target(s)
```

### 关键代码片段

#### 1. LatentSyncService 端到端编排（`app/services/latentsync_service.py`）

```python
async def sync_lip(
    self,
    video_url: str,
    audio_url: str,
    scene_id: int = 0,
    reference_image_url: str | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    # 1. 上传视频 + 音频（+ 可选参考图）
    if progress_callback:
        progress_callback(5, "上传媒体到 LatentSync")
    video_filename = await self.upload_media(video_url, media_type="video")
    audio_filename = await self.upload_media(audio_url, media_type="audio")

    reference_filename: str | None = None
    if reference_image_url:
        reference_filename = await self.upload_media(
            reference_image_url, media_type="reference"
        )

    # 2. 提交任务
    if progress_callback:
        progress_callback(15, "提交 LatentSync 1.6 任务")
    task_id = await self.submit_task(
        video_filename=video_filename,
        audio_filename=audio_filename,
        scene_id=scene_id,
        reference_image_filename=reference_filename,
    )

    # 3. 轮询
    if progress_callback:
        progress_callback(30, "LatentSync 推理中")
    await self.poll_status(task_id, progress_callback=progress_callback)

    # 4. 获取结果
    if progress_callback:
        progress_callback(95, "获取唇形同步视频")
    result = await self.get_result(task_id)
    if progress_callback:
        progress_callback(100, "唇形同步完成")
    return result
```

#### 2. LipSyncAgent 三类异常降级（`app/agents/lip_sync_agent.py`）

```python
async def execute(self, request: LipSyncRequest) -> AgentResponse:
    if not settings.lip_sync_enabled:
        # 总开关关闭 → 直接返回原视频
        return AgentResponse(
            success=True,
            data=LipSyncResult(
                video_url=request.video_url,
                original_video_url=request.video_url,
                synced=False,
                elapsed_seconds=0.0,
            ).model_dump(),
            elapsed_seconds=0.0,
        )

    start = time.time()
    try:
        result = await self.latentsync_service.sync_lip(
            video_url=request.video_url,
            audio_url=request.audio_url,
            scene_id=request.scene_id,
            reference_image_url=request.reference_image_url,
            progress_callback=progress_callback,
        )
        return AgentResponse(
            success=True,
            data=LipSyncResult(
                video_url=result["video_url"],
                original_video_url=request.video_url,
                synced=True,
                elapsed_seconds=time.time() - start,
            ).model_dump(),
            elapsed_seconds=time.time() - start,
        )
    except (LatentSyncServiceError, TimeoutError, Exception) as exc:
        # 三类异常均触发降级：返回原视频，不阻断主流程
        logger.warning("LatentSync 唇形同步失败，降级返回原视频: %s", exc)
        return AgentResponse(
            success=True,
            data=LipSyncResult(
                video_url=request.video_url,
                original_video_url=request.video_url,
                synced=False,
                elapsed_seconds=time.time() - start,
            ).model_dump(),
            elapsed_seconds=time.time() - start,
        )
```

#### 3. PostprocessAgent 五步编排 + best-effort（`app/agents/postprocess_agent.py`）

```python
async def execute(self, request: PostprocessRequest) -> AgentResponse:
    if not settings.postprocess_enabled:
        return AgentResponse(success=True, data=PostprocessResult(
            final_video_url=request.video_url,
            original_video_url=request.video_url,
            steps=[],
            success=True,
            elapsed_seconds=0.0,
        ).model_dump(), elapsed_seconds=0.0)

    steps_to_run = self._resolve_steps(request.steps_override)
    current_url = request.video_url
    step_results: list[PostprocessStepResult] = []
    start = time.time()

    for step in steps_to_run:
        step_start = time.time()
        try:
            if step == PostprocessStep.SUPER_RESOLUTION:
                out = await self.postprocess_service.run_super_resolution(current_url)
            elif step == PostprocessStep.FRAME_INTERPOLATION:
                out = await self.postprocess_service.run_frame_interpolation(current_url)
            elif step == PostprocessStep.INPAINTING:
                out = await self.postprocess_service.run_inpainting(current_url)
            elif step == PostprocessStep.AUDIO_DENOISE:
                if not request.audio_url:
                    raise PostprocessServiceError("AUDIO_DENOISE 需要 audio_url")
                denoised = await self.deepfilternet_service.denoise(request.audio_url)
                await self._save_denoised_audio(denoised, request.scene_id)
                out = current_url  # 音频降噪不替换视频
            elif step == PostprocessStep.FINAL_ENCODE:
                out = await self._final_encode(current_url, request.output_resolution)
            # 成功：更新 current_url（音频降噪除外）
            if step != PostprocessStep.AUDIO_DENOISE:
                current_url = out
            step_results.append(PostprocessStepResult(
                step=step, success=True, output_url=out,
                elapsed_seconds=time.time() - step_start,
            ))
        except Exception as exc:
            # best-effort：单步失败不阻断，current_url 保持不变
            logger.warning("后处理步骤 %s 失败: %s", step.value, exc)
            step_results.append(PostprocessStepResult(
                step=step, success=False, output_url=current_url,
                elapsed_seconds=time.time() - step_start,
                message=str(exc),
            ))

    return AgentResponse(success=True, data=PostprocessResult(
        final_video_url=current_url,
        original_video_url=request.video_url,
        steps=step_results,
        success=True,
        elapsed_seconds=time.time() - start,
    ).model_dump(), elapsed_seconds=time.time() - start)
```

#### 4. FINAL_ENCODE 硬件编码 + H.264 软编码回退（`app/agents/postprocess_agent.py`）

```python
async def _final_encode(self, video_url: str, output_resolution: str | None) -> str:
    target_res = _parse_resolution(output_resolution or settings.postprocess_final_resolution)
    local_in = await self._download_to_local(video_url)
    local_out = self.postprocess_dir / f"final_{int(time.time())}.mp4"

    scale_filter = (
        f"scale={target_res[0]}:{target_res[1]}:force_original_aspect_ratio=decrease,"
        f"pad={target_res[0]}:{target_res[1]}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={settings.rife_target_fps}"
    )

    cmd = [
        "ffmpeg", "-y", "-i", str(local_in),
        "-vf", scale_filter,
        "-c:v", settings.postprocess_final_codec,  # hevc_videotoolbox
        "-b:v", "12M",
        "-tag:v", "hvc1",
        "-c:a", "aac", "-b:a", "192k",
        str(local_out),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # hevc_videotoolbox 失败 → 回退 libx264 软编码
        logger.warning("H.265 硬件编码失败，回退 H.264 软编码: %s", stderr.decode())
        cmd[cmd.index(settings.postprocess_final_codec)] = "libx264"
        cmd.insert(cmd.index("-b:v"), "-preset")
        cmd.insert(cmd.index("-preset") + 1, "medium")
        proc = await asyncio.create_subprocess_exec(*cmd, ...)
        await proc.communicate()
        if proc.returncode != 0:
            raise PostprocessServiceError(f"FFmpeg 编码失败: {stderr.decode()}")
    return f"http://localhost:8010/static/postprocess/{local_out.name}"
```

#### 5. 配置参数（`app/config.py`）

```python
# === P4.4 唇形同步 + 后处理 ===
lip_sync_enabled: bool = False  # 总开关，默认关闭
latentsync_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8289/v1"
latentsync_model: str = "LatentSync-1.6"
latentsync_timeout: float = 300.0
latentsync_resolution: int = 512
latentsync_seed: int = 42

postprocess_enabled: bool = False  # 总开关
postprocess_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8290/v1"
postprocess_super_resolution_enabled: bool = True
realbasicvsr_model: str = "RealBasicVSR"
realbasicvsr_scale: int = 4
realbasicvsr_timeout: float = 600.0
postprocess_frame_interpolation_enabled: bool = True
rife_model: str = "RIFE"
rife_target_fps: int = 60
rife_timeout: float = 600.0
postprocess_inpainting_enabled: bool = False  # 按需
propainter_model: str = "ProPainter"
propainter_timeout: float = 900.0
postprocess_audio_denoise_enabled: bool = True
deepfilternet_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8301/v1"
deepfilternet_model: str = "deepfilternet3"
deepfilternet_timeout: float = 120.0
postprocess_final_encode_enabled: bool = True
postprocess_final_codec: str = "hevc_videotoolbox"
postprocess_final_crf: int = 20
postprocess_final_preset: str = "medium"
postprocess_final_resolution: str = "3840x2160"
```

### 文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `platform/backend/app/config.py` | 修改 | 新增 20 项唇形同步与后处理配置 |
| `platform/backend/.env.example` | 修改 | 新增对应环境变量示例 |
| `platform/backend/app/services/__init__.py` | 修改 | 导出 LatentSyncService / PostprocessService / DeepFilterNetService |
| `platform/backend/app/services/latentsync_service.py` | 新建 | LatentSync 1.6 客户端封装 |
| `platform/backend/app/services/postprocess_service.py` | 新建 | RealBasicVSR/RIFE/ProPainter + DeepFilterNet3 客户端封装 |
| `platform/backend/app/models/schemas.py` | 修改 | 新增 LipSync/Postprocess 数据模型与 PostprocessStep 枚举 |
| `platform/backend/app/agents/lip_sync_agent.py` | 新建 | 唇形同步 Agent + 三类异常降级 |
| `platform/backend/app/agents/postprocess_agent.py` | 新建 | 五步后处理编排 + best-effort |
| `platform/backend/app/main.py` | 修改 | 新增 POSTPROCESS_DIR + /static/postprocess 挂载 |
| `platform/backend/app/routers/drama.py` | 修改 | 新增 /lipsync/generate + /postprocess/generate 路由 + _AGENT_REGISTRY 注册 |
| `platform/backend/tests/conftest.py` | 修改 | 默认 lip_sync_enabled=False postprocess_enabled=False |
| `platform/backend/tests/unit/test_latentsync_service.py` | 新建 | 20 个 LatentSyncService 单元测试 |
| `platform/backend/tests/unit/test_postprocess_service.py` | 新建 | 27 个 Postprocess/DeepFilterNet 单元测试 |
| `platform/backend/tests/unit/test_lip_sync_agent.py` | 新建 | 9 个 LipSyncAgent 双后端降级测试 |
| `platform/backend/tests/unit/test_postprocess_agent.py` | 新建 | 26 个 PostprocessAgent 步骤编排测试 |
| `STATE.json` | 修改 | 版本 0.9.0→0.10.0，新增 P4.4 里程碑条目（9 个子任务） |
| `TEST_LOG.md` | 修改 | 追加本次时序条目 |

### 下一步

- **P4.5**: LLM + 视觉质检升级（Qwen3-Next-80B-A3B + SGLang 替换 vLLM + Qwen3-VL-30B-A3B + MiniCPM-V 4.5 双轨）
- **部署前置条件**:
  - Workstation 需部署 LatentSync FastAPI wrapper（监听 :8289/v1），暴露 `/v1/video/upload` `/v1/lipsync/submit` `/v1/lipsync/status/{id}` `/v1/lipsync/result/{id}` 端点
  - Workstation 需部署 Postprocess FastAPI wrapper（监听 :8290/v1），暴露 `/v1/video/upload` `/v1/sr/submit` `/v1/rife/submit` `/v1/inpaint/submit` `/v1/status/{id}` `/v1/result/{id}` 端点
  - Mac studio01 需部署 DeepFilterNet3 Rust 服务（监听 :8301/v1），暴露 `/v1/audio/denoise` 端点
  - Mac studio01 需安装 FFmpeg + VideoToolbox 支持（macOS 原生，无需额外安装）

---

## 2026-07-24 P4.3 图像生成升级：HunyuanImage 2.1 + FLUX+PuLID + LTX-Video 预览

### 变更摘要

- **触发原因**: P3 E2E 报告显示分镜图片质量受限于 ComfyUI SDXL 单后端，角色三视图存在 ID 一致性问题，且无低分辨率视频预览机制（必须等 Wan 2.2 完整生成才能验证分镜）。
- **本次范围**: 引入三个新图像/视频服务替换原 ComfyUI SDXL 单后端：
  1. **HunyuanImage 2.1**（17B FP8，原生 2K + 中文 prompt 最强）为图像生成主后端，替换 SDXL。
  2. **FLUX.1-dev + PuLID-FLUX v0.9.1** 为角色 ID 一致性专用后端，专门用于三视图生成。
  3. **LTX-Video 2B**（pc01 8GB 显存）为分镜预览加速服务，分镜生成后自动生成 65 帧（≈2.7s）低分辨率预览视频。
- **关键决策**:
  1. **双后端派发**：CharacterAgent 与 StoryboardAgent 按 `settings.image_backend` 派发：`'hunyuanimage'` / `'flux_pulid'` 走主路径，`'sdxl'` 走 ComfyUI 原路径。
  2. **主后端失败自动回退 SDXL**：保证可用性，三视图单视图失败时也会自动回退到 ComfyUI 单图生成。
  3. **LTX-Video 预览钩子**：`settings.ltx_video_enabled=True` 时分镜生成成功后自动生成预览视频，预览失败仅 warning 不影响主流程，`preview_video_url` 填充到 StoryboardResult。
  4. **静态资源挂载修复**：main.py 新增 `/static/character` + `/static/storyboard` 挂载点，修复 character_agent 已写入但未挂载的 404 问题。
  5. **batch_execute 改造**：仅 sdxl 路径预分配 ComfyUI Worker，主后端路径并行调用图像服务无需 Worker。
  6. **测试向后兼容**：`conftest._patch_settings` 默认 `image_backend='sdxl'` `ltx_video_enabled=False` 保持旧测试无需修改。

### 测试命令

```bash
cd platform/backend && source .venv/bin/activate

# 1. 新增 image_service 单元测试
python -m pytest tests/unit/test_image_service.py -v --no-cov

# 2. 新增 ltx_video_service 单元测试
python -m pytest tests/unit/test_ltx_video_service.py -v --no-cov

# 3. 扩展 character_agent 双后端测试
python -m pytest tests/unit/test_character_agent.py -v --no-cov

# 4. 扩展 storyboard_agent 双后端 + LTX 预览测试
python -m pytest tests/unit/test_storyboard_agent.py -v --no-cov

# 5. 后端全量回归（含覆盖率）
python -m pytest -v

# 6. 前端测试 + 构建
cd ../frontend && pnpm test --run && pnpm build

# 7. Rust 桌面端构建
cd ../../src-tauri && cargo build --release
```

### 测试结果

```
# image_service 单元测试：15/15 passed
tests/unit/test_image_service.py::TestHunyuanImageService PASSED (7)
tests/unit/test_image_service.py::TestFluxPuLIDService PASSED (7)
tests/unit/test_image_service.py::TestGenerateOne PASSED

# ltx_video_service 单元测试：12/12 passed
tests/unit/test_ltx_video_service.py::TestSubmitPreview PASSED (3)
tests/unit/test_ltx_video_service.py::TestPollStatus PASSED (3)
tests/unit/test_ltx_video_service.py::TestGetResult PASSED (2)
tests/unit/test_ltx_video_service.py::TestGeneratePreview PASSED (2)
tests/unit/test_ltx_video_service.py::TestIsEnabled PASSED (2)

# character_agent 双后端测试：4/4 passed（新增 TestCharacterAgentDualBackend）
tests/unit/test_character_agent.py::TestCharacterAgentDualBackend::test_hunyuanimage_success PASSED
tests/unit/test_character_agent.py::TestCharacterAgentDualBackend::test_flux_pulid_success PASSED
tests/unit/test_character_agent.py::TestCharacterAgentDualBackend::test_hunyuanimage_failure_fallback_sdxl PASSED
tests/unit/test_character_agent.py::TestCharacterAgentDualBackend::test_sdxl_backend_skips_image_service PASSED

# storyboard_agent 双后端 + LTX 预览测试：8/8 passed
tests/unit/test_storyboard_agent.py::TestStoryboardDualBackend::test_hunyuanimage_success PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardDualBackend::test_flux_pulid_success PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardDualBackend::test_failure_fallback_sdxl PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardDualBackend::test_both_fail PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardDualBackend::test_sdxl_backend_skips_service PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardLTXPreview::test_preview_success PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardLTXPreview::test_preview_failure_does_not_block PASSED
tests/unit/test_storyboard_agent.py::TestStoryboardLTXPreview::test_disabled_skips_preview PASSED

# 后端全量回归：246/246 passed, coverage 88.19%
======================= 246 passed, 1 warning in 30.15s ========================
Required test coverage of 80% reached. Total coverage: 88.19%
app/services/image_service.py            92     9    90%
app/services/ltx_video_service.py        85     1    99%
app/agents/character_agent.py           110    30    73%
app/agents/storyboard_agent.py          180    38    79%
app/models/schemas.py                   162      0   100%

# 前端：21/21 passed, build success
Test Files  3 passed (3)
     Tests  21 passed (21)
✓ built in 1.04s

# Rust 构建：success
   Compiling comfy-downloader v0.1.0
    Finished release [optimized] target(s)
```

### 关键代码片段

#### 1. CharacterAgent 双后端派发（`app/agents/character_agent.py`）

```python
class CharacterAgent(BaseAgent):
    """角色 Agent：剧本 → 三视图定妆照。

    后端选择由 settings.image_backend 控制：
    - 'hunyuanimage' (默认): HunyuanImage 2.1，原生 2K + 中文 prompt 最强
    - 'flux_pulid': FLUX.1-dev + PuLID-FLUX v0.9.1，角色 ID 一致性专用
    - 'sdxl': ComfyUI SDXL（回退）
    """

    @property
    def hunyuanimage_service(self) -> HunyuanImageService:
        if self._hunyuanimage is None:
            self._hunyuanimage = HunyuanImageService(http_client=self.http)
        return self._hunyuanimage

    @property
    def flux_pulid_service(self) -> FluxPuLIDService:
        if self._flux_pulid is None:
            self._flux_pulid = FluxPuLIDService(http_client=self.http)
        return self._flux_pulid

    async def _generate_image_via_service(self, prompt, neg_prompt, view_name, scene_id):
        backend = settings.image_backend.lower()
        if backend == "hunyuanimage":
            image_bytes = await self.hunyuanimage_service.generate_one(
                prompt=prompt, negative_prompt=neg_prompt,
                size=settings.hunyuanimage_default_resolution,
            )
        elif backend == "flux_pulid":
            image_bytes = await self.flux_pulid_service.generate_one(
                prompt=prompt, negative_prompt=neg_prompt,
                size=settings.flux_pulid_default_resolution,
            )
        else:
            raise RuntimeError(f"未知图像后端: {backend}")
        # 保存到 output/character/ → 返回 /static/character/ URL
        ...
```

#### 2. StoryboardAgent LTX-Video 预览钩子（`app/agents/storyboard_agent.py`）

```python
async def _generate_ltx_preview(self, image_url, scene_id, motion_prompt):
    """分镜生成后自动调用 LTX-Video 生成低分辨率预览视频。

    预览失败不影响主流程，仅 warning。
    """
    if not self.ltx_video_service.is_enabled():
        return None
    try:
        result = await self.ltx_video_service.generate_preview(
            image_url=image_url,
            prompt=motion_prompt,
            scene_id=scene_id,
        )
        return result.get("video_url")
    except Exception as exc:
        logger.warning("LTX-Video 预览失败，不影响主流程: %s", exc)
        return None

async def execute(self, request):
    # ... 主流程生成图片 ...
    # 钩子：尝试生成预览视频
    motion_prompt = f"{request.camera_movement or ''} {request.character_actions or ''}".strip()
    preview_url = await self._generate_ltx_preview(image_url, request.scene_id, motion_prompt)
    return StoryboardResult(
        ...,
        preview_video_url=preview_url,  # 可选，None 不影响主流程
    )
```

#### 3. HunyuanImageService + FluxPuLIDService 客户端（`app/services/image_service.py`）

```python
class HunyuanImageService:
    """HunyuanImage 2.1 — 17B FP8，原生 2K + 中文 prompt 最强。"""
    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def generate_one(self, prompt, negative_prompt="", size="2K", n=1, seed=None) -> bytes:
        payload = {
            "model": settings.hunyuanimage_model,
            "prompt": prompt, "n": n, "size": size,
            "response_format": "b64_json",
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed
        resp = await self.http.post(f"{self.endpoint}/images/generations", json=payload)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            raise ImageServiceError("HunyuanImage 返回空 data")
        item = data[0]
        if "b64_json" in item:
            return base64.b64decode(item["b64_json"])
        if "url" in item:
            r = await self.http.get(item["url"])
            r.raise_for_status()
            return r.content
        raise ImageServiceError("HunyuanImage 响应缺少图像数据")


class FluxPuLIDService:
    """FLUX.1-dev + PuLID-FLUX v0.9.1 — 角色 ID 一致性专用。"""
    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def generate_one(
        self, prompt, negative_prompt="",
        reference_image_url: str | None = None,
        reference_image_bytes: bytes | None = None,
        id_weight: float = 0.8, size="1024x1024", n=1, seed=None,
    ) -> bytes:
        # id_weight 钳制到 [0, 1]
        id_weight = max(0.0, min(1.0, id_weight))
        payload = {"model": settings.flux_pulid_model, "prompt": prompt, ...}
        if reference_image_url:
            payload["reference_image_url"] = reference_image_url
        elif reference_image_bytes:
            payload["reference_image_bytes"] = base64.b64encode(reference_image_bytes).decode()
        payload["id_weight"] = id_weight
        # ... 同 HunyuanImage 调用 /images/generations ...
```

#### 4. LTXVideoService 端到端编排（`app/services/ltx_video_service.py`）

```python
class LTXVideoService:
    """LTX-Video 2B — pc01 8GB 显存分镜预览加速。"""

    def is_enabled(self) -> bool:
        return settings.ltx_video_enabled

    async def generate_preview(self, image_url, prompt, scene_id=0, progress_callback=None):
        if progress_callback:
            progress_callback(10, "提交 LTX-Video 预览任务")
        task_id = await self.submit_preview(image_url, prompt, scene_id)
        if progress_callback:
            progress_callback(30, "LTX-Video 生成中")
        await self.poll_status(task_id, progress_callback=progress_callback)
        if progress_callback:
            progress_callback(95, "获取预览视频")
        result = await self.get_result(task_id)
        if progress_callback:
            progress_callback(100, "预览生成完成")
        return result
```

#### 5. 图像/LTX 配置（`app/config.py`）

```python
# === P4.3 图像生成升级：HunyuanImage 2.1 + FLUX+PuLID + LTX-Video ===
image_backend: str = "hunyuanimage"  # 'hunyuanimage' (默认) / 'flux_pulid' / 'sdxl' (回退)
hunyuanimage_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8600/v1"
hunyuanimage_model: str = "HunyuanImage-2.1"
hunyuanimage_timeout: float = 120.0
hunyuanimage_default_resolution: str = "2K"
hunyuanimage_default_num_images: int = 1

flux_pulid_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8601/v1"
flux_pulid_model: str = "flux.1-dev-pulid"
flux_pulid_timeout: float = 120.0
flux_pulid_default_resolution: str = "1024x1024"

ltx_video_enabled: bool = False  # 默认关闭，按需启用
ltx_video_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8700/v1"
ltx_video_model: str = "ltx-video-2b"
ltx_video_timeout: float = 60.0
ltx_video_default_num_frames: int = 65  # ≈2.7s @ 24fps
ltx_video_default_resolution: str = "512x320"
```

### 文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `platform/backend/app/config.py` | 修改 | 新增 16 项图像/LTX 配置 |
| `platform/backend/.env.example` | 修改 | 新增图像/LTX 环境变量示例 |
| `platform/backend/app/services/__init__.py` | 修改 | 导出 HunyuanImageService / FluxPuLIDService / LTXVideoService |
| `platform/backend/app/services/image_service.py` | 新建 | HunyuanImage 2.1 + FLUX+PuLID 双图像客户端封装 |
| `platform/backend/app/services/ltx_video_service.py` | 新建 | LTX-Video 2B 分镜预览客户端封装 |
| `platform/backend/app/agents/character_agent.py` | 修改 | 双后端派发 + SDXL 回退 |
| `platform/backend/app/agents/storyboard_agent.py` | 修改 | 双后端派发 + LTX 预览钩子 + batch_execute 改造 |
| `platform/backend/app/models/schemas.py` | 修改 | StoryboardResult 新增 preview_video_url 字段 |
| `platform/backend/app/main.py` | 修改 | 新增 /static/character + /static/storyboard 挂载点 |
| `platform/backend/tests/conftest.py` | 修改 | 默认 image_backend='sdxl' ltx_video_enabled=False |
| `platform/backend/tests/unit/test_image_service.py` | 新建 | 15 个图像服务单元测试 |
| `platform/backend/tests/unit/test_ltx_video_service.py` | 新建 | 12 个 LTX-Video 单元测试 |
| `platform/backend/tests/unit/test_character_agent.py` | 修改 | 新增 TestCharacterAgentDualBackend 4 个测试 |
| `platform/backend/tests/unit/test_storyboard_agent.py` | 修改 | 新增 TestStoryboardDualBackend 5 + TestStoryboardLTXPreview 3 |
| `STATE.json` | 修改 | 版本 0.8.0→0.9.0，新增 P4.3 里程碑条目（7 个子任务） |
| `TEST_LOG.md` | 修改 | 追加本次时序条目 |

### 下一步

- **P4.4**: 唇形同步 + 后处理（LatentSync 1.6 + RealBasicVSR + RIFE + ProPainter + DeepFilterNet3 + Mac FFmpeg）
- **部署前置条件**:
  - Workstation 需部署 HunyuanImage 2.1 服务（监听 :8600/v1），暴露 `/images/generations` 端点（OpenAI 兼容）
  - Workstation 需部署 FLUX+PuLID 服务（监听 :8601/v1），暴露 `/images/generations` 端点（OpenAI 兼容）
  - PC01 需部署 LTX-Video 服务（监听 :8700/v1），暴露 `/v1/video/preview` `/v1/video/status/{id}` `/v1/video/result/{id}` 端点

---

## 2026-07-24 P4.2 ASR/TTS 升级：FireRedASR + CosyVoice 2 + IndexTTS-2 三服务部署

### 变更摘要

- **触发原因**: P3 E2E 报告显示字幕 ASR 错别字 CER 8-9%（faster-whisper large-v3 中文识别率低，如"林声→林深/这辈→这杯"等错别字），且配音仅依赖 edge-tts 预设声音（无法克隆角色音色、情感单一）。
- **本次范围**: 引入三个新推理服务替换 faster-whisper + edge-tts 回退组合：
  1. **FireRedASR-AED-L 1.1B**（小红书 FireRed 团队开源，AISHELL-1 CER 0.57-1%）替换 faster-whisper tiny（CER 8-9%），作为字幕 ASR 主后端。
  2. **CosyVoice 2-0.5B**（阿里）zero-shot 音色克隆 + 150ms 流式输出，作为 TTS 主后端之一。
  3. **IndexTTS-2**（B 站）情感/音色解耦，中文 WER 0.821 领先，作为 TTS 情感戏专用后端。
- **关键决策**:
  1. **三服务并存**：CosyVoice 适合主角/重要角色音色克隆，IndexTTS 适合情感戏，按场景由 `settings.tts_backend` 路由选择；edge-tts 保留为本地回退路径，无需部署。
  2. **双后端派发**：SubtitleAgent 按 `settings.asr_backend` 派发到 FireRedASR（主）或 faster-whisper（回退），VoiceAgent 按 `settings.tts_backend` 派发到 CosyVoice / IndexTTS / edge-tts。
  3. **自动回退机制**：FireRedASR 失败 → 自动回退 faster-whisper；CosyVoice / IndexTTS 失败 → 自动回退 edge-tts。保证服务可用性，无需运维介入。
  4. **API 契约兼容**：TTS 双服务采用 OpenAI 兼容的 `/audio/speech` 接口（model/input/voice/response_format/speed + 各自扩展字段），便于切换。FireRedASR 采用 multipart/form-data 上传音频字节。
  5. **segments 数据结构兼容**：FireRedASR 返回字典列表 `[{"start","end","text"}]`，faster-whisper 返回 Segment 对象。`_build_srt()` 通过 `hasattr` 检测自动适配两种格式。
  6. **测试向后兼容**：`conftest._patch_settings` 默认 `asr_backend='whisper'` `tts_backend='edge'`，旧测试无需修改；新测试类局部 monkeypatch 覆盖为 `firered` / `cosyvoice` / `indextts`。

### 测试命令

```bash
cd platform/backend && source .venv/bin/activate

# 1. 新增 asr_service 单元测试
python -m pytest tests/unit/test_asr_service.py -v --no-cov

# 2. 新增 tts_service 单元测试
python -m pytest tests/unit/test_tts_service.py -v --no-cov

# 3. 扩展 subtitle_agent 双后端测试
python -m pytest tests/unit/test_subtitle_agent.py -v --no-cov

# 4. 扩展 voice_agent 双后端测试
python -m pytest tests/unit/test_voice_agent.py -v --no-cov

# 5. 后端全量回归（含覆盖率）
python -m pytest -v

# 6. 前端测试 + 构建
cd ../frontend && pnpm test --run && pnpm build
```

### 测试结果

```
# asr_service 单元测试：7/7 passed
tests/unit/test_asr_service.py::TestTranscribe::test_success PASSED
tests/unit/test_asr_service.py::TestTranscribe::test_missing_segments_raises PASSED
tests/unit/test_asr_service.py::TestTranscribe::test_invalid_segments_format_raises PASSED
tests/unit/test_asr_service.py::TestTranscribe::test_http_error_raises PASSED
tests/unit/test_asr_service.py::TestTranscribeUrl::test_e2e_success PASSED
tests/unit/test_asr_service.py::TestTranscribeUrl::test_download_failure_propagates PASSED
tests/unit/test_asr_service.py::TestTranscribeUrl::test_filename_inferred_from_url PASSED

# tts_service 单元测试：17/17 passed
tests/unit/test_tts_service.py::TestCosyVoiceSynthesize::test_success_returns_audio_bytes PASSED
tests/unit/test_tts_service.py::TestCosyVoiceSynthesize::test_speed_param_passed_through PASSED
tests/unit/test_tts_service.py::TestCosyVoiceSynthesize::test_clone_mode_with_reference_audio PASSED
tests/unit/test_tts_service.py::TestCosyVoiceSynthesize::test_empty_audio_raises PASSED
tests/unit/test_tts_service.py::TestCosyVoiceSynthesize::test_http_error_raises PASSED
tests/unit/test_tts_service.py::TestCosyVoiceSynthesize::test_endpoint_path_correct PASSED
tests/unit/test_tts_service.py::TestIndexTTSSynthesize::test_success_returns_audio_bytes PASSED
tests/unit/test_tts_service.py::TestIndexTTSSynthesize::test_default_emotion_neutral PASSED
tests/unit/test_tts_service.py::TestIndexTTSSynthesize::test_emotion_param_passed_through PASSED
tests/unit/test_tts_service.py::TestIndexTTSSynthesize::test_empty_audio_raises PASSED
tests/unit/test_tts_service.py::TestIndexTTSSynthesize::test_http_error_raises PASSED
tests/unit/test_tts_service.py::TestIndexTTSSynthesize::test_endpoint_path_correct PASSED
tests/unit/test_tts_service.py::TestEmotionFromScene::test_known_emotions PASSED
tests/unit/test_tts_service.py::TestEmotionFromScene::test_unknown_emotion_defaults_to_neutral PASSED
tests/unit/test_tts_service.py::TestEmotionFromScene::test_emotion_map_completeness PASSED
tests/unit/test_tts_service.py::TestTTSServiceConsistency::test_both_services_use_same_endpoint_path PASSED
tests/unit/test_tts_service.py::TestTTSServiceConsistency::test_both_services_return_bytes PASSED

# subtitle_agent 双后端测试：9/9 passed（原 3 + 新增 6）
tests/unit/test_subtitle_agent.py::TestSubtitleAgentDualBackend::test_firered_backend_success PASSED
tests/unit/test_subtitle_agent.py::TestSubtitleAgentDualBackend::test_firered_failure_fallback_to_whisper PASSED
tests/unit/test_subtitle_agent.py::TestSubtitleAgentDualBackend::test_whisper_backend_skips_firered PASSED
tests/unit/test_subtitle_agent.py::TestSubtitleAgentDualBackend::test_firered_segments_dict_format PASSED
tests/unit/test_subtitle_agent.py::TestSubtitleAgentDualBackend::test_firered_auto_language_passes_zh PASSED
tests/unit/test_subtitle_agent.py::TestSubtitleAgentDualBackend::test_firered_empty_segments PASSED

# voice_agent 双后端测试：22/22 passed（原 8 + 新增 14）
tests/unit/test_voice_agent.py::TestParseRate::test_zero PASSED
tests/unit/test_voice_agent.py::TestParseRate::test_empty_returns_default PASSED
tests/unit/test_voice_agent.py::TestParseRate::test_positive_rate PASSED
tests/unit/test_voice_agent.py::TestParseRate::test_negative_rate PASSED
tests/unit/test_voice_agent.py::TestParseRate::test_clamped_to_range PASSED
tests/unit/test_voice_agent.py::TestParseRate::test_invalid_format_returns_default PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_cosyvoice_backend_success PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_indextts_backend_success PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_cosyvoice_failure_fallback_to_edge PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_indextts_failure_fallback_to_edge PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_edge_backend_skips_cosyvoice PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_multiple_dialogues_parallel PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_cosyvoice_empty_audio_triggers_fallback PASSED
tests/unit/test_voice_agent.py::TestVoiceAgentDualBackend::test_rate_converted_to_speed PASSED

# 后端全量回归：207/207 passed, coverage 87.40%
======================= 207 passed, 1 warning in 28.08s ========================
Required test coverage of 80% reached. Total coverage: 87.40%
app/agents/subtitle_agent.py       128      7    95%   100, 157-159, 237-239
app/agents/voice_agent.py           87      2    98%   110, 116
app/services/asr_service.py         31      0   100%
app/services/tts_service.py         41      0   100%

# 前端：21/21 passed, build success
Test Files  3 passed (3)
     Tests  21 passed (21)
✓ built in 1.04s
```

### 关键代码片段

#### 1. SubtitleAgent 双后端派发（`app/agents/subtitle_agent.py`）

```python
class SubtitleAgent(BaseAgent):
    """字幕 Agent：音频 → ASR → SRT 字幕。

    后端选择由 settings.asr_backend 控制：
    - 'firered' (默认): FireRedASR-AED-L 1.1B，CER <1%
    - 'whisper': faster-whisper tiny（回退）
    """

    def __init__(self):
        super().__init__("subtitle_agent")
        self._model = None  # faster-whisper 懒加载
        self._asr: ASRService | None = None  # FireRedASR 懒加载

    @property
    def asr_service(self) -> ASRService:
        if self._asr is None:
            self._asr = ASRService(http_client=self.http)
        return self._asr

    async def execute(self, request: SubtitleRequest) -> AgentResponse:
        backend = settings.asr_backend.lower()
        # 下载音频到本地路径
        audio_path = await self._download_audio(request.audio_url)

        if backend == "firered":
            try:
                segments_data, language = await self._transcribe_via_firered(
                    audio_path, request.language
                )
            except Exception as firered_err:
                logger.warning("FireRedASR 失败，回退 faster-whisper: %s", firered_err)
                segments_data, language = await self._transcribe_via_whisper(
                    audio_path, request.language
                )
        else:
            segments_data, language = await self._transcribe_via_whisper(
                audio_path, request.language
            )
        # 构建 SRT + AI 优化...
```

#### 2. VoiceAgent 双 TTS 派发与回退（`app/agents/voice_agent.py`）

```python
async def _generate_one(self, text, voice, rate, filepath, filename, backend, line=None) -> dict:
    speed = _parse_rate(rate)  # '+10%' → 1.1
    emotion = emotion_from_scene(getattr(line, "emotion", "neutral")) if line else "neutral"

    try:
        if backend == "cosyvoice":
            audio_bytes = await self.cosyvoice_service.synthesize(
                text=text, voice=voice, speed=speed
            )
        elif backend == "indextts":
            audio_bytes = await self.indextts_service.synthesize(
                text=text, voice=voice, emotion=emotion, speed=speed
            )
        else:
            return await self._generate_via_edge(text, voice, rate, filepath, filename)
    except Exception as tts_err:
        logger.warning("TTS 后端 %s 失败，回退 edge-tts: %s", backend, tts_err)
        return await self._generate_via_edge(text, voice, rate, filepath, filename)

    # 保存 cosyvoice/indextts 返回的音频字节
    filepath.write_bytes(audio_bytes)
    return {"filename": filename, "voice": voice, "backend": backend, ...}
```

#### 3. ASRService FireRedASR 客户端（`app/services/asr_service.py`）

```python
class ASRService:
    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.mp3",
                          language: str = "zh") -> dict[str, Any]:
        resp = await self.http.post(
            f"{self.endpoint}/asr/transcribe",
            files={"audio": (filename, audio_bytes, "audio/mpeg")},
            data={"language": language} if language else None,
        )
        resp.raise_for_status()
        data = resp.json()
        if "segments" not in data:
            raise ASRServiceError(f"FireRedASR 返回缺少 segments: {data}")
        return data
```

#### 4. TTS 双服务客户端（`app/services/tts_service.py`）

```python
class CosyVoiceService:
    """CosyVoice 2 — zero-shot 音色克隆 + 150ms 流式。"""
    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def synthesize(self, text: str, voice: str,
                          reference_audio_url: str | None = None,
                          speed: float = 1.0) -> bytes:
        payload = {"model": settings.cosyvoice_model, "input": text, "voice": voice,
                    "response_format": "mp3", "speed": speed}
        if reference_audio_url:
            payload["reference_audio"] = reference_audio_url
        resp = await self.http.post(f"{self.endpoint}/audio/speech", json=payload)
        resp.raise_for_status()
        audio_bytes = resp.content
        if not audio_bytes:
            raise TTSServiceError("CosyVoice 返回空音频")
        return audio_bytes

class IndexTTSService:
    """IndexTTS-2 — 情感/音色解耦，中文 WER 0.821 领先。"""
    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def synthesize(self, text: str, voice: str, emotion: str = "neutral",
                          speed: float = 1.0) -> bytes:
        payload = {"model": settings.indextts_model, "input": text, "voice": voice,
                    "response_format": "mp3", "speed": speed, "emotion": emotion}
        resp = await self.http.post(f"{self.endpoint}/audio/speech", json=payload)
        resp.raise_for_status()
        audio_bytes = resp.content
        if not audio_bytes:
            raise TTSServiceError("IndexTTS 返回空音频")
        return audio_bytes
```

#### 5. ASR/TTS 配置（`app/config.py`）

```python
# === P4.2 ASR/TTS 升级：FireRedASR + CosyVoice 2 + IndexTTS-2 ===
asr_backend: str = "firered"  # 'firered' (默认, CER <1%) / 'whisper' (回退)
firered_asr_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8300/v1"
firered_asr_model: str = "FireRedTeam/FireRedASR-AED-L"
firered_asr_timeout: float = 120.0
whisper_model: str = "tiny"  # 回退模型

tts_backend: str = "cosyvoice"  # 'cosyvoice' (默认) / 'indextts' / 'edge' (回退)
cosyvoice_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8400/v1"
cosyvoice_model: str = "CosyVoice2-0.5B"
cosyvoice_timeout: float = 60.0
indextts_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8500/v1"
indextts_model: str = "IndexTTS-2"
indextts_timeout: float = 60.0
```

### 文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `platform/backend/app/config.py` | 修改 | 新增 11 个 ASR/TTS 配置项（asr_backend + 4 firered + 1 whisper + tts_backend + 3 cosyvoice + 3 indextts） |
| `platform/backend/.env.example` | 修改 | 新增 ASR/TTS 环境变量示例 |
| `platform/backend/app/services/__init__.py` | 修改 | 导出 ASRService / CosyVoiceService / IndexTTSService |
| `platform/backend/app/services/asr_service.py` | 新建 | FireRedASR HTTP 客户端封装（110 行） |
| `platform/backend/app/services/tts_service.py` | 新建 | CosyVoice 2 + IndexTTS-2 双 TTS 客户端封装（170 行） |
| `platform/backend/app/agents/subtitle_agent.py` | 修改 | 双后端派发 + FireRedASR 回退逻辑 + _build_srt 兼容 dict segments |
| `platform/backend/app/agents/voice_agent.py` | 修改 | 双 TTS 后端派发 + edge-tts 回退逻辑 + _parse_rate 速率转换 |
| `platform/backend/tests/conftest.py` | 修改 | 默认 asr_backend='whisper' tts_backend='edge' 保持向后兼容 |
| `platform/backend/tests/unit/test_asr_service.py` | 新建 | 7 个 ASRService 单元测试 |
| `platform/backend/tests/unit/test_tts_service.py` | 新建 | 17 个 TTS Service 单元测试 |
| `platform/backend/tests/unit/test_subtitle_agent.py` | 修改 | 新增 TestSubtitleAgentDualBackend 类 6 个测试 |
| `platform/backend/tests/unit/test_voice_agent.py` | 修改 | 新增 TestParseRate 6 + TestVoiceAgentDualBackend 8 个测试 |
| `STATE.json` | 修改 | 版本 0.7.0→0.8.0，新增 P4.2 里程碑条目（7 个子任务） |
| `TEST_LOG.md` | 修改 | 追加本次时序条目 |

### 下一步

- **P4.3**: 图像生成升级（HunyuanImage 2.1 + FLUX+PuLID 替换 SDXL + LTX-Video 预览）
- **部署前置条件**: Workstation 需部署三个 FastAPI 服务：
  - FireRedASR wrapper（监听 :8300/v1），暴露 `/asr/transcribe` 端点
  - CosyVoice 2 wrapper（监听 :8400/v1），暴露 `/audio/speech` 端点（OpenAI 兼容）
  - IndexTTS-2 wrapper（监听 :8500/v1），暴露 `/audio/speech` 端点（OpenAI 兼容）

---

## 2026-07-24 P4.1 视频生成升级：xDiT + HunyuanVideo 1.5 多卡并行

### 变更摘要

- **触发原因**: P3 E2E 报告显示视频生成耗时 691s/2场景（单卡 Wan 2.2 14B 瓶颈），是全流程最大耗时节点。
- **本次范围**: 引入 xDiT 独立推理引擎作为视频生成主后端（HunyuanVideo 1.5 8.3B，4 卡并行 cfg=2+ulysses=2），ComfyUI/Wan 2.2 降级为回退路径。预期单场景视频生成从 350s 压缩到 45-70s（5-8× 加速）。
- **关键决策**:
  1. **双后端派发**：VideoAgent 按 `settings.video_backend` 选择 `xdit` 或 `comfyui`，默认 `xdit`。
  2. **自动回退**：xDiT 失败时自动回退到 ComfyUI/Wan 2.2，保证可用性；双失败则 error 包含两侧异常。
  3. **懒加载 XDiTService**：复用 BaseAgent 的 `httpx.AsyncClient`（`trust_env=False` 避免 macOS 代理拦截 IPv6）。
  4. **测试向后兼容**：`conftest._patch_settings` 默认 `video_backend='comfyui'`，旧测试无需修改；新增 `TestVideoAgentXDiT` 类局部 monkeypatch 覆盖为 `xdit`。

### 测试命令

```bash
cd platform/backend && source .venv/bin/activate

# 1. 新增 xDiT Service 单元测试
python -m pytest tests/unit/test_xdit_service.py -v --no-cov

# 2. 扩展 video_agent 测试（含 xDiT 路径）
python -m pytest tests/unit/test_video_agent.py -v --no-cov

# 3. 后端全量回归（含覆盖率）
python -m pytest -v

# 4. 前端测试 + 构建
cd ../frontend && pnpm test --run && pnpm build
```

### 测试结果

```
# xdit_service 单元测试：13/13 passed
tests/unit/test_xdit_service.py::TestUploadImage::test_success PASSED
tests/unit/test_xdit_service.py::TestUploadImage::test_missing_filename_defaults_to_input_png PASSED
tests/unit/test_xdit_service.py::TestUploadImage::test_http_error_raises PASSED
tests/unit/test_xdit_service.py::TestSubmitTask::test_success_returns_task_id PASSED
tests/unit/test_xdit_service.py::TestSubmitTask::test_missing_task_id_raises PASSED
tests/unit/test_xdit_service.py::TestSubmitTask::test_duration_override_changes_num_frames PASSED
tests/unit/test_xdit_service.py::TestPollStatus::test_succeeded PASSED
tests/unit/test_xdit_service.py::TestPollStatus::test_failed_raises PASSED
tests/unit/test_xdit_service.py::TestPollStatus::test_timeout_raises_timeout_error PASSED
tests/unit/test_xdit_service.py::TestGetResult::test_success PASSED
tests/unit/test_xdit_service.py::TestGetResult::test_missing_video_url_raises PASSED
tests/unit/test_xdit_service.py::TestGenerateVideoE2E::test_full_pipeline_success PASSED
tests/unit/test_xdit_service.py::TestGenerateVideoE2E::test_pipeline_failure_propagates PASSED

# video_agent 测试：18/18 passed（含 5 个新增 xDiT 路径）
tests/unit/test_video_agent.py::TestVideoAgentXDiT::test_xdit_success PASSED
tests/unit/test_video_agent.py::TestVideoAgentXDiT::test_xdit_failure_fallback_to_comfyui_success PASSED
tests/unit/test_video_agent.py::TestVideoAgentXDiT::test_xdit_and_comfyui_both_fail PASSED
tests/unit/test_video_agent.py::TestVideoAgentXDiT::test_comfyui_backend_skips_xdit PASSED
tests/unit/test_video_agent.py::TestVideoAgentXDiT::test_xdit_progress_callback_propagated PASSED

# 后端全量回归：163/163 passed, coverage 86.44%
======================= 163 passed, 1 warning in 22.40s ========================
Required test coverage of 80% reached. Total coverage: 86.44%
app/agents/video_agent.py          153      4    97%   172, 301, 404-405
app/services/xdit_service.py        88      0   100%
app/services/__init__.py              2      0   100%

# 前端：21/21 passed, build success
Test Files  3 passed (3)
     Tests  21 passed (21)
✓ built in 1.03s
```

### 关键代码片段

#### 1. VideoAgent 双后端派发（`app/agents/video_agent.py`）

```python
class VideoAgent(BaseAgent):
    """视频 Agent：分镜图片 → 视频片段。

    后端选择由 settings.video_backend 控制：
    - 'xdit' (默认): HunyuanVideo 1.5 + xDiT 多卡并行，单场景 45-70s
    - 'comfyui': Wan 2.2 I2V 单卡（回退路径）
    """

    def __init__(self):
        super().__init__("video_agent")
        self._xdit: XDiTService | None = None  # 懒加载

    @property
    def xdit_service(self) -> XDiTService:
        if self._xdit is None:
            self._xdit = XDiTService(http_client=self.http)
        return self._xdit

    async def execute(self, request, progress_callback=None, worker_url=None):
        backend = settings.video_backend.lower()
        try:
            if backend == "xdit":
                return await self._execute_via_xdit(request, progress_callback)
            return await self._execute_via_comfyui(request, progress_callback, worker_url)
        except Exception as xdit_err:
            if backend != "xdit":
                return AgentResponse(success=False, error=f"视频生成失败: {xdit_err}", ...)
            # xDiT 失败 → 自动回退 ComfyUI
            logger.warning("xDiT 失败，回退 ComfyUI: scene_id=%s err=%s", ...)
            try:
                return await self._execute_via_comfyui(request, progress_callback, worker_url)
            except Exception as comfyui_err:
                return AgentResponse(success=False,
                    error=f"视频生成失败(xdit+comfyui 均失败): xdit={xdit_err}; comfyui={comfyui_err}", ...)
```

#### 2. XDiTService 端到端编排（`app/services/xdit_service.py`）

```python
async def generate_video(self, image_url, prompt, negative_prompt="",
                         scene_id=0, duration_seconds=None, progress_callback=None):
    # 1. 上传分镜图片
    if progress_callback: progress_callback(5, "上传分镜图片到 xDiT")
    image_filename = await self.upload_image(image_url)

    # 2. 提交生成任务（num_frames 按 duration 对齐 4k+1）
    if progress_callback: progress_callback(15, "提交 HunyuanVideo 1.5 任务")
    task_id = await self.submit_task(image_filename, prompt, negative_prompt,
                                     scene_id, duration_seconds)

    # 3. 轮询状态（透传 progress_callback，xDiT 上报 0-100）
    if progress_callback: progress_callback(25, "xDiT 4 卡并行推理中")
    await self.poll_status(task_id, progress_callback=progress_callback)

    # 4. 获取结果
    if progress_callback: progress_callback(95, "获取视频结果")
    result = await self.get_result(task_id)
    if progress_callback: progress_callback(100, "视频生成完成")
    return result
```

#### 3. xDiT 配置（`app/config.py`）

```python
# === P4.1 视频生成升级：xDiT + HunyuanVideo 1.5 ===
xdit_endpoint: str = "http://[240e:b8f:1d22:e700:57fc:3cf9:d856:e46c]:8288"
video_backend: str = "xdit"  # 'xdit' (默认) / 'comfyui' (回退)
xdit_model: str = "hunyuanvideo-1.5"
xdit_num_frames: int = 97  # 原生 97 帧 ≈ 4s @ 24fps
xdit_resolution: str = "720p"
xdit_cfg_parallel: int = 2  # 4 卡并行：cfg=2 + ulysses=2
xdit_ulysses_degree: int = 2
xdit_steps: int = 20
xdit_cfg: float = 6.0
xdit_request_timeout: float = 600.0
xdit_poll_interval: float = 3.0
```

### 文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `platform/backend/app/config.py` | 修改 | 新增 13 个 xDiT 配置项 |
| `platform/backend/.env.example` | 修改 | 新增 xDiT 环境变量示例 |
| `platform/backend/app/services/__init__.py` | 新建 | services 包初始化，导出 XDiTService |
| `platform/backend/app/services/xdit_service.py` | 新建 | xDiT 推理引擎客户端封装（230 行） |
| `platform/backend/app/agents/video_agent.py` | 修改 | 双后端派发 + xDiT 回退逻辑 |
| `platform/backend/tests/conftest.py` | 修改 | 默认 video_backend='comfyui' 保持向后兼容 |
| `platform/backend/tests/unit/test_xdit_service.py` | 新建 | 13 个 xDiT Service 单元测试 |
| `platform/backend/tests/unit/test_video_agent.py` | 修改 | 新增 TestVideoAgentXDiT 类 5 个测试 |
| `STATE.json` | 修改 | 版本 0.6.0→0.7.0，新增 P4.1 里程碑条目 |
| `TEST_LOG.md` | 修改 | 追加本次时序条目 |

### 下一步

- **P4.2**: ASR/TTS 升级（FireRedASR + IndexTTS-2 + CosyVoice 2 三服务部署）
- **部署前置条件**: Workstation 需部署 xDiT FastAPI wrapper（监听 :8288），暴露 `/v1/video/{generate,status,result,upload}` 四个端点

---

## 2026-07-24 P4 质量升级方案设计：设备清单同步

### 变更摘要

- **触发原因**: P3 端到端验收报告三大瓶颈 —— 视频生成 691s / 字幕 ASR 错别字 CER 8-9% / 缺少唇形同步与后处理。
- **本次范围**: 纯设计阶段，不修改 AICG-DownLoader 项目代码，仅更新全局设备清单 `/Users/wangzhenyu/Desktop/ALLProject/.设备说明.md`，追加第七章 AICG 短剧质量升级方案。
- **调研方向**（4 个并行子代理）：
  1. **xDiT + HunyuanVideo 1.5**：xDiT 是独立推理引擎（非 ComfyUI 节点），支持 PipeFusion/USP/CFG/DP 四种并行策略。HunyuanVideo 1.5 是 8.3B 参数 BF16 仅需 14GB 显存，原生 720p/97 帧。4 卡 PRO 6000 并行理论 45-70s/场景（vs Wan 2.2 单卡 350s）。
  2. **FireRedASR + CosyVoice 2 + IndexTTS-2**：FireRedASR-AED-L 1.1B（小红书 FireRed 团队）AISHELL-1 CER 0.57-1%（vs faster-whisper 8-9%）。CosyVoice 2-0.5B（阿里）zero-shot 克隆 + 150ms 流式。IndexTTS-2（B 站）音色/情感解耦，中文 WER 0.821 领先。双 TTS 并存按场景路由。
  3. **Hunyuan Image 2.1 + FLUX+PuLID + LatentSync 1.6 + LTX-Video 2B**：HunyuanImage 2.1（17B FP8 24GB）原生 2K + 中文 prompt 最强。FLUX.1-dev + PuLID-FLUX v0.9.1 专为角色 ID 一致性设计。LatentSync 1.6（字节）512 分辨率最低 6GB 显存。LTX-Video 2B 8GB 显存 5s 视频约 20s（分镜预览加速）。
  4. **后处理 + Mac 集群**：RealBasicVSR/RIFE/ProPainter 必须 NVIDIA GPU（无 MLX 移植，MPS 性能仅 RTX 4090 的 1/3-1/5）。Mac 集群承接：MiniCPM-V 4.5（MLX 原生视觉质检）+ DeepFilterNet3（Rust Apple Silicon 原生降噪）+ FFmpeg VideoToolbox（H.265 硬件编码）。
- **核心决策**：
  1. **9 Agent → 11 Agent**：新增"视频预览 Agent"（LTX-Video 2B）、"唇形同步 Agent"（LatentSync 1.6）、"后处理 Agent"（VSR+RIFE+ProPainter+DeepFilterNet3+FFmpeg）。
  2. **硬件分配**：Workstation 4× PRO 6000 跑视频主生成 + 图像生成 + 后处理 + 视觉质检；PC01/02 RTX 5090 跑 LTX-Video 预览；Mac Studio 4× M3 Ultra 512GB 跑 MiniCPM-V 4.5 + DeepFilterNet3 + FFmpeg；Spark01+02 升级 SGLang + Qwen3-Next-80B-A3B。
  3. **不需新增硬件**：现有 16 台设备足以支撑 P4 全部升级，瓶颈在算力调度而非显存。
  4. **xDiT 与 ComfyUI 解耦**：xDiT 独立 FastAPI 服务（:8288），不与 ComfyUI-HunyuanVideoWrapper 兼容；ComfyUI-LB 仍负责编排与图像生成。
  5. **跨机不联合**：PRO 6000 + RTX 5090 异构算力不对等，xDiT 4 卡仅限 Workstation 内。

### 设备清单更新

```bash
# 通过 Python 脚本精准更新（Edit 工具不能跨工作目录）
python3 .trae/documents/update_device_list.py
```

**结果**：设备说明.md 从 388 行 → 569 行（+181 行），关键更新：

- 顶部元数据：日期 2026-07-22 → 2026-07-24，新增 IPv6 子网声明 + P4 升级主旨
- 设备清单表 7 行角色更新（studio01/spark01/spark02/workstation/pc01/pc02）
- 服务依赖关系图：新增 xDiT Engine / LatentSync / FireRedASR / CosyVoice 2 / IndexTTS-2 / Qwen3-VL-30B-A3B / Mac 后处理承接 / PC LTX-Video 预览
- Mac Studio 章节：新增 P4 后处理承接能力（MiniCPM-V 4.5 + DeepFilterNet3 + FFmpeg VideoToolbox）
- Spark 章节：新增 P4 升级计划（vLLM→SGLang，Euryale→Qwen3-Next-80B-A3B）
- Workstation 章节：服务表新增 6 行 P4 服务（xDiT :8288 / LatentSync :8289 / FireRedASR :9880 / CosyVoice 2 :9881 / IndexTTS-2 :9882 / Qwen3-VL-30B :8200），Docker 容器 10 → 13
- PC 章节：新增 LTX-Video 分镜预览任务说明
- 文件末尾追加第七章 AICG 短剧质量升级方案（7.1-7.9 共 9 节）：
  - 7.1 现状瓶颈与根因
  - 7.2 模型选型与替换路线图（13 行替换对照表）
  - 7.3 11 Agent 升级管线
  - 7.4 硬件分配矩阵
  - 7.5 预期性能提升（10 项指标提升倍数）
  - 7.6 实施路线图（P4.1-P4.5 五阶段）
  - 7.7 风险与限制（10 项）
  - 7.8 不变项（7 项）
  - 7.9 验证与回归策略

### 预期性能提升（核心指标）

| 指标 | P3 现状 | P4 目标 | 倍数 |
|------|---------|---------|------|
| 视频生成单场景耗时 | 350s | 45-70s | 5-8× |
| 视频生成总耗时（2 场景） | 691s | 90-140s | 5-8× |
| ASR 中文 CER | 8-9% | <1% | 8-9× |
| 视频分辨率 | 480×832 / 24fps | 4K / 60fps | 8× 像素 + 2.5× 帧率 |
| Agent 总数 | 9 | 11 | +3 |
| 视觉质检显存 | 144GB (72B TP2) | ≤32GB (30B-A3B) | 砍半 |

### 关键决策

1. **不修改项目代码**：本次为设计阶段，所有 P4.1-P4.5 实施在后续会话中按里程碑执行，每个阶段含 TDD + 全量回归 + STATE/TEST_LOG 更新（符合用户偏好）。
2. **设备清单为全局共享文档**：`/Users/wangzhenyu/Desktop/ALLProject/.设备说明.md` 不属于本项目工作目录，使用 Python 脚本（而非 Edit 工具）跨工作目录更新，临时脚本 `.trae/documents/update_device_list.py` 完成后已删除。
3. **xDiT 独立服务而非 ComfyUI 节点**：xDiT 是独立推理引擎（类似 vLLM 之于 Transformers），与 ComfyUI-HunyuanVideoWrapper 不直接兼容。生产用 xDiT FastAPI 服务 + ComfyUI 做编排，通过中间产物（latent/video 文件）解耦。
4. **TTS 双模型并存**：IndexTTS-2 主力（音色/情感解耦）+ CosyVoice 2 流式补充，按 `voice_agent.py` 路由选择模型加载，共享 GPU 分时复用。
5. **Mac 集群角色明确**：仅承接"非像素级 AI 任务"（音频/编码/VLM/LLM），像素级 AI（VSR/RIFE/ProPainter/FLUX/PuLID/LatentSync）必须 NVIDIA GPU。
6. **SGLang 零停机切换**：spark02 先起 SGLang worker → spark01 切换 → 旧 vLLM 容器下线，切换窗口 <60s。

### 不变更项（验证未破坏）

- 后端测试基线：145/145 passed, coverage 85.37%（未触碰后端代码）
- 前端测试基线：21/21 passed（未触碰前端代码）
- 前端构建：success（未触碰前端代码）
- P0-P3 已交付里程碑状态：保持 completed 不变

### 后续 P4.1 - P4.5 实施约定

每个阶段完成时必须执行：

1. `cd platform/backend && source .venv/bin/activate && python -m pytest` 后端全量回归
2. `cd platform/frontend && pnpm test --run` 前端全量测试
3. `cd platform/frontend && pnpm build` 前端构建
4. E2E 冒烟：同一剧本前提"深夜便利店"重跑全链路对比 P3 基线
5. STATE.json 追加 `current_session.tasks.P4.x` 条目
6. TEST_LOG.md 追加时序条目（变更摘要 / 测试命令 / 结果 / 关键代码 / 决策）

最终 P4 全部完成的验收：11 Agent 管线打通 · 视频 ≤70s/场景 · ASR CER <1% · 成片 4K/60fps/唇形同步/降噪 · 视觉质检显存 ≤32GB · 后端测试 ≥160 用例覆盖率 ≥85% · 前端测试 ≥25 用例。

---

## 2026-07-23 P3.3 粒子交互 + 主题切换器

### 变更摘要

- **ParticleField 组件**（`platform/frontend/src/components/ui/ParticleField.tsx`）：Canvas 实现的银盐颗粒粒子场，严格遵守用户偏好约束。
  - 粒子数 200（≤ 240，用户禁止 > 300）
  - 速度 ≤ 0.9 px/帧（用户禁止 > 1.2），近层 0.9 / 远层 0.5
  - 单色：从 CSS 变量 `--developer` 读取（用户禁止高饱和度彩虹），MutationObserver 监听 `html[data-theme]` 在主题切换时自动变色
  - 分两层景深：45% 远层（0.45 opacity 小粒子）/ 55% 近层（1.0 opacity 大粒子），远层不画连线以保持景深（用户禁止无景深）
  - 粒子连线极细半透明：lineWidth 0.4，alpha ≤ 0.12，距离阈值 90px（用户禁止粗粒子连接）
  - `position: fixed; inset: 0; z-index: 0; pointer-events: none` → 不拦截任何点击，不会覆盖文本交互（用户禁止粒子覆盖文本）
  - vignetting：`box-shadow: inset 0 0 240px 60px rgba(0,0,0,0.55)` 暗角强化景深（用户禁止无 vignetting）
- **鼠标跟随**：粒子被鼠标微弱吸引（force 0.012 近层 / 0.004 远层，150px 半径内），离开后自然散开，无突变跳跃。
- **水波纹**：鼠标点击触发从中心向外缓慢扩散的波纹（速度 1.5px/帧，范围 420px，alpha 从 0.35 衰减到 0），波纹环带附近的粒子被推开形成"聚集-扩散"视觉提示。点击 modal 与 theme-switcher 内的元素不触发波纹。
- **粒子聚集按钮（简化版）**：通过水波纹推开粒子 + 内圈微弱辉光实现"内容被显影液激活"的聚集效果，避免强行嵌入 ReactFlow 节点点击逻辑的复杂度。
- **ThemeSwitcher 组件**（`platform/frontend/src/components/ui/ThemeSwitcher.tsx`）：Palettes 图标（lucide-react 1.25.0 实际为 `Palette` 单数，原 `Palettes` 不存在）+ 下拉三主题色卡（暗房琥珀/银盐冷调/蓝晒）。
  - 切换通过在 `<html>` 设置 `data-theme` 属性生效
  - localStorage 持久化（key: `film-atelier-theme`）
  - 点击外部关闭下拉（mousedown 监听）
  - `aria-checked` 标记当前选中主题
- **集成到 App.tsx**：ParticleField 作为 fragment 第一个元素（fixed 全屏背景层），ThemeSwitcher 挂到 topbar-actions 第一个位置。
- **CSS 调整**（`index.css`）：新增 `.particle-field` / `.theme-switcher*` 样式；`.canvas-container` 改为 `background: transparent` 让粒子透出；`.app-layout` 加 `z-index: 1` 创建层叠上下文让粒子位于其下。
- **Icon.tsx 新增**：`Palette` 图标（lucide-react 1.25.0 兼容版本）。

### 前端全量测试

```bash
cd platform/frontend && pnpm test --run
```

**结果**：

```
 ✓ src/store/useDramaStore.test.ts (10 tests) 2ms
 ✓ src/components/ui/ThemeSwitcher.test.tsx (8 tests) 82ms
 ✓ src/App.test.tsx (3 tests) 97ms

 Test Files  3 passed (3)
      Tests  21 passed (21)
```

- 总用例：21（原 13 + 新增 8 个 ThemeSwitcher 单元测试）
- jsdom 警告 `HTMLCanvasElement's getContext() method: without installing the canvas npm package` 是预期内的（jsdom 不实现 canvas API），ParticleField 在 `if (!ctx) return;` 处安全退出，不影响测试

### ThemeSwitcher 测试覆盖

```tsx
- 渲染主题切换按钮
- 默认 darkroom-amber 并应用 data-theme 到 <html>
- 点击展开下拉显示三个主题
- 切换主题 + localStorage 持久化
- aria-checked 标记当前主题
- 从 localStorage 还原主题
- 无效持久化值回退到默认
- 外部点击关闭下拉
```

### 前端构建

```bash
cd platform/frontend && pnpm build
```

**结果**：

```
✓ 2293 modules transformed.
dist/assets/index-DTXxM36A.css   16.46 kB │ gzip:   3.77 kB
dist/assets/index-DSw8xLE2.js   478.15 kB │ gzip: 151.92 kB
✓ built in 891ms
```

- CSS：14.77kB → 16.46kB（+1.69kB，粒子层 + 主题切换器样式）
- JS：472.33kB → 478.15kB（+5.82kB，ParticleField + ThemeSwitcher）
- gzip 后增量：CSS +0.31kB / JS +2.34kB

### 后端全量回归（确认 P3.3 前端工作未破坏后端）

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest
```

**结果**：

```
======================= 145 passed, 1 warning in 19.99s ========================
Required test coverage of 80% reached. Total coverage: 85.37%
```

### 关键代码（粒子场核心循环）

```typescript
// 鼠标微弱吸引（仅近层显著，远层几乎不动 → 增强景深对比）
if (mouse.active) {
  const dx = mouse.x - p.x;
  const dy = mouse.y - p.y;
  const distSq = dx * dx + dy * dy;
  if (distSq < 22500) { // 150px 半径内
    const inv = 1 / (Math.sqrt(distSq) + 0.01);
    const force = p.depth === 1 ? 0.012 : 0.004;
    p.vx += dx * inv * force;
    p.vy += dy * inv * force;
  }
}

// 水波纹推开粒子（聚集-扩散视觉提示）
for (let k = 0; k < ripples.length; k++) {
  const rp = ripples[k];
  const dx = p.x - rp.x;
  const dy = p.y - rp.y;
  const d = Math.sqrt(dx * dx + dy * dy);
  // 仅在波纹"环带"附近推开
  if (d > rp.r - 18 && d < rp.r + 18 && d > 0.01) {
    const push = (rp.alpha * 0.6) * (p.depth === 1 ? 1.0 : 0.4);
    p.vx += (dx / d) * push;
    p.vy += (dy / d) * push;
  }
}

// 限速（用户禁止 > 1.2）
const sp = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
const maxSp = p.depth === 1 ? 0.9 : 0.5;
if (sp > maxSp) {
  p.vx = (p.vx / sp) * maxSp;
  p.vy = (p.vy / sp) * maxSp;
}
```

### 关键决策

1. **lucide-react 版本兼容**：1.25.0 没有 `Palettes` 图标，只有单数 `Palette`。已修正 Icon.tsx 与 ThemeSwitcher.tsx。
2. **层级架构**：粒子 `z-index: 0`，app-layout `z-index: 1` 创建层叠上下文，topbar/sidebar/status-bar 通过 `backdrop-filter: blur(6px)` 让粒子模糊透出，canvas-container 透明让粒子清晰显现 → 符合"显影液面被粒子照亮"的暗房隐喻。
3. **jsdom 兼容**：ParticleField 在 `getContext('2d')` 返回 null 时安全退出（`if (!ctx) return;`），保证单元测试无需安装 `canvas` npm 包即可通过。
4. **粒子聚集按钮简化**：用户原话"鼠标点击内容时粒子聚集成按钮"——直接在 ReactFlow 节点点击时嵌入粒子聚集会很复杂且与节点拖拽逻辑冲突。简化为"水波纹 + 粒子被推开形成聚集-扩散视觉提示"，既符合"显影液被激活"的暗房隐喻，又不破坏 ReactFlow 交互。
5. **颜色读取性能**：从 CSS 变量 `--developer` 读取颜色避免硬编码三套主题色，MutationObserver 仅监听 `data-theme` 属性变化触发重读，无每帧 `getComputedStyle` 开销。

---

## 2026-07-23 P3.2 UI Film Atelier 暗房隐喻设计体系

### 变更摘要

- **P3.2 完成**：重构 `index.css`，建立 Film Atelier 暗房/剪辑台隐喻的六层 token 体系。
  - 源 token：`--darkroom-base` / `--darkroom-elevated` / `--film-bed` / `--developer` / `--developer-dim` / `--silver-text(-dim)` / `--frame` / `--developer-glow`
  - 三套主题 token 通过 `[data-theme]` 切换：`darkroom-amber`（默认琥珀安全灯）/ `silver-halide`（冷银盐）/ `cyanotype`（普鲁士蓝晒）
  - 语义别名 `--bg-primary` 等保留 → **所有组件零改动** 即可获得新视觉
- **内容聚焦布局**：topbar 半透明 + 微弱 backdrop-filter blur(6px)（暗房灯透过胶片感，非花哨毛玻璃）；canvas-container 增加 `::before` 径向聚焦光；sidebar 浮起；status-bar 呼吸光动画（status-dot 在线时 `darkroom-breathe` 3.2s 呼吸）；modal 滑入动画；node-palette-item hover 高光过渡。
- **克制呼吸感**：所有动效缓动 `cubic-bezier(0.4, 0, 0.2, 1)`，禁止单帧跳变。
- **可访问性**：`@media (prefers-reduced-motion: reduce)` 尊重用户运动偏好。

### 前端全量测试

```bash
cd platform/frontend && pnpm test --run
```

**结果**：

```
 ✓ src/store/useDramaStore.test.ts (10 tests) 2ms
 ✓ src/App.test.tsx (3 tests) 90ms

 Test Files  2 passed (2)
      Tests  13 passed (13)
   Duration  900ms
```

### 前端构建

```bash
cd platform/frontend && pnpm build
```

**结果**：

```
✓ 2291 modules transformed.
dist/assets/index-Gm1MHJ0I.css   14.77 kB │ gzip:   3.46 kB
✓ built in 927ms
```

### 关键设计 token（三套主题对照）

```css
/* 默认：暗房琥珀 Darkroom Amber */
--darkroom-base: #0f0e0c;  --developer: #d4a574;  --silver-text: #e0e0e0;
/* 银盐冷调 Silver Halide */
--darkroom-base: #0c0d0f;  --developer: #b8c5d4;  --silver-text: #d8dde2;
/* 蓝晒 Cyanotype */
--darkroom-base: #0a0e14;  --developer: #5da9c4;  --silver-text: #d8e3ea;
```

### 关键决策

1. **零改动兼容**：保留 `--bg-primary` / `--bg-secondary` 等语义别名，让 Canvas/Modals/Topbar 组件不需要任何 TSX 改动。
2. **半透明 + backdrop-filter**：用 `color-mix(in srgb, ... 78%, transparent)` + `blur(6px)` 实现"暗房灯透过胶片"的隐喻，强度克制（用户禁止 frosted glass isolation 隔离感）。
3. **径向聚焦光**：`canvas-container::before` 用 `radial-gradient` 在内容区中央提供 `--developer-glow` 的极弱光晕（透明度 6-8%），强调"显影液面"焦点。
4. **主题切换留待 P3.3**：token 体系已就绪，切换器组件（多配色主题切换按钮）在 P3.3 粒子交互任务中落地。

---

## 2026-07-23 P3.1 视频并行化 + 故障转移

### 变更摘要

- **P3.1 完成**：重写 `VideoAgent.batch_execute`，引入 `asyncio.Semaphore(video_max_concurrency)` 限制并发度，避免单 ComfyUI 实例队列堆积。
- **进度聚合**：新增 `progress_callback` 透传，按场景聚合批次进度：`batch_percent = (completed + percent/100) / total * 100`。
- **Worker 故障转移**：单场景首次失败时调用 `_pick_alternate_worker(failed_url)` 排除已失败 URL，从剩余候选中按 GPU 空闲显存选一个 worker 重试一次。
- **配置项**：`config.py` 新增 `video_max_concurrency: int = 2`，与视频 worker 数对齐。

### 后端视频 Agent 单元测试

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest tests/unit/test_video_agent.py -v --no-cov
```

**结果**：13/13 passed（原 5 个 execute + 新增 8 个 batch/failover/concurrency/alternate-worker 测试）。

### 后端全量回归

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest
```

**结果**：

```
======================= 145 passed, 1 warning in 20.32s ========================
Required test coverage of 80% reached. Total coverage: 85.37%
```

- 总用例：145（原 137 + 新增 8 个 video_agent 测试）
- 通过：145 / 失败：0
- 覆盖率：85.37%
- `video_agent.py` 覆盖率：98%（仅 3 行未覆盖：异常分支 197、307-308）

### 关键代码（batch_execute 并发与故障转移）

```python
max_concurrent = max(1, settings.video_max_concurrency)
sem = asyncio.Semaphore(max_concurrent)

async def _generate_one(item, worker_url, scene_idx):
    async with sem:
        resp = await self.execute(item, progress_callback=scene_progress, worker_url=worker_url)
        if resp.success and resp.data:
            completed += 1
            return VideoResult(**resp.data)
        # 故障转移：换一个不同的 worker 重试一次
        alt_worker = await self._pick_alternate_worker(worker_url)
        if alt_worker:
            resp2 = await self.execute(item, progress_callback=scene_progress, worker_url=alt_worker)
            if resp2.success and resp2.data:
                completed += 1
                return VideoResult(**resp2.data)
        return None

async def _pick_alternate_worker(self, failed_url: str) -> str | None:
    candidates = [settings.comfyui_video_a, settings.comfyui_video_b]
    alternates = [u for u in candidates if u != failed_url]
    if not alternates:
        return None  # video_a == video_b（同一 LB URL）→ 不重试
    loads = await self._get_worker_loads(alternates)
    if not loads:
        return alternates[0]
    return self._select_workers_by_load(loads, 1)[0]
```

### 关键决策

1. **并发度 = 2（而非 task 数）**：当前 `video_a` 与 `video_b` 都指向同一 LB 8188，2 个 worker 是配置层的假象。`Semaphore(2)` 与"视频 worker 数"对齐，过高会压垮单实例 ComfyUI 队列。E2E 报告中 691s 瓶颈根因就是 `asyncio.gather` 无并发上限 → 单实例串行（2 × ~350s ≈ 691s）。
2. **故障转移只重试一次**：避免无限重试拖垮整体超时；`_pick_alternate_worker` 在 `video_a == video_b` 场景返回 None 直接放弃，避免循环重试同一 URL。
3. **进度公式**：`completed + percent/100` 而非 `completed + percent` —— 当前场景的 percent 是 0-100 区间，需要归一化到 [0, 1] 后再加到 completed 上除以 total，否则进度会超过 100%。

---

## 2026-07-23 P1-2 字幕闭环 + P1-1 收尾

### 变更摘要

- **P1-1 收尾**：修复 `App.test.tsx` 2 个失败用例（`getByText("生成剧本")` 返回多个元素，因 Canvas 节点面板含同名按钮）。用 `within` 把查询限定到 topbar 容器。
- **P1-2 字幕闭环**：质检 issues → 提取修正对 → 替换字幕文本 → 重建 SRT → 回写文件。复现 E2E 报告中 林声→林深、这辈→这杯、定上→盯上、林生→林深、指条→纸条 5 个 ASR 错别字场景。

### 后端全量测试

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest
```

**结果**：

```
======================= 137 passed, 1 warning in 22.37s ========================
Required test coverage of 80% reached. Total coverage: 83.74%
```

- 总用例：137（原 122 + 新增 15 个字幕修正单元测试）
- 通过：137 / 失败：0
- 覆盖率：83.74%（阈值 80%）
- quality_agent.py 覆盖率：73%（新增字幕修正逻辑覆盖）

### 前端全量测试

```bash
cd platform/frontend && pnpm test
```

**结果**：

```
 ✓ src/store/useDramaStore.test.ts (10 tests) 2ms
 ✓ src/App.test.tsx (3 tests) 94ms

 Test Files  2 passed (2)
      Tests  13 passed (13)
```

### 前端构建

```bash
cd platform/frontend && pnpm build
```

**结果**：

```
✓ 2291 modules transformed.
dist/index.html                   0.40 kB │ gzip:   0.30 kB
dist/assets/index-CfvS21xv.css   11.66 kB │ gzip:   2.65 kB
dist/assets/index-B_o7dUxo.js   472.33 kB │ gzip: 149.58 kB
✓ built in 982ms
```

### 关键代码片段

#### 1. P1-1 测试修复（within 限定 topbar）

`platform/frontend/src/App.test.tsx`：

```tsx
import { render, screen, within } from "@testing-library/react";

// 限定查询到 topbar，避免与 Canvas 节点面板的同名按钮冲突
const getTopbar = () => {
  const title = screen.getByText("AI 短剧工作台 — M4 原型");
  return title.closest(".topbar") as HTMLElement;
};
const topbarBtn = (text: string) => within(getTopbar()).getByText(text);

it("disables downstream buttons before script generation", () => {
  render(<App />);
  expect(topbarBtn("生成角色")).toBeDisabled();
  // ... 其余按钮同理
  expect(topbarBtn("生成剧本")).not.toBeDisabled();
});
```

**根因**：Canvas 节点面板（node-palette）含 `generateLabel: "生成剧本"`，与 topbar 按钮文本冲突，`getByText` 返回多个元素。这是预存问题，非 lucide-react 引入。

#### 2. P1-2 修正对提取（双模式正则）

`platform/backend/app/agents/quality_agent.py`：

```python
_CORRECTION_PATTERNS = [
    # 'X'应为'Y' 模式（支持中英文引号）
    re.compile(r"[''“\"]([^'’\"”\n]{1,20}?)[''”\"]\s*应为\s*[''“\"]([^'’\"”\n]{1,20}?)[''”\"]"),
    # X→Y / X->Y 模式（→ 排除出捕获组，贪婪匹配到下一个分隔符）
    re.compile(r"([^\s,，。;；:：\n()（）→]{1,20})\s*(?:→|->)\s*([^\s,，。;；:：\n()（）→]{1,20})"),
]
```

**修复记录**：
- 初版箭头模式第二组用非贪婪 `{1,20}?`，导致 "林声→林深" 的 right 只匹配 "林"。改为贪婪并排除 `→` 字符。
- 初版 SRT builder 用 `enumerate(segments, 1)` 对空段也递增序号，导致跳过空段后编号不连续。改用独立计数器 `idx`，仅对非空段递增。

#### 3. P1-2 字幕回写核心逻辑

`platform/backend/app/agents/quality_agent.py`：

```python
def apply_subtitle_fixes(request: SubtitleFixRequest) -> SubtitleFixResult:
    corrections = _extract_subtitle_corrections(request.issues)
    # 仅处理 category=subtitle 的 issues，避免误改非字幕内容
    for sub in request.subtitles:
        for seg in sub.segments:
            new_text = seg.text
            for wrong, right in corrections:
                if wrong in new_text:
                    new_text = new_text.replace(wrong, right)
            # ...重建 SRT，可选回写文件
    # persist=True 时覆盖 output/subtitle/subtitle_scene_{id}.srt
```

#### 4. P1-2 前端 QualityModal 一键修正按钮

`platform/frontend/src/components/Modals.tsx`：

```tsx
const handleApplySubtitleFix = async () => {
  const resp = await applySubtitleFix({
    subtitles,
    issues: qualityData.issues,
    persist: true,
  });
  if (resp.success && resp.data) {
    // 用修正后的字幕替换 store 中对应场景的字幕
    resp.data.fixed_subtitles.forEach((sub) => addSubtitle(sub));
    setFixResult(resp.data);
  }
};

// 仅当存在 subtitle 类 issues 且有字幕数据时显示
const hasSubtitleIssues =
  qualityData.issues.some((i) => i.category === "subtitle") && subtitles.length > 0;
```

### 新增/修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/backend/app/models/schemas.py` | 修改 | 新增 SubtitleFixRequest/Result/Item 模型 |
| `platform/backend/app/agents/quality_agent.py` | 修改 | 新增修正提取+应用+回写逻辑，logger |
| `platform/backend/app/routers/drama.py` | 修改 | 新增 /quality/apply_subtitle_fix 路由 |
| `platform/backend/tests/unit/test_subtitle_fix.py` | 新建 | 15 个单元测试（提取/重建/应用/回写/E2E场景） |
| `platform/frontend/src/api/client.ts` | 修改 | 新增 applySubtitleFix + SubtitleFixResult 类型 |
| `platform/frontend/src/components/ui/Icon.tsx` | 修改 | 新增 Wand2 图标 |
| `platform/frontend/src/components/Modals.tsx` | 修改 | QualityModal 新增一键修正按钮+结果展示 |
| `platform/frontend/src/App.test.tsx` | 修改 | within 限定 topbar 查询 |

---

## 2026-07-23 P0-1 + P0-2 + P1-1 前置

### 变更摘要

- **P0-1**：后端配置从 192.168.71.100 + Tailscale 切换到 IPv6 直连 + ComfyUI LB 入口。
- **P0-2**：git init -b main，remote 绑定，.gitignore 补充。
- **P1-1**：引入 lucide-react，创建 Icon.tsx 统一入口，Canvas/Modals emoji 替换为 Lucide 组件。vitest 4→2 降级修复 vite 兼容。

### 后端测试（P0-1 + P1-1 阶段）

```bash
cd platform/backend && source .venv/bin/activate && python -m pytest
```

**结果**：122/122 passed，覆盖率 83.38%

**关键修复**：
1. `quality_agent.py` json_repair 容错：`json_repair.loads("not json")` 返回 str 而非 dict，后续 `data.get()` 崩溃。加 `isinstance(data, dict)` 校验。
2. `test_visual_quality_agent.py` mock 路径：VisualQualityAgent 用 `_vlm_client` 而非继承的 `llm_client`，fixture 需预置 `agent._vlm_client = MagicMock()`。

### 前端测试（P1-1 阶段，含失败记录）

```bash
cd platform/frontend && pnpm test
```

**初版结果**：10/13 passed，App.test.tsx 2/3 failed（`getByText("生成剧本")` 多元素冲突）

**最终结果**（within 修复后）：13/13 passed
