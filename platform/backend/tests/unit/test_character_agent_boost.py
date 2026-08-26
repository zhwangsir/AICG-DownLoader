"""角色 Agent 覆盖率补全测试。

针对既有 test_character_agent.py 未覆盖的分支：
- _fallback_view_prompt 性别前缀 / 画质尾（98-104）
- execute 的预览确认 / 自定义提示词模式（204-218）与搜索参考日志（225）
- preview 预览全流程：成功 / 搜索失败 / LLM 失败降级 / 整体超时（318-373）
- _generate_prompts：参考资料注入（393）、json_repair 修复（407-408）、
  非字典容错（412）、正面提示词兜底（417）
- _rag_optimize_prompts：anchor 缺省解析（452）、空提示词跳过（464）
- _generate_image_via_sdxl：无 prompt_id 报错（517）
- _get_vlm_client 懒加载（540）
- side/closeup 质检重试耗尽抛错（593）
- 质检 fail-open：visual_model_url 为空直返（621, 673）
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.character_agent import (
    DEFAULT_NEGATIVE_PROMPT,
    CharacterAgent,
    _fallback_view_prompt,
)
from app.config import settings
from app.models.schemas import Character, CharacterPreviewRequest, CharacterRequest
from app.services.style_anchor import resolve_style_anchor


@pytest.fixture
def agent():
    return CharacterAgent()


def _valid_prompts_json() -> str:
    return json.dumps(
        {
            "front_view_prompt": "front",
            "side_view_prompt": "side",
            "closeup_prompt": "closeup",
            "negative_prompt": "blurry",
        }
    )


def _images_output() -> dict:
    return {"7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}}


# ---------------------------------------------------------------------------
# _fallback_view_prompt
# ---------------------------------------------------------------------------
class TestFallbackViewPrompt:
    def test_male_character_uses_1boy_with_realism_tail(self, sample_character):
        """描述无「女」/girl → 1boy 前缀；写实锚定追加 photorealistic 画质尾。"""
        anchor = resolve_style_anchor("写实电影感")
        prompt = _fallback_view_prompt(sample_character, anchor)
        assert prompt.startswith("1boy, solo, single person, only one person")
        assert "photorealistic, professional photography" in prompt
        assert "主角" in prompt  # role 注入

    def test_female_character_uses_1girl(self):
        """描述含「女」→ 1girl 前缀。"""
        char = Character(
            character_id="char_f",
            name="红绫",
            role="女主",
            description="古装女侠，红衣长发，眉目英气。",
        )
        anchor = resolve_style_anchor("写实电影感")
        assert _fallback_view_prompt(char, anchor).startswith("1girl")

    def test_anime_anchor_has_no_realism_tail(self, sample_character):
        """动漫锚定 → 无 photorealistic 画质尾，含风格名。"""
        anchor = resolve_style_anchor("国漫")
        prompt = _fallback_view_prompt(sample_character, anchor)
        assert "Chinese anime guoman style" in prompt
        assert "photorealistic" not in prompt


# ---------------------------------------------------------------------------
# execute 的三种提示词模式
# ---------------------------------------------------------------------------
class TestExecutePromptModes:
    async def test_preview_confirmed_prompt_skips_search_and_llm(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        mock_web_search,
    ):
        """preview_positive_prompt 非空 → 跳过搜索与 LLM，三视图共用确认提示词。"""
        mock_get_comfyui_result.return_value = _images_output()

        request = CharacterRequest(
            character=sample_character,
            preview_positive_prompt="confirmed preview prompt",
            preview_negative_prompt="preview neg",
        )
        response = await agent.execute(request)

        assert response.success is True
        mock_call_llm.assert_not_called()
        mock_web_search.assert_not_called()
        # 工作流正面/负面提示词均来自预览确认值
        workflow = mock_call_comfyui.call_args_list[0].args[1]
        assert "confirmed preview prompt" in workflow["2"]["inputs"]["text"]
        assert "preview neg" in workflow["3"]["inputs"]["text"]
        # used_prompts 回写确认值，供前端再编辑
        assert response.data["used_prompts"]["positive_prompt"] == "confirmed preview prompt"
        assert response.data["used_prompts"]["negative_prompt"] == "preview neg"

    async def test_custom_prompt_mode_skips_search_and_llm(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        mock_web_search,
    ):
        """custom_positive_prompt 非空 → 跳过搜索与 LLM。"""
        mock_get_comfyui_result.return_value = _images_output()

        request = CharacterRequest(
            character=sample_character,
            custom_positive_prompt="my custom prompt",
            custom_negative_prompt="my custom neg",
        )
        response = await agent.execute(request)

        assert response.success is True
        mock_call_llm.assert_not_called()
        mock_web_search.assert_not_called()
        workflow = mock_call_comfyui.call_args_list[0].args[1]
        assert "my custom prompt" in workflow["2"]["inputs"]["text"]
        assert "my custom neg" in workflow["3"]["inputs"]["text"]

    async def test_search_reference_injected_into_llm_message(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        mock_web_search,
    ):
        """默认模式：搜索到参考资料 → 注入 LLM user 消息（参考日志分支）。"""
        mock_web_search.return_value = "外卖员角色设计参考：明黄色制服、头盔。"
        mock_call_llm.return_value = _valid_prompts_json()
        mock_get_comfyui_result.return_value = _images_output()

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        user_msg = next(
            m["content"]
            for m in mock_call_llm.call_args.kwargs["messages"]
            if m["role"] == "user"
        )
        assert "参考资料" in user_msg
        assert "明黄色制服" in user_msg


    async def test_library_registration_failure_not_blocking(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """资产库登记抛异常 → 仅告警，不阻断角色生成主流程（仍 success）。"""
        mock_call_llm.return_value = _valid_prompts_json()
        mock_get_comfyui_result.return_value = _images_output()

        with patch("app.agents.character_agent.character_library") as mock_lib:
            mock_lib.register_from_card.side_effect = RuntimeError("库写入失败")
            response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert set(response.data["reference_images"]) == {"front", "side", "closeup"}
        mock_lib.register_from_card.assert_called_once()


# ---------------------------------------------------------------------------
# preview 预览全流程
# ---------------------------------------------------------------------------
class TestPreview:
    async def test_preview_success(
        self, agent, sample_character, mock_call_llm, mock_web_search
    ):
        """搜索 + LLM 均成功 → success，search_reference 与三视图提示词齐备，无降级错误。"""
        mock_web_search.return_value = "角色设计参考资料"
        mock_call_llm.return_value = _valid_prompts_json()

        response = await agent.preview(
            CharacterPreviewRequest(character=sample_character, style="写实电影感")
        )

        assert response.success is True
        assert response.error is None
        assert response.data["search_reference"] == "角色设计参考资料"
        prompts = response.data["prompts"]
        assert set(prompts) == {
            "front_view_prompt",
            "side_view_prompt",
            "closeup_prompt",
            "negative_prompt",
        }
        assert prompts["front_view_prompt"].startswith("front")

    async def test_preview_search_failure_degrades(
        self, agent, sample_character, mock_call_llm, mock_web_search
    ):
        """联网搜索失败 → 标注降级但仍返回 LLM 提示词，流程不中断。"""
        mock_web_search.side_effect = RuntimeError("搜索服务不可用")
        mock_call_llm.return_value = _valid_prompts_json()

        response = await agent.preview(CharacterPreviewRequest(character=sample_character))

        assert response.success is True
        assert "联网搜索失败" in response.error
        assert response.data["search_reference"] == ""
        assert response.data["prompts"]["front_view_prompt"].startswith("front")

    async def test_preview_llm_failure_falls_back(
        self, agent, sample_character, mock_call_llm, mock_web_search
    ):
        """LLM 生成失败 → 降级为兜底提示词（1boy + 默认负面词），标注降级。"""
        mock_call_llm.side_effect = RuntimeError("LLM 超时")

        response = await agent.preview(CharacterPreviewRequest(character=sample_character))

        assert response.success is True
        assert "提示词生成失败" in response.error
        prompts = response.data["prompts"]
        assert prompts["front_view_prompt"].startswith("1boy")
        # 三视图共用同一兜底提示词
        assert prompts["side_view_prompt"] == prompts["front_view_prompt"]
        assert prompts["closeup_prompt"] == prompts["front_view_prompt"]
        assert prompts["negative_prompt"] == DEFAULT_NEGATIVE_PROMPT

    async def test_preview_overall_timeout_falls_back(
        self, agent, sample_character, mock_call_llm, monkeypatch
    ):
        """整体超过 45s → asyncio.TimeoutError，返回兜底提示词并标注预览超时。"""
        async def _timeout_wait_for(coro, timeout=None):
            coro.close()  # 避免 coroutine never awaited 告警
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", _timeout_wait_for)

        response = await agent.preview(CharacterPreviewRequest(character=sample_character))

        assert response.success is True
        assert "预览超时" in response.error
        assert response.data["prompts"]["front_view_prompt"].startswith("1boy")
        assert response.data["prompts"]["negative_prompt"] == DEFAULT_NEGATIVE_PROMPT
        # 超时发生在 LLM 阶段，call_llm 未必被消费（协程被整体掐断）
        mock_call_llm.assert_not_called()


# ---------------------------------------------------------------------------
# _generate_prompts 解析容错
# ---------------------------------------------------------------------------
class TestGeneratePromptsParsing:
    async def test_broken_json_repaired(self, agent, sample_character, mock_call_llm):
        """LLM 返回非法 JSON（尾逗号）→ json_repair 修复后取到正面提示词。"""
        mock_call_llm.return_value = (
            '{"front_view_prompt": "repaired front", "side_view_prompt": "s",'
            ' "closeup_prompt": "c", "negative_prompt": "nn",}'
        )

        prompts = await agent._generate_prompts(sample_character, "写实电影感", "")

        assert prompts["front_view_prompt"].startswith("repaired front")

    async def test_non_dict_repair_falls_back(self, agent, sample_character, mock_call_llm):
        """json_repair 修复结果为列表（非字典）→ data={} → 正面提示词走兜底。"""
        mock_call_llm.return_value = "[1, 2, 3"

        prompts = await agent._generate_prompts(sample_character, "写实电影感", "")

        assert prompts["front_view_prompt"].startswith("1boy")
        # 负面词缺失 → DEFAULT_NEGATIVE_PROMPT + 风格负面尾
        assert prompts["negative_prompt"].startswith(DEFAULT_NEGATIVE_PROMPT)


# ---------------------------------------------------------------------------
# _rag_optimize_prompts 边界
# ---------------------------------------------------------------------------
class TestRagOptimizeEdge:
    async def test_anchor_resolved_when_none(self, agent):
        """anchor=None → 内部按 style 解析锚定，extra_instruction 携带风格名。"""
        prompts = {
            "front_view_prompt": "front",
            "side_view_prompt": "side",
            "closeup_prompt": "closeup",
            "negative_prompt": "neg",
        }
        with patch(
            "app.agents.character_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={
                "optimized_positive": "rag opt",
                "optimized_negative": "rag neg",
            },
        ) as mock_opt:
            result = await agent._rag_optimize_prompts(prompts, "国漫")

        assert mock_opt.await_count == 3
        extra = mock_opt.call_args.kwargs["extra_instruction"]
        assert "Chinese anime guoman style" in extra
        # 首个非空 optimized_negative 覆盖负面词
        assert result["negative_prompt"] == "rag neg"

    async def test_empty_positive_view_skipped(self, agent):
        """某视图正面提示词为空 → 跳过该视图的 RAG 优化，保留原空串。"""
        prompts = {
            "front_view_prompt": "front",
            "side_view_prompt": "",
            "closeup_prompt": "closeup",
            "negative_prompt": "neg",
        }
        with patch(
            "app.agents.character_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={"optimized_positive": "rag opt"},
        ) as mock_opt:
            result = await agent._rag_optimize_prompts(
                prompts, "写实电影感", resolve_style_anchor("写实电影感")
            )

        assert mock_opt.await_count == 2  # side 被跳过
        assert result["side_view_prompt"] == ""
        assert result["front_view_prompt"] == "rag opt"


# ---------------------------------------------------------------------------
# _generate_image_via_sdxl / _get_vlm_client
# ---------------------------------------------------------------------------
class TestSdxlAndVlmClient:
    async def test_missing_prompt_id_raises(
        self, agent, mock_call_comfyui
    ):
        """ComfyUI 响应缺 prompt_id → RuntimeError。"""
        mock_call_comfyui.return_value = {"error": "bad workflow"}

        with pytest.raises(RuntimeError, match="prompt_id"):
            await agent._generate_image_via_sdxl(
                "http://worker", "pos", "neg", "char_001", "front", 42
            )

    def test_vlm_client_lazy_singleton(self, agent, monkeypatch):
        """_get_vlm_client 懒加载：首次创建，二次复用同一实例。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        with patch("app.agents.character_agent.AsyncOpenAI") as mock_aoai:
            first = agent._get_vlm_client()
            second = agent._get_vlm_client()

        assert first is second
        assert mock_aoai.call_count == 1
        assert mock_aoai.call_args.kwargs["base_url"] == "http://vlm:9000/v1"


# ---------------------------------------------------------------------------
# 质检 fail-open 直返与重试耗尽
# ---------------------------------------------------------------------------
class TestQCEdge:
    QC_PASS = json.dumps({"pass": True, "reason": ""})
    QC_MATCH = json.dumps({"match": True, "reason": ""})
    QC_MISMATCH = json.dumps({"match": False, "reason": "发色不一致"})

    async def test_qc_front_returns_empty_when_vlm_url_missing(
        self, agent, sample_character, monkeypatch
    ):
        """visual_model_url 为空 → _qc_front_view 直返空串（不发起任何请求）。"""
        monkeypatch.setattr(settings, "visual_model_url", "")
        agent.http.get = AsyncMock(side_effect=AssertionError("不应发起请求"))

        reason = await agent._qc_front_view(
            "http://worker/view?filename=f.png",
            sample_character,
            resolve_style_anchor("写实电影感"),
        )
        assert reason == ""

    async def test_qc_consistency_returns_empty_when_vlm_url_missing(
        self, agent, sample_character, monkeypatch
    ):
        """visual_model_url 为空 → _qc_view_consistency 直返空串。"""
        monkeypatch.setattr(settings, "visual_model_url", "")
        agent.http.get = AsyncMock(side_effect=AssertionError("不应发起请求"))

        reason = await agent._qc_view_consistency(
            "side",
            "http://worker/view?filename=s.png",
            "http://worker/view?filename=f.png",
            sample_character,
        )
        assert reason == ""

    async def test_side_retry_exhausted_fails(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """side 初始 + 2 次重试全部与 front 不一致 → success=False，废品拦截。"""
        monkeypatch.setattr(settings, "character_view_qc_enabled", True)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")

        resp = MagicMock()
        resp.content = b"fake-png-bytes"
        resp.raise_for_status = MagicMock()
        agent.http.get = AsyncMock(return_value=resp)

        def _vlm_result(content: str):
            result = MagicMock()
            result.choices = [MagicMock()]
            result.choices[0].message.content = content
            return result

        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(
            side_effect=[
                _vlm_result(self.QC_PASS),       # front 自检合格
                _vlm_result(self.QC_MISMATCH),   # side 初次比对不一致
                _vlm_result(self.QC_MISMATCH),   # side 重试 1 仍不一致
                _vlm_result(self.QC_MISMATCH),   # side 重试 2 仍不一致 → 耗尽抛错
                _vlm_result(self.QC_MATCH),      # closeup（gather 并发，兜底可用）
            ]
        )
        agent._vlm_client = vlm

        mock_call_llm.return_value = _valid_prompts_json()

        counter = {"front": 0, "side": 0, "closeup": 0}

        async def _fake_sdxl(worker_url, positive, negative, character_id, view_name, seed, anchor=None):
            counter[view_name] += 1
            return f"http://mock/{view_name}_{counter[view_name]}.png"

        agent._generate_image_via_sdxl = AsyncMock(side_effect=_fake_sdxl)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is False
        assert "side" in response.error
        assert "质检连续" in response.error
        assert counter["side"] == 3  # 初始 1 次 + 重试 2 次
        assert counter["front"] == 1  # front 合格不重生成
