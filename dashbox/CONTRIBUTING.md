# 贡献指南

DashBox 是基于 DramaClaw CE（Elastic License 2.0）二次开发的本地化 fork，作为本地最终形态维护，**不接受上游 PR**，也没有上游同步通道。

## 本地开发

```bash
uv sync
cp .env.example .env      # 按文档配置模型网关
uv run novelvideo api --port 8780
```

## 改动约定

- 改动后自测：`uv run pytest tests/ -q` 必须全绿；
- 提交信息清晰，建议遵循 [Conventional Commits](https://www.conventionalcommits.org/)；
- 安全问题请本地私下处理，见 [SECURITY.md](SECURITY.md)，请勿公开披露。

## 许可

本仓库保留 DramaClaw CE 的 ELv2 许可与 attribution（见 LICENSES/、NOTICE）。在此之上的本地改动遵循同一许可边界；涉及许可的问题请先阅读 [LICENSE](LICENSE) 与 NOTICE 再行动。
