# 此次会话新增功能融合度梳理（2026-08-17）

> 目标：把"生图 NSFW 内容"做成端到端可用流程——从 R18 门禁、模型浏览、下载、配方选择、出图、视频生成一气呵成

## 一、新增/修改清单

### 1.1 后端（`/Users/wangzhenyu/Desktop/ALLProject/DashBox/`）

| 改动 | 文件 | 行号 | 性质 |
|---|---|---|---|
| `local_gateway` 接受 `checkpoint` body 参数 | [local_gateway/main.py](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/local_gateway/main.py) | [L297-L310](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/local_gateway/main.py#L297-L310) / [L710](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/local_gateway/main.py#L710) | 增强：可指定 SDXL 底模 |
| NSFW 关键词表 40+ 词 | [model_library.py](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/src/novelvideo/model_library.py) | [L45-L51](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/src/novelvideo/model_library.py#L45-L51) | 增强：自动 NSFW 标记 |
| Stale-while-revalidate 后台重扫 | [model_library.py](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/src/novelvideo/model_library.py) | [L233-L275](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/src/novelvideo/model_library.py#L233-L275) | 性能：冷 10-25s / 温 <1ms |
| 缓存 TTL 60s→600s | [model_library.py](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/src/novelvideo/model_library.py) | [L83-L84](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/src/novelvideo/model_library.py#L83-L84) | 性能 |

### 1.2 前端（`frontend/`）

| 改动 | 文件 | 行号 | 性质 |
|---|---|---|---|
| `useModelLibrary` 超时 300s + retry 2 | [model-library.ts](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/frontend/src/lib/queries/model-library.ts) | [L131-L153](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/frontend/src/lib/queries/model-library.ts#L131-L153) | 性能：避免冷启动 ERR_ABORTED |

### 1.3 文档与预设

| 新增 | 路径 | 用途 |
|---|---|---|
| 完整 8 视频搭配预设 | [presets/nsfw/wan22-nsfw-missionary.json](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/presets/nsfw/wan22-nsfw-missionary.json) 等 4 个 | 真机冒烟出片即用 |
| 预设说明 | [presets/nsfw/README.md](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/presets/nsfw/README.md) | 用法 + 必改参数点 + input 目录速查 |
| 90+ 模型标注 | [docs/zh/guides/nsfw-model-catalog.md](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/docs/zh/guides/nsfw-model-catalog.md) | 来源 + 用途 + 触发词 |
| 教学文档扩充 | [docs/zh/guides/nsfw-video-generation.md](file:///Users/wangzhenyu/Desktop/ALLProject/DashBox/docs/zh/guides/nsfw-video-generation.md) | 22→52 资源 + 跨引擎矩阵 |
| AGENTS.md 易错点 25 | [AGENTS.md](file:///Users/wangzhenyu/Desktop/ALLProject/AICG-DownLoader-main/AGENTS.md#L322-L326) | 4 坑：red 走代理 / LB input 目录 / H3 32 倍数 / Turbo 链 |

### 1.4 资产

- **批 6/7/8/9 共 56 件新模型**入库（NAS `Windows/.../models/loras` + `toiv/.../h3/loras` + `checkpoints`）
- 真机冒烟产物：`NAS/toiv/preset_smoke_20260817/` 3 个 mp4
- 真机首帧：`nsfw_first_frame.png` 已分发三端 ComfyUI input 目录

## 二、与项目融合度评估

### 2.1 已深融合 ✅

1. **DashBox 模型库面板**——浏览器实测 384 模型、81 NSFW 徽章、搜索/筛选/分页全可用
2. **R18 琥珀色确认对话框**——状态持久化、跨会话生效、双向切换干净
3. **预设 4 份**——直接粘贴进 ComfyUI 工作流编辑器即用（WorkflowRefsPanel 自动核验）
4. **local_gateway 出图**——可指定 lustify/pornmaster 等 NSFW 底模出 NSFW 首帧

### 2.2 浅融合 / 留有缺口 ⚠️

1. **前端无"出图"按钮**：DashBox 是 Canvas 画布工具（ImageGenNode 等），没有"打开应用一键出图"入口。`local_gateway :8790` 的 `/v1/images/generations` 是给 AI 短剧 pipeline 调用的，前端用户**无法从 UI 触发一次出图**
   - 临时方案：浏览器 demo 用 curl/local_gateway 调出图给你看（不依赖 UI）
   - 长期方案：可在 DashBox 加个"测试台"小页面/弹窗直接调网关
2. **预设首帧名固定** `nsfw_first_frame.png`——使用方需要先在 NAS 放同名文件（已分发自带的占位图）
3. **Anthropic 设计准则冲突未解决**：DashBox 后端用 Cognee 1.0 启动时强启 session memory/access control，会话级数据隔离与"AI 短剧剧本"场景错位（用户每个项目应是独立项目域）
4. **跨设备 NAS 路径漂移**：本机 `/private/tmp/nas_mnt` / PC01 `Z:\Windows\...` / workstation `/home/merlin/nas_mount` 三种挂载方式，预设里写真实文件名（不做路径转换），下载完成后分发到三端才能跑

## 三、演示流程（"我调出图 + 你看 + 我把生成按钮暴露给你点"）

> 协作原则：所有"点生成/出图/保存"按钮由你确认后触发，agent 只展示提示词、UI 状态、参数建议

1. **R18 门禁** ← 这步在浏览器已测过，复核截图
2. **模型库浏览** ← 已测，复核 384/81 数字
3. **预设选择** ← 选一个视频预设粘贴进工作流编辑器（UI 操作）
4. **填首帧** ← 提示词、LoRA 链、首帧文件名由我展示，你点 "保存" / "提交"
5. **生图** ← local_gateway 调出图（不通过 UI，避免弹窗）— 出图后展示（你只看，不点）
6. **视频** ← 工作流编辑器点 "体检" → 绿勾全过 → 你点 "Run" / "提交到 ComfyUI" → 我轮询产物

需要我现在按这个流程启动浏览器演示（每步先给你看我准备的操作 + 提示词文本），还是你想先调整演示方案？
