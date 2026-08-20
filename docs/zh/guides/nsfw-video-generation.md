# NSFW 视频生成教学（Wan 2.2 / MiniMax H3 双路线）

> 适用环境：DashBox 本地框架 + 集群 ComfyUI 后端
> 前置条件：设置 → 模型库 → 完成 R18 确认（NSFW 条目才会显示）
> 素材来源：Civitai 8 个热门 NSFW 视频作品的生成参数逆向（2026-08-17）
> 工作流依据：本集群实跑验证过的 ComfyUI 模板（Wan 2.2 I2V / MiniMax H3 fl2va+ref2va）
> 资源下载：**22 项基准全部落地 ✅**（2026-08-17 三端 object_info 核验通过）；另经 civitai.red 调研扩充 **48 件**（Wan 44 / H3 4 + 修复 2），见第六节

---

## 一、两条技术路线总览

| 路线 | 底模 | 加速 | 时长/画质 | 角色一致性 | 适用场景 |
|---|---|---|---|---|---|
| **A. Wan 2.2 I2V** | wan2.2_i2v_high/low_noise_14B_fp8（双模型） | lightx2v 4 步 LoRA | ~5s/81 帧 | 靠首帧图 + CLIP Vision | Civitai NSFW 主流生态，LoRA 最多 |
| **B. MiniMax H3** | H3 INT8 Pruned 单文件（fl2va / ref2va） | H3 Turbo 4 步 LoRA | 最长 15s/2K/音画同出 | **REF2VA 参考图原生支持（≤9 图 + ≤3 视频 + ≤3 音频）** | 角色一致、长镜头、参考驱动 |

两条路线都已接入 DashBox：Wan 2.2 走 ComfyUI-LB（:8188），H3 走专用实例（:8195）。

---

## 二、路线 A：Wan 2.2 I2V（重点）

### 2.1 配套文件核对（全部已在库，无需下载）

| 角色 | 文件 | 库内位置 |
|---|---|---|
| 高噪扩散模型 | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | diffusion_models ✅ |
| 低噪扩散模型 | `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | diffusion_models ✅ |
| 视频 VAE | `wan_2.1_vae.safetensors`（Wan2.1/2.2 共用） | vae ✅ |
| 文本编码器 | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | text_encoders ✅ |
| CLIP 视觉编码器 | `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | clip_vision ✅ |
| 加速 LoRA（高/低） | `wan2.2_i2v_lightx2v_4steps_lora_v1_high/low_noise.safetensors` | loras ✅ |

### 2.2 节点级工作流（本集群验证模板 + 作者 NSFW 增强）

**基础链（M2 实战模板，原生节点，LB 三端可跑）：**

```
1  UNETLoader        wan2.2_i2v_high_noise_14B_fp8_scaled ──→ 3 ModelSamplingSD3 (shift=3.0)
2  UNETLoader        wan2.2_i2v_low_noise_14B_fp8_scaled  ──→ 4 ModelSamplingSD3 (shift=3.0)
10 VAELoader         wan_2.1_vae
11 CLIPLoader        umt5_xxl_fp8_e4m3fn_scaled (type=wan) ──→ 12/13 CLIPTextEncode(正/负)
20 LoadImage         首帧图 ──→ 23 CLIPVisionEncode(22 CLIPVisionLoader CLIP-ViT-H)
21 WanImageToVideo   positive=12 negative=13 vae=10 start_image=20 clip_vision_output=23
                     (width/height: 832×480 横 或 480×832 竖, length=81, batch=1)
30 KSamplerAdvanced  model=3, 0 → steps/2, return_with_leftover_noise=enable  (高噪段)
31 KSamplerAdvanced  model=4, steps/2 → steps, add_noise=disable              (低噪段)
40 VAEDecode ──→ 50 VHS_VideoCombine (mp4, 16fps)
```

**NSFW 增强：在两个 UNETLoader 与 ModelSamplingSD3 之间各插一条 LoRA 链**

```
高噪侧: 1 UNETLoader → L1[LoraLoaderModelOnly: WAN General NSFW(0.8)]
                    → L2[LoraLoaderModelOnly: 动作 LoRA(0.7~1.0)]
                    → L3[LoraLoaderModelOnly: lightx2v high(1.0)] → 3 ModelSamplingSD3
低噪侧: 2 UNETLoader → L4[LoraLoaderModelOnly: DR34ML4Y LOW(0.8~1.0)]
                    → L5[LoraLoaderModelOnly: Anime Cumshot LOW(0.7，可选)]
                    → L6[LoraLoaderModelOnly: lightx2v low(1.0)]  → 4 ModelSamplingSD3
```

**挂 lightx2v 加速后采样参数改为作者配方**（原模板 20 步/CFG3.5 是高质量无加速档）：

| 参数 | 无加速（质量档） | lightx2v 加速（作者档） |
|---|---|---|
| 总步数 steps | 20（30: 0→10，31: 10→20） | **6（30: 0→3，31: 3→6）** |
| 采样器 | euler / simple | euler / simple |
| CFG | 3.5 | **5** |
| 时长 | 81 帧≈5s | 同 |

### 2.3 NSFW LoRA 搭配公式（8 个作品归纳）

```
HIGH 侧：通用 NSFW 底 LoRA ×1 + 动作/部位 LoRA ×1~2 + lightx2v 加速
LOW 侧：DR34ML4Y LOW（必备，姿势触发词来源）+ 对应 LOW 版内容 LoRA（可选）+ lightx2v 加速
```

| 作品 | HIGH 侧组合 | LOW 侧组合 | 主题 |
|---|---|---|---|
| 139345695 / 139346628 | WAN General NSFW + M4CROM4STI4 胸物理 + POV Cumshot + Lightning | DR34ML4Y + Anime Cumshot | 传教士 |
| 139346895 | WAN General NSFW + Slop Twerk + POV Cumshot + Lightning | DR34ML4Y + Anime Cumshot | 后入/twerk |
| 139593511 | Deepthroat + CloseUp Facial Cumshots + chasing blowjob + Lightning | DR34ML4Y + Anime Cumshot | 口交特写 |

**规律**：① DR34ML4Y LOW 是万能底座（全作品都带）；② 同主题 LoRA 不重复挂（blowjob 系三选一）；③ 内容 LoRA 强度 0.7~1.0，叠 3 个以上降到 0.5~0.7；④ 加速 LoRA 固定 1.0。

### 2.4 触发词速查表

| LoRA | 触发词（写进提示词才生效） |
|---|---|
| DR34ML4Y LOW v2 | `m15510n4ry`（传教士）、`d0gg1e`（后入）、`c0wg1rl`（女上）、`bl0wj0b`（口交）、`d0ubl3_bj`（双人） |
| M4CROM4STI4 胸物理 | `m4crom4sti4` |
| POV Body Cumshot & Pullout | `b0dyshot`、`pull0ut`、`sp0ntaneous`、`s3lf`、`p4rtner` |
| jfj Deepthroat | `blowjob, deepthroat` |
| chasing blowjob | `The girl performs a blowjob. moving her mouth up and down the penis.` / `The girl slowly performs blowjob. licking the penis up and down while contorting her mouth.` |
| HMPussy（H3） | `hmpussy`、`Vagina` |
| HMNSFW AIO（H3） | `hmmotion` |

### 2.5 提示词骨架与负面词模板

```
[姿势触发词], [主体+动作：强调运动动词 thrusting / rocking / bouncing / piston],
[视线与镜头：looking at the viewer / camera zooms out / first-person point of view],
[画质尾注：Authentic film look, High-fidelity details]
```

负面词（直接复制）：

```
watermark, text, subtitles, letterbox, pillarbox, frame, border, split screen, noise, artifacts, blur, vignette, 色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走
```

### 2.6 首帧图（I2V 的"I"）

1. **库内 NSFW SDXL**（零下载）：`lustifySDXLNSFW_apexV8` / `uberRealisticPornMerge_urpmv13` / `10eros_v14` + `RealSkin_xxXL_v1` LoRA，走 LB :8188 出图
2. **Krea2 Turbo**（作者路线，12.3GB 下载中）：`krea2TurboFP8_krea2TURBO` + Realistic Snapshot / Cutifyier / Realism Engine LoRA；配套 `flux2-vae` + `mistral_3_small_flux2_fp8` 编码器（已在库），按 FLUX2 链接线（UNETLoader + CLIPLoader(flux2) + VAELoader）
3. **SDXL 写实 + IPAdapter 角色锚定**：majicMIX 系 + `ip-adapter-plus-face_sdxl_vit-h`（角色一致性需求时）

---

## 三、路线 B：MiniMax H3（角色一致 + 长镜头 + 音画同出）

### 3.1 节点级工作流（本集群实战模板，:8195 专用实例）

**fl2va（图生视频）链：**

```
1  UNETLoader   minimax_h3_fl2va_pruned_int8_convrot.safetensors
2  CLIPLoader   qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors (type=minimax)
3  VAELoader    minimax_h3_video_vae_fp16.safetensors   （视频 VAE）
4  VAELoader    minimax_h3_audio_vae_fp32.safetensors   （音频 VAE）
10 LoadImage    首帧
20 MiniMaxH3ImageToVideo   clip=2 vae=3 首帧=10（输出 conditioning+latent）
30 RandomNoise / 31 KSamplerSelect(res_multistep) / 32 BasicScheduler(simple, steps=20)
33 BasicGuider  model=1 conditioning=20
34 SamplerCustomAdvanced → 双 VAEDecode（视频+音频）→ 50 CreateVideo → 60 SaveVideo(mp4)
```

**ref2va（参考图生视频，角色一致性）**：节点 1 换 `minimax_h3_ref2va_pruned_int8_convrot.safetensors`，节点 20 换 `MiniMaxH3ReferenceToVideo`（无首帧输入，吃参考图/视频/音频，prompt 内用 `<Picture 1>` `<Video 1>` `<Audio 1>` 1-based 标签指认参考资产）。

### 3.2 Turbo 加速改造（M19 实战方案）

在基础链上注入两个专用节点（**不是**通用 LoraLoader）：

```
100 MiniMaxH3TurboLoRA   model=1, lora_name=<turbo lora>, strength=1.0, low_vram=false
                        → 全链所有 model=1 的引用改指 100
101 MiniMaxH3TurboSampler → 34 SamplerCustomAdvanced.sampler 改指 101
32  BasicScheduler steps 20 → 6（推荐 4-8，6 步速度/画质最平衡）
```

Turbo LoRA 可选文件：`minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`（我们生产默认值）/ `minimax_h3_turbo_4step_comfy_pruned.safetensors`（larryvrh 版）/ `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy(_resized).safetensors`（lightx2v 版）。
强度调参：模糊拖影 → 1.05~1.2；过锐噪点 → 0.8~0.95；爆显存 → low_vram=true（合并权重，画质略软）。

### 3.3 NSFW 内容 LoRA 挂载

内容 LoRA（非 Turbo）走通用 **`LoraLoaderModelOnly`** 串在 UNETLoader(1) 之后、与 Turbo 节点兼容（先内容后 Turbo 或先 Turbo 后内容均可，建议内容 LoRA 在前）：

```
1 UNETLoader → L1[LoraLoaderModelOnly: HMNSFW_AIO_V2(0.8)] 
             → L2[LoraLoaderModelOnly: HMPussy(0.7，需要时)]
             → 100 MiniMaxH3TurboLoRA → …原链
```

| LoRA | 触发词 | 用途 |
|---|---|---|
| `HMNSFW_AIO_V2.safetensors` | `hmmotion` | 全能动作底座（I2V/T2V 通用） |
| `h3_musubi_v4-000040.safetensors` | —（Innie Pussy 部位增强） | 部位画质 |
| `hmpussy_v6_epoch30.safetensors` | `hmpussy`、`Vagina` | 部位+动作 |
| `VBVR_H3_attn_only.safetensors` | — | 视频推理增强（动作合理性），31MB 轻量 |

已入库 H3 LoRA（现货）：`stomach_bulge_H3_i2v_v1.0`、`riding_pose_H3_i2v_v1.0`。

### 3.4 提示词风格

H3 不吃 Wan 触发词骨架，用 `integrated_multimodal_description` 长段自然描写：视角（first-person POV）+ 人物 + 场景 + 动作 + 光线氛围一段写完；ref2va 路径用 `<Picture 1>` 等标签指认参考资产。

---

## 四、DashBox 实操流程

0. **零装配起步（推荐）**：直接用现成预设 [`presets/nsfw/`](../../../presets/nsfw/README.md)——4 个按 8 个作品搭配做好的工作流 JSON（Wan×3 + H3×1），全部真机冒烟出片；粘贴进工作流编辑器改提示词与首帧即用
1. **开 R18**：设置 → 模型库 → 「我已年满 18 岁」→ NSFW 条目可见（本批 LoRA 已按文件名自动标记；漏标可点行尾盾牌手动标记）
2. **选渠道**：设置 → 模型配置 → 自定义（local_gateway）
3. **出首帧**：画布/自由区 ComfyUI 渠道，SDXL NSFW 出图（或 Krea2）
4. **跑视频**：
   - **Wan 路线**：ComfyUI 渠道工作流编辑器贴 2.2 节链路 JSON → ModelNamePicker 逐个替换为在库权重 → 「体检」按钮一键核对在位 → 提交 LB :8188
   - **H3 路线**：视频渠道选 H3，按 3.2/3.3 节挂 Turbo + 内容 LoRA；ref2va 喂角色三视图（≤9 图）实现跨镜头一致性
5. **缺模型**：体检红叉旁的琥珀色「下载」按钮 → 自动跳 Civitai 搜索下载（401 资源需 Civitai token）

---

## 五、资源清单与下载状态（22 项）

> ✅=已入库（22/22，2026-08-17 核验：LB 三端 154 loras 一致、H3 :8195 共 25 loras）

| 状态 | 资源 | 文件 | 大小 | 路线/侧 |
|---|---|---|---|---|
| 已有 | Wan2.2 I2V high/low fp8 | wan2.2_i2v_*_noise_14B_fp8_scaled | 14.3GB×2 | A 双模型 |
| 已有 | lightx2v 4 步 high/low | wan2.2_i2v_lightx2v_4steps_lora_v1_* | 1.2GB×2 | A 加速 |
| ✅ | Lightning 1030-H | Wan_2_2_I2V_A14B_HIGH_lightx2v_4step_lora_v1030_rank_64_bf16 | 601MB | A 加速 |
| ✅ | WAN General NSFW | NSFW-22-H-e8 | 585MB | A HIGH 底座 |
| ✅ | Anime Cumshot (Low) | 56Low-noise-Cumshot-Aesthetics | 293MB | A LOW |
| ✅ | M4CROM4STI4 胸物理 | wan22-m4crom4sti4-i2v-20epoc-high-k3nk | 293MB | A HIGH |
| ✅ | POV Cumshot & Pullout | WAN-2.2-I2V-POV-Body-Cumshot-Pullout-HIGH-v1 | 585MB | A HIGH |
| ✅ | DR34ML4Y LOW v2 | DR34ML4Y_I2V_14B_LOW_V2 | 293MB | A LOW（必备） |
| ✅ | Slop Twerk | slop_twerk_HighNoise_merged3_7_v2 | 293MB | A HIGH |
| ✅ | jfj Deepthroat | jfj-deepthroat-W22-I2V-HN | 220MB | A HIGH |
| ✅ | Close-Up Facial Cumshots | CloseUpFacialCum-v10_High | 293MB | A HIGH |
| ✅ | chasing blowjob | chasing_blowjob_wan22_v1.0_000001500_high_noise | 293MB | A HIGH |
| ✅ | H3 FL2VA INT8 Pruned | minimaxH3INT8INT4_fl2vaINT8Pruned | 20GB | B（同生产 fl2va 权重） |
| ✅ | H3 REF2VA INT8 Pruned | minimaxH3INT8INT4_ref2vaINT8Pruned | 20GB | B（ref2va 权重） |
| ✅ | H3 Turbo lightx2v v0.1 | minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy(_resized) | 300MB~1.9GB | B 加速 |
| ✅ | H3 Turbo larryvrh | minimax_h3_turbo_4step_comfy_pruned 等 7 变体 | 592MB | B 加速 |
| ✅ | H3 Innie Pussy | h3_musubi_v4-000040 | 284MB | B 部位 |
| ✅ | HMPussy v6 | hmpussy_v6_epoch30 | 597MB | B 部位 |
| ✅ | HMNSFW AIO V2 | HMNSFW_AIO_V2 | 296MB | B 底座 |
| ✅ | VBVR 推理 LoRA | VBVR_H3_attn_only | 31MB | B 增强 |
| ✅ | Krea2 Turbo FP8 | krea2TurboFP8_krea2TURBO | 12.3GB | 首帧 |
| ✅ | Krea2 LoRA ×3 | RealisticSnapshotKrea2 / cutifier_krea2 / realism_engine_krea2_v3.1 | 218MB~1.5GB | 首帧 |

**注意**：Grok Imagine（139329388 用到）为闭源云端模型，无法自托管，已忽略；该作品的 H3 + Turbo 部分已覆盖。Krea2 为 KREA.ai 社区 FP8 优化权重（原 24.76GB BF16 → 12GB FP8），注意遵守 KREA 2 License。

---

## 六、扩充资源矩阵与融合方案（2026-08-17 调研新增）

### 6.1 扩充清单（48 件，全部三端/H3 object_info 核验通过）

**Wan 2.2 侧（44 件，NAS `Windows/.../models/loras`，LB 三端共享）：**

| 主题 | 文件（双噪成对只列一个） | 触发词/备注 |
|---|---|---|
| POV 传教士 | `wan2.2_i2v_high/lownoise_pov_missionary_v1.0`（+T2V 双件） | `pov missionary`；I2V/T2V 全套 4 件 |
| 女上位+反向 | `wan22.r3v3rs3_c0wg1rl-14b-High/Low-i2v_e70`（+T2V 双件 + 5B TI2V 两件） | `c0wg1rl`；6 件 |
| 手口组合 | `WAN-2.2-I2V-HandjobBlowjobCombo-HIGH/LOW-v1` | 组合动作一件搞定 |
| F4C3SPL4SH 射精 | `wan22-f4c3spl4sh-100epoc-high / 154epoc-low-k3nk` | K3NK 系，与 m4crom4sti4 同源 |
| Anal | `wan22_i2v_anal_v1_high/low_noise` | — |
| POV 女上位 | `WAN-2.2-I2V-POV-Cowgirl-HIGH/LOW-v1.0-fixed`（首选）+ v0.2/v0.1 双噪 | 用 v1.0-fixed |
| POV 双人 blowjob | `WAN-2.2-I2V-Double-Blowjob-HIGH/LOW-v1` | 双人场景 |
| DR34MJOB | `wan22-dr34mjob-*`（单/双/手） | DR34ML4Y 同作者，触发词风格一致 |
| Assertive Cowgirl | `Wan22-I2V-HIGH/LOW-Hip_Slammin_Assertive_Cowgirl` | 强势女上位 |
| 倒悬 | `reverse_suspended_congress_I2V/T2V_high/low` | I2V/T2V 4 件 |
| Futanari | `Wan22_I2V_A14B_FutaTF_lora_v1 / v1-2 双噪`（4 件） | 变身题材；H3 版见下 |
| 胸物理 | `BounceHighWan2_2` / `bounceV03-000084` | 与 m4crom4sti4 可叠加 |
| 射精增强 | `Wan22_CumV3_High/Low` | CumV3 系列 |
| 口交增强 | `deepthroat_v02` / `jfj-deepthroat-W22-I2V-HN`（jfj 版 401 受限未取，用 v02 替代） | — |
| 指交 | `wan_fingering_pussy_i2v2.2hi/lo_v10` | `fingering` |
| 综合 | `NaughtyTimes_pruned_r128_v2`（1.1GB r128 大 rank） | 多姿势综合 |
| 其他已落地 | `slop_twerk_HighNoise_merged3_7_v2` / `CloseUpFacialCum-v10_High` / `chasing_blowjob_wan22_v1.0_000001500_high_noise` | — |

**H3 侧（25 件全量，`toiv/comfyui-models/h3/loras`）：**

| 类别 | 文件 |
|---|---|
| 底座 | `HMNSFW_AIO_V2`（`hmmotion`） |
| 部位 | `h3_musubi_v4-000040` / `hmpussy_v6_epoch30` / `minimax_vag_000002500` / `vagassist_e40` |
| 射精 | `epic_cumshots-MiniMaxH3-ALPHA-CUMSH0T` |
| 姿势 | `riding_pose_H3_i2v_v1.0` / `stomach_bulge_H3_i2v_v1.0` / `SexGod-NaughtyTimes-lora-MINIMAXH3`（2.5GB 综合） |
| 口交 | `deepthroat_v1`（H3 版 1.2GB） |
| 题材 | `MiniMax-H3_Futa_Transformations_LoRA_V5.1` / `PlagueKind-tiddies-realismslider` / `minimax-h3-digicam`（Y2K 裸写实）/ `MiniMax-H3_RemoteOrgasm_v1`（高潮表情） |
| 动作增强 | `VBVR_H3_attn_only`（推理增强，**非 NSFW 不标**）/ `MiniMax_H3_Combat_LoRA`（SFW 打斗也可用） |
| 亲密 | `cxy_kiss_lora_h3_v01_step1500` / `H3_footjob_v0_step1000_fixed` |
| 角色 | `AI_Girl_Fictional_Women_Series30/31_H3`（修复 401 坏文件 + 新增 31） |
| 加速 | `minimax_h3_turbo_v4_step600_ema(_pruned)_comfyui` / `minimax_h3_turbo_4step_ema_ckpt850_pruned_comfyui` / `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy` |

**未获取（1 件）**：`MiniMax-H3_penis_coachbate_v1_8350` — Civitai **Early Access 付费墙**（需 Buzz），跳过；部位增强已有 musubi/vag/vagassist 三件覆盖。

### 6.2 跨引擎同主题矩阵（融合核心）

同一题材按「Wan 生态丰富度 vs H3 角色一致性」双轨选路：

| 题材 | Wan 2.2 方案（LoRA 叠加序） | H3 方案 | 首选 |
|---|---|---|---|
| 常规动作 | DR34ML4Y(0.8低噪)+NSFW-22(0.8高噪)+姿势 LoRA(0.7~1.0) | HMNSFW_AIO_V2(0.8) | 平手；H3 要音画同出时胜 |
| 口交 | chasing_blowjob + deepthroat_v02 + DR34MJOB | deepthroat_v1 | Wan（LoRA 选择多 3 倍） |
| 射精 | CumV3 双噪 + F4C3SPL4SH + POV Pullout + CloseUpFacialCum | epic_cumshots ALPHA | Wan（可高低噪分层精细控制） |
| 胸物理 | m4crom4sti4 + BounceHigh + bounceV03 | tiddies-realismslider | Wan（三件可叠加调强度） |
| 部位特写 | hmpussy_v6 | h3_musubi + minimax_vag + vagassist | 平手 |
| 女上位 | c0wg1rl 系 + POV Cowgirl + Assertive Cowgirl | riding_pose | Wan |
| Futanari | FutaTF Wan 版（v1-2 新） | Futa V5.1 | 平手 |
| 角色一致长镜 | （弱，靠首帧） | ref2va ≤9 参考图 + 任意内容 LoRA | **H3 碾压** |
| 多姿势/综合 | NaughtyTimes r128 | SexGod-NaughtyTimes H3 版 | 平手 |
| 亲密前戏 | Posing Nude + kissing 类 | cxy_kiss + footjob | 平手 |

### 6.3 融合工作流（双引擎接力）

**① 首尾帧桥接（角色一致 + Wan 生态兼得，推荐）**
```
Krea2/SDXL 出角色定妆首帧 → H3 ref2va（≤9 三视图锁角色，出 10-15s 主体镜头）
  → 取末帧 → Wan I2V（末帧为首帧，挂题材 LoRA 出 5s 高潮/特写镜头）
  → VHS_VideoCombine 拼接
```
要点：H3 出片取最后一帧用 VHS 的 `Get Last Frame` 或直接读 mp4 尾帧 PNG 化；Wan 段用 v1.0-fixed 系 LoRA 防爆。

**② 音画分层（Wan 画面 + H3 音频思路）**
Wan 2.2 无原生音频——需要音画同出的镜头一律走 H3；Wan 段落后期配 CosyVoice2 配音 + 音效库。

**③ LoRA 叠加公式（防爆显存/崩坏上限）**
```
高噪侧: 底座(0.8) + 题材/姿势(0.7~1.0) + 部位(0.5~0.7，可选) + lightx2v 加速(1.0)  ≤4 层
低噪侧: DR34ML4Y(0.8~1.0) + 题材配对低噪(0.7) + lightx2v low(1.0)                  ≤3 层
```
信号：糊/拖影 → 题材 LoRA 降 0.1~0.2；崩坏/多肢 → 部位 LoRA 撤掉；H3 侧内容 LoRA 一律 `LoraLoaderModelOnly` 串在 UNETLoader 后、Turbo 节点前。

**④ 提示词分层模板（Wan 触发词骨架 + H3 自然段）**
- Wan：`[触发词], [主体+运动动词], [镜头], [画质尾注]`（见 2.4 节）
- H3：`integrated_multimodal_description` 一段式，ref2va 用 `<Picture 1>` 标签指认角色

### 6.4 运维备忘

- **下载链路**：Civitai 冷门文件 red 镜像 307 → `b2.civitai.com`（Backblaze，国内被墙）——**必须走本机 Clash 代理 `http://127.0.0.1:7897`**（批 6/7/8 实测 8MB/s）；401/Early Access 资源附 `?token=`（token 在团队密码库）
- **NSFW 标记**：新文件落库后 DashBox 按文件名关键词自动标记（词表已扩至 40+：`cum/futa/naughty/bounce/cowgirl/fingering/orgasm/anal/handjob/boob/penis/vagina/creampie/ahegao/paizuri/tiddies/nipple/dr34mjob` 等）；VBVR 类推理增强 LoRA 不标记属预期；漏标用模型库行尾盾牌手动标记
- **双库分工**：Wan/SDXL/Krea 权重 → `NAS/Windows/ComfyUI/ComfyUIModel/models`（LB 三端共享）；H3 权重 → `NAS/toiv/comfyui-models/h3`（:8195 实例经 extra_model_paths 挂载）；跨库复用（如 hmpussy_v6 双引擎可用）直接 `cp` 双写
