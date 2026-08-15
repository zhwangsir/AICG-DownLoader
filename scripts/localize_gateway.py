"""本地化改造：一键写入 DashBox CE settings.db 配置。

把 CE 网关模式切换为 custom，指向本地网关适配层（local_gateway :8790），
并配置 Cognee embedding（本地 Qwen3-Embedding-4B，2560 维）与本机 HTTP
媒体 relay（替代阿里云 OSS relay）。

用法：cd DashBox && uv run python scripts/localize_gateway.py
"""

from __future__ import annotations

from novelvideo.model_gateway_settings import (
    _write_many,
    get_model_gateway_settings,
    save_custom_newapi_gateway,
    save_newapi_embedding_model_config,
)

LOCAL_GATEWAY_BASE_URL = "http://127.0.0.1:8790/v1"


def main() -> None:
    # 1. custom 模式：全部模型调用走本地适配层
    save_custom_newapi_gateway(
        base_url=LOCAL_GATEWAY_BASE_URL,
        api_key="local-dashbox",
        activate=True,
    )

    # 2. Cognee embedding：本地 Qwen3-Embedding-4B（OpenAI 兼容，2560 维）
    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="Qwen3-Embedding-4B",
        dimension=2560,
        batch_size=8,
    )

    # 3. 本机 HTTP 媒体 relay（参考图换 URL，替代阿里云 OSS relay）
    _write_many({"media_relay_provider": "local_http"})

    settings = get_model_gateway_settings()
    print("model_gateway_mode =", settings.get("model_gateway_mode"))
    print("custom_newapi_base_url =", settings.get("custom_newapi_base_url"))
    print("custom_newapi_embedding_model =", settings.get("custom_newapi_embedding_model"))
    print("media_relay_provider =", settings.get("media_relay_provider"))


if __name__ == "__main__":
    main()
