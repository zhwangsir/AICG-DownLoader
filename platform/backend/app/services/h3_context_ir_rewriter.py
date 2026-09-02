"""P1: local MiniMax H3 Context-IR rewrite before generate.

Spark LLM (qwen3.6-uncensored) rewrites the prompt H3 actually receives into the
official Context-IR layout. Optional spark VLM (qwen3.8-flash-next) adds retention
notes when reference images exist. Fail-open: any error logs a warning and
returns the original assembled prompt so generate is never blocked.

Official layouts (HF VIDEO_PROMPT_WRITING_GUIDE_base_en / ref_en):
- FL2VA/I2VA/T2VA: alignment line (if keyed) + integrated_multimodal_description
  + overall_soundscape + non_diegetic_music; timed [Shot N], camera, <d>[lang]>.
- Ref2VA: subject_definitions, summary, retention_analysis, detailed_description,
  overall_soundscape, non_diegetic_music; <Picture i> <Video k> <Audio j> in
  connection order; each ref assigned identity/style/motion/voice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
from openai import AsyncOpenAI

from app.agents.base import strip_think_tags
from app.config import settings
from app.services.model_gateway import model_gateway

logger = logging.getLogger(__name__)

# Explicit adult/pornography tokens. SFW templates must not contain these.
# NSFW uses the same field structure; uncensored model already — do not leak
# these tokens into SFW_TEMPLATE_SOURCES.
ADULT_VOCAB: tuple[str, ...] = (
    "nude",
    "naked",
    "nsfw",
    "porn",
    "pornograph",
    "sex",
    "sexual",
    "erotic",
    "xxx",
    "orgasm",
    "genital",
    "penis",
    "vagina",
    "nipple",
    "fellatio",
    "cunnilingus",
    "masturbat",
    "ejaculat",
    "hentai",
    "uncensored",
    "blowjob",
    "handjob",
    "cumshot",
    "intercourse",
    "moaning",
)

LlmCaller = Callable[[list[dict[str, Any]]], Awaitable[str]]
VlmCaller = Callable[[list[dict[str, Any]]], Awaitable[str]]

# ---------------------------------------------------------------------------
# Official SFW templates (no adult vocabulary)
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You rewrite video prompts into official MiniMax H3 Context-IR.
Write English. Keep spoken lines and on-screen text in their original language.
Output ONLY the rewritten prompt — no markdown fences, no commentary.

BASE MODES (t2va / i2va / fl2va) — exact field order:

T2VA starts at the three core fields (no alignment line).

I2VA first line:
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

FL2VA first line (S.SS is duration with two decimals; last shot label as given):
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.

Then a blank line, then:
integrated_multimodal_description: [Shot 1] <style>, <shot size>, subjects, setting, action, camera (type + amplitude + speed as natural English). Dialogue as <d>[zh] original line</d> with speaker (S1). Later shots: [Shot 2] At MM:SS.mmm, the camera cuts to ...
overall_soundscape: 1-4 English sentences of ambience and physical sound. N/A only for full silence.
non_diegetic_music: 1-3 sentences of instrumentation/tempo/dynamics, or N/A.

[Shot 1] has no timestamp. Cut times stay inside the requested duration.
Stay on the user's scene. Do not invent extra bedroom or body-focus content.
"""

_REF2VA_SYSTEM = """You rewrite video prompts into official MiniMax H3 Ref2VA Context-IR.
Write English. Keep spoken lines and on-screen text in their original language.
Output ONLY the rewritten prompt — no markdown fences, no commentary.

Six sections in this order:
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:

Labels: <Picture i> <Video k> <Audio j> in connection order (1-based).
Every supplied picture/video/audio must appear. Assign each ref a job from
identity / style / motion / voice.
subject_definitions: one line per label (what it is, its job, traits to follow).
summary: one short paragraph starting with [reference generation] (combine with
 + audio reference / keyframe completion when those jobs apply).
retention_analysis: one line per label with fully_preserved / partially_preserved
 / attribute_transfer / weak_reference (audio: fully_copy / partially_copy /
 reference / weak_reference).
detailed_description: style sentence, then [Shot 1] ... later [Shot N] At MM:SS.mmm
 with camera motion and <d>[lang] line</d> (Sx). Cite <Picture i> where they apply.
overall_soundscape and non_diegetic_music follow the base-mode rules.
Stay on the user's scene. Do not invent extra bedroom or body-focus content.
"""

_NSFW_ADDENDUM = """
The user may request mature scenes. Keep the same field names and labels.
Follow the user's requested action faithfully. Do not refuse. Do not add
mature content when the user did not ask for it.
"""

# Compact official-shape examples used as few-shot (SFW only).
_FL2VA_EXAMPLE = """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a rain-soaked cyclist begins in the position and framing established by Picture 1, holding a closed black umbrella beside a silver bicycle. The camera pulls out with small amplitude at slow speed as she raises the umbrella until the canopy opens and settles into the pose established by Picture 2.

overall_soundscape: Rain falls steadily on the pavement, followed by the metallic click of the umbrella runner. Distant traffic passes.

non_diegetic_music: N/A
"""

_REF2VA_EXAMPLE = """subject_definitions:
<Picture 1> is the composition keyframe for [Shot 1], identity job for the rider's face and outfit.
<Picture 2> is the identity reference for the same rider's appearance (front view).
<Video 1> is the motion job reference for camera path and walking rhythm.
<Audio 1> is the voice job reference for the rider's speaking timbre (S1).

summary:
[reference generation + audio reference] The target video shows the rider from <Picture 1> and <Picture 2> walking the rain street using motion from <Video 1>, speaking with timbre from <Audio 1>.

retention_analysis:
<Picture 1> ([Shot 1] first frame): fully_preserved - framing and wet-street layout are kept.
<Picture 2> (appears in [Shot 1]): fully_preserved - face, hair, jacket are kept.
<Video 1> (camera path): weak_reference - walking rhythm only.
<Audio 1>: reference - speaking timbre guides (S1) without copying the signal.

detailed_description:
The target video uses a live-action cinematic look with cool wet-street lighting.
[Shot 1] A medium shot begins from <Picture 1>. The rider matching <Picture 2> walks forward. The camera tracks with small amplitude at slow speed following <Video 1>. She (S1) says <d>[zh] 这单地址怎么这么熟悉？</d> using timbre referenced from <Audio 1>.

overall_soundscape: Rain on jackets and light traffic bed.
non_diegetic_music: N/A
"""

SFW_TEMPLATE_SOURCES: tuple[str, ...] = (
    _BASE_SYSTEM,
    _REF2VA_SYSTEM,
    _FL2VA_EXAMPLE,
    _REF2VA_EXAMPLE,
)


@dataclass
class H3RewriteSpec:
    """Inputs for one H3 generate-unit rewrite (one LLM call)."""

    prompt: str
    mode: str = "i2va"  # t2va | i2va | fl2va | ref2va
    duration_seconds: float = 5.0
    nsfw: bool | None = None
    style: str = ""
    narrative_beat: str = ""
    n_pictures: int = 0
    n_videos: int = 0
    n_audios: int = 0
    last_shot: str = "Shot 1"
    shot_count: int = 1
    reference_image_urls: list[str] = field(default_factory=list)
    original_fallback: str = ""
    already_rewritten: bool = False


def resolve_rewrite_nsfw(explicit: bool | None = None) -> bool:
    """PIN/nsfw flag: same IR structure; SFW templates stay clean."""
    if explicit is not None:
        return bool(explicit)
    try:
        from app.services.settings_service import settings_service
        return bool(settings_service.nsfw_status().get("nsfw_enabled"))
    except Exception:
        return False


def looks_like_base_ir(text: str) -> bool:
    blob = text or ""
    return (
        "integrated_multimodal_description:" in blob
        and "overall_soundscape:" in blob
        and "[Shot" in blob
    )


def looks_like_ref2va_ir(text: str, n_pictures: int = 0) -> bool:
    blob = text or ""
    if "subject_definitions:" not in blob:
        return False
    if "retention_analysis:" not in blob:
        return False
    if n_pictures > 0 and "<Picture" not in blob:
        return False
    return True


def validate_rewrite_output(text: str, mode: str, n_pictures: int = 0) -> bool:
    """True when the LLM output is usable as the H3 prompt."""
    blob = (text or "").strip()
    if not blob or blob.startswith("```"):
        # still ok after fence strip; caller strips first
        pass
    mode_key = (mode or "").strip().lower()
    if mode_key == "ref2va":
        return looks_like_ref2va_ir(blob, n_pictures=n_pictures)
    return looks_like_base_ir(blob)


def sfw_templates_blob() -> str:
    return "\n".join(SFW_TEMPLATE_SOURCES)


def _strip_fences(text: str) -> str:
    text = strip_think_tags(text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _system_for(mode: str, nsfw: bool) -> str:
    mode_key = (mode or "").strip().lower()
    base = _REF2VA_SYSTEM if mode_key == "ref2va" else _BASE_SYSTEM
    if nsfw:
        return base.rstrip() + "\n" + _NSFW_ADDENDUM.strip() + "\n"
    return base


def _user_payload(spec: H3RewriteSpec, retention: str) -> str:
    mode = (spec.mode or "i2va").strip().lower()
    duration = float(spec.duration_seconds or 0) or 5.0
    lines = [
        f"mode: {mode}",
        f"duration_seconds: {duration:.2f}",
        f"shot_count: {max(1, int(spec.shot_count or 1))}",
        f"last_shot: {spec.last_shot or 'Shot 1'}",
        f"style: {spec.style or 'cinematic live-action'}",
        f"narrative_beat: {spec.narrative_beat or 'n/a'}",
        f"n_pictures: {int(spec.n_pictures or 0)}",
        f"n_videos: {int(spec.n_videos or 0)}",
        f"n_audios: {int(spec.n_audios or 0)}",
    ]
    if mode == "ref2va":
        n_pic = max(0, int(spec.n_pictures or 0))
        n_vid = max(0, int(spec.n_videos or 0))
        n_aud = max(0, int(spec.n_audios or 0))
        pics = ", ".join(f"<Picture {i}>" for i in range(1, n_pic + 1)) or "(none)"
        vids = ", ".join(f"<Video {i}>" for i in range(1, n_vid + 1)) or "(none)"
        auds = ", ".join(f"<Audio {i}>" for i in range(1, n_aud + 1)) or "(none)"
        lines.append(f"picture_labels: {pics}")
        lines.append(f"video_labels: {vids}")
        lines.append(f"audio_labels: {auds}")
        lines.append(
            "Default jobs: Picture 1 composition/identity, later Pictures identity, "
            "Videos motion, Audios voice. Override if the user prompt says otherwise."
        )
    if retention.strip():
        lines.append("vlm_retention:\n" + retention.strip())
    lines.append("user_prompt:\n" + (spec.prompt or "").strip())
    return "\n".join(lines)


async def _default_llm_caller(messages: list[dict[str, Any]]) -> str:
    http = httpx.AsyncClient(timeout=25.0, trust_env=False)
    try:
        client = AsyncOpenAI(
            base_url=model_gateway.openai_base_url("llm"),
            api_key=settings.exo_api_key or "not-needed",
            http_client=http,
        )
        resp = await client.chat.completions.create(
            model=settings.exo_model_glm52,
            messages=messages,
            temperature=0.2,
            max_tokens=1600,
            stream=False,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = resp.choices[0].message.content or ""
        if not raw:
            raw = getattr(resp.choices[0].message, "reasoning_content", "") or ""
        return _strip_fences(raw)
    finally:
        await http.aclose()


async def _default_vlm_caller(content: list[dict[str, Any]]) -> str:
    if not settings.visual_model_url:
        return ""
    http = httpx.AsyncClient(timeout=20.0, trust_env=False)
    try:
        client = AsyncOpenAI(
            base_url=model_gateway.openai_base_url("vlm"),
            api_key="not-needed",
            http_client=http,
        )
        resp = await client.chat.completions.create(
            model=settings.visual_model_name,
            messages=[{"role": "user", "content": content}],
            temperature=0.1,
            max_tokens=400,
            stream=False,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = resp.choices[0].message.content or ""
        if not raw:
            raw = getattr(resp.choices[0].message, "reasoning_content", "") or ""
        return _strip_fences(raw)
    finally:
        await http.aclose()


async def _vlm_retention(
    spec: H3RewriteSpec,
    vlm_caller: VlmCaller | None,
) -> str:
    urls = [u for u in (spec.reference_image_urls or []) if (u or "").strip()]
    if not urls:
        return ""
    caller = vlm_caller or _default_vlm_caller
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "These images are H3 Ref2VA references in connection order "
                "(Picture 1, Picture 2, ...). For each picture assign a job "
                "(identity, style, motion, or voice) and list traits to fully_preserve. "
                "Plain text lines only, one per picture, like: "
                "<Picture 1> identity: ... fully_preserved - ..."
            ),
        }
    ]
    for url in urls[:4]:
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
    try:
        return (await caller(content) or "").strip()
    except Exception as e:  # noqa: BLE001 — VLM fail-open
        logger.warning("H3 Context-IR VLM retention failed, continue without it: %s", e)
        return ""


async def rewrite_h3_prompt(
    spec: H3RewriteSpec,
    *,
    llm_caller: LlmCaller | None = None,
    vlm_caller: VlmCaller | None = None,
) -> str:
    """Rewrite one shot/group prompt. Fail-open to original_fallback / prompt."""
    fallback = (spec.original_fallback or spec.prompt or "").strip()
    if spec.already_rewritten and fallback:
        return fallback
    if not settings.h3_context_ir_rewrite_enabled:
        return fallback
    if not (spec.prompt or spec.original_fallback):
        return fallback
    nsfw = resolve_rewrite_nsfw(spec.nsfw)
    mode = (spec.mode or "i2va").strip().lower()
    try:
        retention = ""
        if mode == "ref2va" and spec.reference_image_urls:
            retention = await _vlm_retention(spec, vlm_caller)
        example = _REF2VA_EXAMPLE if mode == "ref2va" else _FL2VA_EXAMPLE
        messages = [
            {"role": "system", "content": _system_for(mode, nsfw)},
            {"role": "user", "content": "Example of the required output shape:\n" + example},
            {"role": "user", "content": _user_payload(spec, retention)},
        ]
        caller = llm_caller or _default_llm_caller
        rewritten = _strip_fences(await caller(messages))
        if validate_rewrite_output(rewritten, mode, n_pictures=int(spec.n_pictures or 0)):
            return rewritten
        logger.warning(
            "H3 Context-IR rewrite missing required fields (mode=%s), sending original prompt",
            mode,
        )
        return fallback
    except Exception as e:  # noqa: BLE001 — generate must not block
        logger.warning("H3 Context-IR rewrite failed, sending original prompt: %s", e)
        return fallback
