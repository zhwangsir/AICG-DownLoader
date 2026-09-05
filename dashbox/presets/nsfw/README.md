# NSFW 工作流预设（8 个 Civitai 视频搭配逆向）

> 来源：8 个热门 NSFW 视频的底模+LoRA 搭配逆向（详见 `docs/zh/guides/nsfw-video-generation.md`）
> 状态：**全部真机冒烟出片**（2026-08-17：Wan 传教士 250s 出 mp4 / H3 241s 出音画 mp4，产物存 `NAS/toiv/preset_smoke_20260817/`）
> 前置：DashBox 设置 → 模型库 → 完成 R18 确认

## 预设清单

| 文件 | 路线 | 搭配（原作品） | 底模 | LoRA 链 |
|---|---|---|---|---|
| `wan22-nsfw-missionary.json` | Wan 2.2 I2V | 传教士（139345695/139346628） | wan2.2_i2v high/low fp8 | HIGH: NSFW-22(0.8)→m4crom4sti4(0.8)→POV-Cumshot(0.8)→lightx2v(1.0)；LOW: DR34ML4Y(0.9)→56Low(0.7)→lightx2v(1.0) |
| `wan22-nsfw-doggie-twerk.json` | Wan 2.2 I2V | 后入/twerk（139346895） | 同上 | HIGH: NSFW-22(0.8)→slop_twerk(0.8)→POV-Cumshot(0.7)→lightx2v；LOW 同上 |
| `wan22-nsfw-blowjob-closeup.json` | Wan 2.2 I2V | 口交特写（139593511） | 同上 | HIGH: chasing_blowjob(0.9)→CloseUpFacialCum(0.7)→deepthroat_v02(0.7)→lightx2v；LOW 同上 |
| `h3-nsfw-fl2va-aio.json` | MiniMax H3 | 全能动作+音画同出（139490627 等 H3 系） | minimax_h3_fl2va_pruned_int8_convrot | HMNSFW_AIO_V2(0.8)→VBVR(0.6)，基线 20 步 res_multistep |

## 用法 A：DashBox 工作流编辑器（推荐）

1. 设置 → 模型配置 → ComfyUI 渠道（或混合模式载入 H3 渠道）
2. 「添加工作流」→ 全选粘贴预设 JSON → 失焦自动校验
3. WorkflowRefsPanel 会列出全部权重引用（绿勾=在库）→ 点「体检」复核
4. 替换正向提示词（节点 `12`）与首帧图（节点 `20`/`10`）后提交

## 用法 B：curl 直提 ComfyUI

```bash
# Wan 路线 → LB :8188（三端负载均衡）
curl -X POST http://192.168.71.127:8188/prompt -H "Content-Type: application/json" \
  -d "{\"prompt\":$(cat wan22-nsfw-missionary.json)}"

# H3 路线 → 专用实例 :8195
curl -X POST http://192.168.71.127:8195/prompt -H "Content-Type: application/json" \
  -d "{\"prompt\":$(cat h3-nsfw-fl2va-aio.json)}"
```

轮询产物：`GET http://<host>:<port>/history/<prompt_id>`，mp4 落在各实例 `output/nsfw/`。

## 必改参数点

| 节点 | 字段 | 说明 |
|---|---|---|
| `12`（Wan）/ `20`（H3） | `text`/`prompt` | 正向提示词（触发词已在骨架内，换主题时保留对应触发词） |
| `20`（Wan）/ `10`（H3） | `image` | 首帧文件名，必须提前放入**执行后端的 input 目录**（见下） |
| `30`/`31`（Wan）| `noise_seed` | 两节点必须同种子 |
| `21`（Wan）| `width/height/length` | 832×480@81 帧≈5s；竖屏 480×832 |
| `20`（H3）| `width/height/length` | **宽高必须是 32 的倍数**（1280×704 已验证；720 会形状报错）；length 按 17k+5 网格（124≈5s、241≈10s） |

## input 目录速查（首帧图放置处）

| 后端 | input 目录 |
|---|---|
| workstation :8196（LB 本地） | `/opt/ComfyUI/instances/gpu0/input`（⚠️ 不是 /opt/ComfyUI/input） |
| pc01 / pc02 | `C:\ComfyUI\input` |
| H3 :8195 | `/home/merlin/ComfyUI-h3-eval/input` |

LB 随机路由——三端都要放同名首帧图。

## 已知坑（冒烟实录）

1. **H3 宽高非 32 倍数** → SamplerCustomAdvanced 报 `shape [1,24,1,1,22,2,40,2] invalid`（720 的教训）
2. **H3 Turbo 链（MiniMaxH3TurboLoRA+TurboSampler）** 在当前内容 LoRA 组合下同样形状报错——基线 20 步已验证，Turbo 改造待单独排错（先停用）
3. **workstation LB 后端 input 目录**被 systemd `--input-directory` 改到 `instances/gpu0/input`，文档/直觉路径不生效
