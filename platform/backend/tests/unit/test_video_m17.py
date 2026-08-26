"""M17: MiniMax H3 全模态能力释放单元测试。

覆盖：
- M17.1 原生 CUT 语法（Context-IR [Shot N] + At MM:SS.mmm cuts to，含保险丝回退）
- M17.2 原生音频方向（overall_soundscape / non_diegetic_music 确定性注入）
- M17.3 FL2VA 首帧+末帧双锚定（官方对齐指令 + last_frame 接线 + 组间链式）
- M17.4 ref2va 全模态参考（<Video N>/<Audio N> 标签引导 + LoadVideo/LoadAudio 组挂接）

conftest._patch_settings 默认 video_backend='comfyui'，H3 用例局部 monkeypatch 为 'h3'。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.video_agent import (
    VideoAgent,
    _append_audio_direction,
    _snap_h3_frames,
    build_audio_direction,
    build_fl2va_alignment_instruction,
    build_multishot_prompt,
    build_r2v_media_guide,
)
from app.config import settings
from app.models.schemas import VideoRequest


def _req(
    scene_id: int,
    episode: int = 1,
    duration: int = 4,
    prompt: str = "p",
    refs: list[str] | None = None,
    beat: str = "",
    last_frame: str = "",
    videos: list[str] | None = None,
    audios: list[str] | None = None,
) -> VideoRequest:
    return VideoRequest(
        scene_id=scene_id,
        episode=episode,
        image_url=f"http://x/sb_{scene_id}.png",
        prompt=prompt,
        duration_seconds=duration,
        reference_images=refs or [],
        narrative_beat=beat,
        last_frame_url=last_frame,
        reference_videos=videos or [],
        reference_audios=audios or [],
    )


@pytest.fixture
def agent():
    return VideoAgent()


@pytest.fixture
def mock_upload_media():
    """Mock BaseAgent.upload_media_to_comfyui，按 URL 尾段返回文件名。"""
    with patch(
        "app.agents.base.BaseAgent.upload_media_to_comfyui", new_callable=AsyncMock
    ) as mock:
        mock.side_effect = (
            lambda worker_url, url, fallback_name="input.bin": url.rsplit("/", 1)[-1]
        )
        yield mock


# ---------------------------------------------------------------------------
# M17.1 原生 CUT 语法
# ---------------------------------------------------------------------------


class TestNativeCutSyntax:
    """官方 Context-IR：integrated_multimodal_description + [Shot N] At MM:SS.mmm cuts to。"""

    def test_default_uses_context_ir_format(self):
        prompt = build_multishot_prompt(
            [_req(1, prompt="alpha"), _req(2, prompt="beta")]
        )
        assert "integrated_multimodal_description:" in prompt
        assert "[Shot 1] alpha" in prompt
        assert "[Shot 2] At 00:04.000, the camera cuts to beta" in prompt

    def test_cut_timestamps_accumulate_durations(self):
        """后续镜时间戳 = 前序场景时长累计（3s/4s/5s → 00:03.000 / 00:07.000）。"""
        prompt = build_multishot_prompt(
            [
                _req(1, duration=3, prompt="a"),
                _req(2, duration=4, prompt="b"),
                _req(3, duration=5, prompt="c"),
            ]
        )
        assert "[Shot 2] At 00:03.000, the camera cuts to b" in prompt
        assert "[Shot 3] At 00:07.000, the camera cuts to c" in prompt

    def test_first_shot_has_no_timestamp(self):
        prompt = build_multishot_prompt([_req(1, prompt="alpha"), _req(2, prompt="b")])
        shot1 = prompt.split("[Shot 2]")[0]
        assert "At 00:00" not in shot1

    def test_native_cut_false_falls_back_to_legacy(self):
        """保险丝：显式 native_cut=False 回退 M11 旧版 SHOT X: 格式。"""
        prompt = build_multishot_prompt(
            [_req(1, prompt="alpha"), _req(2, prompt="beta")], native_cut=False
        )
        assert "integrated_multimodal_description" not in prompt
        assert "SHOT 1: alpha" in prompt
        assert "SHOT 2: beta" in prompt

    def test_setting_disabled_falls_back_to_legacy(self, monkeypatch):
        """settings.h3_native_cut_prompt_enabled=False 时默认走旧版格式。"""
        monkeypatch.setattr(settings, "h3_native_cut_prompt_enabled", False)
        prompt = build_multishot_prompt([_req(1, prompt="alpha"), _req(2, prompt="b")])
        assert "SHOT 1: alpha" in prompt
        assert "[Shot 1]" not in prompt

    def test_beat_hints_preserved_in_native_format(self):
        """M12.1 节拍视觉指令在原生 CUT 格式下仍注入对应镜段。"""
        prompt = build_multishot_prompt(
            [_req(1, prompt="alpha", beat="hook"), _req(2, prompt="beta")]
        )
        shot1_seg = prompt.split("[Shot 2]")[0]
        assert "high-contrast" in shot1_seg


# ---------------------------------------------------------------------------
# M17.2 原生音频方向
# ---------------------------------------------------------------------------


class TestAudioDirection:
    """按叙事节拍确定性生成官方 soundscape/music 字段。"""

    def test_known_beat_produces_both_fields(self):
        direction = build_audio_direction(["hook"])
        assert "overall_soundscape:" in direction
        assert "non_diegetic_music:" in direction

    def test_unknown_beats_return_empty(self):
        assert build_audio_direction(["", "not_a_beat"]) == ""
        assert build_audio_direction([]) == ""

    def test_dominant_beat_wins(self):
        """多节拍组按优先级取最强者：reversal 胜过 transition。"""
        direction = build_audio_direction(["transition", "reversal"])
        assert "abrupt hush" in direction  # reversal 的 soundscape 文案

    def test_all_six_beats_have_mappings(self):
        for beat in (
            "hook", "escalation", "reversal", "cliffhanger", "emotional_beat", "transition",
        ):
            assert build_audio_direction([beat]) != ""

    def test_append_audio_direction_at_prompt_end(self):
        prompt = _append_audio_direction("cinematic shot", ["hook"])
        assert prompt.startswith("cinematic shot\n")
        assert prompt.rstrip().endswith(build_audio_direction(["hook"]).splitlines()[-1])

    def test_append_audio_direction_respects_kill_switch(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_audio_direction_enabled", False)
        assert _append_audio_direction("cinematic shot", ["hook"]) == "cinematic shot"

    def test_multishot_prompt_appends_direction_after_description(self):
        """多镜 prompt：音频方向位于 integrated_multimodal_description 之后。"""
        prompt = build_multishot_prompt(
            [_req(1, prompt="alpha", beat="hook"), _req(2, prompt="beta")]
        )
        assert "overall_soundscape:" in prompt
        assert prompt.index("integrated_multimodal_description") < prompt.index(
            "overall_soundscape:"
        )

    async def test_single_fl2va_prompt_carries_audio_direction(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """单镜 fl2va：节拍非空时 prompt 尾部注入官方音频字段。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        resp = await agent.execute(_req(1, prompt="cinematic", beat="hook"))
        assert resp.success is True
        prompt = mock_call_comfyui.call_args[0][1]["20"]["inputs"]["prompt"]
        assert prompt.startswith("cinematic\n")
        assert "overall_soundscape:" in prompt
        assert "non_diegetic_music:" in prompt


# ---------------------------------------------------------------------------
# M17.3 FL2VA 首帧+末帧双锚定
# ---------------------------------------------------------------------------


class TestFL2VAAlignment:
    """官方对齐指令 + last_frame 接线（单镜与多镜组）。"""

    def test_alignment_instruction_single_shot_format(self):
        text = build_fl2va_alignment_instruction(3.0)
        assert "Picture 1 (from Shot 1) aligns with the 0.00-second mark" in text
        assert "Picture 2 (from Shot 1) aligns with the 3.00-second mark" in text

    def test_alignment_instruction_multishot_last_shot(self):
        """多镜组末帧属于组末 Shot N。"""
        text = build_fl2va_alignment_instruction(8.0, last_shot="Shot 2")
        assert "Picture 2 (from Shot 2) aligns with the 8.00-second mark" in text

    async def test_single_fl2va_dual_anchor_wiring(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """last_frame_url 非空 → 节点 11 LoadImage + last_frame 挂接 + 对齐指令前置。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        resp = await agent.execute(
            _req(1, prompt="cinematic", duration=3, last_frame="http://x/end.png")
        )
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["11"]["class_type"] == "LoadImage"
        assert workflow["20"]["inputs"]["last_frame"] == ["11", 0]
        prompt = workflow["20"]["inputs"]["prompt"]
        assert prompt.startswith("How the reference pictures align")
        assert "Picture 2 (from Shot 1) aligns with the 3.00-second mark" in prompt
        # 上传 2 次：首帧 + 末帧
        assert mock_upload_image.await_count == 2
        uploaded = [c.args[1] for c in mock_upload_image.call_args_list]
        assert uploaded == ["http://x/sb_1.png", "http://x/end.png"]

    async def test_single_fl2va_no_last_frame_keeps_i2v(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """last_frame_url 空串 → 退化为 I2VA 首帧单锚定（向后兼容）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        resp = await agent.execute(_req(1, prompt="cinematic"))
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert "11" not in workflow
        assert "last_frame" not in workflow["20"]["inputs"]
        assert workflow["20"]["inputs"]["prompt"] == "cinematic"
        assert mock_upload_image.await_count == 1

    async def test_multishot_group_chain_last_frame(
        self,
        agent,
        monkeypatch,
        tmp_path,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """组末场景携带链式末帧（组后一镜关键帧）→ 组级末帧锚定 + Shot N 对齐指令。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr("app.agents.video_agent._MULTISHOT_OUTPUT_DIR", tmp_path)
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "g.mp4", "subfolder": "", "type": "output"}]}
        }
        reqs = [
            _req(1, prompt="alpha"),
            _req(2, prompt="beta", last_frame="http://x/sb_3.png"),
        ]
        with (
            patch.object(agent, "_download_to_file", new_callable=AsyncMock) as m_dl,
            patch("app.agents.video_agent._run_ffmpeg", new_callable=AsyncMock),
        ):
            m_dl.return_value = tmp_path / "group.mp4"
            results = await agent.execute_multi_shot(reqs)

        assert all(r.success for r in results)
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["inputs"]["last_frame"] == ["11", 0]
        prompt = workflow["20"]["inputs"]["prompt"]
        assert "Picture 2 (from Shot 2) aligns with the 8.00-second mark" in prompt
        uploaded = [c.args[1] for c in mock_upload_image.call_args_list]
        assert uploaded == ["http://x/sb_1.png", "http://x/sb_3.png"]

    async def test_multishot_group_defaults_to_last_keyframe(
        self,
        agent,
        monkeypatch,
        tmp_path,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """无链式末帧 → 回退组末场景自身关键帧作组末帧（仍享双锚定）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr("app.agents.video_agent._MULTISHOT_OUTPUT_DIR", tmp_path)
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "g.mp4", "subfolder": "", "type": "output"}]}
        }
        with (
            patch.object(agent, "_download_to_file", new_callable=AsyncMock) as m_dl,
            patch("app.agents.video_agent._run_ffmpeg", new_callable=AsyncMock),
        ):
            m_dl.return_value = tmp_path / "group.mp4"
            results = await agent.execute_multi_shot(
                [_req(1, prompt="alpha"), _req(2, prompt="beta")]
            )

        assert all(r.success for r in results)
        uploaded = [c.args[1] for c in mock_upload_image.call_args_list]
        assert uploaded == ["http://x/sb_1.png", "http://x/sb_2.png"]


# ---------------------------------------------------------------------------
# M17.4 ref2va 全模态参考
# ---------------------------------------------------------------------------


class TestR2VMediaGuide:
    """官方 <Video N>/<Audio N> 标签引导（纯函数）。"""

    def test_empty_when_no_media(self):
        assert build_r2v_media_guide(0, 0) == ""

    def test_video_only_labels(self):
        guide = build_r2v_media_guide(1, 0)
        assert "<Video 1> is reference clip" in guide
        assert "camera movement" in guide

    def test_audio_only_labels(self):
        guide = build_r2v_media_guide(0, 1)
        assert "<Audio 1> is audio reference" in guide
        assert "background-music style" in guide

    def test_plural_labels(self):
        guide = build_r2v_media_guide(2, 2)
        assert "<Video 1>, <Video 2> are reference clips" in guide
        assert "<Audio 1>, <Audio 2> are audio references" in guide


class TestR2VMediaInjection:
    """音视频参考的触发扩展与工作流组挂接。"""

    @staticmethod
    def _video_outputs():
        return {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }

    async def test_r2v_triggered_by_reference_videos_only(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_upload_media,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """无角色参考图、仅参考视频 → 也走 ref2va（M17.4 触发条件扩展）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()
        resp = await agent.execute(
            _req(1, prompt="p", videos=["http://x/ref.mp4"])
        )
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"

    async def test_r2v_triggered_by_reference_audios_only(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_upload_media,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """仅独立参考音频 → 同样走 ref2va。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()
        resp = await agent.execute(
            _req(1, prompt="p", audios=["http://x/bgm.mp3"])
        )
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"

    async def test_media_nodes_wired(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_upload_media,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """1 视频 + 1 音频：LoadVideo→GetVideoComponents 挂 ref_videos/ref_video_audios，
        LoadAudio 挂 ref_audios，prompt 带 <Video 1>/<Audio 1> 标签引导。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()
        resp = await agent.execute(
            _req(
                1,
                prompt="p",
                videos=["http://x/ref.mp4"],
                audios=["http://x/bgm.mp3"],
            )
        )
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["70"]["class_type"] == "LoadVideo"
        assert workflow["70"]["inputs"]["file"] == "ref.mp4"
        assert workflow["80"]["class_type"] == "GetVideoComponents"
        assert workflow["80"]["inputs"]["video"] == ["70", 0]
        assert workflow["90"]["class_type"] == "LoadAudio"
        assert workflow["90"]["inputs"]["audio"] == "bgm.mp3"
        inputs = workflow["20"]["inputs"]
        assert inputs["ref_videos"]["ref_video_0"] == ["80", 0]
        assert inputs["ref_video_audios"]["ref_video_audio_0"] == ["80", 1]
        assert inputs["ref_audios"]["ref_audio_0"] == ["90", 0]
        assert "<Video 1>" in inputs["prompt"]
        assert "<Audio 1>" in inputs["prompt"]

    async def test_media_capped_at_max(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_upload_media,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """音视频参考各超 3 → 截断到节点组上限（h3_ref_max_videos/audios=3）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()
        resp = await agent.execute(
            _req(
                1,
                prompt="p",
                videos=[f"http://x/v{i}.mp4" for i in range(5)],
                audios=[f"http://x/a{i}.mp3" for i in range(5)],
            )
        )
        assert resp.success is True
        inputs = mock_call_comfyui.call_args[0][1]["20"]["inputs"]
        assert len(inputs["ref_videos"]) == 3
        assert len(inputs["ref_audios"]) == 3
        assert "ref_video_3" not in inputs["ref_videos"]
        assert "ref_audio_3" not in inputs["ref_audios"]

    async def test_upload_media_preserves_extension_and_endpoint(self, agent):
        """通用上传：保留源扩展名（LoadVideo/LoadAudio 按扩展名识别），统一走 /upload/image。"""
        get_resp = MagicMock()
        get_resp.content = b"video-bytes"
        get_resp.raise_for_status = MagicMock()
        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()
        post_resp.json = MagicMock(return_value={"name": "clip.mp4"})
        agent.http = MagicMock()
        agent.http.get = AsyncMock(return_value=get_resp)
        agent.http.post = AsyncMock(return_value=post_resp)

        name = await agent.upload_media_to_comfyui("http://w", "http://x/clip.mp4?token=1")

        assert name == "clip.mp4"
        assert agent.http.post.call_args[0][0] == "http://w/upload/image"
        files = agent.http.post.call_args.kwargs["files"]
        assert files["image"][0] == "clip.mp4"

    async def test_upload_media_fallback_name_when_no_extension(self, agent):
        """URL 无扩展名 → 使用调用方给的 fallback 名。"""
        get_resp = MagicMock()
        get_resp.content = b"audio-bytes"
        get_resp.raise_for_status = MagicMock()
        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()
        post_resp.json = MagicMock(return_value={})
        agent.http = MagicMock()
        agent.http.get = AsyncMock(return_value=get_resp)
        agent.http.post = AsyncMock(return_value=post_resp)

        name = await agent.upload_media_to_comfyui(
            "http://w", "http://x/download", fallback_name="ref_audio_0.mp3"
        )

        assert name == "ref_audio_0.mp3"
        files = agent.http.post.call_args.kwargs["files"]
        assert files["image"][0] == "ref_audio_0.mp3"
