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


# P4 A/B only — not the PIN default. Files are not on :8195 UNETLoader today;
# selecting the variant is opt-in and will fail preflight if weights are absent.
H3_UNET_NSFW_DASIWA = "DaSiWa_MiniMax_H3_fl2va.safetensors"
H3_REF_UNET_NSFW_DASIWA = "DaSiWa_REF2VA_Hybrid_v1.0.safetensors"
# Remix (civitai 2879272) is not on :8195 / local registry → not exposed as A/B.
H3_ADD_GUIDE_CLASS = "MiniMaxH3AddGuide"
H3_REPAIR_DENOISE_DEFAULT = 0.55
WAN_VIDEO_CLASS_TYPES = frozenset(
    {
        "WanImageToVideo",
        "WanFunInpaintToVideo",
        "Wan22ImageToVideoLatent",
        "WanFirstLastFrameToVideo",
    }
)


class H3RepairUnavailable(RuntimeError):
    """Repair requested but MiniMaxH3AddGuide is not on the H3 instance."""


def request_repair(body: dict[str, Any] | None = None) -> bool:
    """True when the request asks for local region/clip repair (inpaint)."""
    if not body:
        return False
    meta = metadata_of(body)
    for src in (
        body.get("repair"),
        meta.get("repair"),
        body.get("inpaint"),
        meta.get("inpaint"),
    ):
        if src is True:
            return True
        if src is False:
            continue
        if str(src or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def request_nsfw_variant(body: dict[str, Any] | None = None) -> str:
    """Opt-in NSFW A/B key. Empty / 10eros = PIN default. dasiwa is P4 A/B only."""
    if not body:
        return ""
    meta = metadata_of(body)
    raw = (
        body.get("nsfw_variant")
        or meta.get("nsfw_variant")
        or body.get("h3_nsfw_variant")
        or ""
    )
    return str(raw or "").strip().lower()


def request_repair_denoise(body: dict[str, Any] | None = None) -> float:
    if not body:
        return H3_REPAIR_DENOISE_DEFAULT
    meta = metadata_of(body)
    raw = body.get("repair_denoise")
    if raw is None:
        raw = meta.get("repair_denoise")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return H3_REPAIR_DENOISE_DEFAULT
    return min(1.0, max(0.05, val))


def request_inpaint_mask(body: dict[str, Any] | None = None) -> str:
    if not body:
        return ""
    meta = metadata_of(body)
    return str(
        body.get("inpaint_mask_url")
        or body.get("denoise_mask")
        or meta.get("inpaint_mask_url")
        or meta.get("denoise_mask")
        or ""
    ).strip()


def h3_unets(nsfw: bool, variant: str | None = None) -> tuple[str, str]:
    """Return (fl2va_unet, ref2va_unet) for the PIN/nsfw flag.

    NSFW default is always 10Eros. variant='dasiwa' is P4 A/B opt-in only.
    Remix is not in the local registry and is never selected here.
    """
    key = str(variant or "").strip().lower()
    if nsfw and key in {"dasiwa", "dasiwa_minimax_h3", "civitai-2877206"}:
        return (
            os.getenv("LOCAL_H3_DASIWA_UNET", H3_UNET_NSFW_DASIWA),
            os.getenv("LOCAL_H3_DASIWA_REF_UNET", H3_REF_UNET_NSFW_DASIWA),
        )
    if nsfw:
        return (
            os.getenv("LOCAL_H3_NSFW_UNET", H3_UNET_NSFW),
            os.getenv("LOCAL_H3_NSFW_REF_UNET", H3_REF_UNET_NSFW),
        )
    return (
        os.getenv("LOCAL_H3_UNET", H3_UNET_SFW),
        os.getenv("LOCAL_H3_REF_UNET", H3_REF_UNET_SFW),
    )


def h3_add_guide_available(object_info: dict[str, Any] | None) -> bool:
    return isinstance(object_info, dict) and H3_ADD_GUIDE_CLASS in object_info


def require_h3_add_guide(object_info: dict[str, Any] | None) -> None:
    """Fail-closed when MiniMaxH3AddGuide is missing. Never fall back to Wan."""
    if h3_add_guide_available(object_info):
        return
    raise H3RepairUnavailable(
        "H3 local repair requires MiniMaxH3AddGuide on :8195 plus a denoise_mask "
        "inpaint path. The node is not available on this H3 instance. "
        "Refusing to fall back to Wan/LTX."
    )


def workflow_has_wan(workflow: dict[str, Any]) -> bool:
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if ct in WAN_VIDEO_CLASS_TYPES:
            return True
        lowered = ct.lower()
        if lowered.startswith("wan") and "video" in lowered:
            return True
    return False


def apply_h3_repair_guide(
    workflow: dict[str, Any],
    *,
    mask_name: str = "",
    guide_image_name: str = "",
    denoise: float = H3_REPAIR_DENOISE_DEFAULT,
    frame_idx: int = 0,
) -> dict[str, Any]:
    """Insert MiniMaxH3AddGuide + denoise_mask (SetLatentNoiseMask) into an H3 graph.

    Caller must have already required MiniMaxH3AddGuide. Does not switch UNet
    or insert Wan nodes. Guide image defaults to existing LoadImage node 10.
    """
    if workflow_has_wan(workflow):
        raise H3RepairUnavailable("H3 repair graph must not contain Wan video nodes")
    cond_src = ["20", 0]
    latent_src = ["20", 1]
    image_src = ["10", 0]
    if guide_image_name:
        workflow["110"] = {"class_type": "LoadImage", "inputs": {"image": guide_image_name}}
        image_src = ["110", 0]
    elif "10" in workflow:
        image_src = ["10", 0]
    elif "11" in workflow:
        image_src = ["11", 0]
    else:
        raise H3RepairUnavailable(
            "H3 repair needs a guide image (first frame / existing clip). "
            "Empty T2VA repair is not supported."
        )
    workflow["112"] = {
        "class_type": H3_ADD_GUIDE_CLASS,
        "inputs": {
            "positive": cond_src,
            "latent": latent_src,
            "frame_idx": int(frame_idx),
            "vae": ["3", 0],
            "image": image_src,
        },
    }
    guider = workflow.get("33")
    if isinstance(guider, dict) and guider.get("class_type") == "BasicGuider":
        guider.setdefault("inputs", {})["conditioning"] = ["112", 0]

    latent_for_sampler = latent_src
    if mask_name:
        workflow["111"] = {
            "class_type": "LoadImageMask",
            "inputs": {"image": mask_name, "channel": "red"},
        }
        workflow["113"] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": latent_src, "mask": ["111", 0]},
        }
        latent_for_sampler = ["113", 0]
    sampler = workflow.get("34")
    if isinstance(sampler, dict) and sampler.get("class_type") == "SamplerCustomAdvanced":
        sampler.setdefault("inputs", {})["latent_image"] = latent_for_sampler

    scheduler = workflow.get("32")
    if isinstance(scheduler, dict) and scheduler.get("class_type") == "BasicScheduler":
        scheduler.setdefault("inputs", {})["denoise"] = float(denoise)

    if workflow_has_wan(workflow):
        raise H3RepairUnavailable("H3 repair graph must not contain Wan video nodes")
    return workflow


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


# ---------------------------------------------------------------------------
# P3: two-speed H3 — Turbo preview vs native 20-step final
# preview=true OR quality=preview → MiniMaxH3TurboLoRA + MiniMaxH3TurboSampler
# omit / preview=false / quality=final → turbo OFF, native 20 steps
# Do not stack turbo + content LoRA (skip content LoRA when turbo on)
# ---------------------------------------------------------------------------

H3_TURBO_LORA_SFW = "minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors"
H3_TURBO_LORA_NSFW = "10Eros_Max_h3_TURBO_ref2va.safetensors"
H3_TURBO_FL2VA_STEPS = 8
H3_TURBO_REF2VA_STEPS = 4
H3_TURBO_STRENGTH = 1.0
H3_TURBO_LOW_VRAM = False
H3_TURBO_LORA_NODE = "100"
H3_TURBO_SAMPLER_NODE = "101"
_CONTENT_LORA_CLASS_TYPES = frozenset(
    {
        "LoraLoader",
        "LoraLoaderModelOnly",
        "PowerLoraLoader",
        "Lora Loader",
    }
)
_FINAL_QUALITY = frozenset({"final", "delivery", "baseline", "max"})


def _truthy(src: Any) -> bool:
    if src is True:
        return True
    if src is False or src is None:
        return False
    return str(src).strip().lower() in {"1", "true", "yes", "on"}


def request_h3_preview(body: dict[str, Any] | None = None) -> bool:
    """True when preview=true OR quality=preview. quality=final forces off."""
    if not body:
        return False
    meta = metadata_of(body)
    quality = str(body.get("quality") or meta.get("quality") or "").strip().lower()
    if quality == "preview":
        return True
    if quality in _FINAL_QUALITY:
        return False
    return _truthy(body.get("preview")) or _truthy(meta.get("preview"))


def resolve_h3_turbo_lora_name(nsfw: bool = False) -> str:
    """SFW product default turbo LoRA; NSFW may use 10Eros turbo. SFW never 10Eros."""
    if nsfw:
        name = (os.getenv("LOCAL_H3_NSFW_TURBO_LORA") or H3_TURBO_LORA_NSFW).strip()
        if name:
            return name
    name = (os.getenv("LOCAL_H3_TURBO_LORA") or H3_TURBO_LORA_SFW).strip()
    if "10eros" in name.lower():
        return H3_TURBO_LORA_SFW
    return name or H3_TURBO_LORA_SFW


def h3_turbo_steps_for_mode(mode: str) -> int:
    if (mode or "").lower() in {"r2v", "ref2va"}:
        try:
            return int(os.getenv("LOCAL_H3_TURBO_REF2VA_STEPS") or H3_TURBO_REF2VA_STEPS)
        except ValueError:
            return H3_TURBO_REF2VA_STEPS
    try:
        return int(os.getenv("LOCAL_H3_TURBO_FL2VA_STEPS") or H3_TURBO_FL2VA_STEPS)
    except ValueError:
        return H3_TURBO_FL2VA_STEPS


def workflow_has_content_lora(workflow: dict[str, Any]) -> list[str]:
    """Node ids of content LoRA (not MiniMaxH3TurboLoRA). Stacking causes shape error."""
    found: list[str] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if not ct or ct == "MiniMaxH3TurboLoRA":
            continue
        if ct in _CONTENT_LORA_CLASS_TYPES:
            found.append(str(nid))
            continue
        lowered = ct.lower()
        if "lora" in lowered and "turbo" not in lowered:
            found.append(str(nid))
    return found


def skip_content_lora(workflow: dict[str, Any]) -> list[str]:
    """When turbo is on, drop content LoRA and rewire consumers back to UNETLoader."""
    removed = workflow_has_content_lora(workflow)
    for lora_id in removed:
        for nid, node in list(workflow.items()):
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for key, value in list(inputs.items()):
                if isinstance(value, list) and len(value) == 2 and str(value[0]) == str(lora_id):
                    inputs[key] = ["1", 0]
        workflow.pop(lora_id, None)
    return removed


def apply_h3_turbo_to_workflow(
    workflow: dict[str, Any],
    *,
    mode: str = "fl2va",
    nsfw: bool = False,
) -> dict[str, Any]:
    """Insert MiniMaxH3TurboLoRA + MiniMaxH3TurboSampler. Caller already decided preview.

    FL2VA 8 steps, Ref2VA 4 steps. SFW LoRA is never 10Eros. Content LoRA is skipped.
    """
    skip_content_lora(workflow)
    if workflow.get("1", {}).get("class_type") != "UNETLoader":
        return workflow

    lora_node_id = H3_TURBO_LORA_NODE
    sampler_node_id = H3_TURBO_SAMPLER_NODE
    resolved_lora = resolve_h3_turbo_lora_name(nsfw)
    if not nsfw and "10eros" in resolved_lora.lower():
        resolved_lora = H3_TURBO_LORA_SFW
    try:
        strength = float(os.getenv("LOCAL_H3_TURBO_STRENGTH") or H3_TURBO_STRENGTH)
    except ValueError:
        strength = H3_TURBO_STRENGTH
    low_vram = str(os.getenv("LOCAL_H3_TURBO_LOW_VRAM") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    resolved_steps = h3_turbo_steps_for_mode(mode)

    workflow[lora_node_id] = {
        "class_type": "MiniMaxH3TurboLoRA",
        "inputs": {
            "model": ["1", 0],
            "lora_name": resolved_lora,
            "strength": strength,
            "low_vram": low_vram,
        },
    }
    for nid, node in workflow.items():
        if nid == lora_node_id or not isinstance(node, dict) or "inputs" not in node:
            continue
        for key, value in list(node["inputs"].items()):
            if isinstance(value, list) and len(value) == 2 and value == ["1", 0]:
                node["inputs"][key] = [lora_node_id, 0]

    workflow[sampler_node_id] = {
        "class_type": "MiniMaxH3TurboSampler",
        "inputs": {},
    }
    sampler_advanced = workflow.get("34", {})
    if isinstance(sampler_advanced, dict) and sampler_advanced.get("class_type") == "SamplerCustomAdvanced":
        sampler_advanced.setdefault("inputs", {})["sampler"] = [sampler_node_id, 0]

    scheduler = workflow.get("32", {})
    if isinstance(scheduler, dict) and scheduler.get("class_type") == "BasicScheduler":
        scheduler.setdefault("inputs", {})["steps"] = resolved_steps
    return workflow
