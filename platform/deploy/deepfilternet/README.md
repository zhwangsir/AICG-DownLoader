# DeepFilterNet3 服务部署 (Mac studio01)

## 概览

- **服务地址**: `http://192.168.71.109:8301/v1`
- **部署位置**: `~/deploys/deepfilternet/`
- **依赖**: Python 3.9+ (macOS 系统自带) + `deep-filter` 预编译二进制 (arm64)
- **守护**: launchd (`com.aicg.deepfilternet`), 开机自启 + KeepAlive
- **工作目录**: `/tmp/deepfilternet-io` (自动清理 1 小时前的临时文件)

## 文件清单

```
~/deploys/deepfilternet/
├── deep-filter                 # 0.5.6 arm64 预编译二进制
├── serve_api.py                # Python HTTP 包装器 (仅标准库)
├── run.sh                      # 手动启动脚本 (后台运行)
├── com.aicg.deepfilternet.plist # launchd 守护配置
├── serve.log                   # 手动启动日志
├── launchd.out.log             # launchd stdout
└── launchd.err.log             # launchd stderr
```

## API

### `GET /v1/health`
返回服务状态 + 二进制位置。

### `GET /v1/models`
OpenAI 兼容模型列表, 返回 `deepfilternet3`。

### `POST /v1/denoise`
上传音频文件 (raw bytes 或 `multipart/form-data`), 返回降噪后的 WAV。
- 自动识别 `wav/mp3/ogg/flac` 格式
- 输出文件名: `denoised_<input_stem>.wav`

## 部署步骤

```bash
# 1. (已完成) 下载二进制
cd ~/deploys/deepfilternet
curl -L -o deep-filter \
  https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-aarch64-apple-darwin
chmod +x deep-filter

# 2. (已完成) 上传 serve_api.py + run.sh + plist

# 3. 安装 launchd 守护
cp com.aicg.deepfilternet.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aicg.deepfilternet.plist

# 4. 验证
curl -s http://localhost:8301/v1/health
curl -s http://localhost:8301/v1/models
```

## 手动管理

```bash
# 手动启动 (替代 launchd)
cd ~/deploys/deepfilternet && ./run.sh

# 停止
launchctl unload ~/Library/LaunchAgents/com.aicg.deepfilternet.plist
# 或手动启动时:
kill $(cat ~/deploys/deepfilternet/serve.pid)

# 查看日志
tail -f ~/deploys/deepfilternet/launchd.out.log
tail -f ~/deploys/deepfilternet/launchd.err.log
```

## 调用示例 (后端)

后端 `app/services/postprocess_service.py` 中的 `denoise_audio` 方法会调用:
- 端点: `settings.deepfilternet_endpoint` = `http://192.168.71.109:8301/v1`
- 模型: `settings.deepfilternet_model` = `deepfilternet3`
- 超时: `settings.deepfilternet_timeout` = 60.0s

## 性能 (M3 Ultra)

- 测试样本: 1s 静音 wav
- RTF (Real-Time Factor): 0.000032 — 处理 1 秒音频只需 32 微秒
- 模型: DeepFilterNet3 (onnx, 自动加载, 嵌入式权重)
