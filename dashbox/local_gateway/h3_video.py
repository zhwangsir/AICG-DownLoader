"""MiniMax H3 video routing helpers for local_gateway (P0).

Locked routing:
- reference images/videos/audios → MiniMaxH3ReferenceToVideo (ref2va UNet)
- only first/last frames → MiniMaxH3ImageToVideo (fl2va UNet)
- no frames → T2VA = ImageToVideo with first/last omitted
- NSFW PIN/flag → 10Eros UNets; SFW → minimax_h3_* UNets
- 2K is not local H3-Regenerate; do not treat DashBox 1.5x scale as 2K
"""

from __future__ import annotations

import os
from typing import Any

H3_UNET_SFW = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
H3_REF_UNET_SFW = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
H3_UNET_NSFW = "10Eros_Max_h3_fl2va_beta2_pruned_int8_convrot.safetensors"
H3_REF_UNET_NSFW = "10Eros_Max_h3_ref2va_beta2_pruned_int8_convrot.safetensors"

# Native H3 canvas. 2k/4k must not 1.5x/2.0x-scale this (that is not H3-Regenerate-2K).
_H3_FAKE_HIRES = {"2k", "4k"}


def _as_url_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            url = str(item or "").strip()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out
    return []


def metadata_of(body: dict[str, Any]) -> dict[str, Any]:
    meta = body.get("metadata")
    return meta if isinstance(meta, dict) else {}


def request_nsfw(body: dict[str, Any] | None = None) -> bool:
    """True when the request nsfw flag / metadata / LOCAL_H3_NSFW is on."""
    env = str(os.getenv("LOCAL_H3_NSFW") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        # explicit env off still allows per-request flag
        pass
    if not body:
        return False
    meta = metadata_of(body)
    for src in (body.get("nsfw"), meta.get("nsfw"), body.get("nsfw_enabled"), meta.get("nsfw_enabled")):
        if src is True:
            return True
        if src is False:
            continue
        if str(src or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def collect_video_inputs(body: dict[str, Any]) -> dict[str, Any]:
    """Pull first/last frames and multimodal refs from top-level or metadata."""
    meta = metadata_of(body)
    first = str(
        body.get("first_frame_image")
        or body.get("image")
        or body.get("image_url")
        or meta.get("first_frame_image")
        or meta.get("image_url")
        or meta.get("image")
        or ""
    ).strip()
    last = str(body.get("last_frame_image") or meta.get("last_frame_image") or "").strip()
    ref_images = _as_url_list(body.get("reference_images")) or _as_url_list(meta.get("reference_images"))
    ref_videos = _as_url_list(body.get("reference_videos")) or _as_url_list(meta.get("reference_videos"))
    ref_audios = _as_url_list(body.get("reference_audios")) or _as_url_list(meta.get("reference_audios"))
    return {
        "first": first,
        "last": last,
        "ref_images": ref_images,
        "ref_videos": ref_videos,
        "ref_audios": ref_audios,
    }


def has_multimodal_refs(inputs: dict[str, Any]) -> bool:
    return bool(inputs.get("ref_images") or inputs.get("ref_videos") or inputs.get("ref_audios"))


def select_h3_mode(inputs: dict[str, Any]) -> str:
    """r2v | i2v | t2va. Refs win over first/last (P0)."""
    if has_multimodal_refs(inputs):
        return "r2v"
    if inputs.get("first") or inputs.get("last"):
        return "i2v"
    return "t2va"


def h3_unets(nsfw: bool) -> tuple[str, str]:
    """Return (fl2va_unet, ref2va_unet) for the PIN/nsfw flag."""
    if nsfw:
        return (
            os.getenv("LOCAL_H3_NSFW_UNET", H3_UNET_NSFW),
            os.getenv("LOCAL_H3_NSFW_REF_UNET", H3_REF_UNET_NSFW),
        )
    return (
        os.getenv("LOCAL_H3_UNET", H3_UNET_SFW),
        os.getenv("LOCAL_H3_REF_UNET", H3_REF_UNET_SFW),
    )


def h3_resolution_scale(resolution: str, default_scale: float) -> float:
    """Drop fake 2K/4K scale for local H3. 768P native stays 1.0."""
    if str(resolution or "").strip().lower() in _H3_FAKE_HIRES:
        return 1.0
    return default_scale


def r2v_ref_images(inputs: dict[str, Any]) -> list[str]:
    """Composition first-frame (if any) + reference images, de-duplicated, max 9."""
    ordered: list[str] = []
    seen: set[str] = set()
    first = str(inputs.get("first") or "").strip()
    if first:
        ordered.append(first)
        seen.add(first)
    for url in inputs.get("ref_images") or []:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered[:9]
