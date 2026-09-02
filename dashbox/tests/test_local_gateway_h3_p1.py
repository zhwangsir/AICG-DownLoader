"""P1: local H3 Context-IR rewrite on dashbox local_gateway."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from local_gateway.h3_context_ir import (
    ADULT_VOCAB,
    rewrite_h3_prompt,
    sfw_templates_blob,
    validate_rewrite_output,
)


def _fl2va() -> str:
    return (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        "Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.\n\n"
        "integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium shot.\n"
        "overall_soundscape: Rain on pavement.\n"
        "non_diegetic_music: N/A"
    )


def _ref2va(n: int = 2) -> str:
    pics = "\n".join(f"<Picture {i}> is identity ref {i}." for i in range(1, n + 1))
    body = " ".join(f"<Picture {i}>" for i in range(1, n + 1))
    return (
        "subject_definitions:\n"
        f"{pics}\n"
        "summary:\n"
        f"[reference generation] Keep {body}.\n"
        "retention_analysis:\n"
        "<Picture 1>: fully_preserved - framing.\n"
        "detailed_description:\n"
        f"[Shot 1] Opens from {body}.\n"
        "overall_soundscape: Room tone.\n"
        "non_diegetic_music: N/A"
    )


def test_sfw_template_has_no_adult_vocabulary():
    blob = sfw_templates_blob().lower()
    for word in ADULT_VOCAB:
        assert re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", blob) is None, word


@pytest.mark.asyncio
async def test_fl2va_output_contains_shot_and_soundscape(monkeypatch):
    monkeypatch.setattr("local_gateway.h3_context_ir.REWRITE_ENABLED", True)

    async def llm(_messages):
        return _fl2va()

    out = await rewrite_h3_prompt(
        "rider in the rain",
        mode="fl2va",
        duration=5,
        original_fallback="rider in the rain",
        llm_caller=llm,
    )
    assert "integrated_multimodal_description:" in out
    assert "[Shot" in out
    assert "overall_soundscape:" in out


@pytest.mark.asyncio
async def test_ref2va_output_contains_picture_tags(monkeypatch):
    monkeypatch.setattr("local_gateway.h3_context_ir.REWRITE_ENABLED", True)

    async def llm(_messages):
        return _ref2va(3)

    async def vlm(_content):
        return "<Picture 1> identity fully_preserved"

    out = await rewrite_h3_prompt(
        "lock this face",
        mode="ref2va",
        n_pictures=3,
        reference_image_urls=["http://x/a.png", "http://x/b.png", "http://x/c.png"],
        original_fallback="lock this face",
        llm_caller=llm,
        vlm_caller=vlm,
    )
    assert "subject_definitions:" in out
    for i in range(1, 4):
        assert f"<Picture {i}>" in out


@pytest.mark.asyncio
async def test_failure_falls_back_to_original(monkeypatch, caplog):
    monkeypatch.setattr("local_gateway.h3_context_ir.REWRITE_ENABLED", True)
    original = "plain prompt"

    async def boom(_messages):
        raise RuntimeError("spark down")

    with caplog.at_level(logging.WARNING):
        out = await rewrite_h3_prompt(
            "plain prompt",
            mode="fl2va",
            original_fallback=original,
            llm_caller=boom,
        )
    assert out == original
    assert any("original prompt" in r.message for r in caplog.records)


def test_validate_ref2va_requires_picture_when_n():
    assert validate_rewrite_output(_ref2va(2), "ref2va", n_pictures=2)
    assert not validate_rewrite_output("subject_definitions:\nnone", "ref2va", n_pictures=2)
