"""分镜 Agent 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.storyboard_agent import BEAT_VISUAL_HINTS, StoryboardAgent
from app.config import settings
from app.models.schemas import Scene, StoryboardRequest
from app.services.style_anchor import (
    SDXL_CHECKPOINT_ANIME,
    SDXL_CHECKPOINT_REALISTIC,
    resolve_style_anchor,
)


@pytest.fixture
def agent():
    return StoryboardAgent()


class TestStoryboardFilenamePrefixUniqueness:
    """M15.5: 分镜 filename_prefix 带每次运行唯一后缀（防跨后端同名碰撞）。"""

    async def test_filename_prefix_has_unique_suffix_per_run(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """连续两次执行 → workflow filename_prefix 后缀不同，且保留 scene 语义。"""
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        request = StoryboardRequest(scene=sample_scene)
        assert (await agent.execute(request)).success is True
        assert (await agent.execute(request)).success is True

        prefixes = [
            call.args[1]["7"]["inputs"]["filename_prefix"]
            for call in mock_call_comfyui.call_args_list
        ]
        assert len(prefixes) == 2
        assert prefixes[0] != prefixes[1]
        assert all(p.startswith("storyboard_scene_1_") for p in prefixes)


class TestStoryboardAgentExecute:
    """基础执行测试（默认 sdxl 后端，由 conftest 设置）。"""

    async def test_success_with_existing_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        sample_scene.prompt = "existing prompt"
        sample_scene.negative_prompt = "existing negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {
                "images": [
                    {"filename": "sb_1.png", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 1
        # 提示词现在总是经 LLM 重写
        # M15.1：execute 强制追加默认画风（写实电影感）锚定尾
        assert response.data["prompt_used"] == (
            "rewritten prompt, cinematic realistic, photorealistic, professional photography"
        )
        # 预览钩子已随旧 LTX-2B 移除 → preview_video_url 默认为空字符串
        assert response.data["preview_video_url"] == ""

    async def test_success_generate_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        sample_scene.prompt = ""
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "generated prompt",
                "negative_prompt": "generated negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {
                "images": [
                    {"filename": "sb_1.png", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["prompt_used"] == (
            "generated prompt, cinematic realistic, photorealistic, professional photography"
        )

    async def test_exception_returns_error(self, agent, sample_scene, mock_call_llm):
        sample_scene.prompt = ""
        mock_call_llm.side_effect = RuntimeError("失败")

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is False
        assert "失败" in response.error


class TestBeatVisualHints:
    """narrative_beat → 分镜视觉指令注入测试。"""

    def _make_scene(self, beat: str) -> Scene:
        return Scene(
            scene_id=1,
            episode=1,
            shot_type="特写",
            description="主角盯着手机屏幕",
            narrative_beat=beat,
        )

    async def test_beat_hint_injected_into_llm_message(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        scene = self._make_scene("hook")

        await agent._generate_prompts(scene, [], "写实电影感")

        messages = mock_call_llm.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "叙事节拍" in user_msg
        assert "hook" in user_msg
        assert BEAT_VISUAL_HINTS["hook"] in user_msg

    async def test_no_beat_no_hint(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        scene = self._make_scene("")

        await agent._generate_prompts(scene, [], "写实电影感")

        messages = mock_call_llm.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "叙事节拍" not in user_msg

    async def test_unknown_beat_no_hint(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        scene = self._make_scene("unknown_beat")

        await agent._generate_prompts(scene, [], "写实电影感")

        messages = mock_call_llm.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "叙事节拍" not in user_msg

    def test_all_valid_beats_have_hints(self):
        for beat in ("hook", "escalation", "reversal", "cliffhanger", "emotional_beat", "transition"):
            assert beat in BEAT_VISUAL_HINTS
            assert BEAT_VISUAL_HINTS[beat]


class TestStoryboardAgentRAGEnhance:
    async def test_rag_enhances_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        with patch(
            "app.agents.storyboard_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={
                "optimized_positive": "rag positive",
                "optimized_negative": "rag negative",
            },
        ):
            response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        assert response.data["prompt_used"] == (
            "rag positive, cinematic realistic, photorealistic, professional photography"
        )

    async def test_rag_failure_keeps_llm_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        with patch(
            "app.agents.storyboard_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("RAG 失败"),
        ):
            response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        # M15.1：execute 强制追加默认画风（写实电影感）锚定尾
        assert response.data["prompt_used"] == (
            "rewritten prompt, cinematic realistic, photorealistic, professional photography"
        )


class TestStoryboardStyleSanitize:
    """M15.4: 分镜 LLM 重写结果在追加风格尾前先清洗冲突风格词。"""

    async def test_guoman_strips_conflicts_before_tail(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风「国漫」：LLM 重写带 photorealistic、负面词排斥 anime → 清洗后再追加国漫尾。"""
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "close-up, convenience store, photorealistic, hyperrealistic texture",
                "negative_prompt": "anime, cartoon, blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        request = StoryboardRequest(scene=sample_scene, style="国漫")
        response = await agent.execute(request)

        assert response.success is True
        prompt_used = response.data["prompt_used"]
        assert "photorealistic" not in prompt_used
        assert "hyperrealistic" not in prompt_used
        assert "convenience store" in prompt_used
        assert prompt_used.endswith(", Chinese anime guoman style")

    async def test_realistic_strips_anime_before_tail(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风「写实电影感」：LLM 重写误带 cartoon → 清洗后追加写实尾。"""
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "medium shot, city street, cartoon shading",
                "negative_prompt": "photorealistic, blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        request = StoryboardRequest(scene=sample_scene, style="写实电影感")
        response = await agent.execute(request)

        assert response.success is True
        prompt_used = response.data["prompt_used"]
        assert "cartoon" not in prompt_used.lower()
        assert prompt_used.endswith(
            ", cinematic realistic, photorealistic, professional photography"
        )


class TestStoryboardCheckpointByStyle:
    """M15.7: 分镜 SDXL 工作流 checkpoint 随画风写实性切换（与角色 Agent 同因）。"""

    async def test_guoman_uses_animagine_checkpoint(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风「国漫」→ workflow 使用 animagineXL40。"""
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        request = StoryboardRequest(scene=sample_scene, style="国漫")
        assert (await agent.execute(request)).success is True

        ckpt = mock_call_comfyui.call_args.args[1]["1"]["inputs"]["ckpt_name"]
        assert ckpt == SDXL_CHECKPOINT_ANIME

    async def test_realistic_uses_majicmix_checkpoint(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风「写实电影感」→ workflow 使用 majicMIX。"""
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        request = StoryboardRequest(scene=sample_scene, style="写实电影感")
        assert (await agent.execute(request)).success is True

        ckpt = mock_call_comfyui.call_args.args[1]["1"]["inputs"]["ckpt_name"]
        assert ckpt == SDXL_CHECKPOINT_REALISTIC


class TestStoryboardStyleWeightSeparation:
    """M16.1: 分镜画风子句权重分离 — system prompt 结构断言（与角色 Agent 同因）。"""

    async def test_system_prompt_separates_style_and_appearance(
        self, agent, sample_scene, mock_call_llm
    ):
        """画风「国漫」→ system prompt 必填行仅风格名，整串降可选，含权重分离规则。"""
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})

        await agent._generate_prompts(sample_scene, [], "国漫", "")

        messages = mock_call_llm.call_args.kwargs["messages"]
        system = next(m["content"] for m in messages if m["role"] == "system")
        mandatory = next(line for line in system.split("\n") if "必须显式包含" in line)
        assert '"Chinese anime guoman style"' in mandatory
        assert "分镜画面" in mandatory
        assert "elaborate costumes" not in mandatory
        optional_line = next(
            line for line in system.split("\n") if "elaborate costumes" in line
        )
        assert "可选" in optional_line
        assert "权重分离规则" in system


# ============================================================================
# M16.2: 拼贴检测 + 短 prompt 重构重试
# ============================================================================


def _make_vlm_result(content: str):
    result = MagicMock()
    result.choices = [MagicMock()]
    result.choices[0].message.content = content
    return result


class TestStoryboardAppearanceCheck:
    """M16.2b: VLM 校验关键帧中出场角色外貌与角色描述的一致性。

    判定焦点为发色/发型/服装款式与颜色；画风、姿势、表情、视角差异不算失真。
    返回空串表示一致或跳过，非空串为失真原因（供短 prompt 重构参考）。
    """

    def _attach_vlm(self, agent, content: str):
        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(
            return_value=_make_vlm_result(content)
        )
        agent._vlm_client = vlm
        return vlm

    def _mock_image_download(self, agent):
        resp = MagicMock()
        resp.content = b"fake-png-bytes"
        resp.raise_for_status = MagicMock()
        agent.http = MagicMock()
        agent.http.get = AsyncMock(return_value=resp)

    async def test_vlm_not_configured_returns_empty(
        self, agent, sample_character, monkeypatch
    ):
        """visual_model_url 为空 → 跳过校验（不发起任何请求）。"""
        monkeypatch.setattr(settings, "visual_model_url", "")
        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )
        assert reason == ""

    async def test_match_true_returns_empty(
        self, agent, sample_character, monkeypatch
    ):
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        self._mock_image_download(agent)
        self._attach_vlm(agent, json.dumps({"match": True, "reason": ""}))

        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )

        assert reason == ""

    async def test_match_false_returns_reason(
        self, agent, sample_character, monkeypatch
    ):
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        self._mock_image_download(agent)
        vlm = self._attach_vlm(
            agent, json.dumps({"match": False, "reason": "发色为银灰而非黑色"})
        )

        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )

        assert reason == "发色为银灰而非黑色"
        # 校验 VLM 请求结构：文本含角色描述与判定焦点，图片走 data URL high detail
        call_kwargs = vlm.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == settings.visual_model_name
        assert call_kwargs["temperature"] == 0.1
        content = call_kwargs["messages"][0]["content"]
        text_part = next(p for p in content if p["type"] == "text")
        img_part = next(p for p in content if p["type"] == "image_url")
        assert "林远" in text_part["text"]
        assert "发色" in text_part["text"]
        assert "不算失真" in text_part["text"]
        assert img_part["image_url"]["url"].startswith("data:image/png;base64,")
        assert img_part["image_url"]["detail"] == "high"

    async def test_match_false_without_reason_returns_default(
        self, agent, sample_character, monkeypatch
    ):
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        self._mock_image_download(agent)
        self._attach_vlm(agent, json.dumps({"match": False}))

        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )

        assert reason == "外貌与角色描述不一致"

    async def test_think_tags_stripped_before_parse(
        self, agent, sample_character, monkeypatch
    ):
        """推理模型内联 <think> 思维链 → 剥离后再解析 JSON。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        self._mock_image_download(agent)
        self._attach_vlm(
            agent,
            "<think>逐步分析画面</think>" + json.dumps({"match": True, "reason": ""}),
        )

        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )

        assert reason == ""

    async def test_non_dict_output_returns_empty(
        self, agent, sample_character, monkeypatch
    ):
        """VLM 输出无法解析为 dict → 跳过校验（不误报失真）。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        self._mock_image_download(agent)
        self._attach_vlm(agent, '"just a plain string"')

        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )

        assert reason == ""


class TestStoryboardRebuildShortPrompt:
    """M16.2c: 失真后的 LLM 短 prompt 重构（外貌前置、禁氛围词、≤80 词）。"""

    async def test_returns_prompt_and_embeds_constraints(
        self, agent, sample_scene, sample_character, mock_call_llm
    ):
        mock_call_llm.return_value = json.dumps(
            {"prompt": "medium shot, girl with black long hair, white shirt"}
        )

        prompt = await agent._rebuild_short_prompt(
            sample_scene, [sample_character], "国漫", "发色为银灰而非黑色"
        )

        assert prompt == "medium shot, girl with black long hair, white shirt"
        # system 含失真原因与硬性规则（词数上限、禁氛围词清单）
        system = mock_call_llm.call_args.kwargs["messages"][0]["content"]
        assert "发色为银灰而非黑色" in system
        assert "80" in system
        assert "elaborate costumes" in system
        assert "vibrant colors" in system
        # user 含角色描述、画风与场景信息
        user_msg = mock_call_llm.call_args.kwargs["messages"][1]["content"]
        assert "林远" in user_msg
        assert "国漫" in user_msg
        assert "特写" in user_msg
        # 低温重构（确定性优先）
        assert mock_call_llm.call_args.kwargs["temperature"] == 0.4

    async def test_invalid_json_returns_empty(
        self, agent, sample_scene, sample_character, mock_call_llm
    ):
        mock_call_llm.return_value = '"not a dict"'

        prompt = await agent._rebuild_short_prompt(
            sample_scene, [sample_character], "国漫", "reason"
        )

        assert prompt == ""

    async def test_missing_prompt_key_returns_empty(
        self, agent, sample_scene, sample_character, mock_call_llm
    ):
        mock_call_llm.return_value = json.dumps({"other": "x"})

        prompt = await agent._rebuild_short_prompt(
            sample_scene, [sample_character], "国漫", "reason"
        )

        assert prompt == ""


class TestStoryboardVerifyAndRetryAppearance:
    """M16.2b/c: 拼贴检测+重试主流程 — 失真才重试，任何环节异常保留原图。"""

    def _patch_internals(
        self,
        agent,
        *,
        mismatch="",
        rebuild="rebuilt prompt",
        dispatch_url="http://worker/view?filename=new.png",
        dispatch_side_effect=None,
    ):
        check = AsyncMock(return_value=mismatch)
        rebuild_mock = AsyncMock(return_value=rebuild)
        dispatch = AsyncMock(
            return_value=(dispatch_url, 42), side_effect=dispatch_side_effect
        )
        agent._check_appearance_mismatch = check
        agent._rebuild_short_prompt = rebuild_mock
        agent._dispatch_image_generation = dispatch
        return check, rebuild_mock, dispatch

    async def _run(self, agent, sample_scene, sample_character):
        url, _seed = await agent._verify_and_retry_appearance(
            image_url="http://worker/view?filename=old.png",
            scene=sample_scene,
            characters=[sample_character],
            style="国漫",
            worker_url="http://worker",
            negative="neg",
            anchor=resolve_style_anchor("国漫"),
        )
        return url

    async def test_no_mismatch_keeps_original(
        self, agent, sample_scene, sample_character
    ):
        check, rebuild_mock, dispatch = self._patch_internals(agent, mismatch="")

        url = await self._run(agent, sample_scene, sample_character)

        assert url == "http://worker/view?filename=old.png"
        assert check.await_count == 1
        assert rebuild_mock.await_count == 0
        assert dispatch.await_count == 0

    async def test_mismatch_rebuilds_and_regenerates(
        self, agent, sample_scene, sample_character
    ):
        _, rebuild_mock, dispatch = self._patch_internals(
            agent,
            mismatch="发色为银灰而非黑色",
            rebuild="medium shot, girl, photorealistic, vibrant colors",
        )

        url = await self._run(agent, sample_scene, sample_character)

        assert url == "http://worker/view?filename=new.png"
        # 重构时传入失真原因
        assert rebuild_mock.call_args.args[3] == "发色为银灰而非黑色"
        # 重试 prompt 经清洗 + 氛围剥离 + 锚定尾：冲突词/氛围词被移除，风格名强制在尾
        retry_positive = dispatch.call_args.kwargs["positive"]
        assert "photorealistic" not in retry_positive
        assert "vibrant colors" not in retry_positive
        assert "medium shot, girl" in retry_positive
        assert retry_positive.endswith(", Chinese anime guoman style")
        # 复用原 worker；负面词在原文基础上追加注册表 correction 层子句（M25.9 C2）
        assert dispatch.call_args.kwargs["worker_url"] == "http://worker"
        retry_negative = dispatch.call_args.kwargs["negative"]
        assert retry_negative.startswith("neg")
        assert "correction layer" in retry_negative  # 注册表修正子句注入

    async def test_check_exception_keeps_original(
        self, agent, sample_scene, sample_character
    ):
        agent._check_appearance_mismatch = AsyncMock(
            side_effect=RuntimeError("VLM down")
        )
        dispatch = AsyncMock()
        agent._dispatch_image_generation = dispatch

        url = await self._run(agent, sample_scene, sample_character)

        assert url == "http://worker/view?filename=old.png"
        assert dispatch.await_count == 0

    async def test_rebuild_exception_keeps_original(
        self, agent, sample_scene, sample_character
    ):
        agent._check_appearance_mismatch = AsyncMock(return_value="失真")
        agent._rebuild_short_prompt = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )
        dispatch = AsyncMock()
        agent._dispatch_image_generation = dispatch

        url = await self._run(agent, sample_scene, sample_character)

        assert url == "http://worker/view?filename=old.png"
        assert dispatch.await_count == 0

    async def test_rebuild_empty_keeps_original(
        self, agent, sample_scene, sample_character
    ):
        _, _, dispatch = self._patch_internals(agent, mismatch="失真", rebuild="")

        url = await self._run(agent, sample_scene, sample_character)

        assert url == "http://worker/view?filename=old.png"
        assert dispatch.await_count == 0

    async def test_regenerate_failure_keeps_original(
        self, agent, sample_scene, sample_character
    ):
        self._patch_internals(
            agent,
            mismatch="失真",
            rebuild="medium shot, girl",
            dispatch_side_effect=RuntimeError("ComfyUI OOM"),
        )

        url = await self._run(agent, sample_scene, sample_character)

        assert url == "http://worker/view?filename=old.png"


class TestStoryboardExecuteAppearanceCheckWiring:
    """M16.2: execute 接线 — 有出场角色且开关开启时才触发外貌校验重试。"""

    def _setup_base_mocks(
        self, agent, mock_call_llm, mock_get_comfyui_result
    ):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        verify = AsyncMock(return_value=("http://worker/view?filename=sb.png", None))
        agent._verify_and_retry_appearance = verify
        return verify

    async def test_characters_present_triggers_check(
        self,
        agent,
        sample_scene,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        verify = self._setup_base_mocks(agent, mock_call_llm, mock_get_comfyui_result)

        request = StoryboardRequest(scene=sample_scene, characters=[sample_character])
        response = await agent.execute(request)

        assert response.success is True
        assert verify.await_count == 1
        assert verify.call_args.kwargs["characters"] == [sample_character]
        # 返回 URL 采用校验重试后的结果
        assert response.data["image_url"] == "http://worker/view?filename=sb.png"

    async def test_no_characters_skips_check(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        verify = self._setup_base_mocks(agent, mock_call_llm, mock_get_comfyui_result)

        response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        assert verify.await_count == 0

    async def test_check_disabled_skips(
        self,
        agent,
        sample_scene,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "storyboard_appearance_check", False)
        verify = self._setup_base_mocks(agent, mock_call_llm, mock_get_comfyui_result)

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, characters=[sample_character])
        )

        assert response.success is True
        assert verify.await_count == 0


# ============================================================================
# M18.3: 关键帧定妆照 IPAdapter 锚定
# ============================================================================


class TestStoryboardKeyframeAnchor:
    """M18.3: SDXL 关键帧生成注入角色定妆照 front 作为 IPAdapter 图像参考。

    有定妆照参考时工作流动态注入 IPAdapterModelLoader / CLIPVisionLoader /
    LoadImage / IPAdapterAdvanced 节点，并将 KSampler model 重定向到 IPAdapter
    输出；无参考图 / 开关关闭 / 注入异常时回退原工作流（锚定是增强不是阻断）。
    """

    ANCHOR_URL = "http://lib/front_char001.png"

    def _enable_anchor(self, monkeypatch):
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", True)
        # 与本地 .env 的 COMFYUI_LB_BACKEND_URLS 隔离：本类旧用例断言单点上传语义，
        # LB 复制行为由 TestStoryboardKeyframeAnchorLB 用 _enable_lb_backends 显式覆盖
        monkeypatch.setattr(settings, "comfyui_lb_backend_urls", "")

    @staticmethod
    def _ok_comfyui_result(mock_get_comfyui_result):
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

    @staticmethod
    def _node_types(workflow) -> set:
        return {n["class_type"] for n in workflow.values()}

    @staticmethod
    def _node_id_by_type(workflow, class_type: str) -> str:
        return next(k for k, n in workflow.items() if n["class_type"] == class_type)

    async def test_ipadapter_injected_when_reference_available(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """有定妆照 front → 注入 4 类节点，KSampler model 重定向，定妆照上传挂 LoadImage。"""
        self._enable_anchor(monkeypatch)
        self._ok_comfyui_result(mock_get_comfyui_result)

        url, _seed = await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        assert "/view?" in url
        workflow = mock_call_comfyui.call_args.args[1]
        types = self._node_types(workflow)
        assert "IPAdapterModelLoader" in types
        assert "CLIPVisionLoader" in types
        assert "IPAdapterAdvanced" in types
        assert "LoadImage" in types
        # KSampler model 重定向到 IPAdapterAdvanced 输出
        ipa_id = self._node_id_by_type(workflow, "IPAdapterAdvanced")
        load_id = self._node_id_by_type(workflow, "LoadImage")
        assert workflow["5"]["inputs"]["model"] == [ipa_id, 0]
        # 定妆照经 upload_image_to_comfyui 上传（mock 返回 input.png）后挂 LoadImage，
        # IPAdapterAdvanced 的图像输入来自该 LoadImage
        mock_upload_image.assert_awaited_once_with("http://worker", self.ANCHOR_URL)
        assert workflow[load_id]["inputs"]["image"] == "input.png"
        assert workflow[ipa_id]["inputs"]["image"] == [load_id, 0]

    async def test_no_anchor_when_no_reference_image(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """无参考图 → 工作流保持原样，KSampler model 仍为 ["1", 0]，不上传。"""
        self._enable_anchor(monkeypatch)
        self._ok_comfyui_result(mock_get_comfyui_result)

        url, _seed = await agent._generate_image_via_sdxl("http://worker", "pos", "neg", 1)

        assert "/view?" in url
        workflow = mock_call_comfyui.call_args.args[1]
        assert "IPAdapterAdvanced" not in self._node_types(workflow)
        assert workflow["5"]["inputs"]["model"] == ["1", 0]
        assert mock_upload_image.await_count == 0

    async def test_anchor_disabled_by_config(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """开关关闭 → 即便传入参考图也不注入、不上传。"""
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
        self._ok_comfyui_result(mock_get_comfyui_result)

        await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        workflow = mock_call_comfyui.call_args.args[1]
        assert "IPAdapterAdvanced" not in self._node_types(workflow)
        assert workflow["5"]["inputs"]["model"] == ["1", 0]
        assert mock_upload_image.await_count == 0

    async def test_anchor_weight_from_config(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """IPAdapter 权重由 settings.storyboard_keyframe_anchor_weight 控制。"""
        self._enable_anchor(monkeypatch)
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_weight", 0.45)
        self._ok_comfyui_result(mock_get_comfyui_result)

        await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        workflow = mock_call_comfyui.call_args.args[1]
        ipa_id = self._node_id_by_type(workflow, "IPAdapterAdvanced")
        assert workflow[ipa_id]["inputs"]["weight"] == 0.45

    async def test_upload_failure_falls_back_to_original_workflow(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """定妆照上传异常 → 回退原工作流（无 IPAdapter 节点），生成流程不中断。"""
        self._enable_anchor(monkeypatch)
        mock_upload_image.side_effect = RuntimeError("upload boom")
        self._ok_comfyui_result(mock_get_comfyui_result)

        url, _seed = await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        assert "/view?" in url
        workflow = mock_call_comfyui.call_args.args[1]
        assert "IPAdapterAdvanced" not in self._node_types(workflow)
        assert workflow["5"]["inputs"]["model"] == ["1", 0]

    # --- M18.3.1: 定妆照复制到 LB 全部后端（修复上传/执行分离 400） ---

    def _enable_lb_backends(self, monkeypatch, urls="http://b1,http://b2,http://b3"):
        monkeypatch.setattr(settings, "comfyui_lb_backend_urls", urls)

    async def test_anchor_replicated_to_all_lb_backends(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """配置 LB 后端清单后，定妆照以同一文件名直连上传到每个后端（不经 LB 轮询）。"""
        self._enable_anchor(monkeypatch)
        self._enable_lb_backends(monkeypatch)
        self._ok_comfyui_result(mock_get_comfyui_result)

        url, _seed = await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        assert "/view?" in url
        # 复制到 3 个后端，且不再经 LB 上传
        assert mock_upload_image.await_count == 3
        urls = {c.args[0] for c in mock_upload_image.call_args_list}
        assert urls == {"http://b1", "http://b2", "http://b3"}
        # 所有后端使用同一确定性文件名（LoadImage 引用的文件名）
        filenames = {c.kwargs["filename"] for c in mock_upload_image.call_args_list}
        assert len(filenames) == 1
        workflow = mock_call_comfyui.call_args.args[1]
        load_id = self._node_id_by_type(workflow, "LoadImage")
        assert workflow[load_id]["inputs"]["image"] == filenames.pop()

    async def test_anchor_replication_partial_failure_still_injects(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """部分后端复制失败仍注入（健康后端可执行，LB 健康检查避开故障后端）。"""
        self._enable_anchor(monkeypatch)
        self._enable_lb_backends(monkeypatch)
        self._ok_comfyui_result(mock_get_comfyui_result)
        mock_upload_image.side_effect = [
            "ok1.png",
            RuntimeError("b2 down"),
            "ok3.png",
        ]

        url, _seed = await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        assert "/view?" in url
        workflow = mock_call_comfyui.call_args.args[1]
        assert "IPAdapterAdvanced" in self._node_types(workflow)

    async def test_anchor_replication_all_failed_falls_back(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """全部后端复制失败 → 回退原工作流（锚定是增强不是阻断）。"""
        self._enable_anchor(monkeypatch)
        self._enable_lb_backends(monkeypatch)
        self._ok_comfyui_result(mock_get_comfyui_result)
        mock_upload_image.side_effect = RuntimeError("all down")

        url, _seed = await agent._generate_image_via_sdxl(
            "http://worker", "pos", "neg", 1, anchor_image_url=self.ANCHOR_URL
        )

        assert "/view?" in url
        workflow = mock_call_comfyui.call_args.args[1]
        assert "IPAdapterAdvanced" not in self._node_types(workflow)
        assert workflow["5"]["inputs"]["model"] == ["1", 0]

    async def test_appearance_retry_carries_anchor(
        self, agent, sample_scene, sample_character
    ):
        """M16.2 外貌失真重试时锚定参考图一并透传（重试不丢锚定）。"""
        agent._check_appearance_mismatch = AsyncMock(return_value="发色不符")
        agent._rebuild_short_prompt = AsyncMock(return_value="medium shot, girl")
        dispatch = AsyncMock(return_value=("http://worker/view?filename=new.png", 7))
        agent._dispatch_image_generation = dispatch

        url, _seed = await agent._verify_and_retry_appearance(
            image_url="http://worker/view?filename=old.png",
            scene=sample_scene,
            characters=[sample_character],
            style="国漫",
            worker_url="http://worker",
            negative="neg",
            anchor=resolve_style_anchor("国漫"),
            anchor_image_url=self.ANCHOR_URL,
        )

        assert url == "http://worker/view?filename=new.png"
        assert dispatch.call_args.kwargs["anchor_image_url"] == self.ANCHOR_URL


class TestStoryboardKeyframeAnchorWiring:
    """M18.3: execute 级联 — 从角色资产库解析定妆照 front 并注入 SDXL 工作流。"""

    ANCHOR_URL = "http://lib/front_char001.png"

    def _mock_library(self, monkeypatch, fronts: list):
        """patch 角色资产库解析：按角色顺序返回对应 reference_front。"""

        def _resolve(chars):
            return [
                {
                    "character_id": c.character_id,
                    "name": c.name,
                    "role": c.role,
                    "description": c.description,
                    "appearance_lock": "",
                    "reference_front": fronts[idx],
                }
                for idx, c in enumerate(chars)
            ]

        monkeypatch.setattr(
            "app.agents.storyboard_agent.character_library.resolve_characters",
            _resolve,
        )

    def _setup_base(
        self, mock_call_llm, mock_get_comfyui_result, monkeypatch
    ):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        # 外貌校验与锚定正交（M16.2 另有接线测试），此处关闭避免触发真实 VLM 请求
        monkeypatch.setattr(settings, "storyboard_appearance_check", False)

    async def test_execute_uses_first_available_front(
        self,
        agent,
        sample_scene,
        sample_character,
        mock_call_llm,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """首角色无定妆照、次角色有 → 锚定取首个可用 front。"""
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", True)
        # 与本地 .env 的 COMFYUI_LB_BACKEND_URLS 隔离（单点上传语义断言）
        monkeypatch.setattr(settings, "comfyui_lb_backend_urls", "")
        self._setup_base(mock_call_llm, mock_get_comfyui_result, monkeypatch)
        c2 = sample_character.model_copy(
            update={"character_id": "char_002", "name": "苏晴"}
        )
        self._mock_library(monkeypatch, ["", self.ANCHOR_URL])

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, characters=[sample_character, c2])
        )

        assert response.success is True
        mock_upload_image.assert_awaited_once()
        assert mock_upload_image.call_args.args[1] == self.ANCHOR_URL

    async def test_execute_no_front_skips_anchor(
        self,
        agent,
        sample_scene,
        sample_character,
        mock_call_llm,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """资产库无定妆照 → 不锚定、不上传，主流程不受影响。"""
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", True)
        self._setup_base(mock_call_llm, mock_get_comfyui_result, monkeypatch)
        self._mock_library(monkeypatch, [""])

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, characters=[sample_character])
        )

        assert response.success is True
        assert mock_upload_image.await_count == 0
        workflow = mock_call_comfyui.call_args.args[1]
        assert "IPAdapterAdvanced" not in {
            n["class_type"] for n in workflow.values()
        }

    async def test_execute_disabled_skips_anchor(
        self,
        agent,
        sample_scene,
        sample_character,
        mock_call_llm,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """开关关闭 → 即便资产库有定妆照也不锚定。"""
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
        self._setup_base(mock_call_llm, mock_get_comfyui_result, monkeypatch)
        self._mock_library(monkeypatch, [self.ANCHOR_URL])

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, characters=[sample_character])
        )

        assert response.success is True
        assert mock_upload_image.await_count == 0

    async def test_library_exception_skips_anchor(
        self,
        agent,
        sample_scene,
        sample_character,
        mock_call_llm,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """资产库解析异常 → 跳过锚定不阻断生成。"""
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", True)
        self._setup_base(mock_call_llm, mock_get_comfyui_result, monkeypatch)
        monkeypatch.setattr(
            "app.agents.storyboard_agent.character_library.resolve_characters",
            MagicMock(side_effect=RuntimeError("lib boom")),
        )

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, characters=[sample_character])
        )

        assert response.success is True
        assert mock_upload_image.await_count == 0


class TestAutoLink:
    """M25.2: AutoLink 自动资产匹配 — 场景文本提及的资产库角色自动并入出场角色。"""

    @pytest.fixture
    def lib(self, tmp_path):
        """隔离角色资产库 + patch mention_service 全局单例（auto_link 默认取它）。"""
        from app.models.schemas import CharacterAsset
        from app.services.character_library import CharacterLibrary

        library = CharacterLibrary(library_dir=tmp_path)
        library.save(CharacterAsset(
            character_id="c001",
            name="云曦",
            role="女主",
            description="银发蓝眼少女",
            reference_images={"front": "http://x/yunxi.png"},
            appearance_lock="yunxi, silver hair, blue eyes",
            locked=True,
        ))
        library.save(CharacterAsset(
            character_id="c002",
            name="林远",
            role="男主",
            description="黄衣外卖员",
            reference_images={"front": "http://x/linyuan.png"},
            appearance_lock="linyuan, yellow uniform",
            locked=True,
        ))
        return library

    def _patch_libs(self, monkeypatch, lib):
        from app.services import mention_service

        monkeypatch.setattr(mention_service, "character_library", lib)
        monkeypatch.setattr(
            "app.agents.storyboard_agent.character_library", lib
        )

    def test_default_enabled_merges_matched(self, agent, sample_scene, lib, monkeypatch):
        """默认开启（全局 True + 请求 None）→ 文本命中的角色并入出场角色。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        sample_scene.description = "云曦在便利店值夜班，林远推门进来"

        out = agent._apply_auto_link(StoryboardRequest(scene=sample_scene))

        assert [c.character_id for c in out.characters] == ["c001", "c002"]
        assert out.characters[0].name == "云曦"
        assert out.characters[0].reference_views == ["http://x/yunxi.png"]

    def test_request_false_disables(self, agent, sample_scene, lib, monkeypatch):
        """请求级显式 False → 关闭本请求自动匹配（回退 M24 前行为）。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        sample_scene.description = "云曦在便利店值夜班"

        req = StoryboardRequest(scene=sample_scene, auto_link_assets=False)
        assert agent._apply_auto_link(req).characters == []

    def test_request_true_overrides_global_off(self, agent, sample_scene, lib, monkeypatch):
        """全局关闭 + 请求级 True → 仍自动匹配。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", False)
        sample_scene.description = "云曦在便利店值夜班"

        req = StoryboardRequest(scene=sample_scene, auto_link_assets=True)
        assert [c.character_id for c in agent._apply_auto_link(req).characters] == ["c001"]

    def test_global_off_default_no_merge(self, agent, sample_scene, lib, monkeypatch):
        """全局关闭 + 请求 None → 不匹配。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", False)
        sample_scene.description = "云曦在便利店值夜班"

        assert agent._apply_auto_link(StoryboardRequest(scene=sample_scene)).characters == []

    def test_existing_character_not_duplicated(self, agent, sample_scene, sample_character, lib, monkeypatch):
        """已在出场列表的角色（按名字判重）不重复挂接。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        sample_scene.description = "林远看着手机"  # sample_character 名字即「林远」

        req = StoryboardRequest(scene=sample_scene, characters=[sample_character])
        out = agent._apply_auto_link(req)

        assert len(out.characters) == 1
        assert out.characters[0] is sample_character

    def test_no_text_no_merge(self, agent, lib, monkeypatch):
        """场景文本全空 → 不匹配。"""
        from app.models.schemas import Scene

        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        scene = Scene(scene_id=9, description="", character_actions="", dialogue="")

        assert agent._apply_auto_link(StoryboardRequest(scene=scene)).characters == []

    def test_exception_falls_back(self, agent, sample_scene, monkeypatch):
        """匹配过程异常 → 回退原请求不阻断。"""
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        monkeypatch.setattr(
            "app.agents.storyboard_agent.auto_link_characters",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        sample_scene.description = "云曦在便利店"

        req = StoryboardRequest(scene=sample_scene)
        assert agent._apply_auto_link(req) is req

    def test_scans_actions_and_dialogue(self, agent, sample_scene, lib, monkeypatch):
        """description 无角色名时，character_actions / dialogue 命中也挂接。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        sample_scene.description = "深夜便利店内景"
        sample_scene.character_actions = "云曦整理货架"
        sample_scene.dialogue = "林远：这么晚还没下班？"

        out = agent._apply_auto_link(StoryboardRequest(scene=sample_scene))
        assert [c.character_id for c in out.characters] == ["c001", "c002"]

    async def test_execute_injects_appearance_lock(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        lib,
        monkeypatch,
    ):
        """execute 级联：AutoLink 命中角色的外观锁定卡进入 LLM 提示词装配。"""
        self._patch_libs(monkeypatch, lib)
        monkeypatch.setattr(settings, "auto_link_assets_enabled", True)
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
        monkeypatch.setattr(settings, "storyboard_appearance_check", False)
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        sample_scene.description = "云曦在便利店值夜班"

        response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        user_msg = mock_call_llm.call_args.kwargs["messages"][1]["content"]
        assert "云曦" in user_msg
        assert "yunxi, silver hair, blue eyes" in user_msg


class TestSketchMode:
    """M25.9 C1 线稿先行两段式分镜（DramaClaw 虾导本地化）。"""

    def _setup(self, mock_call_llm, mock_get_comfyui_result, monkeypatch):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
        monkeypatch.setattr(settings, "sketch_mode_enabled", True)

    async def test_sketch_workflow_params(
        self, agent, sample_scene, mock_call_llm, mock_call_comfyui, mock_get_comfyui_result, monkeypatch
    ):
        """草图模式：工作流走低步数/低CFG/小尺寸（返工成本卡在最便宜阶段）。"""
        self._setup(mock_call_llm, mock_get_comfyui_result, monkeypatch)

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, sketch_mode=True)
        )

        assert response.success is True
        workflow = mock_call_comfyui.call_args.args[1]
        assert workflow["5"]["inputs"]["steps"] == settings.sketch_steps
        assert workflow["5"]["inputs"]["cfg"] == settings.sketch_cfg
        assert workflow["4"]["inputs"]["width"] == settings.sketch_width
        assert workflow["4"]["inputs"]["height"] == settings.sketch_height

    async def test_sketch_result_marks_seed(
        self, agent, sample_scene, mock_call_llm, mock_call_comfyui, mock_get_comfyui_result, monkeypatch
    ):
        """草图结果：is_sketch=True 且 sketch_seed 与工作流实际 seed 一致（确定性锚点）。"""
        self._setup(mock_call_llm, mock_get_comfyui_result, monkeypatch)

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, sketch_mode=True)
        )

        assert response.data["is_sketch"] is True
        workflow = mock_call_comfyui.call_args.args[1]
        assert response.data["sketch_seed"] == workflow["5"]["inputs"]["seed"]

    async def test_refine_reuses_sketch_seed(
        self, agent, sample_scene, mock_call_llm, mock_call_comfyui, mock_get_comfyui_result, monkeypatch
    ):
        """精渲染：refine_seed 复用草图 seed（同 seed 防构图漂移），走全参数。"""
        self._setup(mock_call_llm, mock_get_comfyui_result, monkeypatch)

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, sketch_mode=False, refine_seed=123456789)
        )

        workflow = mock_call_comfyui.call_args.args[1]
        assert workflow["5"]["inputs"]["seed"] == 123456789
        # 精渲染不走路草图参数
        assert workflow["5"]["inputs"]["steps"] == 25
        assert workflow["4"]["inputs"]["width"] == 1024
        # 非草图结果不带 seed 标记
        assert response.data["is_sketch"] is False
        assert response.data["sketch_seed"] is None

    async def test_sketch_skips_appearance_check(
        self, agent, sample_scene, sample_character, mock_call_llm, mock_call_comfyui, mock_get_comfyui_result, monkeypatch
    ):
        """草图阶段跳过 M16.2 外貌校验（粗图构图确认即可，校验留给精渲染）。"""
        self._setup(mock_call_llm, mock_get_comfyui_result, monkeypatch)
        monkeypatch.setattr(settings, "storyboard_appearance_check", True)
        verify = AsyncMock(return_value=("http://worker/view?filename=sb.png", None))
        agent._verify_and_retry_appearance = verify

        response = await agent.execute(
            StoryboardRequest(
                scene=sample_scene, characters=[sample_character], sketch_mode=True
            )
        )

        assert response.success is True
        assert verify.await_count == 0

    async def test_sketch_disabled_falls_back_full_render(
        self, agent, sample_scene, mock_call_llm, mock_call_comfyui, mock_get_comfyui_result, monkeypatch
    ):
        """全局开关关闭时 sketch_mode=True 回退全参数精渲染。"""
        self._setup(mock_call_llm, mock_get_comfyui_result, monkeypatch)
        monkeypatch.setattr(settings, "sketch_mode_enabled", False)

        response = await agent.execute(
            StoryboardRequest(scene=sample_scene, sketch_mode=True)
        )

        workflow = mock_call_comfyui.call_args.args[1]
        assert workflow["5"]["inputs"]["steps"] == 25
        assert response.data["is_sketch"] is False
