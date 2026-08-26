"""视觉质检 Agent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.quality_agent import VisualQualityAgent
from app.models.schemas import QualityVisualRequest


@pytest.fixture
def visual_agent():
    agent = VisualQualityAgent()
    agent.llm_client = MagicMock()
    # VisualQualityAgent 使用独立的 _vlm_client, 而非继承自 BaseAgent 的 llm_client
    # 预设 MagicMock 避免 _get_vlm_client 触发真实 AsyncOpenAI 创建
    agent._vlm_client = MagicMock()
    return agent


class TestVisualQualityAgent:
    async def test_fallback_when_model_not_configured(self, visual_agent):
        with patch("app.agents.quality_agent.settings.visual_model_url", ""):
            request = QualityVisualRequest(
                project_id="p1",
                title="测试视频",
                scene_id=1,
                video_url="http://x/v.mp4",
            )
            response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 0
        assert "未部署" in response.data["summary"]
        assert response.data["issues"][0]["category"] == "system"

    async def test_success_with_frames(self, visual_agent):
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = (
            '{"score": 88, "summary": "画面连贯", "issues": []}'
        )
        fake_resp.choices[0].message.reasoning_content = None

        visual_agent._vlm_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            max_frames=2,
        )

        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with patch.object(visual_agent, "_download_video", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = MagicMock()
                with patch.object(
                    visual_agent, "_extract_frames", new_callable=AsyncMock
                ) as mock_extract:
                    frame_path = MagicMock()
                    frame_path.read_bytes.return_value = b"fake_image"
                    mock_extract.return_value = [(1.0, frame_path), (2.0, frame_path)]
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 88
        assert response.data["summary"] == "画面连贯"

    async def test_json_decode_error(self, visual_agent):
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = "not json"
        fake_resp.choices[0].message.reasoning_content = None

        visual_agent._vlm_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
        )

        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with patch.object(visual_agent, "_download_video", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = MagicMock()
                with patch.object(
                    visual_agent, "_extract_frames", new_callable=AsyncMock
                ) as mock_extract:
                    frame_path = MagicMock()
                    frame_path.read_bytes.return_value = b"fake_image"
                    mock_extract.return_value = [(1.0, frame_path)]
                    response = await visual_agent.execute(request)

        assert response.success is False
        assert "JSON 解析失败" in response.error

    async def test_probe_duration(self, visual_agent):
        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(return_value=(b"12.5\n", b""))
        proc_mock.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc_mock
        ):
            duration = await visual_agent._probe_duration(MagicMock())

        assert duration == 12.5


def _vlm_response(payload: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = payload
    resp.choices[0].message.reasoning_content = None
    return resp


def _patch_video_and_frames(visual_agent):
    """Mock 视频下载 + 抽帧，返回帧路径 mock（read_bytes → b'frame'）。"""
    frame_path = MagicMock()
    frame_path.read_bytes.return_value = b"frame"
    dl = patch.object(visual_agent, "_download_video", new_callable=AsyncMock)
    ex = patch.object(visual_agent, "_extract_frames", new_callable=AsyncMock)
    return dl, ex, frame_path


class TestVisualDriftDetection:
    """M13 角色一致性对照：独立漂移检测调用（与主画质检查分离）。"""

    async def test_drift_detected_via_independent_call(self, visual_agent):
        """检出漂移：追加 critical issue + 修订 summary；主检查 content 不含参考图。"""
        main_resp = _vlm_response('{"score": 85, "summary": "画质OK", "issues": []}')
        drift_resp = _vlm_response(
            '{"drift_detected": true, "details": "古装女子与卡通男孩明显不同"}'
        )
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = MagicMock()
        ref_path.read_bytes.return_value = b"ref"
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref1.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is True
        critical = [i for i in response.data["issues"] if i["severity"] == "critical"]
        assert len(critical) == 1
        assert critical[0]["category"] == "visual_consistency"
        assert "角色漂移" in critical[0]["message"]
        assert "古装女子" in critical[0]["message"]
        assert "漂移" in response.data["summary"]
        # 两次 VLM 调用：主检查 text+1帧；漂移检查 text+1参考图+1帧
        create = visual_agent._vlm_client.chat.completions.create
        assert create.call_count == 2
        main_content = create.call_args_list[0].kwargs["messages"][0]["content"]
        assert len(main_content) == 2
        assert main_content[0]["type"] == "text"
        assert main_content[1]["type"] == "image_url"
        drift_content = create.call_args_list[1].kwargs["messages"][0]["content"]
        assert len(drift_content) == 3
        assert "三视图" in drift_content[0]["text"]
        import base64

        ref_b64 = base64.b64encode(b"ref").decode()
        frame_b64 = base64.b64encode(b"frame").decode()
        assert ref_b64 in drift_content[1]["image_url"]["url"]
        assert frame_b64 in drift_content[2]["image_url"]["url"]

    async def test_no_drift_when_vlm_says_no(self, visual_agent):
        """漂移调用返回 false：不追加 critical，summary 保持主检查结果。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        drift_resp = _vlm_response('{"drift_detected": false, "details": "无"}')
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = MagicMock()
        ref_path.read_bytes.return_value = b"ref"
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref1.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is False
        assert all(i["severity"] != "critical" for i in response.data["issues"])
        assert response.data["summary"] == "ok"

    async def test_no_reference_images_skips_drift_call(self, visual_agent):
        """无参考图：只做主画质检查（1 次 VLM 调用），drift_detected 恒 False。"""
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response('{"score": 90, "summary": "ok", "issues": []}')
        )
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is False
        assert visual_agent._vlm_client.chat.completions.create.call_count == 1

    async def test_reference_download_failure_skips_drift_call(self, visual_agent):
        """参考图下载全失败（ref_paths 空）：跳过漂移检测，主检查不受影响。"""
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response('{"score": 80, "summary": "ok", "issues": []}')
        )
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/broken.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=None,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is False
        assert visual_agent._vlm_client.chat.completions.create.call_count == 1

    async def test_drift_check_failure_falls_back(self, visual_agent):
        """漂移调用异常：兜底 (False, "")，主画质结果正常返回。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, RuntimeError("vlm boom")]
        )
        ref_path = MagicMock()
        ref_path.read_bytes.return_value = b"ref"
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref1.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 85
        assert response.data["drift_detected"] is False


class TestDriftCharacterAbsentExemption:
    """M16.3: 漂移判定边缘 case 豁免 — 参考角色未出镜场景不误报。

    背景：core E2E（pipeline-7470e3e104d9）scene 1 为 POV 手持信封特写，
    主角林浅未出镜，VLM 将背景路人（棕发女生）与参考图比对误判漂移。
    新增 character_present 结构化字段：VLM 判定参考角色未出镜时，
    无论 drift_detected 为何均豁免（程序兜底，不依赖 VLM 自觉遵守规则）。
    """

    async def test_character_absent_overrides_drift_detected(self, visual_agent):
        """VLM 报 drift_detected=true 但 character_present=false → 豁免，不报漂移。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        drift_resp = _vlm_response(
            '{"drift_detected": true, "character_present": false, '
            '"details": "POV 手持信封特写，仅背景路人，参考角色未出镜"}'
        )
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = MagicMock()
        ref_path.read_bytes.return_value = b"ref"
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref1.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is False
        assert all(i["severity"] != "critical" for i in response.data["issues"])
        assert "漂移" not in response.data["summary"]

    async def test_character_present_true_keeps_drift(self, visual_agent):
        """character_present=true + drift_detected=true → 正常报漂移（不豁免）。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        drift_resp = _vlm_response(
            '{"drift_detected": true, "character_present": true, '
            '"details": "帧中角色为棕发，参考图为黑发"}'
        )
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = MagicMock()
        ref_path.read_bytes.return_value = b"ref"
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref1.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is True
        critical = [i for i in response.data["issues"] if i["severity"] == "critical"]
        assert len(critical) == 1
        assert "棕发" in critical[0]["message"]

    async def test_missing_character_present_field_backward_compatible(self, visual_agent):
        """旧版响应无 character_present 字段 → 行为与 M13 一致（drift_detected 生效）。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        drift_resp = _vlm_response(
            '{"drift_detected": true, "details": "古装女子与卡通男孩明显不同"}'
        )
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = MagicMock()
        ref_path.read_bytes.return_value = b"ref"
        dl, ex, frame_path = _patch_video_and_frames(visual_agent)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref1.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl as mock_dl, ex as mock_extract:
                mock_dl.return_value = MagicMock()
                mock_extract.return_value = [(1.0, frame_path)]
                with patch.object(
                    visual_agent, "_download_reference_image", new_callable=AsyncMock,
                    return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is True

    def test_drift_prompt_contains_absent_rule(self):
        """DRIFT_CHECK_PROMPT 含未出镜豁免规则与 character_present 字段说明。"""
        from app.agents.quality_agent import DRIFT_CHECK_PROMPT

        assert "未出镜" in DRIFT_CHECK_PROMPT
        assert "character_present" in DRIFT_CHECK_PROMPT
        assert "背景路人" in DRIFT_CHECK_PROMPT


class TestDownloadReferenceImage:
    """M13 _download_reference_image：本地静态资源复用 + 失败容错。"""

    async def test_local_static_reuse(self, visual_agent, tmp_path, monkeypatch):
        """localhost /static/character/xxx.png 直接映射 output/character/ 本地文件，免下载。"""
        import app.agents.quality_agent as qa

        # quality_agent.py 位于 app/agents/ → parent.parent.parent 是 backend 根；
        # 将模块 __file__ 指到 tmp_path/app/agents/ 下，使 output 根解析到 tmp_path/output
        fake_file = tmp_path / "app" / "agents" / "quality_agent.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()
        monkeypatch.setattr(qa, "__file__", str(fake_file))

        char_dir = tmp_path / "output" / "character"
        char_dir.mkdir(parents=True)
        target = char_dir / "char_001_front.png"
        target.write_bytes(b"img")

        result = await visual_agent._download_reference_image(
            "http://localhost:8000/static/character/char_001_front.png"
        )
        assert result == target

    async def test_download_failure_returns_none(self, visual_agent):
        """远程下载异常 → 返回 None（调用方跳过）。"""
        failing_http = MagicMock()
        failing_http.stream = MagicMock(side_effect=RuntimeError("conn refused"))
        visual_agent.http = failing_http

        result = await visual_agent._download_reference_image("http://remote/ref.png")
        assert result is None

    async def test_empty_url_returns_none(self, visual_agent):
        assert await visual_agent._download_reference_image("") is None
        assert await visual_agent._download_reference_image("   ") is None
