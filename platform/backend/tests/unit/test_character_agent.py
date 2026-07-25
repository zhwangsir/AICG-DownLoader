"""角色 Agent 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.character_agent import CharacterAgent
from app.config import settings
from app.models.schemas import CharacterRequest


@pytest.fixture
def agent():
    return CharacterAgent()


class TestCharacterAgentExecute:
    async def test_success(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front view",
                "side_view_prompt": "side view",
                "closeup_prompt": "close up",
                "negative_prompt": "blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {
                "images": [
                    {
                        "filename": "char_001_front.png",
                        "subfolder": "",
                        "type": "output",
                    }
                ]
            }
        }

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["character_id"] == "char_001"
        assert "front" in response.data["reference_images"]
        assert "side" in response.data["reference_images"]
        assert "closeup" in response.data["reference_images"]

    async def test_json_repair_fallback(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_call_llm.return_value = '{"front_view_prompt":"f","side_view_prompt":"s","closeup_prompt":"c","negative_prompt":"n"}'
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is True

    async def test_exception_returns_error(self, agent, sample_character, mock_call_llm):
        mock_call_llm.side_effect = RuntimeError("失败")

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is False
        assert "失败" in response.error

    async def test_no_images_raises(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )
        mock_get_comfyui_result.return_value = {"7": {}}

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is False


# ============================================================================
# P4.3: HunyuanImage / FLUX+PuLID 双后端派发测试
# ============================================================================


class TestCharacterAgentDualBackend:
    """P4.3: HunyuanImage / FLUX+PuLID 主后端 + SDXL 回退测试。

    本测试类通过 monkeypatch 局部覆盖 settings.image_backend = 'hunyuanimage'/'flux_pulid'。
    """

    def _attach_mock_hunyuanimage(self, agent, return_value=None, side_effect=None):
        """注入 mock HunyuanImageService 到 agent._hunyuanimage，绕过懒加载。"""
        mock_svc = MagicMock()
        mock_svc.generate_one = AsyncMock(
            return_value=return_value or b"hunyuanimage-png",
            side_effect=side_effect,
        )
        agent._hunyuanimage = mock_svc
        return mock_svc

    def _attach_mock_flux_pulid(self, agent, return_value=None, side_effect=None):
        """注入 mock FluxPuLIDService 到 agent._flux_pulid，绕过懒加载。"""
        mock_svc = MagicMock()
        mock_svc.generate_one = AsyncMock(
            return_value=return_value or b"flux-png",
            side_effect=side_effect,
        )
        agent._flux_pulid = mock_svc
        return mock_svc

    async def test_hunyuanimage_backend_success(
        self, agent, sample_character, mock_call_llm, monkeypatch, tmp_path
    ):
        """image_backend='hunyuanimage' → 三视图全部走 HunyuanImage 主路径。"""
        monkeypatch.setattr(settings, "image_backend", "hunyuanimage")
        mock_svc = self._attach_mock_hunyuanimage(agent, return_value=b"hunyuan-img")
        # 重定向 OUTPUT_DIR 到临时目录，避免污染真实输出目录
        monkeypatch.setattr(
            "app.agents.character_agent.OUTPUT_DIR", tmp_path, raising=True
        )

        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["character_id"] == "char_001"
        # 三视图全部生成成功
        assert len(response.data["reference_images"]) == 3
        for view in ("front", "side", "closeup"):
            url = response.data["reference_images"][view]
            assert "/static/character/" in url
        # HunyuanImage 被调用 3 次（三视图并行）
        assert mock_svc.generate_one.await_count == 3

    async def test_flux_pulid_backend_success(
        self, agent, sample_character, mock_call_llm, monkeypatch, tmp_path
    ):
        """image_backend='flux_pulid' → 三视图全部走 FLUX+PuLID 主路径。"""
        monkeypatch.setattr(settings, "image_backend", "flux_pulid")
        mock_svc = self._attach_mock_flux_pulid(agent, return_value=b"flux-img")
        monkeypatch.setattr(
            "app.agents.character_agent.OUTPUT_DIR", tmp_path, raising=True
        )

        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is True
        assert len(response.data["reference_images"]) == 3
        for view in ("front", "side", "closeup"):
            assert "/static/character/" in response.data["reference_images"][view]
        assert mock_svc.generate_one.await_count == 3

    async def test_hunyuanimage_failure_fallback_to_sdxl(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
        tmp_path,
    ):
        """HunyuanImage 抛异常 → 自动回退到 ComfyUI SDXL 路径。"""
        monkeypatch.setattr(settings, "image_backend", "hunyuanimage")
        # HunyuanImage 全部失败
        self._attach_mock_hunyuanimage(
            agent, side_effect=RuntimeError("HunyuanImage OOM")
        )
        monkeypatch.setattr(
            "app.agents.character_agent.OUTPUT_DIR", tmp_path, raising=True
        )
        # SDXL 路径返回成功
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "fallback.png", "subfolder": "", "type": "output"}]}
        }

        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is True
        # 三视图全部走 SDXL 回退
        for view in ("front", "side", "closeup"):
            url = response.data["reference_images"][view]
            assert "/view?" in url  # ComfyUI view URL

    async def test_sdxl_backend_skips_service(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui,
        mock_get_comfyui_result, monkeypatch
    ):
        """image_backend='sdxl'（conftest 默认）→ 不调用 HunyuanImage/FLUX+PuLID。"""
        mock_h = self._attach_mock_hunyuanimage(agent)
        mock_f = self._attach_mock_flux_pulid(agent)
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "f",
                "side_view_prompt": "s",
                "closeup_prompt": "c",
                "negative_prompt": "n",
            }
        )

        request = CharacterRequest(character=sample_character)
        response = await agent.execute(request)

        assert response.success is True
        # SDXL 路径不调用图像服务
        assert mock_h.generate_one.await_count == 0
        assert mock_f.generate_one.await_count == 0
