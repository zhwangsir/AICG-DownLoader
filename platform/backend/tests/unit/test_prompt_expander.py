"""统一提示词扩写系统单元测试（短剧场景 IR → H3/LTX 双引擎编译器）。

覆盖：
- H3ContextIRCompiler：官方 Context-IR 三字段结构 / 对齐指令 / <d> 台词 / (Sx) 说话人 / N/A 规则
- validate_h3_prompt：时间戳递增与上限、<d> 配对、(Sx) 连续编号
- LTXProseCompiler：六要素顺序 / 单段现在时 / 引号台词注明语言 / 4-8 句 / 多镜转场
- PromptExpander：LLM 扩写成功/失败回退/开关关闭/坏 JSON
- recommended_quality_params：H3/LTX 各质量档参数
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.prompt_expander import (
    DialogueSpec,
    H3ContextIRCompiler,
    LTXProseCompiler,
    PromptExpander,
    ShotSpec,
    recommended_quality_params,
    validate_h3_prompt,
)


def _shot(**kw) -> ShotSpec:
    base = dict(
        duration_ms=3000,
        shot_type="medium close-up",
        setting="Dim convenience-store fluorescent lighting, cool color tone",
        action="Lin Yuan lowers his head and stares at the phone, brows tightly knit",
        characters=["A young delivery rider in a yellow jacket, shoulders tense"],
        camera_movement="slow dolly in",
        dialogue=[DialogueSpec(speaker_id=1, language="zh", text="这单地址怎么这么熟悉？")],
        ambience="hum of refrigerators and distant traffic",
        music="",
    )
    base.update(kw)
    return ShotSpec(**base)


# ---------------------------------------------------------------------------
# H3 Context-IR 编译器
# ---------------------------------------------------------------------------


class TestH3ContextIRCompiler:
    def test_three_field_structure_and_alignment(self):
        text = H3ContextIRCompiler().compile(_shot(mode="fl2va"))
        lines = text.split("\n")
        # 首行：FL2VA 官方对齐指令（首帧 0.00s + 末帧 3.00s）
        assert lines[0].startswith("How the reference pictures align")
        assert "Picture 1 (from Shot 1) aligns with the 0.00-second mark" in lines[0]
        assert "Picture 2 (from Shot 1) aligns with the 3.00-second mark" in lines[0]
        # 三字段齐全
        assert "integrated_multimodal_description:" in text
        assert "overall_soundscape:" in text
        assert "non_diegetic_music:" in text
        # 镜头标签 + 台词 <d>[语言] 原文</d> (Sx)
        assert "[Shot 1]" in text
        assert "<d>[zh] 这单地址怎么这么熟悉？</d> (S1)" in text
        # 无配乐 → N/A，且 music 字段位于最末（官方 prompt 结构约定）
        assert text.rstrip().endswith("non_diegetic_music: N/A")

    def test_i2va_alignment_single_picture(self):
        text = H3ContextIRCompiler().compile(_shot(mode="i2va"))
        assert "Picture 1 (from Shot 1) aligns with the 0.00-second mark" in text
        assert "Picture 2" not in text

    def test_t2va_has_no_alignment_line(self):
        text = H3ContextIRCompiler().compile(_shot(mode="t2va"))
        assert text.startswith("integrated_multimodal_description:")
        assert "Picture 1" not in text

    def test_empty_ambience_writes_na(self):
        text = H3ContextIRCompiler().compile(_shot(ambience=""))
        assert "overall_soundscape: N/A" in text

    def test_music_present_written_verbatim(self):
        text = H3ContextIRCompiler().compile(
            _shot(music="A tense pulsating electronic score")
        )
        assert "non_diegetic_music: A tense pulsating electronic score" in text


# ---------------------------------------------------------------------------
# H3 prompt 机械校验器
# ---------------------------------------------------------------------------


class TestValidateH3Prompt:
    VALID = (
        "integrated_multimodal_description: [Shot 1] a girl waits. "
        "<d>[zh] 你好</d> (S1) "
        "[Shot 2] At 00:03.000, the camera cuts to a rainy street. "
        "<d>[zh] 走了</d> (S2)"
    )

    def test_valid_prompt_passes(self):
        assert validate_h3_prompt(self.VALID, 6000) == []

    def test_timestamp_not_strictly_increasing(self):
        text = (
            "[Shot 2] At 00:05.000, the camera cuts to x. "
            "[Shot 3] At 00:02.000, the camera cuts to y."
        )
        errors = validate_h3_prompt(text, 10000)
        assert any("递增" in e for e in errors)

    def test_timestamp_equal_is_not_increasing(self):
        text = (
            "[Shot 2] At 00:03.000, the camera cuts to x. "
            "[Shot 3] At 00:03.000, the camera cuts to y."
        )
        errors = validate_h3_prompt(text, 10000)
        assert any("递增" in e for e in errors)

    def test_timestamp_exceeds_total_duration(self):
        text = "[Shot 2] At 00:09.000, the camera cuts to x."
        errors = validate_h3_prompt(text, 6000)
        assert any("总时长" in e for e in errors)

    def test_unpaired_dialogue_tag(self):
        errors = validate_h3_prompt("[Shot 1] <d>[zh] 你好 (S1)", 6000)
        assert any("<d>" in e for e in errors)

    def test_speaker_ids_must_be_consecutive(self):
        text = "[Shot 1] <d>[zh] 你好</d> (S1) <d>[zh] 嗨</d> (S3)"
        errors = validate_h3_prompt(text, 6000)
        assert any("说话人" in e for e in errors)

    def test_speaker_ids_must_start_from_s1(self):
        text = "[Shot 1] <d>[zh] 你好</d> (S2)"
        errors = validate_h3_prompt(text, 6000)
        assert any("说话人" in e for e in errors)


# ---------------------------------------------------------------------------
# LTX-2.5 散文式编译器
# ---------------------------------------------------------------------------


def _count_sentences(text: str) -> int:
    return len([p for p in re.split(r"[.!?。！？]", text) if p.strip()])


class TestLTXProseCompiler:
    def test_single_paragraph_six_elements_in_order(self):
        text = LTXProseCompiler().compile(_shot())
        # 单段（无换行）
        assert "\n" not in text
        # 六要素顺序：镜头规模 → 场景(灯光/色调) → 动作 → 角色 → 运镜 → 音频
        i_shot = text.index("medium close-up")
        i_setting = text.index("fluorescent")
        i_action = text.index("lowers his head")
        i_char = text.index("yellow jacket")
        i_cam = text.index("camera")
        i_audio = text.index("hum of refrigerators")
        assert i_shot < i_setting < i_action < i_char < i_cam < i_audio
        # 引号内台词 + 语言注明
        assert '"这单地址怎么这么熟悉？"' in text
        assert "Mandarin Chinese" in text
        # 单镜头 4-8 句
        assert 4 <= _count_sentences(text) <= 8

    def test_minimal_shot_still_four_sentences(self):
        text = LTXProseCompiler().compile(ShotSpec(action="Rain falls on the empty street"))
        assert "medium shot" in text  # 默认景别
        assert "Rain falls on the empty street" in text
        assert "camera" in text
        assert 4 <= _count_sentences(text) <= 8

    def test_music_sentence_when_present(self):
        text = LTXProseCompiler().compile(_shot(music="A gentle solo piano melody"))
        assert "A gentle solo piano melody" in text

    def test_compile_sequence_transition_and_audio_continuity(self):
        text = LTXProseCompiler().compile_sequence(
            [_shot(), _shot(action="He looks up slowly")]
        )
        assert "cuts to" in text  # 转场动词连接
        assert "seamlessly" in text  # 音频连续性声明

    def test_compile_sequence_empty_returns_empty(self):
        assert LTXProseCompiler().compile_sequence([]) == ""


# ---------------------------------------------------------------------------
# PromptExpander（LLM 扩写 + 确定性回退）
# ---------------------------------------------------------------------------


def _llm_client_returning(content: str) -> MagicMock:
    result = MagicMock()
    result.choices = [MagicMock()]
    result.choices[0].message.content = content
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=result)
    return client


class TestPromptExpander:
    async def test_llm_expansion_ltx(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_expander_enabled", True)
        payload = json.dumps({
            "shot_type": "wide tracking shot",
            "action": "A courier dashes through neon-lit rain",
        })
        with patch(
            "app.services.prompt_expander.get_shared_llm_client",
            return_value=_llm_client_returning(payload),
        ):
            out = await PromptExpander().expand("雨夜外卖员狂奔", "ltx", ShotSpec())
        assert "wide tracking shot" in out
        assert "A courier dashes through neon-lit rain" in out
        assert "\n" not in out  # LTX 单段散文

    async def test_llm_expansion_h3(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_expander_enabled", True)
        payload = json.dumps({"action": "She turns around slowly"})
        with patch(
            "app.services.prompt_expander.get_shared_llm_client",
            return_value=_llm_client_returning(payload),
        ):
            out = await PromptExpander().expand(
                "她缓缓转身", "h3", ShotSpec(mode="t2va")
            )
        assert "integrated_multimodal_description:" in out
        assert "She turns around slowly" in out

    async def test_llm_failure_falls_back_to_deterministic(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_expander_enabled", True)
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("llm down"))
        with patch(
            "app.services.prompt_expander.get_shared_llm_client", return_value=client
        ):
            out = await PromptExpander().expand("任意描述", "ltx", _shot())
        # 回退确定性模板：原 ShotSpec 字段直接进入编译结果，不阻断
        assert "lowers his head" in out
        assert "\n" not in out

    async def test_llm_bad_json_falls_back(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_expander_enabled", True)
        with patch(
            "app.services.prompt_expander.get_shared_llm_client",
            return_value=_llm_client_returning("not-json{{{"),
        ):
            out = await PromptExpander().expand("任意描述", "ltx", _shot())
        assert "lowers his head" in out

    async def test_disabled_skips_llm(self, monkeypatch):
        monkeypatch.setattr(settings, "prompt_expander_enabled", False)
        with patch(
            "app.services.prompt_expander.get_shared_llm_client"
        ) as mock_client:
            out = await PromptExpander().expand("雨夜", "h3", _shot(mode="t2va"))
        mock_client.assert_not_called()
        assert "integrated_multimodal_description:" in out

    async def test_unknown_engine_raises(self):
        with pytest.raises(ValueError):
            await PromptExpander().expand("", "wan", ShotSpec())


# ---------------------------------------------------------------------------
# 质量参数推荐（模块C）
# ---------------------------------------------------------------------------


class TestRecommendedQualityParams:
    def test_h3_preview_turbo_4_steps(self):
        params = recommended_quality_params("h3", "preview")
        assert params["turbo"] is True
        assert params["steps"] == 4
        assert params["sampler"] == "res_multistep"
        assert params["scheduler"] == "simple"
        assert params["shift_video"] == 12.0
        assert params["shift_audio"] == 3.0
        assert params["min_short_side"] == 384

    def test_h3_delivery_turbo_6_to_8_steps(self):
        params = recommended_quality_params("h3", "delivery")
        assert params["turbo"] is True
        assert 6 <= params["steps"] <= 8

    def test_h3_baseline_native_20_steps(self):
        params = recommended_quality_params("h3", "baseline")
        assert params["turbo"] is False
        assert params["steps"] == 20

    def test_ltx_preview_distilled_single_stage(self):
        params = recommended_quality_params("ltx", "preview")
        assert params["mode"] == "distilled"
        assert params["stages"] == 1
        assert params["steps"] == 8
        assert params["cfg"] == 1.0
        assert params["fps"] == 25

    def test_ltx_delivery_two_stage_8_plus_3(self):
        params = recommended_quality_params("ltx", "delivery")
        assert params["stages"] == 2
        assert params["stage_steps"] == [8, 3]
        assert params["cfg"] == 1.0

    def test_ltx_max_dev_with_distilled_lora(self):
        params = recommended_quality_params("ltx", "max")
        assert params["mode"] == "dev"
        assert params["steps_range"] == [15, 40]
        assert params["cfg_range"] == [3.0, 3.5]
        assert params["distilled_lora_strength_range"] == [0.2, 0.5]

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError):
            recommended_quality_params("wan", "preview")

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            recommended_quality_params("h3", "ultra")
