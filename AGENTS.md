# AGENTS.md — 集群操作记忆与决策记录

> **目的**：避免 AI 助手反复犯同样的错误，每次会话必须先读本文件
> **维护者**：设备管家（AI Assistant）
> **最后更新**：2026-07-28 14:00
> **读取规则**：每次会话开始时必须完整阅读本文件，尤其注意「⚠️ 易错点」和「🔒 硬性规则」

---

## 一、集群设备清单（17台）

| # | 设备 | 角色 | LAN IP | Tailscale IP | 类型 | SSH 用户 |
|---|------|------|--------|-------------|------|---------|
| 1 | studio01-04 | EXO RDMA 推理 | .109/.111/.112/.113 | 100.64.201.37 / 100.66.68.72 / 100.95.164.2 / 100.97.146.45 | Mac Studio M2 Pro | dgmt-studio01-04 |
| 2 | openclaw01-04 | OpenClaw 网关 | .86/.75/.81/.85 | 100.69.0.4 / 100.95.216.83 / 100.82.122.93 / 100.91.128.30 | Mac mini M2 | dgmt-openclaw01-04 |
| 3 | spark01-02 | vLLM Ray (Euryale 70B) | .82/.84 | 100.81.235.124 / 100.86.42.89 | Linux GB10 | dgmt-spark |
| 4 | workstation | 算力+真机服务 | 192.168.71.127 | 100.68.100.90 | Linux 4×RTX PRO 6000 | merlin |
| 5 | pc01 | ComfyUI worker | 192.168.71.115 | 100.69.134.27 | Windows RTX 5090 | home |
| 6 | pc02 | ComfyUI worker | 192.168.71.114 | 100.107.94.26 | Windows RTX 5090 | w |
| 7 | NAS | SMB 存储 44T | 192.168.71.7 | 100.80.237.96 | Linux | dgmt-nas |
| 8 | cloud | 网关/1Panel/frps | 43.119.32.180 | 100.83.78.114 | Linux | root |
| 9 | core | 服务器(待业务) | 192.168.71.47 | 100.77.80.100 | Ubuntu | merlin |
| 10 | MateBook | 操作终端 | — | 100.74.15.34 | macOS | 本机 |

---

## 二、关键凭据

> ⚠️ 这些凭据已多次询问用户，**禁止再次询问**

| 服务 | 用户名 | 密码 | 备注 |
|------|--------|------|------|
| NAS SMB | dgmt-nas | Aki.19950108 | 192.168.71.7，共享名 NAS |
| Tailscale Auth Key | — | tskey-auth-kPM5hHvNGY11CNTRL-UTn8rtRjK8Pfw3riNoGB8Pru71VhdRR9C | 已用于 core 设备授权 |

### NAS 挂载方式

**Linux (Workstation)**：已配置 fstab 自动挂载到 `/home/merlin/nas_mount`
```bash
# 凭据文件：/root/.smbcredentials（如不存在，内容为）
# username=dgmt-nas
# password=Aki.19950108
```

**Windows (PC01/PC02)**：用 `net use` + `cmdkey` + 计划任务自动挂载
```cmd
cmdkey /add:192.168.71.7 /user:dgmt-nas /pass:Aki.19950108
net use Z: \\192.168.71.7\NAS /persistent:yes
```

---

## 三、Workstation GPU 分配（🔒 硬性规则，不可随意更改）

> ⚠️ **2026-07-28 错误教训**：我曾把 IndexTTS 放到 GPU3，但 GPU3 是预留给 Nemotron vLLM 的。TTS 应在 GPU0。**每次启动服务前必须核对此表**。

| GPU | 服务 | 端口 | 显存占用 | systemd 服务 | 备注 |
|-----|------|------|---------|-------------|------|
| GPU0 | ComfyUI #1 | :8189 | ~0.5GB | 手动 nohup | 与 TTS 共卡 |
| GPU0 | IndexTTS2 | :9200 | ~7.6GB | 手动 nohup | `CUDA_VISIBLE_DEVICES=0` |
| GPU1 | ComfyUI #2 | :8190 | ~0.5GB | 手动 nohup | 与 Embedding 共卡 |
| GPU1 | Qwen3-Embedding-4B | :9302 | ~8.4GB | **qwen3-embedding.service** | `CUDA_VISIBLE_DEVICES=1` |
| GPU2 | ComfyUI #3 | :8191 | ~0.5GB | 手动 nohup | 与 ASR 共卡 |
| GPU2 | AI-Omni ASR (faster-whisper large-v3) | :9210 | ~4.9GB | 手动 screen | `device_index=2` |
| GPU3 | **Nemotron vLLM** | :8000 | ~86GB | **nemotron-vllm.service** | `CUDA_VISIBLE_DEVICES=3`，served-name=qwen3.6-uncensored |

### ComfyUI-LB 后端配置
- 本地 3 后端：:8189(GPU0) / :8190(GPU1) / :8191(GPU2)
- 远程 2 后端：pc01 :8188 / pc02 :8193
- **GPU3 不跑 ComfyUI**（留给 Nemotron vLLM）

### 关键服务路径（Workstation）

| 服务 | 路径 | venv | 启动命令 |
|------|------|------|---------|
| ComfyUI | /opt/ComfyUI | /opt/ComfyUI/venv (Python 3.12, torch 2.13.0+cu130) | `CUDA_VISIBLE_DEVICES=N venv/bin/python main.py --listen 0.0.0.0 --port 818X` |
| ComfyUI-LB | /opt/ComfyUI/comfyui-lb.py | 同上 | `venv/bin/python comfyui-lb.py` |
| IndexTTS2 | /home/merlin/index-tts | /home/merlin/index-tts/.venv (Python 3.11, torch 2.8.0+cu128) | `CUDA_VISIBLE_DEVICES=0 .venv/bin/python toiv_tts_server.py --host 0.0.0.0 --port 9200` |
| Nemotron vLLM | /home/merlin/models/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-BF16 | /opt/nemotron-venv | `sudo systemctl start nemotron-vllm` |
| Qwen3-Embedding-4B | /home/merlin/models/Qwen3-Embedding-4B | /opt/nemotron-venv | `sudo systemctl start qwen3-embedding` |
| AI-Omni ASR | /opt/ai-omni-asr | /opt/ai-omni-asr (Python 3.12, faster-whisper 1.2.1) | `screen -S ai-omni-asr -L -Logfile /opt/ai-omni-asr/logs/screen.log bash -c 'cd /opt/ai-omni-asr && source bin/activate && python asr_server.py'` |
| Drt ERP | /home/merlin/drt | — | **待项目负责人迁移到 core** |
| ToIV | /home/merlin/toiv | — | **待项目负责人迁移到 core** |

---

## 四、NAS 模型路径

| 路径 | 内容 | 大小 |
|------|------|------|
| `NAS/Windows/ComfyUI/ComfyUIModel/models` | 主模型库 | 524GB |
| `NAS/toiv/comfyui-models` | ToIV 专用模型 | ~180GB |

### ComfyUI extra_model_paths.yaml 配置

**Workstation**：未配置（使用本地 /opt/ComfyUI/models）

**PC01/PC02**（已配置，注意不要包含 `custom_nodes`，会导致启动报错）：
```yaml
# C:\ComfyUI\extra_model_paths.yaml
nas:
    base_path: "Z:/Windows/ComfyUI/ComfyUIModel"
    checkpoints: models/checkpoints
    clip: models/clip
    clip_vision: models/clip_vision
    configs: models/configs
    controlnet: models/controlnet
    embeddings: models/embeddings
    loras: models/loras
    upscale_models: models/upscale_models
    vae: models/vae
    text_encoders: models/text_encoders
    diffusion_models: models/diffusion_models
    unet: models/unet
```

---

## 五、Core 设备状态

> **角色变更**：core 已从 Docker 监控栈改为真机服务器，待项目负责人推送 ToIV/DRT 业务

| 项目 | 状态 |
|------|------|
| PostgreSQL 18 | ✅ 真机运行 |
| Redis | ✅ 真机运行 (127.0.0.1:6379) |
| Docker | ❌ 已禁用+全清（12个监控容器已删） |
| ToIV/DRT 代码 | 待项目负责人推送 |
| 备份文件 | 在 Workstation /tmp（drt_pg_dump.sql / drt_redis_dump.rdb / drt_env_backup） |

---

## 六、⚠️ 易错点记录（避免重复犯错）

### 1. GPU 分配搞错（2026-07-28）
- **错误**：把 IndexTTS 放到 GPU3
- **正确**：GPU3 是 Nemotron vLLM 专用，TTS 在 GPU0
- **教训**：启动服务前必须核对第三节 GPU 分配表

### 2. /tmp tmpfs 吃内存（2026-07-28）
- **错误**：往 /tmp 写大文件（toiv_code.tar.gz 8G）导致内存被吃
- **正确**：/tmp 是 tmpfs（内存盘），大文件应写到磁盘
- **教训**：打包备份文件写到 /var/tmp 或指定磁盘目录

### 3. Windows SSH session 隔离（2026-07-28）
- **错误**：在 SSH session 中 net use 映射 Z: 盘，用户桌面看不到
- **正确**：用 cmdkey 保存凭据 + schtasks 在登录时运行 net use，或创建 .bat 启动脚本
- **教训**：Windows 的 SMB 映射是 per-session 的

### 4. ComfyUI-LB 后端数量搞错（2026-07-27）
- **错误**：曾配置 6 个后端
- **正确**：5 个后端（3 本地 GPU0/1/2 + pc01 + pc02），GPU3 不跑 ComfyUI

### 5. Windows SSH session 中启动 ComfyUI 子进程被终止（2026-07-28）
- **错误**：通过 ssh 用 `Start-Process` 或 `wscript` 启动 ComfyUI，ssh 断开后子进程被杀
- **正确**：Windows 计划任务直接执行 `start_comfyui.bat`，且 `LogonType` 用 `InteractiveToken` 在用户登录后触发
- **教训**：不要依赖 ssh 启动长期运行的 GUI/服务进程；计划任务 + bat 是最稳定的开机自启方案

### 6. 重复询问 NAS 密码（多次）
- **错误**：多次询问 NAS SMB 密码
- **正确**：密码已记录在第二节，禁止再问

---

## 七、操作历史

### 2026-07-28 会话

| 时间 | 操作 | 结果 |
|------|------|------|
| 05:50 | 停止 Workstation 所有服务（Docker 4容器 + Caddy + socat代理 + dcgm-exporter + cups） | ✅ load 3.15→0.92 |
| 05:55 | 清理 /tmp 残留文件（toiv_code.tar.gz 8G 等） | ✅ 释放 8G 内存 |
| 06:00 | 全设备状态检查（15台SSH验证） | ✅ 14在线，1离线(cloud SSH超时) |
| 06:04 | Core Docker 清理（12个监控容器全删，docker禁用） | ✅ |
| 06:10 | 启动 ComfyUI 3卡 + LB | ✅ :8188-8191 全部 200 |
| 06:15 | 启动 IndexTTS（误放 GPU3） | ❌ 后修正 |
| 09:00 | 修正 GPU 分配：TTS 迁至 GPU0，启动 Nemotron vLLM on GPU3 | ✅ :8000/:9200 正常 |
| 09:10 | PC02 磁盘分析：真凶是 Steam(40G)+炉石(11G)，非模型 | ✅ |
| 09:15 | PC02 NAS 挂载（cmdkey + schtasks MountNAS） | ✅ 计划任务已注册 |
| 09:20 | 创建 AGENTS.md | ✅ 本文件 |
| 10:00 | 排查 PC01 ComfyUI 无法启动 | ✅ 原因：计划任务执行 wscript 且 ssh 触发子进程被杀 |
| 10:05 | 修正 PC01 计划任务为直接执行 start_comfyui.bat | ✅ PC01 :8188 启动并加载 NAS 模型 |
| 10:10 | 验证 PC02 :8193 加载 NAS 模型 | ✅ 805 节点，checkpoints 列表来自 NAS |
| 11:00 | 优化 Nemotron vLLM 启动参数 | ✅ 去掉 --enforce-eager，启用 chunked-prefill/prefix-caching，显存 88%→94.5%，:8000 推理正常 |
| 13:30 | 恢复 Qwen3-Embedding-4B 真机服务 | ✅ 安装 sentence-transformers，systemd 托管 :9302，输出维度 2560，OpenAI /v1/embeddings 兼容 |
| 14:00 | 更新设备清单并分发到所有项目 | ✅ 已同步到 22 个项目目录（含 ToIV 新增） |
| 14:05 | 清理 .archive/old-docs 过期文档 | ✅ 删除 8 个旧版设备说明/迁移计划/调研报告 |
| 14:10 | 补齐 AGENTS.md 设备清单 Tailscale IP | ✅ 17 台设备 IP 全部具体化 |

---

## 八、待办事项

- [x] PC01 ComfyUI 配置 extra_model_paths.yaml 指向 NAS（✅ 端口8188）
- [x] PC01 start_with_nas.bat 启动脚本（✅ 端口8188）
- [x] PC01 计划任务 MountNAS（✅ 用户 DESKTOP-04VJ6QG\home）
- [x] PC01 凭据保存到 Windows 凭据管理器（✅ 已保存）
- [x] PC02 ComfyUI 配置 extra_model_paths.yaml 指向 NAS（✅ 端口8193）
- [x] PC02 start_with_nas.bat 启动脚本（✅ 端口8193）
- [x] PC02 计划任务 MountNAS（✅ 用户 DESKTOP-T9JILFS\w）
- [x] PC02 凭据保存到 Windows 凭据管理器（✅ 已保存）
- [x] PC01/PC02 重启 ComfyUI 使 extra_model_paths.yaml 生效（✅ PC01 :8188 / PC02 :8193 均加载 NAS 模型）
- [ ] Cloud SSH banner 超时排查（HTTPS 正常）
- [x] Workstation SGLang/infinity 真机未安装（✅ 已用 Qwen3-Embedding-4B 真机 sentence-transformers 服务替代，:9302 恢复）
- [ ] 项目负责人推送 ToIV/DRT 到 core
- [ ] Cloud 反代切换指向 core（待 core 业务就绪后）
