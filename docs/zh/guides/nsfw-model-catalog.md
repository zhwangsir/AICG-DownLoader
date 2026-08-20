# NSFW 模型资产总账（来源 / 用途 / 触发词 / 搭配）

> 维护：2026-08-17 全量盘点（LB 三端 154 loras + H3 25 loras + checkpoints 22 项）
> 存储：`NAS/Windows/ComfyUI/ComfyUIModel/models`（LB 三端共享）+ `NAS/toiv/comfyui-models/h3`（H3 实例）
> 使用：Wan 走 LB :8188；H3 走 :8195；生图走 LB :8188（SDXL 链）

---

## 一、生图底模（checkpoints，首帧/定妆照）

### 1.1 写实系（真人风，NSFW 首帧主力）

| 模型 | 来源 | 用途 | 备注 |
|---|---|---|---|
| `lustifySDXLNSFW_apexV8` | [civitai.com/models/573152](https://civitai.com/models/573152) LUSTIFY! | **NSFW 写实首帧首选** | 36 万下载；专为 NSFW 调教，解剖准确 |
| `pornmaster_proSDXLV8` | [civitai.com/models/82543](https://civitai.com/models/82543) PornMaster-色情大师 | NSFW 写实（国产审美） | 36 万下载；2026-08-17 新增 |
| `uberRealisticPornMerge_urpmv13` | [civitai.com/models/2664](https://civitai.com/models/2664) URPM | NSFW 写实融合 | 老牌稳定，2GB 轻量 |
| `cyberrealistic_v120` / `cyberrealisticPony_v180Coreshift` | [civitai.com/models/312530](https://civitai.com/models/312530) / [/443821](https://civitai.com/models/443821) CyberRealistic | 写实通用（SFW/轻 NSFW） | Pony 版兼容 Pony 提示词体系 |
| `ponyRealism_V22` | [civitai.com/models/372465](https://civitai.com/models/372465) Pony Realism | **Pony 系写实化**（2.5D→真人桥） | 56 万下载；2026-08-17 新增 |
| `waiREALCN_v150` | [civitai.com/models/469902](https://civitai.com/models/469902) WAI-REAL_CN | **亚洲面孔写实** | 国产模型，国漫/亚洲角色首选；2026-08-17 新增 |
| `10eros_v14` | [civitai.com/models/929685](https://civitai.com/models/929685) 10eros | NSFW 写实（27.8GB FP16 大文件） | 细节极强但占盘 |
| `majicMIX` 系 | [civitai.com/models/43331](https://civitai.com/models/43331) majicMIX realistic | **AICG 角色定妆（SFW）** | 角色一致性锚定专用 |

### 1.2 Pony / Illustrious 系（动漫·2.5D 风）

| 模型 | 来源 | 用途 | 备注 |
|---|---|---|---|
| `ponyDiffusionV6XL_v6` | [civitai.com/models/257749](https://civitai.com/models/257749) Pony V6 | Pony 系祖底 | 105 万下载；提示词必须带 `score_9, score_8_up` 体系 |
| `autismmixSDXL_autismmixPony` | [civitai.com/models/288584](https://civitai.com/models/288584) AutismMix | Pony 动漫 NSFW | 37 万下载 |
| `waiIllustriousSDXL_v170` | [civitai.com/models/827184](https://civitai.com/models/827184) WAI-illustrious | **Illustrious 系顶流** | 148 万下载 |
| `waiSHUFFLENOOB_vPred04` / `noobaiXL_vpred10` | [civitai.com/models/1195881](https://civitai.com/models/1195881) WAI-SHUFFLE / NoobAI | NoobAI 系动漫 | v-prediction 模型（需对应采样设置） |
| `hassakuXLIllustrious_v34` | [civitai.com/models/140272](https://civitai.com/models/140272) Hassaku XL | 明亮风动漫 NSFW | 50 万下载 |
| `prefectIllustriousXL_40` | [civitai.com/models/1224788](https://civitai.com/models/1224788) Prefect illustrious | 精品动漫 | — |
| `animagineXL40` | [civitai.com/models/260267](https://civitai.com/models/260267) Animagine XL 4.0 | 动漫通用（SFW/NSFW） | AICG 国漫画风锚定 |
| `nova3DCGXL_ilV90` / `novaAnimeXL_ilV190` | [civitai.com/models/376130](https://civitai.com/models/376130) Nova 系 | 3DCG 风 / 日系动漫 | Anime 版 2026-08-17 新增 |

### 1.3 Krea2 系（高质量首帧，FLUX2 架构）

| 模型 | 来源 | 用途 | 配套 |
|---|---|---|---|
| `krea2TurboFP8_krea2TURBO`（12.3GB） | [civitai.com/models/2061639](https://civitai.com/models/2061639) Krea2 Turbo FP8 | **最高质量写实首帧**（4-8 步出图） | `flux2-vae` + `mistral_3_small_flux2_fp8` 编码器（已在库） |
| LoRA：`RealisticSnapshotKrea2` | Civitai Krea2 配套 | 写实快照质感 | 强度 0.6-0.8 |
| LoRA：`cutifier_krea2` | 同上 | 人物美化 | 0.4-0.6 |
| LoRA：`realism_engine_krea2_v3.1`（1.5GB） | 同上 | 极致写实引擎 | 0.5-0.7 |

### 1.4 生图 LoRA

| LoRA | 来源 | 用途 | 用法 |
|---|---|---|---|
| `RealSkin_xxXL_v1`（5MB） | [civitai.com/models/152292](https://civitai.com/models/152292) | **皮肤质感增强**（去塑料感） | 写实底模通用，0.4-0.6 |
| `AddMicroDetails_Illustrious_v6` | [civitai.com/models/1387960](https://civitai.com/models/1387960) | 微细节增强 | Illustrious 系，0.3-0.5 |
| `Breast Size Slider` (Illustrious V2) | [civitai.com/models/1451700](https://civitai.com/models/1451700) | 胸围滑杆（负值缩/正值增） | -2.0~+2.0 |
| `ip-adapter-plus-face_sdxl_vit-h` | [huggingface.co/h94/IP-Adapter](https://huggingface.co/h94/IP-Adapter) | **角色脸一致性锚定** | 配 majicMIX + CLIP-ViT-H |

---

## 二、Wan 2.2 视频 LoRA（loras，LB 三端共享）

### 2.1 底座/加速（必备）

| LoRA | 来源 | 用途 | 触发词 |
|---|---|---|---|
| `NSFW-22-H-e8` | [civitai.com/models/1307155](https://civitai.com/models/1307155) WAN General NSFW | **高噪通用底座**（38 万下载） | — |
| `DR34ML4Y_I2V_14B_LOW_V2` | [civitai.com/models/1811313](https://civitai.com/models/1811313) DR34ML4Y | **低噪万能底座**（47 万下载，姿势触发词来源） | `m15510n4ry`（传教士）`d0gg1e`（后入）`c0wg1rl`（女上）`bl0wj0b`（口交）`d0ubl3_bj`（双人） |
| `wan2.2_i2v_lightx2v_4steps_lora_v1_high/low_noise` | [civitai.com/models/1585622](https://civitai.com/models/1585622) lightx2v | **4 步加速**（6 步 CFG5 配方） | — 固定 1.0 |
| `Wan_2_2_I2V_A14B_HIGH_lightx2v_4step_lora_v1030_rank_64_bf16` | 同上 1030 版 | 加速新版（HIGH 单件） | — |

### 2.2 姿势/动作

| LoRA | 来源 | 用途 | 触发词/备注 |
|---|---|---|---|
| `wan2.2_i2v_high/lownoise_pov_missionary_v1.0`（+T2V 版） | [civitai.com/models/1331682](https://civitai.com/models/1331682) | POV 传教士 | 23 万下载 |
| `wan22.r3v3rs3_c0wg1rl-14b-High/Low-i2v_e70`（+T2V + 5B 版） | [civitai.com/models/1428098](https://civitai.com/models/1428098) | 女上位+反向 | `c0wg1rl` |
| `Wan22-I2V-HIGH/LOW-Hip_Slammin_Assertive_Cowgirl` | [civitai.com/models/1566648](https://civitai.com/models/1566648) | 强势女上位 | — |
| `WAN-2.2-I2V-POV-Cowgirl-HIGH/LOW-v1.0-fixed`（首选） | [civitai.com/models/1874099](https://civitai.com/models/1874099) | POV 女上位 | 用 v1.0-fixed |
| `reverse_suspended_congress_I2V/T2V_high/low` | [civitai.com/models/1894970](https://civitai.com/models/1894970) | 倒悬 | — |
| `wan22_i2v_anal_v1_high/low_noise` | [civitai.com/models/1426284](https://civitai.com/models/1426284) | Anal | — |
| `slop_twerk_HighNoise_merged3_7_v2` | [civitai.com/models/2273468](https://civitai.com/models/2273468) | twerk/臀舞 | — |
| `wan_fingering_pussy_i2v2.2hi/lo_v10` | [civitai.com/models/1952032](https://civitai.com/models/1952032) Perfect Fingering | 指交 | `fingering` |
| `NaughtyTimes_pruned_r128_v2`（1.1GB） | [civitai.com/models/3212436](https://civitai.com/models/3212436) | 多姿势综合（r128 大 rank） | — |
| `NSFW Posing Nude` | [civitai.com/models/1648982](https://civitai.com/models/1648982) | 裸体摆姿（开场/静态） | — |

### 2.3 口交系

| LoRA | 来源 | 用途 | 触发词 |
|---|---|---|---|
| `chasing_blowjob_wan22_v1.0_000001500_high_noise` | [civitai.com/models/2796979](https://civitai.com/models/2796979) | chasing 系口交 | `The girl performs a blowjob. moving her mouth up and down the penis.` |
| `deepthroat_v02` | [civitai.com/models/2023407](https://civitai.com/models/2023407) | 深喉 v02 | `deepthroat` |
| `WAN-2.2-I2V-Double-Blowjob-HIGH/LOW-v1` | [civitai.com/models/1906148](https://civitai.com/models/1906148) | 双人口交 | — |
| `WAN-2.2-I2V-HandjobBlowjobCombo-HIGH/LOW-v1` | [civitai.com/models/1899045](https://civitai.com/models/1899045) | 手口组合 | — |
| `wan22-dr34mjob-*` | [civitai.com/models/1395313](https://civitai.com/models/1395313) WAN DR34MJOB | 单/双/手口交（DR34ML4Y 同作者） | 同 DR34ML4Y 风格 |

### 2.4 射精/部位/胸物理

| LoRA | 来源 | 用途 | 触发词 |
|---|---|---|---|
| `Wan22_CumV3_High/Low` | [civitai.com/models/1962545](https://civitai.com/models/1962545) Cum/Facial Wan2.2 | 射精/颜射（11 万下载） | — |
| `wan22-f4c3spl4sh-100epoc-high / 154epoc-low-k3nk` | [civitai.com/models/1922973](https://civitai.com/models/1922973) F4C3SPL4SH | 射精（K3NK 系） | — |
| `WAN-2.2-I2V-POV-Body-Cumshot-Pullout-HIGH-v1` | [civitai.com/models/2071314](https://civitai.com/models/2071314) | POV 体射+拔出 | `b0dyshot`、`pull0ut`、`sp0ntaneous`、`s3lf`、`p4rtner` |
| `CloseUpFacialCum-v10_High` | [civitai.com/models/2772088](https://civitai.com/models/2772088) | 特写颜射 | — |
| `56Low-noise-Cumshot-Aesthetics` | [civitai.com/models/1869475](https://civitai.com/models/1869475) | 动漫射精美学（低噪） | — |
| `wan22-m4crom4sti4-i2v-20epoc-high-k3nk` | [civitai.com/models/1873058](https://civitai.com/models/1873058) M4CROM4STI4 | **胸部物理**（K3NK） | `m4crom4sti4`（也可纯自然语言 bounce 驱动） |
| `BounceHighWan2_2` / `bounceV03-000084` | [civitai.com/models/1343431](https://civitai.com/models/1343431) Bouncing Boobs | 胸部弹跳 | — |
| `hmpussy_v6_epoch30` | [civitai.com/models/3215304](https://civitai.com/models/3215304) HMPussy | 部位特写+动作 | `hmpussy`、`Vagina` |

### 2.5 题材/综合

| LoRA | 来源 | 用途 |
|---|---|---|
| `Wan22_I2V_A14B_FutaTF_lora_v1 / v1-2 双噪`（4 件） | [civitai.com/models/1911812](https://civitai.com/models/1911812) | Futanari 变身（43 万下载） |
| `deepthroat_v1`（H3 版 1.2GB） | H3 库 | H3 口交（见三节） |

---

## 三、H3 视频 LoRA（`toiv/comfyui-models/h3/loras`，:8195 实例）

| LoRA | 来源 | 用途 | 触发词/备注 |
|---|---|---|---|
| `HMNSFW_AIO_V2` | [civitai.com/models/1916809](https://civitai.com/models/1916809) | **全能动作底座** | `hmmotion` |
| `h3_musubi_v4-000040` | [civitai.com/models/1825006](https://civitai.com/models/1825006) | Innie Pussy 部位增强 | — |
| `minimax_vag_000002500` / `vagassist_e40` | Civitai | 部位增强 | — |
| `epic_cumshots-MiniMaxH3-ALPHA-CUMSH0T` | [civitai.com/models/3202064](https://civitai.com/models/3202064) | 射精 | ALPHA 版 |
| `riding_pose_H3_i2v_v1.0` / `stomach_bulge_H3_i2v_v1.0` | Civitai | 骑乘/腹部凸起 | — |
| `SexGod-NaughtyTimes-lora-MINIMAXH3`（2.5GB） | [civitai.com/models/3225809](https://civitai.com/models/3225809) | 多姿势综合（H3 版） | — |
| `deepthroat_v1`（H3 版） | Civitai | H3 口交 | — |
| `MiniMax-H3_Futa_Transformations_LoRA_V5.1` | [civitai.com/models/1911812](https://civitai.com/models/1911812) | Futa 变身（H3 版） | — |
| `PlagueKind-tiddies-realismslider` | [civitai.com/models/2858760](https://civitai.com/models/2858760) | 胸部/写实滑杆 | 强度可调正负 |
| `minimax-h3-digicam` | [civitai.com/models/2855485](https://civitai.com/models/2855485) | Y2K 数码相机裸写实 | — |
| `MiniMax-H3_RemoteOrgasm_v1` | [civitai.com/models/2858113](https://civitai.com/models/2858113) | 高潮表情 | — |
| `cxy_kiss_lora_h3_v01_step1500` / `H3_footjob_v0_step1000_fixed` | Civitai | 接吻/足交 | — |
| `AI_Girl_Fictional_Women_Series30/31_H3` | [civitai.com/models/2845077](https://civitai.com/models/2845077) | 虚构角色系列 | — |
| `VBVR_H3_attn_only` | [civitai.com/models/3220766](https://civitai.com/models/3220766) | **动作推理增强**（非内容，不标 NSFW） | 0.5-0.7 |
| `MiniMax_H3_Combat_LoRA` | [civitai.com/models/2853878](https://civitai.com/models/2853878) | 打斗动作（SFW 通用） | — |
| `tutu_t8_minimax_h3_av_20to8_nfe_step300_comfyui` | Civitai | 音视频加速变体 | — |

**H3 Turbo 加速**：`minimax_h3_turbo_v4_step600_ema_pruned_comfyui`（生产默认）/ `minimax_h3_turbo_4step_ema_ckpt850_pruned_comfyui` / `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy`——⚠️ Turbo 链与内容 LoRA 组合目前形状报错（AGENTS.md 易错点 25），生产暂用 20 步基线

---

## 四、用法速查

**首帧配方（写实）**：`lustifySDXLNSFW_apexV8` + `RealSkin_xxXL_v1`(0.5) → 提示词带视角描述（`lying on back, legs spread, looking up at viewer`）
**首帧配方（亚洲）**：`waiREALCN_v150` + `RealSkin`；**动漫**：`waiIllustriousSDXL_v170` + `AddMicroDetails`(0.4)
**视频配方**：直接抄 [presets/nsfw/](../../../presets/nsfw/README.md) 4 个预设（含 LoRA 链+触发词+参数）
**标注规范**：新模型入库后在此文件补一行（来源 URL + 用途 + 触发词），DashBox 模型库靠文件名关键词自动打 NSFW 标
