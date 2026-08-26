"""分镜 Agent 覆盖率补全测试（boost）。

针对既有 test_storyboard_agent.py 未触达的分支：
- 联网参考资料注入（execute 日志 + user_msg 参考资料段）
- LLM 空提示词报错 / ComfyUI 无 prompt_id / 输出无图片
- 失败模式注册表子句注入与命中回写的异常兜底（不阻断主流程）
- batch_execute 批量并行分发（成功/失败/异常/无 worker 四分支）
- _generate_prompts / _check_appearance_mismatch / _rebuild_short_prompt 的
  json_repair 修复路径与非 dict 回退
- _get_vlm_client 懒加载 / _resolve_keyframe_anchor_url 空角色 / RAG 空 prompt 早退
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import AsyncOpenAI

from app.agents.storyboard_agent import StoryboardAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    Scene,
    StoryboardBatchRequest,
    StoryboardRequest,
)
from app.services.style_anchor import resolve_style_anchor


@pytest.fixture
def agent():
    return StoryboardAgent()


def _ok_images(mock_get_comfyui_result):
    mock_get_comfyui_result.return_value = {
        "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
    }


def _llm_json(mock_call_llm, prompt="p", negative="n"):
    mock_call_llm.return_value = json.dumps({"prompt": prompt, "negative_prompt": negative})


class TestReferenceInjection:
    """联网搜索到参考资料：execute 记录日志（L258）且注入 user_msg（L659）。"""

    async def test_reference_logged_and_injected_into_user_msg(
        self,
        agent,
        sample_scene,
        mock_web_search,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        caplog,
    ):
        mock_web_search.return_value = "低角度仰拍制造压迫感，参考自某电影分镜解析"
        _llm_json(mock_call_llm)
        _ok_images(mock_get_comfyui_result)

        with caplog.at_level("INFO", logger="app.agents.storyboard_agent"):
            response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        # execute 层记录参考资资料字符数
        assert any("分镜 Agent 搜索到参考资料" in r.message for r in caplog.records)
        # _generate_prompts 将参考资料注入 user_msg
        user_msg = mock_call_llm.call_args.kwargs["messages"][1]["content"]
        assert "参考资料（联网搜索" in user_msg
        assert "低角度仰拍制造压迫感" in user_msg


class TestExecuteFailurePaths:
    """execute 层快速失败分支。"""

    async def test_empty_llm_prompt_raises(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
    ):
        """LLM 未返回 prompt 且 scene.prompt 为空 → 报「LLM 未返回分镜提示词」（L275）。"""
        sample_scene.prompt = ""
        sample_scene.negative_prompt = ""
        _llm_json(mock_call_llm, prompt="", negative="")

        response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is False
        assert "LLM 未返回分镜提示词" in response.error
        assert mock_call_comfyui.await_count == 0

    async def test_comfyui_missing_prompt_id(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
    ):
        """ComfyUI 提交响应缺 prompt_id → 报错（L528）。"""
        _llm_json(mock_call_llm)
        mock_call_comfyui.return_value = {"error": "invalid workflow"}

        response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is False
        assert "未返回 prompt_id" in response.error

    async def test_no_images_in_outputs(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """ComfyUI 输出无任何 images 节点 → 报「未找到生成的图片」（L542）。"""
        _llm_json(mock_call_llm)
        mock_get_comfyui_result.return_value = {"7": {"texts": ["no image here"]}}

        response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is False
        assert "未找到生成的图片" in response.error


class TestFailureRegistryExceptionFallback:
    """M25.9 C2：注册表子句注入/命中回写异常不阻断生成主流程。"""

    async def test_generator_clause_exception_still_generates(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """generator 层注册表子句构建异常 → 警告跳过后照常出图（L295-296）。"""
        _llm_json(mock_call_llm)
        _ok_images(mock_get_comfyui_result)
        monkeypatch.setattr(
            "app.agents.storyboard_agent.failure_registry.build_negative_prompt_clause",
            MagicMock(side_effect=RuntimeError("registry boom")),
        )

        response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        assert "filename=sb.png" in response.data["image_url"]

    async def test_bump_hit_exception_still_retries(
        self, agent, sample_scene, sample_character, monkeypatch
    ):
        """失真命中回写注册表异常 → 跳过后继续短 prompt 重试（L833-834）。"""
        agent._check_appearance_mismatch = AsyncMock(return_value="发色不符")
        agent._rebuild_short_prompt = AsyncMock(return_value="medium shot, girl")
        dispatch = AsyncMock(return_value=("http://worker/view?filename=new.png", 7))
        agent._dispatch_image_generation = dispatch
        monkeypatch.setattr(
            "app.agents.storyboard_agent.failure_registry.bump_hit",
            MagicMock(side_effect=RuntimeError("registry down")),
        )

        url, _seed = await agent._verify_and_retry_appearance(
            image_url="http://worker/view?filename=old.png",
            scene=sample_scene,
            characters=[sample_character],
            style="国漫",
            worker_url="http://worker",
            negative="neg",
            anchor=resolve_style_anchor("国漫"),
        )

        assert url == "http://worker/view?filename=new.png"
        assert dispatch.await_count == 1

    async def test_correction_clause_exception_keeps_original_negative(
        self, agent, sample_scene, sample_character, monkeypatch
    ):
        """correction 层注册表子句注入异常 → 重试沿用原负面提示词（L850-851）。"""
        agent._check_appearance_mismatch = AsyncMock(return_value="发色不符")
        agent._rebuild_short_prompt = AsyncMock(return_value="medium shot, girl")
        dispatch = AsyncMock(return_value=("http://worker/view?filename=new.png", 7))
        agent._dispatch_image_generation = dispatch
        monkeypatch.setattr(
            "app.agents.storyboard_agent.failure_registry.build_negative_prompt_clause",
            MagicMock(side_effect=RuntimeError("registry down")),
        )

        url, _seed = await agent._verify_and_retry_appearance(
            image_url="http://worker/view?filename=old.png",
            scene=sample_scene,
            characters=[sample_character],
            style="国漫",
            worker_url="http://worker",
            negative="neg",
            anchor=resolve_style_anchor("国漫"),
        )

        assert url == "http://worker/view?filename=new.png"
        # 子句注入失败 → 负面提示词保持原样透传
        assert dispatch.call_args.kwargs["negative"] == "neg"


class TestBatchExecute:
    """batch_execute 批量并行分发（L359-396）。"""

    def _scenes(self, n: int) -> list[Scene]:
        return [
            Scene(scene_id=i + 1, episode=1, shot_type="特写", description=f"场景{i + 1}")
            for i in range(n)
        ]

    def _ok_response(self, scene_id: int) -> AgentResponse:
        return AgentResponse(
            success=True,
            data={
                "scene_id": scene_id,
                "image_url": f"http://w/view?filename=sb_{scene_id}.png",
                "prompt_used": "p",
            },
        )

    async def test_all_success_with_preallocated_workers(self, agent):
        """预分配 worker：逐场景指定 worker_url，全部成功汇入 results。"""
        scenes = self._scenes(3)
        agent.get_available_image_workers = AsyncMock(
            return_value=["http://w1", "http://w2", "http://w3"]
        )
        execute = AsyncMock(
            side_effect=[self._ok_response(s.scene_id) for s in scenes]
        )
        agent.execute = execute

        resp = await agent.batch_execute(StoryboardBatchRequest(scenes=scenes))

        assert resp.success is True
        assert len(resp.data["results"]) == 3
        assert resp.data["failed_scenes"] == []
        assert [r["scene_id"] for r in resp.data["results"]] == [1, 2, 3]
        # 每个场景分配到对应序号的 worker
        worker_urls = [c.kwargs["worker_url"] for c in execute.call_args_list]
        assert worker_urls == ["http://w1", "http://w2", "http://w3"]

    async def test_no_workers_passes_none(self, agent):
        """无可用 worker → worker_url 传 None（execute 内部自行解析）。"""
        scenes = self._scenes(2)
        agent.get_available_image_workers = AsyncMock(return_value=[])
        execute = AsyncMock(
            side_effect=[self._ok_response(s.scene_id) for s in scenes]
        )
        agent.execute = execute

        resp = await agent.batch_execute(StoryboardBatchRequest(scenes=scenes))

        assert resp.success is True
        assert len(resp.data["results"]) == 2
        worker_urls = [c.kwargs["worker_url"] for c in execute.call_args_list]
        assert worker_urls == [None, None]

    async def test_failed_scene_collected(self, agent):
        """单场景 execute 返回失败 → 记入 failed_scenes，其余照常。"""
        scenes = self._scenes(2)
        agent.get_available_image_workers = AsyncMock(return_value=None)
        execute = AsyncMock(
            side_effect=[
                self._ok_response(1),
                AgentResponse(success=False, error="ComfyUI OOM"),
            ]
        )
        agent.execute = execute

        resp = await agent.batch_execute(StoryboardBatchRequest(scenes=scenes))

        assert resp.success is True
        assert [r["scene_id"] for r in resp.data["results"]] == [1]
        assert resp.data["failed_scenes"] == [2]

    async def test_execute_exception_collected_as_failed(self, agent):
        """单场景 execute 抛异常 → gather 捕获后记入 failed_scenes，不拖垮整批。"""
        scenes = self._scenes(3)
        agent.get_available_image_workers = AsyncMock(return_value=None)
        execute = AsyncMock(
            side_effect=[
                self._ok_response(1),
                RuntimeError("worker crashed"),
                self._ok_response(3),
            ]
        )
        agent.execute = execute

        resp = await agent.batch_execute(StoryboardBatchRequest(scenes=scenes))

        assert resp.success is True
        assert [r["scene_id"] for r in resp.data["results"]] == [1, 3]
        assert resp.data["failed_scenes"] == [2]

    async def test_batch_request_fields_propagated(self, agent):
        """批量级 characters/style/auto_link_assets 透传到每个子请求。"""
        scenes = self._scenes(1)
        agent.get_available_image_workers = AsyncMock(return_value=None)
        execute = AsyncMock(side_effect=[self._ok_response(1)])
        agent.execute = execute

        req = StoryboardBatchRequest(
            scenes=scenes, style="国漫", auto_link_assets=False
        )
        resp = await agent.batch_execute(req)

        assert resp.success is True
        sub_req = execute.call_args.args[0]
        assert sub_req.style == "国漫"
        assert sub_req.auto_link_assets is False
        assert sub_req.scene.scene_id == 1


class TestGeneratePromptsJsonFallback:
    """_generate_prompts 的 JSON 解析兜底分支。"""

    async def test_broken_json_repaired(self, agent, sample_scene, mock_call_llm):
        """LLM 输出非法 JSON → json_repair 修复后正常返回（L677-678）。"""
        mock_call_llm.return_value = "{prompt: 'repaired prompt', negative_prompt: 'repaired neg'}"

        data = await agent._generate_prompts(sample_scene, [], "写实电影感")

        assert data["prompt"] == "repaired prompt"
        assert data["negative_prompt"] == "repaired neg"

    async def test_non_dict_json_returns_empty_dict(self, agent, sample_scene, mock_call_llm):
        """LLM 输出合法 JSON 但非 dict → 回退空 dict（L680）。"""
        mock_call_llm.return_value = '["just", "a", "list"]'

        data = await agent._generate_prompts(sample_scene, [], "写实电影感")

        assert data == {}


class TestVlmClientLazyInit:
    """_get_vlm_client 懒加载（L695）：首次调用创建，之后复用同一实例。"""

    def test_lazy_creates_and_reuses(self, agent):
        assert agent._vlm_client is None

        c1 = agent._get_vlm_client()

        assert isinstance(c1, AsyncOpenAI)
        assert agent._vlm_client is c1
        # 第二次调用命中缓存分支，不重建
        assert agent._get_vlm_client() is c1


class TestCheckAppearanceJsonRepair:
    """_check_appearance_mismatch 的 json_repair 修复路径（L744-745）。"""

    async def test_broken_vlm_json_repaired(
        self, agent, sample_character, monkeypatch
    ):
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        resp = MagicMock()
        resp.content = b"fake-png-bytes"
        resp.raise_for_status = MagicMock()
        agent.http = MagicMock()
        agent.http.get = AsyncMock(return_value=resp)

        vlm = MagicMock()
        vlm_result = MagicMock()
        vlm_result.choices = [MagicMock()]
        # 非法 JSON（裸键名 + 单引号）→ json.loads 失败，json_repair 修复
        vlm_result.choices[0].message.content = "{match: false, reason: '发色为银灰而非黑色'}"
        vlm.chat.completions.create = AsyncMock(return_value=vlm_result)
        agent._vlm_client = vlm

        reason = await agent._check_appearance_mismatch(
            "http://x/img.png", [sample_character]
        )

        assert reason == "发色为银灰而非黑色"


class TestRebuildShortPromptJsonRepair:
    """_rebuild_short_prompt 的 json_repair 修复路径（L795-796）。"""

    async def test_broken_json_repaired(
        self, agent, sample_scene, sample_character, mock_call_llm
    ):
        mock_call_llm.return_value = "{prompt: 'medium shot, black hair girl'}"

        prompt = await agent._rebuild_short_prompt(
            sample_scene, [sample_character], "国漫", "发色不符"
        )

        assert prompt == "medium shot, black hair girl"


class TestResolveKeyframeAnchorUrl:
    """_resolve_keyframe_anchor_url 空角色早退（L554）。"""

    async def test_empty_characters_returns_empty(self, agent):
        assert await agent._resolve_keyframe_anchor_url([]) == ""


class TestRagOptimizeEarlyReturn:
    """_rag_optimize_storyboard_prompts 空 prompt 早退（L873）。"""

    async def test_blank_positive_returns_prompts_unchanged(self, agent):
        prompts = {"prompt": "   ", "negative_prompt": "n"}

        with patch(
            "app.agents.storyboard_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
        ) as rag:
            out = await agent._rag_optimize_storyboard_prompts(prompts, "写实电影感")

        assert out is prompts
        assert rag.await_count == 0
