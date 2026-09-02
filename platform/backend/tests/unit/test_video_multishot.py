"""M11: MiniMax H3 多镜叙事联合生成单元测试。

覆盖：
- 场景分组纯函数 group_scenes_for_multishot（同集相邻成组/跨集不并/超长拆组/超数拆组/边界）
- 多镜 prompt 构建 build_multishot_prompt（SHOT 编号/顺序/总览前缀/空 prompt 兜底）
- 切分边界计算 _multishot_split_plan（累计帧偏移/末场吃到组尾）
- VideoAgent.execute_multi_shot（fl2va/r2v 工作流、ffmpeg 切分参数、失败整组回退）

conftest._patch_settings 默认 video_backend='comfyui'，H3 用例局部 monkeypatch 为 'h3'。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


from app.agents.video_agent import (
    H3_MULTISHOT_PROMPT_GUIDE,
    VideoAgent,
    _multishot_split_plan,
    _snap_h3_frames,
    build_multishot_prompt,
    group_scenes_for_multishot,
)
from app.config import settings
from app.models.schemas import AgentResponse, VideoRequest

@pytest.fixture(autouse=True)
def _sfw_pin_off(monkeypatch):
    monkeypatch.setattr(
        "app.services.settings_service.settings_service.nsfw_status",
        lambda: {"nsfw_enabled": False, "has_pin": False},
    )



def _req(
    scene_id: int,
    episode: int = 1,
    duration: int = 4,
    prompt: str = "p",
    refs: list[str] | None = None,
) -> VideoRequest:
    return VideoRequest(
        scene_id=scene_id,
        episode=episode,
        image_url=f"http://x/sb_{scene_id}.png",
        prompt=prompt,
        duration_seconds=duration,
        reference_images=refs or [],
    )


class TestGroupScenesForMultishot:
    """分组纯函数：同集相邻场景贪心合并，受场景数/总时长双上限约束。"""

    def test_adjacent_same_episode_grouped(self):
        """相邻同集 3 场景（12s ≤ 14s）→ 合并为一组。"""
        groups = group_scenes_for_multishot([_req(1), _req(2), _req(3)], 3, 14.0)
        assert [[r.scene_id for r in g] for g in groups] == [[1, 2, 3]]

    def test_cross_episode_not_merged(self):
        """跨集场景不得并入同一组。"""
        groups = group_scenes_for_multishot(
            [_req(1, episode=1), _req(2, episode=2)], 3, 14.0
        )
        assert [[r.scene_id for r in g] for g in groups] == [[1], [2]]

    def test_episode_boundary_resets_group(self):
        """集边界切开后，新集内相邻场景仍可成组。"""
        reqs = [
            _req(1, episode=1),
            _req(2, episode=1),
            _req(3, episode=2),
            _req(4, episode=2),
        ]
        groups = group_scenes_for_multishot(reqs, 3, 14.0)
        assert [[r.scene_id for r in g] for g in groups] == [[1, 2], [3, 4]]

    def test_split_when_over_max_seconds(self):
        """组内总时长超上限 → 拆组（4×4s：前 3 个 12s，第 4 个并入将 16s 超限）。"""
        reqs = [_req(i, duration=4) for i in range(1, 5)]
        groups = group_scenes_for_multishot(reqs, 10, 14.0)
        assert [[r.scene_id for r in g] for g in groups] == [[1, 2, 3], [4]]

    def test_split_when_over_max_scenes(self):
        """组内场景数超上限 → 拆组（4×3s=12s 时长足够但数量超 3）。"""
        reqs = [_req(i, duration=3) for i in range(1, 5)]
        groups = group_scenes_for_multishot(reqs, 3, 14.0)
        assert [[r.scene_id for r in g] for g in groups] == [[1, 2, 3], [4]]

    def test_single_scene_not_grouped(self):
        """单场景返回单元素组（调用方按 len<2 走原逐场景路径）。"""
        groups = group_scenes_for_multishot([_req(1)], 3, 14.0)
        assert [[r.scene_id for r in g] for g in groups] == [[1]]

    def test_exactly_max_seconds_in_one_group(self):
        """边界恰好 14s（2×7s）→ 允许同组（≤ 上限）。"""
        groups = group_scenes_for_multishot(
            [_req(1, duration=7), _req(2, duration=7)], 3, 14.0
        )
        assert [[r.scene_id for r in g] for g in groups] == [[1, 2]]

    def test_over_max_seconds_splits(self):
        """2×7s 已占满 14s，第三场 1s 并入将超限 → 拆出。"""
        groups = group_scenes_for_multishot(
            [_req(1, duration=7), _req(2, duration=7), _req(3, duration=1)], 3, 14.0
        )
        assert [[r.scene_id for r in g] for g in groups] == [[1, 2], [3]]


class TestBuildMultishotPrompt:
    """多镜 prompt：总览前缀 + 镜号顺序组装（M17.1 默认原生 CUT 语法）。"""

    def test_shot_numbering_and_order(self):
        prompt = build_multishot_prompt([_req(1, prompt="alpha"), _req(2, prompt="beta")])
        assert "[Shot 1] alpha" in prompt
        assert "[Shot 2] At 00:04.000, the camera cuts to beta" in prompt
        assert prompt.index("[Shot 1]") < prompt.index("[Shot 2]")

    def test_legacy_shot_format_when_native_cut_disabled(self):
        """保险丝：native_cut=False 回退 M11 旧版 SHOT X: 格式。"""
        prompt = build_multishot_prompt(
            [_req(1, prompt="alpha"), _req(2, prompt="beta")], native_cut=False
        )
        assert "SHOT 1: alpha" in prompt
        assert "SHOT 2: beta" in prompt

    def test_guide_prefix_present(self):
        """前缀带跨镜连续性总览要求（参考 r2v H3_R2V_PROMPT_GUIDE 模式）。"""
        prompt = build_multishot_prompt([_req(1), _req(2)])
        assert prompt.startswith(H3_MULTISHOT_PROMPT_GUIDE)

    def test_empty_prompt_fallback(self):
        """场景 prompt 为空/空白 → 使用默认电影感兜底文案。"""
        prompt = build_multishot_prompt([_req(1, prompt=""), _req(2, prompt="  ")])
        assert "[Shot 1] cinematic, high quality, smooth motion" in prompt


class TestBuildMultishotPromptBeats:
    """M12.1：多镜 SHOT prompt 注入叙事节拍视觉指令（英文，与场景 prompt 同语言）。"""

    def test_hook_beat_injects_visual_hint(self):
        """narrative_beat=hook 的镜段追加高对比/张力视觉指令。"""
        req = _req(1, prompt="alpha")
        req.narrative_beat = "hook"
        prompt = build_multishot_prompt([req, _req(2, prompt="beta")])
        shot1_seg = prompt.split("[Shot 2]")[0]
        assert "high-contrast" in shot1_seg

    def test_distinct_beats_get_distinct_hints(self):
        """不同节拍注入不同视觉指令（hook ≠ cliffhanger）。"""
        r1, r2 = _req(1, prompt="alpha"), _req(2, prompt="beta")
        r1.narrative_beat, r2.narrative_beat = "hook", "cliffhanger"
        prompt = build_multishot_prompt([r1, r2])
        seg1, seg2 = prompt.split("[Shot 2]", 1)
        # 去掉镜号与 prompt 公共部分后，节拍视觉指令必须不同
        suffix1 = seg1.split("alpha", 1)[-1]
        suffix2 = seg2.split("beta", 1)[-1]
        assert suffix1 and suffix2 and suffix1 != suffix2

    def test_empty_or_unknown_beat_no_hint(self):
        """空/非法节拍不注入指令，镜段保持纯 prompt（回归 M11 行为）。"""
        r1 = _req(1, prompt="alpha")
        r1.narrative_beat = "not_a_beat"
        prompt = build_multishot_prompt([r1, _req(2, prompt="beta")])
        assert "[Shot 1] alpha [Shot 2]" in prompt

    def test_default_narrative_beat_empty(self):
        """VideoRequest.narrative_beat 默认空串（向后兼容既有构造调用）。"""
        assert VideoRequest(scene_id=1, image_url="http://x/i.png").narrative_beat == ""


class TestMultishotSplitPlan:
    """切分边界：按各场景时长累计帧偏移，最后一场吃到组尾。"""

    def test_cumulative_frame_offsets(self):
        """[4s,4s] 共 192 帧 → (0,96),(96,192)。"""
        assert _multishot_split_plan([4, 4], 192) == [(0, 96), (96, 192)]

    def test_last_scene_eats_tail(self):
        """[4,4,4] 网格吸附到 294 帧 → 末段多吃 6 帧余量（102 帧）。"""
        plan = _multishot_split_plan([4, 4, 4], 294)
        assert plan == [(0, 96), (96, 192), (192, 294)]

    def test_single_scene_takes_whole(self):
        assert _multishot_split_plan([5], 124) == [(0, 124)]


@pytest.fixture
def agent():
    return VideoAgent()


class TestExecuteMultiShot:
    """execute_multi_shot：一次 H3 工作流 → 下载组视频 → ffmpeg 逐场景切分。"""

    @staticmethod
    def _video_outputs():
        return {
            "60": {
                "videos": [
                    {
                        "filename": "video_multishot_1_2_00001.mp4",
                        "subfolder": "",
                        "type": "output",
                    }
                ]
            }
        }

    @staticmethod
    def _ok(scene_id: int) -> AgentResponse:
        return AgentResponse(
            success=True,
            data={
                "scene_id": scene_id,
                "video_url": f"http://x/v{scene_id}.mp4",
                "duration_seconds": 4,
            },
            elapsed_seconds=0.01,
        )

    async def test_fl2va_group_workflow_and_split(
        self,
        agent,
        monkeypatch,
        tmp_path,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """无参考图 → fl2va 多镜：prompt 含多 SHOT、length=组总帧数、逐场景 ffmpeg 切分。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr("app.agents.video_agent._MULTISHOT_OUTPUT_DIR", tmp_path)
        mock_get_comfyui_result.return_value = self._video_outputs()

        with (
            patch.object(agent, "_download_to_file", new_callable=AsyncMock) as m_dl,
            patch("app.agents.video_agent._run_ffmpeg", new_callable=AsyncMock) as m_ff,
        ):
            m_dl.return_value = tmp_path / "group.mp4"
            results = await agent.execute_multi_shot(
                [_req(1, prompt="alpha"), _req(2, prompt="beta")]
            )

        assert len(results) == 2
        assert all(r.success for r in results)
        assert [r.data["scene_id"] for r in results] == [1, 2]
        for r in results:
            assert (
                f"/static/video/video_scene_{r.data['scene_id']}.mp4"
                in r.data["video_url"]
            )
            assert r.data["duration_seconds"] == 4

        # 必须直连 H3 专用实例（conftest 占位 http://localhost:9005）
        call_url, workflow = mock_call_comfyui.call_args[0]
        assert call_url == "http://localhost:9005"

        # fl2va 节点 + 多镜 prompt（M17.1 原生 CUT）+ 组总帧数（8s → 192）
        node20 = workflow["20"]
        assert node20["class_type"] == "MiniMaxH3ImageToVideo"
        assert "[Shot 1] alpha" in node20["inputs"]["prompt"]
        assert "the camera cuts to beta" in node20["inputs"]["prompt"]
        assert node20["inputs"]["length"] == _snap_h3_frames(8) == 192
        assert workflow["1"]["inputs"]["unet_name"] == settings.h3_unet_name
        assert workflow["60"]["inputs"]["filename_prefix"] == "video_multishot_1_2"

        # M17.3 组末帧双锚定：无链式末帧时回退末场景自身关键帧，上传共 2 次
        assert workflow["11"]["class_type"] == "LoadImage"
        assert node20["inputs"]["last_frame"] == ["11", 0]
        assert node20["inputs"]["prompt"].startswith("How the reference pictures align")
        assert (
            "Picture 2 (from Shot 2) aligns with the 8.00-second mark"
            in node20["inputs"]["prompt"]
        )
        assert mock_upload_image.await_count == 2

        # 每场景一次 ffmpeg 重编码切分：帧边界 (0,96),(96,192) → 0s/4s 起各 4s
        assert m_ff.await_count == 2
        cmd1, cmd2 = (c.args[0] for c in m_ff.call_args_list)
        assert cmd1[cmd1.index("-ss") + 1] == "0.000"
        assert cmd1[cmd1.index("-t") + 1] == "4.000"
        assert cmd2[cmd2.index("-ss") + 1] == "4.000"
        assert cmd2[cmd2.index("-t") + 1] == "4.000"
        # 重编码参数：libx264 + aac（保证帧精度）
        assert "libx264" in cmd1
        assert "aac" in cmd1
        # 切分产物落到本地静态视频目录
        assert str(tmp_path / "video_scene_1.mp4") in cmd1
        assert str(tmp_path / "video_scene_2.mp4") in cmd2

    async def test_r2v_group_merges_reference_images(
        self,
        agent,
        monkeypatch,
        tmp_path,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """组内有参考图 → r2v 多镜：跨场景参考图合并去重（保序），首场景关键帧占 ref_image_0。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr("app.agents.video_agent._MULTISHOT_OUTPUT_DIR", tmp_path)
        mock_get_comfyui_result.return_value = self._video_outputs()

        reqs = [
            _req(1, prompt="alpha", refs=["http://x/a.png", "http://x/b.png"]),
            _req(2, prompt="beta", refs=["http://x/b.png", "http://x/c.png"]),
        ]
        with (
            patch.object(agent, "_download_to_file", new_callable=AsyncMock) as m_dl,
            patch("app.agents.video_agent._run_ffmpeg", new_callable=AsyncMock),
        ):
            m_dl.return_value = tmp_path / "group.mp4"
            results = await agent.execute_multi_shot(reqs)

        assert all(r.success for r in results)
        workflow = mock_call_comfyui.call_args[0][1]
        node20 = workflow["20"]
        assert node20["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert workflow["1"]["inputs"]["unet_name"] == settings.h3_ref_unet_name
        # prompt 含多镜原生 CUT 与 r2v 参考图用途引导
        assert "[Shot 1] alpha" in node20["inputs"]["prompt"]
        assert "the camera cuts to beta" in node20["inputs"]["prompt"]
        assert "reference" in node20["inputs"]["prompt"].lower()

        # 合并去重：关键帧 + a/b/c 三张唯一参考图 → 上传 4 次，ref_image_0..3
        assert mock_upload_image.await_count == 4
        uploaded = [c.args[1] for c in mock_upload_image.call_args_list]
        assert uploaded == [
            "http://x/sb_1.png",
            "http://x/a.png",
            "http://x/b.png",
            "http://x/c.png",
        ]
        assert node20["inputs"]["ref_images"]["ref_image_0"] == ["10", 0]
        assert node20["inputs"]["ref_images"]["ref_image_3"] == ["13", 0]

    async def test_last_scene_eats_tail_in_split(
        self,
        agent,
        monkeypatch,
        tmp_path,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """三场景组（12s → 294 帧）：末段吃到组尾，-t 4.250（102 帧）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr("app.agents.video_agent._MULTISHOT_OUTPUT_DIR", tmp_path)
        mock_get_comfyui_result.return_value = self._video_outputs()

        with (
            patch.object(agent, "_download_to_file", new_callable=AsyncMock) as m_dl,
            patch("app.agents.video_agent._run_ffmpeg", new_callable=AsyncMock) as m_ff,
        ):
            m_dl.return_value = tmp_path / "group.mp4"
            results = await agent.execute_multi_shot([_req(1), _req(2), _req(3)])

        assert [r.data["scene_id"] for r in results] == [1, 2, 3]
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["inputs"]["length"] == _snap_h3_frames(12) == 294

        assert m_ff.await_count == 3
        cmd3 = m_ff.call_args_list[2].args[0]
        assert cmd3[cmd3.index("-ss") + 1] == "8.000"
        assert cmd3[cmd3.index("-t") + 1] == "4.250"
        # 末段 102 帧 → duration 取整 4s（与单镜路径 num_frames//24 约定一致）
        assert results[2].data["duration_seconds"] == 102 // 24

    async def test_group_failure_falls_back_to_per_scene_execute(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
    ):
        """组推理失败 → 整组回退为逐场景调用 execute（各自走 h3 单镜+comfyui 回退）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_call_comfyui.side_effect = RuntimeError("h3 OOM")

        with patch.object(agent, "execute", new_callable=AsyncMock) as m_exec:
            m_exec.side_effect = lambda req, **kw: self._ok(req.scene_id)
            results = await agent.execute_multi_shot([_req(1), _req(2)])

        assert len(results) == 2
        assert [r.data["scene_id"] for r in results] == [1, 2]
        assert all(r.success for r in results)
        assert m_exec.await_count == 2

    async def test_ffmpeg_failure_falls_back_to_per_scene_execute(
        self,
        agent,
        monkeypatch,
        tmp_path,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """ffmpeg 切分失败 → 同样整组回退逐场景 execute。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr("app.agents.video_agent._MULTISHOT_OUTPUT_DIR", tmp_path)
        mock_get_comfyui_result.return_value = self._video_outputs()

        with (
            patch.object(agent, "_download_to_file", new_callable=AsyncMock) as m_dl,
            patch("app.agents.video_agent._run_ffmpeg", new_callable=AsyncMock) as m_ff,
            patch.object(agent, "execute", new_callable=AsyncMock) as m_exec,
        ):
            m_dl.return_value = tmp_path / "group.mp4"
            m_ff.side_effect = RuntimeError("ffmpeg boom")
            m_exec.side_effect = lambda req, **kw: self._ok(req.scene_id)
            results = await agent.execute_multi_shot([_req(1), _req(2)])

        assert all(r.success for r in results)
        assert [r.data["scene_id"] for r in results] == [1, 2]
        assert m_exec.await_count == 2

    async def test_single_request_goes_individual(
        self,
        agent,
        monkeypatch,
        mock_call_comfyui,
    ):
        """单场景不成组：直接走逐场景 execute，不提交多镜工作流。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        with patch.object(agent, "execute", new_callable=AsyncMock) as m_exec:
            m_exec.return_value = self._ok(1)
            results = await agent.execute_multi_shot([_req(1)])

        assert len(results) == 1
        assert results[0].data["scene_id"] == 1
        assert m_exec.await_count == 1
        mock_call_comfyui.assert_not_awaited()

    async def test_non_h3_backend_goes_individual(
        self,
        agent,
        mock_call_comfyui,
    ):
        """非 h3 后端（conftest 默认 comfyui）→ 逐场景 execute，不多镜。"""
        with patch.object(agent, "execute", new_callable=AsyncMock) as m_exec:
            m_exec.side_effect = lambda req, **kw: self._ok(req.scene_id)
            results = await agent.execute_multi_shot([_req(1), _req(2)])

        assert len(results) == 2
        assert m_exec.await_count == 2
        mock_call_comfyui.assert_not_awaited()
