"""Pin DashBox CE settings.db to the AIGCPannel local/cluster stack.

Writes custom gateway + provider channels + media models + embedding +
local_http media relay. Does not call NewAPI :3000.

Inside dashbox-api (Docker): custom base URL is host.docker.internal:8790
so the container can reach the host local_gateway adapter.
On the host: 127.0.0.1:8790.

Usage:
  docker cp scripts/localize_gateway.py dashbox-api-1:/tmp/localize_gateway.py
  docker exec dashbox-api-1 python /tmp/localize_gateway.py
"""

from __future__ import annotations

import os
from pathlib import Path

from novelvideo.model_gateway_settings import (
    get_model_gateway_settings,
    save_custom_newapi_gateway,
    save_media_relay_config,
    save_newapi_embedding_model_config,
    save_newapi_media_model_mappings,
    save_newapi_provider_channels,
)

CLUSTER_LLM = "http://192.168.71.82:8000/v1"
CLUSTER_VLM = "http://192.168.71.82:8000/v1"
CLUSTER_COMFY = "http://192.168.71.127:8188"

SDXL_CHECKPOINT = "majicMIX realistic 麦橘写实_v7.safetensors"

SDXL_WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SDXL_CHECKPOINT}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 25,
            "cfg": 7.0,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "denoise": 1.0,
            "model": ["1", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": ["4", 0],
        },
    },
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "dc_image"}},
}


def _in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("NOVELVIDEO_DATA_ROOT") == "/data"


def adapter_base_url() -> str:
    override = str(os.environ.get("LOCAL_GATEWAY_BASE_URL") or "").strip()
    if override:
        return override
    if _in_docker():
        return "http://host.docker.internal:8790/v1"
    return "http://127.0.0.1:8790/v1"


def main() -> None:
    save_custom_newapi_gateway(
        base_url=adapter_base_url(),
        api_key="local-dashbox",
        admin_base_url="",
        token_name="local-cluster",
        token_id="",
        activate=True,
    )

    save_newapi_provider_channels(
        [
            {
                "provider": "openai",
                "type": 1,
                "upstreamKey": "not-needed",
                "baseUrl": CLUSTER_LLM,
                "priority": 0,
                "settings": {},
            },
            {
                "provider": "custom",
                "type": 8,
                "upstreamKey": "not-needed",
                "baseUrl": CLUSTER_VLM,
                "priority": 1,
                "settings": {},
            },
            {
                "provider": "comfyui",
                "type": 63,
                "upstreamKey": "",
                "baseUrl": CLUSTER_COMFY,
                "priority": 0,
                "settings": {
                    "comfyui": {
                        "model_name": "local-sdxl",
                        "workflow_by_model": {"local-sdxl": SDXL_WORKFLOW},
                    }
                },
            },
        ]
    )

    save_newapi_media_model_mappings(
        {
            "local-sdxl": {
                "provider": "comfyui",
                "upstreamModel": "local-sdxl",
                "mediaType": "image",
                "label": "Local SDXL",
                "enabled": True,
                "sortOrder": 10,
                "config": {
                    "request": {"endpoint": "images/generations", "parameters": []},
                },
            },
            "LingShan-G2": {
                "provider": "comfyui",
                "upstreamModel": "local-sdxl",
                "mediaType": "image",
                "label": "Local SDXL",
                "enabled": True,
                "sortOrder": 11,
                "config": {
                    "request": {"endpoint": "images/generations", "parameters": []},
                },
            },
            "MiniMax-H3": {
                "provider": "openai",
                "upstreamModel": "MiniMax-H3",
                "mediaType": "video",
                "label": "MiniMax H3",
                "enabled": True,
                "sortOrder": 10,
                "config": {
                    "request": {"endpoint": "video/generations", "parameters": []},
                    "resolutionOptions": ["480p", "768p", "1080p"],
                    "ratioOptions": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                    "minDuration": 4,
                    "maxDuration": 15,
                    "supportedModes": ["text_to_video", "first_frame", "first_last_frame"],
                    "referenceImageMax": 2,
                },
            },
            "LTX-2.5": {
                "provider": "openai",
                "upstreamModel": "LTX-2.5",
                "mediaType": "video",
                "label": "LTX-2.5",
                "enabled": True,
                "sortOrder": 20,
                "config": {
                    "request": {"endpoint": "video/generations", "parameters": []},
                    "resolutionOptions": ["480p", "640p"],
                    "ratioOptions": ["16:9", "1:1", "9:16"],
                    "minDuration": 4,
                    "maxDuration": 15,
                    "supportedModes": ["text_to_video", "first_frame"],
                    "referenceImageMax": 1,
                },
            },
            "index-tts-2": {
                "provider": "openai",
                "upstreamModel": "index-tts-2",
                "mediaType": "audio",
                "label": "IndexTTS-2",
                "enabled": True,
                "sortOrder": 10,
                "config": {},
            },
        }
    )

    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="Qwen3-Embedding-4B",
        dimension=2560,
        batch_size=8,
    )

    save_media_relay_config(provider="local_http", ttl_seconds=1800)

    settings = get_model_gateway_settings()
    print("model_gateway_mode =", settings.get("model_gateway_mode"))
    print("custom_newapi_base_url =", settings.get("custom_newapi_base_url"))
    print("media_relay_provider =", settings.get("media_relay_provider"))
    print("adapter_base_url =", adapter_base_url())
    print("in_docker =", _in_docker())


if __name__ == "__main__":
    main()
