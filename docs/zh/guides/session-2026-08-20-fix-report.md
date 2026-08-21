# R18 工厂字幕时间轴与画布交互修复报告（2026-08-20/21）

本报告覆盖近期 R18 制作工厂的三轮修复：P0 字幕时间轴漂移、P1 画布交互三项（浏览器实测发现）、P2 任务中心 hydrate 500 定位。全部修复已提交 Git 并通过全量回归。

## 一、问题背景

### P0：字幕时间轴漂移（合成链路核心缺陷）

前端曾按分镜表**计划时长**（`row.durationSec`）自行拼装 SRT 烧录进成片，但合成端（`r18-compose`）的真实时间轴由三部分构成：

| 因素 | 影响 |
|------|------|
| 镜头视频真实时长（ffprobe 实测） | H3/Wan 出片时长远非计划的整数秒（如 5.37s） |
| 片头卡偏移 | 首镜字幕应从 `opening.duration_sec` 起算 |
| xfade 转场重叠 | 每个切点吃掉 `transition_sec`（默认 0.5s） |

两者必然随镜头数**渐进漂移**；且第 8 工序 QC 的字幕 ASR 回读比对也使用同一份漂移 SRT，造成误判。

### P1：画布交互三项（浏览器真机实测发现）

1. **插入即所见失效**：一键插入 8 工序链后视口停在原处，8 节点仅 4 个可见；
2. **连续插入叠死**：第二次起插入的链与既有链逐位完全重叠；
3. **Esc 无法关闭节点菜单**：只能点空白处关闭。

### P2：任务中心 hydrate 500

浏览器实测中 `GET /api/v1/projects/{id}/tasks` 报 `HTTPError 500`，后端日志却零错误，需要定位。

## 二、技术实现方案

### 2.1 P0：SRT 由后端按真实时间轴重建（commit `e4c20b6`）

核心思路：**字幕与 TTS 配音必须在同一时间轴循环内计算**。

- `r18-compose` 请求新增 `subtitles` 参数（逐镜头文本，与 `shots` 对齐）；
- 后端在与 TTS `adelay` 计算共用的循环里记录每个镜头真实起始时间（片头卡偏移 + 逐镜真实时长 − xfade 重叠），据此重建 SRT 烧录；
- 响应回传最终 `srt`，工厂⑧ QC 直接用这份与烧录字幕同源的 SRT 做 ASR 回读比对；
- 旧 `srt` 参数保持原样烧录（向后兼容）；删除前端计划时长版 SRT 构建函数（`factoryBuildSrt`/`buildStudioSrt`）。

调用方收敛为三类全部改传 `subtitles`：工厂⑦合成节点、工厂⑧质检节点、快进工厂（NSFWDramaStudio）。

### 2.2 P1：三项交互修复（commit `e4057ac`）

**①插入即所见（两层踩坑后定型）**

- 第一层：新增 `requestFitViewNodes` 一次性请求通道，spawn 完成后发出；
- 第二层（复测发现）：React Flow 内部 `getFitViewNodes` 按 `measured` 硬性过滤节点——低缩放档（<0.35）LOD shell 渲染 0×0、`measured` 永不写入，目标节点被整体剔除、视口纹丝不动；
- 最终方案：手动计算 bbox（逻辑尺寸回退）+ `getViewportForBounds` + `setViewport`，绕开 RF 过滤。

**②连续插入叠死**

根因是 4000px「离屏残留阈值」判定错误：链宽 4240px，首次插入后包围盒 maxX 必然超阈值，此后每次插入都被判为残留回退同一位置。改为**全量包围盒右侧落位**（+80 间距）；「排到视口外找不到」的历史顾虑已由 ①的自动 fitView 解决。

**③Esc 关闭菜单**

`NodeSelectionMenu` 补 document 级 `keydown`（capture）监听，Esc 触发既有 `handleClose` 动画关闭。

### 2.3 P2：hydrate 500 定位（commit 见第四节）

通过控制变量实验排除后端嫌疑（匿名/带鉴权/90 并发/重启即轰炸/真实浏览器会话均 200，任务 DB 空、代码路径防御充分），最终确认：

- **500 来自 vite dev 代理**：`node-http-proxy` 无 error handler，后端重启窗口/不可达时对浏览器回「裸 500（空响应体）」，请求从未到达后端；
- 当时的触发条件：后端刚被 `kill -9` 重启 + 机器并发跑全量 pytest（api.log 中该时段只有 pytest 噪音，无服务端错误）；
- 修复：`vite.config.ts` 代理增加 error handler，不可达时回 **503 + `backend_unavailable` JSON**，语义上与「服务端代码 bug」区分；前端 hydrate/SSE 本就容错重试，无需改动。

## 三、测试验证结果

### 后端（pytest：2414 passed 全量）

`tests/test_r18_factory.py` 20 用例，关键新增：

- `test_subtitles_rebuilt_on_real_timeline`：3 镜 + 片头卡 + fade，断言 SRT 时间戳精确值与烧录文件一致；
- `test_subtitles_aligned_with_tts_on_real_timeline`：**非均匀真实时长** `[5.37, 4.83, 6.20, 3.50]s` + 30 字长对话 + 双 TTS，断言 SRT 块 `[2.000→6.870→11.200→16.900→19.900]` 与 TTS `adelay=7120/17150`（同起点 +250ms 淡入）完全同源，总时长 19.9s；
- `test_legacy_srt_param_still_burned`：旧参数向后兼容。

### 前端（vitest 2338 passed + tsc 0 全量）

`nsfw-factory-pipeline.test.ts` 新增 3 用例：fitView 请求发出、连续插入全量右移零重叠（坐标集合无重复）、远端残留场景可达性。

### 浏览器真机（三轮 + 终验）

- 第一轮（全链路 9 步）：菜单收敛 4 入口、8 工序顺序正确（③分镜表/④数字资产）、7 边完整、空态引导正确、模型库 386 模型 + R18 双向过滤——全过，并产出上述 P1 清单；
- 第二轮（复测）：暴露 fitView 两层踩坑中的第二层与 4000 阈值叠死根因；
- 终验：20% 低缩放插入 → 视口 266ms 平滑动画定位整链（两次插入可复现）；4 条链逐条右移零重叠；Esc 三轮验证通过。

### P2 复现脚本

`scripts/repro_task_center_500.sh`：诊断模式（不动服务）+ `--reproduce` 模式（停后端 → 复现 500/验证 503 → 恢复后端）。修复验证数据：后端不可达经代理 500（空体）→ 修复后 503 + JSON；后端恢复后 200。

## 四、Git 提交索引

| Commit | 内容 |
|--------|------|
| `e5682e2` | chore(fork): 移除上游发布/CI 基建（遗留暂存批次，96 文件） |
| `e4c20b6` | feat(r18): 8 工序流水线 + 字幕真实时间轴重建 P0（57 文件） |
| `e4057ac` | fix(canvas): P1 三项交互修复（4 文件，纯修复 diff） |
| （本次） | fix(dev): vite 代理 503 语义化 + P2 复现脚本 + 本报告 |

## 五、后续优化建议

### 5.1 历史节点命名迁移（✅ 已执行，commit `a82af1c`）

**矛盾机制**（2026-08-19 调序遗留）：工序顺序调整为「分镜表③、数字资产④」（原为资产③、分镜④，见 `nodeRegistry.ts:1055` 注释），但节点显示名走 `resolveNodeDisplayName`（`nodeDisplay.ts:65`）——优先取 `data.displayName`，它是**创建节点时**写入的默认名并随画布 JSON 持久化；工序徽标 `工序 X/8` 则由组件写死（新序）。结果：旧链节点显示旧名「工厂③数字资产」但徽标已是「工序 4/8」，与新链同名不同物。

**存量现状**（已查证，画布 `user_local_17cvc3s.json`，92 节点）：

| type + displayName | 数量 | 性质 |
|---|---|---|
| `nsfwFactoryAssetNode` + `工厂③数字资产` | 1 | 旧序遗留（待迁移） |
| `nsfwFactoryStoryboardNode` + `工厂④分镜表` | 1 | 旧序遗留（待迁移） |
| `nsfwFactoryStoryboardNode` + `工厂③分镜表` | 10 | 新序（正确） |
| `nsfwFactoryAssetNode` + `工厂④数字资产` | 10 | 新序（正确） |

**推荐方案：显示层规范化（方案 B）**，不动后端存量数据，随代码发布即生效、幂等。**执行结果**（2026-08-21，commit `a82af1c`）：实现细节与文档步骤一致，差异点——迁移实现为「重置回现行默认名」而非「清空」（`createDefaultData()` 本就预置新名，被 node.data 旧名覆盖，重置保持字段形状一致）；接入点选在 `canvasStore.normalizeNodes`（`hydrateCanvasDraft` 唯一入口，undo/redo history 快照同步归正）。浏览器实测：历史画布旧名 0 处、新名 11 处，autosave 已将迁移回写后端（revision 481），迁移固化、幂等成立。

1. **新增 normalize 函数**（`nodeDisplay.ts`）：按「type + displayName 精确匹配历史默认名」判定为默认名而非用户自定义——`nsfwFactoryAssetNode` 匹配 `工厂③数字资产`、`nsfwFactoryStoryboardNode` 匹配 `工厂④分镜表`（旧序名）时，直接**清空 displayName**，使其回退 `DEFAULT_NODE_DISPLAY_NAME`（新序名）。清空而非替换新串的理由：以后再调序也不会产生新的存量漂移；
2. **接入点**：`useCanvasSync` 画布 hydrate 时对 nodes 逐个 normalize（只改前端内存态，不触发持久化写入；用户下次编辑该节点才自然落盘新名）；
3. **单测**：覆盖 4 组 case——旧序名清空回退新序名、新序名不动、用户自定义名不动、非工厂类型含「工厂」字样不动；
4. **回归**：vitest + tsc 全量；浏览器打开历史画布确认旧链两节点显示「工厂④数字资产」「工厂③分镜表」；
5. **可选清理**（非必须）：确认无回滚需求后，可跑一次性脚本清理 `state/**/freezone/canvases/_history/` 中含旧名的 100+ 历史快照（`_history` 仅用于画布版本回溯，不影响显示）。

**备选方案 A（一次性后端数据迁移）**：直接改写画布 JSON 落盘文件中旧名 → 新名。缺点：需停写窗口或写后端迁移逻辑、`_history` 快照同样需要处理、未来调序还会复发。仅当规范化函数不足以覆盖（如用户已把旧名当自定义名手改过）时再考虑。

### 5.2 其余建议

2. **LOD 低缩放体验**（✅ 已执行）：缩放 <35% 节点为空壳「整屏空白」。修复四件套——① shell 新增工序徽标（`LOD_SHELL_STAGE_BADGES`，①立项…⑧质检，字号按节点宽 ~10% 内联取值，屏幕上恒为可读大小，无需订阅 zoom）；② `SHELL_FALLBACK_SIZES` 补齐 R18 全系 14 类型；③ 尺寸判定 `??` 改 `> 0`（RF 对未渲染节点给 0，`0 ?? 460 === 0` 曾使 shell 渲染 0×0 不可见）；④ busy 角标兼容工厂 `isRunning`/R18 批次 `isBatchRunning`；Canvas fitView bbox 同步加零尺寸防御。浏览器实测：12 条链 96 徽标齐全有序、20% 档平移 121FPS（性能设计未破坏）、50% 档完整组件恢复徽标消失；
3. **503 语义化后的前端提示**：task-center 目前把 503 与其它错误同样静默容错，可对 `backend_unavailable` 显示「后端重启中」轻提示；
4. **字幕长对话拆分**：超长对白（>30 字）当前整块显示到镜头结束，可按标点自动拆分为多条子块改善观感；
5. **compose 响应字段的 `audio_mode`**：请求体中该字段后端并不消费（实际按探测音轨 + tts_url 判定），可在 API 文档标注或移除，减少误解。
