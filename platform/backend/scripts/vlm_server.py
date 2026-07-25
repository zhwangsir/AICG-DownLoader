r"""VLM Server — Qwen3-VL OpenAI 兼容 API 服务。

在 workstation GPU 上运行，为视觉质检提供多模态推理服务。

启动方式:
    set HF_ENDPOINT=https://hf-mirror.com
    F:\comfy\ComfyUI\ComfyUI\.venv\Scripts\python.exe vlm_server.py --model Qwen/Qwen3-VL-4B-Instruct --port 8200 --gpu 1
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import time
import uuid
from typing import Any

import torch
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from transformers import AutoProcessor, AutoModelForImageTextToText


app = FastAPI(title="VLM Server", version="1.0.0")

_model = None
_processor = None
_device = "cuda"
_gpu_id = 0


class ChatMessage(BaseModel):
    role: str = "user"
    content: str | list[dict[str, Any]] = ""


class ChatRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage] = []
    max_tokens: int = 1024
    temperature: float = 0.3
    stream: bool = False


def load_model(model_id: str, gpu_id: int):
    global _model, _processor, _device, _gpu_id
    _gpu_id = gpu_id
    _device = f"cuda:{gpu_id}"

    print(f"[VLM] 加载模型: {model_id} → {_device}")
    _processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    _model = AutoModelForImageTextToText.from_pretrained(
        model_id,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
        device_map={"": _device},
    )
    _model.eval()
    print(f"[VLM] 模型加载完成，GPU {_gpu_id} 显存: {torch.cuda.memory_allocated(_gpu_id)/1024**3:.1f} GB")


def decode_image(image_data: str) -> Any:
    if image_data.startswith("data:"):
        image_data = image_data.split(",", 1)[1]
    img_bytes = base64.b64decode(image_data)
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


def build_conversation(messages: list[ChatMessage]) -> tuple[list[dict[str, Any]], list[Any]]:
    conversation: list[dict[str, Any]] = []
    images: list[Any] = []
    for msg in messages:
        if isinstance(msg.content, str):
            conversation.append({"role": msg.role, "content": [{"type": "text", "text": msg.content}]})
        elif isinstance(msg.content, list):
            content_parts: list[dict[str, Any]] = []
            for part in msg.content:
                if part.get("type") == "text":
                    content_parts.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:") or not url.startswith("http"):
                        img = decode_image(url)
                        images.append(img)
                        content_parts.append({"type": "image"})
                    else:
                        import requests
                        resp = requests.get(url, timeout=30)
                        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                        images.append(img)
                        content_parts.append({"type": "image"})
            conversation.append({"role": msg.role, "content": content_parts})
    return conversation, images


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="模型未加载")

    start = time.time()
    conversation, images = build_conversation(request.messages)

    text = _processor.apply_chat_template(
        conversation,
        tokenize=False,
        add_generation_prompt=True,
    )

    inputs = _processor(
        text=[text],
        images=images if images else None,
        return_tensors="pt",
    ).to(_device)

    with torch.no_grad():
        output_ids = _model.generate(
            **inputs,
            max_new_tokens=request.max_tokens,
            temperature=request.temperature,
            do_sample=request.temperature > 0,
        )

    input_len = inputs["input_ids"].shape[1]
    generated = output_ids[0][input_len:]
    content = _processor.decode(generated, skip_special_tokens=True)

    elapsed = time.time() - start
    print(f"[VLM] 生成完成: {len(content)} 字符, {elapsed:.1f}s, GPU {_gpu_id}")

    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model or "qwen3-vl",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": input_len,
            "completion_tokens": len(generated),
            "total_tokens": input_len + len(generated),
        },
    })


@app.get("/v1/models")
async def list_models():
    return {"data": [{"id": "qwen3-vl", "object": "model"}]}


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": _model is not None}


def main():
    parser = argparse.ArgumentParser(description="VLM Server")
    parser.add_argument("--model", default="Qwen/Qwen3-VL-4B-Instruct", help="模型 ID 或本地路径")
    parser.add_argument("--port", type=int, default=8200, help="监听端口")
    parser.add_argument("--gpu", type=int, default=1, help="GPU ID")
    args = parser.parse_args()

    load_model(args.model, args.gpu)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
