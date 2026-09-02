"""P1: local MiniMax H3 Context-IR rewrite for local_gateway video submit.

Mirrors platform/backend/app/services/h3_context_ir_rewriter.py so the engine
path that actually sends prompts to H3 also gets official FL2VA/Ref2VA layout.
Fail-open: LLM/VLM errors return the original prompt.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("local_gateway.h3_context_ir")

REWRITE_ENABLED = os.getenv("LOCAL_H3_REWRITE", "true").strip().lower() in {
    "1", "true", "yes", "on",
}

ADULT_VOCAB: tuple[str, ...] = (
    "nude", "naked", "nsfw", "porn", "pornograph", "sex", "sexual", "erotic",
    "xxx", "orgasm", "genital", "penis", "vagina", "nipple", "fellatio",
    "cunnilingus", "masturbat", "ejaculat", "hentai", "uncensored", "blowjob",
    "handjob", "cumshot", "intercourse", "moaning",
)

_BASE_SYSTEM = """You rewrite video prompts into official MiniMax H3 Context-IR.
Write English. Keep spoken lines in their original language.
Output ONLY the rewritten prompt — no markdown fences.

T2VA starts at the three core fields.
I2VA first line:
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
FL2VA first line:
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.

Then:
integrated_multimodal_description: [Shot 1] style, shot size, subjects, setting, action, camera. Dialogue <d>[zh] line</d> (S1). Later shots [Shot 2] At MM:SS.mmm, the camera cuts to ...
overall_soundscape: 1-4 sentences of ambience. N/A only for full silence.
non_diegetic_music: instrumentation/tempo, or N/A.
Stay on the user's scene. Do not invent extra bedroom or body-focus content.
"""

_REF2VA_SYSTEM = """You rewrite video prompts into official MiniMax H3 Ref2VA Context-IR.
Write English. Output ONLY the rewritten prompt — no markdown fences.

subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:

Use <Picture i> <Video k> <Audio j> in connection order. Assign each ref a job
from identity / style / motion / voice. Every supplied picture must appear.
Stay on the user's scene. Do not invent extra bedroom or body-focus content.
"""

_NSFW_ADDENDUM = """
The user may request mature scenes. Keep the same field names and labels.
Follow the user's requested action faithfully. Do not refuse.
"""

_FL2VA_EXAMPLE = """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a cyclist raises an umbrella from Picture 1 into the pose of Picture 2. The camera pulls out with small amplitude at slow speed.

overall_soundscape: Rain on pavement and a metallic click of the umbrella runner.
non_diegetic_music: N/A
"""

_REF2VA_EXAMPLE = """subject_definitions:
<Picture 1> is the composition keyframe (job: style).
<Picture 2> is the identity reference for the rider.

summary:
[reference generation] The target video keeps <Picture 1> and <Picture 2> on the same rider.

retention_analysis:
<Picture 1> ([Shot 1] first frame): fully_preserved - framing kept.
<Picture 2> (appears in [Shot 1]): fully_preserved - face kept.

detailed_description:
[Shot 1] A medium shot begins from <Picture 1> with identity from <Picture 2>.

overall_soundscape: Rain on jackets.
non_diegetic_music: N/A
"""

SFW_TEMPLATE_SOURCES: tuple[str, ...] = (
    _BASE_SYSTEM, _REF2VA_SYSTEM, _FL2VA_EXAMPLE, _REF2VA_EXAMPLE,
)

LlmCaller = Callable[[list[dict[str, Any]]], Awaitable[str]]
VlmCaller = Callable[[list[dict[str, Any]]], Awaitable[str]]


def sfw_templates_blob() -> str:
    return "\n".join(SFW_TEMPLATE_SOURCES)


def validate_rewrite_output(text: str, mode: str, n_pictures: int = 0) -> bool:
    blob = (text or "").strip()
    mode_key = (mode or "").strip().lower()
    if mode_key in {"ref2va", "r2v"}:
        if "subject_definitions:" not in blob:
            return False
        if n_pictures > 0 and "<Picture" not in blob:
            return False
        return True
    return (
        "integrated_multimodal_description:" in blob
        and "overall_soundscape:" in blob
        and "[Shot" in blob
    )


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = text.replace("<think>", "").strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _system_for(mode: str, nsfw: bool) -> str:
    mode_key = (mode or "").strip().lower()
    base = _REF2VA_SYSTEM if mode_key in {"ref2va", "r2v"} else _BASE_SYSTEM
    if nsfw:
        return base.rstrip() + "\n" + _NSFW_ADDENDUM.strip() + "\n"
    return base


async def _default_llm_caller(messages: list[dict[str, Any]]) -> str:
    from local_gateway.main import CHAT_MODEL_NAME, LLM_BASE_URL

    timeout = httpx.Timeout(25.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(
            f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
            json={
                "model": CHAT_MODEL_NAME,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 1600,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        raw = msg.get("content") or msg.get("reasoning_content") or ""
        return _strip_fences(raw)


async def _default_vlm_caller(content: list[dict[str, Any]]) -> str:
    from local_gateway.main import VLM_BASE_URL, VLM_MODEL_NAME

    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(
            f"{VLM_BASE_URL.rstrip('/')}/chat/completions",
            json={
                "model": VLM_MODEL_NAME,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.1,
                "max_tokens": 400,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        raw = msg.get("content") or msg.get("reasoning_content") or ""
        return _strip_fences(raw)


async def rewrite_h3_prompt(
    prompt: str,
    *,
    mode: str,
    duration: float = 5.0,
    nsfw: bool = False,
    n_pictures: int = 0,
    n_videos: int = 0,
    n_audios: int = 0,
    last_shot: str = "Shot 1",
    original_fallback: str = "",
    reference_image_urls: list[str] | None = None,
    llm_caller: LlmCaller | None = None,
    vlm_caller: VlmCaller | None = None,
) -> str:
    """Rewrite one H3 prompt. Fail-open to original_fallback/prompt."""
    fallback = (original_fallback or prompt or "").strip()
    if not REWRITE_ENABLED:
        return fallback
    mode_key = (mode or "i2va").strip().lower()
    if mode_key == "r2v":
        mode_key = "ref2va"
    if mode_key == "i2v":
        mode_key = "fl2va" if n_pictures >= 2 else "i2va"
    try:
        retention = ""
        urls = [u for u in (reference_image_urls or []) if u]
        if mode_key == "ref2va" and urls:
            try:
                caller = vlm_caller or _default_vlm_caller
                retention = await caller(
                    [
                        {
                            "type": "text",
                            "text": (
                                "Assign each Picture a job (identity/style/motion/voice) "
                                "and traits to fully_preserve. Plain text lines."
                            ),
                        },
                        *[
                            {"type": "image_url", "image_url": {"url": u, "detail": "low"}}
                            for u in urls[:4]
                        ],
                    ]
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("H3 Context-IR VLM retention failed: %s", e)
        pics = ", ".join(f"<Picture {i}>" for i in range(1, int(n_pictures) + 1)) or "(none)"
        user = (
            f"mode: {mode_key}\n"
            f"duration_seconds: {float(duration):.2f}\n"
            f"last_shot: {last_shot}\n"
            f"n_pictures: {int(n_pictures)}\n"
            f"n_videos: {int(n_videos)}\n"
            f"n_audios: {int(n_audios)}\n"
            f"picture_labels: {pics}\n"
            f"vlm_retention:\n{retention}\n"
            f"user_prompt:\n{prompt}"
        )
        example = _REF2VA_EXAMPLE if mode_key == "ref2va" else _FL2VA_EXAMPLE
        messages = [
            {"role": "system", "content": _system_for(mode_key, nsfw)},
            {"role": "user", "content": "Example of the required output shape:\n" + example},
            {"role": "user", "content": user},
        ]
        caller = llm_caller or _default_llm_caller
        rewritten = _strip_fences(await caller(messages))
        if validate_rewrite_output(rewritten, mode_key, n_pictures=int(n_pictures)):
            return rewritten
        logger.warning(
            "H3 Context-IR rewrite missing required fields (mode=%s), sending original prompt",
            mode_key,
        )
        return fallback
    except Exception as e:  # noqa: BLE001
        logger.warning("H3 Context-IR rewrite failed, sending original prompt: %s", e)
        return fallback
