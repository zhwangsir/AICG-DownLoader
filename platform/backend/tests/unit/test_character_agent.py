"""角色 Agent 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.character_agent import CharacterAgent
from app.config import settings
from app.models.schemas import CharacterAsset, CharacterRequest
from app.services.character_library import CharacterLibrary
from app.services.style_anchor import SDXL_CHECKPOINT_ANIME, SDXL_CHECKPOINT_REALISTIC


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


class TestCharacterAgentRAGEnhance:
    async def test_rag_enhances_view_prompts(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        with patch(
            "app.agents.character_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={
                "optimized_positive": "rag positive",
                "optimized_negative": "rag negative",
            },
        ) as mock_opt, patch.object(
            agent,
            "_generate_image_via_sdxl",
            new_callable=AsyncMock,
            return_value="http://mock/char.png",
        ) as mock_gen:
            response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        # RAG 优化每个视图正面提示词，至少调用 3 次
        assert mock_opt.await_count == 3
        # 图像生成实际调用的是 RAG 优化后的提示词（_generate_image_via_sdxl 使用位置参数）
        prompts = [call.args[1] for call in mock_gen.call_args_list]
        assert all("rag positive" in p for p in prompts)

    async def test_rag_failure_keeps_llm_prompts(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        with patch(
            "app.agents.character_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("RAG 失败"),
        ), patch.object(
            agent,
            "_generate_image_via_sdxl",
            new_callable=AsyncMock,
            return_value="http://mock/char.png",
        ):
            response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True


class TestCharacterFilenamePrefixUniqueness:
    """M15.5: filename_prefix 必须带每次运行唯一的后缀。

    背景：core E2E（pipeline-3ba8b3b3e304）中，pc02 重启后 SaveImage 计数器归零，
    生成 character_char_001_front_00001_.png；LB /view 按后端顺序先试 gpu0，
    命中 gpu0 实例目录同名陈旧文件（上一轮写实风残留），H3 ref2va 与漂移检测
    均拿到错误参考图 → drift_scenes=[1,2]。唯一后缀可根治跨后端文件名碰撞。
    """

    async def test_filename_prefix_has_unique_suffix_per_run(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """连续两次执行 → 两次提交的 workflow filename_prefix 后缀不同。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        request = CharacterRequest(character=sample_character)
        assert (await agent.execute(request)).success is True
        assert (await agent.execute(request)).success is True

        prefixes = [
            call.args[1]["7"]["inputs"]["filename_prefix"]
            for call in mock_call_comfyui.call_args_list
        ]
        assert len(prefixes) == 6  # 两次 × 三视图
        # 同一次执行内三视图共享前缀语义（character_{id}_{view}_{suffix}），跨次不同
        assert all(p.startswith("character_char_001_") for p in prefixes)
        first_run, second_run = prefixes[:3], prefixes[3:]
        for p1, p2 in zip(first_run, second_run):
            assert p1 != p2

    async def test_filename_prefix_keeps_character_and_view_semantics(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """前缀仍包含 character_id 与视图名，便于人工检索与 NAS 归档。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "front",
                "side_view_prompt": "side",
                "closeup_prompt": "closeup",
                "negative_prompt": "blurry",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        await agent.execute(CharacterRequest(character=sample_character))

        prefixes = [
            call.args[1]["7"]["inputs"]["filename_prefix"]
            for call in mock_call_comfyui.call_args_list
        ]
        joined = " ".join(prefixes)
        assert "front" in joined and "side" in joined and "closeup" in joined
        assert all("char_001" in p for p in prefixes)


class TestCharacterStyleAnchoring:
    """M15.1: 角色定妆照画风锚定 — 提示词强制追加风格尾，搜索词随画风。"""

    async def test_prompts_carry_non_realistic_style_tail(
        self, agent, sample_character, mock_call_llm
    ):
        """画风「国漫」→ 三视图提示词追加国漫风格尾（无 photorealistic 画质尾），负面词排斥写实。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "1girl, front view",
                "side_view_prompt": "1girl, side view",
                "closeup_prompt": "1girl, closeup",
                "negative_prompt": "blurry",
            }
        )

        prompts = await agent._generate_prompts(sample_character, "国漫", "")

        for key in ("front_view_prompt", "side_view_prompt", "closeup_prompt"):
            assert prompts[key].endswith(", Chinese anime guoman style")
            assert "photorealistic" not in prompts[key]
        assert "photorealistic" in prompts["negative_prompt"]

    async def test_prompts_carry_realistic_style_tail(
        self, agent, sample_character, mock_call_llm
    ):
        """画风「写实电影感」→ 提示词追加写实风格尾，负面词排斥 anime/卡通。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "1boy, front view",
                "side_view_prompt": "",
                "closeup_prompt": "",
                "negative_prompt": "",
            }
        )

        prompts = await agent._generate_prompts(sample_character, "写实电影感", "")

        assert prompts["front_view_prompt"].endswith(
            ", cinematic realistic, photorealistic, professional photography"
        )
        # side/closeup 为空时回退 front，同样带风格尾
        assert prompts["side_view_prompt"].endswith(
            ", cinematic realistic, photorealistic, professional photography"
        )
        # 空负面词回退 DEFAULT_NEGATIVE_PROMPT，仍追加冲突画风负面词
        assert "anime" in prompts["negative_prompt"]

    async def test_llm_conflicts_sanitized_before_tail(
        self, agent, sample_character, mock_call_llm
    ):
        """M15.4：画风「国漫」→ LLM 产出自带 photorealistic、负面词排斥 anime 时先清洗再追加尾。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "1girl, front view, photorealistic, hyperrealistic skin",
                "side_view_prompt": "1girl, side view",
                "closeup_prompt": "1girl, closeup",
                "negative_prompt": "anime, cartoon, blurry",
            }
        )

        prompts = await agent._generate_prompts(sample_character, "国漫", "")

        assert "photorealistic" not in prompts["front_view_prompt"]
        assert "hyperrealistic" not in prompts["front_view_prompt"]
        assert "1girl, front view" in prompts["front_view_prompt"]
        assert prompts["front_view_prompt"].endswith(", Chinese anime guoman style")
        # 负面词：LLM 自带的 anime/cartoon 被清洗（目标画风本身），
        # 由风格尾统一注入 KB 冲突词（含 western cartoon，属正常 KB 信号）
        neg_lower = prompts["negative_prompt"].lower()
        assert neg_lower.startswith("blurry")
        assert "photorealistic" in neg_lower  # 来自 style_negative_tail
        assert "western cartoon" in neg_lower  # KB 国漫 negative_terms 原样保留

    async def test_search_query_uses_anchor_title(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        mock_web_search,
    ):
        """execute 的联网搜索词以画风锚定 title 开头（替代原硬编码「写实人像摄影」）。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "f",
                "side_view_prompt": "s",
                "closeup_prompt": "c",
                "negative_prompt": "n",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        request = CharacterRequest(character=sample_character, style="国漫")
        response = await agent.execute(request)

        assert response.success is True
        query = mock_web_search.call_args.args[0]
        assert query.startswith("国漫风格")


class TestCharacterCheckpointByStyle:
    """M15.7: SDXL 工作流 checkpoint 随画风写实性切换。

    背景：core E2E（pipeline-1a92d5f7a966）国漫任务用 majicMIX（写实特化）
    生成定妆照，国漫锚定尾无法扭转模型先验 → 写实定妆照，drift 复发。
    """

    async def test_guoman_uses_animagine_checkpoint(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风「国漫」→ 三视图 workflow 全部使用 animagineXL40。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "f",
                "side_view_prompt": "s",
                "closeup_prompt": "c",
                "negative_prompt": "n",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        request = CharacterRequest(character=sample_character, style="国漫")
        assert (await agent.execute(request)).success is True

        ckpts = [
            call.args[1]["1"]["inputs"]["ckpt_name"]
            for call in mock_call_comfyui.call_args_list
        ]
        assert len(ckpts) == 3
        assert all(c == SDXL_CHECKPOINT_ANIME for c in ckpts)

    async def test_realistic_uses_majicmix_checkpoint(
        self,
        agent,
        sample_character,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风「写实电影感」→ 三视图 workflow 全部使用 majicMIX。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "f",
                "side_view_prompt": "s",
                "closeup_prompt": "c",
                "negative_prompt": "n",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }

        request = CharacterRequest(character=sample_character, style="写实电影感")
        assert (await agent.execute(request)).success is True

        ckpts = [
            call.args[1]["1"]["inputs"]["ckpt_name"]
            for call in mock_call_comfyui.call_args_list
        ]
        assert len(ckpts) == 3
        assert all(c == SDXL_CHECKPOINT_REALISTIC for c in ckpts)


class TestCharacterStyleWeightSeparation:
    """M16.1: 角色定妆照画风子句权重分离 — system prompt 结构断言。

    背景：旧子句强制 LLM 将 KB 整串风格关键词（含 elaborate costumes 等内容词）
    写入每条提示词，与角色外貌描述争权重（core E2E 定妆照银灰发缺陷）。
    分离后必填仅风格名，KB 整串降为可选氛围参考，外貌描述显式优先。
    """

    async def test_system_prompt_separates_style_and_appearance(
        self, agent, sample_character, mock_call_llm
    ):
        """画风「国漫」→ system prompt 必填行仅风格名，整串降可选，含权重分离规则。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "1girl, front view",
                "side_view_prompt": "1girl, side view",
                "closeup_prompt": "1girl, closeup",
                "negative_prompt": "blurry",
            }
        )

        await agent._generate_prompts(sample_character, "国漫", "")

        messages = mock_call_llm.call_args.kwargs["messages"]
        system = next(m["content"] for m in messages if m["role"] == "system")
        mandatory = next(line for line in system.split("\n") if "必须显式包含" in line)
        assert '"Chinese anime guoman style"' in mandatory
        assert "elaborate costumes" not in mandatory
        optional_line = next(
            line for line in system.split("\n") if "elaborate costumes" in line
        )
        assert "可选" in optional_line
        assert "权重分离规则" in system

    async def test_system_prompt_realistic_uses_style_name_only(
        self, agent, sample_character, mock_call_llm
    ):
        """画风「写实电影感」→ 必填行含 "cinematic realistic" 风格名。"""
        mock_call_llm.return_value = json.dumps(
            {
                "front_view_prompt": "1boy, front view",
                "side_view_prompt": "",
                "closeup_prompt": "",
                "negative_prompt": "",
            }
        )

        await agent._generate_prompts(sample_character, "写实电影感", "")

        messages = mock_call_llm.call_args.kwargs["messages"]
        system = next(m["content"] for m in messages if m["role"] == "system")
        mandatory = next(line for line in system.split("\n") if "必须显式包含" in line)
        assert '"cinematic realistic"' in mandatory
        assert "角色定妆照" in mandatory


def _make_vlm_result(content: str):
    """构造 VLM chat.completions.create 返回值（与 storyboard 测试同构）。"""
    result = MagicMock()
    result.choices = [MagicMock()]
    result.choices[0].message.content = content
    return result


class TestCharacterAgentViewQC:
    """M18.2: 三视图 VLM 质检 — 拦截「生成成功但内容废品」，不合格换 seed 重生成。

    背景：M18.1 帧级核验发现 char_001 side 实为无关白发少女、char_002 side 实为
    16 格眼睛画法参考表——视图生成「成功」但内容是废品，无质检拦截混入 ref 组。
    质检时机为三视图生成后、角色卡入库前；VLM 不可用一律 fail-open 放行。
    """

    QC_PASS = json.dumps({"pass": True, "reason": ""})
    QC_FAIL = json.dumps(
        {"pass": False, "reason": "画面为16格眼睛画法参考表而非人物肖像"}
    )
    QC_MATCH = json.dumps({"match": True, "reason": ""})
    QC_MISMATCH = json.dumps({"match": False, "reason": "发色为白色而非角色描述的黑色"})

    _PROMPTS_JSON = json.dumps(
        {
            "front_view_prompt": "front",
            "side_view_prompt": "side",
            "closeup_prompt": "closeup",
            "negative_prompt": "blurry",
        }
    )

    def _enable_qc(self, monkeypatch):
        monkeypatch.setattr(settings, "character_view_qc_enabled", True)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")

    def _attach_vlm(self, agent, contents: list[str]):
        """按调用顺序返回 VLM 结果（front 自检 → side/closeup 比对 → 重生复检…）。"""
        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(
            side_effect=[_make_vlm_result(c) for c in contents]
        )
        agent._vlm_client = vlm
        return vlm

    def _mock_image_download(self, agent):
        resp = MagicMock()
        resp.content = b"fake-png-bytes"
        resp.raise_for_status = MagicMock()
        agent.http.get = AsyncMock(return_value=resp)

    def _patch_sdxl_generate(self, agent):
        """patch SDXL 生成入口：按视图计数，返回可区分 URL（view_count_seed）。"""
        counter = {"front": 0, "side": 0, "closeup": 0}

        async def _fake(worker_url, positive, negative, character_id, view_name, seed, anchor=None):
            counter[view_name] += 1
            return f"http://mock/{view_name}_{counter[view_name]}_s{seed}.png"

        mock_gen = AsyncMock(side_effect=_fake)
        agent._generate_image_via_sdxl = mock_gen
        return mock_gen, counter

    def _seeds_of(self, mock_gen, view_name: str) -> list[int]:
        return [c.args[5] for c in mock_gen.call_args_list if c.args[4] == view_name]

    async def test_all_views_pass_qc(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """三视图全部质检合格 → success，VLM 调 3 次（front 自检 + side/closeup 比对），无重生成。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        vlm = self._attach_vlm(agent, [self.QC_PASS, self.QC_MATCH, self.QC_MATCH])
        mock_call_llm.return_value = self._PROMPTS_JSON
        mock_gen, _ = self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert set(response.data["reference_images"]) == {"front", "side", "closeup"}
        assert vlm.chat.completions.create.await_count == 3
        assert mock_gen.await_count == 3

    async def test_qc_disabled_skips_vlm(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """character_view_qc_enabled=False → 不发起任何 VLM 调用（回滚路径与现状一致）。"""
        monkeypatch.setattr(settings, "character_view_qc_enabled", False)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        vlm = self._attach_vlm(agent, [])
        mock_call_llm.return_value = self._PROMPTS_JSON
        mock_gen, _ = self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert vlm.chat.completions.create.await_count == 0
        assert mock_gen.await_count == 3

    async def test_vlm_url_empty_fail_open(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """visual_model_url 为空 → 跳过质检放行（fail-open），不调用 VLM。"""
        monkeypatch.setattr(settings, "character_view_qc_enabled", True)
        monkeypatch.setattr(settings, "visual_model_url", "")
        vlm = self._attach_vlm(agent, [])
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert vlm.chat.completions.create.await_count == 0

    async def test_front_fail_regenerates_with_new_seed(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """front 首次自检不合格 → 换 seed 重生成并复检合格 → success，入库为重生图。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        # 调用序：front 自检 FAIL → front 重生后自检 PASS → side 比对 MATCH → closeup 比对 MATCH
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_PASS, self.QC_MATCH, self.QC_MATCH])
        mock_call_llm.return_value = self._PROMPTS_JSON
        mock_gen, counter = self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert counter == {"front": 2, "side": 1, "closeup": 1}
        front_seeds = self._seeds_of(mock_gen, "front")
        assert len(front_seeds) == 2 and front_seeds[0] != front_seeds[1]
        # 入库为重生后的 front（第 2 次生成）
        assert "front_2_" in response.data["reference_images"]["front"]

    async def test_side_mismatch_regenerates_only_side(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """side 与 front 不一致 → 仅 side 换 seed 重生成，front/closeup 不重生。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        # 调用序：front 自检 PASS → side 比对 MISMATCH → side 重生复检 MATCH → closeup 比对 MATCH
        self._attach_vlm(agent, [self.QC_PASS, self.QC_MISMATCH, self.QC_MATCH, self.QC_MATCH])
        mock_call_llm.return_value = self._PROMPTS_JSON
        mock_gen, counter = self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert counter == {"front": 1, "side": 2, "closeup": 1}
        side_seeds = self._seeds_of(mock_gen, "side")
        assert len(side_seeds) == 2 and side_seeds[0] != side_seeds[1]
        assert "side_2_" in response.data["reference_images"]["side"]

    async def test_retry_exhausted_fails(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """front 初始 + 2 次重试全部不合格 → success=False（废品拦截，不降级放行）。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_FAIL, self.QC_FAIL])
        mock_call_llm.return_value = self._PROMPTS_JSON
        _, counter = self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is False
        assert "质检" in response.error
        assert counter["front"] == 3  # 初始 1 次 + 重试 2 次

    async def test_qc_failure_not_registered_to_library(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """质检重试耗尽 → 角色卡不入库（register_from_card 未被调用）。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_FAIL, self.QC_FAIL])
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        with patch("app.agents.character_agent.character_library") as mock_lib:
            response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is False
        mock_lib.register_from_card.assert_not_called()

    async def test_qc_exhaustion_isolates_library_residual(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """M18.7 拦截即隔离：QC 重试耗尽判失败 → 显式删除资产库该 character_id 残留。

        背景：M18.6 实测新剧本角色（林远/苏清 char_001/002）被 QC 拦截后，旧剧本
        同 ID 资产（林默/林小满）仍残留资产库，被 _collect_character_reference_images
        静默命中，ref2va 参考与漂移对照基准双双错配。判失败时必须隔离残留。
        """
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_FAIL, self.QC_FAIL])
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        with patch("app.agents.character_agent.character_library") as mock_lib:
            response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is False
        mock_lib.delete.assert_called_once_with(sample_character.character_id)
        mock_lib.register_from_card.assert_not_called()

    async def test_qc_exhaustion_deletes_stale_asset_in_real_library(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch, tmp_path
    ):
        """M18.7：真实资产库预置旧剧本残留（同 character_id 不同血缘），QC 拦截后残留被删除。"""
        library = CharacterLibrary(library_dir=tmp_path / "library")
        library.save(
            CharacterAsset(
                character_id=sample_character.character_id,
                name="旧角色",
                reference_images={"front": "http://old/front.png"},
                source_script_id="proj-old",
            )
        )
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_FAIL, self.QC_FAIL])
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        with patch("app.agents.character_agent.character_library", library):
            response = await agent.execute(
                CharacterRequest(character=sample_character, project_id="proj-new")
            )

        assert response.success is False
        # 旧剧本残留已被隔离，后续收集不会再命中
        assert library.get(sample_character.character_id) is None

    async def test_qc_exhaustion_isolation_error_does_not_mask_failure(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """M18.7：资产库删除异常不阻断/不掩盖质检拦截结果（仍 success=False）。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_FAIL, self.QC_FAIL])
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        with patch("app.agents.character_agent.character_library") as mock_lib:
            mock_lib.delete.side_effect = RuntimeError("disk error")
            response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is False
        assert "质检" in response.error

    async def test_vlm_exception_fail_open(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """VLM 调用抛异常 → fail-open 放行（质检器故障不阻断生产）。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(side_effect=RuntimeError("vlm down"))
        agent._vlm_client = vlm
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
        assert set(response.data["reference_images"]) == {"front", "side", "closeup"}

    async def test_vlm_bad_json_fail_open(
        self, agent, sample_character, mock_call_llm, mock_call_comfyui, monkeypatch
    ):
        """VLM 返回无法解析的坏 JSON → fail-open 放行。"""
        self._enable_qc(monkeypatch)
        self._mock_image_download(agent)
        self._attach_vlm(agent, ["not-json{{{", "???", "```json broken"])
        mock_call_llm.return_value = self._PROMPTS_JSON
        self._patch_sdxl_generate(agent)

        response = await agent.execute(CharacterRequest(character=sample_character))

        assert response.success is True
