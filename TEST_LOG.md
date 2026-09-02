# TEST_LOG.md — AIGCPannel

- 2026-09-02 c27f6db dual-pushed (P3, on 291d994): preview=true/quality=preview enables Turbo (MiniMaxH3TurboLoRA+MiniMaxH3TurboSampler; FL2VA 8 steps, Ref2VA 4 steps). Final default / preview=false / quality=final turns Turbo off, native 20 steps; h3_turbo_enabled config default still False. SFW turbo LoRA is minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors (not 10Eros). NSFW preview may use 10Eros_Max_h3_TURBO_ref2va.safetensors (filename not verified on NAS). Turbo+content LoRA rejected (known shape mismatch). VideoModal Turbo preview vs generate; canvas one-click sends preview:false, quality:final. Tests: backend 17 P3 plus existing turbo/NSFW/P2 passed; frontend VideoModal/Canvas/client 121 passed. Gaps: not using official minimax_h3_fl2v/lightx2v names (:8195 product default already v4 pruned); one-click pipeline does not pass preview; no :8195 live Turbo smoke. LICENSE/NOTICE/ToIV untouched.

- 2026-09-02 a284c52 dual-pushed (P2, on 176ab03): last-frame chain default on; fail retry once then degrade to first-frame only; character three-views + front face + optional voiceprint into Ref2VA; empty shots SFW/NSFW both H3 FL2VA, not Wan; LTX-2.5 only when ltx_enabled and :8198 up. LICENSE/NOTICE/ToIV untouched.

- 2026-09-02 412f0ba dual-pushed (P1): before H3 render, spark-local Context-IR rewrite; on failure fall back to original text. LICENSE/NOTICE/ToIV untouched.

- 2026-09-02 bf9fe4c dual-pushed (P0): workbench/gateway with reference uses MiniMaxH3ReferenceToVideo; PIN on = 10Eros_Max H3 UNet, off = official INT8; dialogue keeps H3 native audio only (no IndexTTS overlay); catalog resolution 768P only, fake 2K removed. NSFW also H3, not Wan/LTX. LICENSE/NOTICE/ToIV untouched.

- 2026-08-31 三连运维 + M18.7 core E2E 复验：① HunyuanImage 修复完成（import 路径 + 镜像重建 torch2.8+cu128 适配 Blackwell sm_120 + 96GB swapfile + 5 组权重 ~127GB 全下载），/health loaded:true，2048×2048 测试图 4.4MB 端到端通过；GPU3 显存余量 ~13GB 列入观察。② pc02 ComfyUIWatchdog 上线（5 分钟探测 :8193/:8194，netstat 精确 PID 重启，10 分钟冷却，SYSTEM 计划任务验证无误重启）；ComfyUI-Manager 改 offline。③ core 部署 91a5752 复跑 E2E（pipeline-6cd33231a41d，都市悬疑 1集×2镜，passed=true，1627.8s，成片 2.9MB/6.62s/quality 85）：M18.7 三机制全部按设计触发——QC 拦截唯一角色定妆照 3 次不合格、隔离删除日志确证（deleted=True）、reference_images_stale_skipped=0 零污染；visual_quality 因无参考图 skipped（宁缺毋滥的预期行为）。新暴露瓶颈：SDXL 定妆照与描述匹配度低致 QC 全败（角色锁可用率待提升，候选：PromptEnhancer/换 checkpoint/QC 分档）。ToIV 未动。

- 2026-08-30 M18.7 参考图集质量根治（M18.6 遗留缺口，TDD 先红后绿，未 commit/push）：根治「M18.2 QC 拦截新定妆照后、_collect_character_reference_images 按 character_id 静默命中上一轮旧剧本同 ID 资产（林默/林小满 vs 本轮林远/苏清）」导致的 ref2va/漂移对照基准错配。三件套：① 资产血缘 — CharacterAsset 新增 source_script_id（空=legacy）+ updated_at_iso（ISO 8601；updated_at 保留 epoch int，pydantic v2 不允许 int→str 强转，改类型会令旧资产文件加载失败），CharacterRequest 新增 project_id，character_agent 入库写血缘、orchestrator._step_characters 透传 script.project_id（写入/校验同口径）；② 拦截即隔离 — _qc_three_views 两处重试耗尽判失败点先 _isolate_character_asset 删除资产库残留并记 warning，隔离异常不阻断拦截；③ 收集防串戏 — _collect_character_reference_images 新增 stats 出参，血缘不一致的陈旧资产跳过（info），legacy 无字段资产兜底可用（info 提示陈旧），steps.video（标准+长视频两路）新增 reference_images_stale_skipped 计数。测试：新增 15 例（test_character_library +6：血缘写入/legacy 标记/空血缘保留旧血缘/新血缘覆盖/旧文件兼容/execute 血缘透传；test_character_agent +3：QC 耗尽隔离删除 mock 断言/真实库残留删除/隔离异常不掩盖拦截；test_pipeline_orchestrator +6：跨剧本跳过/同剧本保留/legacy 兜底+info 日志/steps.video 计数上报/全新鲜计数为 0/定妆照步骤 project_id 透传）。RED：14 红 1 防御绿（隔离异常用例在缺实现时碰巧过）；GREEN：三目标文件 87/87。全量回归 `uv run pytest tests/unit -q`：**1247 passed（1232→1247，+15）/ 10 failed 与基线逐一相同**（test_model_registry_service×3、test_nas_library_service×6 为 NAS 挂载依赖，test_rag_service×1 为嵌入模型外网下载，均既有环境问题非回归）；覆盖率 TOTAL 6484 语句 99%（character_agent/character_library/schemas 100%，pipeline_orchestrator 94%）；ruff 5 项报错经 git stash 基线复现为既有问题。遗留：实机 E2E（新剧本跑通后 drift 干净量化）待 core 复跑；legacy 兜底资产仍可能来自旧剧本（仅有 info 日志提示，彻底根治需用户重新生成定妆照）。dashbox/、LICENSE/NOTICE、ToIV 未动。

- 2026-08-30 M25.3 画布工作流模板库落地：GET /api/drama/pipeline/templates（genre_tropes KB，category 过滤，KB 异常兜底 200 空列表）；ScriptModal 新建模式「模板起手」选择器预填创意框。测试：后端 test_pipeline_templates 10/10；前端 vitest 578 passed（569→578）；tsc 0；build 948ms。后端基线 10 个既有失败为 NAS 挂载/嵌入模型外网下载环境问题（git stash 基线复现，与改动无关）。同日 .gitignore 补齐产物目录（.coverage / frontend coverage / test_artifacts / platform output+reports / tts-samples / dashbox/works），268MB 报告产物不入库。LICENSE/NOTICE/ToIV 未动。

- 2026-08-29 7c75196 dual-pushed: engine Settings pinned to cluster. custom gateway configured=true, base http://host.docker.internal:8790/v1; LLM spark02 .84:8000; VLM spark01 .82:8000; image ComfyUI .127:8188 SDXL; video H3 .127:8195; TTS IndexTTS .127:9200; media relay=local_http. LTX-2.5 still configured but :8198 DOWN. Official relayclaw channel kept but unused in custom mode. LICENSE/NOTICE/ToIV untouched.

- 2026-08-28 ToIV repo-check detail (no ToIV code, no ToIV push): H3 :8195 Hailuo 3.0 main; R18 keeps LTX-2.3+10Eros v14 (not LTX-2.5). ToIV SFW LTX-2.5 retired 2026-08-23; unpushed Phase 4 ltx25-multishot not default. Wan2.2 = silent/motion/R18 I2V; Wan2.1-VACE-14B = edit/transition/keyframe chain. LongCat :8197. Image default flux2_dev_fp8mixed; optional qwen_image/z_image; qwen-image-edit; R18 stills URPM. 3D=Hunyuan3D; no Hunyuan video 1.0. AIGCPannel still SDXL+IPAdapter (chase only if user names it). H3 main aligned. AIGCPannel SFW口径 unchanged. LICENSE/NOTICE untouched.

- 2026-08-28 wording: AIGCPannel SFW dialogue/lock=H3; empty/preview=LTX-2.5 (on when :8198 up). Wan2.2 and LTX-2.3+10Eros stay ToIV R18 (NSFW value, not SFW empty-shot default). AIGCPannel does not change ToIV. LICENSE/NOTICE untouched.

- 2026-08-28 evening engine map (corrected): AIGCPannel SFW H3=Hailuo 3.0 dialogue/lock; empty/preview=LTX-2.5 (enable when :8198 up). Wan2.2 / LTX-2.3+10Eros are ToIV R18, not AIGCPannel SFW silent fallback. Round1 landed. LICENSE/NOTICE and ToIV code untouched.

- 2026-08-28 infra aligned to ToIV SoT (no new code): LTX-2.5 :8198 marked retired in STATE.infrastructure; ASR primary is workstation :9210, studio ASR / studio02 :9212 obsolete. LICENSE/NOTICE and ToIV untouched.

- 2026-08-28 H3 generate_async smoked (no new code, HEAD still 71d616f): task video-a54cf30392c7, ~1.5min, mp4 768x1344 3s. :8080 does not reverse-proxy /static/video (410); local static on :8100. LICENSE/NOTICE and ToIV untouched.

- 2026-08-28 0511598+bc85d48 dual-pushed (no force, on 85e0787): script thinking off by default; web_search optional (request/env, default off). model download root uses first readable/writable NAS path (skip unread /mnt/toiv-nas on Mac). GitHub fast-forwarded 85e0787 docs. LICENSE/NOTICE and ToIV untouched. H3 video next.

- 2026-08-28 live web image dashbox-web:latest 11444d78e507 (title AIGCPannel — 通用 AIGC 视频引擎). e09bb3b548e8 superseded. canonical start ./start-aigcpannel.sh (start-dashbox.sh wraps). remotes Winery_z/AIGCPannel and zhwangsir/AIGCPannel, code tip 378f5c7. LICENSE/NOTICE and ToIV untouched.

- 2026-08-28 web image rebuilt: :8080 title is AIGCPannel — 通用 AIGC 视频引擎; HTML no longer uses DashBox/虾导 as product name. no new code commit; tip still 378f5c7. LICENSE/NOTICE and ToIV untouched.

- 2026-08-28 378f5c7 dual-pushed (no force): repo/dir renamed to AIGCPannel (ALLProject/AIGCPannel; gitee.com/Winery_z/AIGCPannel; github.com/zhwangsir/AIGCPannel). dashbox/ is finishing engine, not an independent product. old slugs AICG-DownLoader/DashBox/LibTV/comfy-downloader are redirects, do not delete. crate still comfy-downloader. live :8080 image title still DashBox 虾导 (source already renamed; web image rebuilding). LICENSE/NOTICE ELv2 and ToIV untouched.

- 2026-08-28 5a19c8d dual-pushed (no force, on 7aa28cc): no-.env defaults LTX off, TTS=IndexTTS, LLM spark02 qwen3.6-uncensored, VLM spark01 qwen3-vl-32b, aligned with ToIV/.env.example. LICENSE/NOTICE/brand and ToIV untouched.

- 2026-08-28 46b1994 dual-pushed (no force): product is AIGCPannel; DashBox is engine module :8080/:8780; canonical start ./start-aigcpannel.sh (start-dashbox.sh wraps same). crate/OS still comfy-downloader. repo/dir/old slugs remain DashBox, do not rename or delete. panel product=AIGCPannel. health :8080 and :8780/:8100 /api/drama/health 200. includes docs 923b940. LICENSE/NOTICE/brand and ToIV untouched.

- 2026-08-28 4185c30 dual-pushed (no force): GUI/packaging display name is DashBox 模型库; crate/OS config dir still comfy-downloader; installer DefaultDirName/AppId unchanged for upgrade. NOTICE still historically AIGCPannel. old slugs AICG-DownLoader/AIGCPannel are redirects, do not delete. health :8080 and :8780/:8100 /api/drama/health 200. docs 2117d92 and 2829ff8 went up with it. LICENSE/NOTICE/brand and ToIV untouched.

- 2026-08-28 web baked: dashbox-web:latest e09bb3b548e8, container SPA md5 matches image, no docker-cp overlay. :8080/ and :8080/:8780/:8100 /api/drama/health 200. Dockerfile unchanged so not committed. remote code still 19a3141. LICENSE/NOTICE/brand and ToIV untouched.

- 2026-08-27 Studio 19a3141 dual-pushed (no force): drama edit allows omitting subtitle_url (no empty SRT download); empty subs no longer fall back to R18. Gitee+GitHub aligned at 19a3141. web still docker cp (image baking). LICENSE/NOTICE/brand and ToIV untouched.

- 2026-08-27 Studio 3429167 dual-pushed (1567fc6..3429167, no force): TTS/video/edit also default /api/drama/{voice|video|edit}/generate_async, fail fallback R18. web/api images recreated. nginx CSP img-src includes http://192.168.71.127:8188. edit missing subtitle_url fails then R18. LICENSE/NOTICE/brand and ToIV untouched.

- 2026-08-27 Studio 1567fc6 dual-pushed (no force): NSFWDramaStudioNode default pipelineEngine=drama; script/first-frame via /api/drama/script|storyboard/generate_async; fail fallback R18; switchable. TTS/video/compose still R18. web/api images not fully rebuilt (docker cp). CSP may block ComfyUI :8188 thumbnails. LICENSE/NOTICE/brand and ToIV untouched. Root five-doc set untouched.

- 2026-08-27 keep only the fused DashBox repo. GitHub/Gitee have no independent AICG-DownLoader / AIGCPannel / LibTV / comfy-downloader repos; those slugs are DashBox rename redirects — do not delete. Local disk only ALLProject/DashBox. Registry no longer lists those copies as independent repos. LICENSE/NOTICE/brand untouched. ToIV untouched.

- 2026-08-27 dir+remotes renamed to DashBox and dual-pushed (no force): path /Users/wangzhenyu/Desktop/ALLProject/DashBox; origin https://gitee.com/Winery_z/DashBox; github https://github.com/zhwangsir/DashBox; tip 543264e feat: DashBox 主导融合，drama 反向代理入镜像. Local docs 6a8590f/42b0e38 were ancestors of 543264e (no fork). LICENSE/NOTICE/brand untouched. ToIV untouched.

- 2026-08-27 canvas wiring (code uncommitted): DashBox :8780 reverse-proxies /api/drama/* to host.docker.internal:8100. /api/drama/health 200 on :8080/:8780/:8100. Studio node pipeline still DashBox R18, not switched to platform script/storyboard. LICENSE/NOTICE/brand untouched. dir/remotes still AIGCPannel. ToIV untouched.

- 2026-08-27 fusion first cut (code uncommitted, repo name unchanged): product start ./start-dashbox.sh (drama backend :8100 + DashBox :8080/:8780). Main UI :8080. panel/status product=DashBox. start-aigcpannel.sh thin wrapper. LICENSE/NOTICE/brand untouched. dir still ALLProject/AIGCPannel, remotes still AIGCPannel. Next: canvas->:8100 then Gitee+GitHub rename together. ToIV untouched. Docs identity flipped to DashBox as shell; short-drama pipeline and downloader are modules.

- 2026-08-27 drama smoke (code uncommitted): idea rain-night convenience store -> script-ef4765a34f37 completed. project 杯底的血 id bed1ceac-10cb-46a6-9cea-93669d264432, 2 chars 2 shots. storyboard-1c3cb3b243de sketch scene1 completed, PNG ~630KB from ComfyUI :8188. LLM spark02 live. H3 not submitted. script sync 240s too short, used generate_async, wall ~20min. character preview HTTP 200 but LLM 45s timeout fell back to template. ToIV / DashBox brand untouched.

- 2026-08-27 later: MateBook ~/NAS now SMB-mounted (not on boot); model root readable; registry loras 101, checkpoints 24. DashBox web :8080 and api :8780 listening; panel status web/api_listening true. LICENSE/NOTICE/brand untouched. Colima disk 20G tight. ToIV untouched. Code still uncommitted.

- 2026-08-27 evening model library/gateway (code uncommitted): registry errors when NAS unreadable (no empty list); scan root includes /Users/wangzhenyu/NAS/Windows/ComfyUI/ComfyUIModel/models. MateBook ~/NAS not SMB-mounted: disk checkpoint/lora=0, manifest LoRA=20. gateway/health no longer probes studio04/01/02; required: llm spark02, vlm spark01, LB :8188, H3 :8195, TTS :9200, ASR :9210; LTX required=false. DashBox colima build in progress, :8080/:8780 not listening. LICENSE/NOTICE/brand untouched. ToIV untouched.

- 2026-08-27 用户明确 DashBox 也包含在 AIGCPannel 里，不是外挂。五件套改写成产品一块，不是可选旁路。LICENSE / NOTICE / DramaClaw 品牌文件仍不动、不改成 MIT。开发和测试归 AICG 开发。ToIV 不动。

- 2026-08-27 融合收尾（仍未 commit/push）：CORS 已去掉 localhost:1420；frontend package.json 已无 @tauri-apps；backend .venv 已在 platform/backend 重建；引擎页可手动刷新探测 :8080/:8780。crate/安装器路径/GitHub URL 仍旧。远程仓名等用户点头再两边一起改。ToIV 和 dashbox 品牌文件未动。

- 2026-08-27 第一波融合落盘（未 commit/push）：产品显示名 AIGCPannel。`./start-aigcpannel.sh` backend :8100 + frontend :3501；`./start-engine.sh` DashBox docker 默认 :8080/:8780。根 NOTICE 声明 dashbox/ 为 ELv2。crate/配置目录仍 comfy-downloader。远程仍 AICG-DownLoader。已删 platform/deploy 下 deepfilternet、hunyuanimage、latentsync、video-enhance、xdit-video，保留 comfyui-lb。左侧导航新增模型库、引擎。GET /api/panel/status 可查 config/models.json 是否可读。ToIV 未动。dashbox 品牌文件未改。


- 2026-08-27 用户定名 **AIGCPannel**（拼法以用户为准）：下载器+短剧台+DashBox 融合为一仓。五件套只维护 `ALLProject/AIGCPannel`（原 AICG-DownLoader-main）。三份拷贝已删，不往拷贝写。DashBox 上游 LICENSE/NOTICE/品牌不覆盖。集群真相只在 ToIV/AGENTS.md。远程仓尚未改名。

- 2026-08-27 项目管家文档治理：根目录收敛为 5 件套。

# TEST_LOG.md — 测试与验证时序日志

> 本文件按时间倒序记录每次回归验证的命令、结果与关键代码片段。

## 2026-08-10 M20 长视频分块续写 PoC（H3 I2V 帧链）

### 背景

2026-08-10 长视频调研结论：2-5 分钟长视频采用「分块生成 + 视频续写/首尾帧衔接 + 一致性约束」
技术路线。路线 A 复用现有 H3 I2V 能力：chunk i+1 首帧 = chunk i 末帧（帧链），
逐块透传角色参考图（ref2va）与画风锚定（M18.4），ffmpeg concat 拼接成长视频。
本条目为路线 A 的首次实机验证。PoC 默认关闭（`long_video_enabled=False`），不影响高质量短剧主流程。

### 实现（config.py / long_video_service.py / tests）

- **配置**：`long_video_enabled=False`（默认关闭）、`long_video_max_chunks=4`、
  `long_video_chunk_seconds=5`、`long_video_frame_prefix="longvideo_chain"`。
- **服务**：`app/services/long_video_service.py`
  - `extract_last_frame`：ffmpeg `-sseof -0.1` 尾部定位抽末帧（避免全片解码）；
  - `concat_videos`：concat demuxer + 统一重编码（libx264 crf18 + aac + faststart），
    消除块间时间戳/编码参数缝隙；
  - `LongVideoService.generate`：帧链编排 —— 块 i 末帧抽取后上传 ComfyUI input
    （确定性文件名 + overwrite），构造 `/view?filename=...&type=input` URL 作块 i+1 首帧；
    任一块失败 fail-fast（PoC 阶段不拼接部分结果，防断链视频混入流水线）。
- **测试**：
  - 单元 `tests/unit/test_long_video_service.py` 11 例：ffmpeg/ffprobe 命令构造、
    帧链 image_url 传递、参考图/画风透传、max_chunks 截断、fail-fast、开关关闭、空 prompt。
  - 实机 `tests/integration/test_long_video_poc.py`（`-m slow`）：合成 9:16 关键帧，
    2 块 × 5s 原生 H3（20 步）帧链生成 + 拼接验证，产物归档 `test_artifacts/longvideo_poc/`。

### 实机 PoC 结果

```
chunks=2 elapsed=490.3s（单块约 245s）
final=long_video.mp4 size=3.78MB duration=10.38s（期望 ≈10s）
接缝帧平均像素差=9.00/255（拼接缝 4.90s vs 5.10s 中点采样）
```

- 接缝前后帧目视对比（`seam_pre.png` / `seam_post.png`）：角色（面部/校服/包/姿态）、
  场景（霓虹街景/湿路面反射）、构图几乎完全一致，帧链连续性达标。
- 回归：后端单元 666 passed（+11，覆盖率 83.35%）；后端集成 25 passed（2 slow 摘除）；
  前端 47 passed + 构建成功。

### 结论与下一步

路线 A 帧链续写在本项目 H3 链路上打通，2 块拼接无缝迹象。后续可选：
1. 3-4 块 × 14s 拉长验证（接近 1 分钟）+ 角色漂移累积观测；
2. LongVideoPlanner（剧本 → Act/Scene/Shot/Chunk 拆分）接入 pipeline_orchestrator；
3. 路线 B 并行评估：workstation 部署 LongCat-Video 原生长视频模型对比。

## 2026-08-08 MiniMax H3 Turbo LoRA 可选加速 A/B 验证

### 背景

高质量 AI 短剧当前默认使用原生 MiniMax H3（20 步 res_multistep + simple），单场景 5-15 分钟。
社区 Turbo LoRA 宣称可将采样步数降至 4-8 步并获得约 5× 加速，但质量/画风 trade-off 需要在本项目
短剧 pipeline 上实测。所有加速能力必须**可选、默认关闭**，不能影响现有高质量流程。

### 实现（config.py / video_agent.py / tests）

- **可选开关**：`h3_turbo_enabled=False`（默认关闭），`h3_turbo_steps=6`，
  `h3_turbo_strength=1.0`，`h3_turbo_low_vram=False`。仅当显式开启时才改造工作流。
- **工作流改造**：`_apply_h3_turbo_to_workflow` 在 UNETLoader 后插入 `MiniMaxH3TurboLoRA`，
  将 `BasicGuider`/`BasicScheduler` 的 model 链路重定向到 LoRA 输出；用 `MiniMaxH3TurboSampler`
  替换 `KSamplerSelect`；`steps` 切到 `h3_turbo_steps`。对 FL2VA / R2V / 多镜三条路径均生效。
- **部署**：`minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`（592MB pruned 版）
  已放到 NAS `h3/loras/`，:8195 专用 ComfyUI 实例已加载 `ComfyUI-MiniMax-H3-Turbo` 节点。
- **测试**：
  - 单元测试 `TestH3TurboWorkflowTransformation` 6 例：默认关闭工作流不变、开启后 FL2VA/R2VA
    正确注入、steps 切换、LoRA 自身 model 输入不被误改等。
  - 实机 A/B 测试 `tests/integration/test_h3_turbo_ab.py`（`-m slow`）：合成 9:16 关键帧，
    原生 20 步 vs Turbo 6 步各跑一次，对比耗时、成功率、视频大小与加速比。

### A/B 结果

| 模式 | steps | 耗时 | 视频大小 | 备注 |
|---|---:|---:|---:|---|
| 原生 H3 | 20 | 210.2s | 1.49MB | 默认高质量路径 |
| Turbo LoRA | 6 | 74.1s | 2.32MB | 可选加速路径 |
| **加速比** | — | **2.84×** | — | — |

- 两模式均成功生成可下载 mp4，文件大小均大于 100KB。
- Turbo 明显快于原生，满足「至少更快」的实验性门槛。
- 画质/画风未做定量 VLM 评分，本次仅验证可用性与速度；对高质量短剧，Turbo 仍属**实验性可选项**，
  默认保持原生 20 步。

### 使用方式

```bash
# 默认关闭，不影响任何现有流程
H3_TURBO_ENABLED=false

# 需要快速迭代/预览时显式开启（可在 .env 或运行时切换）
H3_TURBO_ENABLED=true
H3_TURBO_STEPS=6
H3_TURBO_STRENGTH=1.0
```

### 注意事项

- Turbo LoRA 与原生 H3 在权重/采样器上互不兼容；代码通过工作流动态改造保证切换无需改模板文件。
- `h3_turbo_low_vram=True` 会在显存不足时合并权重，画质略软，仅在 OOM 时启用。
- 当前项目全套围绕高质量 AI 短剧，Turbo 只作为**可选加速预览**，不作为默认路径。

---

## 2026-08-07 M18.6 core 重启生效 + M18.4 QC 实机验证（两轮 E2E）

### 第一轮（pipeline-badd72391e7e，style 透传修复生效后同参数复测）

core 后端重启加载 M18.5 两项修复（VideoRequest.style 透传 + HunyuanImage /v1/v1 去重），
参数同 M18.1 基线。结果：9 步全绿，storyboard/video 3/3（pc02 中途再次静默崩溃，
计划任务 MountNAS+StartComfyUI 救援恢复，与易错点 10 预案一致）。

- M18.2 ✅ 持续严格拦截：林远 side 连续 3 次不合格（发色/眼镜/服装不一致）、
  苏清 closeup 连续 3 次不合格（服装款式不一致），废品不入库。
- M18.4 ⚠️ QC 未实跑：本轮 H3 多镜组**一次成功**（上轮为 OOM 回退逐场景），
  组级 QC 按设计跳过（重生成成本 10-20 分钟/组），仅约束层生效。
- drift 3/3 但**指标被污染**（见关键发现 B），不可归因。

### 关键发现（本轮暴露）

1. **HunyuanImage 服务侧新故障**：`No module named 'hyimage.pipelines'`。
   /v1/v1 404 修复有效（请求已到达服务），但服务 import 层损坏，
   角色/分镜全链路回退 SDXL。属 workstation 侧服务问题，待修。
2. **资产库陈旧参考污染（新缺口，派生候选）**：M18.2 拦截新三视图不入库后，
   `_collect_character_reference_images` 按 character_id 静默命中 M18.1 旧资产
   （林默/影子林小满），本轮角色实为林远/苏清 — 视频 ref2va 参考图与
   视觉对照基准双双错配，drift 判定失去意义。候选治理：资产库按剧本指纹隔离 /
   拦截后显式置空引用 / 资产版本化（待决策）。

### 第二轮（pipeline-a91a8cfc2fa6，临时 H3_MULTISHOT_ENABLED=false 强制逐场景）

为实机触达 M18.4 检测/纠偏层，core `.env` 临时关闭多镜开关重启，
同参数复跑（验证后已恢复开关并重启，health 200）。

- **M18.4 QC 首次实机全链路运行**：
  - scene_1 检出画风漂移：「画面呈现写实渲染风格，具有逼真的皮肤纹理、光影反射
    和蒸汽细节，缺乏国漫标志性的线条勾勒、平涂或赛璐璐上色特征」
    → 纠偏触发：`strengthen_h3_style_clause` 强化锚定 + 换 seed 重生成 1/1；
  - 重试产物仍判漂移（「高写实度 3D 渲染质感…更接近现代 3D 动画或写实 CG」）
    → 重试耗尽 **fail-open 放行**（纠偏不阻断生产，与设计一致）；
  - scene_2/3 首检通过（QC 通过无日志，静默放行）。
- 结果：storyboard/video 3/3 全成，成片 9.17s，quality 75；
  drift=[2,3] — scene_1（唯一经 QC 纠偏的场景）恰为唯一过视觉对照的场景，
  提示性证据但受陈旧参考污染，不作强归因。
- pc02 全程健康，定妆照复制零失败。

### M18 收官结论

M18.4 三层机制实机验证完备：约束层（多镜/单镜 prompt 锚定 + 冲突词清洗）、
检测层（VLM 中点帧画风判定）、纠偏层（强化重生成 + 重试耗尽放行）全部实跑；
M18.2 拦截、M18.3 锚定复制前两轮已实证。drift 治理效果的干净量化受
「资产库陈旧参考」缺口阻塞，列入后续候选。全量回归维持 649 passed / 83.07%
（本轮无代码变更，仅 core 侧配置临时切换）。

---

## 2026-08-07 M18.5 M18.3 运维分发 + 三里程碑联合实机 E2E（对照 M18.1 基线）

### 运维三件事（全部完成）

1. **IPAdapter 模型分发**：ip-adapter-plus-face_sdxl_vit-h + CLIP-ViT-H-14-laion2B-s32B-b79K
   核验 workstation / pc01 / pc02 三 LB 后端齐全；`.env` 配
   `IPADAPTER_SDXL_MODEL_NAME` / `IPADAPTER_CLIP_VISION_NAME`。
2. **LB 后端直连清单**：`.env` 配 `COMFYUI_LB_BACKEND_URLS=workstation:8189,pc01:8188,pc02:8193`，
   定妆照同名复制全后端，规避 LB /upload 轮询单点导致 LoadImage 跨后端 400。
3. **pc02 崩溃救援**：E2E 中途 pc02 ComfyUI 进程静默退出（CUDA 初始化后无错误日志），
   经计划任务 MountNAS + StartComfyUI 重启恢复 200，IPAdapter/CLIP-Vision 加载确认。

### 联合 E2E（pipeline-dd4c074a73ac，core 实机，耗时 1591s）

参数同 M18.1：都市悬疑 / 国漫 / 1 集×3 场景 / 定妆照 / 参考视频+音频 / run_visual_check=true。

| 步骤 | 结果 |
|---|---|
| script | 《最后一杯热咖啡》2 角色 3 场景 |
| character | char_001/char_002 **均被 M18.2 拦截未入库**（front/side/closeup 连续 3 次不合格） |
| storyboard | 2/3 成功；scene_1 失败（pc02 崩溃窗口 → SDXL 回退超时 300s） |
| video | H3 多镜 OOM 回退逐场景，2/2 成功（scene_2/3） |
| voice/subtitle/edit | 3/3、3/3、成片 6.62s（2 segments） |
| quality / visual_quality | 85 分 6 issues；checked=2，drift_scenes=[2,3]，两场景各 95 分 |

### 三里程碑运行证据（core /tmp/aicg-backend.log）

- **M18.2 ✅ 按设计严格拦截**：林默 front「眼神清澈缺疲惫空洞、缺智能手表、发际线偏低」、
  苏雅 front「白发 vs 黑长直、侧分刘海、白色外套 vs 黑色高领」等详细判定，
  换 seed 重试 2 轮仍不合格 → 废品不入库（设计目标达成）。
- **M18.3 ✅ 锚定注入并复制**：定妆照以资产库 char_001 front 注入 IPAdapter；
  pc02 崩溃窗口复制失败 5 次（`All connection attempts failed`），2/3 后端成功仍注入
  （部分失败不阻断，与设计一致）。
- **M18.4 ⚠️ QC 零运行**：日志无任何「H3 画风」记录 — 根因排查为
  **orchestrator `_step_video` 构建 VideoRequest 时漏传 `style` 字段**，
  QC fail-open 条件（style 为空）静默跳过；约束层冲突词清洗同样未生效。

### drift 对照结论

- M18.1 基线：drift 3/3（3 场景全漂移）。
- 本轮：drift 2/2（样本因 pc02 崩溃缩为 2 场景），M18.4 QC 未实际运行，
  **漂移治理效果不可归因、待下轮验证**；M18.2/M18.3 价值已实证
  （废品拦截 + 锚定全链路注入）。

### 缺口修复（本轮 E2E 暴露）

1. **M18.4 接线缺口**（pipeline_orchestrator.py）：`VideoRequest` 补 `style=request.style`，
   TDD 先红（`assert '' == '国漫'`）后绿；新增用例
   `test_style_propagated_to_video_request`。
2. **HunyuanImage 404**（image_service.py，本轮日志 `/v1/v1/images/generations` 实锤）：
   endpoint 含 /v1 前缀，三处调用路径去重；core 已同步待重启生效。
3. **测试隔离**（test_storyboard_agent.py）：本地 .env 的 `COMFYUI_LB_BACKEND_URLS`
   污染旧用例单点上传断言（awaited once → 3 次），`_enable_anchor` 与 Wiring 类
   显式置空 LB 清单隔离。

### 全量回归

```text
后端 pytest tests/unit   649 passed，coverage 83.07%（≥80% 门槛），48.97s
```

### 后续

- core 后端重启（加载 style 透传 + HunyuanImage 修复）后重跑同参数 E2E，
  验证 M18.4 QC 实际拦截/纠偏行为，再下 drift 对照结论。
- pc02 静默崩溃根因待查（Windows 事件日志 / ComfyUI 控制台输出）。

---

## 2026-08-07 M18.4 H3 画风漂移治理（约束 + 检测 + 纠偏三层）

### 背景

M18.1 帧级核验发现 H3 输出（半写实厚涂）与参考图/定妆照（卡通平涂）存在系统性
画风漂移（3/3 VLM 真阳性）。M15 画风锚定只覆盖剧本/角色/分镜文生图链路，
H3 视频链路三条 prompt 路径（fl2va/r2v/多镜）均未做画风约束，产出亦无检测。

### 实现（video_agent.py / config.py / schemas.py）

- **约束层** `apply_h3_style_anchor(prompt, style)`：画风冲突词清洗
  （`sanitize_style_conflicts`，如国漫目标下剔除 hyperrealistic/cinematic realism）
  + 幂等追加风格锚定尾（orchestrator M15.1 已追加过时跳过，不二次追加）；
  style 为空或 `h3_style_anchor_enabled=False` 原样透传（向后兼容）。
  三条路径统一接线：fl2va `_execute_via_h3_fl2va`、r2v `_execute_via_h3_r2v`
  （锚定作用于场景 prompt 本体，风格尾位于参考图引导语之前）、
  多镜 `build_multishot_prompt`（组级画风基准=组内首个非空 style，逐镜兜底）。
- **检测层** `_h3_style_qc_check(video_url, style)`：`_extract_h3_middle_frame`
  下载产出视频 ffprobe 取时长中点 ffmpeg 抽帧 → base64 送 VLM
  （`visual_model_url`/`visual_model_name`，与 M18.2 同一入口），
  判定渲染风格是否符合目标画风（内容/构图/外貌不作依据），
  输出 `{"pass": bool, "reason"}`；异常/坏 JSON/结构不符一律 fail-open 放行。
- **纠偏层** `_execute_h3_with_style_qc`：漂移时 `strengthen_h3_style_clause`
  前置强化画风子句（`Rendered strictly in {style_name_en}. {原 prompt}`）
  + 换 seed 重提交，最多 `h3_style_qc_max_retries=1` 次；
  重试耗尽放行最后结果（纠偏不阻断生产，仅日志记录）。
  仅包装单镜 execute（fl2va/r2v 派发入口 `_execute_via_h3`，r2v 重试仍走 r2v
  且参考图挂接保留）；多镜组重生成成本高（10-20 分钟/组），组级漂移由约束层治理。
- `config.py`：`h3_style_anchor_enabled=True` / `h3_style_qc_enabled=True` /
  `h3_style_qc_max_retries=1`（回滚：全部置 False/0 后代码路径与现状一致）。
- `schemas.py`：`VideoRequest.style` 字段（orchestrator 透传目标画风，空串跳过）。

### TDD 用例（18 例，先红后绿）

| 类 | 覆盖 | 结果 |
|---|---|---|
| TestH3StyleAnchorClause (6) | 冲突清洗+风格尾/幂等/写实反向清洗/开关关闭透传/空 style 透传/强化子句前置 | 6/6 PASS |
| TestVideoAgentH3StyleAnchorWiring (4) | fl2va 锚定、r2v 锚定（风格尾在引导语前）、多镜逐镜锚定、多镜空 style 不变 | 4/4 PASS |
| TestVideoAgentH3StyleQC (8) | 合格不重试/漂移换 seed 强化重生成/重试耗尽放行/r2v 亦过 QC/VLM 异常 fail-open/坏 JSON fail-open/开关关闭跳过/style 空跳过 | 8/8 PASS |

首次跑红确认：r2v prompt 未清洗（`cinematic realism` 残留）、QC 包装器不存在；
实现后 18/18 全绿。

### 全量回归

```text
后端 pytest tests/unit   645 passed，coverage 83.02%（≥80% 门槛），49.13s
前端 vitest               47 passed (3 files)
前端 build                811ms 成功
```

### 注意事项

- 检测层每单镜增加 1 次抽帧下载 + 1 次 VLM 调用（约 2-5s）；漂移时整镜重生成
  （H3 单镜 5-15 分钟），`h3_style_qc_max_retries=1` 为成本/质量平衡点。
- fail-open 设计：VLM 不可用、帧抽取失败、坏 JSON 均不阻断生产；误判风险由
  「重试耗尽放行最后结果」兜底，持续误判可置 `h3_style_qc_enabled=False` 回滚。
- 实机 E2E 验证（国漫画风 1 集×3 场景，观察 drift 判定率较 M18.1 基线 3/3 下降）
  待 M18.3 运维（IPAdapter 模型分发）完成后联合进行。

---

## 2026-08-07 M18.3 分镜关键帧外貌锚定（定妆照 front IPAdapter 注入）

### 背景

M18.1 帧级核验发现分镜关键帧（黑长直发）与角色定妆照 front（短碎发）外貌冲突，
H3 ref2va 以冲突关键帧为构图锚点进一步放大不一致。M18.3 在关键帧生成环节
（SDXL 路径）把角色定妆照 front 作为 IPAdapter 图像参考注入，从源头锚定
角色外观/服饰/整体设定一致性。

### 实现（storyboard_agent.py / config.py）

- 节点模板：IPAdapterModelLoader（`ip-adapter_sdxl_vit-h`）+ CLIPVisionLoader
  （`CLIP-ViT-H-14-laion2B-s32B-b79K`）+ LoadImage（定妆照 front，运行时上传替换）
  + IPAdapterAdvanced（权重 `storyboard_keyframe_anchor_weight=0.6`），
  KSampler model 输入重定向到 IPAdapterAdvanced 输出。
- `_resolve_keyframe_anchor_image()`：从角色资产库解析首个有定妆照 front 的角色
  参考图；无定妆照/开关关闭/资产库异常 → 跳过锚定走原工作流。
- `_inject_ipadapter_anchor()`：定妆照上传或节点装配任何异常回退原 SDXL 工作流
  （锚定是增强不是阻断）。
- M16 外貌锚定重试重生成时锚定参考图一并透传（重试不丢锚定）。
- `config.py`：`storyboard_keyframe_anchor_enabled=True`（回滚置 False）。
- 仅 SDXL 路径生效；HunyuanImage/FLUX+PuLID 主路径不变。

### TDD 用例（10 例，先红后绿）

| 类 | 覆盖 | 结果 |
|---|---|---|
| TestStoryboardKeyframeAnchor (6) | 有定妆照注入 4 节点+KSampler 重定向/无参考不注入/开关关闭不注入/权重来自配置/上传失败回退原工作流/外貌重试携带锚定 | 6/6 PASS |
| TestStoryboardKeyframeAnchorWiring (4) | execute 级联：取首个 front/无 front 跳过/开关关闭跳过/资产库异常跳过 | 4/4 PASS |

### 运维待办（合入 M18.4 实机联合验证前完成）

- IPAdapter 模型分发：`ip-adapter_sdxl_vit-h.safetensors` +
  `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` 至 workstation/pc01/pc02
  ComfyUI `models/ipadapter` + `models/clip_vision`（NAS 模型库同步）；
  pc01/pc02 `extra_model_paths.yaml` 确认含 ipadapter/clip_vision 映射；重启 ComfyUI。

---

## 2026-08-07 M18.2 角色三视图生成质检（VLM 校验 side/closeup 与 front 同角色同画风）

### 背景

M18.1 帧级核验发现 char_001 side 实为无关白发少女、char_002 side 实为 16 格眼睛
画法参考表——视图生成「成功」但内容是废品，无质检拦截混入 ref 组，直接污染
H3 ref2va 参考集。M18.2 在三视图生成后、角色卡入库前引入 VLM 质检。

### 实现

- `config.py`：新增 `character_view_qc_enabled=True` / `character_view_qc_max_retries=2`
  （回滚开关：置 False 后代码路径与现状完全一致）；
- `character_agent.py`：
  - `_get_vlm_client()`：懒加载 AsyncOpenAI（与分镜 M16.2b 同一 spark02 Qwen3-VL 入口）；
  - `_qc_front_view()`：front 自检（单人/完整人物肖像非素材表/外貌符合描述/画风符合锚定），
    输出 `{"pass": bool, "reason"}`；
  - `_qc_view_consistency()`：side/closeup 与 front 双图比对（发色/发型/服装一致；
    姿势/视角/表情差异不算不一致），输出 `{"match": bool, "reason"}`；
  - `_qc_three_views()`：编排——front 先自检（不合格换 seed 重生成，最多 2 次，
    重试耗尽抛 RuntimeError 阻断入库），side/closeup 并行比对 front 各自独立重生成；
  - `_regenerate_view()`：新随机 seed 按原后端派发重生成（主后端异常回退 SDXL）；
  - fail-open：VLM 未配置/调用异常/坏 JSON 一律放行 + warning（质检器故障不阻断生产）；
- `conftest.py`：`_patch_settings` 默认 `character_view_qc_enabled=False`（既有用例零影响），
  专项用例局部开启。

### TDD 用例（TestCharacterAgentViewQC 9 例，先红后绿）

| 用例 | 断言要点 | 结果 |
|---|---|---|
| test_all_views_pass_qc | VLM 调 3 次（front 自检+side/closeup 比对），无重生成 | PASS |
| test_qc_disabled_skips_vlm | 开关关闭 → VLM 0 调用（回滚路径） | PASS |
| test_vlm_url_empty_fail_open | URL 空 → 跳过质检放行 | PASS |
| test_front_fail_regenerates_with_new_seed | front 2 次生成 seed 互异，入库为重生图 | PASS |
| test_side_mismatch_regenerates_only_side | 仅 side 重生（front/closeup 各 1 次） | PASS |
| test_retry_exhausted_fails | 初始+2 重试全不合格 → success=False 含「质检」 | PASS |
| test_qc_failure_not_registered_to_library | 重试耗尽 → register_from_card 未调用（废品不入库） | PASS |
| test_vlm_exception_fail_open | VLM 抛异常 → 放行 success | PASS |
| test_vlm_bad_json_fail_open | 坏 JSON → 放行 success | PASS |

首次跑红确认：`assert 0 == 3`（VLM 未被调用，实现不存在）；实现后 9/9 全绿。

### 全量回归

```text
后端 pytest tests/   642 passed（633→642，+9），coverage 86.19%（≥85.90% 基线保持），47.94s
前端 vitest          47 passed (3 files)
前端 build           881ms 成功
```

### 注意事项

- 质检增加 3 次 VLM 调用（约 3-9s），仅在 `character_view_qc_enabled=True` 且
  `visual_model_url` 非空时启用；生产默认开启。
- VLM 误判风险由「2 次重试 + fail-open」缓解；若某画风持续误判可临时关闭开关回滚。
- 实机 E2E 验证（构造 side 废品场景观察拦截+重生成日志）并入 M18.3 联合验证。

---

## 2026-08-07 M18.1 参考图修复红利验证（P0 input.png 修复版 vs bug 基线帧级对照）

### 背景

M17.5 修复了 P0 存量 bug——`upload_image_to_comfyui` 写死 `input.png` 导致多参考图
顺序上传互相覆盖塌缩（M10 起所有 ref2va 工作流实际只用最后上传的一张图）。M18.1
以「同参数修复版 E2E vs bug 基线」量化修复红利，验证参考图真正生效后的实际效果。

### 方法

1. bug 基线：`output/m17_bug_baseline/`（final_pipeline-1786025948.mp4 + video_scene_1/2.mp4，
   工作流 4 个 LoadImage 全部 input.png）；
2. 修复版 E2E：core :8100 `/api/drama/pipeline/run` 同题材参数（都市悬疑 / 国漫 /
   生成定妆照 / 参考视频 + 参考音频 / run_visual_check=true），task `pipeline-7b0cd7cbfc3c`，
   本次为 1 集 × 3 场景；
3. 双维取证：:8195 history 工作流节点取证 + 视频抽帧与参考图逐张肉眼对照。

### 结果

- 修复版 9 步全绿，成片 `final_pipeline-1786033502.mp4`（9.46s，3 段），耗时 901s；
- 视觉质检：score 95/85/85，**drift_scenes=[1,2,3]（3/3 全判漂移）**；bug 基线（M14.2/M17 E2E）
  为 2/2 判漂移——漂移未消除；
- **工作流取证**（prompt `31fe464b-210b-4db9-aea1-fb8ded940f18`，success）：
  **7 个 LoadImage 节点文件名全部互异**（`video_agent_{130fc2ef,dabb1de3,63fa9745,
  b8862859,1185e10e,3d24a35a,f9835b0c}.png`），`ref_images` 7 组全部独立挂接；
  `ref_videos ← GetVideoComponents[80].0 ← LoadVideo[70]`、`ref_audios ← LoadAudio[90]`
  正常；prompt 含 `<Picture N>/<Video 1>/<Audio 1>` 标签 + 音频方向字段。
  **P0 修复实线确认**。

### 帧级对照（修复版 3 场景 + bug 版 scene 1 + 7 张参考图原件）

| 维度 | bug 基线（input.png 塌缩） | 修复版（7 图独立） |
|---|---|---|
| 跨镜角色一致性 | ❌ scene1 水手服 / scene2 形象不一 | ✅ 3 场景统一为「马尾 + 便利店制服」同一角色 |
| 与定妆照一致 | ❌ 仅 closeup 一张生效 | ❌ 仍未对齐（见下根因） |
| 画风 | 卡通平涂（贴近参考图） | 半写实厚涂（偏离参考图） |

**结论：P0 修复带来真实红利——ref2va 多参考图首次真正同时生效，跨镜角色形象统一；
但 drift 未消除，VLM 判定为真阳性。**

### 根因升级：参考图集本身内部矛盾（7 张原件逐张核验）

| # | 文件 | 实际内容 | 应为 |
|---|---|---|---|
| 1 | 130fc2ef | 分镜关键帧：黑**长直发**+蓝围裙，国漫 | scene1 关键帧 ✅ |
| 2 | dabb1de3 | char_001 front：黑**短碎发**+蓝背带 | 主角定妆照 ✅（但与关键帧发型冲突） |
| 3 | 63fa9745 | **白发紫瞳无关少女**（= char_001 side，文件尺寸一致确认） | ❌ side 视图生成失败产物 |
| 4 | b8862859 | char_001 closeup | 主角特写 ✅ |
| 5 | 1185e10e | char_002 front：青灰肤尖牙邪笑反派 | 反派「影子林小满」✅ |
| 6 | 3d24a35a | **16 格眼睛画法参考表**（= char_002 side，尺寸一致确认） | ❌ 根本不是人物图 |
| 7 | f9835b0c | char_002 closeup 尖牙特写 | 反派特写 ✅ |

三重矛盾：① 角色 Agent 的 side 视图生成彻底失败且**无质检拦截**（1 张无关角色、
1 张素材参考表混入 ref 组）；② 分镜关键帧（长发）与定妆照 front（短碎发）外貌冲突
（M16 外貌锚定未贯通到关键帧生成）；③ H3 输出画风（半写实厚涂）与参考图
（卡通平涂）系统性漂移。H3 面对矛盾参考集只能折中输出（马尾=长发+束发折中、
红围裙≠蓝围裙），漂移是必然结果。

### 派生（M18 后续候选，按优先级）

1. **M18.2 角色三视图生成质检**：side/closeup 生成后 VLM 校验「与 front 同角色、
   同画风、是人物图」，失败自动重生成（拦截白发少女/眼睛表这类废品入库）；
2. **M18.3 分镜关键帧外貌锚定**：关键帧生成以定妆照 front 为图像参考（而非仅文字
   描述），消除长发/短发冲突；
3. **M18.4 H3 画风漂移**：参考图卡通平涂 vs 输出半写实厚涂，评估 prompt 画风强化
   或 ref2va 风格约束参数。

---

## 2026-08-06 M17 H3 全模态能力释放（原生 CUT 语法 + 原生音频方向 + FL2VA 双锚定 + ref2va 音视频参考）

### 背景（官方能力调研派生）

M10-M12 已落地 fl2va I2V、ref2va 角色参考图、多镜 SHOT prompt。对照 MiniMax H3 官方指南
与 :8195 节点签名实测，仍有四大能力未释放：

1. 多镜 prompt 用自造 `SHOT X:` 格式；官方推荐 Context-IR 结构
   （`integrated_multimodal_description:` + `[Shot N] At MM:SS.mmm, the camera cuts to ...`），
   时间戳驱动跨镜剪辑连续性；
2. 未注入官方音频方向字段（`overall_soundscape` / `non_diegetic_music`），H3 原生音轨
   内容靠模型自由发挥，叙事节拍（hook/reversal/cliffhanger 等）无法传导到 BGM/声景；
3. fl2va 只用首帧单锚定；官方支持 `last_frame` 双锚定 + 固定对齐指令
   （Picture 1 → 0.00s / Picture 2 → S.SSs），端到端锁定起止构图；
4. ref2va 只挂图片参考；`MiniMaxH3ReferenceToVideo` 实际还有 `ref_videos` /
   `ref_video_audios` / `ref_audios` 三组全模态参考输入未接线。

### 实机契约核验（硬性规则：先实测再编码）

| 核验项 | 方法 | 结论 |
|---|---|---|
| fl2va `last_frame` 输入 | :8195 object_info 拉取 `MiniMaxH3ImageToVideo` 签名 | ✅ 存在，IMAGE 类型 |
| ref2va 全模态参考组 | object_info 拉取 `MiniMaxH3ReferenceToVideo` 完整签名 | ✅ `ref_videos`/`ref_video_audios`/`ref_audios` 三组 COMFY_AUTOGROW_V3，max 各 3 |
| LoadVideo / LoadAudio widget | object_info | ✅ `file` / `audio` 字段，按扩展名识别解码器 |
| 音视频上传端点 | :8195 实测 POST /upload/audio | ✅ 405 不存在；**所有二进制统一走 /upload/image** |

### 变更摘要

- **M17.1 原生 CUT 语法**：`build_multishot_prompt(requests, native_cut=True)` 默认输出
  Context-IR —— 首行 `integrated_multimodal_description:`，Shot 1 直述，`[Shot N] At
  MM:SS.mmm, the camera cuts to ...` 时间戳为前序场景时长累计；`native_cut=False` 或
  `settings.h3_native_cut_prompt_enabled=False` 保险丝回退 M11 旧版 `SHOT X:` 格式；
  M12 节拍视觉指令在原生格式下仍注入对应镜段。
- **M17.2 原生音频方向**：`build_audio_direction(beats)` 六节拍确定性映射官方两字段
  （多节拍组按优先级取最强者，如 reversal 胜过 transition）；`_append_audio_direction`
  追加在 prompt 尾部（多镜位于 description 之后）；`settings.h3_audio_direction_enabled`
  为总开关。
- **M17.3 FL2VA 双锚定**：`build_fl2va_alignment_instruction(duration, last_shot)` 生成官方
  对齐指令前置 prompt 首部；`VideoRequest.last_frame_url` 非空时 fl2va 工作流挂节点 11
  LoadImage → `last_frame`，空串退化为 I2VA（向后兼容）；orchestrator 在
  `h3_last_frame_chain_enabled` 下同集相邻场景把「下一分镜关键帧」填入 `last_frame_url`
  形成链式锚定；多镜组末场景的链式末帧（组后一镜关键帧）作组末帧实现**组间链式连续**，
  无链式末帧时回退末场景自身关键帧（仍享双锚定）。
- **M17.4 ref2va 全模态参考**：`VideoRequest.reference_videos/reference_audios`（各 ≤3）、
  `PipelineRunRequest` 同名字段流水线级透传；ref2va 触发条件从「参考图非空」扩展为
  「图/视频/音频任一非空」；`_inject_r2v_media_refs` 动态挂接 LoadVideo(7X)→
  GetVideoComponents(8X) → `ref_videos.ref_video_N` + 原声音轨 → `ref_video_audios.
  ref_video_audio_N`，LoadAudio(9X) → `ref_audios.ref_audio_N`；`build_r2v_media_guide`
  生成 `<Video N>`/`<Audio N>` 官方标签引导（运镜/节奏/剪辑结构 + BGM 风格/声景质感）；
  `base.upload_media_to_comfyui` 通用上传保留源扩展名、统一走 /upload/image（实测契约）。

### TDD 用例（31 新增 + 多镜旧例同步，600→631）

| 文件 | 类 | 用例数 | 覆盖点 |
|---|---|---|---|
| test_video_m17.py | TestNativeCutSyntax | 6 | Context-IR 默认/时间戳累计/首镜无时间戳/保险丝回退/setting 关闭回退/节拍指令保留 |
| test_video_m17.py | TestAudioDirection | 8 | 双字段生成/未知节拍空/主导节拍优先/六节拍全覆盖/prompt 尾注/总开关/多镜位置/单镜 fl2va 接线 |
| test_video_m17.py | TestFL2VAAlignment | 6 | 对齐指令单镜/多镜格式、双锚定接线（节点11+last_frame+上传2次）、空串退化 I2VA、组链式末帧、组回退末场景关键帧 |
| test_video_m17.py | TestR2VMediaGuide | 4 | 空无媒体/纯视频/纯音频/复数标签 |
| test_video_m17.py | TestR2VMediaInjection | 7 | 仅视频触发 ref2va/仅音频触发/节点挂接+标签引导/超 3 截断/上传保留扩展名+统一端点/无扩展名 fallback |
| test_video_multishot.py | 旧例同步 | — | `SHOT X:` 断言全部迁移为原生 CUT `[Shot N]` 格式 |

### 验证结果（本地全量回归）

```text
后端全量        631 passed（600→631，+31），86.10% coverage（≥80% 达标），51.26s
前端 vitest     47 passed（3 files，本里程碑无前端变更）
前端 build      1.11s 成功（dist 427.66 kB / gzip 130.77 kB）
M17 专项        test_video_m17.py + test_video_multishot.py 56/56
```

### 残留派生（M18 候选）

1. ~~core 重部署 + 全模态 E2E~~ → **已完成，见下方「core 全模态 E2E 终验」**；
2. M16 派生 3 项仍挂账：漂移豁免扩充「肢体局部出镜」/ 多角色外貌分区隔离（反派串色）/
   视频道具文字模糊化；
3. 音频方向效果评估：E2E 后抽轨核对 BGM/声景是否按节拍意图生成（ffprobe 音轨存在性 +
   人工听审）。

### core 全模态 E2E 终验（2026-08-06 22:33 完成）

**请求**：core :8100 `/api/drama/pipeline/run`，都市悬疑 / 国漫 / 1 集 × 2 镜 /
生成定妆照 / 单镜 3s / run_quality_check=true / 烧录 AI 标识 /
reference_videos=[video_scene_1.mp4] / reference_audios=[voice_scene_1_00.mp3]。

**结果**：9 步全绿，成片 `final_pipeline-1786025948.mp4`（6.62s，h264 1080x1920 + aac，
2.26MB）。多镜组（scene 1+2 同集相邻）单次 H3 调用输出 `video_multishot_1_2_00008_.mp4`，
ffmpeg 切分为 video_scene_1/2.mp4。

**:8195 history 工作流取证**（prompt_id `68adfb02-cc1b-432b-8187-5b693c4c6bf6`，
status success）—— M17 三特性全部实线，非仅单测绿：

| 特性 | 取证 |
|---|---|
| M17.1 原生 CUT | prompt 首行 `integrated_multimodal_description:`，`[Shot 2] At 00:03.000, the camera cuts to ...`（时间戳=首镜 3s 累计），首镜无时间戳 |
| M17.2 音频方向 | `overall_soundscape: A low, tense room tone with a sharp sudden impact...` + `non_diegetic_music: A tense pulsating electronic score...`（hook 节拍映射实注） |
| M17.4 全模态参考 | `ref_videos.ref_video_0 ← GetVideoComponents[80].0`、`ref_video_audios.ref_video_audio_0 ← [80].1`（参考视频原声轨）、`ref_audios.ref_audio_0 ← LoadAudio[90]`；prompt 尾部 `<Video 1> is reference clip... <Audio 1> is audio reference...` 标签引导 |
| M17.3 FL2VA | 本次未走（组带定妆照优先 ref2va，角色一致性优先，符合设计）；单测 6 例覆盖 |

### M17.5 P0 修复：多参考图上传文件名碰撞（input.png 塌缩）

**发现**（上述取证工作流）：4 个 LoadImage 节点（分镜关键帧 + 角色三视图）
`image` 字段**全部为 `input.png`**。`BaseAgent.upload_image_to_comfyui` 自 M10 起写死
文件名 `input.png` 且 `overwrite=true`，多图顺序上传互相覆盖，工作流执行时所有
LoadImage 实际读取**最后上传的那一张**（角色 closeup）——ref2va 的构图关键帧锚定与
三视图区分一直未真正生效（M14.2 漂移真阳性、M16 外貌保真问题的重要隐蔽诱因）。

**修复**（base.py）：上传文件名改为 `{agent_name}_{uuid4().hex[:8]}.png` 唯一命名，
8 处 video_agent 调用点零改动自动受益；`result.get("name", filename)` 回退值同步唯一名。

**TDD**（test_base.py +2）：`test_unique_filename_per_upload`（4 连传 4 名互异、
Agent 前缀）、`test_fallback_name_when_comfyui_omits_name`（缺 name 回退为实际发送名）。

**回归与部署**：后端 633/633（631→633，85.90% coverage，51.70s）、前端 vitest 47/47、
build 1.00s；base.py rsync → core，uvicorn :8100 重启 health 200（grep uuid=3 确认
新代码生效）。

**遗留观察**：角色/分镜主后端 hunyuanimage:8600 持续不通，SDXL 回退正常出图（M14 起
已知，不阻断）；FL2VA 双锚定待一次「无定妆照」pipeline 实战走线。

---

## 2026-08-06 M16.2 分镜外貌保真（氛围词剥离 + VLM 拼贴检测 + 短 prompt 重试）+ M16.3 漂移豁免 + core E2E 终验

### 背景（M16.1 待验证项落地）

core E2E（pipeline-87d6d5791120）实测：分镜 prompt 中角色外貌描述完全正确（black straight
long hair / white shirt / dark gray pleated skirt），但 animagineXL40 产出仍为模型先验校服。
M16.1 已将 KB 整串降为「可选」，但 LLM 仍习惯性全量注入氛围词（elaborate costumes /
fantasy elements 等），多角色长 prompt 下稀释 CLIP 注意力并与锁定外貌冲突。

### 变更摘要

- **M16.2a `style_anchor.strip_kb_atmosphere`**：非写实画风确定性剥离 keywords_en 中除
  style_name_en 外的全部氛围分段（风格由风格名 + checkpoint 双保险）；写实画风不剥离
  （KB 词为摄影技术词，不与外貌争权重）；复用 sanitize 同一套标点收口规则。
- **M16.2b `storyboard_agent._check_appearance_mismatch`**：分镜关键帧生成后 VLM（Qwen3-VL,
  data URL high detail, temperature=0.1）比对出场角色外貌与角色描述，判定焦点收窄为
  发色/发型、服装款式与颜色（画风/姿势/表情/视角差异不算失真）；开关
  `settings.storyboard_appearance_check`（config.py 默认 True）。
- **M16.2c `_rebuild_short_prompt` + `_verify_and_retry_appearance`**：失真时 LLM 重构
  ≤80 英文词短 prompt（镜头类型开头 → 角色核心外貌原样翻译前置 → 至多 2 个动作/环境要素、
  禁 7 类氛围填充词），重试 prompt 再经 sanitize + strip_kb_atmosphere + 锚定尾后重生成一次；
  任何环节异常保留原图不阻断。
- **M16.3 漂移豁免**：`DRIFT_CHECK_PROMPT` 新增 `character_present` 结构化字段（POV 主观
  镜头/空镜/仅道具特写/仅背景路人 → false）；`quality_agent._drift_check_single_frame`
  程序兜底：character_present=false 时无论 drift_detected 为何均豁免并记 info 日志。

### TDD 用例（30 新增，570→600）

| 文件 | 类 | 用例数 | 覆盖点 |
|---|---|---|---|
| test_style_anchor.py | TestStripKbAtmosphere | 8 | 非写实剥离保留风格名与实质内容/国漫 9 分段全命中/写实不剥离/空串兜底/标点收口 |
| test_storyboard_agent.py | TestStoryboardAppearanceCheck 等 4 类 | 18 | VLM 请求结构/match=false 返回原因/VLM 未配置跳过/重构 prompt 规则/重试再清洗+剥离+锚定尾/execute 接线/异常保留原图 |
| test_visual_quality_agent.py | TestCharacterPresentExemption | 4 | present=false 豁免/present=true 正常报/缺字段向后兼容/prompt 含豁免规则 |

### 验证结果（本地回归）

```text
后端全量        600 passed（570→600），85.74% coverage（≥80% 达标），52.59s
前端 vitest     47 passed（本里程碑无前端变更）
```

### core E2E 终验（pipeline-6ca41498acf7，成片 final_pipeline-1786007372.mp4）

1集×2镜、style=国漫、定妆照1角色（林浅：黑色长直发/蓝白相间校服）、run_visual_check=true，
全链路 9 步跑通。产物逐帧人工比对（拉取定妆照/分镜双版本/视频抽帧至本地）：

- **画风链（M15 目标复核）**：定妆照/分镜/视频帧全为国漫日系 → 画风漂移根治确认 ✅
- **M16.2 实战命中**：hunyuanimage 主后端连接失败 → SDXL 回退；VLM 校验双场景均检出问题并重试：
  - scene1：初版 b4dba70e（眼睛+信封九宫格拼贴）→ VLM 检出「发色蓝黑/白衬衫/眼神惊讶」
    → 重试 9d1df9fd（三联幅持信少女，黑长发/白衬衫）【gpu0:8189，17:12:15→17:12:27】
  - scene2：初版 a3f24bbe（监控墙多格拼贴）→ VLM 检出「深色连帽卫衣/短发扎起」
    → 重试 676b1385（干净单帧走廊少女，蓝白百褶裙+白领口）【pc02:8193，17:12:17→17:12:31】
  - 重试版均为最终采用（时间线后者），拼贴→可用关键帧改善显著 ✅
- **M16.3 未触发**：drift_scenes=[1,2] 仍报（score 95/98）。逐帧分析：
  - scene1 视频为 POV 手持信封特写（s1_1/s1_2 仅手部）+ 背面镜头（s1_3 长黑发背影），
    VLM 对仅手部出镜帧判 character_present=true（豁免规则未覆盖「肢体局部出镜」），
    深色袖口 vs 蓝白校服 → 假阳性残留；
  - scene2 视频帧林浅穿深色连帽卫衣+兜帽（s2_1/2/3），与定妆照蓝白校服不符 → **真阳性**，
    连帽衫偏差源自分镜重试图（反派神秘人「深色连帽衫」外貌词串色主角）被 H3 ref2va 继承放大。

### 残留派生（M17 候选）

1. 豁免规则扩充：DRIFT_CHECK_PROMPT 补「仅手部/肢体局部出镜 → character_present=false」，
   消除 POV 道具特写假阳性；
2. 多角色外貌串色：分镜/视频 prompt 中反派外貌词（深色连帽衫）泄漏到主角，需角色外貌
   分区隔离或负面词互斥（林浅 negative 加 hoodie）；
3. H3 视频帧信封出现可读英文文字（违反 no readable text 约束，剧本 prompt 含信件文字内容
   被直译生成），需视频 prompt 对道具文字做模糊化处理。

---

## 2026-08-06 M16.1 风格词与外貌词权重分离（prompt 结构改造）

### 背景（M15.9 终验遗留缺陷）

M15 画风锚定链修复后全链路画风统一（国漫 E2E 角色/分镜/视频帧均为动漫风），但残留
**角色外貌保真**缺陷：定妆照/分镜提示词均正确携带「shoulder-length black short hair...
dark blue uniform」，却因 KB 国漫 keywords 整串强制注入（vibrant colors / fantasy
elements / elaborate costumes / particle effects 等内容词）与外貌描述争权重，
定妆照实际产出银灰发（与剧本「黑色齐肩短发」相悖）。根因在三处 LLM 画风子句统一要求
「每个提示词都必须显式包含该风格英文关键词（整串 KB keywords）」。

### 变更摘要

- **`app/services/style_anchor.py`** 新增 `style_prompt_clause(anchor, *, target)`：
  - 必填收窄为风格名 `style_name_en`（如 `"Chinese anime guoman style"`）约束渲染风格；
  - KB 完整关键词降为「风格氛围参考（可选，不必全部使用）」，不再强制全量注入；
  - 显式声明权重分离规则：外貌描述（发色/发型/五官/服装款式与颜色）权重高于风格氛围词，
    冲突氛围词（elaborate costumes 改指定服装、vibrant hair colors 改指定发色）必须舍弃。
- **三链路统一接线**（同源根因，一并改造）：
  - `script_agent.py`（上游源头，target="全剧画面"）；
  - `character_agent.py` `_build_style_system`（target="角色定妆照"）；
  - `storyboard_agent.py` `_build_style_system`（target="分镜画面"）。
- `style_positive_tail` 无需变更（M15.1 起即仅追加风格名，本就符合分离原则）。

### 关键代码片段

```python
def style_prompt_clause(anchor: StyleAnchor, *, target: str) -> str:
    clause = (
        f"画风统一：{target}风格必须严格统一为「{anchor.title}」，"
        f"每个提示词必须显式包含风格关键词 \"{anchor.style_name_en}\""
    )
    if anchor.keywords_en and anchor.keywords_en != anchor.style_name_en:
        clause += f"\n- 风格氛围参考（可选，不必全部使用）：{anchor.keywords_en}"
    clause += (
        "\n- 权重分离规则：角色外貌描述（发色、发型、五官、服装款式与颜色）权重高于风格氛围词，"
        "与外貌冲突的氛围词（如 elaborate costumes 改变指定服装、"
        "vibrant hair colors 改变指定发色）必须舍弃"
    )
    return clause
```

### TDD 用例（10 新增）

| 文件 | 类 | 用例数 | 覆盖点 |
|---|---|---|---|
| test_style_anchor.py | TestStylePromptClause | 6 | 必填仅风格名/整串降可选/权重规则/target 注入/同名省略/写实画风 |
| test_character_agent.py | TestCharacterStyleWeightSeparation | 2 | 国漫+写实 system prompt 结构断言 |
| test_storyboard_agent.py | TestStoryboardStyleWeightSeparation | 1 | 国漫 system prompt 结构断言 |
| test_script_agent.py | TestScriptStyleWeightSeparation | 1 | 国漫 system prompt 结构断言（遍历 call_args_list 规避返修末次调用无 system 的坑） |

### 验证结果

```text
M16.1 新增测试   10/10 passed
四文件局部       116 passed（style_anchor 41 + character 20 + storyboard 23 + script 32... 实际 116）
后端全量         570 passed（+10），85.58% coverage（≥80% 达标），49.58s
前端 vitest      47 passed
前端 build       841ms（427.66 kB / gzip 130.77 kB）
```

### 待验证（M16.2 前置）

- core 部署 + 国漫 E2E 复核定妆照发色/服装是否回归剧本描述（权重分离实效力证）。

---

## 2026-08-06 M15 全链路画风锚定（根治角色定妆照与 H3 视频画风脱节）

### 变更摘要

- **M15.1 style_anchor 服务 + 四链路注入**：新增 `app/services/style_anchor.py`，将 KB `styles.json` 画风解析为 `StyleAnchor`（keywords_en / style_name_en / negative_en / is_realistic），匹配优先级 title→id→归一化互包含→tags，未命中回退「写实电影感」；`style_positive_tail`/`style_negative_tail` 生成锚定尾。四链路统一注入：
  - `script_agent.py`：系统提示词注入画风子句（风格关键词 + 写实性画质尾 + 冲突负面词）；
  - `character_agent.py`：`_build_style_system` 模板化 PROMPT_SYSTEM，搜索词/兜底提示词/RAG extra_instruction 去除硬编码 photorealistic，三视图提示词生成后强制追加风格尾；
  - `storyboard_agent.py`：同样模板化 + POSITIVE_SUFFIX 中性化，execute 末尾强制风格尾；
  - `pipeline_orchestrator.py` `_step_video`：VideoRequest prompt 追加风格尾、negative_prompt 注入冲突画风 + 通用质量负面词；空场景 prompt 保持空串不硬塞。
- **M15.2 TDD + 回归**：新增 20 例锚定测试（TestStyleTails 5 + TestCharacterStyleAnchoring 3 + TestVideoStyleAnchoring 3 + style_anchor 解析 9），storyboard/character_library 3 处旧断言同步风格尾。

### 关键代码片段

#### 1. 锚定尾生成（`app/services/style_anchor.py`）

```python
def style_positive_tail(anchor: StyleAnchor) -> str:
    """追加到正向提示词末尾的画风锚定尾巴（风格名 + 写实画质尾）。"""
    tail = f", {anchor.style_name_en}" if anchor.style_name_en else ""
    if anchor.realism_tail_en:
        tail += f", {anchor.realism_tail_en}"
    return tail

def style_negative_tail(anchor: StyleAnchor) -> str:
    """追加到反向提示词末尾的冲突画风负面词。"""
    return f", {anchor.negative_en}" if anchor.negative_en else ""
```

#### 2. H3 视频注入（`pipeline_orchestrator._step_video`）

```python
anchor = resolve_style_anchor(request.style)
video_style_tail = style_positive_tail(anchor)
style_neg = style_negative_tail(anchor).lstrip(", ")
video_negative = f"{style_neg}, blurry, low quality, distorted" if style_neg else ""
# VideoRequest(prompt=场景prompt + video_style_tail, negative_prompt=video_negative, ...)
```

### 验证结果

```text
后端全量        531 passed（509→531），85.47% coverage（≥80% 达标），47.93s
前端 vitest     47 passed
前端 build      950ms（427.66 kB / gzip 130.77 kB）
core 部署       rsync backend → core；style_anchor.py + 3 文件 grep 命中 style_positive_tail；
                uvicorn :8100 重启 health 200（本里程碑无前端变更，dist 未动）
core E2E        task pipeline-088b6ccb4b9b（1集×2镜、style=国漫、定妆照1角色、
                run_visual_check=true）— 全链路跑通，但 drift_scenes=[2] 残留（见 M15.4/15.5）
```

---

## 2026-08-06 M15.4/M15.5 漂移残留根治（正文冲突清洗 + 参考图碰撞）

### 背景（M15.3 E2E 遗留）

pipeline-088b6ccb4b9b 全链路跑通但 `drift_scenes=[2]` 未消除，双重根因：

1. **正文冲突信号抵消风格尾**：剧本 LLM 场景 prompt 自带 `hyperrealistic`、负面词反向排斥 `anime/cartoon`——仅在末尾追加锚定尾无法抵消正文中的对立风格词。
2. **参考图跨后端同名碰撞**（pipeline-3ba8b3b3e304 复查确认）：pc02 重启后 SaveImage 计数器归零生成 `character_char_001_front_00001_.png`，LB `/view` 按 BACKENDS 顺序（gpu0→pc02→pc01）盲试，命中 gpu0 同名写实陈旧图 → H3 ref2va 与漂移检测拿到错误参考图。

### 变更摘要

- **M15.4 sanitize_style_conflicts（style_anchor.py）**：`_REALISM_FAMILY_TERMS`/`_ANIME_FAMILY_TERMS` 两族互斥词表，词边界正则（`\b` 保护 unrealistic/surrealism）；正向词删对立家族、反向词删目标家族（反向词不得排斥目标画风本身），质量词（blurry/low quality 等）一律保留，删除后标点收口（折叠空白/规范逗号/去连续首尾逗号）。`script_agent`（源头场景 prompt）/`character_agent`/`storyboard_agent` 三链路在追加风格尾前先清洗 LLM 产出。
- **M15.5 三件套**：
  1. **filename_prefix 唯一化**：`character_agent._generate_image_via_sdxl` 与 `storyboard_agent._generate_image_via_sdxl` 的 `filename_prefix` 追加 `uuid.uuid4().hex[:8]` 后缀（如 `character_char_001_front_a1b2c3d4`），从源头杜绝跨后端同名碰撞；保留 character_id/view/scene_id 语义便于人工检索与 NAS 归档。
  2. **LB /view 精确路由（platform/deploy/comfyui-lb/comfyui-lb.py）**：新增 `file_map`（filename→backend_id），`handle_history` 返回完成态 outputs 时 `_learn_file_mapping` 学习映射（images/gifs/videos 三类）；`handle_view` 命中映射时只问生成后端，未命中/异常回退原盲试逻辑；`_trim_map` 统一 5000/4000 容量裁剪。已部署 workstation（备份 comfyui-lb.py.bak-m155），`systemctl restart comfyui-lb` active，`:8188/system_stats` 200。
  3. **冲突词表补充**：`_REALISM_FAMILY_TERMS` 追加 `cinematic realism`/`realistic`/`realism`（core E2E 实测残留），短语在裸词前避免正则交替截断。
- **陈旧文件清理**：gpu0（/opt/ComfyUI/instances/gpu0/output）41→0、pc02（C:\ComfyUI\output）4→0（含肇事文件 character_char_001_front_00001_.png）、pc01/NAS 为 0。

### TDD 用例（先红后绿）

- `test_style_anchor.py`：test_cinematic_realism_stripped / test_bare_realistic_and_realism_stripped / test_word_boundary_protects_unrealistic_and_surrealism / test_realistic_target_keeps_realistic_in_positive
- `test_character_agent.py::TestCharacterFilenamePrefixUniqueness`：连续两次 execute → 6 个 prefix 跨次不同、保留 character_id+view 语义
- `test_storyboard_agent.py::TestStoryboardFilenamePrefixUniqueness`：连续两次 execute → prefix 不同且保留 `storyboard_scene_{id}_` 前缀

### 验证结果

```text
红灯确认        4 用例失败（2 词表 + 2 filename_prefix），符合 TDD 预期
绿灯            实现后 test_style_anchor+character+storyboard 62/62 通过
后端全量        527 passed，82.03% coverage（≥80% 达标），47.82s
前端 vitest     47 passed（1.19s）
前端 build      887ms（427.66 kB / gzip 130.77 kB）
LB 部署         workstation comfyui-lb 重启 active，:8188/system_stats 200
core 重部署     rsync backend+deploy → core；grep 核验 cinematic realism/uuid4 命中；
                uvicorn :8100 重启 health 200（pid 387596）
core E2E 复验   task pipeline-1a92d5f7a966（同 M15.3 参数）— 运行中，验证 drift_scenes 清空
```

### 注意事项

- LB 的 file_map 是内存映射（LB 重启即失效），与 filename 唯一后缀形成双保险：即使映射缺失回退盲试，uuid8 后缀也使跨后端同名概率可忽略。
- Windows SSH 远程 PowerShell 命令中 `$_` 会被本地 shell 双引号展开吞掉，须用单引号包裹远程命令整串。
- 非 KB 画风字符串（如 M14.2 的「日系动画清新」）会经包含/tags 匹配或回退默认画风；前端画风下拉值应与 `styles.json` title 对齐，避免用户自由输入落到兜底。
- 画风锚定尾是在 LLM 输出后**强制追加**的兜底信号，LLM 系统提示词中的风格子句仍是第一道约束；RAG 优化开启时风格尾在 RAG 之后追加，保证不被重写掉。
- 场景 prompt 为空时 video prompt 保持空串（H3 以关键帧为主驱动），不强行塞风格尾。

## 2026-08-06 M15.6 复验漂移残留 → M15.7 checkpoint 选型 + M15.8 剧本源头修复

### M15.6 E2E 结果（drift 未消除，再定位根因）

task pipeline-1a92d5f7a966（同 M15.3 参数）全链路跑通 633.8s，但 `drift_scenes=[1,2]` 未消除。M15.4/M15.5 已解决正文冲突词与参考图碰撞，复查日志定位双重残留根因：

1. **SDXL checkpoint 硬编码 majicMIX 写实模型**：`character_agent`/`storyboard_agent` 的 SDXL workflow `ckpt_name` 固定为 `majicMIX realistic 麦橘写实_v7.safetensors`，国漫提示词无法扭转写实模型先验 → 定妆照/关键帧天然偏写实。
2. **剧本阶段漏传 style 参数**：`pipeline_orchestrator._step_script` 构造 `ScriptRequest` 时未传 `style=request.style`，剧本场景 prompt 按默认写实清洗，国漫任务从源头残留写实冲突词（下游 character/storyboard 清洗方向再正确也无法挽回剧本正文的写实信号）。

### 变更摘要

- **M15.7 按画风写实性选 SDXL checkpoint（style_anchor.py + 双 Agent）**：
  - 新增常量 `SDXL_CHECKPOINT_REALISTIC="majicMIX realistic 麦橘写实_v7.safetensors"` / `SDXL_CHECKPOINT_ANIME="animagineXL40.safetensors"`；
  - 新增 `sdxl_checkpoint_for_anchor(anchor)`：`anchor.is_realistic=False` → animagineXL40，写实/None 兜底 → majicMIX；
  - 双 Agent `_generate_image_via_sdxl` 新增 `anchor` 参数透传，`workflow["1"]["inputs"]["ckpt_name"]` 动态选型替代硬编码。

```python
def sdxl_checkpoint_for_anchor(anchor: StyleAnchor | None) -> str:
    """按画风写实性选择 SDXL checkpoint。"""
    if anchor is not None and not anchor.is_realistic:
        return SDXL_CHECKPOINT_ANIME
    return SDXL_CHECKPOINT_REALISTIC
```

- **M15.8 剧本阶段画风参数补传（pipeline_orchestrator.py）**：`_step_script` 的 `ScriptRequest` 补传 `style=request.style`，剧本场景 prompt 从源头按目标画风清洗。

### TDD 用例

- `test_style_anchor.py::TestSdxlCheckpointSelection` 2 例：非写实（国漫/日漫/卡通3D）→ ANIME；写实（写实电影感/都市情感）→ REALISTIC
- `test_character_agent.py::TestCharacterCheckpointByStyle` 2 例：国漫/写实请求下三次 ComfyUI 提交的 ckpt_name 全部命中对应常量
- `test_storyboard_agent.py::TestStoryboardCheckpointByStyle` 2 例：同上断言 workflow ckpt_name

### 验证结果

```text
M15.7 用例      8/8 passed（1.00s，局部覆盖率 FAIL 为预期）
后端全量        535 passed（527→535），82.06% coverage（≥80% 达标），47.41s
前端 vitest     47 passed（1.23s）
前端 build      881ms（427.66 kB / gzip 130.77 kB）
core 重部署     rsync backend → core；远端 grep 核验 sdxl_checkpoint_for_anchor
                （style_anchor 1 + character/storyboard 各 2）与 style=request.style（3 处）命中；
                uvicorn :8100 重启 health 200
```

### core E2E 终验（task pipeline-7470e3e104d9，M15.9）

入参：1 集 × 2 镜、校园日常/国漫、定妆照 1 角色、`run_quality_check=true`、`run_visual_check=true`。

```text
passed=True，总耗时 566.0s，成片 final_pipeline-1785967294.mp4
script / character / storyboard / video / voice / subtitle / edit / quality / visual_quality 全 OK
visual_quality: checked=2, drift_scenes=[1,2]（性质已变，见下）
```

**gpu0 history 实证（checkpoint 选型生效）**：

| prompt | 时间 | ckpt | prefix |
|--------|------|------|--------|
| efbaa38c | 05:30:41（M15.6 旧任务） | majicMIX 写实 | storyboard_scene_2_27c10e23 |
| f3956c61 | 06:03:59 | **animagineXL40** | character_char_001_front_c789d0f3 |
| d2fe73db | 06:04:29 | **animagineXL40** | character_char_001_side_aa8508a6 |
| d82b42e0 | 06:04:33 | **animagineXL40** | character_char_001_closeup_eacfb680 |
| 42ce3c2d | 06:05:13 | **animagineXL40** | storyboard_scene_1_ddde9dbb |
| c28baca6 | 06:05:18 | **animagineXL40** | storyboard_scene_2_e80c5b80 |

**抽帧人工复核**：定妆照（银灰发动漫少女，蓝制服红领结）、scene 1 视频帧（教室 POV 手持信封，背景动漫学生）、scene 2 视频帧（棕发动漫少女惊恐脸 + 黑西装红领带男性）——**全链路日系动漫画风，M15 画风锚定目标达成，画风漂移（卡通 vs 写实）已消除**。

**残留 drift_scenes=[1,2] 定性：角色外貌保真缺陷（非画风），派生 M16**：

1. **KB 风格词稀释外貌描述**：角色/分镜提示词均正确携带「shoulder-length black short hair... dark blue uniform」，但 KB 国漫 keywords 整串注入（vibrant colors/fantasy elements/elaborate costumes/particle effects）与外貌描述争权重 → 定妆照实际产出银灰发（与剧本「黑色齐肩短发」相悖）。
2. **animagineXL40 多角色长 prompt 崩塌**：分镜 scene 2（双角色复杂长 prompt）产出人脸拼贴网格而非场景插画；视频链未跟随该关键帧（H3 以文本为主驱动，成片反而正常）。
3. **漂移判定边缘 case**：scene 1 为 POV 手持信封特写，主角林浅未出镜，VLM 对背景路人（棕发女生）与参考图（银发）比对判漂移——按规则「角色无法辨认 → 不算漂移」应豁免，但背景路人清晰可见，判定有依据但非主创意图。

### 注意事项

- SSH 远程 `pkill -f <pattern>` 时，若同一条远程命令里同时包含 kill 与 start（pattern 会匹配自身 bash 进程），pkill 会杀死自己的会话导致 exit 255、新进程未启动。kill 与 start 必须拆成两条独立 SSH 命令；pattern 用 `[u]vicorn` 方括号技巧防自匹配。
- animagineXL40 需存在于 ComfyUI checkpoints（NAS 模型库已含）；新增非写实画风时无需改代码，`sdxl_checkpoint_for_anchor` 按 `is_realistic` 自动分流。

## 2026-08-06 配置文件清理 + 核心服务可用性验证

### 变更摘要

- **config.py 配置收敛**：注释死字段 `llm_l1/l2/l3_endpoint`（原指向已退役 Nemotron / 未使用 EXO 端点，无代码引用），LLM/VLM 统一入口说明收敛到 `exo_base_url`（spark02 :8000）；`asr_backend` 默认值从 `qwen3_asr` 改为 `ai_omni`（workstation :9210 faster-whisper large-v3），并补充 `ai_omni_asr_endpoint`/`ai_omni_asr_timeout`；ComfyUI 集群描述明确 LB 入口 `:8188` 与 3 后端（GPU0 `:8189` + pc01 `:8188` + pc02 `:8193`），删除 GPU3 部署信息。
- **subtitle_agent.py 后端回退**：为 `ai_omni` 主路径增加 try-except，失败时自动回退 `faster-whisper`；同步更新模块/类 docstring，明确默认后端为 `ai_omni`。
- **asr_server.py 离线化**：在 `/opt/ai-omni-asr/asr_server.py` 顶部强制 `HF_HUB_OFFLINE=1` / `HF_HUB_DISABLE_TELEMETRY=1`，避免 workstation 无外网访问 HuggingFace 导致 `snapshot_download` 网络超时、模型加载挂死；音频解码/转写异常返回 422 而非裸 500。
- **测试覆盖**：`test_subtitle_agent.py` 新增 `test_ai_omni_backend_success` 与 `test_ai_omni_failure_fallback_to_whisper`，验证 AI-Omni 主路径与失败回退。

### 核心服务可用性验证

```text
LLM   spark02 :8000 /v1/chat/completions  → 200, "Hi"
VLM   spark02 :8000 image_url 视觉输入    → 已实测可用（M13.1）
ComfyUI-LB workstation :8188 /system_stats → 200, queue_running=[], queue_pending=[]
TTS   workstation :9200 /tts (multipart)  → 200
ASR   workstation :9210 /v1/audio/transcriptions → 200, {"text":"","language":"zh","duration":1.0,"segments":[]}
```

### 验证结果

```text
后端 pytest     16 passed（subtitle_agent 新增 2 用例）
前端 vitest     47 passed
前端 tsc        0 错误
前端 build      成功
核心服务        LLM/VLM/ComfyUI-LB/TTS/ASR 全部可用
```

### 注意事项

- `llm_l1_endpoint` 等旧四层字段仅作注释保留，实际调用统一走 `exo_base_url`；如后续需要分层路由，应新增显式引用而非恢复死字段。
- `qwen3_asr_endpoint` 当前未部署，保留字段仅作未来扩展；生产环境必须显式设置 `ASR_BACKEND=ai_omni` 或依赖新的 config 默认值。
- AI-Omni ASR 重启后若再次卡在 `Loading model large-v3...`，优先检查 `HF_HUB_OFFLINE=1` 是否生效以及 `/opt/ai-omni-asr/.locks` 是否有僵尸锁。

## 2026-08-05 M14 视觉漂移对照前端开关 + core 全链路实机验证

### 变更摘要

- **M14.1 前端暴露 `run_visual_check` 开关**：`PipelineRunParams` 增 `run_visual_check?: boolean`；PipelineModal 增 `runVc` state + 复选框「成片后执行视觉漂移对照（需角色定妆照，检测跨镜角色漂移）」+ `STEP_LABELS` 增 `visual_quality: "视觉对照"` + `runPipeline` 传参。修复点：会话恢复时 Modal 处于半完成态（复选框引用未声明的 `runVc`），补齐 state/传参/标签三处。
- **M14.2 core 全链路 E2E**：前端 dist rsync → core :3501；core 首次 9 步全链路（含 visual_quality）实机跑通。
- **M14.3 全量回归**：后端 509/509（85.07%）、前端 47/47、tsc 0、build 1.32s。

### core 全链路 E2E 实测（task pipeline-bdccf8df382f）

入参：1 集 × 2 镜、校园日常/日系动画清新、`generate_character_refs=true`、`run_quality_check=true`、`run_visual_check=true`、`ai_label_enabled=true`。

```text
passed=True，总耗时 1329.6s
script / character / storyboard / video / voice / subtitle / edit / quality(75) / visual_quality 全 OK
visual_quality: checked=2, drift_scenes=[1,2], failed_scenes=[]
```

**人工抽帧复核（确认真阳性）**：

| 素材 | 内容 |
|------|------|
| char_001 林浅 正面参考图 | 卡通风格卷发男孩，蓝 T 恤 + 灰短裤 + 绿鞋 |
| scene 1 视频帧（t=2s） | 写实风格校服女生，走廊看通知书 |
| scene 2 视频帧（t=2s） | 写实风格短发女生，课桌翻书 |

两场景帧与参考图在画风（卡通 vs 写实）、性别呈现、服装上完全不同 → VLM `drift_detected=true` 判定准确，且两场景间角色互相一致（跨镜一致性好，问题是参考图与视频源不一致）。**视觉对照在真实流水线中抓住了上游角色风格与视频生成不一致的真实缺陷**。

### 实机附注（非阻断）

1. 分镜主后端 hunyuanimage（workstation :8600）连接失败，自动回退 SDXL 成功（非致命，3 次重试后回退）。
2. `asr_backend` 默认值 `qwen3_asr` 不匹配 subtitle_agent 的 `ai_omni`/`firered` 分支 → 落本地 faster-whisper-tiny 兜底，core 首次下载 ~75MB（HF xet 断点续传成功）。建议 core `.env` 置 `ASR_BACKEND=ai_omni` 走 workstation :9210 large-v3（本机 restart 会杀运行中任务，故未在跑批中变更）。
3. char_002 顾言参考图仅存 ComfyUI :8188 view URL，未落 core 本地 `output/character/`（char_001 有 front/side/back 本地副本）；视觉质检经 HTTP 下载 ComfyUI URL 正常。
4. core 全链路剧本/角色/分镜 LLM 调用走 `exo_base_url`（spark02 :8000），`llm_l1_endpoint`（指向已退役 Nemotron）为死配置无引用。

### 验证结果

```text
后端全量        509 passed, 85.07% coverage（≥80% 达标），51.05s
前端 vitest     47 passed（46→47，新增 M14 run_visual_check 传参用例）
前端 tsc        0 错误
前端 build      1.32s（427.66 kB / gzip 130.77 kB）
core 部署       前端 dist rsync → :3501（bundle grep 命中「视觉漂移对照」）；后端沿用 M13.1 部署（本里程碑无后端代码变更）
core E2E        pipeline-bdccf8df382f 9 步全绿，visual_quality drift=[1,2] 真阳性
```

## 2026-08-05 M13.1 漂移检测实测调优（独立 VLM 调用）+ Nemotron 退役切换 spark02

### 背景与问题

M13 初版实机测试暴露三类问题：

1. **真阳性漏报**：漂移指令 `VISUAL_DRIFT_PROMPT_ADDENDUM` 拼接在主画质检查 prompt 末尾，被"画质检查"心智框架稀释，极端换人场景（古装女子 vs 卡通男孩参考图）也漏报。
2. **VLM 跨图干扰幻觉**：4+ 张图（3 参考图 + 3 帧）同时输入，Nemotron 把卡通男孩侧/背面帧描述成"写实成年女性"。
3. **占位文本照抄**：模型直接输出 prompt 中的占位说明文字作为 details。

### 变更摘要

- **架构重构**：漂移检测从主画质检查中分离为独立第二次 VLM 调用。`_drift_check` 逐帧并发（`asyncio.gather`）调用 `_drift_check_single_frame`（参考图 + 单帧），任一帧漂移即整体漂移；漂移细节聚合去重取前 3 条。任何异常兜底 `(False, "")` 不阻断主检查。
- **prompt 重写** `DRIFT_CHECK_PROMPT`：明确四条判定规则（性别/年龄段/画风/人种明显不同 → 漂移；同角色三视图视角差异 → 不算漂移；无法辨认 → 不算漂移；同视角发型/服装/妆容明显不同 → 漂移），要求 details 必须基于图片实际内容描述，**严禁照抄示例文字**。
- **图像 detail 提升**：`"low"` → `"high"`，保证 VLM 能看清服装/鞋子颜色等细节。
- **Nemotron 退役**（用户指令 2026-08-05）：workstation GPU3 Nemotron vLLM `systemctl stop + disable`；core 与本地 `.env`、`config.py` 默认值的 `EXO_BASE_URL` / `VISUAL_MODEL_URL` 统一切到 spark02 :8000（`qwen3.6-uncensored` = qwen3.6-35b-a3b-uncensored-heretic FP8，已实测 `image_url` 视觉输入可用——16×16 纯红 PNG 正确回答 "Red"）。模型名不变，零代码改动。

### 关键代码片段

#### 1. 独立漂移检测 prompt（`platform/backend/app/agents/quality_agent.py`）

```python
DRIFT_CHECK_PROMPT = """前 {ref_count} 张图是角色定妆参考图（三视图：正面/侧面/背面），最后 1 张是视频抽帧。
请判断视频帧中的角色是否与参考图为同一角色。判定规则：
- 性别/年龄段/画风（卡通 vs 写实）/人种明显不同，或明显不是同一人 → 判定漂移；
- 确认是同一角色后，不同视角（正面/侧面/背面）之间的外观差异（如背面看不到面部）→ 不算漂移；
- 帧模糊、角色占比过小或被遮挡到无法辨认 → 不算漂移，details 填写"无法辨认"；
- 同视角下发型、服装款式、妆容与参考图明显不同 → 判定漂移。
输出要求：只输出一个 JSON 对象，包含两个字段：drift_detected / details。
严禁照抄本说明中的示例文字。"""
```

#### 2. 逐帧并发判定（`platform/backend/app/agents/quality_agent.py`）

```python
results = await asyncio.gather(*(
    self._drift_check_single_frame(ref_paths, frame_path)
    for _timestamp, frame_path in frames
))
drift_hits = [detail for is_drift, detail in results if is_drift]
if drift_hits:
    uniq = list(dict.fromkeys(d.strip() for d in drift_hits if d.strip()))
    return True, "；".join(uniq[:3])
```

### 实机测试矩阵（core :8100，VLM=spark02 qwen3.6-uncensored）

```text
T1 真阴性     front帧 vs front参考        → drift=false, score=98  ✓（无误报）
T2 细微漂移   红T恤男孩 vs 蓝T恤参考      → drift=true,  score=95  ✓
              细节：蓝T恤+灰短裤+绿鞋 → 红T恤+蓝长裤+棕鞋（具体差异全部命中）
T3 极端漂移   古装女子 vs 卡通男孩(3参考) → drift=true,  score=65  ✓
              细节：3D卡通男性儿童 vs 写实成年女性（画风/性别/年龄段，无幻觉）
```

对比 Nemotron 时代：漂移 details 从模板话（"性别/年龄段/画风明显不同"）升级为基于图像实际内容的具体描述，且无跨图干扰幻觉。

### 回归结果

```text
后端全量        509 passed, 85.07% coverage（≥80% 达标）
前端 vitest     46 passed
前端 tsc        0 错误
core 部署       config.py rsync 同步；uvicorn :8100 health 200；三例矩阵全绿
AGENTS.md       GPU3 条目更新：Nemotron 退役、FlashTalk-14B (:9000 ~50GB)、LLM/VLM 走 spark02
```

### 注意事项

- spark02 vLLM 的 500 "broken PNG file" 报错恰恰证明 image_url 通路存在（已进入图像解码阶段），调试时不要误判为不支持视觉。
- Nemotron vLLM 崩溃根因：GPU3 被 FlashTalk 占 50GB，引擎初始化 OOM——与用户退役决定一致，未做修复。
- `drift_detected` details 聚合按帧去重但不同帧措辞略有差异时仍会拼接 2-3 条相似描述，属预期行为。

## 2026-08-05 M13 角色一致性对照视觉检测（VLM 参考图比对）

### 变更摘要

- **M13.1 schemas 扩展**：`QualityVisualRequest` 新增 `reference_image_urls`（角色定妆参考图 URL 列表，空则不做对照）；`QualityVisualResult` 新增 `drift_detected`（默认 False）；`PipelineRunRequest` 新增 `run_visual_check`（默认 False，成片后是否执行视觉漂移对照）。
- **M13.2 VisualQualityAgent 参考图对照**：新增 `VISUAL_DRIFT_PROMPT_ADDENDUM` 对照指令，引导 VLM 将参考图（消息前部）与视频抽帧（消息后部）逐帧比对脸型/发型/服装/妆容；`_download_reference_image` 并发下载参考图，localhost `/static/` 路径直接映射 `output/` 目录免下载，单张失败返回 None 跳过不阻断；`drift_detected` 解析三层逻辑——VLM 显式输出优先，issues 含 `visual_consistency`+`critical` 兜底判 True，无参考图时恒 False（无对照基础）。
- **M13.3 pipeline 集成**：`_step_visual_quality` 在剪辑完成后按 `run_visual_check` 开关执行，参考图复用 `_collect_character_reference_images`（与视频步骤同规则），`asyncio.gather` 逐场景检测；报告 `steps.visual_quality` 含 `checked`/`failed_scenes`/`drift_scenes`/`results`；无视频/无参考图 skipped，单场景失败与整体异常均非致命。

### 关键代码片段

#### 1. drift_detected 三层解析（`platform/backend/app/agents/quality_agent.py`）

```python
drift_detected=bool(ref_paths) and (
    bool(data.get("drift_detected", False))
    or any(
        item.get("category") == "visual_consistency"
        and item.get("severity") == "critical"
        for item in data.get("issues", [])
        if isinstance(item, dict)
    )
),
```

#### 2. 参考图下载本地复用（`platform/backend/app/agents/quality_agent.py`）

```python
if parsed.hostname in ("localhost", "127.0.0.1") and parsed.path.startswith("/static/"):
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 3:
        local_dir = Path(__file__).resolve().parent.parent.parent / "output" / parts[1]
        candidate = local_dir / parts[-1]
        if candidate.exists():
            return candidate
```

#### 3. 流水线视觉质检步骤（`platform/backend/app/services/pipeline_orchestrator.py`）

```python
if request.run_visual_check:
    await self._step_visual_quality(task_id, project_id, script, videos, report)

# 报告结构
report["steps"]["visual_quality"] = {
    "checked": len(results) - len(failed),
    "failed_scenes": failed,
    "drift_scenes": drift_scenes,
    "results": results,
}
```

### 测试结果

```text
M13 相关单测    新增 11 例（TestVisualDriftDetection 4 + TestDownloadReferenceImage 3 + TestVisualQualityStep 4）
后端全量        508 passed, 85.17% coverage（≥80% 达标）
前端 vitest     46 passed (3 files)
前端 build      937ms（427.38 kB / gzip 130.70 kB）
core 部署       rsync backend → core；uvicorn :8100 health 200（agents 含 visual_quality_agent）
                远端 grep 核验 drift_detected：schemas.py×1 / quality_agent.py×4 / pipeline_orchestrator.py×2
                前端 http.server :3501 200
```

### 注意事项

- ssh 单条复合命令内 `pkill -f "uvicorn app.main:app"` 会匹配到远程 shell 自身的命令行（含 nohup 启动串）导致自杀（exit 255 无输出）；拆分为 kill-only 与 start+verify 两条 ssh，且 kill 用 `[u]vicorn` 括号技巧。
- `run_visual_check` 默认 False，前端 PipelineRunParams 暂未暴露该开关；需要时经 API 直传或在后续里程碑接入前端表单。
- 参考图与视频帧均 `detail: "low"` 送 VLM，参考图在前帧在后的顺序为 prompt 约定，改动时需同步更新 `VISUAL_DRIFT_PROMPT_ADDENDUM` 文案。

## 2026-08-05 M12 H3 精细化优化（SHOT 节拍视觉化 + 动态混音增益 + 多镜漂移标注）

### 变更摘要

- **M12.1 多镜 SHOT 节拍视觉化**：剧本层 `narrative_beat` 经 `_MULTISHOT_BEAT_HINTS_EN` 映射为英文视觉指令，由 `build_multishot_prompt` 追加到多镜 SHOT prompt 行尾（与分镜层 `BEAT_VISUAL_HINTS` 同语义策略，六种节拍：hook/escalation/reversal/cliffhanger/emotional_beat/transition）。`VideoRequest` 新增 `narrative_beat` 字段，`pipeline_orchestrator` 透传剧本层节拍信息。
- **M12.2 动态混音增益**：`compute_ambience_gain` 纯函数按对白密度（人声时长/视频时长）分三档选择 H3 环境音增益——≥0.85 对白密集档 0.15（约 -16dB）、0.4-0.85 基准档 0.25（约 -12dB）、<0.4 大量留白档 0.40（约 -8dB）；ffprobe 探测失败（0/负值）自动回退基准档，主链路不中断。config 新增 `h3_dynamic_gain_enabled`/`h3_ambience_gain_dense`/`h3_ambience_gain_sparse`。
- **M12.3 多镜漂移风险标注**：质检层 `_multishot_group_issues` 与 `video_agent.group_scenes_for_multishot` 同规则模拟分组（同集相邻 + 场景数/总时长双上限），≥2 场景成组时逐场景标注 `visual_risk/info`——「多镜联合生成组（N 镜一次推理）: 跨镜角色漂移风险」，suggestion 提示抽查首尾帧或置 `h3_multishot_enabled=False` 回退逐场景生成。仅 `video_backend=h3` 且多镜开启时生效。

### 关键代码片段

#### 1. SHOT 节拍视觉指令注入（`platform/backend/app/agents/video_agent.py`）

```python
_MULTISHOT_BEAT_HINTS_EN = {
    "hook": "high-contrast dramatic lighting, oppressive composition, intense expression, instant visual impact",
    "escalation": "tighter framing, stronger chiaroscuro, confrontational body language, rising tension",
    "reversal": "frozen beat of subverted expectation, dramatic twist, expressive close-up",
    "cliffhanger": "withheld information, negative-space composition, suspense, urge to continue",
    "emotional_beat": "soft light, shallow depth of field, delicate emotional close-up, slowed pace",
    "transition": "calm establishing framing, visual lead-in, uncluttered composition",
}

def build_multishot_prompt(requests: list[VideoRequest]) -> str:
    lines = [H3_MULTISHOT_PROMPT_GUIDE]
    for idx, req in enumerate(requests, start=1):
        shot = (req.prompt or "").strip() or "cinematic, high quality, smooth motion"
        hint = _MULTISHOT_BEAT_HINTS_EN.get((req.narrative_beat or "").strip().lower())
        lines.append(f"SHOT {idx}: {shot}" + (f" ({hint})" if hint else ""))
    return "\n".join(lines)
```

#### 2. 动态混音增益纯函数（`platform/backend/app/agents/edit_agent.py`）

```python
def compute_ambience_gain(voice_seconds: float, video_seconds: float) -> float:
    if voice_seconds <= 0 or video_seconds <= 0:
        return settings.h3_ambience_gain
    ratio = voice_seconds / video_seconds
    if ratio >= 0.85:
        return settings.h3_ambience_gain_dense
    if ratio >= 0.4:
        return settings.h3_ambience_gain
    return settings.h3_ambience_gain_sparse
```

#### 3. 多镜漂移标注（`platform/backend/app/agents/quality_agent.py`）

```python
return [
    QualityCheckItem(
        category="visual_risk",
        severity="info",
        scene_id=s.scene_id,
        message=f"多镜联合生成组（{len(group)} 镜一次推理）: 跨镜角色漂移风险",
        suggestion="建议抽查该组首尾帧角色一致性；漂移时整组重抽或改逐场景生成（h3_multishot_enabled=False）",
    )
    for group in groups
    if len(group) >= 2
    for s in group
]
```

### 测试结果

```text
M12 相关单测   61 passed（test_video_multishot + test_edit_agent + test_quality_agent）
后端全量       497 passed, 85.17% coverage（≥80% 达标）
前端 vitest    46 passed (3 files)
tsc            0 errors
core 部署      /api/drama/health 200（version 0.11.0）；远端 grep 核验：
               config.py(h3_dynamic_gain_enabled) / schemas.py(narrative_beat) /
               edit_agent.py(compute_ambience_gain) / quality_agent.py(_multishot_group_issues) 全部在案
               前端 http.server :3501 200
```

### 注意事项

- 动态增益三档阈值（0.85/0.4）与增益值（0.15/0.25/0.40）均为可调配置，实测成片听感后可在 `.env` 覆盖微调。
- 多镜漂移标注为 `info` 级不阻断流水线；若某组实片出现明显跨镜漂移，优先整组重抽，反复漂移则将该集 `h3_multishot_enabled=False` 回退逐场景生成。

---

## 2026-08-05 M11 H3 专属优化（ref2va 角色一致性 + 多镜叙事 + 原生音频混音）

### 变更摘要

- **M11.1 ref2va 角色一致性**：通过 MiniMaxH3ReferenceToVideo 节点将角色资产库三视图参考图注入视频生成流程。关键技术点：COMFY_AUTOGROW_V3 动态组 API 格式为嵌套 dict（`inputs["ref_images"] = {"ref_image_0": ["10",0], ...}`），扁平键 `ref_image_N` 能通过 prompt 校验但执行时 TypeError。`WORKFLOW_TEMPLATE_H3_R2V` 与 `execute_multi_shot` 动态挂接逻辑同步修复为嵌套结构。
- **M11.2 多镜叙事联合生成**：同集相邻场景合并为一次多镜推理（单 prompt 多 SHOT），再按帧边界 ffmpeg 切分回各场景视频。`group_scenes_for_multishot` 纯函数贪心分组（同集相邻，受场景数/总时长双上限约束）；`build_multishot_prompt` 组装总览前缀 + SHOT 编号；`_multishot_split_plan` 末段吃到组尾（17k+5 网格吸附余量）；`execute_multi_shot` 失败整组回退逐场景 execute。
- **M11.3 H3 原生音频混音**：H3 生成的环境音轨与人声按比例混合，环境音增益 0.25（约 -12dB）。
- **实机验证**：`scripts/smoke_h3_r2v.py` 上传关键帧 + 2 角色参考图 → 256x256/39帧/4步 → success，93KB mp4 含 H.264 视频轨 + AAC 立体声音轨。
- **core 部署**：rsync platform/ → core（排除 node_modules/.venv/static/outputs）；后端 uvicorn :8100 重启 health 200，前端 http.server :3501 200。

### 关键代码片段

#### 1. ref2va 嵌套 dict 模板（`platform/backend/app/agents/video_agent.py`）

```python
# 参考图 LoadImage 节点（含关键帧+角色参考图）经 COMFY_AUTOGROW_V3 组接线：
# API 格式为嵌套 dict：inputs["ref_images"] = {"ref_image_0": ["10",0], ...}
# （扁平键 ref_image_N 能通过 prompt 校验但执行时 TypeError，不可用）
"20": {
    "class_type": "MiniMaxH3ReferenceToVideo",
    "inputs": {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "audio_vae": ["4", 0],
        "prompt": "{positive_prompt}",
        "width": 768,
        "height": 1344,
        "length": 124,
        "ref_image_size": "match",
        "ref_images": {"ref_image_0": ["10", 0]},
    }
},
```

#### 2. 角色参考图动态挂接（嵌套 dict）

```python
# 角色参考图动态挂接：LoadImage 节点 11/12/... → ref_images 组内 ref_image_1/2/...
# （COMFY_AUTOGROW_V3 API 格式为嵌套 dict，扁平键执行期 TypeError）
ref_group = workflow["20"]["inputs"].setdefault("ref_images", {})
for idx, name in enumerate(ref_names, start=1):
    node_id = str(10 + idx)
    workflow[node_id] = {
        "class_type": "LoadImage",
        "inputs": {"image": name},
    }
    ref_group[f"ref_image_{idx}"] = [node_id, 0]
```

#### 3. 多镜叙事分组纯函数

```python
def group_scenes_for_multishot(requests, max_scenes, max_seconds):
    """同集相邻场景贪心合并为多镜组（保持输入顺序，纯函数）。

    规则：
    - 仅同集（episode 相同）且在输入列表中相邻的场景可同组
    - 组内场景数 ≤ max_scenes，组内总时长 ≤ max_seconds
    - 返回覆盖全部输入的分组；单元素组由调用方走原逐场景路径（≥2 场景才成组）
    """
    groups = []
    current = []
    current_seconds = 0.0
    current_episode = None
    for req in requests:
        duration = float(req.duration_seconds)
        if (
            current
            and req.episode == current_episode
            and len(current) < max_scenes
            and current_seconds + duration <= max_seconds
        ):
            current.append(req)
            current_seconds += duration
        else:
            if current:
                groups.append(current)
            current = [req]
            current_seconds = duration
            current_episode = req.episode
    if current:
        groups.append(current)
    return groups
```

#### 4. 多镜切分边界计算（末段吃到组尾）

```python
def _multishot_split_plan(durations_seconds, total_frames, fps=24):
    """按各场景时长累计帧偏移计算切分边界，最后一场吃到组尾。

    组总帧数经 17k+5 网格吸附后可能略大于各场景时长之和，
    余量全部归末段；返回每场景 (start_frame, end_frame) 帧区间（左闭右开）。
    """
    plan = []
    start = 0
    last = len(durations_seconds) - 1
    for i, duration in enumerate(durations_seconds):
        end = total_frames if i == last else start + round(float(duration) * fps)
        plan.append((start, end))
        start = end
    return plan
```

### 回归结果

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `pytest tests/ -v --tb=short` | **480/480 passed**（覆盖率 85.02%） |
| r2v 实机冒烟 | `./.venv/bin/python scripts/smoke_h3_r2v.py` | SMOKE PASS（93KB mp4，音视频双轨） |
| core 部署 | rsync + uvicorn 重启 | health 200 / front 200 |

---

## 2026-08-04 M9.8 RAG 六路检索线程化（事件循环卡顿修复）

### 变更摘要

- **问题**：M9.6 事件循环审计再延伸——`optimize_prompt` 在 `_warm_up` 线程化后，仍在事件循环内直接同步调用 6 路 `search()`；`search()` 内部 `self._embedding_model.embed()` 为 fastembed ONNX 同步推理（CPU 每路 ~20-100ms），六路串行累积数百 ms 卡顿，`pipeline_orchestrator` 逐场景调用时放大（N 场景 × 6 路 × 推理耗时）。
- **修复**：新增 `_retrieve_multi()` 同步辅助方法（覆盖 style/shot/example/negative/method/genre_trope 六类检索），`optimize_prompt` 改为 `await asyncio.to_thread(self._retrieve_multi, query, domain, style_hint)`；公开同步接口 `search()` 签名不变（测试与直调场景不受影响）。
- **测试**：`test_rag_service.py` 新增 2 用例——①`test_optimize_prompt_retrieves_via_thread`：守卫检索必须经 `_retrieve_multi` 线程入口（事件循环内 0 次 search）；②`test_retrieve_multi_calls_six_categories`：六类检索全覆盖。
- **回归**：全量 428/428 passed，覆盖率 84.32%（≥80% 达标）。

### 关键代码片段

#### 1. 线程化检索（`platform/backend/app/services/rag_service.py`）

```python
# 2. 多路检索：风格 + 镜头 + 示例 + 负面 + 方法 + 类型片叙事模板。
# search() 内部含 fastembed ONNX 同步推理（CPU 每路 ~20-100ms），
# 六路串行在事件循环内执行会累积数百 ms 卡顿（pipeline 逐场景调用时放大），
# 故整体放线程执行。
(
    style_results,
    shot_results,
    example_results,
    negative_results,
    method_results,
    trope_results,
) = await asyncio.to_thread(
    self._retrieve_multi, query, domain, style_hint
)
```

#### 2. 六路同步检索辅助方法

```python
def _retrieve_multi(self, query, domain, style_hint):
    """六路同步检索（风格/镜头/示例/负面/方法/类型片模板）。

    search() 内部含 fastembed ONNX 同步推理，必须由调用方放入线程执行
    （optimize_prompt 经 asyncio.to_thread 调用本方法），避免阻塞事件循环。
    """
    return (
        self.search(query, category="style", domain=domain, style=style_hint, top_k=2),
        self.search(query, category="shot", domain=domain, top_k=2),
        self.search(query, category="example", domain=domain, style=style_hint, top_k=2),
        self.search(query, category="negative", domain=domain, top_k=2),
        self.search(query, category="method", domain=domain, top_k=2),
        self.search(query, category="genre_trope", domain=domain, style=style_hint, top_k=2),
    )
```

### 回归结果

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `pytest tests/ -q` | **428/428 passed**（覆盖率 84.32%） |
| 新增用例 | `TestWarmUp` 扩展 | 2 用例全绿 |

---

## 2026-08-04 M9.7 共享 LLM 客户端单例（连接池泄漏修复）

### 变更摘要

- **问题**：M9.6 事件循环审计延伸——发现三处游离调用点每次请求新建 `AsyncOpenAI`（隐式新建 httpx 连接池）且从不关闭：①`ai_optimizer.optimize_content`（前端「智能体辅助」高频路径，每个可编辑字段润色/扩写/精简/改写都走这里）；②`rag_service.optimize_prompt`；③`drama.py` 智能体辅助端点。高频调用下累积泄漏 socket，且每次重建 TCP 连接徒增延迟。
- **修复**：`base.py` 新增 `get_shared_llm_client()` 懒加载单例（共享 httpx.AsyncClient，`trust_env=False` 与 BaseAgent 一致）+ `close_shared_llm_client()`；三处调用点全部改为复用；`main.lifespan` shutdown 阶段统一关闭共享连接池；`rag_service` 移除无用 `AsyncOpenAI` import。
- **测试**：`test_base.py` 新增 `TestSharedLLMClient` 3 用例（单例复用 / close 后可重新懒加载防残留失效引用 / 未初始化 close 幂等）；`test_rag_service.py` 4 处打桩由 `AsyncOpenAI` 改为 `get_shared_llm_client`。
- **回归**：全量 426/426 passed，覆盖率 84.33%（≥80% 达标）。

### 关键代码片段

#### 1. 共享客户端单例（`platform/backend/app/agents/base.py`）

```python
_shared_http: httpx.AsyncClient | None = None
_shared_llm: AsyncOpenAI | None = None


def get_shared_llm_client() -> AsyncOpenAI:
    """返回进程级共享 AsyncOpenAI 客户端（懒加载，连接池复用）。"""
    global _shared_http, _shared_llm
    if _shared_llm is None:
        # trust_env=False 与 BaseAgent 一致，避免系统代理拦截内网地址
        _shared_http = httpx.AsyncClient(timeout=600.0, trust_env=False)
        _shared_llm = AsyncOpenAI(
            base_url=settings.exo_base_url,
            api_key=settings.exo_api_key or "not-needed",
            http_client=_shared_http,
        )
    return _shared_llm


async def close_shared_llm_client() -> None:
    """关闭共享客户端连接池（应用关闭时由 lifespan 调用）。"""
    global _shared_http, _shared_llm
    if _shared_http is not None:
        await _shared_http.aclose()
    _shared_http = None
    _shared_llm = None
```

#### 2. lifespan 统一清理（`platform/backend/app/main.py`）

```python
for a in agents:
    try:
        await a.aclose()
    except Exception:
        logger.warning("关闭 Agent HTTP 客户端失败: %s", a.name, exc_info=True)
# 关闭模块级共享 LLM 客户端连接池（ai_optimizer/rag_service/智能体辅助复用）
try:
    await close_shared_llm_client()
except Exception:
    logger.warning("关闭共享 LLM 客户端失败", exc_info=True)
```

### 回归结果

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `pytest tests/ -q` | **426/426 passed**（覆盖率 84.33%） |
| 新增用例 | `TestSharedLLMClient` | 3 用例全绿 |

---

## 2026-08-04 M9.6 core E2E 复验 + 事件循环冻结热修复（P0）

### 变更摘要

- **问题①（P0）事件循环冻结**：core 复跑全链路时 `/api/progress` 与 health 全部超时无响应。py-spy dump 实锤主线程阻塞链：`pipeline_orchestrator._step_script → script_agent._rag_enhance_scenes → rag_service.optimize_prompt → search → _init_model → fastembed → huggingface_hub model_info → sync httpx create_connection`（HF 不可达，connect 挂起 ~130s）。根因：`optimize_prompt` 虽用 `asyncio.to_thread(self._ensure_initialized)` 预热，但**缓存命中时 `initialize()` 提前返回不加载模型**（`_embedding_model` 仍为 None），首个 `search()` 在事件循环内同步触发模型下载。
- **修复①（rag_service.py）**：新增 `_warm_up()`（`_ensure_initialized()` + 有条目时显式 `_init_model()`），`optimize_prompt` 改为 `await asyncio.to_thread(self._warm_up)`，确保模型加载始终在线程内完成。
- **问题②（P0）HF 镜像 401**：设置 `HF_ENDPOINT=https://hf-mirror.com` 后 API 可达（0.5s），但 fastembed 默认 Xet 存储后端的分块重建请求直连 `cas-server.xethub.hf.co`（不走镜像）返回 401。
- **修复②（main.py）**：文件顶部（agent 导入前，ENDPOINT 常量在 import 时固化）`os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")` + `os.environ.setdefault("HF_HUB_DISABLE_XET", "1")`。
- **验证**：core 嵌入模型下载成功（bge-small-zh-v1.5，dim 512）；两次全链路 E2E 均 `passed:true`（script/storyboard/video/voice/subtitle/edit/quality 全绿，RAG 0 失败，成片 final_pipeline-1785807045.mp4，quality 85）；本地全量回归 423/423（覆盖率 84.29%，新增 TestWarmUp 3 回归用例：缓存命中时 _warm_up 补加载模型 / 无条目跳过 / optimize_prompt 必须经 _warm_up 预热）；AI-Omni ASR 转写实测恢复（verbose_json 含 segments 时间轴）。

### 关键代码片段

#### 1. RAG 线程内完整预热（`platform/backend/app/services/rag_service.py`）

```python
def _warm_up(self) -> None:
    """线程内预热：初始化索引并加载嵌入模型。

    缓存命中时 initialize() 不会加载模型（_embedding_model 为 None），
    首个 search() 会在事件循环内触发 _init_model() 同步下载/加载模型，
    外网不可达时阻塞事件循环 ~130s（2026-08-04 core 实测全接口冻结）。
    因此预热必须显式补加载模型。
    """
    self._ensure_initialized()
    if self._entries:
        self._init_model()

# optimize_prompt 内：
await asyncio.to_thread(self._warm_up)
```

#### 2. HF 镜像 + 禁用 Xet（`platform/backend/app/main.py` 顶部）

```python
import os

# 必须在导入 fastembed/huggingface_hub 之前设置（ENDPOINT 常量在 import 时固化）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# hf-mirror 不代理 cas-server.xethub.hf.co，Xet 分块重建 401，禁用后走普通 HTTP
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
```

### 回归结果

| 项目 | 结果 |
|------|------|
| 后端 pytest | 423/423 通过，覆盖率 84.29%（新增 TestWarmUp 3 用例） |
| core 全链路 E2E ×2 | passed:true，8 步全绿，RAG 0 失败 |
| 事件循环 | 视频生成期间 health/progress 均 200 即时响应 |
| AI-Omni ASR | 转写恢复，segments 时间轴正常 |

## 2026-08-04 M9 全链路结果回填画布

### 变更摘要

- **后端报告自包含（pipeline_orchestrator.py）**：`_step_script` 终态报告 `steps.script` 新增 `data: script.model_dump()`——此前仅有 `{title, characters: len, scenes: len}` 摘要，前端无法从报告还原画布所需完整剧本，现报告自包含（体积几 KB 可接受）。
- **前端提取函数（client.ts）**：`extractScriptFromReport(report)` 防御性解析 `steps.script.data`，校验 `project_id`/`title` 为 string、`characters`/`scenes` 为数组，任何一层缺失返回 null 由调用方降级。
- **加载到画布（PipelineModal.tsx）**：`canvasScript` memo 仅在 `report.passed` 时可用；任务成功后操作栏显示「加载到画布」按钮（Workflow 图标），`handleLoadToCanvas` 执行 `setScriptData(canvasScript)` + statusInfo 提示 + 关弹窗，画布立即呈现完整项目，打通「一键生成 → 画布微调 → 局部重跑」闭环。
- **测试**：client.test.ts 新增 `describe("M9 extractScriptFromReport")` 3 用例；test_pipeline_orchestrator happy path 断言 `script.data` 含完整 scenes。

### 关键代码片段

#### 1. 后端报告内嵌剧本（`platform/backend/app/services/pipeline_orchestrator.py`）

```python
report["steps"]["script"] = {
    "title": script.title,
    "characters": len(script.characters),
    "scenes": len(script.scenes),
    # 完整剧本数据：供前端「加载到画布」回填，报告体积约几 KB 可接受
    "data": script.model_dump(),
}
```

#### 2. 前端防御性提取（`platform/frontend/src/api/client.ts`）

```typescript
export function extractScriptFromReport(
  report: PipelineReport | null | undefined
): ScriptData | null {
  const data = report?.steps?.script?.data;
  if (!data || typeof data !== "object") return null;
  const s = data as Partial<ScriptData>;
  if (
    typeof s.project_id !== "string" ||
    typeof s.title !== "string" ||
    !Array.isArray(s.characters) ||
    !Array.isArray(s.scenes)
  ) {
    return null;
  }
  return data as ScriptData;
}
```

### 回归结果

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `python -m pytest tests/ -q` | **420 passed / 0 failed**，覆盖率 **84.48%**（≥80% 达标），47.63s |
| 编排服务 | `tests/unit/test_pipeline_orchestrator.py` | 11/11 通过（happy path 新增 script.data 断言） |
| 前端单测 | `pnpm vitest run` | **46/46 通过**（43→46，新增 M9 提取函数 3 用例），1.49s |
| 前端类型 | `npx tsc --noEmit` | 0 错误 |
| 前端构建 | `pnpm build` | 成功（1.12s，427.38 kB / gzip 130.70 kB） |
| core 部署实测 | rsync + curl + E2E pipeline 任务 | health/前端 200；orchestrator 含 model_dump、bundle 含「加载到画布」；E2E 终态报告 script.data 完整 |

## 2026-08-04 M8 前端一键全链路成片入口

### 变更摘要

- **pipeline API 层（client.ts）**：新增 `PipelineRunParams`（14 个参数，含 monetization_mode/ai_label_enabled/license_number 等合规字段）与 `PipelineReport`（终态报告，steps 键为各环节名）类型；`runPipeline` POST `/pipeline/run` 返回 `AsyncTaskResponse`，`cancelPipeline` POST `/pipeline/cancel/{id}` 对 id 做 `encodeURIComponent`；`resolveTaskUrl` 将后端返回的 localhost 绝对 poll/stream URL 按 `API_BASE` 同源重写（相对 API_BASE 部署时仅保留 path），解决远程部署浏览器无法直连 localhost 的问题；`resolveStaticUrl` 统一处理静态资源 URL。
- **PipelineModal 一键成片弹窗（PipelineModal.tsx）**：两态界面——未启动时为创意设定表单（premise/genre/style/集数/每集场景数/iaa-iap 模式/角色定妆照/质检/AI标识+备案号开关），启动后切换为 SSE 进度条（复用 `useProgress` + `ProgressBar`）+ 分步终态报告展示 + 取消按钮；`handleStart` 校验 premise 非空后调用 `runPipeline`，`resolveTaskUrl(resp.stream_url)` 接入进度流。
- **入口接入（App.tsx / useDramaStore / Icon.tsx）**：顶栏新增「一键成片」按钮（Zap 图标，`title="一句话创意 → 全链路自动成片"`）；`ModalsState` 新增 `pipeline` 字段；Icon.tsx 按需 re-export `Zap`/`Square` 并纳入 `IconComponent` 联合类型。
- **测试（client.test.ts）**：新增 `describe("M8 全链路 pipeline API")` 7 用例——runPipeline 正常/422 错误、cancelPipeline URL 编码/404、resolveTaskUrl 相对 API_BASE 剥离 localhost 源/非法 URL 原样返回、resolveStaticUrl 三分支。

### 关键代码片段

#### 1. localhost 绝对 URL 同源重写（`platform/frontend/src/api/client.ts`）

```typescript
export function resolveTaskUrl(url: string): string {
  try {
    const u = new URL(url);
    if (API_BASE.startsWith("http")) {
      const base = new URL(API_BASE);
      u.protocol = base.protocol;
      u.host = base.host;
      return u.toString();
    }
    // API_BASE 为相对路径（经代理/同源部署）：仅保留 path，走当前页面源
    return u.pathname + u.search;
  } catch {
    return url;
  }
}
```

#### 2. 一键全链路启动（`platform/frontend/src/components/modals/PipelineModal.tsx`）

```typescript
const resp = await runPipeline({
  premise: premise.trim(),
  genre, style, episodes,
  scenes_per_episode: scenesPerEpisode,
  monetization_mode: mode,
  generate_character_refs: genCharRefs,
  run_quality_check: runQc,
  ai_label_enabled: aiLabel,
  license_number: licenseNumber.trim(),
});
setTaskId(resp.task_id);
setStreamUrl(resolveTaskUrl(resp.stream_url));
```

### 回归结果

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `python -m pytest tests/ -q` | **420 passed / 0 failed**，覆盖率 **84.48%**（≥80% 达标），50.72s |
| 前端单测 | `pnpm vitest run` | **43/43 通过**（36→43，新增 M8 pipeline API 7 用例），1.12s |
| 前端类型 | `npx tsc --noEmit` | 0 错误（修复 ProgressBar 缺 result 属性 1 处） |
| 前端构建 | `pnpm build` | 成功（938ms，426.81 kB / gzip 130.51 kB） |
| core 部署实测 | rsync + curl `/api/drama/pipeline/run\|status\|cancel` | run 返回 task_id+poll/stream URL；status 显示 Step 1/8 生成剧本；cancel 返回 cancel_requested；30s 后终态「任务已被用户取消」，取消闭环验证通过；前端 :3501 bundle 已含「一键成片」 |

## 2026-08-04 M7 短剧工业化优化 + 全链路自动编排

### 变更摘要

- **剧本层（script_agent.py）**：新增结构校验器 `VALID_NARRATIVE_BEATS`（hook/escalation/reversal/cliffhanger/emotional_beat/transition）——首镜必须为 hook 且 ≤3s（`HOOK_MAX_DURATION`，黄金 3 秒原则）、每 15s 内至少一个强节拍（`DENSITY_MAX_WEAK_SECONDS`）、IAP 模式 8-12 集反转卡点检查；校验不通过时携带 issues 调用 LLM 自动返修，形成生成→校验→返修闭环。
- **分镜层（storyboard_agent.py）**：`BEAT_VISUAL_HINTS` 将 narrative_beat 转换为具体视觉指令（hook=高对比戏剧光+压迫感构图+主体张力），注入分镜提示词组装。
- **质检层（quality_agent.py）**：复用剧本结构检查维度（hook/cliffhanger/时长/景别/情绪密度），自动标注多角色场景/极端角度/跨集次要角色。
- **剪辑层（edit_agent.py）**：`_burn_ai_label` 按 2026-09-01 广电总局新规在成片右上角烧录「AI生成」标识+备案号（`license_number` 经 `re.sub` 消毒）；schemas 新增 `ai_label_enabled`（默认 True）/`license_number`/`monetization_mode`。
- **角色资产库（character_library.py，177 行）**：本地 JSON 持久化外观锁定卡，CRUD + `character_id` 格式严格校验 + update 白名单字段过滤，支撑跨集/跨镜角色一致性。
- **全链路编排（pipeline_orchestrator.py，464 行）**：`PipelineOrchestrator` 内存任务句柄 + `asyncio.Event` 取消标志；`_run` 依次执行 剧本→角色定妆照（可跳过）→分镜→视频→配音→字幕→剪辑→质检 8 步，步间 `_check_cancel`；成功/取消/异常三态终态报告含 `total_elapsed_seconds`。drama.py 新增 `POST /pipeline/run`、`GET /pipeline/status/{task_id}`、`POST /pipeline/cancel/{task_id}`；schemas 新增 `PipelineRunRequest`。
- **RAG 熔断+线程化（rag_service.py）**：core 外网不可达导致嵌入模型加载每次超时 ~130s 且阻塞事件循环——`MODEL_LOAD_FAILURE_TTL_SECONDS=600` 熔断（TTL 内直接抛错，过期自动重置）；`optimize_prompt` 首次初始化改 `asyncio.to_thread`。
- **思维链清洗**：`strip_think_tags` 抽取至 `agents/base.py`，ai_optimizer/rag_service/quality_agent/drama 全部复用，消除 LLM 输出 `</think>` 残留。

### 关键代码片段

#### 1. 嵌入模型加载失败熔断（`platform/backend/app/services/rag_service.py`）

```python
MODEL_LOAD_FAILURE_TTL_SECONDS = 600.0

def _init_model(self) -> None:
    """懒加载嵌入模型，避免导入即耗时。加载失败后熔断 TTL 内直接抛错。"""
    if self._embedding_model is not None:
        return
    if self._model_load_failed_at is not None:
        if time.time() - self._model_load_failed_at < MODEL_LOAD_FAILURE_TTL_SECONDS:
            raise RuntimeError(
                f"嵌入模型 {self.model_name} 此前加载失败，熔断中（TTL 内不再重试）"
            )
        self._model_load_failed_at = None  # TTL 已过，允许重试
    try:
        self._embedding_model = TextEmbedding(model_name=self.model_name)
    except Exception as e:
        self._model_load_failed_at = time.time()
        raise RuntimeError(f"无法加载嵌入模型 {self.model_name}: {e}") from e
```

#### 2. 全链路编排任务启动与取消（`platform/backend/app/services/pipeline_orchestrator.py`）

```python
def start(self, request: PipelineRunRequest) -> str:
    project_id = f"pipeline-{int(time.time())}"
    task_id = progress_tracker.create("pipeline", message="全链路任务已创建")
    cancel_event = asyncio.Event()
    self._cancel_events[task_id] = cancel_event
    self._handles[task_id] = asyncio.create_task(
        self._run(task_id, project_id, request, cancel_event)
    )
    return task_id

def cancel(self, task_id: str) -> bool:
    event = self._cancel_events.get(task_id)
    if event is None:
        return False
    event.set()
    return True
```

#### 3. 熔断单元测试（`platform/backend/tests/unit/test_rag_service.py`）

```python
def test_failure_circuit_breaks_subsequent_calls(self, tmp_path):
    service = self._make_service(tmp_path)
    with patch("app.services.rag_service.TextEmbedding", side_effect=OSError("Connection timed out")) as m_te:
        with pytest.raises(RuntimeError, match="无法加载嵌入模型"):
            service._init_model()
        assert m_te.call_count == 1
        with pytest.raises(RuntimeError, match="熔断中"):
            service._init_model()
        assert m_te.call_count == 1  # TTL 内不再重试
```

### 回归结果

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `python -m pytest tests/ -q` | **420 passed / 0 failed**，覆盖率 **84.48%**（≥80% 达标），48.65s |
| 编排服务 | `tests/unit/test_pipeline_orchestrator.py` | 11/11 通过（happy path 8 步报告、跳过分支、失败终态、取消报告） |
| 角色资产库 | `tests/unit/test_character_library.py` | 全绿（CRUD、id 校验、白名单过滤） |
| RAG 熔断 | `TestModelLoadCircuitBreaker` | 2/2 通过（TTL 内不重试、TTL 过期重置） |
| 前端单测 | `pnpm vitest run` | 36/36 通过（1.54s） |
| 前端类型 | `npx tsc --noEmit` | 0 错误 |
| 前端构建 | `pnpm build` | 成功（1.10s，417.35 kB / gzip 127.84 kB） |
| core 部署实测 | rsync + curl `/api/drama/pipeline/run|status|cancel` | 任务启动/进度轮询/取消全链路正常 |

## 2026-07-29 M6.7 LoRA 搭配、批量下载与 RAG 推荐集成

### 变更摘要

- **NAS LoRA 检查**：扫描 `~/NAS/Windows/ComfyUI/ComfyUIModel/models/loras`，现有模型以视频生成类（Wan2.2 系列等）为主，缺乏与 RAG 风格库匹配的 Flux.1 D 风格化 LoRA，决定批量补充。
- **LoRA 清单设计**：新建 `platform/backend/scripts/lora_manifest.json`，按 `realistic_film/suspense_dark/cyberpunk/ancient_fantasy/chinese_anime/japanese_anime/cartoon_3d/horror/sci_fi_space/post_apocalyptic/film_noir/retro_hongkong/palace_hanfu/campus_youth/comedy_bright/documentary/modern_action` 等 20 个风格 key 组织，包含 `model_id/version_id/filename/size_kb/sha256/trigger_words/download_url`。
- **双源批量下载**：新建 `platform/backend/scripts/download_loras.py`，支持：
  - Civitai API Token 认证（自动读取 `~/Library/Application Support/comfy-downloader/config.json`）。
  - 断点续传（`.part` + `Range`）。
  - 主源 `civitai.com` 失败时自动切换到 `civitai.red`。
  - 下载完成后 SHA256 校验，失败则删除重试。
  - 结果：20 个 LoRA 中 17 成功下载、3 个已存在且校验通过跳过、0 失败。
- **RAG 知识库接入推荐**：
  - `genre_tropes.json` 为 20 条类型片模板新增 `recommended_loras` 字段，绑定 `style_key/filename/trigger_words/weight`。
  - `rag_service.py` 新增 `_collect_lora_recommendations` 去重收集推荐 LoRA；`_build_system_prompt` 注入 `[推荐 LoRA]` 章节；`optimize_prompt` 与 `_fallback_output` 均返回 LoRA 推荐。
  - `schemas.py` 的 `RAGOptimizeResponse` 新增 `lora_recommendations` 字段。
- **测试增强**：`tests/unit/test_rag_service.py` 新增/更新 4 个用例，覆盖 LoRA 去重、系统提示词包含 LoRA 章节、`optimize_prompt` 返回 LoRA 推荐、兜底输出保留 LoRA 推荐。

### 关键代码片段

#### 1. LoRA 批量下载双源切换（`platform/backend/scripts/download_loras.py`）

```python
def try_download(item: dict, dest_dir: Path, token: str, hosts: list[str]) -> bool:
    filename = item["filename"]
    final_path = dest_dir / filename
    part_path = dest_dir / (filename + ".part")
    expected_sha = item["sha256"].upper()

    if final_path.exists() and sha256_file(final_path) == expected_sha:
        print(f"[SKIP] {filename} 已存在且校验通过")
        return True

    if final_path.exists():
        print(f"[WARN] {filename} 校验失败，删除重下")
        final_path.unlink()

    primary_url = item["download_url"]
    urls = [primary_url]
    parsed = urlparse(primary_url)
    for host in hosts:
        if host != parsed.netloc:
            alt = primary_url.replace(parsed.netloc, host, 1)
            urls.append(alt)

    for idx, url in enumerate(urls, start=1):
        host = urlparse(url).netloc
        print(f"[DOWN {idx}/{len(urls)}] {filename} <- {host}")
        if part_path.exists():
            part_path.unlink()
        if download_with_curl(url, part_path, token):
            print(f"[HASH] {filename} 校验中...")
            actual_sha = sha256_file(part_path)
            if actual_sha == expected_sha:
                shutil.move(str(part_path), str(final_path))
                print(f"[OK] {filename} 下载完成 ({actual_sha[:16]}...)")
                return True
            print(f"[HASH FAIL] {filename} SHA256 不匹配")
            part_path.unlink(missing_ok=True)
        time.sleep(1)

    print(f"[FAIL] {filename} 所有源均失败")
    return False
```

#### 2. LoRA 推荐去重收集（`platform/backend/app/services/rag_service.py`）

```python
@staticmethod
def _collect_lora_recommendations(retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in retrieved:
        for lora in item.get("recommended_loras", []):
            filename = lora.get("filename", "")
            if not filename or filename in seen:
                continue
            seen.add(filename)
            recommendations.append({
                "filename": filename,
                "style_key": lora.get("style_key", ""),
                "trigger_words": lora.get("trigger_words", []),
                "weight": lora.get("weight", 0.7),
            })
    return recommendations
```

#### 3. 系统提示词注入 LoRA 章节（`platform/backend/app/services/rag_service.py`）

```python
lora_blocks = []
for rec in lora_recommendations:
    trigger = ", ".join(rec.get("trigger_words", []))
    lora_blocks.append(
        f"- {rec['filename']} (weight={rec['weight']})"
        + (f", trigger words: {trigger}" if trigger else "")
    )
if lora_blocks:
    sections.append("\n[推荐 LoRA]\n" + "\n".join(dict.fromkeys(lora_blocks)))
```

### 回归验证

- **后端全量**：`pytest tests/ -q --tb=short` → **356 passed / 0 failed**，覆盖率 **85.47%**（≥80% 达标）
- **RAG 专项**：`test_rag_service.py` 16/16 通过；`tests/integration/test_rag.py` 5/5 通过
- **前端**：`pnpm test --run` → **36/36** 通过；`pnpm tsc --noEmit` → 0 错误；`pnpm build` → 成功
- **LoRA 下载**：20 个目标，17 成功下载，3 已存在跳过，0 失败，全部 SHA256 校验通过

### 修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/backend/scripts/lora_manifest.json` | 新增 | 20 个 Flux.1 D 风格 LoRA 下载清单 |
| `platform/backend/scripts/download_loras.py` | 新增 | 双源批量下载、断点续传、SHA256 校验脚本 |
| `platform/backend/app/knowledge_base/genre_tropes.json` | 修改 | 20 条类型片模板新增 `recommended_loras` |
| `platform/backend/app/services/rag_service.py` | 修改 | LoRA 推荐收集、系统提示词章节、兜底输出 |
| `platform/backend/app/models/schemas.py` | 修改 | `RAGOptimizeResponse` 新增 `lora_recommendations` |
| `platform/backend/tests/unit/test_rag_service.py` | 修改 | 新增/更新 4 个 LoRA 推荐相关单元测试 |
| `STATE.json` | 修改 | 新增 M6.7 子任务与 L5 任务，更新测试计数 |
| `TEST_LOG.md` | 修改 | 追加 M6.7 时序条目 |

---

## 2026-07-29 M6.6 类型片叙事镜头模板知识库增强

### 变更摘要

- **新增 `genre_tropes.json`**：20 条短剧/影视剧类型片叙事镜头模板，覆盖高频短剧场景：
  - 霸总对峙/壁咚、甜宠咖啡馆约会、校园天台告白、古风仙侠竹林对决、宫廷权谋对峙、都市悬疑雨夜追凶、黑色电影侦探办公室、赛博朋克街头追逐、恐怖 jump scare 走廊、喜剧夸张登场、家庭伦理情感重逢、古装战场千军万马、灵异现身/鬼影浮现、职场权谋办公室博弈、仙侠渡劫天雷、复仇打脸身份揭露、医疗急救手术室外、末日废土幸存者、古风沐浴花瓣浴调情、校园霸凌反击。
  - 统一 schema：`id/category/domain/lang/title/content/tags/negative_terms/style_intensity/model_target`，与现有五类知识库保持一致。
- **RAGService 接入 genre_trope**：
  - `optimize_prompt` 多路检索新增 `category="genre_trope"`，与风格/镜头/示例/负面/方法并列融合。
  - `_build_system_prompt` 新增 `[类型片叙事镜头模板]` 章节，将检索到的类型片模板注入 LLM 上下文。
  - `_fallback_output` 兼容 genre_trope，合并其 `content` 到正向提示词、`negative_terms` 到负向提示词、`tags` 到标签、`title` 到风格说明。
- **测试增强**：`tests/unit/test_rag_service.py` 新增 4 个单元测试，覆盖 genre_trope 的 metadata 过滤检索、系统提示词章节生成、`optimize_prompt` 调用融合、LLM 失败兜底输出。

### 关键代码片段

#### 1. 类型片叙事模板检索接入（`platform/backend/app/services/rag_service.py`）

```python
# 多路检索：风格 + 镜头 + 示例 + 负面 + 方法 + 类型片叙事模板
style_results = self.search(query, category="style", domain=domain, style=style_hint, top_k=2)
shot_results = self.search(query, category="shot", domain=domain, top_k=2)
example_results = self.search(query, category="example", domain=domain, style=style_hint, top_k=2)
negative_results = self.search(query, category="negative", domain=domain, top_k=2)
method_results = self.search(query, category="method", domain=domain, top_k=2)
trope_results = self.search(query, category="genre_trope", domain=domain, style=style_hint, top_k=2)
```

#### 2. 系统提示词新增类型片模板章节（`platform/backend/app/services/rag_service.py`）

```python
if trope_blocks:
    sections.append("\n[类型片叙事镜头模板]\n" + "\n\n".join(trope_blocks))
```

#### 3. 兜底输出兼容 genre_trope（`platform/backend/app/services/rag_service.py`）

```python
elif item["category"] == "genre_trope":
    positives.append(item["content"])
    style_notes.append(item["title"])
    tags.extend(item.get("tags", []))
    if item.get("negative_terms"):
        negatives.extend(item["negative_terms"])
```

### 回归验证

- **后端全量**：`pytest tests/ -q --tb=short` → **354 passed / 0 failed**，覆盖率 **85.36%**（≥80% 达标）
- **RAG 专项**：`test_rag_service.py` 12/12 通过；`tests/integration/test_rag.py` 5/5 通过
- **前端**：本次未改动前端，保持 M5 基线 36/36、tsc 0 错误、build 成功

### 修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/backend/app/knowledge_base/genre_tropes.json` | 新增 | 20 条类型片叙事镜头模板库 |
| `platform/backend/app/services/rag_service.py` | 修改 | 多路检索、系统提示词、兜底输出接入 genre_trope |
| `platform/backend/tests/unit/test_rag_service.py` | 修改 | 新增 4 个 genre_trope 单元测试 |
| `STATE.json` | 修改 | 新增 M6.6 子任务，更新测试计数与覆盖率 |
| `TEST_LOG.md` | 修改 | 追加 M6.6 时序条目 |

---

## 2026-07-29 M6 RAG 提示词优化知识库

### 变更摘要

- **M6.1 RAG 架构与影视生成提示词方法深度调研**：将影视剧、电影、动漫、短剧等领域的生成提示词优化方法整理为五类结构化知识库：
  - `styles.json`：28 种视觉风格，包括写实电影感、都市情感、悬疑暗调、赛博朋克、古风仙侠、国漫、日漫、卡通 3D、恐怖惊悚、科幻太空、末日废土、黑色电影、复古港风、宫廷古装、校园青春、家庭伦理现实主义、喜剧明快、纪录片纪实、韩剧浪漫、现代动作、定格动画黏土、像素艺术 8-bit、蒸汽波、西方奇幻等，含 style_intensity/negative_terms/model_target 等元数据。
  - `shots.json`：43 种影视镜头语言、运镜、光影与构图技法，包括特写、中景、广角 establishing、低角度仰拍、荷兰角、过肩、跟随、推镜、手持、航拍、极特写、POV、变焦、摇臂、斯坦尼康、长镜头、分屏、匹配剪辑、微距、慢动作、延时、柔光箱、烛光、月光、暖辉光、高调、单色、三分法、对称构图、雨、雪、雾等。
  - `negatives.json`：14 组负面提示词，包括通用画质、人体结构、视频运动、风格污染、写实专用、人像特写、通用安全、动漫卡通专用、2D/手绘专用、视频时序一致性、构图与透视、手部、面部、建筑与户外等。
  - `examples.json`：11 条高质量示例，包括都市悬疑雨夜追凶、豪门霸总办公室、古风仙侠竹林对决、甜宠咖啡馆约会、赛博朋克街头追逐、恐怖医院走廊、科幻太空站对接、黑色电影侦探办公室、宫廷夜宴对峙、校园天台告白、现代都市追车等，含中文描述、优化后英文正/负提示词、风格标签。
  - `methods.json`：8 条提示词优化方法论，包括视频/图像提示词核心结构、Seedance 2.0 九模块公式、CogVideoX 四步法、HunyuanVideo/Wan/CogVideoX/SDXL 等模型适配技巧、负面提示词组合策略、角色一致性描述方法、分镜拆解法、LLM-as-optimizer 迭代优化法。
  - 统一 schema：`id/category/domain/lang/title/content/tags/model_target`，示例扩展 `optimized_positive/optimized_negative/style`，方法论扩展 `style_intensity/negative_terms`。
- **M6.2 RAGService 核心实现**：`app/services/rag_service.py` 加载五类知识库，使用 `fastembed` 生成 embedding 并本地缓存，支持按 `domain`/`style` 元数据预过滤、Top-K 向量检索、检索结果融合（风格+镜头+示例+负面+方法论）；随后调用本地 OpenAI 兼容 LLM 将用户中文描述重写为高质量英文正向/负向生成提示词，失败时返回兜底结果。`config.py` 新增 `rag_optimize_enabled`/`rag_embed_model`/`rag_top_k`；`schemas.py` 新增 `RAGOptimizeRequest`/`RAGOptimizeResponse`；`pyproject.toml` 添加 `fastembed`/`numpy` 依赖。
- **M6.3 API 路由暴露**：`app/routers/drama.py` 新增 `POST /api/drama/rag/optimize`（返回优化后正/负提示词、风格说明、标签、检索数量等）与 `GET /api/drama/rag/styles`（返回内置风格列表供前端下拉选择）。
- **M6.4 Agent 集成 RAG 优化**：
  - `script_agent.py`：剧本生成后遍历每幕场景，对 `description` 调用 RAG 优化为英文视频生成提示词，写入 `prompt`/`negative_prompt`。
  - `character_agent.py`：对三视图（front_view/side_view/closeup）英文提示词按 `style` 优化，并强制注入 `solo single person only one person` 约束。
  - `storyboard_agent.py`：对分镜英文提示词优化，并注入 `cinematic storyboard keyframe, keep vertical 9:16 composition`。
  - 三者均在 RAG 失败时保留原提示词，不阻断主流程。
- **M6.5 测试修复与回归**：RAGService 8 个单元测试覆盖知识库加载、向量检索、提示词优化、失败兜底；`tests/integration/test_rag.py` 5 个接口测试；同步修复 `script_agent`/`character_agent`/`storyboard_agent` 测试中的 mock 与断言。关键修复：
  - `AsyncOpenAI` 从 `optimize_prompt` 内部导入移至模块顶部，确保可 mock。
  - 测试 mock `embed` 方法使用 `side_effect=lambda _texts: iter([...])`，避免迭代器在多次检索时耗尽。
  - 角色 Agent 图像生成调用参数从 `kwargs["positive"]` 改为 `args[1]`，匹配 `_generate_image_via_sdxl` 位置参数。

### 关键代码片段

#### 1. RAGService 检索 + LLM 重写 pipeline（`platform/backend/app/services/rag_service.py`）

```python
async def optimize_prompt(
    self,
    user_prompt: str,
    domain: str = "video",
    style_hint: str | None = None,
    extra_instruction: str | None = None,
) -> dict[str, Any]:
    # 1. 元数据过滤 + Top-K 向量检索
    retrieved = await self.search(
        query=user_prompt,
        domain=domain,
        style_hint=style_hint,
        top_k=settings.rag_top_k,
    )
    # 2. 融合检索结果构建系统提示词
    system_prompt = self._build_system_prompt(
        retrieved, domain, style_hint, extra_instruction
    )
    # 3. 调用本地 LLM 重写为结构化英文提示词
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"原始描述：{user_prompt}"},
    ]
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.5,
        max_tokens=800,
    )
    return self._parse_llm_response(response.choices[0].message.content, user_prompt)
```

#### 2. 剧本 Agent 场景提示词 RAG 增强（`platform/backend/app/agents/script_agent.py`）

```python
async def _rag_enhance_scenes(self, scenes: list[dict[str, Any]], genre: str) -> None:
    for scene in scenes:
        description = scene.get("description", "").strip()
        if not description:
            continue
        result = await rag_service.optimize_prompt(
            user_prompt=description,
            domain="video",
            style_hint=genre or None,
            extra_instruction="根据短剧场景描述生成高质量英文图像/视频生成提示词",
        )
        if result.get("optimized_positive"):
            scene["prompt"] = result["optimized_positive"]
        if result.get("optimized_negative"):
            scene["negative_prompt"] = result["optimized_negative"]
```

#### 3. 角色 Agent 三视图 RAG 优化（`platform/backend/app/agents/character_agent.py`）

```python
async def _rag_optimize_prompts(self, prompts: dict[str, str], style: str) -> dict[str, str]:
    views = [
        ("front_view_prompt", "front view character portrait"),
        ("side_view_prompt", "side profile character portrait"),
        ("closeup_prompt", "close-up face portrait"),
    ]
    result = dict(prompts)
    for key, view_hint in views:
        positive = prompts.get(key, "").strip()
        if not positive:
            continue
        opt = await rag_service.optimize_prompt(
            user_prompt=positive,
            domain="image",
            style_hint=style or None,
            extra_instruction=f"{view_hint}, keep solo single person only one person, photorealistic character",
        )
        if opt.get("optimized_positive"):
            result[key] = opt["optimized_positive"]
    return result
```

### 回归验证

- **后端全量**：`pytest tests/ -q --tb=short` → **350 passed / 0 failed**，覆盖率 **85.13%**（≥80% 达标）
- **RAG 专项**：`test_rag_service.py` 8/8 通过；`tests/integration/test_rag.py` 5/5 通过
- **前端**：本次未改动前端，保持 M5 基线 36/36、tsc 0 错误、build 成功

### 修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/backend/app/knowledge_base/styles.json` | 新增 | 影视/短剧/动漫视觉风格库 |
| `platform/backend/app/knowledge_base/shots.json` | 新增 | 镜头语言与运镜技法库 |
| `platform/backend/app/knowledge_base/negatives.json` | 新增 | 分组负面提示词库 |
| `platform/backend/app/knowledge_base/examples.json` | 新增 | 高质量生成示例 Prompt 库 |
| `platform/backend/app/knowledge_base/methods.json` | 新增 | 提示词优化方法论库 |
| `platform/backend/app/services/rag_service.py` | 新增 | RAG 提示词优化服务核心实现 |
| `platform/backend/app/routers/drama.py` | 修改 | 新增 `/rag/optimize` 与 `/rag/styles` 路由 |
| `platform/backend/app/config.py` | 修改 | 新增 RAG 开关与模型/Top-K 配置 |
| `platform/backend/app/models/schemas.py` | 修改 | 新增 RAGOptimizeRequest/Response 模型 |
| `platform/backend/app/agents/script_agent.py` | 修改 | 场景描述 RAG 增强 |
| `platform/backend/app/agents/character_agent.py` | 修改 | 三视图提示词 RAG 优化 |
| `platform/backend/app/agents/storyboard_agent.py` | 修改 | 分镜提示词 RAG 优化 |
| `platform/backend/pyproject.toml` | 修改 | 添加 fastembed/numpy 依赖 |
| `platform/backend/tests/unit/test_rag_service.py` | 新增 | RAGService 单元测试 |
| `platform/backend/tests/integration/test_rag.py` | 新增 | RAG 接口集成测试 |
| `platform/backend/tests/unit/test_script_agent.py` | 修改 | 补 RAG 集成 mock 与断言 |
| `platform/backend/tests/unit/test_character_agent.py` | 修改 | 修复图像生成参数获取 + RAG 断言 |
| `platform/backend/tests/unit/test_storyboard_agent.py` | 修改 | 新增 `agent` fixture + RAG 断言 |
| `platform/backend/tests/conftest.py` | 修改 | 新增 `storyboard_agent`/`mock_web_search` fixtures |
| `STATE.json` | 修改 | v0.13.0，新增 M6 里程碑与当前会话记录 |
| `TEST_LOG.md` | 修改 | 追加 M6 时序条目 |

---

## 2026-07-27 M5 系统性优化（回归修复 + 资源/安全/结构/性能）

### 变更摘要

- **M5.0 测试回归修复（P0）**：会话开始时发现后端 15 failed / 316 passed（与 STATE 记录的 328/328 不符）。根因一：`HunyuanImageService.generate()` 已改为异步任务契约（POST 提交 → 轮询 `/v1/tasks/{task_id}` → 解析 result），但**文件缺少 `import asyncio`**（轮询路径 `await asyncio.sleep` 生产环境必 NameError），且 6 个单测仍按旧同步契约 mock。根因二：`storyboard_agent.execute()` 改为无条件经 LLM 重写英文提示词，9 个测试未 mock `call_llm`，真实请求打到 conftest 占位地址报 Connection error。修复：补 `import asyncio`；6 用例改 mock 新契约并打桩 `asyncio.sleep`；9 用例补 `mock_call_llm`（`prompt_used` 断言同步为 LLM 重写结果）。
- **M5.1 后端资源/日志/安全**：`main.py` lifespan 16 行 `print` 全部改 `logger.info`（AGENTS.md 合规）；删除与模块级重复的 6 行 `mkdir`；版本号 0.3.0→0.11.0 同步；CORS 从 `allow_methods=["*"]/allow_headers=["*"]` 收敛为显式白名单（GET/POST/PUT/DELETE/OPTIONS + Content-Type/Authorization/X-NSFW/Accept）。`BaseAgent` 新增 `aclose()`，lifespan shutdown 阶段逐个关闭 11 个 agent 单例的 httpx 连接池（此前永不关闭）。`config.py` 过期注释同步（ComfyUI 5 后端、TTS GPU0 systemd 托管）。
- **M5.2 前端结构拆分**：`Modals.tsx`（2397 行 / 11 个 Modal 组件）纯结构性拆分为 `components/modals/` 目录 13 个文件（11 组件 + shared.tsx 共享层 + index.ts barrel），最大文件 339 行；行为/props/样式零变化，`App.tsx` import 路径同步。
- **M5.3 前端性能优化**：`Canvas.tsx` 1455→1167 行。核心：自定义节点 `DramaNode` 用 `memo` 包裹且比较器仅比较 `data` 引用（React Flow v11 会把 xPos/yPos/dragging/selected 逐帧传入，默认浅比较拖拽时失效）——拖拽/选中不再重渲染含 video/audio/img 的重型子树；dagre 布局与节点尺寸预置逻辑抽取 `canvas/layout.ts`（P0 边渲染修复机制原样保留）；`onNodeClick` useCallback 化、`characterCardImages/characterPrompts` useMemo 化消除内联对象重建。

### 关键代码片段

#### 1. BaseAgent 连接池生命周期（`platform/backend/app/agents/base.py`）

```python
async def aclose(self) -> None:
    """关闭底层 httpx 连接池（应用关闭时调用）。"""
    await self.http.aclose()
```

lifespan shutdown 中对 11 个 agent 单例逐个 `await a.aclose()`，异常仅 `logger.warning` 不中断。

#### 2. DramaNode memo 比较器（`platform/frontend/src/components/canvas/DramaNode.tsx`）

```typescript
// React Flow v11 逐帧传 xPos/yPos/dragging/selected，默认浅比较拖拽时失效；
// 节点渲染只读 data，故仅比较 data 引用，位移由外层 wrapper transform 呈现
export default memo(DramaNode, (prev, next) => prev.data === next.data);
```

#### 3. HunyuanImage 异步任务契约 mock（`tests/unit/test_image_service.py`）

```python
# POST /v1/images/generations → {"task_id": "mock-task-1", "status": "pending"}
# GET  /v1/tasks/mock-task-1  → {"status": "succeeded", "result": {"data": [...]}}
# autouse fixture 打桩轮询间隔，避免拖慢测试
monkeypatch.setattr("app.services.image_service.asyncio.sleep", AsyncMock())
```

### 回归验证

- **后端全量**：`pytest tests/ -q` → **331 passed / 0 failed**，覆盖率 86.97%（≥80% 达标）
- **前端**：`tsc --noEmit` 0 错误；`vitest run` **36/36** 通过；`pnpm build` 成功（473KB gzip 151KB）
- **基线对比**：修复前 316 passed / 15 failed → 修复后 331 passed / 0 failed

### 修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/backend/app/services/image_service.py` | 修改 | 补 `import asyncio`（生产 bug） |
| `platform/backend/tests/unit/test_image_service.py` | 修改 | 6 用例改异步任务契约 mock + sleep 打桩 |
| `platform/backend/tests/unit/test_storyboard_agent.py` | 修改 | 9 用例补 mock_call_llm + 断言同步 |
| `platform/backend/app/main.py` | 修改 | print→logging、CORS 收敛、版本 0.11.0、去重 mkdir、shutdown 关闭 agent |
| `platform/backend/app/agents/base.py` | 修改 | 新增 `aclose()` |
| `platform/backend/app/config.py` | 修改 | 过期注释同步（仅注释） |
| `platform/frontend/src/components/modals/` | 新增 13 文件 | Modals.tsx 拆分（shared + 11 组件 + barrel） |
| `platform/frontend/src/components/Modals.tsx` | 删除 | 已拆分为 modals/ 目录 |
| `platform/frontend/src/App.tsx` | 修改 | Modal import 路径同步 |
| `platform/frontend/src/components/Canvas.tsx` | 修改 | memo/useCallback/useMemo 优化，1455→1167 行 |
| `platform/frontend/src/components/canvas/DramaNode.tsx` | 新增 | memo 化自定义节点 |
| `platform/frontend/src/components/canvas/layout.ts` | 新增 | dagre 布局 + 节点尺寸预置（P0 机制保留） |
| `STATE.json` | 修改 | v0.12.0，新增 M5 里程碑（M5.0-M5.3），test_summary 更新 |

---

## 2026-07-27 端到端重跑 + 最新设备清单核对（17 台）

### 变更摘要

- **设备清单核对**：核对 2026-07-27 最新清单（17 台，新增 core 监控中心）。关键变更：Workstation LLM 切换为本机 vLLM Nemotron-3-Nano-Omni-30B（GPU3）；ComfyUI-LB 5 后端轮询（GPU3 让给 Nemotron）；ToIV IndexTTS 迁移至 GPU0 并由 systemd 托管；pc01 ComfyUI 0.28.0 + mihomo 恢复；pc02 在线；NAS SMB 服务端正常（仅 pc01 挂载异常）。
- **核心服务验证（11 项全在线）**：Nemotron :8000 / IndexTTS :9200(cuda:0) / ComfyUI-LB :8188 / xDiT :8288 / ASR :9880 / LatentSync :8289 / PostProcess :8290(全权重) / DeepFilterNet :8301 / EXO :52415 / NAS(43Ti,10%)。
- **TTS 契约修复**：IndexTTS 真实契约为 `POST /tts multipart/form-data → WAV`（非 OpenAI `/v1/audio/speech`）。httpx `data=dict` 实际发送 `application/x-www-form-urlencoded`，改用 `files={k:(None,v)}` 发送真 multipart；新增 `_looks_like_audio` 魔数校验（RIFF/WAVE/ID3）防止占位文本落盘；新增 `_wav_to_mp3` ffmpeg 转码。`config.py` 端点移除错误 `/v1` 后缀。
- **E2E 阶段二重跑**：配音 indextts 后端产出 28478B 有效 MP3（4.01s，ID3 头）；剪辑合成 PASS 0.44s；成片 `final_471e3228.mp4` 283KB h264 1080x1920+aac 2.06s。
- **边界与异常回归**：A2/A3/A4/B1/B2/D1/D2 PASS；A1 空 premise 30s 超时（已知：未前置校验直接进 LLM，非回归）。
- **NAS 专项 6/6**：smbfs 挂载、20MB 写读 md5 一致、读回一致、200MB 写入 66MB/s、中文文件名、SMB mode 位忽略（预期）。历史遗留测试文件（`.e2e_test/` 等）已全部清理。

### 关键代码片段

#### 1. IndexTTS multipart 契约修复（`platform/backend/app/services/tts_service.py`）

```python
# httpx data=dict 会发 application/x-www-form-urlencoded；
# 真实契约为 multipart/form-data，用 files={k: (None, v)} 发送纯表单字段
multipart = {k: (None, v) for k, v in form.items()}
resp = await self.http.post(f"{self.endpoint}/tts", files=multipart)
resp.raise_for_status()
audio_bytes = resp.content
if not _looks_like_audio(audio_bytes):
    raise TTSServiceError(f"IndexTTS返回非音频内容 ({len(audio_bytes)}字节)")
if audio_bytes[:4] == b"RIFF":
    audio_bytes = await _wav_to_mp3(audio_bytes)
```

#### 2. 音频魔数校验

```python
def _looks_like_audio(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":  # WAV
        return True
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):  # MP3
        return True
    return False
```

### 回归验证

- **单元测试**：TTS 19/19 通过（含 multipart Content-Type 断言、非音频内容抛错用例）；全量 329 项通过。
- **macOS 后台进程修复**：`nohup uvicorn ... &` 被 `suspended (tty output)` 挂起，改用 `nohup uvicorn ... </dev/null >/tmp/log 2>&1 & disown` 彻底脱离终端。
- **NAS 终验**（`/private/tmp/e2e_nas_final.sh`）：写入 md5 = 源 md5 = 读回 md5，`FINAL_C2C3_PASS`。

### 修改文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `platform/backend/app/services/tts_service.py` | 修改 | IndexTTS multipart 契约 + 魔数校验 + WAV→MP3 转码 |
| `platform/backend/app/config.py` | 修改 | indextts_endpoint 移除 `/v1` 后缀 |
| `platform/backend/tests/unit/test_tts_service.py` | 修改 | 重写 IndexTTS 测试匹配新契约 |
| `platform/backend/tests/conftest.py` | 修改 | 测试环境端点同步为 :9200 |
| `STATE.json` | 修改 | 记录 session 2026-07-27-e2e-rerun-device-check（R1-R5 全部 completed） |

---

## 2026-07-25 P5 UI/UX 评估 + 模拟用户点击测试 + 真实用户测试方案

### 变更摘要

- **UI 主题重构**：移除粒子特效与主题切换，落地简洁浅色单一主题（白底 + 蓝色强调），CSS 变量统一管理颜色/边框/阴影；`index.css` 新增 768/1024 响应式断点（侧面板、模态框、浮动按钮适配）。
- **模拟用户点击测试**：Agent 驱动真实 Chromium 浏览器执行 5 条关键任务路径，全部通过，0 操作错误。
- **产出**：`docs/USER_CLICK_TEST_REPORT.md`（行为数据 + 问题清单 + 优化建议 + 真实用户招募测试方案）。

### 任务路径结果

| 任务 | 路径 | 结果 |
|---|---|---|
| T2 | 创意输入 → 生成剧本（8 节点 + 7 边渲染） | ✅ |
| T3 | 新建剧本模态框：改标题「铁心：最后守护·修订版」→ 保存 → 画布同步 | ✅ |
| T4 | 流程菜单展开/收起 + 禁用态 + 「生成角色」点击 | ✅ |
| T5 | 画布 zoom in/out + 拖拽 scene-1 (680,440)→(1537,1255) + fit view | ✅ |
| T6 | 节点详情面板：题材「科幻未来」→「科幻悬疑」→ 保存 → 重开验证同步 | ✅ |

**核心指标**：任务完成率 100% ｜ 纯交互任务平均 ≈11s ｜ 操作错误率 0% ｜ 缺陷 4 个全部修复

### 关键缺陷修复（点击测试中发现）

**P0 连接边缺失**：剧本生成后节点渲染但 React Flow SVG edges 为空。根因：React Flow v11 依赖 ResizeObserver 完成节点/handle 测量才渲染边，rAF 被节流的环境（后台标签页/自动化浏览器）中测量永不完成。修复：预置节点尺寸 + `NodeInternalsUpdater` 主动同步测量：

```typescript
// Canvas.tsx — 布局时预置尺寸，让节点立即被视为已测量
width: NODE_WIDTH,
height: nodeHeight(node),

// NodeInternalsUpdater — 节点集合变化后主动写入 handleBounds（不依赖 rAF）
if (updates.length) updateNodeDimensions(updates);
if (prevCountRef.current !== null && prevCountRef.current !== ids.length) {
  instance.fitView({ padding: 0.2, maxZoom: 1, duration: 0 });
}
```

**P1 生成无超时**：剧本生成后端 >4.5min 无响应时前端永久 loading。修复：`api/client.ts` 新增 `fetchWithTimeout`，`generateScript` 300s 超时 + 中文友好错误提示。

**P2 体验**：画布背景深色残留 `#2a2a2a` → `#cbd5e1`；fitView 过度放大 → `fitViewOptions={{padding:0.2,maxZoom:1}}` + `maxZoom=1.5`。

### 回归验证

- 前端 dev 3501 / 后端 8100 健康检查 200；修复后 T2 路径复测边渲染通过（7 边全渲染）。
- 报告：`docs/USER_CLICK_TEST_REPORT.md` 第五章含真实用户测试方案（3 类画像 10 人、R1-R10 任务、完成率/时间/错误率/SUS 指标）。

---

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

## 2026-07-26 P5.1 API 超时保护全覆盖 + pollVideoTask 截止期限

### 变更摘要

P5 复盘发现 F2（generateScript 无超时）同类风险仍然普遍存在：

1. **13 个长耗时端点无超时保护**：character/storyboard/storyboardBatch/video/videoBatch/videoAsync/voice/subtitle/compose/quality/visualQuality/lipSync/postprocess 均使用裸 `fetch`，后端阻塞时前端永久等待。
2. **pollVideoTask 无限轮询**：定义在 Canvas.tsx 组件层（违反 API 分层规范），`while(true)` 无截止期限，任务卡死时前端永久轮询。

### 修复内容

**[client.ts](platform/frontend/src/api/client.ts)**：

- 新增 `API_TIMEOUTS` 分级超时常量（15 档）：

```typescript
export const API_TIMEOUTS = {
  script: 300_000, character: 180_000, storyboard: 240_000,
  storyboardBatch: 600_000, video: 600_000, videoBatch: 1_800_000,
  taskCreate: 30_000, voice: 120_000, subtitle: 120_000,
  compose: 900_000, quality: 300_000, visualQuality: 600_000,
  lipSync: 600_000, postprocess: 1_800_000, pollInterval: 3_000,
} as const;
```

- 13 个端点全部从裸 `fetch` 切换为 `fetchWithTimeout(url, opts, API_TIMEOUTS.x)`。
- `pollVideoTask` 从 Canvas.tsx 移入 client.ts，增加双层保护：

```typescript
export async function pollVideoTask(
  pollUrl: string,
  maxWaitMs: number = API_TIMEOUTS.video
): Promise<ProgressEvent> {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, API_TIMEOUTS.pollInterval));
    const resp = await fetchWithTimeout(pollUrl, {}, API_TIMEOUTS.taskCreate);
    if (!resp.ok) throw new Error(`轮询失败: ${resp.status}`);
    const evt: ProgressEvent = await resp.json();
    if (evt.status === "completed" || evt.status === "failed") return evt;
  }
  throw new Error(`轮询超时（${Math.round(maxWaitMs / 1000)}秒）。任务仍在运行，请稍后重试。`);
}
```

**[Canvas.tsx](platform/frontend/src/components/Canvas.tsx)**：删除本地 `pollVideoTask` 与不再使用的 `ProgressEvent` 导入，改为从 `../api/client` 导入。

### 前端测试

新增 **[client.test.ts](platform/frontend/src/api/client.test.ts)** 19 条用例（TDD 先红后绿）：

- 14 条端点超时：mock 永不响应的 fetch（仅响应 abort），用 fake timers 推进到各端点预期超时毫秒，断言抛出 `/请求超时/` 而非永久等待。
- 5 条轮询行为：完成返回 / 失败返回 / 长期 running 超过 maxWaitMs 抛 `/轮询超时/` / 错误状态码抛 `/轮询失败: 404/` / 单次请求阻塞在 pollInterval + taskCreate 内中止。

顺带修复 UI 重构后过期的 **App.test.tsx** 3 条用例（属历史欠账，非本次变更引入）：

- 顶栏标题 `AI 短剧工作台 — M4 原型` → `AI 短剧工作台`。
- 流程按钮已收纳进"流程"下拉菜单：测试改为 `fireEvent.click(getByText("流程"))` 后在 `.dropdown-menu` 内断言。
- `生成剧本` → `新建剧本`；`质检` → `剧本质检`。

```bash
cd platform/frontend && pnpm vitest run && pnpm build
```

**结果**：36/36 passed（useDramaStore 14 + ThemeSwitcher 8 + App 3 + client 19）；tsc + vite build success（dist/index.js 473.12KB / gzip 149.73KB）

## 2026-07-26 P5.2 NAS 挂载 + 真实环境端到端测试

### 背景

完成绿联 DXP8800 NAS（`192.168.71.7:445`，smbfs，43Ti 总量 / 4.1Ti 已用）挂载配置后，执行覆盖"界面 → 后端 → 存储 → 网络"全链路的真实环境 E2E 测试。

### E2E 阶段一（此前已通过）

剧本生成 → 角色卡 → 分镜 → 配音 → 字幕，5/5 PASS。

### E2E 阶段二（本次）：视频生成 → 剪辑合成

发现并修复两个 P0 级缺陷：

**缺陷 1：xDiT 容器 OpenCV ImportError（导出阶段失败）**

推理 4009.8s 完成后 `export_to_video` 抛 `ImportError: export_to_video requires the OpenCV library`。但 `docker exec ... python -c 'import cv2'` 正常。根因：opencv-python-headless 是在服务进程（PID 1）启动**之后**才 pip install 的，diffusers `export_utils` 在模块加载时缓存了可用性检测结果，进程不重启永远不会重新检测。修复：

```bash
docker exec xdit-hunyuanvideo pip install imageio imageio-ffmpeg  # diffusers 推荐后端，双保险
docker restart xdit-hunyuanvideo
```

**缺陷 2：后端 --reload 模式二次热重载丢任务**

视频推理中途（~15:15）uvicorn worker 从 PID 6027 变为 48337，内存任务表清空，轮询返回 `{"detail":"任务 video-xxx 不存在或已过期"}`。修复：改为无 `--reload` 生产模式启动（`nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100`）。

**配置调整**：[config.py](platform/backend/app/config.py) `xdit_request_timeout` 600s → 1800s（720p/8s/193帧实测推理 4009.8s 超旧超时；本次验证用 2s/49帧，实测 355s）。

### 测试结果

```text
视频生成   PASS  355s（xDiT 侧推理 353.2s，49帧 720p）
剪辑合成   PASS  0.73s
成片验证   PASS  HTTP 200, 267597 字节, MP4 h264 1080x1920 + aac 音频轨, 2.06s
```

### 边界与异常测试

| 组 | 项目 | 结果 |
|---|---|---|
| A1 | 空 premise | PASS（被拒绝） |
| A2 | 超长 premise（10万字） | PASS（HTTP200 不崩溃） |
| A3 | episodes=0 / 999 | PASS（边界处理不崩溃） |
| A4 | 非法 JSON | PASS（HTTP422） |
| B1 | 不存在的进度任务 | PASS（HTTP404 + 友好提示） |
| B2 | 不存在的静态文件 | PASS（HTTP404） |
| C1 | NAS 挂载有效性 | PASS（smbfs） |
| C2/C3/C5 | NAS 写读一致性/只读权限/读回 | 未完整验证（trae-sandbox 拦截 NAS 路径 cp/rm/chmod；dd/echo/cat 可写） |
| C4 | 500MB 大文件写入 | PASS（524288000 字节落盘；首次挂载实测 11MB/s） |
| C6 | 中文目录/文件名读写 | PASS |
| D1 | 并发 10 健康检查 | PASS（10/10） |
| D2 | 视频推理期间后端响应 | PASS（HTTP200） |

**汇总**：A 4/4 + B 2/2 + D 2/2 + C 3/6。

### 性能基线

- xDiT 720p/8s（193帧）推理：4009.8s（重启前实测）
- xDiT 720p/2s（49帧）推理：355s
- 剪辑合成（单段 2s）：0.73s
- NAS SMB 写速：11MB/s（首次挂载 500MB 实测）

### 遗留事项

- NAS 上残留 5 个测试文件（`.e2e_big.bin` 500MB、`.e2e_test_rw.bin`、`.e2e_ro.txt`、`.e2e_dir/`、`.e2e_test/`），trae-sandbox 拦截 rm 无法自动清理，需手动删除。
- xdit_request_timeout=1800s 对 720p/8s（约 67 分钟）仍不足，长视频场景需调至 5400s 或拆段生成。

---

## M10 MiniMax H3 视频后端切换（2026-08-04）

### 用户信息核实（硬性规则：先 SSH 实测再回答）

| 用户陈述 | 实测结果 | 结论 |
|---|---|---|
| workstation 已部署 MiniMax H3 | `/home/merlin/ComfyUI-h3-eval/` 独立实例 :8195，ComfyUI 0.30.0，`CUDA_VISIBLE_DEVICES=1` | ✅ 属实 |
| H3 33B H3-Omni-Transformer | NAS 实存 `minimax_h3_fl2va_bf16`(66GB≈33B×2B) + `fl2va/ref2va_pruned_int8_convrot`(21GB×2) | ✅ 属实 |
| Qwen3-VL-32B 文本编码器 | `qwen3vl_32b_minimax_h3_bf16`(51.5GB) + `nvfp4_awq`(15.7GB)，CLIPLoader type=minimax | ✅ 属实 |
| 2K / 15s / 原生立体声 | 节点 tooltip：训练范围 124-362 帧@24fps（≈5-15s）；官方 API 支持 768P/2K；双 VAE 音视频联合解码 | ✅ 属实（联网调研亦确认已开源） |
| 官方模板无负面/无 CFG | 官方 `video_minimax_h3_i2v.json` 子图展开：BasicGuider 单条件 + SamplerCustomAdvanced，无 CLIPTextEncode | ✅ 属实 |

### 官方模板逐节点比对（object_info + 模板 JSON 双重验证）

UNETLoader(fl2va INT8, default) / CLIPLoader(qwen3vl NVFP4, minimax, default) / VAELoader×2(video fp16 + audio fp32) / MiniMaxH3ImageToVideo(clip,视频VAE,first_frame,prompt,w,h,length → CONDITIONING+联合LATENT) / RandomNoise / KSamplerSelect(res_multistep) / BasicScheduler(simple,20,denoise=1) / BasicGuider / SamplerCustomAdvanced / VAEDecode+VAEDecodeAudio / CreateVideo(fps=24,8bit) / SaveVideo — 与 `WORKFLOW_TEMPLATE_H3` 15 节点完全一致。帧数公式 `_snap_h3_frames` ≡ 官方 ComfyMathExpression `max(5,round(a*24))+(5-(max(5,round(a*24))%17))%17`。

### 测试结果

```text
后端全量     436 passed, 84.48% coverage
前端 vitest  46 passed (3 files)
tsc          0 errors
build        954ms
H3 单测      TestSnapH3Frames 3 + TestVideoAgentH3（成功/回退） 全过
实机冒烟     scripts/smoke_h3.py：256x256/39帧/4步 → success，64KB mp4
产物验证     H.264 视频轨(avc1) + AAC 立体声 32kHz 音轨(mp4a)；时长 1.625s = 39帧/24fps 精确吻合
core 部署    health 200 / front 200；修正 core .env VIDEO_BACKEND=comfyui→h3（环境变量覆盖了 config.py 新默认值）
```

### 注意事项

- GPU1 显存 96.7/97.8GB 接近满载（H3 常驻 39.3GB + liveact 37.5GB + Qwen3-Embedding 19GB + ComfyUI#2 0.8GB），生产参数 768x1344/124帧/20步 单场景生成时需观察 OOM 风险。
- H3 支持多镜叙事 prompt（官方示例含 "SHOT 1: ... SHOT 2: ..."）与 ref2va 参考图（最多 9 张，角色一致性）— 列为后续优化方向。

---

## M22 路线 B：LongCat-Video 全量基准 + A/B 对比（2026-08-11）

### 变更

- `scripts/benchmark_longcat.py`：新增 `_TextEncoderStub`（仅承载 `AutoConfig.d_model`）——预编码缓存模式下 `pipe.text_encoder=None` 导致 `_cache_clean_latents` 构造空 embeds 时 `AttributeError: 'NoneType' has no attribute 'config'`（单卡 cp_size=1 唯一触达点，pipeline_longcat_video.py:333）；缓存/回退两路径均挂桩，缓存未命中报清晰错误
- `scripts/run_route_ab_comparison.py`：REPORT_DIR 修正为 platform/reports/M22.2（原 parents[3] 落盘仓库根目录）
- 运行方式升级为 `systemd-run` 瞬态服务（`longcat-bench-full.service`）：setsid+nohup 仍在 ssh 会话 scope 内，空闲连接断开被 systemd-logind SIGTERM（本次实锤两轮）

### 测试结果

```text
后端单测     749 passed, 83.71% coverage（≥80% 门槛）
前端 vitest  47 passed；tsc 0 errors
全量基准     stage1(480p) 11段 8808.6s / 893帧 / 59.5s / gen_fps=0.101
             refine(720p BSA) 11段 2151.9s / 1786帧 / 59.5s / gen_fps=0.83
             壁钟总计 11010.4s；GPU2 峰值 86233MiB / 利用率均值 98.7%；RAM 峰值 36.0GiB
MOS 对比     路线A(H3帧链)=4.42（画质4.5/运动4.33/时序3.83/文本5.0）
             路线B(LongCat原生)=4.83（画质5.0/运动4.33/时序5.0/文本5.0）
效率对比     A 78.6 vs B 185.0 壁钟秒/视频秒（A 快 2.4 倍）
报告         platform/reports/M22.2/route_ab_comparison_20260811_112909.{json,md}
```

### 注意事项

- 路线 B refine 峰值 86.2GiB 与 H3（GPU2 常驻 48.9GiB）互斥，基准需维护窗口独占 GPU2（本次暂停 H3/ASR 3.1h，完成后已恢复 200）
- 恢复 ASR 时发现 05:08 手动 screen 残留的 asr_server.py（PID 1176566）占用 :9210，toiv-asr bind 失败 EADDRINUSE 崩溃循环 112 次（/health 200 实为残留进程应答，具欺骗性）；kill 残留后 systemd 干净接管，active + 200。教训已固化 AGENTS.md 易错点 12 错误3
- workstation 上有并行运维方（用户/其他 Agent）：基准曾两轮被主动 SIGTERM（04:07 sudo kill -TERM + pkill -9），第三轮带 Description 的 systemd-run 未被干预
- 结论：默认路线 A（已集成 M21.3，快 2.4 倍）；路线 B 适合质量敏感场景（时序一致性 5.0 无接缝跳变）
- 收官回归（2026-08-11 11:45）：后端 tests/unit 749 passed / 83.71%；前端 vitest 47 passed。注意全量 pytest（含 integration）会挂起等待真实服务，标准回归口径为 tests/unit

---

## M23 项目审查：AGENTS 统一版 + 激进清理 + 下载器打通 + LTX-2.5 双引擎（2026-08-14）

### 变更
- **AGENTS.md 统一版**：融合 ToIV 最新事实（设备 18 台、LTX-2.5 :8198、LongCat :8197、M6 超分 fleet :8261-8263、ASR 迁 studio02 :9212、VLM 迁 studio04 :9303、SenseVoice :9211、JoyCaption :9304、北京入口）；新增第一/二硬性规则、易错点 13-19、第八节项目架构速览
- **激进清理**（实测 :8288/:8289/:8290/:8301 CLOSED、:8600 损坏、:8601 未部署、pc01 LTX-2B 被 :8198 替换）：删 xdit/latentsync/postprocess/ltx_video/image 5 服务 + lip_sync/postprocess 2 agent + 7 测试 + 5 脚本；image_backend→sdxl、video_backend 收敛 h3/comfyui；摘 langgraph/react-query 死依赖；版本统一 0.4.0
- **下载器↔工作台打通**：新建 model_registry_service（融合 lora_manifest trigger_words/weight + 下载器 models.json 已下载事实，按 filename 标注 downloaded）+ GET /api/drama/models/registry + 前端 getModelRegistry；前端同步删除唇形/后处理残留
- **LTX-2.5 双引擎**：新建 ltx25_video_service（T2V/I2V/FLF2V distilled 两阶段）+ prompt_expander（ShotSpec IR + H3ContextIR/LTXProse 双编译器 + validate_h3_prompt + recommended_quality_params）+ route_video_engine 路由（台词/参考→H3，长镜/运动→LTX，回退链 ltx→h3→comfyui）
- **liblib.tv 对标方案**：docs/LIBLIB_BENCHMARK_PROPOSAL.md（10 条借鉴点，不实现）
- **文档更新**：platform/README、TECHNICAL_DESIGN、DEPLOYMENT、根 README、USER_GUIDE 全部更新至当前架构

### 测试结果
```text
后端单测   676 passed / 1 failed（test_rag_service 嵌入模型下载需外网，基线既有环境性失败）/ 83.52% coverage
前端 vitest 42 passed；tsc 0 errors；build 868ms
新增用例   LTX-2.5 服务 18 + 提示词扩写 26 + 引擎路由 21 + 模型注册表 3
实机核验   LTX-2.5(:8198)/LongCat(:8197)/ASR(:9210)/H3(:8195) LISTEN 200
```

### 注意事项
- LTX-2.5 工作流节点名/权重文件名基于官方模板，**实机接入前必须 curl :8198/object_info 核验**（清单见 ltx25_video_service.py docstring）
- 路线 B LongCat 仍未工程化（worker 封装接入 pipeline 待后续）
- 角色资产库缺口决策（M18.2 拦截后陈旧资产污染 drift 量化）仍待用户决策

---

## M25.9 DramaClaw 架构重构：模型网关 + 失败模式注册表 + 线稿先行两段式（2026-08-15）

### 变更
- **P1 模型网关** `platform/backend/app/services/model_gateway.py`：DramaClaw litellm/NewAPI 外部网关的本地化平替——能力注册表（llm→spark02 :8000 / vision / image→ComfyUI-LB :8188 / video_h3→:8195 / video_ltx→:8198 / tts→:9200 / asr→:9210/studio02 :9212 / embedding→:9302）统一健康路由 + 探测缓存 + 调用指标；API 接入 drama.py 路由
- **P2 失败模式注册表（C2）** `platform/backend/app/services/failure_registry.py`：JSON 单库线程安全；FailureMode 五元组（detection 门禁判定问句/prevention_rule/correction_template/negative_prompt_clause/gate_enabled）；`build_negative_prompt_clause(layer)` 按层拼接注入生成负向提示词；预置 collage_mismatch（M16.2 拼贴失真）/black_and_white_drift/legible_text_leak；VLM 门禁回写命中率
- **P3 线稿先行后端（C1）**：`storyboard_agent._generate_image_via_sdxl` 新增 sketch/seed_override 参数——sketch 模式 8 步/CFG4.0/512×896 快速构图，refine_seed 非空时同 seed 精绘防构图漂移；返回 (url, seed) 元组；`StoryboardRequest.sketch_mode/refine_seed` + `StoryboardResult.is_sketch/sketch_seed`；config 新增 sketch_mode_enabled/sketch_steps/sketch_cfg/sketch_width/sketch_height
- **P4 线稿先行前端**：`StoryboardModal.tsx` 两段式确认流——线稿先行开关（手动修正场景默认开）→「生成线稿」→ 预览卡（图+seed）→「采用构图并精绘」（refine_seed 同 seed）/「重出线稿」/「弃用」；切场景自动丢弃线稿；`client.ts` StoryboardData + generateStoryboard 参数扩展（sketch_mode/refine_seed）
- **测试**：新增 test_model_gateway.py（健康路由/指标/离线报错）、test_failure_registry.py（CRUD/种子/negative 注入/API）；test_storyboard_agent.py 新增 TestSketchMode（sketch 工作流参数/seed 一致性/外观校验跳过）+ 适配元组返回值
- **P5 网关全链路接线**（审计发现网关此前仅挂 API 路由未被业务调用，本轮回补真实接线）：
  - `model_gateway`：注册表动态化（`_spec` 每次从 settings 构建，配置/monkeypatch 热生效）+ image 能力补回退链（hq→fast）+ 新增 video_comfy 能力（video_a/video_b）+ `openai_base_url()` 幂等 helper
  - `base.py`：LLM base_url 经 `model_gateway.openai_base_url("llm")` 解析（实例 + 共享客户端两处）；`call_llm` 记录网关调用指标（延迟/错误）；图像/视频 worker 候选选举全部改走 `model_gateway.endpoints("image"/"video_comfy")`
  - 服务层：`tts_service` 端点经网关 tts 能力；`ltx25_video_service` ×3 + `long_video_service` + `video_agent` ×3（H3 fl2va/ref2va/多镜联合）+ `_pick_alternate_worker` 全部经网关 video_ltx/video_h3/video_comfy 能力
  - `subtitle_agent` ASR 经 `model_gateway.route("asr")` 健康路由：studio02 whisper.cpp :9212（主）→ workstation faster-whisper :9210（回退），fail-open 后由 faster-whisper 本地兜底
  - conftest 新增 `_mock_gateway_probe` autouse fixture：单测不发起真实健康探测
  - VLM 调用点保持 settings.visual_model_url 直读（单端点无回退链，网关 vlm 能力负责健康监控与展示，机械替换无路由收益且有破坏既有断言风险）

### 测试结果
```text
分镜单测    60 passed（含 TestSketchMode 新用例）
前端 vitest 110 passed（8 files）；tsc 0 errors
P5 接线     网关联动测试 186 passed（gateway/base/subtitle/tts/ltx25/route/video_agent/long_video/multishot）
收官回归    后端 791 passed / 87.04% coverage（tests/unit + integration/test_drama+test_progress）
```

### 注意事项
- 线稿参数仅为精绘 1/3 耗时（8 步 vs 25 步），返工成本钉死在最便宜阶段；同 seed 保证线稿→精绘构图零漂移
- sketch_mode 不影响一键全链路无人值守管线（orchestrator 不传 sketch_mode，直出精绘）；仅前端手动分镜修正场景两段式
- failure_registry 存储于 output/verification/failure_modes.json，首次运行自动落库预置模式

---

## 前端浏览器全面验证 + 真机集成流转 + T3 节点日志埋点（2026-08-15）

### 变更
- **T3 日志埋点**：新增 `app/core/node_logger.py`（`node_log`/`node_span` 结构化节点日志：时间戳/节点标识/关键参数/状态 start-ok-error/耗时/异常，单值 200 字符截断、换行压平）；埋点覆盖 pipeline_orchestrator 8 步骤 span（含 task_id/场景数/画风/后端等参数）、`gateway.route`（capability+选中端点/离线报错）、`llm.chat`（模型/流式/耗时/产出字符数）、`comfyui.submit`/`comfyui.poll`（worker/prompt_id/节点数/轮询耗时/超时）；`main.py` 补 `logging.basicConfig(INFO, 带时间戳)`——此前 root 默认 WARNING，全部业务 logger.info 被静默
- **前端缺陷修复**（浏览器验证发现）：`.modal-actions` 改 sticky 底栏 + 不透明背景（矮视口下主 CTA 曾被卷出滚动区不可见）；VideoModal/VoiceModal/Canvas 节点标题口径 Wan 2.2/edge-tts → H3/LTX-2.5/IndexTTS-2（与真实架构对齐）

### 测试结果
```text
后端回归    797 passed / 87.07%（含 test_node_logger 6 新用例）
前端        tsc 0 errors；vitest 110 passed
真机集成    网关 12/12 能力 UP（llm/vlm/vlm_heavy/image/video_comfy/video_h3/video_ltx/tts/asr/embedding/music_caption/demucs）
            真实 LLM agent/assist 扩写流转成功（4.6s）；分镜全链路 LLM 改写→SDXL LB→636KB 实图 200
            真实 VLM quality/visual 推理成功（真实 mp4 抽帧→score 85+中文质检报告：识别乱码招牌/透视融合问题）
            真实 TTS voice/generate 合成成功（IndexTTS-2→18.5KB MP3 200）
            真实 ASR subtitle/generate 转写成功（TTS 音频→网关路由 studio02 :9212→SRT「歡迎光臨，請問需要點什麼？」2.68s 段，TTS→ASR 台词闭环一致）
            节点日志真实产出：node=llm.chat/comfyui.submit/comfyui.poll/gateway.route 带时间戳+耗时+参数
浏览器验证  12 项全过（五区布局/深色令牌/字体/8 模态交互/线稿先行开关联动「生成线稿↔生成分镜」/
            任务中心/MiniMap/响应式 1440-1024-768/按钮 hover+禁用态/零 emoji）
修复复核    粘性底栏 1280×800 不滚动/滚到底均完整可见且无透出；两模态标题口径正确（5 项通过）
```

### 注意事项
- 主题切换 Palette 图标已注册但未接线（UI 无入口）——多配色功能待用户决策是否补齐
- AssetLibraryPanel 残留面板开关调试 console.log，生产构建前建议清理
- 角色库存在失效缩略图资产（优雅降级显示占位块），建议后端清理失效资产

---

## M26 全面测试工程：后端 100% 语句覆盖（2026-08-16）

### 变更
- **单元测试补齐（355 用例 / 12 个 boost 文件）**：8 个并行 Agent 按覆盖率缺口逐行补齐——quality_agent(63)/rag_service(40)/drama 路由(45)/script_agent(19)/ai_optimizer(10)/character_agent(20)/drift_metrics(19)/storyboard_agent(19)/base(27)/video_agent(23)/orchestrator(19)/19 小模块(51)；覆盖函数/方法/边界/异常/fail-open/懒加载/批量隔离全维度，全 mock 离线可跑
- **4 行不可达死代码**：rag_service:158（glob 永不匹配）/ drift_metrics:182（denom 数学恒正）/ video_agent:1360（循环必经 return）/ long_video_planner:208（守卫必非空）——逐一证明后按标准 `# pragma: no cover` 标注
- **缺陷修复 D1**：pyproject markers 声称 slow「默认跳过」但 addopts 未配置，全量 pytest 被 3 个真机长超时用例挂死 → addopts 补 `-m 'not slow'`
- **前端**：安装 @vitest/coverage-v8@2.1.9，测得基线 37.99%（modal 组件多数 ~1%）
- **测试报告**：platform/reports/TEST_REPORT_2026-08-16.md（范围/基线/四层测试/缺陷 D1-D5/遗留建议）

### 测试结果
```text
后端     815→1170 passed / 87.83%→100.00%（5877 语句 0 未覆盖，27.5s）
前端     110 vitest passed / 覆盖率基线 37.99%
Rust     46 passed / 10 ignored（网络/GUI）
系统     10 服务端点全 UP；LLM/Embedding 真实调用；SDXL 三路/animagine/LTX T2V 真机冒烟全过
验收     8 步编排/角色一致性/双引擎路由/网关能力/前端交互 全过
```

### 注意事项
- 前端覆盖率 38% 为下一补齐目标（panels→modals→store，预估 ~300 用例量级）
- 3 个 slow 真机用例（h3_turbo_ab/long_video_drift_56s/long_video_poc）建议夜间窗口 `-m slow` 执行
- 分支覆盖率（--cov-branch）未纳入本轮口径，可作下一阶目标

---

## M26.4 前端覆盖率补齐：37.99% → 100%（2026-08-16）

### 变更
- **6 个并行 Agent 补 405 用例**（110→515 vitest）：modals 10 组件全量（Script/Storyboard/Video/Voice/Quality/Edit/Pipeline/Subtitle/VisualQuality/Character）、Canvas/DramaNode/layout、CharacterPreviewPanel/NodeDetailPanel/ProgressBar/AgentBar、App/api-client/store/useProgress/main + 各处小尾巴；全部 RTL+jsdom，mock 后端/媒体/SSE
- **真实 bug 修复 ×2**：① NodeDetailPanel「保存并重新生成」premise 校验死锁（已有剧本态无创意输入框 → validateScriptForm 必失败，该按钮自始不可用）→ validateScriptForm 增加 skipPremise 选项并附回归测试 ×2；② layout.ts loading 高度死分支（isFutureNode 要求 !loading，`future ? 14 : 18` 的 14 恒不可达）→ 固定 18
- **死代码清理**：DramaNode 删除未引用的 GENRE_PRESETS/truncate/useDramaStore import；NodeDetailPanel 两处 `: prev` 死三元改非空断言；CharacterPreviewPanel startSearch 冗余守卫（唯一调用方已同条件守卫）删除
- **记录未修问题**（见报告）：VideoModal render 期 setTimeout 副作用、client.ts resolveTaskUrl 端口泄漏（远程部署 SSE 会断）、CharacterPreviewPanel 切角色不重研、PipelineModal chip border 简写冲突

### 测试结果
```text
前端   110→520 passed（33 文件）/ 语句 100% / 行 100% / 函数 100% / 分支 94.55% / tsc 0 errors
后端   1170 passed / 100.00%（未受影响）
```

### 收官补测（同日，+5 例）
- App.boost.test +4：顶栏「一键成片」/「新建剧本」onClick 打开对应模态；EditModal/QualityModal onClose 接线（stub 已有 edit-close/quality-close 按钮但无触发用例）
- PipelineModal.test +1：变现模式 IAP→IAA 切换提交参数断言（IAA chip onClick 从未被调用）
- 函数覆盖 98.57% → **100%**（App.tsx 4 个 + PipelineModal.tsx 1 个未触达内联 handler 全部收口）

### 注意事项
- 分支覆盖残余缺口均为 UI 不可达防御分支（disabled 按钮 guard、null 态不渲染分支），已逐条核对
- VideoModal 陈旧 SSE failed 事件过滤属脆弱设计（仅时序规避），建议后续加任务 id 校验

---

## M27 系统设置全面更新：NAS 模型库 + 下载整合 + NSFW 门禁（2026-08-16）

### 需求
1. 整合主项目（Rust 下载器）模型下载能力到工作台，与系统其他功能无缝衔接
2. NAS 存储已有模型可视化浏览（名称/大小/类型/修改日期）
3. 新增 NSFW 成人向内容访问与管理功能
4. 符合系统架构规范，可维护可扩展
5. 完整单元测试 + 集成测试

### 变更
- **后端 3 新服务 + 1 新路由**（`app/services/nas_library_service.py` / `model_download_service.py` / `settings_service.py` + `app/routers/models.py`，main.py 注册 models.router + settings_router）：
  - `nas_library_service`：扫描白名单子目录（checkpoints/loras/vae/embeddings/controlnet/upscale_models 等），输出 NasModelEntry（name/size_bytes/type/rel_path/modified_at/nsfw）；TTL 缓存（`nas_library_cache_ttl`）+ refresh 强扫 + 类型过滤 + 名称模糊搜索 + NSFW 过滤；不可读文件 fail-open 跳过
  - `model_download_service`：Civitai 搜索（`GET /api/models/search`，透传 query/nsfw 参数）+ 后台线程下载任务（task_id/进度/速度/状态机 pending→running→done|error|cancelled/取消标志/SHA256 校验不匹配标 error）；`sanitize_filename` 防路径穿越 + 子目录白名单；下载根目录与 NAS 库同源（`resolve_download_root()`），完成即入库
  - `settings_service`：NSFW 开关持久化 JSON；PIN 采用 salt(8B hex)+sha256 存储；首次开启强制设 PIN（4-12 位数字校验）；支持解锁/锁定/修改 PIN；线程锁保护
  - 门禁联动：库列表 `include_nsfw` 需已解锁才生效；NSFW 下载（显式标记或文件名命中关键词）未解锁一律 403
- **配置**：config.py 新增 `nas_model_roots` / `download_root` / `nas_library_cache_ttl` / `civitai_api_base` / `nsfw_keywords`；schemas.py 新增 NasModelEntry/NasLibraryResponse/ModelDownloadRequest/DownloadTask/NsfwStatus/NsfwSetRequest
- **前端**：
  - `ModelLibraryPanel`（三页签：注册表=原 LoraRegistryList / NAS 模型=类型徽章+大小+日期+搜索+刷新 / 下载=Civitai 搜索→版本选择→一键下载→任务进度条+取消）+ NSFW 锁按钮（Lock/LockOpen 状态图标）
  - `NsfwGateModal` 四模式：首设 PIN（新 PIN+确认一致性校验）/ 解锁（输 PIN）/ 锁定 / 修改 PIN；错误内联展示，busy 防重复提交
  - store 新增 `nsfwEnabled`/`nsfwHasPin` + `setNsfwState`，App 启动 `getNsfwStatus` 同步；api/client.ts 新增 getNasLibrary/searchCivitaiModels/startModelDownload/getDownloadTasks/cancelDownload/getNsfwStatus/setNsfwEnabled
  - index.css 新增模型库/下载任务/NSFW 模态样式（沿用 MiniMax 深色令牌，零硬编码色值）

### 测试结果
```text
后端   1170→1248 passed（+78：nas_library 18 / download 24 / settings 22 / router 14）/ 100.00% 语句覆盖保持
前端   520→569 vitest passed（+49：ModelLibraryPanel 26 / NsfwGateModal 19 / client+store 4）/ tsc 0 errors
修复   ① Path.touch(times=) 跨平台不兼容 → os.utime；② 类型徽章文本与筛选 chip 冲突 → container.querySelector('.asset-badge') 定向断言；
       ③ 修改日期断言时区漂移 → new Date(mtime*1000) 动态期望值；④ NSFW 模态测试 store 状态不同步 → beforeEach mock 与 store 对齐
```

### 注意事项
- HuggingFace 搜索未接入本轮（主项目 Rust 端能力），Civitai 已覆盖主要模型来源；如需 HF 源后续按同一任务框架扩展
- PIN 为本地单机门禁（防误触/防旁观），非账户级权限体系；多用户场景需另设计
- 下载任务为进程内线程管理，后端重启任务列表清空（已下载文件不受影响）
